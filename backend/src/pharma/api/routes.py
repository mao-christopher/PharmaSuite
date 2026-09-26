"""REST API route handlers for Pharmacy Inventory Dashboard & Replay System."""

import hashlib
import json
import re
import shutil
from datetime import date, datetime, timezone
from typing import Optional

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from pharma.api.replay_stream import ReplayController, ScenarioNotReady, pharmacy_today
from pharma.db.models import BACKGROUND_PATTERN, Layout
from pharma.services.layout import layout_path, list_layout_ids, load_layout, save_layout
from pharma.services.recordings import EVENTS_FILE, VIDEO_EXTENSIONS, parse_events_file, probe_video, write_events

router = APIRouter(prefix="/api")

# Global controller instance (initialized in main.py)
controller: Optional[ReplayController] = None

MAX_EVENTS_BYTES = 1_000_000
MAX_IMAGE_BYTES = 20_000_000


def get_controller() -> ReplayController:
    if controller is None:
        raise HTTPException(status_code=500, detail="ReplayController not initialized")
    return controller


class ReplayControlRequest(BaseModel):
    action: str  # play | pause | restart | seek
    media_time_ms: Optional[int] = None


class DisposalSubmitRequest(BaseModel):
    selected_receipt_id: str
    explicit_quantity: Optional[int] = None


class PrescriptionStatusRequest(BaseModel):
    status: str  # created | confirmed_fill | paid | cancelled


class ConfirmationRequest(BaseModel):
    resolved_region_id: Optional[str] = None


class ReceiveStockRequest(BaseModel):
    medication_key: str
    bottle_count: int = Field(..., ge=1)
    tablets_per_bottle: int = Field(..., ge=0)
    expiry_date: str
    lot_number: Optional[str] = None
    received_at: Optional[str] = None

    @field_validator("expiry_date")
    @classmethod
    def _iso_date(cls, value: str) -> str:
        date.fromisoformat(value)
        return value


# ---------------------------------------------------------------- recordings


