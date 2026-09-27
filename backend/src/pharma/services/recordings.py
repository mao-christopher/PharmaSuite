"""Uploaded recordings: video metadata, pose tracks, and pickup/release timestamp files."""

import csv
import io
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2

from pharma.services.inventory_engine import Hand

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
POSES_FILE = "poses.json"
EVENTS_FILE = "imu_events.jsonl"
LEFT_WRIST, RIGHT_WRIST = 9, 10
WRIST_JOINTS = (LEFT_WRIST, RIGHT_WRIST)
# When wrists are hidden at a signal, fall back to elbows (a coarser proxy for the hand).
HAND_JOINTS = (("wrist", WRIST_JOINTS), ("elbow", (7, 8)))
# How many frames before or after a signal still count as "at" the signal.
HAND_SEARCH_FRAMES = 5
# Last resort: the nearest confident wrist before or after the signal, if within this
# long. Anything further needs the employee to confirm. Unvalidated.
NEAREST_WRIST_MS = 1000
EVENT_ALIASES = {
    "pickup": "pickup", "pick_up": "pickup", "pick": "pickup", "grab": "pickup", "grabbed": "pickup",
    "release": "release", "drop": "release", "dropped": "release", "put_down": "release", "place": "release",
}
# Signals that carry no location decision (e.g. the simulator's in-transit IMU samples).
IGNORED_EVENTS = {"movement", "move", "moving"}


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

    def save(self, path: Path, model: str, imgsz: Optional[int] = None) -> None:
        payload = {"fps": self.fps, "width": self.width, "height": self.height, "model": model,
                   "imgsz": imgsz, "frames": self.frames}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)

    def frame_index(self, media_time_ms: float) -> int:
        return max(0, min(len(self.frames) - 1, int(round(media_time_ms * self.fps / 1000.0))))

    def index_at(self, media_time_ms: float) -> Optional[int]:
        """Frame shown at a media time, or None past the end of this track (cameras can differ in length)."""
        if not self.frames or media_time_ms < 0 or media_time_ms > len(self.frames) * 1000.0 / self.fps:
            return None
        return self.frame_index(media_time_ms)

    def keypoints_at(self, media_time_ms: float) -> Optional[List[List[float]]]:
        if not self.frames:
            return None
        return self.frames[self.frame_index(media_time_ms)]

    def hand_points_at(self, media_time_ms: float, min_conf: float = 0.0) -> Tuple[List[Hand], Optional[str]]:
        """Hand positions at a signal and which joint they came from (see locate_hands)."""
        fix = locate_hands([(None, self)], media_time_ms, min_conf)
        return fix.points, fix.joint

    def hands_at(self, media_time_ms: float, min_conf: float = 0.0) -> List[Hand]:
        return self.hand_points_at(media_time_ms, min_conf)[0]


@dataclass
class HandFix:
    """Where the hand was at a signal: points in one camera's normalized coordinates."""

    points: List[Hand]
    joint: Optional[str] = None  # wrist | elbow | last_seen_wrist | next_seen_wrist | None (nothing usable)
    camera_id: Optional[str] = None
    offset_ms: float = 0.0  # when the points were seen relative to the signal (negative: before)


def _points(kps, indices) -> List[Hand]:
    return [tuple(kps[i]) for i in indices if i < len(kps)]


def _nearest_first(limit: int):
    """0, -1, +1, -2, +2, ...: the signal's frame, then outwards, earlier frames first on ties."""
    yield 0
    for step in range(1, limit + 1):
        yield -step
        yield step


def locate_hands(tracks: List[Tuple[Optional[str], PoseTrack]], media_time_ms: float, min_conf: float) -> HandFix:
    """Find the hand at a signal across cameras, listed best first.

    Wrists, else elbows, each within HAND_SEARCH_FRAMES of the signal's frame (nearest
    first); else the nearest confident wrist before or after the signal within
    NEAREST_WRIST_MS. Returns no joint when nothing qualifies (including when no person
    is ever seen), and the employee confirms the location.
    """
    tracks = [(cam, t) for cam, t in tracks if t is not None and t.frames]

    def confident(track, idx, indices):
        if not 0 <= idx < len(track.frames) or not track.frames[idx]:
            return None
        points = _points(track.frames[idx], indices)
        return points if any(p[2] >= min_conf for p in points) else None

    for joint, indices in HAND_JOINTS:
        for cam, track in tracks:
            center = track.index_at(media_time_ms)
            if center is None:
                continue
            for step in _nearest_first(HAND_SEARCH_FRAMES):
                points = confident(track, center + step, indices)
                if points:
                    return HandFix(points, joint, cam, step * 1000.0 / track.fps)
    best: Optional[HandFix] = None
    for cam, track in tracks:
        center = track.index_at(media_time_ms)
        if center is None:
            continue
        reach = int(NEAREST_WRIST_MS * track.fps / 1000.0)
        for step in _nearest_first(reach):
            if abs(step) <= HAND_SEARCH_FRAMES:
                continue  # already searched above
            points = confident(track, center + step, WRIST_JOINTS)
            if points:
                offset = step * 1000.0 / track.fps
                if best is None or abs(offset) < abs(best.offset_ms):
                    best = HandFix(points, "last_seen_wrist" if step < 0 else "next_seen_wrist", cam, offset)
                break
    if best:
        return best
    cam, track = tracks[0] if tracks else (None, None)
    idx = track.index_at(media_time_ms) if track else None
    kps = track.frames[idx] if idx is not None else None
    return HandFix(_points(kps, WRIST_JOINTS) if kps else [], None, cam)


def _parse_time_ms(row: Dict[str, Any]) -> float:
    for key in ("media_time_ms", "time_ms", "timestamp_ms", "ms"):
        if row.get(key) not in (None, ""):
            return float(row[key])
    for key in ("time_s", "seconds", "time", "t"):
        if row.get(key) not in (None, ""):
            return float(row[key]) * 1000.0
    raise ValueError("each event needs media_time_ms (or time_s)")


def _parse_event_type(row: Dict[str, Any]) -> Optional[str]:
    raw = str(row.get("event_type") or row.get("event") or row.get("type") or "").strip().lower()
    raw = raw.replace("-", "_").replace(" ", "_")
    if raw in IGNORED_EVENTS:
        return None
    if raw not in EVENT_ALIASES:
        raise ValueError(f"unknown event type {raw!r}; use pickup or release")
    return EVENT_ALIASES[raw]


def parse_events_file(content: str, duration_ms: Optional[int] = None) -> List[Dict[str, Any]]:
    """Parse pickup/release timestamps from JSON, JSONL, or CSV into mock IMU events.

    Each pickup opens a new movement session; the next release closes it. Movement
    samples (as the Unity simulator exports) are skipped; the source file is kept as uploaded.
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
        if kind is None:
            continue
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
