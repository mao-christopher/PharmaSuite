"""REST API route handlers for Pharmacy Inventory Dashboard & Replay System."""

import csv
import io
import json
import math
import os
import re
import shutil
import time
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, TypeAdapter, ValidationError, field_validator

from pharma.api.replay_stream import DRAFT_FILE, ReplayController, ScenarioNotReady, pharmacy_today
from pharma.db.models import BACKGROUND_PATTERN, Catalog, Layout, Region, medication_key_for
from pharma.services.layout import (
    DEFAULT_LAYOUT_ID, layout_path, list_layout_ids, load_layout, merge_view, new_layout_id, save_background,
    save_catalog, save_layout, split_view,
)
from pharma.services.room import list_rooms, save_room
from pharma.services.multicamera import MEDIA_CLOCK, write_spec
from pharma.services.recordings import EVENTS_FILE, POSES_FILE, VIDEO_EXTENSIONS, parse_events_file, probe_video, write_events
from pharma.services.live_capture import associate_wrist, event_hand
from pharma.services.inventory_engine import point_in_polygon

router = APIRouter(prefix="/api")

# Global controller instance (initialized in main.py)
controller: Optional[ReplayController] = None

MAX_EVENTS_BYTES = 1_000_000
MAX_IMAGE_BYTES = 20_000_000
MAX_LIVE_FRAME_BYTES = 400_000


def get_controller() -> ReplayController:
    if controller is None:
        raise HTTPException(status_code=500, detail="ReplayController not initialized")
    return controller


class LiveLeaseRequest(BaseModel):
    capture_id: uuid.UUID


@router.post("/live/lease")
async def renew_live_lease(req: LiveLeaseRequest, ctrl: ReplayController = Depends(get_controller)):
    try:
        ctrl.renew_live(str(req.capture_id))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await ctrl.broadcast_state_snapshot()
    return {"status": "active"}


@router.delete("/live/lease")
async def stop_live_lease(req: LiveLeaseRequest, ctrl: ReplayController = Depends(get_controller)):
    ctrl.stop_live(str(req.capture_id))
    await ctrl.broadcast_state_snapshot()
    return {"status": "stopped"}


