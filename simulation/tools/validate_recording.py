"""Validate replay boundaries, sensor clock, stock fixture, and video hash (stdlib only)."""
import argparse
import hashlib
import json
import math
from pathlib import Path

SENSOR_FIELDS = {"schema_version", "event_id", "session_id", "event_type", "sensor_id", "media_time_ms"}


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


SCENE_KEYS = {"schema", "coordinates", "floor_y", "camera", "regions", "solids"}
REGION_KEYS = {"region_id", "kind", "medication_id", "center", "size", "yaw_deg", "front"}
SOLID_KEYS = {"name", "note", "center", "size", "yaw_deg"}


def _rotate(q, v):
    """Rotate v by the unit quaternion q = (x, y, z, w)."""
    x, y, z, w = q
    tx, ty, tz = 2 * (y * v[2] - z * v[1]), 2 * (z * v[0] - x * v[2]), 2 * (x * v[1] - y * v[0])
    return (v[0] + w * tx + y * tz - z * ty, v[1] + w * ty + z * tx - x * tz, v[2] + w * tz + x * ty - y * tx)


def project_unity(camera, point):
    """A Unity world point as normalized top-left image coordinates, plus its depth."""
    x, y, z, w = camera["rotation_xyzw"]
    local = _rotate((-x, -y, -z, w), [p - c for p, c in zip(point, camera["position"])])
    tan = math.tan(math.radians(camera["vertical_fov_deg"]) / 2)
    aspect = camera["width"] / camera["height"]
    u = (local[0] / local[2] / (tan * aspect) + 1) / 2
    v = 1 - (local[1] / local[2] / tan + 1) / 2
    return u, v, local[2]


def box_corners(box):
    a = math.radians(box.get("yaw_deg", 0.0))
    c, s = math.cos(a), math.sin(a)
    out = []
    for i in range(8):
        hx, hy, hz = [(1 if i >> k & 1 else -1) * box["size"][k] / 2 for k in range(3)]
        out.append((box["center"][0] + c * hx + s * hz, box["center"][1] + hy, box["center"][2] - s * hx + c * hz))
    return out


def check_scene_geometry(scene, calibration):
    """Static setup only; each region box must project onto its calibration rectangle.
    Returns the largest rectangle-edge difference in pixels."""
    if scene.get("schema") != "scene-geometry/1" or set(scene) != SCENE_KEYS:
        raise ValueError("Unexpected scene geometry schema or fields")
    for region in scene["regions"]:
        if not set(region) <= REGION_KEYS:
            raise ValueError("Unexpected region fields: potential ground-truth leakage")
    for solid in scene["solids"]:
        if not set(solid) <= SOLID_KEYS or "bottle" in solid["name"].lower():
            raise ValueError("Scene geometry solids must be static furniture envelopes")
    camera = scene["camera"]
    if (camera["width"], camera["height"]) != (calibration["width"], calibration["height"]):
        raise ValueError("Scene geometry and calibration dimensions disagree")
    rects = {r["region_id"]: r for r in calibration["regions"]}
    if {r["region_id"] for r in scene["regions"]} != set(rects):
        raise ValueError("Scene geometry and calibration regions disagree")
    worst = 0.0
    for region in scene["regions"]:
        pts = [project_unity(camera, p) for p in box_corners(region)]
        if min(p[2] for p in pts) <= 0:
            raise ValueError("Region behind camera")
        rect = rects[region["region_id"]]
        us, vs = [p[0] for p in pts], [p[1] for p in pts]
        for mine, theirs, size in ((min(us), rect["x_min"], camera["width"]), (max(us), rect["x_max"], camera["width"]),
                                   (min(vs), rect["y_min"], camera["height"]), (max(vs), rect["y_max"], camera["height"])):
            worst = max(worst, abs(mine - theirs) * size)
    if worst > 1.0:
        raise ValueError(f"Scene geometry regions miss their calibration rectangles by {worst:.2f} px")
    return worst


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
    scene_px = None
    if "scene_geometry" in manifest:
        path = Path(manifest["scene_geometry"])
        if path.is_absolute() or ".." in path.parts or "evaluator_only" in path.parts:
            raise ValueError("Runtime manifest must not reference ground truth or external paths")
        scene_px = check_scene_geometry(json.loads((root / path).read_text()), calibration)
    inventory = json.loads((root / manifest["initial_inventory"]).read_text())
    for med in inventory["medications"]:
        receipts = [r for r in inventory["receipts"] if r["medication_id"] == med["medication_id"]]
        if sum(r["bottle_count"] for r in receipts) != med["total_bottles"]:
            raise ValueError("Receiving and bottle stock disagree")
        if sum(r["initial_tablets"] for r in receipts) != med["tablets"]:
            raise ValueError("Receiving and tablet stock disagree")
        if med["designated_region"] not in region_ids:
            raise ValueError("Medication has no shelf region")
    scene_note = "" if scene_px is None else f", scene geometry within {scene_px:.3f} px of calibration"
    print(f"Valid replay: {count} frames, {len(events)} sensor events, {len(region_ids)} regions{scene_note}")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording", type=Path)
    validate(parser.parse_args().recording.resolve())
