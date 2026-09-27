"""Live wristband capture: the browser's camera lease and band event uploads.

Each band notification arrives with the camera frames from PRE_ROLL_MS before to
POST_ROLL_MS after it. An accepted event becomes its own recording (source "live"),
analyzed once on upload: H.264 clip, YOLO pose, wrist-to-region decision, inventory.
Replaying the clip later never applies it again.

This route runs outside the request-wide inventory lock: encoding and pose take
seconds and must not stall playback. It takes the lock only to read the held bottle
and to apply the event.
"""

import asyncio
import json
import math
import re
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from pharma.api.routes import get_controller
from pharma.api.replay_stream import ReplayController
from pharma.db.models import Layout
from pharma.db.repository import StateConflict, StateTooLarge, StorageUnavailable
from pharma.services import live_capture as live
from pharma.services.inventory_engine import point_in_polygon
from pharma.services.recordings import EVENTS_FILE, POSES_FILE, PoseTrack
from pharma.services.store import now_iso

router = APIRouter(prefix="/api/live")

MAX_LIVE_FRAME_BYTES = 400_000
STAGING_DIR = ".live-staging"  # dot-prefixed, so the recording library skips it


class LiveLeaseRequest(BaseModel):
    capture_id: uuid.UUID


@router.post("/lease")
async def renew_live_lease(req: LiveLeaseRequest, ctrl: ReplayController = Depends(get_controller)):
    try:
        ctrl.renew_live(str(req.capture_id))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await ctrl.broadcast_state_snapshot()
    return {"status": "active"}


@router.delete("/lease")
async def stop_live_lease(req: LiveLeaseRequest, ctrl: ReplayController = Depends(get_controller)):
    ctrl.stop_live(str(req.capture_id))
    await ctrl.broadcast_state_snapshot()
    return {"status": "stopped"}


@dataclass
class LiveEvent:
    event_id: str
    capture_id: str
    live_session_id: str
    event_type: str  # pickup | release
    band_id: str
    wrist: str
    layout_id: str
    calibration_version: int
    frame_times_ms: List[float]
    notification_ms: float
    notification_epoch_ms: float
    source: str  # band | dev (keyboard stand-in for the band)

    @property
    def name(self) -> str:
        return live.clip_name(self.event_id)


def parse_event(metadata: str, frame_count: int) -> LiveEvent:
    data = json.loads(metadata)
    event = LiveEvent(
        event_id=str(uuid.UUID(data["event_id"])),
        capture_id=str(uuid.UUID(data["capture_id"])),
        live_session_id=str(uuid.UUID(data.get("live_session_id") or data["capture_id"])),
        event_type={"P": "pickup", "D": "release"}[data["code"]],
        band_id=str(data["band_id"]),
        wrist=data["wrist"],
        layout_id=data["layout_id"],
        calibration_version=int(data["calibration_version"]),
        frame_times_ms=[float(t) for t in data["frame_times_ms"]],
        notification_ms=float(data["notification_ms"]),
        notification_epoch_ms=float(data["notification_epoch_ms"]),
        source=data.get("source", "band"),
    )
    times = event.frame_times_ms
    if not re.fullmatch(r"[0-9A-Za-z_-]{2,32}", event.band_id) or event.wrist not in live.WRIST_INDEX:
        raise ValueError("Invalid wristband identity or wrist")
    if event.source not in ("band", "dev"):
        raise ValueError("Unknown event source")
    if frame_count > live.MAX_FRAMES or len(times) != frame_count:
        raise ValueError(f"Provide at most {live.MAX_FRAMES} timestamped camera frames")
    if not all(math.isfinite(t) for t in times + [event.notification_ms, event.notification_epoch_ms]):
        raise ValueError("Camera timestamps must be finite")
    if any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError("Camera timestamps must increase")
    if times and (times[0] < event.notification_ms - live.PRE_ROLL_MS - live.EDGE_TOLERANCE_MS
                  or times[-1] > event.notification_ms + live.POST_ROLL_MS + live.EDGE_TOLERANCE_MS):
        raise ValueError("Camera frames must fall within the clip window around the notification")
    return event


