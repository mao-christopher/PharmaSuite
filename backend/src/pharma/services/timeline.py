"""The re-enactment timeline: everything Unity needs to replay one recording, in one file.

It bundles the floor track, the pickup and put-down decisions as the inventory made them,
the bottle counts before the recording, the rebuilt room and the registered cameras.
Everything here comes from the dashboard's own evidence (CV, the wearable signals and
employee confirmations). Nothing evaluator-only goes in: no rig truth, no Unity object
transforms, no scripted outcomes.

The file is deterministic: the same inputs give byte-identical JSON, and `inputs_sha256`
changes only when something the re-enactment shows changes (for example an employee
confirming a pending location). `inputs_key` is a cheaper hash of the same inputs that
can be checked without computing the track; renders are keyed on it. Action IDs are the
wearable event IDs.
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

SCHEMA = "timeline/1"
TIMELINE_FILE = "timeline.json"

PUTDOWN_OUTCOMES = {"dispensing_counter": "counter", "disposal": "disposed"}


def canonical(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def _status(row: Optional[Dict[str, Any]]) -> str:
    """not_applied | pending | confirmed (by an employee) | decided (by CV, definite)."""
    if row is None:
        return "not_applied"
    if row.get("confirmed_region_id"):
        return "confirmed"
    if row.get("held_pending") or row.get("state") == "NEEDS_CONFIRMATION":
        return "pending"
    return "decided"


def _outcome(kind: str, status: str, region_type: Optional[str], region: Optional[str],
             original_shelf: Optional[str]) -> Optional[str]:
    """How the action looks in the re-enactment (M8): in_hand | returned | misplaced |
    counter | disposed | pending. Derived from the accepted region, not the session's
    current state, which later signals may have moved on."""
    if status == "not_applied":
        return None
    if status == "pending":
        return "pending"
    if kind == "pickup":
        return "in_hand"
    if region_type in PUTDOWN_OUTCOMES:
        return PUTDOWN_OUTCOMES[region_type]
    return "returned" if region == original_shelf else "misplaced"


def build_actions(events: Iterable[Dict[str, Any]], activity: List[Dict[str, Any]],
                  sessions: Dict[str, Any], region_types: Dict[str, str], session_prefix: str) -> List[Dict[str, Any]]:
    """One action per pickup/put-down signal, in media time order.

    A pending action keeps its nearest region only as `suggested_region_id`; its
    `region_id` stays empty until an employee confirms, so the re-enactment can't show a
    put-down the inventory hasn't accepted.
    """
    rows = {r["event_id"]: r for r in activity}
    actions = []
    for event in sorted(events, key=lambda e: (e["media_time_ms"], e["event_id"])):
        if event.get("event_type") not in ("pickup", "release"):
            continue
        row = rows.get(event["event_id"])
        status = _status(row)
        session_id = row["session_id"] if row else session_prefix + event["session_id"]
        session = sessions.get(session_id)
        region = None
        suggested = None
        if row:
            suggested = row.get("nearest_region_id")
            region = row.get("confirmed_region_id") or (suggested if status == "decided" else None)
        kind = "pickup" if event["event_type"] == "pickup" else "putdown"
        original = getattr(session, "original_shelf_id", None)
        region_type = region_types.get(region) if region else None
        actions.append({
            "action_id": event["event_id"],
            "type": kind,
            "contact_ms": int(event["media_time_ms"]),
            "bottle_id": session_id,
            "status": status,
            "region_id": region,
            "region_type": region_type,
            "suggested_region_id": suggested if region is None else None,
            "outcome": _outcome(kind, status, region_type, region, original),
            "medication_key": (row or {}).get("medication_key") or getattr(session, "medication_key", None),
            "original_shelf_id": original,
            "camera_id": (row or {}).get("camera_id"),
            "layout_id": (row or {}).get("layout_id"),
            "calibration_version": (row or {}).get("calibration_version"),
        })
    return actions


def start_counts(entry: Dict[str, Any], inventory: Dict[str, Any]) -> Dict[str, Any]:
    """Bottles per shelf (and at the counter or in hand) before the recording's first signal.

    Recordings applied before this snapshot was kept fall back to the current counts, and
    say so: those may already include this recording's own moves.
    """
    snapshot = entry.get("start_inventory")
    if snapshot is not None:
        source = "before_first_signal"
    else:
        snapshot = {key: {"shelf_counts": dict(inv.shelf_counts), "held_bottles": inv.held_bottles,
                          "counter_bottles": inv.counter_bottles, "total_bottles": inv.total_bottles}
                    for key, inv in inventory.items()}
        source = "current" if not entry.get("applied_event_ids") else "current_after_recording"
    shelves: Dict[str, Dict[str, int]] = {}
    for key, inv in sorted(snapshot.items()):
        for region_id, n in sorted(inv["shelf_counts"].items()):
            if n:
                shelves.setdefault(region_id, {})[key] = int(n)
    return {
        "source": source,
        "shelves": shelves,
        "counter": {k: int(v["counter_bottles"]) for k, v in sorted(snapshot.items()) if v["counter_bottles"]},
        "held": {k: int(v["held_bottles"]) for k, v in sorted(snapshot.items()) if v["held_bottles"]},
    }


def camera_entry(camera_id: Optional[str], layout_id: str, found) -> Dict[str, Any]:
    if not found:
        return {"camera_id": camera_id, "layout_id": layout_id, "registered": False}
    room, reg = found
    return {
        "camera_id": camera_id,
        "layout_id": layout_id,
        "registered": True,
        "room_id": room.room_id,
        "registration_revision": reg.revision,
        "view_calibration_version": reg.view_calibration_version,
        "frame_size": list(reg.frame_size),
        "intrinsics": reg.intrinsics.model_dump(),
        "rotation": [list(r) for r in reg.rotation],
        "translation": list(reg.translation),
        "position": list(reg.position),
        "rms_px": reg.rms_px,
    }


def build_timeline(*, recording: str, label: str, fps: float, frame_size, duration_ms: Optional[int],
                   track: Dict[str, Any], actions: List[Dict[str, Any]], counts: Dict[str, Any],
                   rebuilt: Dict[str, Any], room, cameras: List[Dict[str, Any]], catalog) -> Dict[str, Any]:
    medications = {m.medication_key: {"name": m.name, "strength": m.strength} for m in catalog.medications}
    body = {
        "schema": SCHEMA,
        "recording": recording,
        "label": label,
        "fps": fps,
        "frame_size": list(frame_size),
        "duration_ms": duration_ms,
        "room": {"room_id": room.room_id, "room_version": room.room_version,
                 "regions": [r.model_dump(mode="json") for r in room.regions]},
        "medications": medications,
        "cameras": cameras,
        "track": {k: track[k] for k in ("schema", "fps", "frame_size", "fields", "frames", "hip_height_m", "camera")},
        "actions": actions,
        "start_counts": counts,
        "rebuilt": rebuilt,
    }
    body["inputs_sha256"] = hashlib.sha256(canonical(body).encode()).hexdigest()
    return body


def write_timeline(scenario_dir: Path, timeline: Dict[str, Any]) -> Path:
    """Write the file only when it changed, so an unchanged export leaves it untouched."""
    path = scenario_dir / TIMELINE_FILE
    text = canonical(timeline)
    try:
        if path.read_text(encoding="utf-8") == text:
            return path
    except OSError:
        pass
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return path


def collect(scenario_dir: Path, name: str, store, rooms_dir: Path, layout_id: str, catalog) -> Dict[str, Any]:
    """Everything the timeline depends on except the heavy parts (track frames, rebuilt room).

    Cheap enough to run for every recording in a list, so `key` tells whether a render is
    stale. Returns {"reason": ...} when the recording can't be re-enacted yet.
    """
    from pharma.services import floor_track, room_rebuild
    from pharma.services.fixture_loader import load_json, load_jsonl
    from pharma.services.multicamera import camera_specs
    from pharma.services.recordings import EVENTS_FILE, POSES_FILE
    from pharma.services.room import registration_for_view
    from pharma.services.store import session_key

    found = registration_for_view(rooms_dir, layout_id)
    if not found:
        return {"reason": "unregistered", "layout_id": layout_id}
    room, reg = found
    poses = scenario_dir / POSES_FILE
    if not poses.exists():
        return {"reason": "no_skeletons", "layout_id": layout_id}
    meta = load_json(scenario_dir / "scenario.json") or {}
    entry = store.recordings.get(name, {})
    specs = camera_specs(scenario_dir) or [{"camera_id": None, "layout_id": layout_id}]
    actions = build_actions(load_jsonl(scenario_dir / EVENTS_FILE), entry.get("activity", []), store.engine.sessions,
                            {r.region_id: r.region_type for r in room.regions}, session_key(name, ""))
    cameras = [camera_entry(c.get("camera_id"), c["layout_id"], registration_for_view(rooms_dir, c["layout_id"]))
               for c in specs]
    counts = start_counts(entry, store.engine.inventory)
    key_parts = {
        "schema": SCHEMA, "recording": name, "label": meta.get("label") or name,
        "duration_ms": meta.get("duration_ms"), "actions": actions, "counts": counts, "cameras": cameras,
        "room": [room.room_id, room.room_version], "regions": [r.model_dump(mode="json") for r in room.regions],
        "track_inputs": floor_track._inputs(poses, room, reg), "rebuild": [room_rebuild.ALGORITHM, room.mesh.sha256],
        "medications": [m.model_dump(mode="json") for m in catalog.medications],
    }
    return {"layout_id": layout_id, "room": room, "registration": reg, "poses": poses, "meta": meta,
            "actions": actions, "counts": counts, "cameras": cameras,
            "key": hashlib.sha256(canonical(key_parts).encode()).hexdigest()}


def export(scenario_dir: Path, name: str, collected: Dict[str, Any], rooms_dir: Path, catalog) -> Dict[str, Any]:
    """Build the full timeline from `collect`'s result and write it to the recording folder."""
    from pharma.services import floor_track, room_rebuild
    from pharma.services.room import load_obstacles

    room, reg = collected["room"], collected["registration"]
    track = floor_track.load_or_compute(scenario_dir, collected["poses"], room, reg, load_obstacles(rooms_dir, room))
    meta = collected["meta"]
    data = build_timeline(
        recording=name, label=meta.get("label") or name, fps=track["fps"], frame_size=track["frame_size"],
        duration_ms=meta.get("duration_ms"), track=track, actions=collected["actions"], counts=collected["counts"],
        rebuilt=room_rebuild.load_or_rebuild(rooms_dir, room, catalog), room=room, cameras=collected["cameras"],
        catalog=catalog)
    data["inputs_key"] = collected["key"]
    write_timeline(scenario_dir, data)
    return data