def _recording_errors(fn):
    try:
        return fn()
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ScenarioNotReady as e:
        raise HTTPException(status_code=409, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/recordings")
def list_recordings(ctrl: ReplayController = Depends(get_controller)):
    """Every stored recording with its skeleton status and how much of it has been applied."""
    return {"recordings": ctrl.list_recordings(), "current": ctrl.current_scenario_name}


@router.get("/recordings/{name}")
def get_recording(name: str, ctrl: ReplayController = Depends(get_controller)):
    """One recording with its signals, the decisions made for them, and the alerts they raised."""
    return _recording_errors(lambda: ctrl.recording_detail(name))


@router.get("/recordings/{name}/thumbnail")
def recording_thumbnail(name: str, ctrl: ReplayController = Depends(get_controller)):
    jpeg = _recording_errors(lambda: ctrl.thumbnail_jpeg(name))
    return Response(content=jpeg, media_type="image/jpeg", headers={"Cache-Control": "max-age=300"})


@router.post("/recordings/{name}/load")
async def load_recording(name: str, ctrl: ReplayController = Depends(get_controller)):
    """Put a recording in the player. Live inventory is kept; signals apply as they play."""
    res = _recording_errors(lambda: ctrl.load_scenario(name))
    await ctrl.broadcast_state_snapshot()
    return {"status": "success", "data": res}


@router.post("/recordings/{name}/apply")
async def apply_recording(name: str, ctrl: ReplayController = Depends(get_controller)):
    """Apply every remaining signal of a recording to live inventory without playing it."""
    applied = _recording_errors(lambda: ctrl.apply_recording(name))
    await ctrl.broadcast_state_snapshot()
    return {"status": "success", "applied": applied, "recording": ctrl.recording_summary(ctrl.scenario_dir(name))}


@router.post("/recordings/{name}/process")
def process_recording(name: str, ctrl: ReplayController = Depends(get_controller)):
    """(Re)run skeleton extraction for a recording's video."""
    _recording_errors(lambda: ctrl.start_processing(name))
    return {"status": "processing"}


@router.delete("/recordings/{name}")
async def delete_recording(name: str, ctrl: ReplayController = Depends(get_controller)):
    """Delete an uploaded recording's files. Inventory changes it already made are kept."""
    _recording_errors(lambda: ctrl.delete_recording(name))
    await ctrl.broadcast_state_snapshot()
    return {"status": "deleted", "name": name}


@router.post("/recordings")
async def upload_recording(
    video: UploadFile = File(...),
    events: UploadFile = File(...),
    name: Optional[str] = Form(None),
    layout_id: str = Form("default"),
    ctrl: ReplayController = Depends(get_controller),
):
    """Upload a video plus pickup/release timestamps; skeletons are extracted in the background."""
    ext = "." + (video.filename or "").rsplit(".", 1)[-1].lower() if "." in (video.filename or "") else ""
    if ext not in VIDEO_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported video type. Use one of: {', '.join(sorted(VIDEO_EXTENSIONS))}")
    try:
        layout = load_layout(ctrl.layouts_dir, layout_id)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    raw_events = await events.read(MAX_EVENTS_BYTES + 1)
    if len(raw_events) > MAX_EVENTS_BYTES:
        raise HTTPException(status_code=400, detail="Timestamps file is too large.")

    label = (name or (video.filename or "recording").rsplit(".", 1)[0]).strip()[:80] or "recording"
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:40] or "recording"
    folder = f"upload-{slug}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    path = ctrl.scenarios_dir / folder
    path.mkdir(parents=True)
    try:
        video_path = path / f"video{ext}"
        with video_path.open("wb") as out:
            shutil.copyfileobj(video.file, out, length=1024 * 1024)
        info = probe_video(video_path)
        parsed = parse_events_file(raw_events.decode("utf-8-sig"), info.duration_ms)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as e:
        shutil.rmtree(path, ignore_errors=True)
        raise HTTPException(status_code=400, detail=str(e))
    write_events(path / EVENTS_FILE, parsed)
    meta = {
        "layout_id": layout_id,
        "label": label,
        "source": "upload",
        "uploaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "video_filename": video.filename,
        "events_filename": events.filename,
        "duration_ms": info.duration_ms,
        "fps": round(info.fps, 3),
        "width": info.width,
        "height": info.height,
        "calibration_version": layout.calibration_version,
    }
    (path / "scenario.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    ctrl.start_processing(folder)
    ctrl.store.record("recording_uploaded", f"Uploaded recording {label}.", recording=folder, events=len(parsed))
    ctrl.store.save()

    warnings = []
    if abs(info.width / info.height - layout.frame_width / layout.frame_height) > 0.02:
        warnings.append(
            f"Video is {info.width}x{info.height} but layout '{layout_id}' was annotated at "
            f"{layout.frame_width}x{layout.frame_height}; regions may not line up."
        )
    return {
        "name": folder,
        "label": label,
        "duration_ms": info.duration_ms,
        "events": len(parsed),
        "status": "processing",
        "warnings": warnings,
    }


# ---------------------------------------------------------------- layouts


@router.get("/layouts")
def list_layouts(ctrl: ReplayController = Depends(get_controller)):
    return {"layouts": list_layout_ids(ctrl.layouts_dir)}


@router.get("/layouts/{layout_id}")
def get_layout(layout_id: str, ctrl: ReplayController = Depends(get_controller)):
    try:
        return load_layout(ctrl.layouts_dir, layout_id).model_dump(mode="json")
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/layouts/{layout_id}")
async def put_layout(
    layout_id: str,
    layout: Layout,
    reset_inventory: bool = Query(False, description="Also restore live inventory to the opening stock"),
    ctrl: ReplayController = Depends(get_controller),
):
    """Save a layout and bump its calibration version. Live inventory is kept unless reset."""
    if layout.layout_id != layout_id:
        raise HTTPException(status_code=400, detail="layout_id in body does not match URL")
    if layout.background_image and not (ctrl.layouts_dir / layout_id / layout.background_image).exists():
        raise HTTPException(status_code=400, detail="Background image was not uploaded")
    saved = save_layout(ctrl.layouts_dir, layout)
    notes = []
    applied = ctrl.layout.layout_id == layout_id
    if applied:
        notes = ctrl.apply_layout(saved, reset_inventory=reset_inventory)
        await ctrl.broadcast_state_snapshot()
    return {
        "layout": saved.model_dump(mode="json"),
        "inventory_reset": applied and reset_inventory,
        "notes": notes,
    }


@router.post("/layouts/{layout_id}/background")
async def upload_background(
    layout_id: str, image: UploadFile = File(...), ctrl: ReplayController = Depends(get_controller)
):
    """Store a camera photo for annotation. It takes effect when the layout is saved."""
    try:
        folder = layout_path(ctrl.layouts_dir, layout_id).parent
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    data = await image.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="Image is larger than 20 MB.")
    decoded = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if decoded is None:
        raise HTTPException(status_code=400, detail="Not a readable image. Use JPG or PNG.")
    is_png = data[:8] == b"\x89PNG\r\n\x1a\n"
    filename = f"background-{hashlib.sha256(data).hexdigest()[:12]}.{'png' if is_png else 'jpg'}"
    folder.mkdir(parents=True, exist_ok=True)
    if is_png or data[:3] == b"\xff\xd8\xff":
        (folder / filename).write_bytes(data)
    else:
        cv2.imwrite(str(folder / filename), decoded)
    height, width = decoded.shape[:2]
    return {"background_image": filename, "width": width, "height": height}