@asynccontextmanager
async def inventory_transaction(ctrl: ReplayController):
    """The request-wide middleware's lock/refresh/rollback, for one block of this route."""
    async with ctrl.inventory_lock:
        try:
            ctrl.store.refresh()
            ctrl.storage_error = None
            yield
        except (StorageUnavailable, StateConflict, StateTooLarge) as exc:
            ctrl.store.rollback()
            ctrl.is_playing = False
            ctrl.storage_error = str(exc)
            await ctrl.broadcast_json({"type": "storage_error", "message": str(exc)})
            status = 409 if isinstance(exc, StateConflict) else 507 if isinstance(exc, StateTooLarge) else 503
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        except BaseException:
            ctrl.store.rollback()
            raise


def seen(ctrl: ReplayController, event: LiveEvent) -> bool:
    return event.event_id in ctrl.store.applied_event_ids(event.name)


def duplicate_response(ctrl: ReplayController, event: LiveEvent) -> Dict[str, Any]:
    live_meta = ctrl.store.recordings.get(event.name, {}).get("live", {})
    return {"status": "duplicate", "event_id": event.event_id,
            "recording": event.name if (ctrl.scenarios_dir / event.name).is_dir() else None,
            "ignored": live_meta.get("ignored")}


def ignore(ctrl: ReplayController, event: LiveEvent, reason: str) -> Dict[str, Any]:
    """Keep the raw event in history, store no footage, change no stock."""
    entry = ctrl.store.recording_entry(event.name)
    entry["applied_event_ids"].append(event.event_id)
    entry["live"] = {**live_meta(event, movement_id=None, clip=False), "ignored": reason}
    verb = "Pickup" if event.event_type == "pickup" else "Put-down"
    why = "a bottle is already in hand" if reason == "pickup_while_holding" else "no bottle is in hand"
    ctrl.store.record("live_ignored", f"Ignored live {verb.lower()}: {why} (treated as a false detection).",
                      recording=event.name, event_id=event.event_id, reason=reason, band_id=event.band_id,
                      source=event.source, notification_epoch_ms=event.notification_epoch_ms)
    ctrl.store.save()
    return {"status": "ignored", "event_id": event.event_id, "reason": reason}


def live_meta(event: LiveEvent, movement_id: Optional[str], clip: bool) -> Dict[str, Any]:
    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "movement_id": movement_id,
        "live_session_id": event.live_session_id,
        "capture_id": event.capture_id,
        "band_id": event.band_id,
        "wrist": event.wrist,
        "source": event.source,
        "captured_at": datetime.fromtimestamp(event.notification_epoch_ms / 1000, timezone.utc).isoformat(timespec="seconds"),
        "ingested_at": time.time(),
        "clip": clip,
    }


