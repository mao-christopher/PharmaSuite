"""Live wristband capture: clip windows, bottle tracking across clips, and wrist association.

The band reports a prediction after an unknown delay. The browser keeps a short rolling
buffer in memory and uploads only the frames around each notification; nothing else of
the continuous feed is stored. Each accepted notification becomes its own recording.

A pickup and the next put-down are the same bottle, even though they arrive as two
separate clips, so live movement sessions are keyed by the pickup's event ID under one
shared scope rather than per recording. A pickup while a bottle is already in hand, or a
put-down with nothing in hand, is treated as a false detection and ignored.

Only a sustained, unique intersection of the selected wrist and a configured region is
accepted as a location.
"""

from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from pharma.db.models import MovementSession, Region
from pharma.services.inventory_engine import MIN_KEYPOINT_CONF, point_in_polygon

WRIST_INDEX = {"left": 9, "right": 10}
MIN_RUN = 3

# Clip window around a band notification (not tuned on hardware). The band's
# notification arrives roughly 4-5 s after the physical pickup or put-down (reported
# 2026-09-27, not measured), so most of the window lies before the notification.
PRE_ROLL_MS = 9000
POST_ROLL_MS = 1000
# Estimated delay from the physical action to the notification, used only to pick the
# thumbnail frame. The region decision still scans the whole clip.
BAND_LATENCY_MS = 4500
# A buffer counts as complete when it reaches within this much of each window edge
# and no two frames are further apart than MAX_FRAME_GAP_MS.
EDGE_TOLERANCE_MS = 300
MAX_FRAME_GAP_MS = 300
MIN_FRAMES = 3
MAX_FRAMES = 130  # 10 s at the browser's 10 fps, with room for timing jitter

LIVE_SCOPE = "live"  # movement sessions shared by every live clip: live:<pickup event ID>
LIVE_SOURCE = "live"  # scenario.json source of a live clip recording
CLIP_PREFIX = "live-"
THUMB_WIDTH = 480


def clip_name(event_id: str) -> str:
    """Recording name of a live event's clip (also its idempotency key)."""
    return f"{CLIP_PREFIX}{event_id}"


def holds_bottle(session: Optional[MovementSession]) -> bool:
    """Whether a movement's bottle is still in hand (picked up, not yet put down)."""
    if session is None:
        return False
    if session.state == "HELD":
        return True
    return (session.state == "NEEDS_CONFIRMATION" and session.evidence.get("awaiting") == "pickup"
            and "pending_release" not in session.evidence)


def live_entries(recordings: Dict[str, Dict[str, Any]], include_ignored: bool = False) -> List[Tuple[str, Dict[str, Any]]]:
    """Store entries of live events in the order they were ingested."""
    found = [(name, entry) for name, entry in recordings.items()
             if entry.get("live") and (include_ignored or not entry["live"].get("ignored"))]
    return sorted(found, key=lambda item: item[1]["live"]["ingested_at"])


def held_movement(recordings: Dict[str, Dict[str, Any]], sessions: Dict[str, MovementSession]) -> Optional[str]:
    """Movement ID of the bottle in hand from the latest live pickup, if it's still in hand."""
    pickups = [entry["live"] for _, entry in live_entries(recordings) if entry["live"]["event_type"] == "pickup"]
    if not pickups:
        return None
    movement_id = pickups[-1]["movement_id"]
    return movement_id if holds_bottle(sessions.get(f"{LIVE_SCOPE}:{movement_id}")) else None


def sequence_problem(event_type: str, held: Optional[str]) -> Optional[str]:
    if event_type == "pickup" and held:
        return "pickup_while_holding"
    if event_type == "release" and not held:
        return "release_without_pickup"
    return None


