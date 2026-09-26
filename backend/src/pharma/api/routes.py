"""REST API route handlers for Pharmacy Inventory Dashboard & Replay System."""

import csv
import io
import json
import re
import shutil
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from pharma.api.replay_stream import ReplayController, ScenarioNotReady, pharmacy_today
from pharma.db.models import BACKGROUND_PATTERN, Layout, Region, medication_key_for
from pharma.services.layout import layout_path, load_layout, save_background, save_catalog, save_layout, split_view
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
    # For a pickup whose put-down is also unresolved: settle both in one step.
    release_region_id: Optional[str] = None


class AssignViewRequest(BaseModel):
    action: str  # use | replace | new
    layout_id: Optional[str] = None
    name: Optional[str] = Field(default=None, max_length=80)
    regions: Optional[List[Region]] = None


class BackgroundFromRecordingRequest(BaseModel):
    recording: str


class DisposeBatchRequest(BaseModel):
    bottles: int = Field(..., ge=1)
    tablets: Optional[int] = Field(default=None, ge=0)


class NewPrescriptionRequest(BaseModel):
    medication_key: str
    quantity: int = Field(..., ge=1)
    transaction_id: Optional[str] = Field(default=None, max_length=40)
    status: str = "created"


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
    layout_id: Optional[str] = Form(None),
    ctrl: ReplayController = Depends(get_controller),
):
    """Upload a video plus pickup/release timestamps; skeletons are extracted in the background.

    Without a layout_id the most similar saved camera view is suggested and used until
    the employee confirms or edits it.
    """
    ext = "." + (video.filename or "").rsplit(".", 1)[-1].lower() if "." in (video.filename or "") else ""
    if ext not in VIDEO_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported video type. Use one of: {', '.join(sorted(VIDEO_EXTENSIONS))}")
    if layout_id:
        try:
            load_layout(ctrl.layouts_dir, layout_id)
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
    try:
        suggestions = ctrl.suggest_views(folder) if not layout_id else []
    except FileNotFoundError:
        suggestions = []
    layout_id = layout_id or (suggestions[0]["layout_id"] if suggestions else ctrl.default_layout_id)
    layout = ctrl.view(layout_id)
    meta = {
        "layout_id": layout_id,
        "view_confirmed": False,
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
        "width": info.width,
        "height": info.height,
        "events": len(parsed),
        "status": "processing",
        "layout_id": layout_id,
        "view_suggestions": suggestions,
        "warnings": warnings,
    }


@router.get("/recordings/{name}/frame")
def recording_frame(name: str, ctrl: ReplayController = Depends(get_controller)):
    """The recording's raw frame, used as the backdrop when annotating its camera view."""
    frame = _recording_errors(lambda: ctrl.video_frame(name))
    ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return Response(content=jpeg.tobytes(), media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})


@router.get("/recordings/{name}/views")
def recording_view_suggestions(name: str, ctrl: ReplayController = Depends(get_controller)):
    """Saved camera views ranked by similarity to this recording's frame."""
    suggestions = _recording_errors(lambda: ctrl.suggest_views(name))
    frame = ctrl.video_frame(name)
    meta = ctrl.recording_summary(ctrl.scenario_dir(name))
    return {
        "suggestions": suggestions,
        "width": frame.shape[1],
        "height": frame.shape[0],
        "layout_id": meta["layout_id"],
        "label": meta["label"],
    }


@router.post("/recordings/{name}/view")
async def assign_recording_view(name: str, req: AssignViewRequest, ctrl: ReplayController = Depends(get_controller)):
    """Use a view as is, replace it with edits on this recording's frame, or save a new view."""
    try:
        view = _recording_errors(lambda: ctrl.assign_view(name, req.action, req.layout_id, req.name, req.regions))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    await ctrl.broadcast_state_snapshot()
    return {"layout": view.model_dump(mode="json")}


# ---------------------------------------------------------------- layouts