def analyse(event: LiveEvent, raw_frames: List[bytes], view: Layout, staging: Path) -> Dict[str, Any]:
    """Encode the clip, run pose, and decide the region. Runs in a worker thread; touches no state."""
    from pharma import pose as pose_module
    from pharma.config import Settings

    images = []
    for raw in raw_frames:
        image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("Invalid camera frame")
        images.append(image)
    times = event.frame_times_ms
    calibration_changed = view.calibration_version != event.calibration_version
    width, height = view.frame_width, view.frame_height
    poses: List[Optional[List[List[float]]]] = []
    people: List[int] = []
    fps, codec = None, None
    if images:
        height, width = images[0].shape[:2]
        if any(image.shape[:2] != (height, width) for image in images):
            raise ValueError("Camera frame dimensions changed")
        staging.mkdir(parents=True, exist_ok=True)
        fps = live.clip_fps(times)
        codec = live.write_clip(images, fps, staging / "video.mp4")
        # The thumbnail shows the estimated moment of the action, which precedes the notification.
        action_ms = event.notification_ms - live.BAND_LATENCY_MS
        at = min(range(len(times)), key=lambda i: abs(times[i] - action_ms))
        live.write_thumbnail(images[at], staging / "thumb.jpg")
        cfg = Settings()
        if calibration_changed:
            # Old footage is not reinterpreted against new region geometry.
            poses = [None] * len(images)
        else:
            # A second visible person makes the event uncertain instead of silently
            # following a different technician.
            poses = pose_module.extract_video_keypoints(staging / "video.mp4", len(images), require_single_person=True,
                                                        person_counts=people, imgsz=cfg.pose_imgsz)
        PoseTrack(fps, width, height, poses).save(staging / POSES_FILE, Path(pose_module.resolve_model_path(cfg)).name,
                                                  cfg.pose_imgsz)

    region_id, evidence = live.associate_wrist(poses, view.regions, event.wrist, event.event_type)
    if any(count > 1 for count in people):
        region_id, evidence["reason"] = None, "multiple_people"
    if images and abs(width / height - view.frame_width / view.frame_height) > .02:
        region_id, evidence["reason"] = None, "camera_aspect_mismatch"
    if not images:
        region_id, evidence["reason"] = None, "no_camera_frames"
    elif len(poses) != len(images) or not live.buffer_complete(times, event.notification_ms):
        region_id, evidence["reason"] = None, "incomplete_clip_window"
    if calibration_changed:
        region_id, evidence["reason"] = None, "calibration_changed"
        evidence["event_calibration_version"] = event.calibration_version
        evidence["current_calibration_version"] = view.calibration_version

    # The engine receives only the selected wrist inside the decided region.
    selected_hand = []
    region = next((r for r in view.regions if r.region_id == region_id), None)
    if region:
        for pose in reversed(poses):
            hand = live.event_hand(pose, event.wrist)
            if hand and hand[0][2] >= 0.35 and point_in_polygon(hand[0][0], hand[0][1], region.polygon):
                selected_hand = hand
                break
    return {"clip": bool(images), "width": width, "height": height, "fps": fps, "codec": codec,
            "region_id": region_id if selected_hand else None, "evidence": evidence, "hand": selected_hand}