@router.post("/live/events")
async def ingest_live_event(
    metadata: str = Form(...),
    frames: Optional[List[UploadFile]] = File(None),
    ctrl: ReplayController = Depends(get_controller),
):
    """Process one browser BLE notification with its preceding camera frames."""
    frames = frames or []
    try:
        data = json.loads(metadata)
        event_id = str(uuid.UUID(data["event_id"]))
        capture_id = str(uuid.UUID(data["capture_id"]))
        kind = {"P": "pickup", "D": "release"}[data["code"]]
        band_id = str(data["band_id"])
        wrist = data["wrist"]
        layout_id = data["layout_id"]
        calibration = int(data["calibration_version"])
        times = [float(t) for t in data["frame_times_ms"]]
        notification_ms = float(data["notification_ms"])
        notification_epoch_ms = float(data["notification_epoch_ms"])
        if not re.fullmatch(r"[0-9A-Za-z_-]{2,32}", band_id) or wrist not in {"left", "right"}:
            raise ValueError("Invalid wristband identity or wrist")
        if len(frames) > 60 or len(times) != len(frames):
            raise ValueError("Provide at most 60 timestamped camera frames")
        if not all(math.isfinite(t) for t in times + [notification_ms, notification_epoch_ms]):
            raise ValueError("Camera timestamps must be finite")
        if any(b <= a for a, b in zip(times, times[1:])) or (times and times[-1] > notification_ms + 200):
            raise ValueError("Camera timestamps must precede the notification")
        view = ctrl.view(layout_id)
        calibration_changed = view.calibration_version != calibration
    except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    recording = "live"
    if ctrl.live_active() and ctrl.live_owner != capture_id:
        raise HTTPException(status_code=409, detail="Another browser tab owns live camera capture")
    if event_id in ctrl.store.applied_event_ids(recording):
        return {"status": "duplicate", "event_id": event_id,
                "clip_url": (f"/api/live/clips/{capture_id}/{event_id}" if
                             (ctrl.scenarios_dir.parent / "live_clips" / capture_id / f"{event_id}.mp4").is_file()
                             else None)}
    if ctrl.is_playing:
        ctrl.pause()
    images = []
    for upload in frames:
        raw = await upload.read(MAX_LIVE_FRAME_BYTES + 1)
        if len(raw) > MAX_LIVE_FRAME_BYTES:
            raise HTTPException(status_code=400, detail="Camera frame exceeds 400 KB")
        image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise HTTPException(status_code=400, detail="Invalid camera frame")
        images.append(image)
    width, height = view.frame_width, view.frame_height
    poses = []
    clip_path = None
    people_per_frame: List[int] = []
    if images:
        height, width = images[0].shape[:2]
        if any(image.shape[:2] != (height, width) for image in images):
            raise HTTPException(status_code=400, detail="Camera frame dimensions changed")
        clip_dir = ctrl.scenarios_dir.parent / "live_clips" / capture_id
        clip_dir.mkdir(parents=True, exist_ok=True)
        clip_path = clip_dir / f"{event_id}.mp4"
        fps = min(30.0, max(1.0, (len(images) - 1) * 1000 / max(times[-1] - times[0], 1)))
        writer = cv2.VideoWriter(str(clip_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
        if not writer.isOpened():
            raise HTTPException(status_code=503, detail="MP4 encoder unavailable")
        try:
            for image in images:
                writer.write(image)
        finally:
            writer.release()
        cv2.imwrite(str(clip_dir / f"{event_id}.jpg"), images[-1])

        # Reuse the package's YOLO pose path; a second visible person makes the
        # event uncertain instead of silently selecting a different technician.
        from pharma.pose import extract_video_keypoints
        if not calibration_changed:
            try:
                poses = extract_video_keypoints(clip_path, len(images), require_single_person=True,
                                                person_counts=people_per_frame)
            except Exception as exc:
                raise HTTPException(status_code=503, detail=f"Pose extraction failed: {exc}") from exc
    complete = (len(images) >= 3 and len(poses) == len(images) and
                times[0] <= notification_ms - 4800 and
                notification_ms - times[-1] <= 300 and
                all(b - a <= 300 for a, b in zip(times, times[1:])))
    region_id, evidence = associate_wrist(poses, view.regions, wrist, kind)
    if any(count > 1 for count in people_per_frame):
        region_id, evidence["reason"] = None, "multiple_people"
    if images and abs(width / height - view.frame_width / view.frame_height) > .02:
        region_id, evidence["reason"] = None, "camera_aspect_mismatch"
    if not images:
        region_id, evidence["reason"] = None, "no_camera_frames"
    elif not complete:
        region_id, evidence["reason"] = None, "incomplete_five_second_buffer"
    if calibration_changed:
        region_id, evidence["reason"] = None, "calibration_changed"
        evidence["event_calibration_version"] = calibration
        evidence["current_calibration_version"] = view.calibration_version
    region = next((r for r in view.regions if r.region_id == region_id), None)
    selected_hand = []
    if region:
        for pose in reversed(poses):
            hand = event_hand(pose, wrist)
            if hand and hand[0][2] >= 0.35 and point_in_polygon(hand[0][0], hand[0][1], region.polygon):
                selected_hand = hand
                break
    event = {"event_id": event_id, "event_type": kind, "session_id": "one_bottle",
             "sensor_id": band_id, "timestamp": notification_epoch_ms / 1000,
             "media_time_ms": round(notification_ms - times[0]) if times else 0, "schema_version": "1.0",
             "details": {"capture_id": capture_id, "notification_ms": notification_ms,
                         "frame_times_ms": times,
                         "clip": str(clip_path.relative_to(ctrl.scenarios_dir.parent)) if clip_path else None,
                         "association": evidence}}
    previous = ctrl.engine.sessions.get("live:one_bottle")
    blocked_sequence = ((kind == "pickup" and previous is not None and
                         previous.state in {"HELD", "NEEDS_CONFIRMATION"}) or
                        (kind == "release" and (previous is None or previous.state not in
                         {"HELD", "NEEDS_CONFIRMATION"})))
    activity = ctrl.store.apply_event(recording, event, selected_hand, (width, height),
                                      "Live wristband", regions=view.regions, layout_id=layout_id,
                                      joint="wrist" if selected_hand else None,
                                      camera_id=layout_id, calibration_version=calibration)
    ctrl.store.save()
    await ctrl.broadcast_state_snapshot()
    status = "reconciliation" if blocked_sequence else "applied" if selected_hand else "needs_confirmation"
    return {"status": status,
            "event_id": event_id, "region_id": region_id if selected_hand else None,
            "activity": activity, "evidence": evidence,
            "clip_url": f"/api/live/clips/{capture_id}/{event_id}" if clip_path else None}


@router.get("/live/clips/{capture_id}/{event_id}")
def live_clip(capture_id: uuid.UUID, event_id: uuid.UUID, ctrl: ReplayController = Depends(get_controller)):
    path = ctrl.scenarios_dir.parent / "live_clips" / str(capture_id) / f"{event_id}.mp4"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Clip not found")
    return FileResponse(path, media_type="video/mp4")


@router.get("/live/clips/{capture_id}/{event_id}/thumbnail")
def live_thumbnail(capture_id: uuid.UUID, event_id: uuid.UUID, ctrl: ReplayController = Depends(get_controller)):
    path = ctrl.scenarios_dir.parent / "live_clips" / str(capture_id) / f"{event_id}.jpg"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    return FileResponse(path, media_type="image/jpeg")


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
    action: str  # use | replace (this frame becomes the view's photo) | new
    layout_id: Optional[str] = None
    name: Optional[str] = Field(default=None, max_length=80)
    camera_id: Optional[str] = None  # which camera of a multi-camera recording


class NewViewRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)


class ViewPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    background_image: Optional[str] = Field(default=None, pattern=BACKGROUND_PATTERN)


class BackgroundFromRecordingRequest(BaseModel):
    recording: str
    camera_id: Optional[str] = None


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
async def apply_recording(name: str, include_earlier: bool = False, ctrl: ReplayController = Depends(get_controller)):
    """Apply every remaining signal of a recording to live inventory without playing it.

    Uploads are assumed to have happened in upload order; include_earlier first applies
    earlier uploads that still have signals left, oldest first.
    """
    if include_earlier:
        result = _recording_errors(lambda: ctrl.apply_in_order(name))
    else:
        result = {"applied": _recording_errors(lambda: ctrl.apply_recording(name)), "earlier_applied": [], "earlier_waiting": []}
    await ctrl.broadcast_state_snapshot()
    return {"status": "success", **result, "recording": ctrl.recording_summary(ctrl.scenario_dir(name))}


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


def _video_ext(upload: UploadFile) -> str:
    filename = upload.filename or ""
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in VIDEO_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported video type ({filename or 'no file name'}). Use one of: {', '.join(sorted(VIDEO_EXTENSIONS))}",
        )
    return ext


