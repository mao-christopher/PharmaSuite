"""Turn the Unity simulator's scene geometry into a dashboard room (N2).

Rendered recordings follow the same 3D-only path as real footage: the simulator's solid
furniture becomes a box-mesh "scan", its configured regions become 3D boxes, and its
camera becomes a registration built from exact point pairs (about 0 px of error).

The exporter writes `scene_geometry.json` next to `calibration.json`. It is static setup
information like the calibration, not an answer: no bottle, rig or outcome data.

    {
      "schema": "scene-geometry/1",
      "coordinates": "unity_world",          # left-handed, Y up, meters
      "floor_y": 0.0,
      "camera": {"camera_id", "calibration_version", "position": [x, y, z],
                 "rotation_xyzw": [qx, qy, qz, qw], "vertical_fov_deg", "width", "height"},
      "regions": [{"region_id", "kind": "shelf" | "counter" | "disposal", "medication_id",
                   "center": [x, y, z], "size": [x, y, z], "yaw_deg", "front": [x, y, z]?}],
      "solids": [{"name", "center", "size", "yaw_deg"}]
    }

Unity (x, y, z) becomes glTF/room (x, y, -z). The room import then levels and centers the
mesh (`mesh_to_room`), and every box and point here follows the same transform.
"""

import io
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from pharma.services.camera_geometry import Camera

SCHEMA = "scene-geometry/1"
MIRROR = np.diag([1.0, 1.0, -1.0])  # Unity world <-> right-handed room frame
CV_FLIP = np.diag([1.0, -1.0, 1.0])  # Unity camera (y up) <-> OpenCV camera (y down)
FLOOR_MARGIN_M = 0.5
FLOOR_THICKNESS_M = 0.05
MAX_PAIRS = 24
MIN_PAIR_GAP_M = 0.05
EDGE_MARGIN = 0.03  # keep clicked points this far inside the frame (normalized)
REGION_TYPES = {"shelf": "designated_shelf", "counter": "dispensing_counter", "disposal": "disposal"}
MIN_TAG_DEPTH_M = 0.1  # thinner regions are planes; they're deepened over the furniture behind
MAX_TAG_DEPTH_M = 1.0


def unity_to_gltf(points: np.ndarray) -> np.ndarray:
    return np.asarray(points, float) @ MIRROR


def quaternion_matrix(xyzw: Sequence[float]) -> np.ndarray:
    x, y, z, w = np.asarray(xyzw, float) / np.linalg.norm(xyzw)
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def unity_yaw_matrix(yaw_deg: float) -> np.ndarray:
    """Unity's Quaternion.Euler(0, yaw, 0): +Z turns toward +X."""
    a = np.radians(yaw_deg)
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def camera_in_gltf(camera: Dict[str, Any]) -> Camera:
    """The Unity camera as an OpenCV pinhole in the glTF frame (before the room import)."""
    rot_u = quaternion_matrix(camera["rotation_xyzw"])
    center = np.asarray(camera["position"], float)
    w, h = int(camera["width"]), int(camera["height"])
    fy = (h / 2) / np.tan(np.radians(camera["vertical_fov_deg"]) / 2)
    R = CV_FLIP @ rot_u.T @ MIRROR
    t = -CV_FLIP @ rot_u.T @ center
    return Camera(fy, fy, w / 2, h / 2, R, t, w, h)


def _box_axes_gltf(box: Dict[str, Any]) -> np.ndarray:
    """The box's local X, Y, Z axes (rows) in the glTF frame."""
    return (MIRROR @ unity_yaw_matrix(box.get("yaw_deg", 0.0))).T


def box_corners_gltf(box: Dict[str, Any]) -> np.ndarray:
    axes = _box_axes_gltf(box)
    half = np.asarray(box["size"], float) / 2
    signs = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], float)
    return unity_to_gltf(np.asarray(box["center"], float)[None]) + (signs * half) @ axes


