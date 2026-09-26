"""Recording replay: media clock, event dispatch, MJPEG rendering, and WebSocket broadcast."""

import asyncio
import os
import re
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import cv2
import numpy as np
from fastapi import WebSocket

from pharma.db.models import Layout, PrescriptionTransaction, Region
from pharma.services.fixture_loader import load_json, load_jsonl
from pharma.services.inventory_engine import MIN_KEYPOINT_CONF, Hand, InventoryEngine
from pharma.services.layout import build_initial_state, load_layout
from pharma.services.recordings import POSES_FILE, PoseTrack, VideoInfo, find_video, probe_video

SCENARIO_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
SYNTHETIC_DURATION_MS = 10000
SYNTHETIC_FPS = 30
STREAM_MAX_WIDTH = 1280
CLOCK_TICK_S = 1 / 60
CLOCK_BROADCAST_S = 0.25

# BGR colors, matched to the dashboard's region palette.
REGION_COLORS = {
    "designated_shelf": (235, 99, 37),
    "dispensing_counter": (6, 119, 217),
    "disposal": (38, 38, 220),
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


class ReplayController:
    def __init__(self, scenarios_dir: Path, layouts_dir: Optional[Path] = None):
        self.scenarios_dir = scenarios_dir
        self.layouts_dir = layouts_dir or scenarios_dir.parent / "layouts"
        self.current_scenario_name: Optional[str] = None
        self.scenario_path: Optional[Path] = None
        self.engine: Optional[InventoryEngine] = None
        self.events: List[Dict[str, Any]] = []
        self.processed_event_ids: Set[str] = set()
        self.activity: List[Dict[str, Any]] = []
        self.layout: Optional[Layout] = None
        self.regions: List[Region] = []
        self.video_path: Optional[Path] = None
        self.video: Optional[VideoInfo] = None
        self.poses: Optional[PoseTrack] = None
        self.background: Optional[np.ndarray] = None
        self.duration_ms: int = SYNTHETIC_DURATION_MS
        self.fps: float = SYNTHETIC_FPS
        self.active_websockets: Set[WebSocket] = set()
        self.is_playing: bool = False
        self.current_media_time_ms: float = 0
        self.generation = 0  # bumps on every load so open streams reopen their source
        self.processing: Dict[str, Dict[str, Any]] = {}
        self._last_tick: Optional[float] = None

    # ------------------------------------------------------------------ loading

    def scenario_dir(self, scenario_name: str) -> Path:
        path = self.scenarios_dir / scenario_name
        if not SCENARIO_NAME_PATTERN.fullmatch(scenario_name) or not path.is_dir():
            raise FileNotFoundError(f"Scenario '{scenario_name}' not found")
        return path

    def scenario_status(self, path: Path) -> str:
        job = self.processing.get(path.name)
        if job and job["state"] in ("processing", "error"):
            return job["state"]
        if find_video(path) and not (path / POSES_FILE).exists():
            return "unprocessed"
        return "ready"

    def load_scenario(self, scenario_name: str) -> Dict[str, Any]:
        path = self.scenario_dir(scenario_name)
        status = self.scenario_status(path)
        if status != "ready":
            raise ScenarioNotReady(f"Recording '{scenario_name}' is {status}; skeletons are not available yet.")
        layout_id = scenario_layout_id(path)
        if not layout_id:
            raise FileNotFoundError(f"Scenario '{scenario_name}' has no layout_id in scenario.json")
        layout = load_layout(self.layouts_dir, layout_id)
        regions, inventory, receipts = build_initial_state(layout)

        video_path = find_video(path)
        if video_path:
            self.video = probe_video(video_path)
            self.poses = PoseTrack.load(path / POSES_FILE)
            self.duration_ms = self.video.duration_ms
            self.fps = self.video.fps
            frame_size = (self.video.width, self.video.height)
        else:
            self.video = None
            self.poses = None
            self.duration_ms = SYNTHETIC_DURATION_MS
            self.fps = SYNTHETIC_FPS
            frame_size = (layout.frame_width, layout.frame_height)

        tx_raw = load_json(path / "transactions.json")
        transactions = {t["transaction_id"]: PrescriptionTransaction(**t) for t in tx_raw}

        self.current_scenario_name = scenario_name
        self.scenario_path = path
        self.layout = layout
        self.regions = regions
        self.video_path = video_path
        self.background = self._load_background(layout)
        self.events = sorted(load_jsonl(path / "imu_events.jsonl"), key=lambda e: e["media_time_ms"])
        self.processed_event_ids = set()
        self.activity = []
        self.engine = InventoryEngine(
            inventory=inventory,
            regions=regions,
            receipts=receipts,
            transactions=transactions,
            frame_size=frame_size,
        )
        self.engine.trigger_expiry_alerts(pharmacy_today())
        self.current_media_time_ms = 0
        self.is_playing = False
        self.generation += 1

        return {
            "scenario_name": scenario_name,
            "layout_id": layout.layout_id,
            "calibration_version": layout.calibration_version,
            "regions_count": len(self.regions),
            "events_count": len(self.events),
            "inventory_keys": list(inventory.keys()),
            "has_video": video_path is not None,
            "duration_ms": self.duration_ms,
        }

    def _load_background(self, layout: Layout) -> Optional[np.ndarray]:
        if not layout.background_image:
            return None
        img = cv2.imread(str(self.layouts_dir / layout.layout_id / layout.background_image))
        return img

    # ------------------------------------------------------------------ clock & events

    def hands_at(self, media_time_ms: float) -> List[Hand]:
        if self.poses:
            return self.poses.hands_at(media_time_ms, MIN_KEYPOINT_CONF)
        return [synthetic_hand(media_time_ms)]

    def process_events_until(self, media_time_ms: float) -> int:
        """Dispatch every not-yet-processed event at or before media_time_ms, exactly once."""
        if not self.engine:
            return 0
        applied = 0
        for evt in self.events:
            if evt["media_time_ms"] > media_time_ms:
                break
            if evt["event_id"] in self.processed_event_ids:
                continue
            self.processed_event_ids.add(evt["event_id"])
            hands = self.hands_at(evt["media_time_ms"])
            if evt["event_type"] == "pickup":
                session = self.engine.handle_pickup(evt["session_id"], hands, evt.get("timestamp", 0.0))
            elif evt["event_type"] == "release":
                session = self.engine.handle_release(evt["session_id"], hands, evt.get("timestamp", 0.0))
            else:
                continue
            evidence = session.evidence.get("pending_release", {}).get("evidence") or session.evidence
            self.activity.append({
                "event_id": evt["event_id"],
                "media_time_ms": evt["media_time_ms"],
                "event_type": evt["event_type"],
                "session_id": evt["session_id"],
                "medication_key": session.medication_key,
                "state": session.state,
                "held_pending": "pending_release" in session.evidence,
                "nearest_region_id": evidence.get("nearest_region_id"),
                "distance": evidence.get("distance"),
                "reason": evidence.get("reason"),
                "hands_seen": sum(1 for h in hands if h[2] >= MIN_KEYPOINT_CONF),
            })
            applied += 1
        return applied

    def play(self):
        if self.current_media_time_ms >= self.duration_ms:
            self.restart()
        self.is_playing = True
        self._last_tick = time.monotonic()

    def pause(self):
        self.is_playing = False

    def restart(self):
        """Replays start from the recording's own seed so events never apply twice."""
        if self.current_scenario_name:
            self.load_scenario(self.current_scenario_name)

    def seek(self, media_time_ms: float):
        target = max(0.0, min(float(media_time_ms), float(self.duration_ms)))
        was_playing = self.is_playing
        self.restart()
        self.current_media_time_ms = target
        self.process_events_until(target)
        if was_playing:
            self.play()

    def tick(self, now: float) -> bool:
        """Advance the media clock by real elapsed time. Returns True if inventory state changed."""
        if not self.is_playing or self._last_tick is None:
            return False
        elapsed_ms = (now - self._last_tick) * 1000.0
        self._last_tick = now
        self.current_media_time_ms = min(self.duration_ms, self.current_media_time_ms + elapsed_ms)
        changed = self.process_events_until(self.current_media_time_ms) > 0
        if self.current_media_time_ms >= self.duration_ms:
            self.is_playing = False
            changed = True
        return changed

    async def run_clock(self):
        last_broadcast = 0.0
        while True:
            await asyncio.sleep(CLOCK_TICK_S)
            now = time.monotonic()
            if self.tick(now):
                await self.broadcast_state_snapshot()
                last_broadcast = now
            elif self.is_playing and now - last_broadcast >= CLOCK_BROADCAST_S:
                await self.broadcast_json({
                    "type": "clock",
                    "media_time_ms": int(self.current_media_time_ms),
                    "is_playing": self.is_playing,
                })
                last_broadcast = now

    # ------------------------------------------------------------------ pose processing

    def start_processing(self, scenario_name: str) -> None:
        path = self.scenario_dir(scenario_name)
        video_path = find_video(path)
        if not video_path:
            raise FileNotFoundError(f"Scenario '{scenario_name}' has no video")
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
        return {
            "scenario": self.current_scenario_name,
            "media_time_ms": int(self.current_media_time_ms),
            "duration_ms": self.duration_ms,
            "is_playing": self.is_playing,
            "has_video": self.video_path is not None,
            "frame_size": list(self.frame_size()),
            "events": [
                {**e, "processed": e["event_id"] in self.processed_event_ids} for e in self.events
            ],
            "activity": self.activity,
            "max_region_distance": self.engine.max_region_distance,
            "layout": {
                "layout_id": layout.layout_id,
                "calibration_version": layout.calibration_version,
                "frame_width": layout.frame_width,
                "frame_height": layout.frame_height,
                "background_image": layout.background_image,
                "medications": [m.model_dump() for m in layout.medications],
                "regions": [r.model_dump() for r in layout.regions],
            } if layout else None,
            "inventory": {k: v.model_dump() for k, v in self.engine.inventory.items()},
            "sessions": {k: v.model_dump() for k, v in self.engine.sessions.items()},
            "disposals": {k: v.model_dump() for k, v in self.engine.disposals.items()},
            "alerts": {k: v.model_dump() for k, v in self.engine.alerts.items()},
            "receipts": [r.model_dump() for r in self.engine.receipts.values()],
            "transactions": {k: v.model_dump() for k, v in self.engine.transactions.items()},
        }

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
        if not self.engine:
            return
        await self.broadcast_json({"type": "state_snapshot", **self.state_dict()})

    # ------------------------------------------------------------------ rendering

    def frame_size(self) -> Tuple[int, int]:
        if self.video:
            return self.video.width, self.video.height
        if self.layout:
            return self.layout.frame_width, self.layout.frame_height
        return 1280, 720

    def base_frame(self) -> np.ndarray:
        """Layout background photo if one was imported, otherwise a flat placeholder."""
        width, height = self.frame_size()
        if self.background is not None:
            return cv2.resize(self.background, (width, height))
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        frame[:] = (236, 232, 229)
        return frame

    def annotate(self, frame: np.ndarray, media_time_ms: float) -> np.ndarray:
        height, width = frame.shape[:2]
        meds = {m.medication_key: f"{m.name} {m.strength}" for m in (self.layout.medications if self.layout else [])}

        fill = frame.copy()
        polys = []
        for r in self.regions:
            pts = np.array([[int(x * width), int(y * height)] for x, y in r.polygon], dtype=np.int32)
            polys.append((r, pts))
            cv2.fillPoly(fill, [pts], REGION_COLORS[r.region_type])
        frame = cv2.addWeighted(fill, 0.12, frame, 0.88, 0)
        scale = max(0.5, width / 1280)
        for r, pts in polys:
            color = REGION_COLORS[r.region_type]
            cv2.polylines(frame, [pts], True, color, max(2, int(2 * scale)), cv2.LINE_AA)
            if r.region_type == "designated_shelf":
                label = meds.get(r.medication_key, r.region_id)
            else:
                label = "Counter" if r.region_type == "dispensing_counter" else "Disposal"
            x0, y0 = int(pts[:, 0].min()), int(pts[:, 1].min())
            cv2.putText(frame, label, (x0 + 8, y0 + int(22 * scale)), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55 * scale, color, max(1, int(scale)), cv2.LINE_AA)

        if self.poses:
            kps = self.poses.keypoints_at(media_time_ms)
            if kps:
                px = [(int(x * width), int(y * height), c) for x, y, c in kps]
                for a, b in SKELETON_EDGES:
                    if px[a][2] >= MIN_KEYPOINT_CONF and px[b][2] >= MIN_KEYPOINT_CONF:
                        cv2.line(frame, px[a][:2], px[b][:2], (255, 255, 255), max(4, int(5 * scale)), cv2.LINE_AA)
                        cv2.line(frame, px[a][:2], px[b][:2], SKELETON_COLOR, max(2, int(3 * scale)), cv2.LINE_AA)
                for i, (x, y, c) in enumerate(px):
                    if c >= MIN_KEYPOINT_CONF and i not in (9, 10):
                        cv2.circle(frame, (x, y), max(3, int(3 * scale)), (255, 255, 255), -1, cv2.LINE_AA)
                hands = [(x, y) for i, (x, y, c) in enumerate(px) if i in (9, 10) and c >= MIN_KEYPOINT_CONF]
            else:
                hands = []
        else:
            hx, hy, _ = synthetic_hand(media_time_ms)
            hands = [(int(hx * width), int(hy * height))]
        for x, y in hands:
            cv2.circle(frame, (x, y), int(9 * scale), WRIST_COLOR, -1, cv2.LINE_AA)
            cv2.circle(frame, (x, y), int(14 * scale), (255, 255, 255), max(2, int(2 * scale)), cv2.LINE_AA)
        return frame

    def mjpeg_generator(self):
        """Render frames at the shared media clock; viewers never advance time themselves."""
        cap = None
        cap_generation = -1
        last_index = -1
        last_frame = None
        try:
            while True:
                t = self.current_media_time_ms
                if self.video_path and (cap is None or cap_generation != self.generation):
                    if cap is not None:
                        cap.release()
                    cap = cv2.VideoCapture(str(self.video_path))
                    cap_generation, last_index, last_frame = self.generation, -1, None
                if not self.video_path and cap is not None:
                    cap.release()
                    cap = None

                if cap is not None:
                    index = int(t * self.fps / 1000.0)
                    if index != last_index or last_frame is None:
                        if index != last_index + 1:
                            cap.set(cv2.CAP_PROP_POS_FRAMES, index)
                        ok, frame = cap.read()
                        if ok:
                            last_frame, last_index = frame, index
                    frame = last_frame.copy() if last_frame is not None else self.base_frame()
                else:
                    frame = self.base_frame()

                frame = self.annotate(frame, t)
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
        """Annotation background: imported photo, else the recording's first frame, else placeholder."""
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