@router.get("/layouts/{layout_id}/files/{filename}")
def layout_file(layout_id: str, filename: str, ctrl: ReplayController = Depends(get_controller)):
    if not re.fullmatch(BACKGROUND_PATTERN, filename):
        raise HTTPException(status_code=404, detail="Not found")
    try:
        path = layout_path(ctrl.layouts_dir, layout_id).parent / filename
    except ValueError:
        raise HTTPException(status_code=404, detail="Not found")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path)


@router.get("/video/still")
def video_still(ctrl: ReplayController = Depends(get_controller)):
    """Single un-annotated camera frame used as the annotation background."""
    return Response(content=ctrl.still_jpeg(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------- live state


@router.get("/inventory")
def get_inventory_status(ctrl: ReplayController = Depends(get_controller)):
    """Current inventory, sessions, alerts, disposals, receipts, and transactions."""
    return ctrl.state_dict()


@router.post("/inventory/reset")
async def reset_inventory(ctrl: ReplayController = Depends(get_controller)):
    """Restore the layout's opening stock and mark every recording's signals as unapplied."""
    ctrl.reset_inventory()
    await ctrl.broadcast_state_snapshot()
    return {"status": "success"}


@router.get("/history")
def get_history(limit: int = Query(100, ge=1, le=1000), ctrl: ReplayController = Depends(get_controller)):
    """Inventory history, newest first: signals, receipts, disposals, corrections, resets."""
    history = ctrl.store.history
    return {"history": list(reversed(history[-limit:])), "total": len(history)}


@router.post("/replay/control")
async def control_replay(req: ReplayControlRequest, ctrl: ReplayController = Depends(get_controller)):
    """Play, pause, restart, or seek. Signals apply the first time the playhead passes them."""
    if not ctrl.current:
        raise HTTPException(status_code=400, detail="No recording is in the player")
    if req.action == "play":
        ctrl.play()
    elif req.action == "pause":
        ctrl.pause()
    elif req.action == "restart":
        ctrl.restart()
        ctrl.play()
    elif req.action == "seek" and req.media_time_ms is not None:
        ctrl.seek(req.media_time_ms)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown action {req.action!r}")
    await ctrl.broadcast_state_snapshot()
    return {"status": "success", "is_playing": ctrl.is_playing, "media_time_ms": int(ctrl.current_media_time_ms)}


@router.post("/inventory/receipts")
async def receive_stock(req: ReceiveStockRequest, ctrl: ReplayController = Depends(get_controller)):
    """Receive a shipment into live stock: bottles go straight onto the medication's shelf."""
    engine = ctrl.engine
    try:
        receipt = engine.receive_stock(
            medication_key=req.medication_key,
            bottle_count=req.bottle_count,
            tablets_per_bottle=req.tablets_per_bottle,
            expiry_date=req.expiry_date,
            lot_number=(req.lot_number or "").strip() or None,
            received_at=req.received_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            today_iso=pharmacy_today(),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await ctrl.commit(
        "receive_stock",
        f"Received {receipt.bottle_count} bottle(s) of {receipt.medication_key}.",
        receipt_id=receipt.receipt_id, medication_key=receipt.medication_key,
        bottles=receipt.bottle_count, tablets=receipt.total_tablets, expiry_date=receipt.expiry_date,
    )
    return {"status": "success", "receipt": receipt.model_dump()}


@router.post("/inventory/disposals/{disposal_id}")
async def submit_disposal_form(disposal_id: str, req: DisposalSubmitRequest, ctrl: ReplayController = Depends(get_controller)):
    """Submit employee selection and discarded tablet quantity for a disposal record."""
    engine = ctrl.engine
    try:
        record = engine.resolve_disposal(
            disposal_id=disposal_id,
            selected_receipt_id=req.selected_receipt_id,
            explicit_quantity=req.explicit_quantity,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await ctrl.commit(
        "disposal",
        f"Disposed {record.medication_key} bottle identified as batch {record.selected_receipt_id}.",
        disposal_id=disposal_id, receipt_id=record.selected_receipt_id,
        tablets=record.quantity_deducted, default_quantity=record.is_default_quantity,
    )
    return {"status": "success", "disposal": record.model_dump()}


@router.post("/transactions/{transaction_id}/status")
async def update_transaction_status(transaction_id: str, req: PrescriptionStatusRequest, ctrl: ReplayController = Depends(get_controller)):
    """Update prescription transaction status and apply idempotent tablet deduction."""
    engine = ctrl.engine
    if transaction_id not in engine.transactions:
        raise HTTPException(status_code=404, detail="Prescription not found")
    applied = engine.process_prescription_deduction(transaction_id, req.status)
    await ctrl.commit(
        "prescription", f"Prescription {transaction_id} marked {req.status}.",
        transaction_id=transaction_id, status=req.status, deduction_applied=applied,
    )
    return {
        "status": "success",
        "transaction_id": transaction_id,
        "new_status": req.status,
        "deduction_applied": applied,
    }


@router.post("/inventory/confirmations/{alert_id}")
async def resolve_alert(alert_id: str, req: ConfirmationRequest, ctrl: ReplayController = Depends(get_controller)):
    """Resolve an alert. Uncertainty alerts require the employee-confirmed region, which is applied."""
    engine = ctrl.engine
    alert = engine.alerts.get(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    if alert.alert_type == "uncertainty":
        if not req.resolved_region_id:
            raise HTTPException(status_code=400, detail="Choose the region the bottle was at.")
        try:
            engine.confirm_location(alert_id, req.resolved_region_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    elif alert.alert_type == "expiry":
        raise HTTPException(status_code=400, detail="Expiry alerts clear when the expired bottles are disposed of.")
    else:
        alert.status = "resolved"
    summary = (
        f"Employee confirmed the {alert.metadata.get('phase')} location as {req.resolved_region_id}."
        if alert.alert_type == "uncertainty" else f"Resolved {alert.alert_type.replace('_', ' ')} alert."
    )
    await ctrl.commit("correction", summary, alert_id=alert_id, alert_type=alert.alert_type,
                      region_id=req.resolved_region_id, medication_key=alert.medication_key)
    return {"status": "success", "alert": engine.alerts[alert_id].model_dump()}