def _pick_view(suggestions: List[Dict[str, Any]], taken: set, fallback: str) -> str:
    """Best-matching view, preferring one no other camera of this upload already uses."""
    for s in suggestions:
        if s["has_photo"] and s["layout_id"] not in taken:
            return s["layout_id"]
    return suggestions[0]["layout_id"] if suggestions else fallback


class UploadViewChoice(BaseModel):
    """The employee's decision for one camera, made before the upload finishes."""

    camera_id: str
    action: str  # use | replace | new (see AssignViewRequest)
    layout_id: Optional[str] = None
    name: Optional[str] = Field(default=None, max_length=80)


DRAFT_MAX_AGE_S = 24 * 3600


def _prune_drafts(ctrl: ReplayController) -> None:
    """Drop uploads whose window was closed without finishing (or cancelling) long ago."""
    if not ctrl.drafts_dir.exists():
        return
    cutoff = time.time() - DRAFT_MAX_AGE_S
    for path in ctrl.drafts_dir.iterdir():
        if path.is_dir() and path.stat().st_mtime < cutoff:
            shutil.rmtree(path, ignore_errors=True)


def _discard_draft(ctrl: ReplayController, path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
    try:
        ctrl.drafts_dir.rmdir()  # only when no other draft is open
    except OSError:
        pass


def _stage_videos(ctrl: ReplayController, uploads: List[UploadFile]) -> Path:
    """Save one or more camera videos into a new draft. The first is the main camera."""
    exts = [_video_ext(v) for v in uploads]
    _prune_drafts(ctrl)
    path = ctrl.drafts_dir / f"draft-{uuid.uuid4().hex[:12]}"
    path.mkdir(parents=True)
    cameras: List[Dict[str, Any]] = []
    try:
        for n, (upload, ext) in enumerate(zip(uploads, exts), start=1):
            # The first camera is the recording's primary video; the rest sit beside it.
            file = f"video{ext}" if n == 1 else f"camera-{n}{ext}"
            with (path / file).open("wb") as out:
                shutil.copyfileobj(upload.file, out, length=1024 * 1024)
            try:
                info = probe_video(path / file)
            except ValueError as e:
                raise ValueError(f"{upload.filename}: {e}") from None
            cameras.append({"camera_id": f"camera-{n}", "label": upload.filename or f"Camera {n}", "video": file,
                            "poses": POSES_FILE if n == 1 else f"camera-{n}-poses.json",
                            "width": info.width, "height": info.height, "fps": round(info.fps, 3),
                            "duration_ms": info.duration_ms})
    except ValueError as e:
        _discard_draft(ctrl, path)
        raise HTTPException(status_code=400, detail=str(e))
    draft = {"cameras": cameras, "video_filename": uploads[0].filename,
             "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    (path / DRAFT_FILE).write_text(json.dumps(draft, indent=2) + "\n", encoding="utf-8")
    return path


def _draft_summary(path: Path) -> Dict[str, Any]:
    draft = json.loads((path / DRAFT_FILE).read_text(encoding="utf-8"))
    main = draft["cameras"][0]
    return {
        "draft_id": path.name,
        "label": (draft.get("video_filename") or "recording").rsplit(".", 1)[0],
        "duration_ms": main["duration_ms"],
        "width": main["width"],
        "height": main["height"],
        "cameras": [{k: c[k] for k in ("camera_id", "label", "width", "height", "fps", "duration_ms")}
                    for c in draft["cameras"]],
    }


def _finish_upload(
    ctrl: ReplayController, draft_path: Path, raw_events: bytes, events_filename: Optional[str],
    name: Optional[str], layout_id: Optional[str], choices: List[UploadViewChoice],
) -> Dict[str, Any]:
    """Turn a draft into a recording: parse its times, settle each camera's view, start skeletons.

    Validation happens before anything moves, so a rejected request leaves the draft to fix.
    """
    draft = json.loads((draft_path / DRAFT_FILE).read_text(encoding="utf-8"))
    cameras: List[Dict[str, Any]] = draft["cameras"]
    main = cameras[0]
    multi = len(cameras) > 1
    if layout_id:
        try:
            load_layout(ctrl.layouts_dir, layout_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e))
    if len(raw_events) > MAX_EVENTS_BYTES:
        raise HTTPException(status_code=400, detail="Timestamps file is too large.")
    try:
        text = raw_events.decode("utf-8-sig")
        parsed = parse_events_file(text, main["duration_ms"])
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    by_camera = {c["camera_id"]: c for c in cameras}
    chosen: Dict[str, UploadViewChoice] = {}
    for choice in choices:
        if choice.camera_id not in by_camera:
            raise HTTPException(status_code=400, detail=f"Unknown camera '{choice.camera_id}'.")
        if choice.action not in ("use", "replace", "new"):
            raise HTTPException(status_code=400, detail=f"Unknown action {choice.action!r}; use use, replace or new.")
        if choice.action != "new":
            try:
                ctrl.view(choice.layout_id or ctrl.default_layout_id)
            except (FileNotFoundError, ValueError) as e:
                raise HTTPException(status_code=400, detail=str(e))
        chosen[choice.camera_id] = choice

    label = (name or (draft.get("video_filename") or "recording").rsplit(".", 1)[0]).strip()[:80] or "recording"
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:40] or "recording"
    folder = f"upload-{slug}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    suffix = 2
    while (ctrl.scenarios_dir / folder).exists():  # same name within the same second
        folder = f"upload-{slug}-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{suffix}"
        suffix += 1
    path = ctrl.scenarios_dir / folder
    os.replace(draft_path, path)
    (path / DRAFT_FILE).unlink()
    _discard_draft(ctrl, draft_path)  # nothing left there; drops the drafts folder if empty
    write_events(path / EVENTS_FILE, parsed)
    source_ext = (events_filename or "").rsplit(".", 1)[-1].lower() if "." in (events_filename or "") else "txt"
    (path / f"events-source.{source_ext if source_ext.isalnum() else 'txt'}").write_text(text, encoding="utf-8")

    warnings = []
    for cam in cameras:
        # Uploads follow each view's current calibration (None) and start unconfirmed.
        cam.update(layout_id=ctrl.default_layout_id, calibration_version=None, view_confirmed=False)
        if abs(cam["duration_ms"] - main["duration_ms"]) > 1000:
            warnings.append(
                f"{cam['label']} is {cam['duration_ms'] / 1000:.1f}s long but {main['label']} is "
                f"{main['duration_ms'] / 1000:.1f}s. Cameras are matched from their first frame; check they started together."
            )
    spec = {"schema_version": 1, "clock": MEDIA_CLOCK, "source": "upload", "cameras": cameras}
    if multi:
        write_spec(path, spec)  # each camera's frame must be readable to suggest its view
    # Cameras the employee didn't settle get the most similar saved view, still unconfirmed.
    taken = {c.layout_id or ctrl.default_layout_id for c in chosen.values() if c.action != "new"}
    suggestions: Dict[str, List[Dict[str, Any]]] = {}
    for n, cam in enumerate(cameras):
        if cam["camera_id"] in chosen:
            choice = chosen[cam["camera_id"]]
            # A new view doesn't exist yet; assign_view below points the camera at it.
            cam["layout_id"] = ctrl.default_layout_id if choice.action == "new" else choice.layout_id or ctrl.default_layout_id
            suggestions[cam["camera_id"]] = []
            continue
        if layout_id and n == 0:
            ranked = []
        else:
            try:
                ranked = ctrl.suggest_views(folder, cam["camera_id"] if multi else None)
            except FileNotFoundError:
                ranked = []
        cam["layout_id"] = layout_id if (layout_id and n == 0) else _pick_view(ranked, taken, ctrl.default_layout_id)
        taken.add(cam["layout_id"])
        suggestions[cam["camera_id"]] = ranked
    if multi:
        write_spec(path, spec)
    layout = ctrl.view(cameras[0]["layout_id"])
    meta = {
        "layout_id": layout.layout_id,
        "view_confirmed": False,
        "label": label,
        "source": "upload",
        # Millisecond precision: upload order is the order the events are assumed to have happened.
        "uploaded_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "video_filename": draft.get("video_filename"),
        "events_filename": events_filename,
        "duration_ms": main["duration_ms"],
        "fps": main["fps"],
        "width": main["width"],
        "height": main["height"],
        "calibration_version": layout.calibration_version,
    }
    (path / "scenario.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    for camera_id, choice in chosen.items():
        try:
            ctrl.assign_view(folder, choice.action, choice.layout_id, choice.name, camera_id if multi else None)
        except (ValueError, FileNotFoundError) as e:
            warnings.append(f"{by_camera[camera_id]['label']}: the view wasn't saved ({e}). Check it on Recordings.")
    ctrl.start_processing(folder)
    ctrl.store.record("recording_uploaded", f"Uploaded recording {label}.", recording=folder, events=len(parsed),
                      cameras=len(cameras))
    ctrl.store.save()

    summary = ctrl.recording_summary(path)
    final = summary["cameras"] or [{"label": main["label"], "layout_id": summary["layout_id"],
                                    "width": main["width"], "height": main["height"]}]
    for cam in final:
        view = ctrl.view(cam["layout_id"])
        if abs(cam["width"] / cam["height"] - view.frame_width / view.frame_height) > 0.02:
            warnings.append(
                f"{cam['label']} is {cam['width']}x{cam['height']} but view '{view.name or view.layout_id}' was "
                f"annotated at {view.frame_width}x{view.frame_height}; regions may not line up."
            )
    return {
        "name": folder,
        "label": label,
        "duration_ms": main["duration_ms"],
        "width": main["width"],
        "height": main["height"],
        "events": len(parsed),
        "status": "processing",
        "layout_id": summary["layout_id"],
        "view_confirmed": summary["view_confirmed"],
        "view_suggestions": suggestions[main["camera_id"]],
        "cameras": [{"camera_id": c["camera_id"], "label": c["label"], "layout_id": c["layout_id"]}
                    for c in summary["cameras"]] if multi else [],
        "warnings": warnings,
    }


@router.post("/recordings")
async def upload_recording(
    video: UploadFile = File(...),
    events: UploadFile = File(...),
    extra_videos: List[UploadFile] = File(default=[]),
    name: Optional[str] = Form(None),
    layout_id: Optional[str] = Form(None),
    ctrl: ReplayController = Depends(get_controller),
):
    """Upload one or more camera videos plus pickup/release timestamps in one request.

    Skeletons are extracted in the background. Each camera gets the most similar saved
    view until the employee confirms or edits it. With several videos (filmed at the same
    time, starting together) the player switches to whichever camera sees the arm.
    """
    uploads = [video] + [v for v in extra_videos if v.filename]
    for v in uploads:
        _video_ext(v)
    if layout_id:
        try:
            load_layout(ctrl.layouts_dir, layout_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e))
    raw_events = await events.read(MAX_EVENTS_BYTES + 1)
    draft = _stage_videos(ctrl, uploads)
    try:
        return _finish_upload(ctrl, draft, raw_events, events.filename, name, layout_id, [])
    finally:
        _discard_draft(ctrl, draft)


