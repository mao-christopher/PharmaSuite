"""Scanned rooms: an iPhone scan (GLB) placed in a metric room frame, 3D regions, and camera registrations.

`data/rooms/<room_id>/` holds the scan exactly as uploaded (`mesh.glb`, never rewritten),
`room.json` (the transform from scan to room coordinates, regions and camera
registrations), and two top-down images used by the floor map: `plan.png` (floor and
furniture shaded by height) and `obstacles.png` (where nobody can stand).

Room frame: meters, right-handed, Y up, floor at y = 0, walls turned to line up with X
and Z. That is glTF's own convention, so the dashboard applies `mesh_to_room` to the
loaded scene as is. Import only levels and turns the scan; it never rescales it
(LiDAR scans are already metric).
"""

import hashlib
import json
import re
import shutil
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from pharma.db.models import LAYOUT_ID_PATTERN, Catalog, Region3D, Room, RoomMesh, RoomPlan

ROOM_FILE = "room.json"
MESH_FILE = "mesh.glb"
PLAN_FILE = "plan.png"
OBSTACLES_FILE = "obstacles.png"
ROOM_FILES = {MESH_FILE, PLAN_FILE, OBSTACLES_FILE}

# Floor leveling. Up-facing triangles are binned by height; the floor is the lowest bin
# holding at least FLOOR_MIN_SHARE of the largest bin's area (so a stray patch under the
# floor doesn't win, and neither does a big table).
UP_NORMAL_Y = 0.85
FLOOR_BIN_M = 0.02
FLOOR_MIN_SHARE = 0.25
FLOOR_BAND_M = 0.04
MAX_TILT_DEG = 10.0
# Walls are turned onto X/Z only when vertical surfaces agree on a direction this well
# (resultant length of their 4-fold angles, 0-1).
MIN_WALL_AGREEMENT = 0.15

# Top-down plan.
PLAN_RESOLUTION_M = 0.02
PLAN_MAX_CELLS = 2500  # per side
FLOOR_TOLERANCE_M = 0.05
OBSTACLE_MIN_M = 0.15  # anything this far above the floor blocks standing there...
OBSTACLE_MAX_M = 1.8  # ...unless it is overhead (a hanging sign, a door frame)
PLAN_MAX_HEIGHT_M = 2.2  # the ceiling is left out of the plan
SAMPLES_PER_M2 = 8000
MIN_OBSTACLE_CELLS = 6  # smaller specks of "furniture" are scan noise


