"""The simulator's scene geometry becomes a room with exact regions and camera (N2)."""

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from pharma.api.main import app
from pharma.db.models import Region3D
from pharma.services.camera_geometry import Camera, box_corners
from pharma.services.sim_scene import (
    camera_in_gltf, deepen_region, import_scene, quaternion_matrix, unity_points_to_room, unity_yaw_matrix,
)
from tests.test_api import make_controller

W, H = 1920, 1080


def _quaternion(m: np.ndarray):
    """Rotation matrix -> (x, y, z, w)."""
    w = np.sqrt(max(0.0, 1 + m[0, 0] + m[1, 1] + m[2, 2])) / 2
    x = np.copysign(np.sqrt(max(0.0, 1 + m[0, 0] - m[1, 1] - m[2, 2])) / 2, m[2, 1] - m[1, 2])
    y = np.copysign(np.sqrt(max(0.0, 1 - m[0, 0] + m[1, 1] - m[2, 2])) / 2, m[0, 2] - m[2, 0])
    z = np.copysign(np.sqrt(max(0.0, 1 - m[0, 0] - m[1, 1] + m[2, 2])) / 2, m[1, 0] - m[0, 1])
    return [float(x), float(y), float(z), float(w)]


def _look_at(eye, target):
    """A Unity camera rotation looking from eye to target (left-handed, Y up)."""
    fwd = np.subtract(target, eye).astype(float)
    fwd /= np.linalg.norm(fwd)
    right = np.cross([0, 1, 0], fwd)
    right /= np.linalg.norm(right)
    up = np.cross(fwd, right)
    return _quaternion(np.column_stack([right, up, fwd]))


def unity_pixels(camera, points):
    """Unity's own projection: WorldToScreenPoint with the image row flipped to top-left."""
    rot = quaternion_matrix(camera["rotation_xyzw"])
    local = (np.asarray(points, float) - camera["position"]) @ rot
    f = (camera["height"] / 2) / np.tan(np.radians(camera["vertical_fov_deg"]) / 2)
    return np.column_stack([camera["width"] / 2 + f * local[:, 0] / local[:, 2],
                            camera["height"] / 2 - f * local[:, 1] / local[:, 2]])


def _scene():
    eye = [7.8, 6.3, -8.8]
    walls = [
        {"name": "wall_s", "center": [3, 1.25, -0.05], "size": [6, 2.5, 0.1], "yaw_deg": 0},
        {"name": "wall_n", "center": [3, 1.25, 5.05], "size": [6, 2.5, 0.1], "yaw_deg": 0},
        {"name": "wall_w", "center": [-0.05, 1.25, 2.5], "size": [0.1, 2.5, 5], "yaw_deg": 0},
        {"name": "wall_e", "center": [6.05, 1.25, 2.5], "size": [0.1, 2.5, 5], "yaw_deg": 0},
    ]
    shelf = {"name": "bank_front", "center": [2.0, 0.9, 4.7], "size": [1.6, 1.8, 0.4], "yaw_deg": 0}
    counter = {"name": "counter", "center": [4.5, 0.475, 2.0], "size": [1.2, 0.95, 0.6], "yaw_deg": 30}
    return {
        "schema": "scene-geometry/1",
        "coordinates": "unity_world",
        "floor_y": 0.0,
        "camera": {"camera_id": "room-camera-01", "calibration_version": "pharmacy-v3", "position": eye,
                   "rotation_xyzw": _look_at(eye, [3, 0.5, 2.5]), "vertical_fov_deg": 43, "width": W, "height": H},
        "regions": [
            {"region_id": "shelf-a", "kind": "shelf", "medication_id": "amoxicillin-500-mg",
             "center": [2.0, 0.65, 4.7], "size": [1.56, 0.5, 0.36], "yaw_deg": 0, "front": [0, 0, -1]},
            {"region_id": "shelf-x", "kind": "shelf", "medication_id": "not-in-catalog",
             "center": [2.0, 1.15, 4.7], "size": [1.56, 0.5, 0.36], "yaw_deg": 0},
            {"region_id": "counter-01", "kind": "counter", "center": counter["center"],
             "size": counter["size"], "yaw_deg": 30},
        ],
        "solids": [*walls, shelf, counter],
    }


@pytest.fixture
def client(tmp_path):
    from pharma.api import routes

    with TestClient(app) as c:
        routes.controller = make_controller(tmp_path)
        yield c


def test_camera_conversion_matches_unity_projection():
    scene = _scene()
    cam = camera_in_gltf(scene["camera"])
    pts = np.array([[1, 0, 1], [5, 2, 4], [3, 0.9, 4.5]], float)
    px, depth = cam.project(pts * [1, 1, -1])
    assert (depth > 0).all()
    assert px == pytest.approx(unity_pixels(scene["camera"], pts), abs=1e-6)
    # Unity's yaw turns +Z toward +X.
    assert unity_yaw_matrix(90) @ [0, 0, 1] == pytest.approx([1, 0, 0])


