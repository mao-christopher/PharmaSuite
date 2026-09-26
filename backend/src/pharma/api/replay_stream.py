"""Recording library and player: media clock, event dispatch, MJPEG rendering, and WebSocket broadcast.

Inventory lives in a PharmacyStore that carries across recordings. Loading a recording
only puts it in the player; its signals change inventory the first time the playhead
passes them (or when it is applied without playback), and never again.
"""

import asyncio
import json
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import cv2
import numpy as np
from fastapi import WebSocket

from pharma.db.models import Catalog, Layout, Region
from pharma.services.fixture_loader import load_json, load_jsonl
from pharma.services.inventory_engine import MIN_KEYPOINT_CONF, Hand
from pharma.services.layout import (
    DEFAULT_LAYOUT_ID, frame_similarity, list_layout_ids, load_background, load_catalog, load_layout,
    merge_view, new_layout_id, save_catalog, save_frame_background, save_layout,
)
from pharma.services.multicamera import CameraGroup
from pharma.services.recordings import EVENTS_FILE, POSES_FILE, PoseTrack, VideoInfo, find_video, probe_video
from pharma.db.repository import StorageUnavailable, StateConflict, StateTooLarge
from pharma.services.store import PharmacyStore, now_iso, session_key

SCENARIO_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
SYNTHETIC_DURATION_MS = 10000
SYNTHETIC_FPS = 30
STREAM_MAX_WIDTH = 1280
THUMB_FILE = "thumb.jpg"
THUMB_WIDTH = 480
CLOCK_TICK_S = 1 / 60
CLOCK_BROADCAST_S = 0.25

# BGR colors, matched to the dashboard's region palette.
REGION_COLORS = {
    "designated_shelf": (224, 108, 31),
    "dispensing_counter": (10, 125, 196),
    "disposal": (56, 52, 206),
}
SKELETON_EDGES = [
    (5, 7), (7, 9), (6, 8), (8, 10), (5, 6), (5, 11), (6, 12), (11, 12),
    (11, 13), (13, 15), (12, 14), (14, 16), (0, 5), (0, 6),
]
SKELETON_COLOR = (80, 200, 40)
WRIST_COLOR = (153, 51, 147)


class ScenarioNotReady(Exception):
    pass


def scenario_meta(scenario_path: Path) -> Dict[str, Any]:
    meta = load_json(scenario_path / "scenario.json")
    return meta if isinstance(meta, dict) else {}


def scenario_layout_id(scenario_path: Path) -> Optional[str]:
    return scenario_meta(scenario_path).get("layout_id")


def pharmacy_today() -> str:
    """Date used for expiry checks; PHARMACY_DATE (YYYY-MM-DD) overrides the system date."""
    return os.getenv("PHARMACY_DATE") or date.today().isoformat()


def synthetic_hand(media_time_ms: float) -> Hand:
    """Scripted wrist path for recordings without video (the bundled demo)."""
    progress = (media_time_ms % SYNTHETIC_DURATION_MS) / SYNTHETIC_DURATION_MS
    return (0.2 + 0.6 * progress, 0.25 + 0.1 * float(np.sin(progress * 2 * np.pi)), 0.9)


@dataclass
class Recording:
    """One stored recording: its signals plus, for uploads, the video and pose track."""

    name: str
    path: Path
    meta: Dict[str, Any]
    events: List[Dict[str, Any]]
    video_path: Optional[Path]
    video: Optional[VideoInfo]
    poses: Optional[PoseTrack]
    camera_group: Optional[CameraGroup] = None

    @property
    def label(self) -> str:
        return self.meta.get("label") or self.name

    @property
    def layout_id(self) -> str:
        return self.meta.get("layout_id") or DEFAULT_LAYOUT_ID

    @property
    def duration_ms(self) -> int:
        return self.video.duration_ms if self.video else SYNTHETIC_DURATION_MS

    @property
    def fps(self) -> float:
        return self.video.fps if self.video else SYNTHETIC_FPS

    def hand_points_at(self, media_time_ms: float) -> Tuple[List[Hand], Optional[str]]:
        if self.camera_group:
            return self.camera_group.hands_at(media_time_ms)
        if self.poses:
            return self.poses.hand_points_at(media_time_ms, MIN_KEYPOINT_CONF)
        return [synthetic_hand(media_time_ms)], "wrist"

    def hands_at(self, media_time_ms: float) -> List[Hand]:
        return self.hand_points_at(media_time_ms)[0]


