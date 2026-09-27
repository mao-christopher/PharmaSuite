"""The re-enactment planner (M7/M8): dashboard timeline in, Unity plan out."""
import copy
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import timeline_fixture as tf
from reenact_plan import (
    BLEND_LIMIT_M, FLAG_CUT, FLAG_IN_VIEW, FLAG_VISIBLE, STEP_MAX_M, PlanError, box_corners, build_plan, cutaway,
    project_registration, unity_json,
)


def _inside(point_room, box, margin=1e-6):
    """Is a room point inside a room box (any yaw)?"""
    a = math.radians(box['yaw_deg'])
    dx, dz = point_room[0] - box['center'][0], point_room[2] - box['center'][2]
    lx, lz = dx * math.cos(a) - dz * math.sin(a), dx * math.sin(a) + dz * math.cos(a)
    return (abs(lx) <= box['size'][0] / 2 + margin and abs(lz) <= box['size'][2] / 2 + margin
            and abs(point_room[1] - box['center'][1]) <= box['size'][1] / 2 + margin)


def _unity_pixel(camera, p):
    """Unity's WorldToScreenPoint for the plan's camera, rows from the top."""
    x, y, z, w = camera['rotation_xyzw']
    rot = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
           [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
           [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    d = [p[i] - camera['position'][i] for i in range(3)]
    local = [sum(rot[r][c] * d[r] for r in range(3)) for c in range(3)]
    f = (camera['height'] / 2) / math.tan(math.radians(camera['vertical_fov_deg']) / 2)
    return [camera['width'] / 2 + f * local[0] / local[2], camera['height'] / 2 - f * local[1] / local[2]]


def test_camera_matches_the_registration():
    plan = build_plan(tf.timeline())
    assert plan['alignment_points']
    for point in plan['alignment_points']:
        u, v = _unity_pixel(plan['camera'], point['unity'])
        assert abs(u - point['pixel'][0]) < 1e-6 and abs(v - point['pixel'][1]) < 1e-6
    assert plan['camera']['lens_shift'] == [0.0, 0.0]


def test_bottles_start_on_their_shelves():
    timeline = tf.timeline()
    plan = build_plan(timeline)
    regions = {r['region_id']: r for r in timeline['room']['regions']}
    shown = [b for b in plan['bottles'] if not b['hidden']]
    by_med = {}
    for b in shown:
        by_med[b['medication_key']] = by_med.get(b['medication_key'], 0) + 1
        room = [b['position'][0], b['position'][1], -b['position'][2]]
        shelf = 'shelf_amoxicillin_500mg' if b['medication_key'] == 'AMOXICILLIN_500MG' else 'shelf_ibuprofen_200mg'
        assert _inside(room, regions[shelf]['box'])
        assert b['label'] in ('Amoxicillin 500mg', 'Ibuprofen 200mg')
    assert by_med == {'AMOXICILLIN_500MG': 3, 'IBUPROFEN_200MG': 4}
    assert plan['report']['bottles_without_region'] == 0


def test_actions_keep_the_dashboard_decisions():
    plan = build_plan(tf.timeline())
    acts = {a['action_id']: a for a in plan['actions']}
    assert acts['evt_002']['look'] == 'counter' and acts['evt_004']['look'] == 'misplaced'
    assert acts['evt_005']['look'] == 'disposed' and acts['evt_005']['drop'] is True
    # Pending stays pending; a signal the inventory never applied shows nothing.
    assert acts['evt_006']['pending'] is True and acts['evt_006']['look'] == 'pending'
    assert acts['evt_007']['mode'] == 'none' and acts['evt_007']['bottle'] == -1
    # The same bottle (movement session) is carried from shelf to counter and back out.
    assert len({acts[k]['bottle'] for k in ('evt_001', 'evt_002', 'evt_003', 'evt_004')}) == 1
    # A put-down with no pickup since (evt_005 follows another put-down) gets a hidden bottle.
    assert acts['evt_005']['bottle'] != acts['evt_004']['bottle'] and plan['report']['spawned_bottles'] == 1
    assert plan['report']['pending'] == 1 and plan['report']['not_applied'] == 1


def test_reach_steps_stay_within_the_limit():
    timeline = tf.timeline()
    plan = build_plan(timeline)
    frames = timeline['track']['frames']
    for a in plan['actions']:
        if a['mode'] != 'reach':
            continue
        row = frames[a['contact_frame']]
        sx, sz = a['stand'][0], -a['stand'][1]
        assert math.hypot(sx - row[0], sz - row[1]) <= STEP_MAX_M + 1e-6, a['action_id']
        assert a['reach_start_frame'] == a['contact_frame'] - 18  # 0.6 s at 30 fps


def test_out_of_view_holds_then_blends_or_cuts():
    timeline = tf.timeline()
    plan = build_plan(timeline)
    frames = plan['frames']
    gap = [f for f, row in enumerate(timeline['track']['frames']) if row is None]
    first, after = gap[0], gap[-1] + 1
    held = frames[first - 1][:3]
    for f in gap:
        assert frames[f][:3] == held and frames[f][3] & FLAG_VISIBLE and not frames[f][3] & FLAG_IN_VIEW
    # The fixture's technician walks 1.2 m while hidden: that's a cut.
    reappear = timeline['track']['frames'][after]
    assert math.hypot(reappear[0] - held[0], reappear[1] + held[1]) >= BLEND_LIMIT_M
    assert frames[after][3] & FLAG_CUT and plan['report']['blends'] == 0

    # A short gap (a few centimetres of travel) blends instead.
    short = tf.timeline()
    short['track']['frames'] = tf.track(gap=(12.2, 12.3))
    blended = build_plan(short)
    assert blended['report']['blends'] == 1 and blended['report']['cuts'] == 1  # the only cut is the first sighting

    # A long jump while hidden is a cut, not a slide across the room.
    far = copy.deepcopy(timeline)
    rows = far['track']['frames']
    for f in range(after, len(rows)):
        if rows[f] is not None:
            rows[f] = [-2.2, -1.2, 90.0, 'ankles', .9, False]
    jumped = build_plan(far)['frames']
    assert jumped[after][3] & FLAG_CUT


def test_track_inside_furniture_is_pushed_out():
    timeline = tf.timeline()
    rows = timeline['track']['frames']
    counter = next(o for o in timeline['rebuilt']['objects'] if o['id'] == 'counter_01')['box']
    rows[10] = [counter['center'][0], counter['center'][2], 0.0, 'hips', .8, False]
    plan = build_plan(timeline)
    x, z = plan['frames'][10][0], -plan['frames'][10][1]
    assert not _inside([x, counter['center'][1], z], counter)
    assert plan['report']['pushed_frames'] >= 1


def test_plan_is_deterministic_and_unity_readable():
    a, b = build_plan(tf.timeline()), build_plan(tf.timeline())
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    body = json.loads(unity_json(a))
    assert len(body['frames_flat']) == 4 * a['frame_count'] and 'null' not in unity_json(a)
    assert a['frame_count'] == len(tf.timeline()['track']['frames'])


def test_unusable_timelines_are_refused():
    bad = tf.timeline()
    bad['schema'] = 'floor-track/1'
    try:
        build_plan(bad)
        raise AssertionError('accepted a non-timeline')
    except PlanError:
        pass
    unregistered = tf.timeline(camera=dict(tf.CAMERA, registered=False))
    try:
        build_plan(unregistered)
        raise AssertionError('accepted an unregistered camera')
    except PlanError as e:
        assert 'not registered' in str(e)


def test_region_corners_project_in_front_of_the_camera():
    timeline = tf.timeline()
    cam = timeline['cameras'][0]
    shelf = timeline['room']['regions'][0]
    pts = [project_registration(cam, timeline['frame_size'], c) for c in box_corners(shelf['box'])]
    assert all(p is not None and p[2] > 0 for p in pts)


def test_walls_between_the_camera_and_the_room_are_cut_away():
    wall = {'center': [0.0, 1.5, 5.0], 'size': [10.0, 3.0, 0.1], 'yaw_deg': 0.0}  # thin along room Z
    assert cutaway(wall, [2.0, 6.0, 9.0], (0.0, 0.0))  # camera beyond the wall: cut away
    assert not cutaway(wall, [2.0, 2.0, 1.0], (0.0, 0.0))  # camera inside the room: drawn
    turned = dict(wall, center=[4.0, 1.5, 0.0], yaw_deg=90.0)  # thin along room X
    assert cutaway(turned, [8.0, 6.0, 0.0], (0.0, 0.0)) and not cutaway(turned, [-8.0, 6.0, 0.0], (0.0, 0.0))

    # The planner marks them; walls stay colliders, so walking is still blocked.
    timeline = tf.timeline()
    plan = build_plan(timeline)
    walls = [b for b in plan['boxes'] if b['name'].startswith('wall')]
    assert walls and all(b['collider'] == 'solid' for b in walls)
    assert plan['report']['cutaway_walls'] == sum(b['cutaway'] for b in walls)
    assert all(not b['cutaway'] for b in plan['boxes'] if not b['name'].startswith('wall'))


def test_a_misplaced_bottle_picked_up_again_is_the_same_bottle():
    # A later movement session lifts the misplaced ibuprofen bottle off the amoxicillin shelf:
    # it is that bottle (keeping its identity), not one of the shelf's own amoxicillin bottles.
    timeline = tf.timeline()
    acts = timeline['actions']
    again = dict(acts[3], action_id='evt_004b', type='pickup', contact_ms=17000, bottle_id='rec:sess_009',
                 outcome='in_hand')
    timeline['actions'] = acts[:4] + [again] + [dict(acts[4], bottle_id='rec:sess_009')] + acts[5:]
    plan = build_plan(timeline)
    by_id = {a['action_id']: a for a in plan['actions']}
    moved = by_id['evt_004']['bottle']
    assert by_id['evt_004b']['bottle'] == moved and by_id['evt_005']['bottle'] == moved
    assert plan['bottles'][moved]['medication_key'] == 'IBUPROFEN_200MG'
    assert plan['report']['spawned_bottles'] == 0
