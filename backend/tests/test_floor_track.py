"""Floor position and facing from one camera's skeletons, on synthetic people with known answers.

The synthetic skeleton matches the track's body assumptions exactly, so these errors
are far below what real footage will give; they check the geometry, not real accuracy.
"""

import json

import numpy as np
import pytest

from pharma.services.floor_track import BRIDGE_FRAMES, TRACK_FILE, compute_track, load_or_compute, yaw_error_deg
from pharma.services.recordings import PoseTrack
from tests.room_fixtures import HEIGHT, WIDTH, default_camera, observe, skeleton

FPS = 30


def _track(path_fn, frames=120, hide=(), gaps=(), noise_px=1.0):
    cam = default_camera()
    out, truth = [], []
    for i in range(frames):
        x, z, yaw = path_fn(i)
        truth.append((x, z, yaw % 360))
        kps = None if any(a <= i < b for a, b in gaps) else observe(
            cam, skeleton((x, z), yaw), hide=hide(i) if callable(hide) else hide, noise_px=noise_px,
            rng=np.random.default_rng(i))
        out.append(kps)
    return cam, PoseTrack(FPS, WIDTH, HEIGHT, out), truth


def _errors(result, truth):
    pos, yaw = [], []
    for frame, (x, z, y) in zip(result["frames"], truth):
        if frame:
            pos.append(np.hypot(frame[0] - x, frame[1] - z))
            yaw.append(yaw_error_deg(frame[2], y))
    return np.array(pos), np.array(yaw)


def test_walking_position_and_heading():
    cam, track, truth = _track(lambda i: (-1 + i / 60, 0.5, 90))
    result = compute_track(track, cam)
    pos, yaw = _errors(result, truth)
    assert result["stats"]["observed"] == 120
    assert np.median(pos) < 0.03 and pos.max() < 0.08
    assert np.median(yaw) < 5 and yaw.max() < 15


@pytest.mark.parametrize("facing", [0, 45, 90, 135, 180, 225, 270, 315])
def test_standing_facing_any_direction(facing):
    cam, track, truth = _track(lambda i: (0.2, -0.5, facing), frames=45)
    result = compute_track(track, cam)
    pos, yaw = _errors(result, truth)
    assert np.median(pos) < 0.03
    assert np.median(yaw) < 10, f"facing {facing}: got {result['frames'][20][2]}"


def test_hidden_feet_fall_back_to_hips_at_the_measured_height():
    # Feet visible for the first half, hidden behind the counter for the second.
    cam, track, truth = _track(lambda i: (-1 + i / 60, 0.5, 90), hide=lambda i: (15, 16) if i >= 60 else ())
    result = compute_track(track, cam)
    assert result["hip_height_measured"]
    assert result["hip_height_m"] == pytest.approx(0.95, abs=0.03)
    assert result["stats"]["from_hips"] >= 55
    assert [f[3] for f in result["frames"][70:75]] == ["hips"] * 5
    pos, _ = _errors(result, truth)
    assert np.median(pos[60:]) < 0.05


def test_short_gaps_are_bridged_and_long_ones_left_empty():
    cam, track, _ = _track(lambda i: (-1 + i / 60, 0.5, 90), frames=150, gaps=[(30, 35), (80, 80 + BRIDGE_FRAMES + 5)])
    frames = compute_track(track, cam)["frames"]
    assert all(frames[i] and frames[i][5] for i in range(30, 35))
    assert frames[32][3] == "bridged" and frames[32][4] is None
    assert all(frames[i] is None for i in range(80, 80 + BRIDGE_FRAMES + 5))
    assert frames[79] and frames[80 + BRIDGE_FRAMES + 5]


def test_nobody_in_view_gives_no_positions():
    cam = default_camera()
    result = compute_track(PoseTrack(FPS, WIDTH, HEIGHT, [None] * 30), cam)
    assert result["frames"] == [None] * 30 and result["stats"]["observed"] == 0


def test_a_single_wild_detection_is_dropped():
    cam, track, truth = _track(lambda i: (0.2, -0.5, 180), frames=40)
    wrong = observe(cam, skeleton((1.8, 1.5), 180))
    track.frames[20] = wrong
    result = compute_track(track, cam)
    assert result["stats"]["rejected_jumps"] == 1
    assert np.hypot(result["frames"][20][0] - 0.2, result["frames"][20][1] + 0.5) < 0.05


def test_positions_on_furniture_move_to_the_nearest_free_floor():
    cam, track, _ = _track(lambda i: (0.2, -0.5, 180), frames=20, noise_px=0)
    obstacles = np.zeros((100, 100), dtype=bool)
    obstacles[40:60, 40:60] = True  # a 0.4 m block around (0.2, -0.5) with the origin below
    origin, res = (0.2 - 1.0, -0.5 - 1.0), 0.02
    result = compute_track(track, cam, obstacles, origin, res)
    x, z = result["frames"][10][:2]
    col, row = int((x - origin[0]) / res), int((z - origin[1]) / res)
    assert not obstacles[row, col]
    assert np.hypot(x - 0.2, z + 0.5) == pytest.approx(0.2, abs=0.03)
    assert result["stats"]["moved_off_furniture"] == 20


def test_track_is_cached_until_its_inputs_change(tmp_path):
    from types import SimpleNamespace

    cam, track, _ = _track(lambda i: (0.2, -0.5, 180), frames=20)
    poses = tmp_path / "poses.json"
    track.save(poses, "test")
    room = SimpleNamespace(room_id="r", room_version=1, plan=SimpleNamespace(origin=(0, 0), resolution_m=0.02))
    reg = SimpleNamespace(layout_id="default", revision=1, view_calibration_version=3, frame_size=(WIDTH, HEIGHT),
                          intrinsics=SimpleNamespace(fx=cam.fx, fy=cam.fy, cx=cam.cx, cy=cam.cy),
                          rotation=cam.R.tolist(), translation=cam.t.tolist())
    first = load_or_compute(tmp_path, poses, room, reg, None)
    assert first["camera"]["view_calibration_version"] == 3
    cached = json.loads((tmp_path / TRACK_FILE).read_text())
    cached["frames"][0] = "marker"
    (tmp_path / TRACK_FILE).write_text(json.dumps(cached))
    assert load_or_compute(tmp_path, poses, room, reg, None)["frames"][0] == "marker"
    reg.revision = 2  # re-registered camera: recompute
    assert load_or_compute(tmp_path, poses, room, reg, None)["frames"][0] != "marker"


def test_refuses_a_recording_with_another_frame_shape(tmp_path):
    from types import SimpleNamespace

    poses = tmp_path / "poses.json"
    PoseTrack(FPS, 1080, 1080, [None]).save(poses, "test")
    room = SimpleNamespace(room_id="r", room_version=1)
    reg = SimpleNamespace(layout_id="default", revision=1, frame_size=(WIDTH, HEIGHT))
    with pytest.raises(ValueError, match="Register the camera on a frame from this video"):
        load_or_compute(tmp_path, poses, room, reg, None)
