"""Tests for recording uploads: timestamp parsing, pose tracks, and the replay clock."""

import shutil
from pathlib import Path

import pytest

from pharma.api.replay_stream import ReplayController
from pharma.services.recordings import PoseTrack, locate_hands, parse_events_file

DATA = Path(__file__).resolve().parents[1] / "data"


def test_parse_csv_with_header_and_aliases():
    events = parse_events_file("time_s,event\n1.5,grab\n4,drop\n6.25,pickup\n9,release\n")
    assert [(e["media_time_ms"], e["event_type"], e["session_id"]) for e in events] == [
        (1500, "pickup", "sess_001"),
        (4000, "release", "sess_001"),
        (6250, "pickup", "sess_002"),
        (9000, "release", "sess_002"),
    ]
    assert len({e["event_id"] for e in events}) == 4
    assert all("shelf" not in str(e).lower() for e in events)  # no location answers in sensor data


def test_parse_headerless_csv_is_milliseconds_and_sorted():
    events = parse_events_file("5000,release\n1000,pickup\n")
    assert [(e["media_time_ms"], e["event_type"]) for e in events] == [(1000, "pickup"), (5000, "release")]
    assert events[0]["session_id"] == events[1]["session_id"]


def test_parse_json_array_and_jsonl():
    arr = parse_events_file('[{"media_time_ms": 100, "event_type": "pickup"}, {"media_time_ms": 900, "event_type": "release"}]')
    jsonl = parse_events_file('{"media_time_ms": 100, "event_type": "pickup"}\n{"media_time_ms": 900, "event_type": "release"}\n')
    assert [e["media_time_ms"] for e in arr] == [e["media_time_ms"] for e in jsonl] == [100, 900]


def test_release_without_pickup_gets_its_own_session():
    events = parse_events_file("1000,release\n2000,pickup\n3000,release\n")
    assert events[0]["session_id"] != events[1]["session_id"] == events[2]["session_id"]


@pytest.mark.parametrize(
    "content, message",
    [
        ("", "empty"),
        ("1000,jump\n", "unknown event type"),
        ("-5,pickup\n", "negative"),
        ("99999,pickup\n", "past the end"),
        ("[]", "no events"),
    ],
)
def test_parse_rejects_bad_files(content, message):
    with pytest.raises(ValueError, match=message):
        parse_events_file(content, duration_ms=10000)


def test_pose_track_hands_search_a_few_frames_either_way():
    empty = [[0.0, 0.0, 0.0]] * 17
    visible = [[0.0, 0.0, 0.0]] * 17
    visible = visible[:9] + [[0.3, 0.4, 0.9], [0.6, 0.5, 0.2]] + visible[11:]
    track = PoseTrack(fps=10, width=100, height=100, frames=[empty, None, visible, empty])
    wrists = [(0.3, 0.4, 0.9), (0.6, 0.5, 0.2)]
    # 100 ms = frame 1 (no person); the wrist a frame later counts as being at the signal.
    assert track.hand_points_at(100, min_conf=0.35) == (wrists, "wrist")
    assert locate_hands([(None, track)], 100, 0.35).offset_ms == 100.0
    # 300 ms = frame 3; the wrist one frame earlier.
    assert track.hand_points_at(300, min_conf=0.35) == (wrists, "wrist")
    assert locate_hands([(None, track)], 300, 0.35).offset_ms == -100.0


@pytest.fixture
def controller(tmp_path):
    from tests.conftest import FIXTURE_LAYOUTS

    skip = shutil.ignore_patterns("upload-*", "video.*", "poses.json", "thumb.jpg")
    shutil.copytree(DATA / "scenarios", tmp_path / "scenarios", ignore=skip)
    shutil.copytree(FIXTURE_LAYOUTS, tmp_path / "layouts")
    shutil.copy(FIXTURE_LAYOUTS.parent / "catalog.json", tmp_path / "catalog.json")
    ctrl = ReplayController(scenarios_dir=tmp_path / "scenarios")
    ctrl.load_scenario("demo_scenario_01")
    return ctrl


def amox(ctrl):
    return ctrl.engine.inventory["AMOXICILLIN_500MG"]


