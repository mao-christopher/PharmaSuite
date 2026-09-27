"""Live wristband events: each accepted event becomes a clip recording, analyzed once.

Pose is mocked; clips are real encoded MP4s. Regions come from tests/fixtures/layouts/default:
amoxicillin shelf around (.25, .25), ibuprofen shelf around (.25, .65), counter around (.65, .25).
"""

import io
import json
import uuid

import cv2
import numpy as np
import pytest

from pharma.services import live_capture
from tests.test_api import client, make_controller  # noqa: F401  (fixture)

AMOX = "AMOXICILLIN_500MG"
AMOX_SHELF = "shelf_amoxicillin_500mg"
COUNTER = "counter_dispensing_01"
NOTIFY_MS = 10_000
# 4 s before to 1 s after the notification at 10 fps.
WINDOW = list(range(NOTIFY_MS - live_capture.PRE_ROLL_MS, NOTIFY_MS + live_capture.POST_ROLL_MS + 1, 100))


@pytest.fixture
def wrist(monkeypatch):
    """Where the fake pose puts the right wrist, and how many clips pose ran on."""
    import pharma.pose

    spot = {"x": .25, "y": .25, "people": 1, "calls": 0}

    def fake_pose(path, count, **kwargs):
        assert kwargs["require_single_person"] is True
        spot["calls"] += 1
        kwargs["person_counts"].extend([spot["people"]] * count)
        joints = [[0, 0, 0] for _ in range(17)]
        joints[10] = [spot["x"], spot["y"], .9]
        return [joints for _ in range(count)]

    monkeypatch.setattr(pharma.pose, "extract_video_keypoints", fake_pose)
    return spot


def jpeg(width=160, height=90):
    ok, data = cv2.imencode(".jpg", np.zeros((height, width, 3), dtype=np.uint8))
    assert ok
    return data.tobytes()


def send(client, code, event_id=None, times=WINDOW, calibration_version=1, source="band", capture_id=None):
    event_id = event_id or str(uuid.uuid4())
    metadata = {"event_id": event_id, "capture_id": capture_id or CAPTURE, "live_session_id": CAPTURE,
                "code": code, "band_id": "01", "wrist": "right", "layout_id": "default",
                "calibration_version": calibration_version, "notification_ms": NOTIFY_MS,
                "notification_epoch_ms": 1_800_000_000_000, "frame_times_ms": list(times), "source": source}
    frame = jpeg()
    files = [("frames", (f"{i}.jpg", io.BytesIO(frame), "image/jpeg")) for i in range(len(times))]
    return client.post("/api/live/events", data={"metadata": json.dumps(metadata)}, files=files or None)


CAPTURE = str(uuid.uuid4())


def state(client):
    return client.get("/api/inventory").json()


def amox(client):
    return state(client)["inventory"][AMOX]


def test_pickup_and_counter_putdown_share_one_movement_across_clips(client, wrist, tmp_path):
    before = amox(client)
    first_id = str(uuid.uuid4())
    picked = send(client, "P", first_id)
    assert picked.status_code == 200, picked.text
    body = picked.json()
    assert body["status"] == "applied" and body["region_id"] == AMOX_SHELF
    assert body["movement_id"] == first_id

    # The clip is an ordinary, ready recording in the library.
    recording = body["recording"]
    listed = {r["name"]: r for r in client.get("/api/recordings").json()["recordings"]}
    assert listed[recording]["source"] == "live" and listed[recording]["status"] == "ready"
    assert listed[recording]["live"]["movement_id"] == first_id
    assert "upload_index" not in listed[recording]
    video = client.get(f"/api/recordings/{recording}/video")
    assert video.status_code == 200 and video.headers["content-type"] == "video/mp4"
    assert client.get(f"/api/recordings/{recording}/thumbnail").status_code == 200

    held = amox(client)
    assert held["shelf_counts"][AMOX_SHELF] == before["shelf_counts"][AMOX_SHELF] - 1
    assert held["total_bottles"] == before["total_bottles"]
    assert state(client)["live"]["held_movement_id"] == first_id

    # Duplicate delivery and a restart do not repeat the pickup.
    assert send(client, "P", first_id).json()["status"] == "duplicate"
    from pharma.api import routes
    routes.controller = make_controller(tmp_path)
    assert send(client, "P", first_id).json()["status"] == "duplicate"

    wrist["x"] = .65
    placed = send(client, "D")
    assert placed.status_code == 200, placed.text
    assert placed.json()["region_id"] == COUNTER
    assert placed.json()["movement_id"] == first_id  # same bottle as the pickup clip
    after = amox(client)
    assert after["counter_bottles"] == before["counter_bottles"] + 1
    assert after["total_bottles"] == before["total_bottles"]
    live = state(client)["live"]
    assert live["held_movement_id"] is None
    movement = live["movements"][0]
    assert movement["movement_id"] == first_id and movement["state"] == "AT_COUNTER"
    assert movement["medication_key"] == AMOX and movement["original_shelf_id"] == AMOX_SHELF
    assert movement["pickup"]["region_id"] == AMOX_SHELF and movement["release"]["region_id"] == COUNTER
    assert movement["pickup"]["recording"] and movement["release"]["recording"]