@router.post("/uploads")
async def start_upload(
    video: UploadFile = File(...),
    extra_videos: List[UploadFile] = File(default=[]),
    ctrl: ReplayController = Depends(get_controller),
):
    """Stage camera videos while the employee marks times and checks each camera's boxes."""
    path = _stage_videos(ctrl, [video] + [v for v in extra_videos if v.filename])
    return _draft_summary(path)


@router.get("/uploads/{draft_id}/frame")
def upload_frame(draft_id: str, camera: Optional[str] = None, ctrl: ReplayController = Depends(get_controller)):
    frame = _recording_errors(lambda: ctrl.draft_frame(draft_id, camera))
    ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return Response(content=jpeg.tobytes(), media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})


@router.get("/uploads/{draft_id}/views")
def upload_view_suggestions(draft_id: str, camera: Optional[str] = None, ctrl: ReplayController = Depends(get_controller)):
    """Saved camera views ranked by similarity to one staged camera's first frame."""
    frame = _recording_errors(lambda: ctrl.draft_frame(draft_id, camera))
    return {"suggestions": ctrl.rank_views(frame), "width": frame.shape[1], "height": frame.shape[0],
            "camera_id": camera}


@router.delete("/uploads/{draft_id}")
def cancel_upload(draft_id: str, ctrl: ReplayController = Depends(get_controller)):
    _discard_draft(ctrl, _recording_errors(lambda: ctrl.draft_dir(draft_id)))
    return {"deleted": draft_id}


