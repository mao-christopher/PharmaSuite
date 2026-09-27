"""Where the technician stands and which way they face, per video frame, in room coordinates.

Built from a recording's stored skeletons (`poses.json`) and its camera view's
registration in a scanned room. One camera; when neither feet nor hips are seen, the
frame has no position and nothing is guessed (gaps of up to BRIDGE_FRAMES are joined
so single missed detections don't flicker).

- Position: the ray through the ankles' midpoint meets ankle height above the floor. When the feet are
  hidden (behind the counter), the ray through the hips meets a plane at the person's
  hip height, which is measured from frames where both are visible.
- Facing: for each candidate direction, where would the shoulders and hips appear in the
  image? The direction whose prediction best matches the skeleton wins. The model's
  left/right labels and whether the face is visible separate front from back. While
  walking, the direction of travel takes over.

Presentation and simulation input only: inventory decisions never read this track.
All constants are unvalidated starting values; see plan.md for what has been measured.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from pharma.services.camera_geometry import Camera
from pharma.services.inventory_engine import MIN_KEYPOINT_CONF
from pharma.services.recordings import PoseTrack

ALGORITHM = "floor-track/1"
TRACK_FILE = "floor_track.json"
FIELDS = ["x", "z", "yaw_deg", "source", "conf", "bridged"]

NOSE, L_EYE, R_EYE = 0, 1, 2
L_SHOULDER, R_SHOULDER, L_HIP, R_HIP, L_ANKLE, R_ANKLE = 5, 6, 11, 12, 15, 16

# The pose model's ankle point is the ankle joint, not the sole: rays through it meet a
# plane this far above the floor, or every position lands too far from the camera.
ANKLE_HEIGHT_M = 0.08
HIP_HEIGHT_M, HIP_RANGE_M = 0.95, (0.6, 1.2)
SHOULDER_HEIGHT_M, SHOULDER_RANGE_M = 1.40, (1.05, 1.75)
SHOULDER_HALF_M, HIP_HALF_M = 0.18, 0.13
FACE_SEEN, FACE_HIDDEN = 0.5, 0.2
FACE_PENALTY = 1.0
YAW_STEPS = 72
MIN_BODY_CONFIDENCE = 0.3

BRIDGE_FRAMES = 10
OUTLIER_WINDOW = 7
OUTLIER_M = 0.4
POSITION_SIGMA_S = 0.10
YAW_SIGMA_S = 0.15
WALK_MIN, WALK_FULL = 0.2, 0.5  # m/s: travel direction blends in between these speeds
SPEED_WINDOW_S = 0.3


def _keypoints(track: PoseTrack) -> np.ndarray:
    """Frames x 17 x 3 (x, y normalized; confidence), NaN where no person was found."""
    out = np.full((len(track.frames), 17, 3), np.nan)
    for i, kps in enumerate(track.frames):
        if kps and len(kps) >= 17:
            out[i] = np.asarray(kps[:17], dtype=np.float64)
    return out


def _confident(kp: np.ndarray, *joints: int) -> np.ndarray:
    return np.all(np.stack([kp[:, j, 2] >= MIN_KEYPOINT_CONF for j in joints]), axis=0)


def _midpoint(kp: np.ndarray, a: int, b: int) -> np.ndarray:
    return (kp[:, a, :2] + kp[:, b, :2]) / 2


def _height_along(cam: Camera, image_pts: np.ndarray, ground: np.ndarray) -> np.ndarray:
    """Height at which each ray passes closest to the vertical line through its ground point."""
    d = cam.rays(image_pts)
    c = cam.center
    dxz = d[:, [0, 2]]
    s = np.einsum("ij,ij->i", ground[:, [0, 2]] - c[[0, 2]], dxz) / np.maximum(np.einsum("ij,ij->i", dxz, dxz), 1e-12)
    return c[1] + s * d[:, 1]


def _measured_height(cam: Camera, kp: np.ndarray, ground: np.ndarray, has_ground: np.ndarray,
                     a: int, b: int, default: float, bounds: Tuple[float, float]) -> Tuple[float, int]:
    usable = has_ground & _confident(kp, a, b)
    if usable.sum() < 5:
        return default, int(usable.sum())
    heights = _height_along(cam, _midpoint(kp[usable], a, b), ground[usable])
    return float(np.clip(np.median(heights), *bounds)), int(usable.sum())


def _body_yaw(cam: Camera, kp: np.ndarray, ground: np.ndarray, hip_h: float, shoulder_h: float
              ) -> Tuple[np.ndarray, np.ndarray]:
    """Per frame: facing direction (unit x, z) from shoulders/hips, and a 0-1 confidence."""
    n = len(kp)
    facing = np.full((n, 2), np.nan)
    confidence = np.zeros(n)
    usable = np.isfinite(ground[:, 0]) & _confident(kp, L_SHOULDER, R_SHOULDER)
    if not usable.any():
        return facing, confidence
    idx = np.flatnonzero(usable)
    size = np.array([cam.width, cam.height])
    psi = np.linspace(0, 2 * np.pi, YAW_STEPS, endpoint=False)
    fwd = np.column_stack([np.sin(psi), np.cos(psi)])  # yaw 0 faces +Z, 90° faces +X
    left = np.column_stack([fwd[:, 1], -fwd[:, 0]])  # up x forward, on the floor

    def predicted(offset_m: float, height: float, g: np.ndarray) -> np.ndarray:
        """Image vector right->left joint for every candidate yaw (Y x 2 pixels)."""
        base = np.array([g[0], height, g[2]])
        lp = base + np.column_stack([left[:, 0], np.zeros(YAW_STEPS), left[:, 1]]) * offset_m
        rp = base - np.column_stack([left[:, 0], np.zeros(YAW_STEPS), left[:, 1]]) * offset_m
        return cam.project(lp)[0] - cam.project(rp)[0]

    center = cam.center
    for i in idx:
        g = ground[i]
        observed_s = (kp[i, L_SHOULDER, :2] - kp[i, R_SHOULDER, :2]) * size
        pred_s = predicted(SHOULDER_HALF_M, shoulder_h, g)
        scale = max(float(np.linalg.norm(pred_s, axis=1).max()), 1.0)
        cost = np.sum((pred_s - observed_s) ** 2, axis=1) / scale ** 2
        if kp[i, L_HIP, 2] >= MIN_KEYPOINT_CONF and kp[i, R_HIP, 2] >= MIN_KEYPOINT_CONF:
            observed_h = (kp[i, L_HIP, :2] - kp[i, R_HIP, :2]) * size
            cost = cost + np.sum((predicted(HIP_HALF_M, hip_h, g) - observed_h) ** 2, axis=1) / scale ** 2
        toward = center[[0, 2]] - g[[0, 2]]
        toward = toward / max(np.linalg.norm(toward), 1e-9)
        facing_camera = fwd @ toward
        face = kp[i, [NOSE, L_EYE, R_EYE], 2]
        if face[0] >= FACE_SEEN and max(face[1], face[2]) >= FACE_SEEN:
            cost = cost + FACE_PENALTY * (facing_camera < -0.2)
        elif face.max() < FACE_HIDDEN:
            cost = cost + FACE_PENALTY * (facing_camera > 0.2)
        best = int(np.argmin(cost))
        spread = float(np.median(cost))
        confidence[i] = 0.0 if spread <= 1e-9 else float(np.clip(1 - cost[best] / spread, 0, 1))
        facing[i] = fwd[best]
    return facing, confidence


def _segments(mask: np.ndarray) -> List[Tuple[int, int]]:
    """[start, end) runs where mask is True."""
    runs, start = [], None
    for i, m in enumerate(mask):
        if m and start is None:
            start = i
        elif not m and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(mask)))
    return runs


def _gaussian(values: np.ndarray, valid: np.ndarray, sigma: float) -> np.ndarray:
    """Smooth each column over valid frames only; invalid frames stay NaN."""
    out = np.full_like(values, np.nan)
    if sigma <= 0:
        out[valid] = values[valid]
        return out
    radius = max(1, int(3 * sigma))
    kernel = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma) ** 2)
    n = len(values)

    def smooth(x):  # centered, and the same length as x even when the kernel is longer
        return np.convolve(x, kernel)[radius:radius + n]

    filled = np.where(valid[:, None], values, 0.0)
    weight = smooth(valid.astype(float))
    for c in range(values.shape[1]):
        out[:, c] = smooth(filled[:, c]) / np.maximum(weight, 1e-9)
    out[~valid] = np.nan
    return out


def _reject_outliers(xz: np.ndarray, valid: np.ndarray) -> np.ndarray:
    keep = valid.copy()
    half = OUTLIER_WINDOW // 2
    for i in np.flatnonzero(valid):
        window = xz[max(0, i - half):i + half + 1][valid[max(0, i - half):i + half + 1]]
        if len(window) >= 3 and np.linalg.norm(xz[i] - np.median(window, axis=0)) > OUTLIER_M:
            keep[i] = False
    return keep


def _bridge(values: np.ndarray, valid: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Linearly fill interior gaps of at most BRIDGE_FRAMES; returns filled values and which were filled."""
    out, filled = values.copy(), np.zeros(len(valid), dtype=bool)
    idx = np.flatnonzero(valid)
    for a, b in zip(idx[:-1], idx[1:]):
        if 1 < b - a <= BRIDGE_FRAMES + 1:
            for k in range(a + 1, b):
                u = (k - a) / (b - a)
                out[k] = values[a] * (1 - u) + values[b] * u
                filled[k] = True
    return out, filled