def test_out_of_sequence_events_are_ignored_without_footage(client, wrist, tmp_path):
    before = amox(client)
    lonely = send(client, "D")
    assert lonely.json() == {"status": "ignored", "event_id": lonely.json()["event_id"],
                             "reason": "release_without_pickup"}
    assert wrist["calls"] == 0  # checked before analysis: no pose run, no clip written
    assert not (routes_dir(client) / live_capture.clip_name(lonely.json()["event_id"])).exists()

    assert send(client, "P").json()["status"] == "applied"
    second = send(client, "P")
    assert second.json()["status"] == "ignored" and second.json()["reason"] == "pickup_while_holding"
    assert not (routes_dir(client) / live_capture.clip_name(second.json()["event_id"])).exists()
    assert amox(client)["held_bottles"] == before["held_bottles"] + 1
    # A retry of an ignored event stays ignored.
    retry = send(client, "P", second.json()["event_id"]).json()
    assert retry["status"] == "duplicate" and retry["ignored"] == "pickup_while_holding"
    history = client.get("/api/history").json()["history"]
    assert sum(h["kind"] == "live_ignored" for h in history) == 2
    assert len(state(client)["live"]["movements"]) == 1


def routes_dir(client):
    from pharma.api import routes
    return routes.controller.scenarios_dir


def test_replaying_a_live_clip_never_applies_it_again(client, wrist):
    recording = send(client, "P").json()["recording"]
    wrist["x"] = .65
    send(client, "D")
    counts = amox(client)
    assert client.post(f"/api/recordings/{recording}/load").status_code == 200
    assert client.post("/api/replay/control", json={"action": "seek", "media_time_ms": 10_000}).status_code == 200
    assert client.post(f"/api/recordings/{recording}/apply").json()["applied"] == 0
    assert amox(client) == counts
    # Even after an inventory reset makes it look unapplied, a live clip stays review-only.
    assert client.post("/api/inventory/reset").status_code == 200
    reset = amox(client)
    assert client.post(f"/api/recordings/{recording}/load").status_code == 200
    client.post("/api/replay/control", json={"action": "seek", "media_time_ms": 10_000})
    assert client.post(f"/api/recordings/{recording}/apply").json()["applied"] == 0
    assert amox(client) == reset


def test_counter_bottle_picked_up_again_keeps_its_identity(client, wrist):
    before = amox(client)
    send(client, "P")
    wrist["x"] = .65
    send(client, "D")
    again = send(client, "P")  # at the counter: the parked amoxicillin bottle
    assert again.json()["status"] == "applied"
    wrist["x"] = .25
    returned = send(client, "D")
    assert returned.json()["movement_id"] == again.json()["movement_id"]
    after = amox(client)
    assert after["shelf_counts"] == before["shelf_counts"]
    assert after["counter_bottles"] == before["counter_bottles"]
    assert state(client)["live"]["movements"][0]["state"] == "ON_DESIGNATED_SHELF"