@router.post("/uploads/{draft_id}/finish")
async def finish_upload(
    draft_id: str,
    events: UploadFile = File(...),
    name: Optional[str] = Form(None),
    views: Optional[str] = Form(None),
    ctrl: ReplayController = Depends(get_controller),
):
    """Finish a staged upload with its pickup/put-down times and each camera's view decision."""
    path = _recording_errors(lambda: ctrl.draft_dir(draft_id))
    try:
        choices = TypeAdapter(List[UploadViewChoice]).validate_json(views) if views else []
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"Invalid camera views: {e.errors()[0]['msg']}")
    raw_events = await events.read(MAX_EVENTS_BYTES + 1)
    result = _finish_upload(ctrl, path, raw_events, events.filename, name, None, choices)
    await ctrl.broadcast_state_snapshot()
    return result


@router.get("/recordings/{name}/frame")
def recording_frame(name: str, camera: Optional[str] = None, ctrl: ReplayController = Depends(get_controller)):
    """A camera's raw frame, used as the backdrop when annotating its view."""
    frame = _recording_errors(lambda: ctrl.video_frame(name, camera))
    ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return Response(content=jpeg.tobytes(), media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})


@router.get("/recordings/{name}/views")
def recording_view_suggestions(name: str, camera: Optional[str] = None, ctrl: ReplayController = Depends(get_controller)):
    """Saved camera views ranked by similarity to this recording's (or one camera's) frame."""
    suggestions = _recording_errors(lambda: ctrl.suggest_views(name, camera))
    frame = ctrl.video_frame(name, camera)
    meta = ctrl.recording_summary(ctrl.scenario_dir(name))
    cam = next((c for c in meta["cameras"] if c["camera_id"] == camera), None)
    return {
        "suggestions": suggestions,
        "width": frame.shape[1],
        "height": frame.shape[0],
        "layout_id": cam["layout_id"] if cam else meta["layout_id"],
        "label": meta["label"],
        "camera_id": camera,
        "cameras": meta["cameras"],
    }