def build_mesh_glb(scene: Dict[str, Any]) -> bytes:
    """A GLB of the solid boxes plus a floor slab, in the glTF frame."""
    import trimesh

    solids = scene.get("solids") or []
    if not solids:
        raise ValueError("The scene geometry has no solid boxes.")
    floor_y = float(scene.get("floor_y", 0.0))
    meshes = []
    corners = []
    for box in solids:
        mesh = trimesh.creation.box(extents=np.abs(np.asarray(box["size"], float)))
        transform = np.eye(4)
        # The mirror makes the box's axes left-handed; a box is symmetric, so flipping its
        # Z axis gives the same box with a proper rotation (and outward-facing triangles).
        transform[:3, :3] = (_box_axes_gltf(box) * np.array([[1.0], [1.0], [-1.0]])).T
        transform[:3, 3] = unity_to_gltf(np.asarray(box["center"], float)[None])[0]
        mesh.apply_transform(transform)
        meshes.append(mesh)
        corners.append(np.asarray(mesh.vertices))
    pts = np.vstack(corners)
    lo, hi = pts.min(axis=0) - FLOOR_MARGIN_M, pts.max(axis=0) + FLOOR_MARGIN_M
    floor = trimesh.creation.box(extents=[hi[0] - lo[0], FLOOR_THICKNESS_M, hi[2] - lo[2]])
    floor.apply_translation([(lo[0] + hi[0]) / 2, floor_y - FLOOR_THICKNESS_M / 2, (lo[2] + hi[2]) / 2])
    scene_mesh = trimesh.Scene()
    for n, mesh in enumerate([floor, *meshes]):
        scene_mesh.add_geometry(mesh, node_name=f"part{n:03d}")
    out = io.BytesIO()
    out.write(scene_mesh.export(file_type="glb"))
    return out.getvalue()


def _apply(transform: np.ndarray, points: np.ndarray) -> np.ndarray:
    return points @ transform[:3, :3].T + transform[:3, 3]


def _key(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def medication_keys(medication_ids: Iterable[str], catalog) -> Dict[str, Optional[str]]:
    """Match the simulator's medication IDs (e.g. amoxicillin-500-mg) to catalog keys."""
    by_key = {_key(m.medication_key): m.medication_key for m in catalog.medications}
    return {mid: by_key.get(_key(mid)) for mid in medication_ids}


def deepen_region(region: Dict[str, Any], solids: Sequence[Dict[str, Any]], front_u: np.ndarray) -> Dict[str, Any]:
    """A thin region (the simulator draws shelves as planes on their front) extended back
    over the furniture directly behind it, the way a person tags a shelf row's volume.

    Solids count when they overlap the region sideways and in height and lie within
    MAX_TAG_DEPTH_M behind its front face; the front face itself stays where it was.
    """
    axes = unity_yaw_matrix(region.get("yaw_deg", 0.0)).T  # rows: local X, Y, Z in Unity world
    front = np.asarray(front_u, float) * np.array([1.0, 0.0, 1.0])
    k = max((0, 2), key=lambda i: abs(float(axes[i] @ front)))
    size = np.abs(np.asarray(region["size"], float))
    if size[k] >= MIN_TAG_DEPTH_M or not np.linalg.norm(front):
        return region
    normal = axes[k] * np.sign(float(axes[k] @ front))
    side = axes[2 - k]
    center = np.asarray(region["center"], float)
    face = size[k] / 2  # the front face, along `normal` from the center
    lo, hi = center[1] - size[1] / 2, center[1] + size[1] / 2
    depth = size[k]
    signs = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], float)
    for solid in solids:
        rel = np.asarray(solid["center"], float) + (signs * np.abs(np.asarray(solid["size"], float)) / 2) \
            @ unity_yaw_matrix(solid.get("yaw_deg", 0.0)).T - center
        behind = face - rel @ normal
        lateral = rel @ side
        if (lateral.max() <= -size[2 - k] / 2 or lateral.min() >= size[2 - k] / 2
                or rel[:, 1].max() + center[1] <= lo or rel[:, 1].min() + center[1] >= hi):
            continue
        if behind.min() < -0.05 or behind.max() > MAX_TAG_DEPTH_M:
            continue
        depth = max(depth, float(behind.max()))
    if depth == size[k]:
        return region
    out = dict(region)
    grown = size.copy()
    grown[k] = depth
    out["size"] = [float(v) for v in grown]
    out["center"] = [float(v) for v in center + normal * (face - depth / 2)]
    return out