def _clamp_to_free(xz: np.ndarray, valid: np.ndarray, obstacles: Optional[np.ndarray],
                   origin: Tuple[float, float], resolution: float) -> Tuple[np.ndarray, int]:
    """Move points that land on furniture to the nearest spot someone could stand."""
    if obstacles is None or not obstacles.any() or obstacles.all():
        return xz, 0
    # Every free cell gets its own label; obstacle cells get the label of the nearest free one.
    # Labels count free cells in row-major order, starting at 1.
    _dist, labels = cv2.distanceTransformWithLabels(obstacles.astype(np.uint8), cv2.DIST_L2, 5,
                                                    labelType=cv2.DIST_LABEL_PIXEL)
    free_rows, free_cols = np.nonzero(~obstacles)
    moved = 0
    out = xz.copy()
    h, w = obstacles.shape
    for i in np.flatnonzero(valid):
        c = int((xz[i, 0] - origin[0]) / resolution)
        r = int((xz[i, 1] - origin[1]) / resolution)
        if 0 <= r < h and 0 <= c < w and obstacles[r, c]:
            k = labels[r, c] - 1
            if 0 <= k < len(free_rows):
                out[i] = (origin[0] + (free_cols[k] + 0.5) * resolution, origin[1] + (free_rows[k] + 0.5) * resolution)
                moved += 1
    return out, moved