def test_scene_imports_as_a_room_with_exact_regions_and_camera(client, tmp_path):
    from pharma.api import routes

    ctrl = routes.controller
    scene = _scene()
    ok, png = cv2.imencode(".png", np.full((H, W, 3), 128, np.uint8))
    result = import_scene(client, scene, ctrl.catalog, png.tobytes(), layout_id=None)
    assert result["rms_px"] < 0.05 and result["max_px"] < 0.2
    assert result["skipped"] == ["shelf-x: not-in-catalog isn't in the catalog"]
    assert sorted(result["generated_regions"]) == sorted(result["regions"])

    room = client.get(f"/api/rooms/{result['room_id']}").json()
    reg = next(c for c in room["cameras"] if c["layout_id"] == result["layout_id"])
    from pharma.db.models import CameraRegistration

    registered = Camera.from_registration(CameraRegistration.model_validate(reg))
    to_room = np.array(room["mesh_to_room"])
    assert room["floor_fit"] and abs(room["bounds_min"][1]) < 0.06  # floor slab at y = 0

    # Every region box covers the same points as the Unity box, and projects to the
    # same pixels through the registration as through Unity's own camera.
    for region in scene["regions"][::2]:
        saved = next(Region3D.model_validate(r) for r in room["regions"]
                     if r["region_type"] == {"shelf": "designated_shelf", "counter": "dispensing_counter"}[region["kind"]])
        half = np.asarray(region["size"]) / 2
        signs = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], float)
        corners_u = np.asarray(region["center"]) + (signs * half) @ unity_yaw_matrix(region["yaw_deg"]).T
        expected = unity_points_to_room(corners_u, to_room)
        got = box_corners(saved.box.center, saved.box.size, saved.box.yaw_deg)
        assert np.sort(np.round(got, 3), axis=0) == pytest.approx(np.sort(np.round(expected, 3), axis=0), abs=2e-3)
        px, _ = registered.project(expected)
        assert px == pytest.approx(unity_pixels(scene["camera"], corners_u), abs=0.5)

    # The shelf's front (+Z of its box) faces the aisle: Unity -Z, where the camera is.
    shelf = next(Region3D.model_validate(r) for r in room["regions"] if r["region_type"] == "designated_shelf")
    a = np.radians(shelf.box.yaw_deg)
    front_room = np.array([np.sin(a), 0, np.cos(a)])
    expected_front = unity_points_to_room(np.array([[2.0, 0.65, 3.7]]), to_room)[0] - np.array(shelf.box.center)
    assert front_room @ (expected_front / np.linalg.norm(expected_front)) > 0.99


def test_thin_shelf_regions_are_deepened_over_the_board_behind():
    # The simulator draws a shelf row as a plane on its front; the board behind it is 0.65 m deep.
    plane = {"region_id": "shelf-a", "kind": "shelf", "center": [-1.65, 1.105, 1.08],
             "size": [1.47, 0.57, 0.02], "yaw_deg": 0, "front": [0, 0, -1]}
    solids = [
        {"name": "board", "center": [-1.65, 0.84, 1.38], "size": [1.6, 0.05, 0.65], "yaw_deg": 0},
        {"name": "board above", "center": [-1.65, 2.16, 1.38], "size": [1.6, 0.05, 0.65], "yaw_deg": 0},
        {"name": "next shelf", "center": [1.65, 0.84, 1.38], "size": [1.6, 0.05, 0.65], "yaw_deg": 0},
        {"name": "wall far behind", "center": [0, 1.65, 7.45], "size": [8, 3.3, 0.16], "yaw_deg": 0},
        {"name": "label strip in front", "center": [-1.65, 0.85, 1.03], "size": [1.44, 0.09, 0.02], "yaw_deg": 0},
    ]
    deep = deepen_region(plane, solids, np.array([0, 0, -1.0]))
    assert deep["size"] == pytest.approx([1.47, 0.57, 0.635], abs=1e-6)
    assert deep["center"][2] - deep["size"][2] / 2 == pytest.approx(1.07, abs=1e-6)  # front face unchanged
    assert deep["center"][:2] == pytest.approx([-1.65, 1.105])
    # Regions that already have depth, or have nothing behind them, are left alone.
    assert deepen_region(dict(plane, size=[1.47, 0.57, 0.4]), solids, np.array([0, 0, -1.0]))["size"][2] == 0.4
    assert deepen_region(plane, solids[3:], np.array([0, 0, -1.0])) is plane