@router.post("/recordings/{name}/view")
async def assign_recording_view(name: str, req: AssignViewRequest, ctrl: ReplayController = Depends(get_controller)):
    """Use a view as is, replace it with edits on this recording's frame, or save a new view."""
    try:
        view = _recording_errors(lambda: ctrl.assign_view(name, req.action, req.layout_id, req.name, req.camera_id))
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
        base = {"name": r["name"], "label": r["label"], "width": r["width"], "height": r["height"],
                "has_video": r["has_video"], "camera_id": None}
        if not r["cameras"]:
            usage.setdefault(r["layout_id"] or ctrl.default_layout_id, []).append(base)
        for cam in r["cameras"]:  # each camera of a multi-camera recording uses its own view
            usage.setdefault(cam["layout_id"], []).append({
                **base, "label": f"{r['label']} ({cam['label']})", "camera_id": cam["camera_id"],
                "width": cam.get("width"), "height": cam.get("height"),
            })
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
                "regions_source": v.regions_source.model_dump() if v.regions_source else None,
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


@router.post("/layouts")
async def create_layout(req: NewViewRequest, ctrl: ReplayController = Depends(get_controller)):
    """A new, empty camera view. Give it a photo, then register its camera on the Room page."""
    layout_id = new_layout_id(ctrl.layouts_dir, req.name)
    view = merge_view(Layout(layout_id=layout_id, name=req.name.strip()), ctrl.catalog)
    saved = save_layout(ctrl.layouts_dir, view)
    ctrl.store.record("layout", f"Added camera view {saved.name}.", layout_id=layout_id)
    ctrl.store.save()
    ctrl._views.pop(layout_id, None)
    await ctrl.broadcast_state_snapshot()
    return {"layout": ctrl.view(layout_id).model_dump(mode="json")}