def buffer_complete(times: Sequence[float], notification_ms: float) -> bool:
    """The frames cover the whole clip window with no long gaps."""
    return (len(times) >= MIN_FRAMES
            and times[0] <= notification_ms - PRE_ROLL_MS + EDGE_TOLERANCE_MS
            and times[-1] >= notification_ms + POST_ROLL_MS - EDGE_TOLERANCE_MS
            and all(b - a <= MAX_FRAME_GAP_MS for a, b in zip(times, times[1:])))


def clip_fps(times: Sequence[float]) -> float:
    if len(times) < 2:
        return 1.0
    return min(30.0, max(1.0, (len(times) - 1) * 1000 / max(times[-1] - times[0], 1)))


def write_clip(images: Sequence[np.ndarray], fps: float, path: Path) -> str:
    """Encode frames as H.264 so browsers can play the clip; fall back to MPEG-4 Part 2.

    OpenCV's bundled FFmpeg provides H.264 on macOS wheels. Builds without it (some Linux
    wheels) fall back to mp4v, which the server-side player still reads.
    """
    height, width = images[0].shape[:2]
    for codec in ("avc1", "mp4v"):
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*codec), fps, (width, height))
        if not writer.isOpened():
            writer.release()
            continue
        try:
            for image in images:
                writer.write(image)
        finally:
            writer.release()
        if path.exists() and path.stat().st_size > 0:
            return codec
    raise RuntimeError("No MP4 encoder is available")


def write_thumbnail(image: np.ndarray, path: Path) -> None:
    h, w = image.shape[:2]
    small = cv2.resize(image, (THUMB_WIDTH, int(h * THUMB_WIDTH / w)), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(path), small, [cv2.IMWRITE_JPEG_QUALITY, 78])


def event_hand(pose: Optional[List[List[float]]], wrist: str) -> List[Tuple[float, float, float]]:
    """One selected wrist, never the other hand or an elbow/shoulder fallback."""
    index = WRIST_INDEX[wrist]
    return [tuple(pose[index])] if pose and len(pose) > index else []


def associate_wrist(
    poses: Sequence[Optional[List[List[float]]]],
    regions: Sequence[Region],
    wrist: str,
    event_type: str,
) -> Tuple[Optional[str], Dict[str, Any]]:
    """Return a region only when one region has a three-frame wrist intersection.

    A frame containing more than one region is ambiguous, even if the overlap
    resolves on a later frame. The clip is evidence of an estimated action
    location, not evidence of bottle depth or physical counting.
    """
    if wrist not in WRIST_INDEX or event_type not in {"pickup", "release"}:
        raise ValueError("Unknown wrist or event type")
    eligible = [r for r in regions if event_type == "release" or
                r.region_type in {"designated_shelf", "dispensing_counter"}]
    index = WRIST_INDEX[wrist]
    hits: List[Optional[str]] = []
    visible = 0
    overlap = False
    for pose in poses:
        if not pose or len(pose) <= index or pose[index][2] < MIN_KEYPOINT_CONF:
            hits.append(None)
            continue
        x, y, _ = pose[index]
        visible += 1
        matches = [r.region_id for r in eligible if point_in_polygon(x, y, r.polygon)]
        if len(matches) > 1:
            overlap = True
        hits.append(matches[0] if len(matches) == 1 else None)
    runs: Counter[str] = Counter()
    previous = None
    length = 0
    for hit in hits + [None]:
        if hit is not None and hit == previous:
            length += 1
        else:
            if previous and length >= MIN_RUN:
                runs[previous] += 1
            previous, length = hit, 1
    evidence = {"wrist": wrist, "frames": len(poses), "visible_frames": visible,
                "qualifying_regions": sorted(runs), "overlap": overlap,
                "rule": "three_consecutive_frames_inside_one_region"}
    if overlap:
        evidence["reason"] = "overlapping_regions"
    elif len(runs) != 1:
        evidence["reason"] = "no_stable_intersection" if not runs else "competing_regions"
    else:
        return next(iter(runs)), evidence
    return None, evidence
