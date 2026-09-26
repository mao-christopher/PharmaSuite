"""Tests for recording uploads: timestamp parsing, pose tracks, and the replay clock."""

import shutil
from pathlib import Path

import pytest

from pharma.api.replay_stream import ReplayController
from pharma.services.recordings import PoseTrack, parse_events_file

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


def test_pose_track_hands_fall_back_to_nearby_frame():
    empty = [[0.0, 0.0, 0.0]] * 17
    visible = [[0.0, 0.0, 0.0]] * 17
    visible = visible[:9] + [[0.3, 0.4, 0.9], [0.6, 0.5, 0.2]] + visible[11:]
    track = PoseTrack(fps=10, width=100, height=100, frames=[empty, None, visible, empty])
    # 100 ms = frame 1 (no person); nearest frame with a confident wrist is frame 2.
    hands = track.hands_at(100, min_conf=0.35)
    assert hands == [(0.3, 0.4, 0.9), (0.6, 0.5, 0.2)]


@pytest.fixture
def controller(tmp_path):
    shutil.copytree(DATA / "scenarios", tmp_path / "scenarios")
    shutil.copytree(DATA / "layouts", tmp_path / "layouts")
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


def test_restart_and_seek_rebuild_from_seed(controller):
    ctrl = controller
    ctrl.seek(7000)
    assert amox(ctrl).counter_bottles == 1
    ctrl.seek(7000)  # repeating must not double-apply
    assert amox(ctrl).counter_bottles == 1 and amox(ctrl).shelf_counts["shelf_amoxicillin_500mg"] == 4
    ctrl.seek(2000)
    assert amox(ctrl).counter_bottles == 0 and amox(ctrl).held_bottles == 1
    ctrl.restart()
    assert amox(ctrl).held_bottles == 0 and ctrl.current_media_time_ms == 0


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