@router.patch("/layouts/{layout_id}")
async def patch_layout(layout_id: str, req: ViewPatch, ctrl: ReplayController = Depends(get_controller)):
    """Rename a view, or give it a new photo (uploaded or taken from a recording beforehand).

    A new photo keeps the view's regions and registration. The Room page then checks the
    registration against the new photo; if the camera moved, register it again.
    """
    try:
        stored = load_layout(ctrl.layouts_dir, layout_id)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=404, detail=str(e))
    update: Dict[str, Any] = {}
    if req.name is not None:
        update["name"] = req.name.strip()
    if req.background_image is not None and req.background_image != stored.background_image:
        image = cv2.imread(str(ctrl.layouts_dir / layout_id / req.background_image))
        if image is None:
            raise HTTPException(status_code=400, detail="Background image was not uploaded")
        update.update(background_image=req.background_image, frame_width=image.shape[1], frame_height=image.shape[0])
    if not update:
        return {"layout": ctrl.view(layout_id).model_dump(mode="json")}
    photo = "background_image" in update
    saved = save_layout(ctrl.layouts_dir, merge_view(stored.model_copy(update=update), ctrl.catalog), bump=photo)
    ctrl.store.record("layout", f"{'New photo for' if photo else 'Renamed'} camera view {saved.name or layout_id}.",
                      layout_id=layout_id, calibration_version=saved.calibration_version)
    ctrl.apply_layout(saved)
    await ctrl.broadcast_state_snapshot()
    return {"layout": ctrl.view(layout_id).model_dump(mode="json")}


@router.delete("/layouts/{layout_id}")
async def delete_layout(layout_id: str, ctrl: ReplayController = Depends(get_controller)):
    """Remove a camera view no recording uses (and its registration in any room)."""
    try:
        view = ctrl.view(layout_id)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=404, detail=str(e))
    if layout_id in (ctrl.default_layout_id, DEFAULT_LAYOUT_ID):
        raise HTTPException(status_code=409, detail="The main camera view can't be deleted.")
    users = [r["label"] for r in ctrl.list_recordings()
             if r["layout_id"] == layout_id or any(c["layout_id"] == layout_id for c in r["cameras"])]
    if users:
        raise HTTPException(status_code=409, detail=f"Recordings use this view: {', '.join(users[:5])}"
                                                    f"{'…' if len(users) > 5 else ''}. Delete them or move them to another view first.")
    for room in list_rooms(ctrl.rooms_dir):
        if any(c.layout_id == layout_id for c in room.cameras):
            save_room(ctrl.rooms_dir, room.model_copy(update={"cameras": [c for c in room.cameras if c.layout_id != layout_id]}))
    shutil.rmtree(layout_path(ctrl.layouts_dir, layout_id).parent)
    ctrl._views.pop(layout_id, None)
    ctrl.store.record("layout", f"Deleted camera view {view.name or layout_id}.", layout_id=layout_id)
    ctrl.store.save()
    await ctrl.broadcast_state_snapshot()
    return {"deleted": layout_id}