def test_clock_fires_each_event_exactly_once_and_stops_at_end(controller):
    ctrl = controller
    ctrl.play()
    start = ctrl._last_tick
    # Many small ticks (like a real 60 Hz loop) across the pickup at 1.0 s.
    for i in range(1, 91):
        ctrl.tick(start + i * (1 / 60))
    assert 1400 < ctrl.current_media_time_ms < 1600
    assert amox(ctrl).held_bottles == 1 and amox(ctrl).shelf_counts["shelf_amoxicillin_500mg"] == 4

    ctrl.tick(start + 60)  # far past the end
    assert ctrl.current_media_time_ms == ctrl.duration_ms and ctrl.is_playing is False
    assert amox(ctrl).counter_bottles == 1 and amox(ctrl).held_bottles == 0
    assert amox(ctrl).shelf_counts["shelf_amoxicillin_500mg"] == 4
    assert ctrl.processed_event_ids == {"evt_001", "evt_002"}


def test_ticks_while_paused_do_nothing(controller):
    ctrl = controller
    ctrl.tick(10_000.0)
    assert ctrl.current_media_time_ms == 0 and not ctrl.processed_event_ids


def test_seek_and_restart_never_reapply_or_undo_signals(controller):
    ctrl = controller
    ctrl.seek(7000)
    assert amox(ctrl).counter_bottles == 1
    ctrl.seek(7000)  # repeating must not double-apply
    assert amox(ctrl).counter_bottles == 1 and amox(ctrl).shelf_counts["shelf_amoxicillin_500mg"] == 4
    ctrl.seek(2000)  # going back replays the video only; inventory keeps what already happened
    assert amox(ctrl).counter_bottles == 1 and amox(ctrl).held_bottles == 0
    ctrl.restart()
    ctrl.seek(ctrl.duration_ms)
    assert amox(ctrl).counter_bottles == 1 and ctrl.current_media_time_ms == ctrl.duration_ms
    assert len(ctrl.activity) == 2


def test_live_state_survives_server_restart_without_reapplying(controller, tmp_path):
    controller.seek(7000)
    assert amox(controller).counter_bottles == 1

    restarted = ReplayController(scenarios_dir=tmp_path / "scenarios")
    restarted.restore_player()
    assert restarted.current_scenario_name == "demo_scenario_01"
    assert amox(restarted).counter_bottles == 1 and amox(restarted).shelf_counts["shelf_amoxicillin_500mg"] == 4
    restarted.seek(7000)
    restarted.restart()
    restarted.seek(restarted.duration_ms)
    assert amox(restarted).counter_bottles == 1
    assert len([a for a in restarted.engine.alerts.values() if a.alert_type == "expiry"]) == 1


def test_inventory_carries_across_recordings_with_separate_sessions(controller, tmp_path):
    shutil.copytree(tmp_path / "scenarios" / "demo_scenario_01", tmp_path / "scenarios" / "demo_copy")
    ctrl = controller
    ctrl.seek(ctrl.duration_ms)
    ctrl.load_scenario("demo_copy")
    assert not ctrl.processed_event_ids  # same event IDs, different recording
    ctrl.seek(ctrl.duration_ms)
    assert amox(ctrl).counter_bottles == 2 and amox(ctrl).shelf_counts["shelf_amoxicillin_500mg"] == 3
    assert {"demo_scenario_01:sess_001", "demo_copy:sess_001"} <= set(ctrl.engine.sessions)
    assert ctrl.apply_recording("demo_scenario_01") == 0


def test_engine_state_round_trips_with_aliased_sessions(controller):
    from pharma.services.inventory_engine import InventoryEngine

    ctrl = controller
    ctrl.seek(ctrl.duration_ms)  # bottle parked at the counter
    engine = ctrl.engine
    engine.handle_pickup("again", [(0.6, 0.2, 0.9)], 0)  # picks the parked bottle back up
    assert engine.sessions["again"] is engine.sessions["demo_scenario_01:sess_001"]

    clone = InventoryEngine.from_dict(engine.to_dict(), regions=ctrl.regions)
    assert clone.sessions["again"] is clone.sessions["demo_scenario_01:sess_001"]
    clone.handle_release("again", [(0.25, 0.25, 0.9)], 0)
    assert clone.sessions["demo_scenario_01:sess_001"].state == "ON_DESIGNATED_SHELF"
    assert clone.inventory["AMOXICILLIN_500MG"].shelf_counts["shelf_amoxicillin_500mg"] == 5
    assert clone._alert_counter == engine._alert_counter


