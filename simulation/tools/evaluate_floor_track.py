"""Score the dashboard's floor track on a Unity render against the rig truth (N3).

    python simulation/tools/evaluate_floor_track.py simulation/Exports/demo-001 \
        --track backend/data/scenarios/<recording>/floor_track.json \
        --room backend/data/rooms/<room>/room.json

The track is computed by the backend from YOLO skeletons of the rendered pixels and the
room imported from the render's scene_geometry.json (backend/scripts/import_sim_room.py).
The truth is evaluator-only: the character's body position from
evaluator_only/simulation_states.jsonl, and its facing from the rig's shoulders and hips
in evaluator_only/rig_skeleton.jsonl, unprojected with the exact Unity camera. Nothing
here feeds the track or the inventory. Reports position error (cm) and facing error
(degrees), split by the track's source (ankles, hips, bridged).
"""
import argparse
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend/src"))
from pharma.services.sim_scene import quaternion_matrix, unity_points_to_room

RIG_R_SHOULDER, RIG_L_SHOULDER, RIG_R_HIP, RIG_L_HIP = 4, 7, 10, 13


def unproject_unity(camera, pixels_with_depth):
    """Rig joints (x, y top-left pixels, z camera depth in meters) -> Unity world points."""
    pts = np.asarray(pixels_with_depth, float)
    f = (camera["height"] / 2) / np.tan(np.radians(camera["vertical_fov_deg"]) / 2)
    local = np.column_stack([(pts[:, 0] - camera["width"] / 2) / f * pts[:, 2],
                             (camera["height"] / 2 - pts[:, 1]) / f * pts[:, 2], pts[:, 2]])
    return local @ quaternion_matrix(camera["rotation_xyzw"]).T + np.asarray(camera["position"], float)


def truth_yaw_deg(camera, joints, mesh_to_room):
    """Facing in the room frame (0 faces +Z, 90 faces +X), from shoulders and hips."""
    pts = [[j["x"], j["y"], j["z"]] for j in joints]
    world = unproject_unity(camera, pts)
    room = unity_points_to_room(world, mesh_to_room)
    left = (room[RIG_L_SHOULDER] - room[RIG_R_SHOULDER]) + (room[RIG_L_HIP] - room[RIG_R_HIP])
    return float(np.degrees(np.arctan2(-left[2], left[0]))) % 360  # forward = (-left_z, left_x)


def yaw_error(a, b):
    return abs((a - b + 180) % 360 - 180)


def _summary(values):
    if not values:
        return None
    v = np.asarray(values)
    return {"frames": int(len(v)), "median": round(float(np.median(v)), 2),
            "p90": round(float(np.percentile(v, 90)), 2), "max": round(float(v.max()), 2)}


def evaluate(track, states, rig, scene, mesh_to_room, stand_speed_mps=0.1):
    """Per-source error summaries. Frames without a track position count as missing."""
    fields = track["fields"]
    ix, iz, iyaw, isrc = (fields.index(k) for k in ("x", "z", "yaw_deg", "source"))
    fps = track["fps"]
    body_u = np.array([[s["body_position"]["x"], s["body_position"]["y"], s["body_position"]["z"]] for s in states])
    body = unity_points_to_room(body_u, mesh_to_room)
    speed = np.r_[0, np.linalg.norm(np.diff(body[:, [0, 2]], axis=0), axis=1) * fps]
    n = min(len(track["frames"]), len(body), len(rig))
    pos, yaw = {}, {}
    missing = 0
    for i in range(n):
        frame = track["frames"][i]
        if frame is None:
            missing += 1
            continue
        src = frame[isrc] or "unknown"
        err_cm = float(np.hypot(frame[ix] - body[i, 0], frame[iz] - body[i, 2]) * 100)
        pos.setdefault(src, []).append(err_cm)
        pos.setdefault("all", []).append(err_cm)
        truth = truth_yaw_deg(scene["camera"], rig[i]["joints_pixels"], mesh_to_room)
        err = yaw_error(frame[iyaw], truth)
        yaw.setdefault(src, []).append(err)
        yaw.setdefault("all", []).append(err)
        if speed[i] < stand_speed_mps:
            yaw.setdefault("standing", []).append(err)
    return {
        "frames": n,
        "placed_fraction": round(1 - missing / max(n, 1), 4),
        "position_cm": {k: _summary(v) for k, v in sorted(pos.items())},
        "facing_deg": {k: _summary(v) for k, v in sorted(yaw.items())},
        "facing_flips_over_90deg": int(sum(e > 90 for e in yaw.get("all", []))),
    }


def _jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("export", type=Path)
    parser.add_argument("--track", type=Path, required=True, help="floor_track.json from the backend")
    parser.add_argument("--room", type=Path, required=True, help="room.json of the imported simulation room")
    parser.add_argument("--output", type=Path, default=None, help="Where to write the report (default: evaluator_only/)")
    args = parser.parse_args()

    scene = json.loads((args.export / "scene_geometry.json").read_text(encoding="utf-8"))
    room = json.loads(args.room.read_text(encoding="utf-8"))
    report = evaluate(
        json.loads(args.track.read_text(encoding="utf-8")),
        _jsonl(args.export / "evaluator_only/simulation_states.jsonl"),
        _jsonl(args.export / "evaluator_only/rig_skeleton.jsonl"),
        scene, np.asarray(room["mesh_to_room"], float),
    )
    out = args.output or args.export / "evaluator_only/floor_track_eval.json"
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
