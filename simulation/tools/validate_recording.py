"""Validate replay boundaries, sensor clock, stock fixture, and video hash (stdlib only)."""
import argparse
import hashlib
import json
from pathlib import Path

SENSOR_FIELDS = {"schema_version", "event_id", "session_id", "event_type", "sensor_id", "media_time_ms"}


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def validate(root):
    manifest = json.loads((root / "manifest.json").read_text())
    for key in ("video", "imu_events", "calibration", "initial_inventory", "business_events"):
        path = Path(manifest[key])
        if path.is_absolute() or ".." in path.parts or "evaluator_only" in path.parts:
            raise ValueError("Runtime manifest must not reference ground truth or external paths")
        if not (root / path).is_file():
            raise ValueError(f"Missing runtime file: {key}")
    digest = hashlib.sha256((root / manifest["video"]).read_bytes()).hexdigest()
    if digest != manifest["video_sha256"]:
        raise ValueError("Video hash mismatch")
    fps, count = manifest["fps"], manifest["frame_count"]
    if fps <= 0 or count <= 0 or abs(manifest["duration_ms"] - count * 1000 / fps) > .01:
        raise ValueError("Inconsistent media timebase")
    events = read_jsonl(root / manifest["imu_events"])
    seen, previous, holding = set(), -1, False
    for event in events:
        if set(event) != SENSOR_FIELDS:
            raise ValueError("Unexpected sensor fields: potential ground-truth leakage")
        if event["event_id"] in seen:
            raise ValueError("Duplicate sensor event ID")
        seen.add(event["event_id"])
        if event["session_id"] != manifest["session_id"]:
            raise ValueError("Wrong sensor session")
        t = event["media_time_ms"]
        if t < previous or not 0 <= t < manifest["duration_ms"]:
            raise ValueError("Sensor timestamp outside ordered media timeline")
        if abs(t * fps / 1000 - round(t * fps / 1000)) > .01:
            raise ValueError("Action event not aligned to a captured frame")
        previous = t
        kind = event["event_type"]
        if kind == "pickup":
            if holding:
                raise ValueError("Second pickup violates one-bottle constraint")
            holding = True
        elif kind == "release":
            if not holding:
                raise ValueError("Release without pickup")
            holding = False
        elif kind != "movement" or not holding:
            raise ValueError("Unsupported sensor transition")
    if holding:
        raise ValueError("Recording ends while bottle is held")
    calibration = json.loads((root / manifest["calibration"]).read_text())
    if (calibration["width"], calibration["height"]) != (manifest["width"], manifest["height"]):
        raise ValueError("Calibration and video dimensions disagree")
    if calibration["coordinates"] != "normalized_top_left":
        raise ValueError("Unsupported calibration coordinates")
    region_ids = set()
    for region in calibration["regions"]:
        if region["region_id"] in region_ids:
            raise ValueError("Duplicate region ID")
        region_ids.add(region["region_id"])
        if not (0 <= region["x_min"] < region["x_max"] <= 1 and 0 <= region["y_min"] < region["y_max"] <= 1):
            raise ValueError("Region falls outside camera view")
    inventory = json.loads((root / manifest["initial_inventory"]).read_text())
    for med in inventory["medications"]:
        receipts = [r for r in inventory["receipts"] if r["medication_id"] == med["medication_id"]]
        if sum(r["bottle_count"] for r in receipts) != med["total_bottles"]:
            raise ValueError("Receiving and bottle stock disagree")
        if sum(r["initial_tablets"] for r in receipts) != med["tablets"]:
            raise ValueError("Receiving and tablet stock disagree")
        if med["designated_region"] not in region_ids:
            raise ValueError("Medication has no shelf region")
    print(f"Valid replay: {count} frames, {len(events)} sensor events, {len(region_ids)} regions")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording", type=Path)
    validate(parser.parse_args().recording.resolve())