def test_uncertain_pickup_then_putdown_waits_for_confirmation(client, wrist):
    before = amox(client)
    short = send(client, "P", times=WINDOW[20:])  # buffer starts 2 s before the notification
    assert short.json()["status"] == "needs_confirmation"
    assert short.json()["evidence"]["reason"] == "incomplete_clip_window"
    assert amox(client)["shelf_counts"] == before["shelf_counts"]
    live = state(client)["live"]
    assert live["held_movement_id"] == short.json()["movement_id"]  # still counts as in hand
    wrist["x"] = .65
    assert send(client, "D").json()["movement_id"] == short.json()["movement_id"]
    alert_id = state(client)["live"]["movements"][0]["alert_id"]
    assert alert_id
    confirmed = client.post(f"/api/inventory/confirmations/{alert_id}",
                            json={"resolved_region_id": AMOX_SHELF})
    assert confirmed.status_code == 200, confirmed.text
    movement = state(client)["live"]["movements"][0]
    assert movement["pickup"]["confirmed"] and movement["pickup"]["region_id"] == AMOX_SHELF
    assert movement["state"] == "AT_COUNTER"
    assert amox(client)["counter_bottles"] == before["counter_bottles"] + 1


def test_band_event_without_camera_frames_is_uncertain_and_has_no_recording(client, wrist):
    before = amox(client)
    response = send(client, "P", times=[])
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "needs_confirmation"
    assert response.json()["recording"] is None
    assert response.json()["evidence"]["reason"] == "no_camera_frames"
    assert amox(client)["shelf_counts"] == before["shelf_counts"]
    assert state(client)["live"]["movements"][0]["pickup"]["recording"] is None


def test_second_visible_person_prevents_automatic_stock_change(client, wrist):
    wrist["people"] = 2
    response = send(client, "P")
    assert response.json()["status"] == "needs_confirmation"
    assert response.json()["evidence"]["reason"] == "multiple_people"


def test_queued_event_after_calibration_change_keeps_clip_without_reinterpreting(client, wrist):
    old_layout = client.get("/api/layouts/default").json()
    updated = client.put("/api/layouts/default", json=old_layout)
    assert updated.status_code == 200, updated.text
    before = amox(client)
    response = send(client, "P", calibration_version=old_layout["calibration_version"])
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "needs_confirmation"
    assert response.json()["evidence"]["reason"] == "calibration_changed"
    assert wrist["calls"] == 0
    assert client.get(f"/api/recordings/{response.json()['recording']}/video").status_code == 200
    assert amox(client)["shelf_counts"] == before["shelf_counts"]


def test_frames_outside_the_clip_window_are_rejected(client, wrist):
    late = send(client, "P", times=[t + 2000 for t in WINDOW])
    assert late.status_code == 400


def test_dev_key_events_use_the_same_path(client, wrist):
    response = send(client, "P", source="dev")
    assert response.json()["status"] == "applied"
    listed = {r["name"]: r for r in client.get("/api/recordings").json()["recordings"]}
    assert listed[response.json()["recording"]]["live"]["source"] == "dev"


def test_live_lease_blocks_replay_but_allows_clip_review(client, wrist):
    recording = send(client, "P").json()["recording"]
    capture_id = str(uuid.uuid4())
    assert client.post("/api/live/lease", json={"capture_id": capture_id}).status_code == 200
    assert state(client)["live_active"] is True
    assert client.post("/api/replay/control", json={"action": "play"}).status_code == 409
    assert client.post("/api/recordings/demo_scenario_01/apply").status_code == 409
    assert client.post(f"/api/recordings/{recording}/load").status_code == 200
    assert client.post("/api/replay/control", json={"action": "play"}).status_code == 200
    # Another tab can't send events while this one holds the lease.
    assert send(client, "D", capture_id=str(uuid.uuid4())).status_code == 409
    assert client.request("DELETE", "/api/live/lease", json={"capture_id": capture_id}).status_code == 200


def test_live_clip_can_be_deleted_and_its_stock_change_stays(client, wrist):
    recording = send(client, "P").json()["recording"]
    counts = amox(client)
    assert client.delete(f"/api/recordings/{recording}").status_code == 200
    assert amox(client) == counts
    assert state(client)["live"]["movements"][0]["pickup"]["recording"] is None


def test_live_page_direct_navigation(client):
    response = client.get("/live")
    assert response.status_code == 200
