"""Uploaded recordings: video metadata, pose tracks, and pickup/release timestamp files."""

import csv
import io
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2

from pharma.services.inventory_engine import Hand

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
POSES_FILE = "poses.json"
EVENTS_FILE = "imu_events.jsonl"
LEFT_WRIST, RIGHT_WRIST = 9, 10
# How far (in frames) to look around an event when the wrists aren't visible in its exact frame.
WRIST_SEARCH_FRAMES = 3
EVENT_ALIASES = {
    "pickup": "pickup", "pick_up": "pickup", "pick": "pickup", "grab": "pickup", "grabbed": "pickup",
    "release": "release", "drop": "release", "dropped": "release", "put_down": "release", "place": "release",
}


@dataclass
class VideoInfo:
    fps: float
    frame_count: int
    width: int
    height: int

    @property
    def duration_ms(self) -> int:
        return int(round(self.frame_count / self.fps * 1000)) if self.fps else 0


def probe_video(path: Path) -> VideoInfo:
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            raise ValueError("Could not open the video file.")
        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        info = VideoInfo(fps=fps, frame_count=count, width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                         height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    finally:
        cap.release()
    if info.fps <= 0 or info.frame_count <= 0 or info.width <= 0:
        raise ValueError("The video has no readable frames or frame rate.")
    return info


def find_video(scenario_dir: Path) -> Optional[Path]:
    return next((p for p in sorted(scenario_dir.glob("video.*")) if p.suffix.lower() in VIDEO_EXTENSIONS), None)


class PoseTrack:
    """Per-frame skeleton keypoints for one recording."""

    def __init__(self, fps: float, width: int, height: int, frames: List[Optional[List[List[float]]]]):
        self.fps = fps
        self.width = width
        self.height = height
        self.frames = frames

    @classmethod
    def load(cls, path: Path) -> "PoseTrack":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(data["fps"], data["width"], data["height"], data["frames"])

    def save(self, path: Path, model: str) -> None:
        payload = {"fps": self.fps, "width": self.width, "height": self.height, "model": model,
                   "frames": self.frames}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)

    def frame_index(self, media_time_ms: float) -> int:
        return max(0, min(len(self.frames) - 1, int(round(media_time_ms * self.fps / 1000.0))))

    def keypoints_at(self, media_time_ms: float) -> Optional[List[List[float]]]:
        if not self.frames:
            return None
        return self.frames[self.frame_index(media_time_ms)]

    def hands_at(self, media_time_ms: float, min_conf: float = 0.0) -> List[Hand]:
        """Both wrists at the event's frame, falling back to the nearest frame with a visible wrist."""
        if not self.frames:
            return []
        center = self.frame_index(media_time_ms)
        for offset in sorted(range(-WRIST_SEARCH_FRAMES, WRIST_SEARCH_FRAMES + 1), key=abs):
            idx = center + offset
            if not 0 <= idx < len(self.frames) or self.frames[idx] is None:
                continue
            kps = self.frames[idx]
            hands = [tuple(kps[i]) for i in (LEFT_WRIST, RIGHT_WRIST) if i < len(kps)]
            if any(h[2] >= min_conf for h in hands):
                return hands
        kps = self.frames[center]
        return [tuple(kps[i]) for i in (LEFT_WRIST, RIGHT_WRIST)] if kps else []


def _parse_time_ms(row: Dict[str, Any]) -> float:
    for key in ("media_time_ms", "time_ms", "timestamp_ms", "ms"):
        if row.get(key) not in (None, ""):
            return float(row[key])
    for key in ("time_s", "seconds", "time", "t"):
        if row.get(key) not in (None, ""):
            return float(row[key]) * 1000.0
    raise ValueError("each event needs media_time_ms (or time_s)")


def _parse_event_type(row: Dict[str, Any]) -> str:
    raw = str(row.get("event_type") or row.get("event") or row.get("type") or "").strip().lower()
    raw = raw.replace("-", "_").replace(" ", "_")
    if raw not in EVENT_ALIASES:
        raise ValueError(f"unknown event type {raw!r}; use pickup or release")
    return EVENT_ALIASES[raw]


def parse_events_file(content: str, duration_ms: Optional[int] = None) -> List[Dict[str, Any]]:
    """Parse pickup/release timestamps from JSON, JSONL, or CSV into mock IMU events.

    Each pickup opens a new movement session; the next release closes it.
    """
    text = content.strip().lstrip("﻿")
    if not text:
        raise ValueError("The timestamps file is empty.")
    rows: List[Dict[str, Any]]
    if text.startswith("["):
        rows = json.loads(text)
    elif text.startswith("{"):
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        lines = [ln for ln in text.splitlines() if ln.strip()]
        first = [c.strip().lower() for c in lines[0].split(",")]
        try:
            float(first[0])
            has_header = False
        except ValueError:
            has_header = True
        reader = csv.reader(io.StringIO("\n".join(lines[1:] if has_header else lines)))
        header = first if has_header else ["time_ms", "event_type"]
        rows = [dict(zip(header, [c.strip() for c in r])) for r in reader if r]

    parsed = []
    for n, row in enumerate(rows, start=1):
        try:
            t = _parse_time_ms(row)
            kind = _parse_event_type(row)
        except (ValueError, TypeError) as e:
            raise ValueError(f"Event {n}: {e}") from None
        if t < 0:
            raise ValueError(f"Event {n}: time cannot be negative")
        if duration_ms is not None and t > duration_ms:
            raise ValueError(f"Event {n}: {t / 1000:.2f}s is past the end of the video ({duration_ms / 1000:.2f}s)")
        parsed.append((int(round(t)), kind))
    if not parsed:
        raise ValueError("The timestamps file has no events.")
    parsed.sort(key=lambda e: e[0])

    now = time.time()
    events, open_session, session_n = [], None, 0
    for i, (t, kind) in enumerate(parsed, start=1):
        if kind == "pickup" or open_session is None:
            session_n += 1
            session_id = f"sess_{session_n:03d}"
            open_session = session_id if kind == "pickup" else None
        else:
            session_id, open_session = open_session, None
        events.append({
            "event_id": f"evt_{i:03d}",
            "schema_version": "1.0",
            "session_id": session_id,
            "timestamp": round(now + t / 1000.0, 3),
            "media_time_ms": t,
            "event_type": kind,
            "sensor_id": "mock_imu_upload",
            "details": {},
        })
    return events


def write_events(path: Path, events: List[Dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
