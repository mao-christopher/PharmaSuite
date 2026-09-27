"""Synthetic room scans, cameras and skeletons with known answers, for the room/floor-track tests."""

from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import numpy as np

from pharma.services.camera_geometry import Camera

WIDTH, HEIGHT = 1280, 720
# Furniture in room coordinates (before the scan transform): center, size.
SHELF = ((0.0, 0.9, -2.2), (1.6, 1.8, 0.4))
COUNTER = ((-1.8, 0.475, 0.5), (0.6, 0.95, 1.2))


def _yaw(deg: float) -> np.ndarray:
    a = np.radians(deg)
    return np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]])


def _tilt(deg: float) -> np.ndarray:
    a = np.radians(deg)
    return np.array([[1, 0, 0], [0, np.cos(a), -np.sin(a)], [0, np.sin(a), np.cos(a)]])


def scan_transform(tilt_deg: float = 2.0, yaw_deg: float = 17.0, offset=(1.3, 0.4, -2.0)) -> np.ndarray:
    """Room -> scan coordinates: the scan is slightly tilted, turned and offset."""
    t = np.eye(4)
    t[:3, :3] = _yaw(yaw_deg) @ _tilt(tilt_deg)
    t[:3, 3] = offset
    return t


def room_scene():
    """Floor (6 x 5 m, top at y = 0), four walls, a shelf unit and a counter."""
    import trimesh

    def box(center, size):
        mesh = trimesh.creation.box(extents=size)
        mesh.apply_translation(center)
        return mesh

    parts = [
        box((0, -0.05, 0), (6, 0.1, 5)),
        box((0, 1.25, -2.55), (6, 2.5, 0.1)),
        box((0, 1.25, 2.55), (6, 2.5, 0.1)),
        box((-3.05, 1.25, 0), (0.1, 2.5, 5)),
        box((3.05, 1.25, 0), (0.1, 2.5, 5)),
        box(*SHELF),
        box(*COUNTER),
    ]
    return parts


def write_scan(path: Path, transform: Optional[np.ndarray] = None) -> np.ndarray:
    """Export the synthetic room as a GLB in scan coordinates; returns the room->scan transform."""
    import trimesh

    transform = scan_transform() if transform is None else transform
    scene = trimesh.Scene()
    for n, mesh in enumerate(room_scene()):
        mesh = mesh.copy()
        mesh.apply_transform(transform)
        scene.add_geometry(mesh, node_name=f"part{n}")
    path.write_bytes(scene.export(file_type="glb"))
    return transform


def look_at_camera(eye: Sequence[float], target: Sequence[float], f: float = 900.0,
                   width: int = WIDTH, height: int = HEIGHT) -> Camera:
    """OpenCV-convention camera at `eye` looking at `target` (room Y up)."""
    eye, target = np.asarray(eye, float), np.asarray(target, float)
    z = target - eye
    z /= np.linalg.norm(z)
    x = np.cross(z, np.array([0.0, 1.0, 0.0]))  # camera right; image y points down
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    R = np.vstack([x, y, z])
    return Camera(f, f, width / 2, height / 2, R, -R @ eye, width, height)


def default_camera() -> Camera:
    return look_at_camera((2.2, 2.4, 2.2), (-0.2, 0.7, -0.8))


def skeleton(position: Tuple[float, float], yaw_deg: float) -> np.ndarray:
    """17 COCO joints (room coordinates) of a person standing at (x, z) facing yaw_deg."""
    a = np.radians(yaw_deg)
    fwd = np.array([np.sin(a), 0, np.cos(a)])
    left = np.array([np.cos(a), 0, -np.sin(a)])  # up x forward
    base = np.array([position[0], 0, position[1]])
    up = np.array([0, 1.0, 0])

    def at(h, side=0.0, ahead=0.0):
        return base + up * h + left * side + fwd * ahead

    return np.array([
        at(1.62, 0, 0.09),  # nose
        at(1.66, 0.03, 0.07), at(1.66, -0.03, 0.07),  # eyes
        at(1.63, 0.075, 0), at(1.63, -0.075, 0),  # ears
        at(1.42, 0.18), at(1.42, -0.18),  # shoulders
        at(1.12, 0.21), at(1.12, -0.21),  # elbows
        at(0.86, 0.22, 0.05), at(0.86, -0.22, 0.05),  # wrists
        at(0.95, 0.13), at(0.95, -0.13),  # hips
        at(0.5, 0.12, 0.02), at(0.5, -0.12, 0.02),  # knees
        at(0.08, 0.11), at(0.08, -0.11),  # ankles
    ])


def observe(cam: Camera, joints: np.ndarray, hide: Sequence[int] = (), noise_px: float = 0.0,
            rng: Optional[np.random.Generator] = None) -> Optional[List[List[float]]]:
    """What a pose model would report: normalized keypoints with confidence.

    The face is confident only when turned toward the camera, like a real detector.
    """
    px, z = cam.project(joints)
    if (z <= 0).any():
        return None
    if noise_px:
        px = px + (rng or np.random.default_rng(0)).normal(0, noise_px, px.shape)
    toward = cam.center - joints[5:7].mean(axis=0)
    facing = np.cross(joints[5] - joints[6], [0, 1, 0])  # (left - right) x up = forward
    face_conf = 0.9 if np.dot(facing[[0, 2]], toward[[0, 2]]) > 0 else 0.05
    out = []
    for i, (u, v) in enumerate(px):
        conf = face_conf if i <= 2 else 0.9
        out.append([float(u / cam.width), float(v / cam.height), 0.05 if i in hide else conf])
    return out