def write_recording(ctrl: ReplayController, event: LiveEvent, movement_id: str, result: Dict[str, Any],
                    staging: Path, signal: Dict[str, Any], label: str) -> None:
    """Move an analyzed clip into the library as a ready recording."""
    target = ctrl.scenarios_dir / event.name
    if target.exists():  # left behind by an earlier attempt whose save failed
        shutil.rmtree(target)
    (staging / EVENTS_FILE).write_text(json.dumps(signal) + "\n", encoding="utf-8")
    times = event.frame_times_ms
    meta = {
        "label": label,
        "source": live.LIVE_SOURCE,
        "layout_id": event.layout_id,
        "view_confirmed": True,
        "uploaded_at": now_iso(),
        "video_filename": "video.mp4",
        "duration_ms": round(len(times) / result["fps"] * 1000),
        "width": result["width"],
        "height": result["height"],
        "fps": result["fps"],
        "live": {
            "event_id": event.event_id, "event_type": event.event_type, "movement_id": movement_id,
            "live_session_id": event.live_session_id, "band_id": event.band_id, "wrist": event.wrist,
            "source": event.source, "calibration_version": event.calibration_version, "codec": result["codec"],
            "pre_roll_ms": live.PRE_ROLL_MS, "post_roll_ms": live.POST_ROLL_MS,
        },
    }
    (staging / "scenario.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    staging.replace(target)


@router.post("/events")
async def ingest_live_event(
    metadata: str = Form(...),
    frames: Optional[List[UploadFile]] = File(None),
    ctrl: ReplayController = Depends(get_controller),
):
    """Process one band notification with the camera frames around it."""
    frames = frames or []
    try:
        event = parse_event(metadata, len(frames))
        view = ctrl.view(event.layout_id)
    except (KeyError, ValueError, TypeError, FileNotFoundError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raw_frames = []
    for upload in frames:
        raw = await upload.read(MAX_LIVE_FRAME_BYTES + 1)
        if len(raw) > MAX_LIVE_FRAME_BYTES:
            raise HTTPException(status_code=400, detail="Camera frame exceeds 400 KB")
        raw_frames.append(raw)

    async with inventory_transaction(ctrl):
        if ctrl.live_active() and ctrl.live_owner != event.capture_id:
            raise HTTPException(status_code=409, detail="Another browser tab owns live camera capture")
        if seen(ctrl, event):
            return duplicate_response(ctrl, event)
        problem = live.sequence_problem(event.event_type, live.held_movement(ctrl.store.recordings, ctrl.engine.sessions))
        if problem:
            # Checked before analysis so a false detection never writes footage.
            response = ignore(ctrl, event, problem)
            await ctrl.broadcast_state_snapshot()
            return response

    staging = ctrl.scenarios_dir / STAGING_DIR / event.event_id
    shutil.rmtree(staging, ignore_errors=True)
    try:
        try:
            result = await asyncio.to_thread(analyse, event, raw_frames, view, staging)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # encoder or pose failure: the browser keeps the event and retries
            raise HTTPException(status_code=503, detail=f"Clip analysis failed: {exc}") from exc

        async with inventory_transaction(ctrl):
            if seen(ctrl, event):
                return duplicate_response(ctrl, event)
            held = live.held_movement(ctrl.store.recordings, ctrl.engine.sessions)
            problem = live.sequence_problem(event.event_type, held)
            if problem:
                response = ignore(ctrl, event, problem)
                await ctrl.broadcast_state_snapshot()
                return response
            # A pickup starts a movement; the put-down continues the one in hand.
            movement_id = event.event_id if event.event_type == "pickup" else held
            times = event.frame_times_ms
            signal = {
                "event_id": event.event_id, "event_type": event.event_type, "session_id": movement_id,
                "sensor_id": event.band_id, "timestamp": event.notification_epoch_ms / 1000,
                "media_time_ms": round(event.notification_ms - times[0]) if times else 0, "schema_version": "1.0",
                "details": {"live_session_id": event.live_session_id, "capture_id": event.capture_id,
                            "source": event.source, "notification_ms": event.notification_ms,
                            "frame_times_ms": times, "association": result["evidence"]},
            }
            verb = "Pickup" if event.event_type == "pickup" else "Put-down"
            local = datetime.fromtimestamp(event.notification_epoch_ms / 1000).strftime("%H:%M:%S")
            label = f"{verb} at {local}"
            if result["clip"]:
                write_recording(ctrl, event, movement_id, result, staging, signal, label)
            activity = ctrl.store.apply_event(
                event.name, signal, result["hand"], (result["width"], result["height"]), label,
                regions=view.regions, layout_id=event.layout_id, joint="wrist" if result["hand"] else None,
                camera_id=event.layout_id, calibration_version=event.calibration_version,
                session_scope=live.LIVE_SCOPE)
            entry = ctrl.store.recordings[event.name]
            reason = result["evidence"].get("reason")
            entry["live"] = {**live_meta(event, movement_id, result["clip"]),
                             "region_id": result["region_id"], "reason": reason}
            # The engine only saw an empty hand list; keep the clip's actual reason for the employee.
            for alert in ctrl.engine.alerts.values():
                if alert.metadata.get("recording") == event.name and "live_reason" not in alert.metadata:
                    alert.metadata["live_reason"] = reason
            ctrl.store.save()
            await ctrl.broadcast_state_snapshot()
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    # A decided region can still need an employee: e.g. which bottle came off a mixed shelf.
    waiting = not result["region_id"] or (activity or {}).get("state") == "NEEDS_CONFIRMATION"
    return {"status": "needs_confirmation" if waiting else "applied",
            "event_id": event.event_id, "movement_id": movement_id, "region_id": result["region_id"],
            "recording": event.name if result["clip"] else None,
            "activity": activity, "evidence": result["evidence"]}
