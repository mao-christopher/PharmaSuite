"""Association of a wristband notification with browser camera frames.

The band reports a prediction after an unknown delay. Only a sustained, unique
intersection of the selected wrist and a configured region is accepted.
"""

from collections import Counter
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pharma.db.models import Region
from pharma.services.inventory_engine import MIN_KEYPOINT_CONF, point_in_polygon

WRIST_INDEX = {"left": 9, "right": 10}
MIN_RUN = 3


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