def compute_track(track: PoseTrack, cam: Camera, obstacles: Optional[np.ndarray] = None,
                  plan_origin: Tuple[float, float] = (0.0, 0.0), plan_resolution: float = 0.02) -> Dict[str, Any]:
    """Per-frame floor position and facing for one camera's skeletons."""
    kp = _keypoints(track)
    n = len(kp)
    fps = float(track.fps)

    left_foot, right_foot = _confident(kp, L_ANKLE), _confident(kp, R_ANKLE)
    feet = left_foot | right_foot
    ankle_sum = (np.where(left_foot[:, None], kp[:, L_ANKLE, :2], 0.0)
                 + np.where(right_foot[:, None], kp[:, R_ANKLE, :2], 0.0))
    ankle_pts = ankle_sum / np.maximum(left_foot.astype(int) + right_foot, 1)[:, None]
    ground = np.full((n, 3), np.nan)
    if feet.any():
        ground[feet] = cam.hit_height(ankle_pts[feet], ANKLE_HEIGHT_M)
        ground[feet, 1] = 0.0
    has_feet = np.isfinite(ground[:, 0])
    hip_h, hip_frames = _measured_height(cam, kp, ground, has_feet, L_HIP, R_HIP, HIP_HEIGHT_M, HIP_RANGE_M)
    shoulder_h, _ = _measured_height(cam, kp, ground, has_feet, L_SHOULDER, R_SHOULDER, SHOULDER_HEIGHT_M,
                                     SHOULDER_RANGE_M)

    hips = ~has_feet & _confident(kp, L_HIP, R_HIP)
    if hips.any():
        at_hips = cam.hit_height(_midpoint(kp[hips], L_HIP, R_HIP), hip_h)
        at_hips[:, 1] = 0.0
        ground[hips] = at_hips
    source = np.where(has_feet, "ankles", np.where(np.isfinite(ground[:, 0]), "hips", ""))
    conf = np.where(has_feet, np.nanmax(kp[:, [L_ANKLE, R_ANKLE], 2], axis=1, initial=0),
                    np.nanmin(kp[:, [L_HIP, R_HIP], 2], axis=1, initial=1))

    raw = np.isfinite(ground[:, 0])
    xz = ground[:, [0, 2]]
    valid = _reject_outliers(xz, raw)
    rejected = int(raw.sum() - valid.sum())
    xz_smooth = _gaussian(np.nan_to_num(xz), valid, POSITION_SIGMA_S * fps)
    xz_filled, bridged = _bridge(np.nan_to_num(xz_smooth), valid)
    observed = valid | bridged

    body_ground = np.column_stack([xz_filled[:, 0], np.zeros(n), xz_filled[:, 1]])
    body_ground[~observed] = np.nan
    facing, body_conf = _body_yaw(cam, kp, body_ground, hip_h, shoulder_h)
    body_ok = observed & (body_conf >= MIN_BODY_CONFIDENCE)

    half = max(1, int(SPEED_WINDOW_S * fps / 2))
    velocity = np.full((n, 2), np.nan)
    for a, b in _segments(observed):
        for i in range(a, b):
            lo, hi = max(a, i - half), min(b - 1, i + half)
            if hi > lo:
                velocity[i] = (xz_filled[hi] - xz_filled[lo]) * fps / (hi - lo)
    speed = np.linalg.norm(np.nan_to_num(velocity), axis=1)
    walk_w = np.clip((speed - WALK_MIN) / (WALK_FULL - WALK_MIN), 0, 1)
    walk_dir = np.nan_to_num(velocity) / np.maximum(speed, 1e-9)[:, None]
    body_w = np.where(body_ok, (1 - walk_w) * np.maximum(body_conf, 0.05), 0.0)
    heading = walk_w[:, None] * walk_dir + body_w[:, None] * np.nan_to_num(facing)
    has_heading = observed & (np.linalg.norm(heading, axis=1) > 1e-6)
    heading_unit = heading / np.maximum(np.linalg.norm(heading, axis=1), 1e-9)[:, None]
    for a, b in _segments(observed):  # hold the last heading through frames without one
        last = None
        first = next((heading_unit[i] for i in range(a, b) if has_heading[i]), None)
        for i in range(a, b):
            if has_heading[i]:
                last = heading_unit[i]
            else:
                heading_unit[i] = last if last is not None else (first if first is not None else (0.0, 1.0))
    yaw_vec = _gaussian(heading_unit, observed, YAW_SIGMA_S * fps)
    yaw = np.degrees(np.arctan2(yaw_vec[:, 0], yaw_vec[:, 1]))

    xz_final, clamped = _clamp_to_free(xz_filled, observed, obstacles, plan_origin, plan_resolution)
    frames: List[Optional[List[Any]]] = []
    for i in range(n):
        if not observed[i]:
            frames.append(None)
            continue
        frames.append([
            round(float(xz_final[i, 0]), 3), round(float(xz_final[i, 1]), 3),
            round(float(yaw[i]) % 360, 1), "bridged" if bridged[i] else source[i],
            round(float(conf[i]), 2) if np.isfinite(conf[i]) and not bridged[i] else None, bool(bridged[i]),
        ])
    return {
        "schema": ALGORITHM,
        "fps": fps,
        "frame_size": [int(track.width), int(track.height)],
        "fields": FIELDS,
        "frames": frames,
        "hip_height_m": round(hip_h, 3),
        "hip_height_measured": hip_frames >= 5,
        "shoulder_height_m": round(shoulder_h, 3),
        "stats": {
            "frames": n,
            "observed": int(observed.sum()),
            "from_ankles": int((valid & has_feet).sum()),
            "from_hips": int((valid & ~has_feet).sum()),
            "bridged": int(bridged.sum()),
            "rejected_jumps": rejected,
            "moved_off_furniture": clamped,
            "facing_from_body": int(body_ok.sum()),
        },
    }


