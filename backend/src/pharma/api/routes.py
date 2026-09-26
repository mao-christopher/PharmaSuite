"""REST API route handlers for Pharmacy Inventory Dashboard & Replay System."""

import hashlib
import json
import re
import shutil
from datetime import date, datetime, timezone
from typing import Optional

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from pharma.api.replay_stream import ReplayController, ScenarioNotReady, pharmacy_today, scenario_meta
from pharma.db.models import BACKGROUND_PATTERN, Layout
from pharma.services.layout import layout_path, list_layout_ids, load_layout, save_layout
from pharma.services.recordings import (
    EVENTS_FILE, VIDEO_EXTENSIONS, find_video, parse_events_file, probe_video, write_events,
)

router = APIRouter(prefix="/api")

# Global controller instance (initialized in main.py)
controller: Optional[ReplayController] = None

MAX_EVENTS_BYTES = 1_000_000
MAX_IMAGE_BYTES = 20_000_000


def get_controller() -> ReplayController:
    if controller is None:
        raise HTTPException(status_code=500, detail="ReplayController not initialized")
    return controller


def require_engine(ctrl: ReplayController):
    if not ctrl.engine:
        raise HTTPException(status_code=400, detail="No recording is loaded")
    return ctrl.engine


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


@router.get("/scenarios")
def list_scenarios(ctrl: ReplayController = Depends(get_controller)):
    """List recordings with their layout and skeleton-processing status."""
    scenarios = []
    if ctrl.scenarios_dir.exists():
        for item in sorted(ctrl.scenarios_dir.iterdir()):
            if item.is_dir() and not item.name.startswith("."):
                meta = scenario_meta(item)
                job = ctrl.processing.get(item.name, {})
                scenarios.append({
                    "name": item.name,
                    "label": meta.get("label") or item.name,
                    "layout_id": meta.get("layout_id"),
                    "has_video": find_video(item) is not None,
                    "status": ctrl.scenario_status(item),
                    "progress": job.get("progress"),
                    "error": job.get("error"),
                })
    return {"scenarios": scenarios, "current": ctrl.current_scenario_name}


@router.post("/scenarios/{scenario_name}/load")
async def load_scenario(scenario_name: str, ctrl: ReplayController = Depends(get_controller)):
    """Reset state and load a recording into the replay engine."""
    try:
        res = ctrl.load_scenario(scenario_name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ScenarioNotReady as e:
        raise HTTPException(status_code=409, detail=str(e))
    await ctrl.broadcast_state_snapshot()
    return {"status": "success", "data": res}


@router.post("/scenarios/{scenario_name}/process")
def process_scenario(scenario_name: str, ctrl: ReplayController = Depends(get_controller)):
    """(Re)run skeleton extraction for a recording's video."""
    try:
        ctrl.start_processing(scenario_name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"status": "processing"}


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
    (path / "scenario.json").write_text(
        json.dumps({"layout_id": layout_id, "label": label}, indent=2) + "\n", encoding="utf-8"
    )
    ctrl.start_processing(folder)

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
async def put_layout(layout_id: str, layout: Layout, ctrl: ReplayController = Depends(get_controller)):
    """Save a layout, bump its calibration version, and reload the recording if it uses it."""
    if layout.layout_id != layout_id:
        raise HTTPException(status_code=400, detail="layout_id in body does not match URL")
    if layout.background_image and not (ctrl.layouts_dir / layout_id / layout.background_image).exists():
        raise HTTPException(status_code=400, detail="Background image was not uploaded")
    saved = save_layout(ctrl.layouts_dir, layout)
    reloaded = False
    if ctrl.layout and ctrl.layout.layout_id == layout_id and ctrl.current_scenario_name:
        ctrl.load_scenario(ctrl.current_scenario_name)
        await ctrl.broadcast_state_snapshot()
        reloaded = True
    return {"layout": saved.model_dump(mode="json"), "scenario_reloaded": reloaded}


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
    require_engine(ctrl)
    return ctrl.state_dict()


@router.post("/replay/control")
async def control_replay(req: ReplayControlRequest, ctrl: ReplayController = Depends(get_controller)):
    """Play, pause, restart, or seek. Restart and seek rebuild state from the recording's seed."""
    require_engine(ctrl)
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
    engine = require_engine(ctrl)
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
    await ctrl.broadcast_state_snapshot()
    return {"status": "success", "receipt": receipt.model_dump()}


@router.post("/inventory/disposals/{disposal_id}")
async def submit_disposal_form(disposal_id: str, req: DisposalSubmitRequest, ctrl: ReplayController = Depends(get_controller)):
    """Submit employee selection and discarded tablet quantity for a disposal record."""
    engine = require_engine(ctrl)
    try:
        record = engine.resolve_disposal(
            disposal_id=disposal_id,
            selected_receipt_id=req.selected_receipt_id,
            explicit_quantity=req.explicit_quantity,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await ctrl.broadcast_state_snapshot()
    return {"status": "success", "disposal": record.model_dump()}


@router.post("/transactions/{transaction_id}/status")
async def update_transaction_status(transaction_id: str, req: PrescriptionStatusRequest, ctrl: ReplayController = Depends(get_controller)):
    """Update prescription transaction status and apply idempotent tablet deduction."""
    engine = require_engine(ctrl)
    applied = engine.process_prescription_deduction(transaction_id, req.status)
    await ctrl.broadcast_state_snapshot()
    return {
        "status": "success",
        "transaction_id": transaction_id,
        "new_status": req.status,
        "deduction_applied": applied,
    }


@router.post("/inventory/confirmations/{alert_id}")
async def resolve_alert(alert_id: str, req: ConfirmationRequest, ctrl: ReplayController = Depends(get_controller)):
    """Resolve an alert. Uncertainty alerts require the employee-confirmed region, which is applied."""
    engine = require_engine(ctrl)
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
    await ctrl.broadcast_state_snapshot()
    return {"status": "success", "alert": engine.alerts[alert_id].model_dump()}