class ScanError(ValueError):
    """The uploaded file can't be used as a room scan."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def room_dir(rooms_dir: Path, room_id: str) -> Path:
    if not re.fullmatch(LAYOUT_ID_PATTERN, room_id):
        raise FileNotFoundError(f"Room '{room_id}' not found")
    return rooms_dir / room_id


def list_rooms(rooms_dir: Path) -> List[Room]:
    if not rooms_dir.exists():
        return []
    rooms = []
    for path in sorted(rooms_dir.glob(f"*/{ROOM_FILE}")):
        try:
            rooms.append(Room.model_validate_json(path.read_text(encoding="utf-8")))
        except ValueError:
            continue  # a half-written or hand-edited room shouldn't hide the others
    return rooms


def load_room(rooms_dir: Path, room_id: str) -> Room:
    path = room_dir(rooms_dir, room_id) / ROOM_FILE
    if not path.exists():
        raise FileNotFoundError(f"Room '{room_id}' not found")
    return Room.model_validate_json(path.read_text(encoding="utf-8"))


def save_room(rooms_dir: Path, room: Room, bump: bool = True) -> Room:
    """Write room.json atomically; `bump` marks regions or cameras as changed."""
    saved = room.model_copy(update={
        "room_version": room.room_version + 1 if bump else room.room_version,
        "updated_at": _now(),
    })
    Room.model_validate(saved.model_dump(mode="json"))  # re-run the consistency checks
    path = room_dir(rooms_dir, room.room_id) / ROOM_FILE
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(saved.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return saved


def delete_room(rooms_dir: Path, room_id: str) -> None:
    path = room_dir(rooms_dir, room_id)
    if not (path / ROOM_FILE).exists():
        raise FileNotFoundError(f"Room '{room_id}' not found")
    shutil.rmtree(path)


def registration_for_view(rooms_dir: Path, layout_id: str) -> Optional[Tuple[Room, Any]]:
    """The room and camera registration of a camera view, if it has been registered."""
    for room in list_rooms(rooms_dir):
        for cam in room.cameras:
            if cam.layout_id == layout_id:
                return room, cam
    return None


def new_room_id(rooms_dir: Path, name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "room"
    candidate, n = base, 2
    while (rooms_dir / candidate).exists():
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def validate_regions(regions: List[Region3D], catalog: Catalog) -> None:
    """Shelves must name a configured medication (the Room model checks the rest)."""
    keys = {m.medication_key for m in catalog.medications}
    unknown = sorted({r.medication_key for r in regions
                      if r.region_type == "designated_shelf" and r.medication_key and r.medication_key not in keys})
    if unknown:
        raise ValueError(f"Not configured medications: {', '.join(unknown)}. Add them in Setup first.")


# ---------------------------------------------------------------- GLB reading


def _glb_json(data: bytes) -> Dict[str, Any]:
    """The JSON chunk of a binary glTF, after checking the header."""
    if len(data) < 20 or data[:4] != b"glTF":
        raise ScanError("Not a GLB file. Export the scan as GLB (binary glTF).")
    version, _length = struct.unpack_from("<II", data, 4)
    if version != 2:
        raise ScanError(f"GLB version {version} isn't supported; export glTF 2.0.")
    chunk_length, chunk_type = struct.unpack_from("<II", data, 12)
    if chunk_type != 0x4E4F534A:  # "JSON"
        raise ScanError("The GLB has no JSON chunk.")
    return json.loads(data[20:20 + chunk_length].decode("utf-8"))


def read_scan(path: Path) -> Tuple[np.ndarray, np.ndarray]:
    """All triangles of a GLB in its own coordinates: (vertices Nx3, faces Mx3)."""
    import trimesh

    with path.open("rb") as f:
        head = f.read(20)
        if len(head) < 20:
            raise ScanError("Not a GLB file. Export the scan as GLB (binary glTF).")
        chunk_length = struct.unpack_from("<I", head, 12)[0]
        gltf = _glb_json(head + f.read(chunk_length))
    compressed = {"KHR_draco_mesh_compression", "EXT_meshopt_compression"} & set(gltf.get("extensionsRequired", []))
    if compressed:
        raise ScanError(f"The scan uses {', '.join(sorted(compressed))}. Export it again without mesh compression.")
    try:
        scene = trimesh.load(str(path), file_type="glb", force="scene", process=False)
    except Exception as e:  # trimesh raises many types for broken files
        raise ScanError(f"Could not read the GLB: {e}") from None

    vertices, faces, offset = [], [], 0
    for node in scene.graph.nodes_geometry:
        transform, geometry_name = scene.graph[node]
        geometry = scene.geometry.get(geometry_name)
        if not isinstance(geometry, trimesh.Trimesh) or len(geometry.faces) == 0:
            continue  # point clouds and lines carry no surfaces
        v = np.asarray(geometry.vertices, dtype=np.float64)
        v = v @ transform[:3, :3].T + transform[:3, 3]
        vertices.append(v)
        faces.append(np.asarray(geometry.faces, dtype=np.int64) + offset)
        offset += len(v)
    if not faces:
        raise ScanError("The GLB has no triangle meshes.")
    return np.concatenate(vertices), np.concatenate(faces)


# ---------------------------------------------------------------- leveling


def _face_geometry(vertices: np.ndarray, faces: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per face: centroid, unit normal, area (degenerate faces get zero area)."""
    tri = vertices[faces]
    cross = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    length = np.linalg.norm(cross, axis=1)
    normals = np.divide(cross, length[:, None], out=np.zeros_like(cross), where=length[:, None] > 0)
    return tri.mean(axis=1), normals, length / 2


