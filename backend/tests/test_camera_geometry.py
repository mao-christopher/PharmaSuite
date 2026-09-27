"""Registering a camera from clicked point pairs, and casting rays back into the room."""

import numpy as np
import pytest

from pharma.services.camera_geometry import Camera, box_corners, solve_registration
from tests.room_fixtures import HEIGHT, WIDTH, default_camera, look_at_camera

FLOOR_AND_SHELF = np.array([
    [-2.5, 0, -2.0], [2.5, 0, -2.0], [2.5, 0, 2.0], [-2.5, 0, 1.5], [0, 0, 0],
    [-0.8, 1.8, -2.0], [0.8, 0.0, -2.0], [-1.5, 0.95, 0.2],
])


def _clicks(cam: Camera, room_pts: np.ndarray, noise_px: float = 0.0, seed: int = 0) -> np.ndarray:
    px, _ = cam.project(room_pts)
    px = px + np.random.default_rng(seed).normal(0, noise_px, px.shape)
    return px / np.array([cam.width, cam.height])


def test_recovers_lens_and_position_from_exact_clicks():
    cam = default_camera()
    sol = solve_registration(_clicks(cam, FLOOR_AND_SHELF), FLOOR_AND_SHELF, WIDTH, HEIGHT)
    assert sol["intrinsics"]["fx"] == pytest.approx(cam.fx, rel=1e-3)
    assert np.allclose(sol["position"], cam.center, atol=1e-3)
    assert sol["rms_px"] < 0.01
    assert sol["quality"] == "good" and sol["note"] is None
    assert sol["height_m"] == pytest.approx(2.4, abs=1e-3)


@pytest.mark.parametrize("f", [500.0, 900.0, 1600.0])
def test_noisy_clicks_still_land_close(f):
    cam = look_at_camera((2.2, 2.4, 2.2), (-0.2, 0.7, -0.8), f=f)
    sol = solve_registration(_clicks(cam, FLOOR_AND_SHELF, noise_px=1.0, seed=int(f)), FLOOR_AND_SHELF, WIDTH, HEIGHT)
    assert sol["intrinsics"]["fx"] == pytest.approx(f, rel=0.05)
    assert np.linalg.norm(np.array(sol["position"]) - cam.center) < 0.15
    assert sol["rms_px"] < 2.0
    assert len(sol["errors_px"]) == len(FLOOR_AND_SHELF)


def test_flags_points_that_are_all_on_the_floor():
    cam = default_camera()
    floor = np.array([[-2.5, 0, -2.0], [2.5, 0, -2.0], [2.5, 0, 2.0], [-2.5, 0, 1.5], [0, 0, 0], [1, 0, -1]])
    sol = solve_registration(_clicks(cam, floor), floor, WIDTH, HEIGHT)
    assert "one flat surface" in sol["note"]
    assert np.allclose(sol["position"], cam.center, atol=0.01)


def test_a_wrong_pair_shows_up_as_the_largest_error():
    cam = default_camera()
    clicks = _clicks(cam, FLOOR_AND_SHELF)
    clicks[3] += (0.05, 0.0)  # one pair mismatched by 64 px
    sol = solve_registration(clicks, FLOOR_AND_SHELF, WIDTH, HEIGHT)
    assert int(np.argmax(sol["errors_px"])) == 3
    assert sol["quality"] != "good"


@pytest.mark.parametrize("room_pts,message", [
    (FLOOR_AND_SHELF[:5], "at least 6"),
    (np.array([[x, 0, 0] for x in range(6)], dtype=float), "one line"),
    (np.vstack([FLOOR_AND_SHELF[:5], FLOOR_AND_SHELF[4:5] + 0.001]), "same spot"),
])
def test_rejects_unsolvable_layouts(room_pts, message):
    with pytest.raises(ValueError, match=message):
        solve_registration(np.full((len(room_pts), 2), 0.5), room_pts, WIDTH, HEIGHT)


def test_rays_hit_the_heights_they_came_from():
    cam = default_camera()
    pts = np.array([[0.3, 0.08, -0.5], [-1.2, 0.95, 1.0], [1.5, 1.4, 0.2]])
    px, _ = cam.project(pts)
    for p, (u, v) in zip(pts, px):
        hit = cam.hit_height(np.array([[u / WIDTH, v / HEIGHT]]), p[1])[0]
        assert np.allclose(hit, p, atol=1e-9)


def test_rays_that_never_reach_a_height_are_nan():
    cam = default_camera()
    # Looking down from 2.4 m: a ray through the image center never climbs to 3 m.
    assert np.isnan(cam.hit_height(np.array([[0.5, 0.5]]), 3.0)).all()


def test_box_corners_follow_yaw():
    corners = box_corners((0, 1, 0), (2, 1, 0.5), 90)
    # A 90° yaw turns the box's width (its X) onto -Z/+Z.
    assert np.allclose(sorted(set(np.round(corners[:, 2], 6))), [-1, 1])
    assert np.allclose(sorted(set(np.round(corners[:, 0], 6))), [-0.25, 0.25])