class ReplayController:
    def __init__(
        self,
        scenarios_dir: Path,
        layouts_dir: Optional[Path] = None,
        state_path: Optional[Path] = None,
        layout_id: str = "default",
        repository=None,
    ):
        self.inventory_lock = asyncio.Lock()
        self.storage_error = None
        self.scenarios_dir = scenarios_dir
        self.layouts_dir = layouts_dir or scenarios_dir.parent / "layouts"
        self.default_layout_id = layout_id
        self._views: Dict[str, Layout] = {}
        self.catalog: Catalog = load_catalog(self.layouts_dir)
        self.layout: Layout = self.view(layout_id)
        self.store = PharmacyStore.open(state_path or scenarios_dir.parent / "state" / "pharmacy.json", self.catalog, repository=repository)
        self.engine.trigger_expiry_alerts(pharmacy_today())
        self.store.save()
        self.background: Optional[np.ndarray] = load_background(self.layouts_dir, self.layout)
        self.current: Optional[Recording] = None
        self.active_websockets: Set[WebSocket] = set()
        self.is_playing: bool = False
        self.current_media_time_ms: float = 0
        self.generation = 0  # bumps whenever the player source or overlay changes
        self.processing: Dict[str, Dict[str, Any]] = {}
        self._last_tick: Optional[float] = None

    # ------------------------------------------------------------------ views

    def view(self, layout_id: str) -> Layout:
        """A camera view merged with the shared catalog (cached until a view or catalog is saved)."""
        if layout_id not in self._views:
            self._views[layout_id] = merge_view(load_layout(self.layouts_dir, layout_id), self.catalog)
        return self._views[layout_id]

    def views(self) -> List[Layout]:
        out = []
        for layout_id in list_layout_ids(self.layouts_dir):
            try:
                out.append(self.view(layout_id))
            except (FileNotFoundError, ValueError):
                continue
        return out

    def view_for_session(self, session_id: str) -> Optional[Layout]:
        """The view a movement session was recorded in (sessions are `<recording>:<id>`)."""
        recording = session_id.split(":", 1)[0] if ":" in session_id else None
        if not recording:
            return None
        try:
            return self.view(scenario_meta(self.scenario_dir(recording)).get("layout_id") or self.default_layout_id)
        except (FileNotFoundError, ValueError):
            return None

    def use_view_for_alert(self, alert) -> None:
        """Point the engine at the regions of the view an alert came from before resolving it."""
        view = None
        if alert.metadata.get("layout_id"):
            try:
                view = self.view(alert.metadata["layout_id"])
            except (FileNotFoundError, ValueError):
                view = None
        view = view or self.view_for_session(str(alert.metadata.get("session_id", ""))) or self.layout
        self.engine.regions = {r.region_id: r for r in view.regions}
        self.engine.frame_size = (view.frame_width, view.frame_height)

    def _set_player_view(self, layout_id: str) -> None:
        self.layout = self.view(layout_id)
        self.background = load_background(self.layouts_dir, self.layout)
        self.generation += 1

    # ------------------------------------------------------------------ accessors

    @property
    def engine(self):
        return self.store.engine

    @property
    def regions(self):
        return list(self.layout.regions)

    @property
    def current_scenario_name(self) -> Optional[str]:
        return self.current.name if self.current else None

    @property
    def processed_event_ids(self) -> Set[str]:
        return self.store.applied_event_ids(self.current.name) if self.current else set()

    @property
    def activity(self) -> List[Dict[str, Any]]:
        return self.store.recordings.get(self.current.name, {}).get("activity", []) if self.current else []

    @property
    def duration_ms(self) -> int:
        return self.current.duration_ms if self.current else 0

    @property
    def fps(self) -> float:
        return self.current.fps if self.current else SYNTHETIC_FPS

    @property
    def video_path(self) -> Optional[Path]:
        return self.current.video_path if self.current else None

    def frame_size_of(self, rec: Optional[Recording]) -> Tuple[int, int]:
        if rec and rec.video:
            return rec.video.width, rec.video.height
        return self.layout.frame_width, self.layout.frame_height

    def frame_size(self) -> Tuple[int, int]:
        return self.frame_size_of(self.current)

    # ------------------------------------------------------------------ recordings

    def scenario_dir(self, scenario_name: str) -> Path:
        path = self.scenarios_dir / scenario_name
        if not SCENARIO_NAME_PATTERN.fullmatch(scenario_name) or not path.is_dir():
            raise FileNotFoundError(f"Recording '{scenario_name}' not found")
        return path

    def scenario_status(self, path: Path) -> str:
        job = self.processing.get(path.name)
        if job and job["state"] in ("processing", "error"):
            return job["state"]
        if find_video(path) and not (path / POSES_FILE).exists():
            return "unprocessed"
        return "ready"

    def open_recording(self, name: str) -> Recording:
        path = self.scenario_dir(name)
        status = self.scenario_status(path)
        if status != "ready":
            raise ScenarioNotReady(f"Recording '{name}' is {status}; skeletons are not available yet.")
        meta = scenario_meta(path)
        layout_id = meta.get("layout_id")
        if not layout_id:
            raise FileNotFoundError(f"Recording '{name}' has no layout_id in scenario.json")
        group = CameraGroup.load(path) if (path / "multicam.json").exists() else None
        if group:
            for camera in group.cameras.values():
                view = self.view(camera.layout_id)
                if view.calibration_version != camera.calibration_version:
                    raise ValueError("Camera calibration changed; reprocess/review the synchronized recording")
        self.view(layout_id)  # the recording's camera view must exist
        video_path = find_video(path)
        return Recording(
            name=name,
            path=path,
            meta=meta,
            events=sorted(load_jsonl(path / EVENTS_FILE), key=lambda e: e["media_time_ms"]),
            video_path=video_path,
            video=probe_video(video_path) if video_path else None,
            poses=PoseTrack.load(path / POSES_FILE) if video_path else None,
            camera_group=group,
        )

    def load_scenario(self, scenario_name: str) -> Dict[str, Any]:
        """Put a recording in the player. Inventory is untouched until its signals play."""
        rec = self.open_recording(scenario_name)
        self.store.merge_transactions(load_json(rec.path / "transactions.json"))
        self.current = rec
        self.store.current_recording = rec.name
        self.store.save()
        self.current_media_time_ms = 0
        self.is_playing = False
        self._set_player_view(rec.layout_id)
        applied = self.processed_event_ids
        return {
            "scenario_name": rec.name,
            "label": rec.label,
            "layout_id": self.layout.layout_id,
            "calibration_version": self.layout.calibration_version,
            "events_count": len(rec.events),
            "events_applied": sum(1 for e in rec.events if e["event_id"] in applied),
            "has_video": rec.video_path is not None,
            "duration_ms": rec.duration_ms,
        }

    def restore_player(self, fallback: Optional[str] = None) -> None:
        """At startup, reopen whichever recording was in the player last."""
        for name in (self.store.current_recording, fallback):
            if not name:
                continue
            try:
                self.load_scenario(name)
                return
            except (FileNotFoundError, ScenarioNotReady, ValueError, KeyError):
                continue

    def apply_recording(self, name: str) -> int:
        """Apply every remaining signal of a recording without playing it."""
        rec = self.current if self.current and self.current.name == name else self.open_recording(name)
        added = self.store.merge_transactions(load_json(rec.path / "transactions.json"))
        changed = self._apply(rec, float("inf"))
        if added and not changed:
            self.store.save()
        return changed

    def delete_recording(self, name: str) -> None:
        path = self.scenario_dir(name)
        meta = scenario_meta(path)
        if meta.get("source") != "upload":
            raise PermissionError("Only uploaded recordings can be deleted; bundled fixtures stay.")
        if self.processing.get(name, {}).get("state") == "processing":
            raise PermissionError("Wait for skeleton extraction to finish before deleting.")
        if self.current and self.current.name == name:
            self.current = None
            self.store.current_recording = None
            self.is_playing = False
            self.current_media_time_ms = 0
            self._set_player_view(self.default_layout_id)
        shutil.rmtree(path)
        self.processing.pop(name, None)
        entry = self.store.recordings.get(name)
        if entry is not None:
            entry["deleted_at"] = now_iso()
        self.store.record("recording_deleted", f"Deleted recording {meta.get('label') or name}.", recording=name)
        self.store.save()

    def recording_summary(self, path: Path) -> Dict[str, Any]:
        meta = scenario_meta(path)
        job = self.processing.get(path.name, {})
        events = load_jsonl(path / EVENTS_FILE)
        applied = self.store.applied_event_ids(path.name)
        prefix = session_key(path.name, "")
        alerts = [a for a in self.engine.alerts.values() if str(a.metadata.get("session_id", "")).startswith(prefix)]
        entry = self.store.recordings.get(path.name, {})
        return {
            "name": path.name,
            "label": meta.get("label") or path.name,
            "source": meta.get("source", "fixture"),
            "layout_id": meta.get("layout_id"),
            "view_name": self._view_name(meta.get("layout_id")),
            "view_confirmed": meta.get("view_confirmed", meta.get("source") != "upload"),
            "uploaded_at": meta.get("uploaded_at"),
            "video_filename": meta.get("video_filename"),
            "events_filename": meta.get("events_filename"),
            "duration_ms": meta.get("duration_ms") or (None if find_video(path) else SYNTHETIC_DURATION_MS),
            "width": meta.get("width"),
            "height": meta.get("height"),
            "has_video": find_video(path) is not None,
            "status": self.scenario_status(path),
            "progress": job.get("progress"),
            "error": job.get("error"),
            "events_total": len(events),
            "pickups": sum(1 for e in events if e.get("event_type") == "pickup"),
            "releases": sum(1 for e in events if e.get("event_type") == "release"),
            "events_applied": sum(1 for e in events if e["event_id"] in applied),
            "first_applied_at": entry.get("first_applied_at"),
            "last_applied_at": entry.get("last_applied_at"),
            "alerts_total": len(alerts),
            "alerts_open": sum(1 for a in alerts if a.status == "open"),
            "in_player": self.current is not None and self.current.name == path.name,
        }

    def _view_name(self, layout_id: Optional[str]) -> Optional[str]:
        try:
            view = self.view(layout_id or self.default_layout_id)
        except (FileNotFoundError, ValueError):
            return None
        return view.name or view.layout_id

    def list_recordings(self) -> List[Dict[str, Any]]:
        if not self.scenarios_dir.exists():
            return []
        items = [
            self.recording_summary(p)
            for p in self.scenarios_dir.iterdir()
            if p.is_dir() and not p.name.startswith(".") and SCENARIO_NAME_PATTERN.fullmatch(p.name)
        ]
        # Newest uploads first; bundled fixtures (no upload time) last.
        return sorted(items, key=lambda r: (r["uploaded_at"] is not None, r["uploaded_at"] or "", r["name"]), reverse=True)

    def recording_detail(self, name: str) -> Dict[str, Any]:
        path = self.scenario_dir(name)
        summary = self.recording_summary(path)
        applied = self.store.applied_event_ids(name)
        prefix = session_key(name, "")
        return {
            **summary,
            "events": [{**e, "processed": e["event_id"] in applied} for e in load_jsonl(path / EVENTS_FILE)],
            "activity": self.store.recordings.get(name, {}).get("activity", []),
            "alerts": [a.model_dump() for a in self.engine.alerts.values()
                       if str(a.metadata.get("session_id", "")).startswith(prefix)],
        }

    def thumbnail_jpeg(self, name: str) -> bytes:
        path = self.scenario_dir(name)
        video_path = find_video(path)
        if not video_path:
            frame = self.annotate(self.base_frame(self.frame_size_of(None)), 0)
        else:
            cached = path / THUMB_FILE
            if cached.exists():
                return cached.read_bytes()
            events = load_jsonl(path / EVENTS_FILE)
            cap = cv2.VideoCapture(str(video_path))
            try:
                fps = cap.get(cv2.CAP_PROP_FPS) or SYNTHETIC_FPS
                count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 1)
                at_ms = events[0]["media_time_ms"] if events else count / fps * 500
                cap.set(cv2.CAP_PROP_POS_FRAMES, min(count - 1, int(at_ms * fps / 1000)))
                ok, frame = cap.read()
            finally:
                cap.release()
            if not ok:
                raise FileNotFoundError("Could not read a frame from the video")
        h, w = frame.shape[:2]
        frame = cv2.resize(frame, (THUMB_WIDTH, int(h * THUMB_WIDTH / w)), interpolation=cv2.INTER_AREA)
        ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 78])
        if not ok:
            raise RuntimeError("Failed to encode thumbnail")
        if video_path:
            (path / THUMB_FILE).write_bytes(jpeg.tobytes())
        return jpeg.tobytes()

    def video_frame(self, name: str) -> np.ndarray:
        """A representative raw frame of a recording (its first readable one) at full size."""
        video_path = find_video(self.scenario_dir(name))
        if not video_path:
            raise FileNotFoundError(f"Recording '{name}' has no video")
        cap = cv2.VideoCapture(str(video_path))
        try:
            ok, frame = cap.read()
        finally:
            cap.release()
        if not ok:
            raise FileNotFoundError("Could not read a frame from the video")
        return frame

    def suggest_views(self, name: str) -> List[Dict[str, Any]]:
        """Saved views ranked by how much their photo looks like this recording's frame."""
        frame = self.video_frame(name)
        h, w = frame.shape[:2]
        ranked = []
        for view in self.views():
            bg = load_background(self.layouts_dir, view)
            score = frame_similarity(frame, bg) if bg is not None else 0.0
            ranked.append({
                "layout_id": view.layout_id,
                "name": view.name or view.layout_id,
                "score": score,
                "has_photo": bg is not None,
                "frame_width": view.frame_width,
                "frame_height": view.frame_height,
                "same_aspect": abs(view.frame_width / view.frame_height - w / h) <= 0.02,
            })
        ranked.sort(key=lambda v: (v["score"], v["layout_id"] == self.default_layout_id), reverse=True)
        return ranked

    def _write_meta(self, path: Path, **changes: Any) -> Dict[str, Any]:
        meta = {**scenario_meta(path), **changes}
        tmp = path / "scenario.json.tmp"
        tmp.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path / "scenario.json")
        return meta

    def assign_view(
        self, name: str, action: str, layout_id: Optional[str] = None,
        view_name: Optional[str] = None, regions: Optional[List[Region]] = None,
    ) -> Layout:
        """Settle which camera view an uploaded recording uses.

        use: keep an existing view unchanged. replace: overwrite an existing view's regions
        and photo with this recording's frame. new: save the frame and regions as a new view.
        Saving from the recording's own frame keeps boxes aligned with its video.
        """
        path = self.scenario_dir(name)
        if action == "use":
            view = self.view(layout_id or self.default_layout_id)
        elif action in ("replace", "new"):
            if regions is None:
                raise ValueError("Send the regions to save.")
            frame = self.video_frame(name)
            h, w = frame.shape[:2]
            if action == "replace":
                base = self.view(layout_id or self.default_layout_id)
                target_id, label = base.layout_id, view_name or base.name
            else:
                label = (view_name or "").strip() or f"View from {scenario_meta(path).get('label') or name}"
                target_id = new_layout_id(self.layouts_dir, label)
            folder = self.layouts_dir / target_id
            background = save_frame_background(folder, frame)
            draft = Layout.model_validate({
                "layout_id": target_id,
                "name": label,
                "frame_width": w,
                "frame_height": h,
                "background_image": background,
                "regions": [r.model_dump() for r in regions],
                "medications": [m.model_dump() for m in self.catalog.medications],
                "receipts": [r.model_dump() for r in self.catalog.receipts],
            })
            if self.catalog.medications and not (self.layouts_dir.parent / "catalog.json").exists():
                save_catalog(self.layouts_dir, self.catalog)
            save_layout(self.layouts_dir, draft)
            self._views.pop(target_id, None)
            view = self.view(target_id)
            verb = "Replaced" if action == "replace" else "Saved new"
            self.store.record("layout", f"{verb} camera view {label} from recording {name}.", layout_id=target_id)
        else:
            raise ValueError(f"Unknown action {action!r}; use use, replace or new.")
        self._write_meta(path, layout_id=view.layout_id, view_confirmed=True)
        if self.current and self.current.name == name:
            self.current.meta = scenario_meta(path)
        if self.layout.layout_id == view.layout_id or (self.current and self.current.name == name):
            self._set_player_view(view.layout_id)
        self.store.save()
        return view

    def background_from_recording(self, layout_id: str, recording: str) -> Dict[str, Any]:
        frame = self.video_frame(recording)
        self.view(layout_id)
        filename = save_frame_background(self.layouts_dir / layout_id, frame)
        h, w = frame.shape[:2]
        return {"background_image": filename, "width": w, "height": h}

    # ------------------------------------------------------------------ inventory-wide changes

    def reset_inventory(self) -> None:
        """Restore the layout's opening stock; every recording's signals become unapplied."""
        self.store.reset(self.catalog)
        if self.current:
            self.store.merge_transactions(load_json(self.current.path / "transactions.json"))
        self.engine.trigger_expiry_alerts(pharmacy_today())
        self.store.save()
        self.is_playing = False
        self.current_media_time_ms = 0

    def apply_layout(self, layout: Layout, reset_inventory: bool = False, catalog: Optional[Catalog] = None) -> List[str]:
        """Adopt a saved view (and catalog). Live inventory is kept unless reset."""
        if catalog is not None:
            self.catalog = catalog
        self._views.clear()
        if self.layout.layout_id == layout.layout_id or self.current is None:
            self._set_player_view(self.current.layout_id if self.current else self.default_layout_id)
        else:
            self._set_player_view(self.layout.layout_id)
        if reset_inventory:
            self.reset_inventory()
            return []
        notes = self.store.sync_catalog(
            self.catalog, note=f"Saved camera view {layout.name or layout.layout_id} (calibration v{layout.calibration_version})."
        )
        self.engine.trigger_expiry_alerts(pharmacy_today())
        self.store.save()
        return notes

    # ------------------------------------------------------------------ clock & events

    def hands_at(self, media_time_ms: float) -> List[Hand]:
        return self.current.hands_at(media_time_ms) if self.current else []

    def _apply(self, rec: Recording, until_ms: float) -> int:
        applied_ids = self.store.applied_event_ids(rec.name)
        pending = [e for e in rec.events if e["media_time_ms"] <= until_ms and e["event_id"] not in applied_ids]
        if not pending:
            return 0
        changed = 0
        for evt in pending:
            camera = rec.camera_group.camera_at(evt["media_time_ms"]) if rec.camera_group else None
            view = self.view(camera.layout_id if camera else rec.layout_id)
            frame_size = (camera.poses.width, camera.poses.height) if camera else ((rec.video.width, rec.video.height) if rec.video else (view.frame_width, view.frame_height))
            hands, joint = rec.hand_points_at(evt["media_time_ms"])
            if self.store.apply_event(rec.name, evt, hands, frame_size, rec.label,
                                      regions=view.regions, layout_id=view.layout_id, joint=joint,
                                      camera_id=camera.camera_id if camera else None,
                                      calibration_version=view.calibration_version):
                changed += 1
        if changed:
            self.store.save()
        return changed

    def process_events_until(self, media_time_ms: float) -> int:
        """Apply every not-yet-applied signal at or before media_time_ms, exactly once."""
        return self._apply(self.current, media_time_ms) if self.current else 0

    def play(self):
        if not self.current:
            return
        if self.current_media_time_ms >= self.duration_ms:
            self.current_media_time_ms = 0
        self.is_playing = True
        self._last_tick = time.monotonic()

    def pause(self):
        self.is_playing = False

    def restart(self):
        self.seek(0)

    def seek(self, media_time_ms: float):
        """Move the playhead. Passing a signal applies it; going back never undoes one."""
        self.current_media_time_ms = max(0.0, min(float(media_time_ms), float(self.duration_ms)))
        self._last_tick = time.monotonic()
        self.process_events_until(self.current_media_time_ms)
        self._select_camera()

    def _select_camera(self):
        if not self.current or not self.current.camera_group:
            return False
        camera = self.current.camera_group.camera_at(self.current_media_time_ms)
        if self.layout.layout_id != camera.layout_id:
            self._set_player_view(camera.layout_id)
            return True
        return False

    def tick(self, now: float) -> bool:
        """Advance the media clock by real elapsed time. Returns True if inventory state changed."""
        if not self.is_playing or self._last_tick is None:
            return False
        elapsed_ms = (now - self._last_tick) * 1000.0
        self._last_tick = now
        self.current_media_time_ms = min(self.duration_ms, self.current_media_time_ms + elapsed_ms)
        changed = self.process_events_until(self.current_media_time_ms) > 0
        changed = self._select_camera() or changed
        if self.current_media_time_ms >= self.duration_ms:
            self.is_playing = False
            changed = True
        return changed

    async def run_clock(self):
        last_broadcast = 0.0
        while True:
            await asyncio.sleep(CLOCK_TICK_S)
            now = time.monotonic()
            async with self.inventory_lock:
                try:
                    if self.is_playing:
                        self.store.refresh()
                    if self.tick(now):
                        await self.broadcast_state_snapshot()
                        last_broadcast = now
                    elif self.is_playing and now - last_broadcast >= CLOCK_BROADCAST_S:
                        await self.broadcast_json({"type": "clock", "media_time_ms": int(self.current_media_time_ms),
                                                   "is_playing": self.is_playing})
                        last_broadcast = now
                except (StorageUnavailable, StateConflict, StateTooLarge) as exc:
                    self.is_playing = False
                    self.storage_error = str(exc)
                    self.store.rollback()
                    await self.broadcast_json({"type": "storage_error", "message": str(exc)})

    # ------------------------------------------------------------------ pose processing

    def start_processing(self, scenario_name: str) -> None:
        path = self.scenario_dir(scenario_name)
        video_path = find_video(path)
        if not video_path:
            raise FileNotFoundError(f"Recording '{scenario_name}' has no video")
        if self.processing.get(scenario_name, {}).get("state") == "processing":
            return
        job = {"state": "processing", "progress": 0.0, "error": None}
        self.processing[scenario_name] = job

        def work():
            from pharma.pose import extract_video_keypoints, resolve_model_path

            try:
                info = probe_video(video_path)
                model = resolve_model_path()

                def progress(done: int, total: int):
                    job["progress"] = min(1.0, done / max(total, 1))

                frames = extract_video_keypoints(video_path, info.frame_count, model, progress=progress)
                PoseTrack(info.fps, info.width, info.height, frames).save(path / POSES_FILE, Path(model).name)
                job.update(state="ready", progress=1.0)
            except Exception as e:  # surfaced to the UI through the status endpoint
                job.update(state="error", error=str(e))

        threading.Thread(target=work, name=f"pose-{scenario_name}", daemon=True).start()

    # ------------------------------------------------------------------ state

    def state_dict(self) -> Dict[str, Any]:
        layout = self.layout
        rec = self.current
        applied = self.processed_event_ids
        return {
            "scenario": rec.name if rec else None,
            "recording": {
                "name": rec.name,
                "label": rec.label,
                "source": rec.meta.get("source", "fixture"),
                "uploaded_at": rec.meta.get("uploaded_at"),
                "events_total": len(rec.events),
                "events_applied": sum(1 for e in rec.events if e["event_id"] in applied),
            } if rec else None,
            "media_time_ms": int(self.current_media_time_ms),
            "duration_ms": self.duration_ms,
            "is_playing": self.is_playing,
            "has_video": self.video_path is not None,
            "camera_selection": rec.camera_group.at(self.current_media_time_ms) if rec and rec.camera_group else None,
            "frame_size": list(self.frame_size()),
            "events": [{**e, "processed": e["event_id"] in applied} for e in (rec.events if rec else [])],
            "activity": self.activity,
            "max_region_distance": self.engine.max_region_distance,
            "store": {"backend": "mongodb", "revision": self.store.revision,
                      "created_at": self.store.created_at, "history_count": len(self.store.history),
                      "error": self.storage_error},
            "views": {
                v.layout_id: {
                    "layout_id": v.layout_id,
                    "name": v.name or v.layout_id,
                    "calibration_version": v.calibration_version,
                    "frame_width": v.frame_width,
                    "frame_height": v.frame_height,
                    "background_image": v.background_image,
                    "regions": [r.model_dump() for r in v.regions],
                }
                for v in self.views()
            },
            "layout": {
                "layout_id": layout.layout_id,
                "name": layout.name or layout.layout_id,
                "calibration_version": layout.calibration_version,
                "frame_width": layout.frame_width,
                "frame_height": layout.frame_height,
                "background_image": layout.background_image,
                "medications": [m.model_dump() for m in layout.medications],
                "regions": [r.model_dump() for r in layout.regions],
            },
            "inventory": {k: v.model_dump() for k, v in self.engine.inventory.items()},
            "sessions": {k: v.model_dump() for k, v in self.engine.sessions.items()},
            "disposals": {k: v.model_dump() for k, v in self.engine.disposals.items()},
            "alerts": {k: v.model_dump() for k, v in self.engine.alerts.items()},
            "receipts": [r.model_dump() for r in self.engine.receipts.values()],
            "transactions": {k: v.model_dump() for k, v in self.engine.transactions.items()},
        }

    async def commit(self, kind: Optional[str] = None, summary: str = "", **detail: Any):
        """Persist an employee action, record it in the history, and push the new state."""
        if kind:
            self.store.record(kind, summary, **detail)
        self.store.save()
        await self.broadcast_state_snapshot()

    async def register_websocket(self, websocket: WebSocket):
        await websocket.accept()
        self.active_websockets.add(websocket)
        await self.broadcast_state_snapshot()

    def unregister_websocket(self, websocket: WebSocket):
        self.active_websockets.discard(websocket)

    async def broadcast_json(self, data: Dict[str, Any]):
        for ws in list(self.active_websockets):
            try:
                await ws.send_json(data)
            except Exception:
                self.active_websockets.discard(ws)

    async def broadcast_state_snapshot(self):
        await self.broadcast_json({"type": "state_snapshot", **self.state_dict()})

    # ------------------------------------------------------------------ rendering

    def base_frame(self, size: Optional[Tuple[int, int]] = None) -> np.ndarray:
        """Layout background photo if one was imported, otherwise a flat placeholder."""
        width, height = size or self.frame_size()
        if self.background is not None:
            return cv2.resize(self.background, (width, height))
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        frame[:] = (240, 240, 240)
        return frame

    def annotate(self, frame: np.ndarray, media_time_ms: float, rec: Optional[Recording] = None) -> np.ndarray:
        height, width = frame.shape[:2]
        meds = {m.medication_key: f"{m.name} {m.strength}" for m in self.layout.medications}

        fill = frame.copy()
        polys = []
        for r in self.layout.regions:
            pts = np.array([[int(x * width), int(y * height)] for x, y in r.polygon], dtype=np.int32)
            polys.append((r, pts))
            cv2.fillPoly(fill, [pts], REGION_COLORS[r.region_type])
        frame = cv2.addWeighted(fill, 0.12, frame, 0.88, 0)
        scale = max(0.5, width / 1280)
        text_scale = max(0.8, max(width, height) / 1280)
        for r, pts in polys:
            color = REGION_COLORS[r.region_type]
            cv2.polylines(frame, [pts], True, color, max(2, int(2 * scale)), cv2.LINE_AA)
            if r.region_type == "designated_shelf":
                label = meds.get(r.medication_key, r.region_id)
            else:
                label = "Counter" if r.region_type == "dispensing_counter" else "Disposal"
            x0, y0 = int(pts[:, 0].min()), int(pts[:, 1].min())
            font, size = cv2.FONT_HERSHEY_SIMPLEX, 0.6 * text_scale
            thick = max(1, round(text_scale))
            (tw, th), base = cv2.getTextSize(label, font, size, thick)
            pad = max(3, int(5 * text_scale))
            cv2.rectangle(frame, (x0, y0), (x0 + tw + 2 * pad, y0 + th + base + 2 * pad), color, -1)
            cv2.putText(frame, label, (x0 + pad, y0 + pad + th), font, size, (255, 255, 255), thick, cv2.LINE_AA)

        hands: List[Tuple[int, int]] = []
        if rec and rec.poses:
            track = rec.camera_group.camera_at(media_time_ms).poses if rec.camera_group else rec.poses
            kps = track.keypoints_at(media_time_ms)
            if kps:
                px = [(int(x * width), int(y * height), c) for x, y, c in kps]
                for a, b in SKELETON_EDGES:
                    if px[a][2] >= MIN_KEYPOINT_CONF and px[b][2] >= MIN_KEYPOINT_CONF:
                        cv2.line(frame, px[a][:2], px[b][:2], (255, 255, 255), max(2, round(2.5 * scale)), cv2.LINE_AA)
                        cv2.line(frame, px[a][:2], px[b][:2], SKELETON_COLOR, max(1, round(1.25 * scale)), cv2.LINE_AA)
                for i, (x, y, c) in enumerate(px):
                    if c >= MIN_KEYPOINT_CONF and i not in (9, 10):
                        cv2.circle(frame, (x, y), max(2, round(2 * scale)), (255, 255, 255), -1, cv2.LINE_AA)
                hands = [(x, y) for i, (x, y, c) in enumerate(px) if i in (9, 10) and c >= MIN_KEYPOINT_CONF]
        elif rec:
            hx, hy, _ = synthetic_hand(media_time_ms)
            hands = [(int(hx * width), int(hy * height))]
        for x, y in hands:
            cv2.circle(frame, (x, y), max(4, round(5 * scale)), WRIST_COLOR, -1, cv2.LINE_AA)
            cv2.circle(frame, (x, y), max(6, round(8 * scale)), (255, 255, 255), max(1, round(1.5 * scale)), cv2.LINE_AA)
        if rec and rec.camera_group:
            selected = rec.camera_group.at(media_time_ms)
            label = selected["camera_id"] + (" | arm visible" if selected["reliable_arm"] else " | arm uncertain")
            cv2.rectangle(frame, (0, 0), (width, 45), (30, 30, 30), -1)
            cv2.putText(frame, label, (15, 30), cv2.FONT_HERSHEY_SIMPLEX, .7, (255, 255, 255), 2)
        return frame

    def mjpeg_generator(self):
        """Render frames at the shared media clock; viewers never advance time themselves."""
        cap = None
        cap_generation = -1
        last_index = -1
        last_frame = None
        cap_path = None
        try:
            while True:
                t = self.current_media_time_ms
                rec = self.current
                camera = rec.camera_group.camera_at(t) if rec and rec.camera_group else None
                video_path = camera.video_path if camera else (rec.video_path if rec else None)
                if cap_generation != self.generation or cap_path != video_path:
                    if cap is not None:
                        cap.release()
                    cap = cv2.VideoCapture(str(video_path)) if video_path else None
                    cap_generation, last_index, last_frame = self.generation, -1, None
                    cap_path = video_path

                if cap is not None:
                    index = int(t * rec.fps / 1000.0)
                    if index != last_index or last_frame is None:
                        if index != last_index + 1:
                            cap.set(cv2.CAP_PROP_POS_FRAMES, index)
                        ok, frame = cap.read()
                        if ok:
                            last_frame, last_index = frame, index
                    frame = last_frame.copy() if last_frame is not None else self.base_frame()
                else:
                    frame = self.base_frame()

                frame = self.annotate(frame, t, rec)
                if frame.shape[1] > STREAM_MAX_WIDTH:
                    h = int(frame.shape[0] * STREAM_MAX_WIDTH / frame.shape[1])
                    frame = cv2.resize(frame, (STREAM_MAX_WIDTH, h), interpolation=cv2.INTER_AREA)
                ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if ok:
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg.tobytes() + b"\r\n"
                time.sleep(1.0 / min(self.fps, 30) if self.is_playing else 0.2)
        finally:
            if cap is not None:
                cap.release()

    def still_jpeg(self) -> bytes:
        """Annotation background: imported photo, else the player's first frame, else placeholder."""
        frame = None
        if self.background is None and self.video_path:
            cap = cv2.VideoCapture(str(self.video_path))
            ok, first = cap.read()
            cap.release()
            frame = first if ok else None
        ok, jpeg = cv2.imencode(".jpg", frame if frame is not None else self.base_frame())
        if not ok:
            raise RuntimeError("Failed to encode frame")
        return jpeg.tobytes()