def _rotation_between(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Smallest rotation taking unit vector a onto unit vector b."""
    v = np.cross(a, b)
    c = float(np.dot(a, b))
    if np.linalg.norm(v) < 1e-12:
        return np.eye(3)
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx * (1.0 / (1.0 + c))


def _yaw_matrix(theta: float) -> np.ndarray:
    """Rotation about +Y by theta radians (counterclockwise seen from above)."""
    c, s = np.cos(theta), np.sin(theta)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def level_scan(vertices: np.ndarray, faces: np.ndarray) -> Tuple[np.ndarray, Dict[str, Any]]:
    """The 4x4 transform from scan to room coordinates, and what the fit found.

    Finds the floor among up-facing triangles, tilts it level (small corrections only:
    phone scans are already gravity-aligned), puts it at y = 0, turns the dominant
    wall direction onto X/Z, and centers the floor on the origin.
    """
    centroids, normals, areas = _face_geometry(vertices, faces)
    up = (normals[:, 1] > UP_NORMAL_Y) & (areas > 0)
    if areas[up].sum() < 0.5:
        raise ScanError("Found no floor in the scan (less than 0.5 m² of upward-facing surface). "
                        "Scan with the floor in view, and export with Y up.")
    heights, weights = centroids[up, 1], areas[up]
    edges = np.arange(heights.min(), heights.max() + 2 * FLOOR_BIN_M, FLOOR_BIN_M)
    hist, edges = np.histogram(heights, bins=edges, weights=weights)
    floor_bin = int(np.argmax(hist >= FLOOR_MIN_SHARE * hist.max()))
    floor_y = (edges[floor_bin] + edges[floor_bin + 1]) / 2

    # Fit the plane through the corners of floor triangles: a floor can be a few big ones.
    band = np.abs(heights - floor_y) <= FLOOR_BAND_M
    pts = vertices[faces[up][band]].reshape(-1, 3)
    w = np.repeat(weights[band] / 3, 3)
    normal = np.array([0.0, 1.0, 0.0])
    fit_rms = None
    for _ in range(3):  # weighted plane fit y = a x + b z + c, dropping outliers
        A = np.column_stack([pts[:, 0], pts[:, 2], np.ones(len(pts))])
        sw = np.sqrt(w)
        (a, b, c), *_ = np.linalg.lstsq(A * sw[:, None], pts[:, 1] * sw, rcond=None)
        residual = pts[:, 1] - A @ np.array([a, b, c])
        fit_rms = float(np.sqrt(np.average(residual ** 2, weights=w)))
        keep = np.abs(residual) <= max(0.02, 2.5 * fit_rms)
        if keep.all() or keep.sum() < 3:
            break
        pts, w = pts[keep], w[keep]
    candidate = np.array([-a, 1.0, -b])
    candidate /= np.linalg.norm(candidate)
    tilt = float(np.degrees(np.arccos(np.clip(candidate[1], -1, 1))))
    tilt_applied = tilt <= MAX_TILT_DEG
    if tilt_applied:
        normal = candidate
    level = _rotation_between(normal, np.array([0.0, 1.0, 0.0]))

    rotated_normals = normals @ level.T
    vertical = (np.abs(rotated_normals[:, 1]) < 0.2) & (areas > 0)
    theta = np.arctan2(rotated_normals[vertical, 0], rotated_normals[vertical, 2])
    wv = areas[vertical]
    agreement, yaw = 0.0, 0.0
    if wv.sum() > 0:
        s, c4 = np.sum(wv * np.sin(4 * theta)), np.sum(wv * np.cos(4 * theta))
        agreement = float(np.hypot(s, c4) / wv.sum())
        if agreement >= MIN_WALL_AGREEMENT:
            yaw = float(np.arctan2(s, c4) / 4)
    # Wall normals sit at angle `yaw` (mod 90°); turning by -yaw puts them on the axes.
    rotation = _yaw_matrix(-yaw) @ level

    floor_pts = pts @ rotation.T
    lo, hi = np.percentile(floor_pts[:, [0, 2]], [1, 99], axis=0)
    shift = np.array([-(lo[0] + hi[0]) / 2, -float(np.median(floor_pts[:, 1])), -(lo[1] + hi[1]) / 2])
    transform = np.eye(4)
    transform[:3, :3] = rotation
    transform[:3, 3] = shift
    return transform, {
        "floor_height_in_scan_m": round(float(floor_y), 4),
        "fit_rms_m": round(fit_rms or 0.0, 4),
        "tilt_deg": round(tilt, 2),
        "tilt_corrected": tilt_applied,
        "wall_agreement": round(agreement, 3),
        "yaw_deg": round(float(np.degrees(-yaw)), 2),
    }


def apply_transform(points: np.ndarray, transform: np.ndarray) -> np.ndarray:
    return points @ transform[:3, :3].T + transform[:3, 3]


# ---------------------------------------------------------------- plan images


def build_plan(vertices: np.ndarray, faces: np.ndarray, seed: int = 0) -> Tuple[np.ndarray, np.ndarray, Tuple[float, float], float]:
    """Top-down RGBA plan and obstacle mask from room-frame geometry.

    Returns (plan RGBA, obstacles uint8 where 255 = can't stand here, origin (x, z),
    meters per pixel). Surfaces are sampled so large flat triangles still fill cells.
    """
    import trimesh

    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    count = int(np.clip(mesh.area * SAMPLES_PER_M2, 50_000, 3_000_000))
    samples, _ = trimesh.sample.sample_surface(mesh, count, seed=seed)
    points = np.concatenate([samples, vertices])
    points = points[points[:, 1] <= PLAN_MAX_HEIGHT_M]

    lo = np.percentile(points[:, [0, 2]], 0.2, axis=0) - 0.1
    hi = np.percentile(points[:, [0, 2]], 99.8, axis=0) + 0.1
    resolution = max(PLAN_RESOLUTION_M, float((hi - lo).max()) / PLAN_MAX_CELLS)
    width, height = (np.ceil((hi - lo) / resolution).astype(int) + 1).tolist()
    col = ((points[:, 0] - lo[0]) / resolution).astype(int)
    row = ((points[:, 2] - lo[1]) / resolution).astype(int)
    inside = (col >= 0) & (col < width) & (row >= 0) & (row < height)
    col, row, y = col[inside], row[inside], points[inside, 1]

    floor = np.zeros((height, width), dtype=bool)
    at_floor = np.abs(y) <= FLOOR_TOLERANCE_M
    floor[row[at_floor], col[at_floor]] = True
    top = np.full((height, width), -np.inf)
    above = y > FLOOR_TOLERANCE_M
    np.maximum.at(top, (row[above], col[above]), y[above])
    blocking = np.zeros((height, width), dtype=np.uint8)
    blocks = (y >= OBSTACLE_MIN_M) & (y <= OBSTACLE_MAX_M)
    blocking[row[blocks], col[blocks]] = 255
    kernel = np.ones((3, 3), np.uint8)
    # Close pinholes between samples, then drop specks. (Opening would erase walls: a
    # scanned wall is a single surface, one cell thick from above.)
    blocking = cv2.morphologyEx(blocking, cv2.MORPH_CLOSE, kernel)
    count, labels, stats, _ = cv2.connectedComponentsWithStats((blocking > 0).astype(np.uint8), connectivity=8)
    small = np.flatnonzero(stats[:, cv2.CC_STAT_AREA] < MIN_OBSTACLE_CELLS)
    blocking[np.isin(labels, small[small > 0])] = 0
    floor = cv2.morphologyEx(floor.astype(np.uint8), cv2.MORPH_CLOSE, kernel).astype(bool)

    plan = np.zeros((height, width, 4), dtype=np.uint8)
    plan[floor] = (226, 229, 232, 255)
    sampled = np.isfinite(top)
    # Sampling leaves a few furniture cells empty or with only side samples; close those
    # holes and shade each cell by the tallest surface in its neighborhood so tops draw solid.
    furniture = cv2.morphologyEx(sampled.astype(np.uint8), cv2.MORPH_CLOSE, kernel).astype(bool)
    top = np.where(furniture, cv2.dilate(np.where(sampled, top, -1.0).astype(np.float32), kernel), -np.inf)
    shade = np.clip(top / PLAN_MAX_HEIGHT_M, 0, 1)
    tone = (205 - shade * 125).astype(np.uint8)  # taller is darker
    plan[furniture, 0] = tone[furniture]
    plan[furniture, 1] = (tone[furniture] * 1.02).clip(0, 255).astype(np.uint8)
    plan[furniture, 2] = (tone[furniture] * 1.06).clip(0, 255).astype(np.uint8)
    plan[furniture, 3] = 255
    return plan, blocking, (float(lo[0]), float(lo[1])), resolution


def _write_png(path: Path, image: np.ndarray) -> None:
    if image.ndim == 3 and image.shape[2] == 4:
        image = cv2.cvtColor(image, cv2.COLOR_RGBA2BGRA)
    if not cv2.imwrite(str(path), image):
        raise RuntimeError(f"Failed to write {path.name}")


def load_obstacles(rooms_dir: Path, room: Room) -> Optional[np.ndarray]:
    """Obstacle mask (True = nobody can stand there), or None if the image is missing."""
    image = cv2.imread(str(room_dir(rooms_dir, room.room_id) / room.plan.obstacles), cv2.IMREAD_GRAYSCALE)
    return image > 127 if image is not None else None


# ---------------------------------------------------------------- import


def import_scan(rooms_dir: Path, upload: Path, name: Optional[str], source_name: Optional[str] = None) -> Room:
    """Turn an uploaded GLB into a room. The upload file is moved into the room folder."""
    vertices, faces = read_scan(upload)
    transform, fit = level_scan(vertices, faces)
    room_vertices = apply_transform(vertices, transform)
    plan, obstacles, origin, resolution = build_plan(room_vertices, faces)

    label = (name or (source_name or "Room").rsplit(".", 1)[0]).strip()[:80] or "Room"
    room_id = new_room_id(rooms_dir, label)
    folder = rooms_dir / room_id
    folder.mkdir(parents=True)
    try:
        digest = hashlib.sha256()
        with upload.open("rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                digest.update(block)
        size = upload.stat().st_size
        shutil.move(str(upload), folder / MESH_FILE)
        _write_png(folder / PLAN_FILE, plan)
        _write_png(folder / OBSTACLES_FILE, obstacles)
        now = _now()
        room = Room(
            room_id=room_id,
            name=label,
            room_version=1,
            created_at=now,
            updated_at=now,
            mesh=RoomMesh(file=MESH_FILE, source_name=source_name, sha256=digest.hexdigest(), bytes=size,
                          triangles=int(len(faces))),
            mesh_to_room=tuple(tuple(round(float(v), 9) for v in row) for row in transform),
            bounds_min=tuple(round(float(v), 4) for v in room_vertices.min(axis=0)),
            bounds_max=tuple(round(float(v), 4) for v in room_vertices.max(axis=0)),
            floor_fit=fit,
            plan=RoomPlan(image=PLAN_FILE, obstacles=OBSTACLES_FILE, origin=origin, resolution_m=resolution,
                          width=int(plan.shape[1]), height=int(plan.shape[0])),
        )
        return save_room(rooms_dir, room, bump=False)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
