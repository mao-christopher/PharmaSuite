"""Pinhole camera math: registering a fixed camera in a scanned room, and casting rays back out.

Registration takes point pairs clicked in the camera photo and in the 3D room. With
no calibration board, the lens is assumed simple: square pixels, principal point at
the image center, no distortion. Focal length is searched for together with the pose,
so the same six-plus clicks give both. If the overlay doesn't line up (high error or a
visibly wide-angle lens), a proper lens calibration is the fallback; it is not built.

Conventions follow OpenCV: x_cam = R @ x_room + t, camera x right, y down, z forward.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

MIN_POINTS = 6
MIN_SPREAD_M = 0.05
# Horizontal field of view searched for the focal length, degrees.
FOV_RANGE_DEG = (15.0, 140.0)
FOV_STEPS = 80
# Rough guidance on registration error, scaled to the image: these are unvalidated
# rules of thumb for "good enough to use", not measured accuracy.
GOOD_ERROR_FRACTION = 0.004
CHECK_ERROR_FRACTION = 0.01


@dataclass
class Camera:
    fx: float
    fy: float
    cx: float
    cy: float
    R: np.ndarray  # 3x3 world-to-camera rotation
    t: np.ndarray  # 3
    width: int
    height: int

    @classmethod
    def from_registration(cls, reg) -> "Camera":
        k = reg.intrinsics
        return cls(k.fx, k.fy, k.cx, k.cy, np.asarray(reg.rotation, dtype=np.float64),
                   np.asarray(reg.translation, dtype=np.float64), int(reg.frame_size[0]), int(reg.frame_size[1]))

    @property
    def K(self) -> np.ndarray:
        return np.array([[self.fx, 0, self.cx], [0, self.fy, self.cy], [0, 0, 1.0]])

    @property
    def center(self) -> np.ndarray:
        return -self.R.T @ self.t

    def project(self, points: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Pixels (Nx2) for room points (Nx3), and their depth in front of the camera."""
        cam = np.atleast_2d(points) @ self.R.T + self.t
        z = cam[:, 2]
        with np.errstate(divide="ignore", invalid="ignore"):
            px = np.column_stack([self.fx * cam[:, 0] / z + self.cx, self.fy * cam[:, 1] / z + self.cy])
        return px, z

    def rays(self, normalized: np.ndarray) -> np.ndarray:
        """Unit ray directions in the room for normalized (0-1) image points (Nx2)."""
        pts = np.atleast_2d(normalized)
        px = np.column_stack([pts[:, 0] * self.width, pts[:, 1] * self.height])
        d_cam = np.column_stack([(px[:, 0] - self.cx) / self.fx, (px[:, 1] - self.cy) / self.fy, np.ones(len(px))])
        d = d_cam @ self.R  # R.T @ d for each row
        return d / np.linalg.norm(d, axis=1, keepdims=True)

    def hit_height(self, normalized: np.ndarray, height: float) -> np.ndarray:
        """Where rays through image points cross the horizontal plane y = height (NaN if never)."""
        d = self.rays(normalized)
        c = self.center
        with np.errstate(divide="ignore", invalid="ignore"):
            s = (height - c[1]) / d[:, 1]
        hits = c + d * s[:, None]
        hits[~(s > 0)] = np.nan
        return hits


def _pose_for_focal(obj: np.ndarray, img: np.ndarray, f: float, cx: float, cy: float):
    K = np.array([[f, 0, cx], [0, f, cy], [0, 0, 1.0]])
    try:
        ok, rvec, tvec = cv2.solvePnP(obj, img, K, None, flags=cv2.SOLVEPNP_SQPNP)
    except cv2.error:
        return None
    if not ok:
        return None
    rvec, tvec = cv2.solvePnPRefineLM(obj, img, K, None, rvec, tvec)
    proj, _ = cv2.projectPoints(obj, rvec, tvec, K, None)
    residual = np.linalg.norm(proj.reshape(-1, 2) - img, axis=1)
    R, _ = cv2.Rodrigues(rvec)
    depth = (obj @ R.T + tvec.reshape(3))[:, 2]
    rms = float(np.sqrt(np.mean(residual ** 2)))
    if (depth <= 0).any():
        rms += 1e6  # a pose with points behind the camera is not a real solution
    return rms, R, tvec.reshape(3), residual