# ---------------------------------------------------------------- caching per recording


def _inputs(poses_path: Path, room, reg) -> Dict[str, Any]:
    stat = poses_path.stat()
    return {"algorithm": ALGORITHM, "poses_size": stat.st_size, "poses_mtime_ns": stat.st_mtime_ns,
            "room_id": room.room_id, "room_version": room.room_version, "layout_id": reg.layout_id,
            "registration_revision": reg.revision}


def load_or_compute(scenario_dir: Path, poses_path: Path, room, reg, obstacles: Optional[np.ndarray]) -> Dict[str, Any]:
    """The recording's track, recomputed when its skeletons, room or registration changed."""
    inputs = _inputs(poses_path, room, reg)
    cached = scenario_dir / TRACK_FILE
    if cached.exists():
        try:
            data = json.loads(cached.read_text(encoding="utf-8"))
            if data.get("inputs") == inputs:
                return data
        except (OSError, ValueError):
            pass
    track = PoseTrack.load(poses_path)
    reg_w, reg_h = reg.frame_size
    if abs(track.width / track.height - reg_w / reg_h) > 0.02:
        raise ValueError(f"This recording is {track.width}x{track.height} but the camera was registered on a "
                         f"{reg_w}x{reg_h} photo. Register the camera on a frame from this video.")
    data = compute_track(track, Camera.from_registration(reg), obstacles, tuple(room.plan.origin), room.plan.resolution_m)
    data.update(inputs=inputs, camera={"layout_id": reg.layout_id, "registration_revision": reg.revision,
                                       "view_calibration_version": reg.view_calibration_version},
                room={"room_id": room.room_id, "room_version": room.room_version})
    tmp = cached.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    tmp.replace(cached)
    return data


def yaw_error_deg(a: float, b: float) -> float:
    """Smallest difference between two headings, degrees."""
    return abs((a - b + 180) % 360 - 180)