def test_old_shelf_ids_are_rekeyed_to_canonical(controller):
    ctrl = controller
    amox(ctrl).shelf_counts = {"shelf_01": 5}  # counts saved before shelf IDs were canonical
    notes = ctrl.store.sync_catalog(ctrl.catalog)
    assert amox(ctrl).shelf_counts == {"shelf_amoxicillin_500mg": 5}
    assert any("shelf_01" in n for n in notes)
    ctrl.seek(2000)
    assert amox(ctrl).shelf_counts == {"shelf_amoxicillin_500mg": 4} and amox(ctrl).held_bottles == 1


def test_stream_rendering_does_not_advance_the_clock(controller):
    ctrl = controller
    ctrl.play()
    stream = ctrl.mjpeg_generator()
    for _ in range(3):
        next(stream)
    stream.close()
    assert ctrl.current_media_time_ms == 0 and not ctrl.processed_event_ids


def test_activity_log_records_each_decision(controller):
    ctrl = controller
    ctrl.seek(ctrl.duration_ms)
    assert [(a["event_type"], a["nearest_region_id"], a["state"]) for a in ctrl.activity] == [
        ("pickup", "shelf_amoxicillin_500mg", "HELD"),
        ("release", "counter_dispensing_01", "AT_COUNTER"),
    ]
    assert all(a["distance"] == 0 and a["hands_seen"] == 1 for a in ctrl.activity)


def test_hand_falls_back_to_elbow_then_nearest_wrist_then_nothing():
    hidden = [0.0, 0.0, 0.0]
    frame = [hidden] * 17
    with_wrist = frame[:10] + [[0.6, 0.6, 0.9]] + frame[11:]
    with_elbow = frame[:7] + [[0.4, 0.5, 0.8]] + frame[8:]
    with_shoulder = frame[:6] + [[0.7, 0.3, 0.9]] + frame[7:]
    # 10 fps: a wrist at 0 s, nothing until an elbow at 2 s, then only a shoulder.
    frames = [with_wrist] + [frame] * 19 + [with_elbow] + [with_shoulder] * 20
    track = PoseTrack(fps=10, width=100, height=100, frames=frames)
    assert track.hand_points_at(2000, 0.35) == ([(0.4, 0.5, 0.8), (0.0, 0.0, 0.0)], "elbow")
    # An elbow three frames away (either side) beats a wrist further off.
    assert track.hand_points_at(2300, 0.35)[1] == "elbow"
    assert track.hand_points_at(1700, 0.35)[1] == "elbow"
    # Nothing within 5 frames: the wrist seen 0.8 s earlier.
    fix = locate_hands([(None, track)], 800, 0.35)
    assert (fix.joint, fix.offset_ms, fix.points[1]) == ("last_seen_wrist", -800.0, (0.6, 0.6, 0.9))
    # More than a second from any wrist: the employee confirms.
    assert track.hand_points_at(1200, 0.35)[1] is None
    # Shoulders are not used.
    assert track.hand_points_at(3500, 0.35)[1] is None
    assert PoseTrack(fps=10, width=100, height=100, frames=[with_shoulder]).hand_points_at(0, 0.35)[1] is None
    # The wrist's next known position counts too, up to a second after the signal.
    later = PoseTrack(fps=10, width=100, height=100, frames=[frame] * 8 + [with_wrist] + [frame] * 10)
    fix = locate_hands([(None, later)], 0, 0.35)
    assert (fix.joint, fix.offset_ms) == ("next_seen_wrist", 800.0)
    far = PoseTrack(fps=10, width=100, height=100, frames=[frame] * 12 + [with_wrist])
    assert locate_hands([(None, far)], 0, 0.35).joint is None


def test_no_person_at_the_start_reports_nothing_without_error():
    empty = PoseTrack(fps=10, width=100, height=100, frames=[None] * 5)
    assert empty.hand_points_at(0, 0.35) == ([], None)
    assert empty.hand_points_at(400, 0.35) == ([], None)
    assert locate_hands([], 0, 0.35).joint is None
    assert locate_hands([(None, PoseTrack(10, 100, 100, []))], 0, 0.35).points == []