def check_points(room: np.ndarray) -> Optional[str]:
    """A note about the point layout, or raises for layouts that can't be solved."""
    if len(room) < MIN_POINTS:
        raise ValueError(f"Click at least {MIN_POINTS} point pairs (have {len(room)}).")
    spread = np.linalg.svd(room - room.mean(axis=0), compute_uv=False)
    if spread[1] < MIN_SPREAD_M:
        raise ValueError("The 3D points lie almost on one line. Spread them across the view.")
    dists = np.linalg.norm(room[:, None] - room[None], axis=2) + np.eye(len(room)) * 1e9
    if dists.min() < 0.02:
        raise ValueError("Two 3D points are the same spot. Remove one of them.")
    if spread[2] < 0.03:
        return "All points are on one flat surface. Adding a couple on a shelf or counter makes the lens estimate steadier."
    return None


def solve_registration(image_norm: Sequence[Sequence[float]], room_pts: Sequence[Sequence[float]],
                       width: int, height: int) -> Dict[str, Any]:
    """Focal length and pose from point pairs. Image points are normalized 0-1."""
    room = np.asarray(room_pts, dtype=np.float64)
    img = np.asarray(image_norm, dtype=np.float64) * np.array([width, height])
    if len(room) != len(img):
        raise ValueError("Each photo point needs a matching 3D point.")
    note = check_points(room)
    cx, cy = width / 2.0, height / 2.0

    def focal(fov_deg: float) -> float:
        return (width / 2.0) / np.tan(np.radians(fov_deg) / 2)

    candidates = []
    for fov in np.geomspace(*FOV_RANGE_DEG, FOV_STEPS):
        result = _pose_for_focal(room, img, focal(fov), cx, cy)
        if result:
            candidates.append((result[0], np.log(focal(fov))))
    if not candidates:
        raise ValueError("Could not solve the camera from these points. Check each pair marks the same spot.")
    candidates.sort()
    # Golden-section refinement in log focal length around the best grid value.
    step = np.log(FOV_RANGE_DEG[1] / FOV_RANGE_DEG[0]) / FOV_STEPS * 2
    lo, hi = candidates[0][1] - step, candidates[0][1] + step
    g = (np.sqrt(5) - 1) / 2

    def cost(logf: float) -> float:
        r = _pose_for_focal(room, img, float(np.exp(logf)), cx, cy)
        return r[0] if r else float("inf")

    a, b = hi - g * (hi - lo), lo + g * (hi - lo)
    fa, fb = cost(a), cost(b)
    for _ in range(40):
        if fa < fb:
            hi, b, fb = b, a, fa
            a = hi - g * (hi - lo)
            fa = cost(a)
        else:
            lo, a, fa = a, b, fb
            b = lo + g * (hi - lo)
            fb = cost(b)
    f = float(np.exp((lo + hi) / 2))
    rms, R, t, residual = _pose_for_focal(room, img, f, cx, cy)
    if rms >= 1e6:
        raise ValueError("Could not place the camera with every point in front of it. Check the pairs.")
    cam = Camera(f, f, cx, cy, R, t, width, height)
    center = cam.center
    forward = R[2]  # camera +z in room coordinates
    diag = float(np.hypot(width, height))
    quality = "good" if rms <= GOOD_ERROR_FRACTION * diag else "check" if rms <= CHECK_ERROR_FRACTION * diag else "poor"
    return {
        "intrinsics": {"fx": f, "fy": f, "cx": cx, "cy": cy},
        "rotation": R.tolist(),
        "translation": t.tolist(),
        "position": center.tolist(),
        "rms_px": rms,
        "max_px": float(residual.max()),
        "errors_px": residual.tolist(),
        "quality": quality,
        "note": note,
        "height_m": float(center[1]),
        "hfov_deg": float(np.degrees(2 * np.arctan(width / (2 * f)))),
        "tilt_down_deg": float(np.degrees(np.arcsin(np.clip(-forward[1], -1, 1)))),
        "heading_deg": float(np.degrees(np.arctan2(forward[0], forward[2]))),
        "frame_size": [width, height],
    }


def box_corners(center: Sequence[float], size: Sequence[float], yaw_deg: float) -> np.ndarray:
    """The 8 corners of an upright box rotated `yaw_deg` about +Y."""
    w, h, d = (s / 2 for s in size)
    local = np.array([[x, y, z] for x in (-w, w) for y in (-h, h) for z in (-d, d)])
    a = np.radians(yaw_deg)
    rot = np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]])
    return local @ rot.T + np.asarray(center, dtype=np.float64)
