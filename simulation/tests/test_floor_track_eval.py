"""The N3 evaluator puts Unity rig truth in the room frame correctly (evaluator-only)."""
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from evaluate_floor_track import evaluate
from pharma.services.sim_scene import quaternion_matrix, unity_points_to_room, unity_yaw_matrix

CAMERA = {'position': [7.8, 6.3, -8.8], 'vertical_fov_deg': 43, 'width': 1920, 'height': 1080,
          # Looking toward the room center, tilted down (right, up, forward columns).
          'rotation_xyzw': None}


def _camera():
    eye = np.array(CAMERA['position'])
    fwd = np.array([3, 0.5, 2.5]) - eye
    fwd /= np.linalg.norm(fwd)
    right = np.cross([0, 1, 0], fwd); right /= np.linalg.norm(right)
    m = np.column_stack([right, np.cross(fwd, right), fwd])
    w = np.sqrt(1 + np.trace(m)) / 2
    q = [(m[2, 1] - m[1, 2]) / (4 * w), (m[0, 2] - m[2, 0]) / (4 * w), (m[1, 0] - m[0, 1]) / (4 * w), w]
    return {**CAMERA, 'rotation_xyzw': q}


def _rig(camera, body, yaw_deg):
    """Unity joints for a person standing at body (Unity), facing Unity yaw; right = +X local."""
    turn = unity_yaw_matrix(yaw_deg)
    local = np.zeros((16, 3))
    local[:, 1] = 1.0
    local[4] = [0.18, 1.4, 0]; local[7] = [-0.18, 1.4, 0]   # right / left shoulder
    local[10] = [0.12, 0.95, 0]; local[13] = [-0.12, 0.95, 0]  # right / left hip
    world = body + local @ turn.T
    cam_local = (world - camera['position']) @ quaternion_matrix(camera['rotation_xyzw'])
    f = (camera['height'] / 2) / np.tan(np.radians(camera['vertical_fov_deg']) / 2)
    return [{'x': camera['width'] / 2 + f * x / z, 'y': camera['height'] / 2 - f * y / z, 'z': z} for x, y, z in cam_local]


def test_truth_lands_in_the_room_frame():
    camera = _camera()
    a = np.radians(25)
    mesh_to_room = np.array([[np.cos(a), 0, np.sin(a), -3.0], [0, 1, 0, 0.01], [-np.sin(a), 0, np.cos(a), 2.0], [0, 0, 0, 1]])
    bodies = np.array([[1.0 + 0.02 * i, 0.0, 2.0] for i in range(30)])
    yaws = [0.0] * 15 + [90.0] * 15
    states = [{'body_position': dict(zip('xyz', b))} for b in bodies]
    rig = [{'joints_pixels': _rig(camera, b, y)} for b, y in zip(bodies, yaws)]
    room = unity_points_to_room(bodies, mesh_to_room)
    # The track's facing for a Unity yaw: turn Unity forward into the room frame.
    frames = []
    for i, (p, y) in enumerate(zip(room, yaws)):
        fwd_u = unity_yaw_matrix(y) @ [0, 0, 1]
        fwd = unity_points_to_room(np.array([fwd_u]), mesh_to_room)[0] - unity_points_to_room(np.zeros((1, 3)), mesh_to_room)[0]
        yaw = float(np.degrees(np.arctan2(fwd[0], fwd[2]))) % 360
        frames.append(None if i == 0 else [p[0] + 0.10, p[2], yaw, 'ankles' if i < 20 else 'hips', 0.9, False])
    track = {'fps': 30, 'fields': ['x', 'z', 'yaw_deg', 'source', 'conf', 'bridged'], 'frames': frames}
    report = evaluate(track, states, rig, {'camera': camera}, mesh_to_room)
    assert report['placed_fraction'] == round(29 / 30, 4)
    assert abs(report['position_cm']['all']['median'] - 10.0) < 0.01
    assert report['facing_deg']['all']['max'] < 0.01
    assert set(report['position_cm']) == {'all', 'ankles', 'hips'}