# ---------------------------------------------------------------- catalog


@router.get("/catalog")
def get_catalog(ctrl: ReplayController = Depends(get_controller)):
    """Medications and opening stock, shared by every camera view."""
    return ctrl.catalog.model_dump(mode="json")


@router.put("/catalog")
async def put_catalog(
    catalog: Catalog,
    reset_inventory: bool = Query(False, description="Also restore live inventory to the opening stock"),
    ctrl: ReplayController = Depends(get_controller),
):
    """Save medications and opening stock. Live inventory is kept unless reset.

    A medication can't be removed while a 3D shelf (or an older hand-drawn shelf) still
    holds it: remove or reassign that shelf on the Room page first.
    """
    keys = {m.medication_key for m in catalog.medications}
    problems = [f"{room.name or room.room_id}: shelf {r.region_id} holds {r.medication_key}"
                for room in list_rooms(ctrl.rooms_dir) for r in room.regions
                if r.region_type == "designated_shelf" and r.medication_key not in keys]
    for layout_id in list_layout_ids(ctrl.layouts_dir):
        try:
            merge_view(load_layout(ctrl.layouts_dir, layout_id), catalog)
        except ValueError as e:
            problems.append(f"Camera view {layout_id}: {e}")
    if problems:
        raise HTTPException(status_code=422, detail="Reassign these shelves first. " + "; ".join(problems))
    saved = save_catalog(ctrl.layouts_dir, catalog)
    notes = ctrl.apply_catalog(saved, reset_inventory=reset_inventory)
    await ctrl.broadcast_state_snapshot()
    return {"catalog": saved.model_dump(mode="json"), "inventory_reset": reset_inventory, "notes": notes}


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
    try:
        stored = load_layout(ctrl.layouts_dir, layout_id)
    except FileNotFoundError:
        stored = None
    if stored and stored.regions_source is not None:
        # Generated regions follow the room; hand edits would be overwritten on the next change.
        if [r.model_dump() for r in layout.regions] != [r.model_dump() for r in stored.regions]:
            raise HTTPException(status_code=422, detail=f"This view's regions come from room "
                                f"{stored.regions_source.room_id}. Edit its 3D boxes on the Room page.")
        layout = layout.model_copy(update={"regions_source": stored.regions_source})
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
    return _recording_errors(lambda: ctrl.background_from_recording(layout_id, req.recording, req.camera_id))


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
    if ctrl.live_active() and req.action != "pause":
        raise HTTPException(status_code=409, detail="Stop live camera before replaying a recording")
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


# ---------------------------------------------------------------- suggestions


@router.post("/suggestions/{suggestion_id}/dismiss")
async def dismiss_suggestion(suggestion_id: str, ctrl: ReplayController = Depends(get_controller)):
    """Hide a stock suggestion. It comes back if the situation escalates or after a restock."""
    if suggestion_id not in {s["id"] for s in ctrl.suggestions()}:
        raise HTTPException(status_code=404, detail="That suggestion is no longer active.")
    ctrl.store.dismissed_suggestions[suggestion_id] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    await ctrl.commit("suggestion_dismissed", f"Dismissed suggestion {suggestion_id}.", suggestion_id=suggestion_id)
    return {"status": "dismissed", "id": suggestion_id}


@router.delete("/suggestions/dismissed")
async def restore_suggestions(ctrl: ReplayController = Depends(get_controller)):
    """Show every dismissed suggestion again."""
    count = len(ctrl.store.dismissed_suggestions)
    ctrl.store.dismissed_suggestions = {}
    await ctrl.commit("suggestions_restored", f"Restored {count} dismissed suggestion(s).")
    return {"status": "restored", "count": count}
