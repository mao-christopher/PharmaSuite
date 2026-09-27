"""2D regions generated from a room's 3D boxes through a camera registration."""

import numpy as np
import pytest

from pharma.db.models import Room
from pharma.services.camera_geometry import box_corners
from pharma.services.inventory_engine import point_in_polygon
from pharma.services.region_projection import regions_for_camera
from tests.room_fixtures import default_camera, look_at_camera


def _room(regions):
    return Room.model_validate({
        "room_id": "r", "created_at": "2026-09-26T00:00:00+00:00", "updated_at": "2026-09-26T00:00:00+00:00",
        "mesh": {"file": "mesh.glb", "sha256": "0" * 64, "bytes": 1, "triangles": 1},
        "mesh_to_room": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
        "bounds_min": [-3, 0, -3], "bounds_max": [3, 2.5, 3],
        "plan": {"image": "plan.png", "obstacles": "obstacles.png", "origin": [0, 0], "resolution_m": 0.02,
                 "width": 1, "height": 1},
        "regions": regions,
    })


def _shelf(key, center, size=(0.8, 0.4, 0.35), yaw=0.0):
    return {"region_id": key, "region_type": "designated_shelf", "medication_key": key,
            "box": {"center": list(center), "size": list(size), "yaw_deg": yaw}}


def _mask(polygon, n=200):
    xs = (np.arange(n) + 0.5) / n
    return np.array([[point_in_polygon(x, y, polygon) for x in xs] for y in xs])


def test_a_box_in_view_covers_its_projection():
    cam = default_camera()
    room = _room([_shelf("A_1MG", (0, 0.9, -2.2))])
    regions, report = regions_for_camera(room, cam)
    assert [r.region_id for r in regions] == ["shelf_a_1mg"] and report[0]["visible"]
    px, _ = cam.project(box_corners((0, 0.9, -2.2), (0.8, 0.4, 0.35), 0))
    center = px.mean(axis=0) / [cam.width, cam.height]
    assert point_in_polygon(*center, regions[0].polygon)
    lo, hi = px.min(axis=0) / [cam.width, cam.height], px.max(axis=0) / [cam.width, cam.height]
    xs, ys = zip(*regions[0].polygon)
    # The outline hugs the projected box: within a couple of raster pixels of its extent.
    assert min(xs) == pytest.approx(lo[0], abs=0.006) and max(xs) == pytest.approx(hi[0], abs=0.006)
    assert min(ys) == pytest.approx(lo[1], abs=0.01) and max(ys) == pytest.approx(hi[1], abs=0.01)


def test_stacked_shelves_split_the_image_without_overlap():
    cam = look_at_camera((0, 2.3, 1.5), (0, 0.9, -2.2))  # looking down at a two-row unit
    room = _room([_shelf("A_1MG", (0, 0.7, -2.2)), _shelf("B_1MG", (0, 1.1, -2.2))])
    regions, _ = regions_for_camera(room, cam)
    assert len(regions) == 2
    a, b = (_mask(r.polygon) for r in regions)
    assert a.sum() > 0 and b.sum() > 0
    assert (a & b).sum() / min(a.sum(), b.sum()) < 0.02  # boundary pixels only


def test_hidden_or_out_of_view_boxes_get_no_region():
    cam = default_camera()
    behind = _shelf("A_1MG", (2.5, 0.9, 2.8))  # behind the camera
    front = _shelf("B_1MG", (0, 0.9, -1.0), size=(1.0, 1.0, 0.4))
    hidden = _shelf("C_1MG", (0, 0.9, -1.5), size=(0.2, 0.2, 0.2))  # directly behind B from this camera
    cam = look_at_camera((0, 0.9, 1.5), (0, 0.9, -2.0))
    regions, report = regions_for_camera(_room([behind, front, hidden]), cam)
    assert [r.region_id for r in regions] == ["shelf_b_1mg"]
    assert [e["visible"] for e in report] == [False, True, False]


def test_boxes_leaving_the_frame_are_clipped_to_it():
    cam = look_at_camera((0, 1.5, 0.5), (0, 1.0, -2.0))
    wide = _shelf("A_1MG", (0, 1.0, -2.0), size=(8.0, 0.5, 0.35))
    regions, report = regions_for_camera(_room([wide]), cam)
    xs = [x for x, _ in regions[0].polygon]
    assert min(xs) == 0.0 and max(xs) == 1.0 and report[0]["touches_edge"]


def test_generation_is_deterministic():
    cam = default_camera()
    room = _room([_shelf("A_1MG", (0, 0.9, -2.2)),
                  {"region_id": "counter", "region_type": "dispensing_counter",
                   "box": {"center": [-1.8, 0.475, 0.5], "size": [0.6, 0.95, 1.2], "yaw_deg": 0}}])
    first, _ = regions_for_camera(room, cam)
    second, _ = regions_for_camera(room, cam)
    assert [r.model_dump() for r in first] == [r.model_dump() for r in second]