def region_boxes(scene: Dict[str, Any], mesh_to_room: np.ndarray, catalog) -> Tuple[List[Dict[str, Any]], List[str]]:
    """The scene's regions as room Region3D dicts, plus notes on any that were skipped.

    Each box's +Z (its front) points along the region's `front` when given, otherwise the
    box side that faces the camera most. Thin regions are deepened (`deepen_region`).
    """
    transform = np.asarray(mesh_to_room, float)
    cam_center = np.asarray(scene["camera"]["position"], float)
    meds = medication_keys([r["medication_id"] for r in scene.get("regions", []) if r.get("medication_id")], catalog)
    solids = scene.get("solids") or []
    boxes, notes = [], []
    for region in scene.get("regions", []):
        region = deepen_region(region, solids, np.asarray(region.get("front") or
                                                          (cam_center - np.asarray(region["center"], float)), float))
        kind = REGION_TYPES.get(region.get("kind"))
        if kind is None:
            notes.append(f"{region.get('region_id')}: unknown kind {region.get('kind')!r}")
            continue
        med = None
        if kind == "designated_shelf":
            med = meds.get(region.get("medication_id"))
            if med is None:
                notes.append(f"{region['region_id']}: {region.get('medication_id')} isn't in the catalog")
                continue
        center_u = np.asarray(region["center"], float)
        front_u = np.asarray(region.get("front") or (cam_center - center_u), float)
        axes = _box_axes_gltf(region)
        front = (front_u @ MIRROR) * np.array([1.0, 0.0, 1.0])
        size = np.abs(np.asarray(region["size"], float))
        # Candidate fronts: +-X, +-Z of the box; the chosen one becomes the room box's +Z.
        options = [(axes[2], size[0], size[2]), (-axes[2], size[0], size[2]),
                   (axes[0], size[2], size[0]), (-axes[0], size[2], size[0])]
        z_axis, width, depth = max(options, key=lambda o: float(o[0] @ front))
        z_room = transform[:3, :3] @ z_axis
        center = _apply(transform, unity_to_gltf(center_u[None]))[0]
        boxes.append({
            "region_id": region["region_id"],
            "region_type": kind,
            "medication_key": med,
            "box": {"center": [round(float(v), 4) for v in center],
                    "size": [round(float(width), 4), round(float(size[1]), 4), round(float(depth), 4)],
                    "yaw_deg": round(float(np.degrees(np.arctan2(z_room[0], z_room[2]))), 3)},
        })
    return boxes, notes