@router.get("/layouts")
def list_layouts(ctrl: ReplayController = Depends(get_controller)):
    """Saved camera views and how many recordings use each."""
    usage: Dict[str, List[Dict[str, Any]]] = {}
    for r in ctrl.list_recordings():
        usage.setdefault(r["layout_id"] or ctrl.default_layout_id, []).append(
            {"name": r["name"], "label": r["label"], "width": r["width"], "height": r["height"], "has_video": r["has_video"]}
        )
    return {
        "default": ctrl.default_layout_id,
        "layouts": [
            {
                "layout_id": v.layout_id,
                "name": v.name or v.layout_id,
                "calibration_version": v.calibration_version,
                "frame_width": v.frame_width,
                "frame_height": v.frame_height,
                "background_image": v.background_image,
                "regions": len(v.regions),
                "recordings": usage.get(v.layout_id, []),
            }
            for v in ctrl.views()
        ],
    }


@router.get("/layouts/{layout_id}")
def get_layout(layout_id: str, ctrl: ReplayController = Depends(get_controller)):
    """A camera view with the shared medications and opening stock merged in."""
    try:
        return ctrl.view(layout_id).model_dump(mode="json")
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/layouts/{layout_id}")
async def put_layout(
    layout_id: str,
    layout: Layout,
    reset_inventory: bool = Query(False, description="Also restore live inventory to the opening stock"),
    ctrl: ReplayController = Depends(get_controller),
):
    """Save a camera view and the shared catalog (medications, opening stock).

    Bumps the view's calibration version. Live inventory is kept unless reset.
    """
    if layout.layout_id != layout_id:
        raise HTTPException(status_code=400, detail="layout_id in body does not match URL")
    if layout.background_image and not (ctrl.layouts_dir / layout_id / layout.background_image).exists():
        raise HTTPException(status_code=400, detail="Background image was not uploaded")
    view, catalog = split_view(layout)
    catalog = save_catalog(ctrl.layouts_dir, catalog)
    saved = save_layout(ctrl.layouts_dir, layout)
    notes = ctrl.apply_layout(saved, reset_inventory=reset_inventory, catalog=catalog)
    await ctrl.broadcast_state_snapshot()
    return {
        "layout": ctrl.view(layout_id).model_dump(mode="json"),
        "inventory_reset": reset_inventory,
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
    filename = save_background(folder, data, decoded, is_png=data[:8] == b"\x89PNG\r\n\x1a\n")
    height, width = decoded.shape[:2]
    return {"background_image": filename, "width": width, "height": height}


@router.post("/layouts/{layout_id}/background-from-recording")
def background_from_recording(
    layout_id: str, req: BackgroundFromRecordingRequest, ctrl: ReplayController = Depends(get_controller)
):
    """Use a recording's own frame as the view photo, so regions line up with its video exactly."""
    return _recording_errors(lambda: ctrl.background_from_recording(layout_id, req.recording))


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
        ctrl.use_view_for_alert(alert)
        session_id = alert.metadata.get("session_id")
        try:
            engine.confirm_location(alert_id, req.resolved_region_id)
            if req.release_region_id and alert.metadata.get("phase") == "pickup":
                follow_up = next(
                    (a for a in engine.alerts.values()
                     if a.alert_type == "uncertainty" and a.status == "open"
                     and a.metadata.get("session_id") == session_id and a.metadata.get("phase") == "release"),
                    None,
                )
                if follow_up:
                    follow_up.metadata.setdefault("layout_id", alert.metadata.get("layout_id"))
                    engine.confirm_location(follow_up.alert_id, req.release_region_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        for a in engine.alerts.values():  # alerts raised by the confirmation keep their view
            if a.metadata.get("session_id") == session_id and "layout_id" not in a.metadata:
                a.metadata.update(recording=alert.metadata.get("recording"), layout_id=alert.metadata.get("layout_id"))
        confirmed = {alert.metadata.get("phase"): req.resolved_region_id}
        if req.release_region_id:
            confirmed["release"] = req.release_region_id
        ctrl.store.mark_confirmed(session_id, confirmed)
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


# ---------------------------------------------------------------- stock & prescriptions


@router.post("/inventory/receipts/{receipt_id}/dispose")
async def dispose_batch(receipt_id: str, req: DisposeBatchRequest, ctrl: ReplayController = Depends(get_controller)):
    """Remove bottles of a batch from its shelf (for example expired stock found by an employee)."""
    try:
        record = ctrl.engine.dispose_batch(receipt_id, req.bottles, req.tablets)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await ctrl.commit(
        "disposal",
        f"Disposed {req.bottles} bottle(s) of {record.medication_key} from batch {receipt_id}.",
        receipt_id=receipt_id, bottles=req.bottles, tablets=record.quantity_deducted,
        default_quantity=record.is_default_quantity,
    )
    return {"status": "success", "disposal": record.model_dump()}


def _add_prescription(ctrl: ReplayController, req: NewPrescriptionRequest):
    return ctrl.engine.add_transaction(
        req.medication_key, req.quantity, (req.transaction_id or "").strip() or None, req.status
    )


@router.post("/transactions")
async def create_prescription(req: NewPrescriptionRequest, ctrl: ReplayController = Depends(get_controller)):
    """Add a prescription by hand. Filled or paid prescriptions deduct their tablets once."""
    try:
        tx = _add_prescription(ctrl, req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await ctrl.commit("prescription", f"Added prescription {tx.transaction_id}.", transaction_id=tx.transaction_id)
    return {"status": "success", "transaction": tx.model_dump()}


STATUS_ALIASES = {
    "": "created", "created": "created", "waiting": "created", "new": "created",
    "confirmed_fill": "confirmed_fill", "filled": "confirmed_fill", "fill": "confirmed_fill",
    "paid": "paid", "cancelled": "cancelled", "canceled": "cancelled",
}


def parse_prescriptions(content: str, medications) -> List[NewPrescriptionRequest]:
    """Prescriptions from CSV (header row) or JSON/JSONL.

    Columns: quantity, and either medication_key or medication/name + strength;
    optional transaction_id (or rx) and status.
    """
    text = content.strip().lstrip("\ufeff")
    if not text:
        raise ValueError("The file is empty.")
    if text.startswith("["):
        rows = json.loads(text)
    elif text.startswith("{"):
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        rows = list(csv.DictReader(io.StringIO(text)))
    keys = {m.medication_key for m in medications}
    out = []
    for n, raw in enumerate(rows, start=1):
        row = {str(k).strip().lower(): (v.strip() if isinstance(v, str) else v) for k, v in raw.items()}
        key = row.get("medication_key")
        if not key and (row.get("medication") or row.get("name")) and row.get("strength"):
            key = medication_key_for(str(row.get("medication") or row.get("name")), str(row["strength"]))
        if not key:
            raise ValueError(f"Row {n}: give medication_key, or medication and strength.")
        if key not in keys:
            raise ValueError(f"Row {n}: {key} is not a configured medication.")
        try:
            quantity = int(float(row.get("quantity") or row.get("qty") or 0))
        except (TypeError, ValueError):
            raise ValueError(f"Row {n}: quantity must be a whole number.") from None
        status = STATUS_ALIASES.get(str(row.get("status") or "").strip().lower())
        if status is None:
            raise ValueError(f"Row {n}: unknown status {row.get('status')!r}; use waiting, filled, paid or cancelled.")
        try:
            out.append(NewPrescriptionRequest(
                medication_key=key, quantity=quantity, status=status,
                transaction_id=(row.get("transaction_id") or row.get("rx") or None),
            ))
        except Exception:
            raise ValueError(f"Row {n}: quantity must be at least 1.") from None
    if not out:
        raise ValueError("The file has no prescriptions.")
    return out


@router.post("/transactions/import")
async def import_prescriptions(file: UploadFile = File(...), ctrl: ReplayController = Depends(get_controller)):
    """Add several prescriptions from a CSV or JSON file. Nothing is added if any row is invalid."""
    raw = await file.read(MAX_EVENTS_BYTES + 1)
    if len(raw) > MAX_EVENTS_BYTES:
        raise HTTPException(status_code=400, detail="File is too large.")
    try:
        requests = parse_prescriptions(raw.decode("utf-8-sig"), ctrl.catalog.medications)
        ids = [r.transaction_id for r in requests if r.transaction_id]
        dupes = {i for i in ids if ids.count(i) > 1} | {i for i in ids if i in ctrl.engine.transactions}
        if dupes:
            raise ValueError(f"Prescription IDs already used: {', '.join(sorted(dupes))}.")
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    created = [_add_prescription(ctrl, r) for r in requests]
    await ctrl.commit("prescription", f"Imported {len(created)} prescription(s) from {file.filename}.",
                      transaction_ids=[t.transaction_id for t in created])
    return {"status": "success", "transactions": [t.model_dump() for t in created]}