def correspondences(scene: Dict[str, Any], mesh_to_room: np.ndarray, limit: int = MAX_PAIRS) -> List[Dict[str, Any]]:
    """Exact image/room point pairs for registering the simulator's camera.

    Box corners inside the frame, projected with the Unity camera. Occlusion doesn't matter:
    these are computed, not clicked. Spread out over the image, floor and raised points both.
    """
    cam = camera_in_gltf(scene["camera"])
    boxes = [*scene.get("solids", []), *scene.get("regions", [])]
    pts = np.unique(np.round(np.vstack([box_corners_gltf(b) for b in boxes]), 4), axis=0)
    px, depth = cam.project(pts)
    norm = px / np.array([cam.width, cam.height])
    inside = (depth > 0.1) & np.all((norm > EDGE_MARGIN) & (norm < 1 - EDGE_MARGIN), axis=1)
    pts, norm = pts[inside], norm[inside]
    if len(pts) < 6:
        raise ValueError("Fewer than 6 scene corners are inside the camera frame.")
    # Farthest-point sampling in the image, starting from the lowest point (on the floor).
    # Points closer than MIN_PAIR_GAP_M in the room are skipped (the solver rejects them).
    chosen = [int(np.argmin(pts[:, 1] + norm[:, 0] * 1e-3))]
    dist = np.full(len(pts), np.inf)
    while True:
        last = chosen[-1]
        dist = np.minimum(dist, np.linalg.norm(norm - norm[last], axis=1))
        dist[np.linalg.norm(pts - pts[last], axis=1) < MIN_PAIR_GAP_M] = 0.0
        nxt = int(np.argmax(dist))
        if len(chosen) >= limit or dist[nxt] <= 1e-6:
            break
        chosen.append(nxt)
    transform = np.asarray(mesh_to_room, float)
    room_pts = _apply(transform, pts[chosen])
    return [{"image": [round(float(u), 6), round(float(v), 6)], "room": [round(float(c), 5) for c in p]}
            for (u, v), p in zip(norm[chosen], room_pts)]


def unity_points_to_room(points: np.ndarray, mesh_to_room: np.ndarray) -> np.ndarray:
    """Unity world points (N x 3) in the imported room's frame; used by the N3 evaluator."""
    return _apply(np.asarray(mesh_to_room, float), unity_to_gltf(points))


def _ok(res, what: str):
    if res.status_code >= 400:
        raise RuntimeError(f"{what} failed ({res.status_code}): {res.text[:400]}")
    return res.json()


def import_scene(api, scene: Dict[str, Any], catalog, frame_png: bytes, name: str = "Simulated pharmacy",
                 layout_id: Optional[str] = None, view_name: Optional[str] = None) -> Dict[str, Any]:
    """Create the room, its 3D regions, a camera view with the first rendered frame as its
    photo, and the exact registration, through the dashboard API.

    `api` is an httpx-style client (httpx.Client with a base URL, or FastAPI's TestClient).
    """
    if scene.get("schema") != SCHEMA:
        raise ValueError(f"Expected {SCHEMA}, got {scene.get('schema')!r}")
    room = _ok(api.post("/api/rooms", files={"scan": ("simulation.glb", build_mesh_glb(scene), "model/gltf-binary")},
                        data={"name": name}), "Room import")
    mesh_to_room = np.asarray(room["mesh_to_room"], float)
    boxes, notes = region_boxes(scene, mesh_to_room, catalog)
    room = _ok(api.put(f"/api/rooms/{room['room_id']}/regions", json={"regions": boxes}), "Saving regions")
    if layout_id is None:
        label = view_name or f"Simulation {scene['camera'].get('camera_id', 'camera')}"
        layout_id = _ok(api.post("/api/layouts", json={"name": label}), "New camera view")["layout"]["layout_id"]
    photo = _ok(api.post(f"/api/layouts/{layout_id}/background", files={"image": ("frame.png", frame_png, "image/png")}),
                "Photo upload")
    if (photo["width"], photo["height"]) != (int(scene["camera"]["width"]), int(scene["camera"]["height"])):
        raise ValueError(f"The frame is {photo['width']}x{photo['height']}, the camera renders "
                         f"{scene['camera']['width']}x{scene['camera']['height']}.")
    _ok(api.patch(f"/api/layouts/{layout_id}", json={"background_image": photo["background_image"]}), "Setting the photo")
    pairs = correspondences(scene, mesh_to_room)
    reg = _ok(api.put(f"/api/rooms/{room['room_id']}/cameras/{layout_id}", json={"correspondences": pairs}),
              "Camera registration")
    return {"room_id": room["room_id"], "layout_id": layout_id, "mesh_to_room": mesh_to_room.tolist(),
            "regions": [r["region_id"] for r in room["regions"]], "skipped": notes,
            "rms_px": reg["registration"]["rms_px"], "max_px": reg["registration"]["max_px"],
            "generated_regions": [r["region_id"] for r in reg["layout"]["regions"]]}
