"""Room scan, 3D region, camera registration and floor-track endpoints."""

import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from pharma.api.main import app
from tests.room_fixtures import HEIGHT, WIDTH, default_camera, observe, skeleton, write_scan
from tests.test_api import make_controller

# Floor points and a few raised ones (shelf and counter heights) this camera can see.
CANDIDATES = np.array([[x, y, z] for x in (-2, -1, 0, 1, 2) for z in (-2, -1, 0, 1) for y in (0.0, 0.95)])


@pytest.fixture
def client(tmp_path):
    from pharma.api import routes

    with TestClient(app) as c:
        routes.controller = make_controller(tmp_path)
        yield c


def _upload(client, tmp_path, name="Front room"):
    scan = tmp_path / "scan.glb"
    write_scan(scan)
    with scan.open("rb") as f:
        res = client.post("/api/rooms", files={"scan": ("scan.glb", f, "model/gltf-binary")}, data={"name": name})
    assert res.status_code == 200, res.text
    return res.json()


def _pairs():
    """Clicks of known room points as the synthetic camera sees them.

    Registration only needs pairs that agree with each other, so the camera and points are
    placed straight in the imported room's frame.
    """
    cam = default_camera()
    px, _ = cam.project(CANDIDATES)
    norm = px / np.array([WIDTH, HEIGHT])
    seen = np.all((norm > 0.02) & (norm < 0.98), axis=1)
    pairs = [{"image": n.tolist(), "room": p.tolist()} for n, p in zip(norm[seen], CANDIDATES[seen])]
    assert len(pairs) >= 8 and {p["room"][1] for p in pairs} == {0.0, 0.95}
    return cam, pairs


def test_upload_list_and_files(client, tmp_path):
    room = _upload(client, tmp_path)
    assert room["room_id"] == "front-room" and room["room_version"] == 1
    listed = client.get("/api/rooms").json()["rooms"]
    assert [r["room_id"] for r in listed] == ["front-room"]
    assert client.get(room["files"]["mesh"]).headers["content-type"] == "model/gltf-binary"
    assert client.get(room["files"]["plan"]).headers["content-type"] == "image/png"
    assert client.get("/api/rooms/front-room/files/room.json").status_code == 404
    assert client.get("/api/rooms/front-room/files/..%2Fsecret").status_code == 404


def test_upload_rejects_non_scans(client):
    res = client.post("/api/rooms", files={"scan": ("notes.glb", b"hello there, not a scan", "model/gltf-binary")})
    assert res.status_code == 400
    assert "Not a GLB" in res.json()["detail"]


def test_regions_are_validated_and_versioned(client, tmp_path):
    room = _upload(client, tmp_path)
    box = {"center": [0, 0.9, -2.0], "size": [1.6, 0.5, 0.4], "yaw_deg": 0}
    shelf = {"region_id": "x", "region_type": "designated_shelf", "medication_key": "AMOXICILLIN_500MG", "box": box}
    res = client.put("/api/rooms/front-room/regions", json={"regions": [shelf], "room_version": 1})
    assert res.status_code == 200, res.text
    saved = res.json()
    assert saved["room_version"] == 2
    assert saved["regions"][0]["region_id"] == "shelf_amoxicillin_500mg"
    stale = client.put("/api/rooms/front-room/regions", json={"regions": [], "room_version": 1})
    assert stale.status_code == 409
    unknown = {**shelf, "medication_key": "ASPIRIN_81MG"}
    assert client.put("/api/rooms/front-room/regions", json={"regions": [unknown]}).status_code == 422
    twice = client.put("/api/rooms/front-room/regions", json={"regions": [shelf, {**shelf, "region_id": "y"}]})
    assert twice.status_code == 422
    assert "more than one shelf" in twice.json()["detail"]
    assert room["regions"] == []


def test_solve_then_register_a_camera(client, tmp_path):
    _upload(client, tmp_path)
    cam, pairs = _pairs()
    solved = client.post("/api/rooms/front-room/cameras/solve", json={"layout_id": "default", "correspondences": pairs})
    assert solved.status_code == 200, solved.text
    sol = solved.json()
    assert sol["intrinsics"]["fx"] == pytest.approx(cam.fx, rel=1e-3)
    assert sol["quality"] == "good"
    assert len(sol["overlay"]["points"]) == len(pairs) and sol["overlay"]["grid"]
    # Solving saves nothing.
    assert client.get("/api/rooms/front-room").json()["cameras"] == []

    saved = client.put("/api/rooms/front-room/cameras/default", json={"correspondences": pairs})
    assert saved.status_code == 200, saved.text
    reg = saved.json()["registration"]
    assert reg["revision"] == 1 and reg["frame_size"] == [WIDTH, HEIGHT]
    again = client.put("/api/rooms/front-room/cameras/default", json={"correspondences": pairs}).json()
    assert again["registration"]["revision"] == 2
    assert len(again["room"]["cameras"]) == 1

    too_few = client.post("/api/rooms/front-room/cameras/solve", json={"layout_id": "default", "correspondences": pairs[:4]})
    assert too_few.status_code == 422 and "at least 6" in too_few.json()["detail"]
    missing_view = client.post("/api/rooms/front-room/cameras/solve", json={"layout_id": "nope", "correspondences": pairs})
    assert missing_view.status_code == 404


def test_registering_a_view_moves_it_between_rooms(client, tmp_path):
    _upload(client, tmp_path, "Room A")
    _upload(client, tmp_path, "Room B")
    _cam, pairs = _pairs()
    client.put("/api/rooms/room-a/cameras/default", json={"correspondences": pairs})
    client.put("/api/rooms/room-b/cameras/default", json={"correspondences": pairs})
    assert client.get("/api/rooms/room-a").json()["cameras"] == []
    assert [c["layout_id"] for c in client.get("/api/rooms/room-b").json()["cameras"]] == ["default"]
    assert client.delete("/api/rooms/room-b/cameras/default").status_code == 200
    assert client.delete("/api/rooms/room-b/cameras/default").status_code == 404


def _recording(client, frames):
    """A recording folder on the fixture's default view with synthetic skeletons."""
    from pharma.api import routes
    from pharma.services.recordings import PoseTrack

    path = routes.controller.scenarios_dir / "upload-synthetic-walk"
    path.mkdir()
    (path / "scenario.json").write_text(json.dumps({"layout_id": "default", "label": "walk", "source": "upload"}))
    (path / "imu_events.jsonl").write_text("")
    PoseTrack(30, WIDTH, HEIGHT, frames).save(path / "poses.json", "test")
    return path


def test_floor_track_for_a_recording(client, tmp_path):
    cam = default_camera()
    path = _recording(client, [observe(cam, skeleton((-1 + i / 30, 0.5), 90)) for i in range(60)])
    res = client.get("/api/recordings/upload-synthetic-walk/floor-track").json()
    assert res["available"] is False and "isn't registered" in res["reason"] and res["rooms"] == 0

    _upload(client, tmp_path)
    _cam, pairs = _pairs()
    client.put("/api/rooms/front-room/cameras/default", json={"correspondences": pairs})
    res = client.get("/api/recordings/upload-synthetic-walk/floor-track").json()
    assert res["available"] is True, res
    track = res["track"]
    assert track["fields"] == ["x", "z", "yaw_deg", "source", "conf", "bridged"]
    assert track["stats"]["observed"] == 60
    assert track["camera"]["registration_revision"] == 1
    x, z, yaw = track["frames"][30][:3]
    assert np.hypot(x - 0.0, z - 0.5) < 0.05 and abs(yaw - 90) < 10
    assert res["room"]["plan"]["url"].startswith("/api/rooms/front-room/files/plan.png")
    assert res["camera"]["position"] == pytest.approx(cam.center.tolist(), abs=1e-3)
    assert (path / "floor_track.json").exists()


def test_floor_track_needs_skeletons(client, tmp_path):
    path = _recording(client, [None])
    (path / "poses.json").unlink()
    _upload(client, tmp_path)
    _cam, pairs = _pairs()
    client.put("/api/rooms/front-room/cameras/default", json={"correspondences": pairs})
    res = client.get("/api/recordings/upload-synthetic-walk/floor-track").json()
    assert res == {"available": False, "reason": "Skeletons haven't been extracted for this recording yet.",
                   "layout_id": "default", "rooms": 1}
    assert client.get("/api/recordings/no-such-recording/floor-track").status_code == 404


def test_deleting_a_room(client, tmp_path):
    _upload(client, tmp_path)
    assert client.delete("/api/rooms/front-room").status_code == 200
    assert client.get("/api/rooms").json()["rooms"] == []
    assert client.delete("/api/rooms/front-room").status_code == 404


def _shelf_box():
    return {"region_id": "x", "region_type": "designated_shelf", "medication_key": "AMOXICILLIN_500MG",
            "box": {"center": [0, 0.9, -2.2], "size": [1.6, 1.8, 0.4], "yaw_deg": 0}}


def _view(client):
    return client.get("/api/layouts/default").json()


def test_registering_a_camera_generates_its_regions(client, tmp_path):
    _upload(client, tmp_path)
    before = _view(client)
    assert before["regions_source"] is None and before["regions"]  # hand-drawn fixture regions
    client.put("/api/rooms/front-room/regions", json={"regions": [_shelf_box()]})
    _cam, pairs = _pairs()
    solved = client.post("/api/rooms/front-room/cameras/solve",
                         json={"layout_id": "default", "correspondences": pairs}).json()
    preview = solved["overlay"]["generated_regions"]
    assert [r["region_id"] for r in preview] == ["shelf_amoxicillin_500mg"]
    assert _view(client)["regions"] == before["regions"]  # solving previews, saves nothing

    res = client.put("/api/rooms/front-room/cameras/default", json={"correspondences": pairs}).json()
    view = res["layout"]
    assert view["regions"] == preview
    assert view["regions_source"]["room_id"] == "front-room" and view["regions_source"]["registration_revision"] == 1
    assert view["calibration_version"] == before["calibration_version"] + 1

    # Same pairs again: a new registration revision, identical polygons, no calibration bump.
    again = client.put("/api/rooms/front-room/cameras/default", json={"correspondences": pairs}).json()["layout"]
    assert again["regions"] == view["regions"] and again["calibration_version"] == view["calibration_version"]
    assert again["regions_source"]["registration_revision"] == 2

    # Generated regions can't be redrawn by hand; other view edits still save.
    moved = [dict(r, polygon=[[x * 0.9, y] for x, y in r["polygon"]]) for r in again["regions"]]
    bad = client.put("/api/layouts/default", json={**again, "regions": moved})
    assert bad.status_code == 422 and "3D boxes" in bad.json()["detail"]
    ok = client.put("/api/layouts/default", json={**again, "name": "Front"})
    assert ok.status_code == 200 and _view(client)["regions_source"] == again["regions_source"]


def test_views_follow_their_rooms_3d_boxes(client, tmp_path):
    _upload(client, tmp_path)
    _cam, pairs = _pairs()
    client.put("/api/rooms/front-room/regions", json={"regions": [_shelf_box()]})
    client.put("/api/rooms/front-room/cameras/default", json={"correspondences": pairs})
    version = _view(client)["calibration_version"]

    counter = {"region_id": "counter_01", "region_type": "dispensing_counter",
               "box": {"center": [-1.8, 0.475, 0.5], "size": [0.6, 0.95, 1.2], "yaw_deg": 0}}
    res = client.put("/api/rooms/front-room/regions", json={"regions": [_shelf_box(), counter]}).json()
    assert res["view_updates"] and "2 regions" in res["view_updates"][0]
    view = _view(client)
    assert {r["region_id"] for r in view["regions"]} == {"shelf_amoxicillin_500mg", "counter_01"}
    assert view["calibration_version"] == version + 1

    # Saving the same boxes again changes nothing a hand could hit.
    same = client.put("/api/rooms/front-room/regions", json={"regions": [_shelf_box(), counter]}).json()
    assert same["view_updates"] == [] and _view(client)["calibration_version"] == version + 1

    # Without a registration the view has no regions; its signals will ask for confirmation.
    client.delete("/api/rooms/front-room/cameras/default")
    view = _view(client)
    assert view["regions"] == [] and view["regions_source"] is None
    assert view["calibration_version"] == version + 2


def test_hand_drawn_regions_stay_until_adopted(client, tmp_path):
    from pharma.api import routes
    from pharma.db.models import CameraRegistration
    from pharma.services.room import load_room, save_room

    _upload(client, tmp_path)
    client.put("/api/rooms/front-room/regions", json={"regions": [_shelf_box()]})
    _cam, pairs = _pairs()
    reg = client.put("/api/rooms/front-room/cameras/default", json={"correspondences": pairs}).json()["registration"]
    # A registration saved before regions were generated from rooms: put the old regions back.
    ctrl = routes.controller
    client.delete("/api/rooms/front-room/cameras/default")
    room = load_room(ctrl.rooms_dir, "front-room")
    save_room(ctrl.rooms_dir, room.model_copy(update={"cameras": [CameraRegistration.model_validate(reg)]}))
    before = _view(client)
    assert before["regions_source"] is None
    client.put("/api/rooms/front-room/regions", json={"regions": [_shelf_box()]})
    assert _view(client)["regions"] == before["regions"]  # not adopted, so not regenerated

    adopted = client.post("/api/rooms/front-room/cameras/default/adopt-regions").json()["layout"]
    assert adopted["regions_source"]["room_id"] == "front-room"
    assert [r["region_id"] for r in adopted["regions"]] == ["shelf_amoxicillin_500mg"]


def test_deleting_a_room_clears_its_views_generated_regions(client, tmp_path):
    _upload(client, tmp_path)
    client.put("/api/rooms/front-room/regions", json={"regions": [_shelf_box()]})
    _cam, pairs = _pairs()
    client.put("/api/rooms/front-room/cameras/default", json={"correspondences": pairs})
    assert _view(client)["regions"]
    res = client.delete("/api/rooms/front-room").json()
    assert res["view_updates"] and _view(client)["regions"] == []


def test_rebuilt_room_is_served_and_follows_the_room_version(client, tmp_path):
    _upload(client, tmp_path)
    first = client.get("/api/rooms/front-room/rebuilt")
    assert first.status_code == 200
    body = first.json()
    assert body["algorithm"] == "rebuild/2" and len(body["report"]["walls"]) == 4
    assert client.get("/api/rooms/front-room/rebuilt").json() == body  # cached
    client.put("/api/rooms/front-room/regions", json={"regions": [_shelf_box()]})
    again = client.get("/api/rooms/front-room/rebuilt").json()
    assert again["room_version"] == body["room_version"] + 1 and len(again["report"]["shelf_units"]) == 1
    assert client.get("/api/rooms/nowhere/rebuilt").status_code == 404


def _counter_box():
    return {"region_id": "counter_01", "region_type": "dispensing_counter",
            "box": {"center": [1.2, 0.475, -1.0], "size": [0.6, 0.95, 0.6], "yaw_deg": 0}}


def test_timeline_bundles_the_track_decisions_and_starting_counts(client, tmp_path):
    from pharma.api import routes

    ctrl = routes.controller
    cam = default_camera()
    path = _recording(client, [observe(cam, skeleton((-1 + i / 30, 0.5), 90)) for i in range(60)])
    events = [{"event_id": f"evt-{n}", "session_id": sid, "timestamp": 1000.0 + ms / 1000, "media_time_ms": ms,
               "event_type": kind, "sensor_id": "wrist-imu"}
              for n, (sid, ms, kind) in enumerate([("s1", 500, "pickup"), ("s1", 1500, "release"),
                                                   ("s2", 1800, "pickup")])]
    (path / "imu_events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    assert client.get("/api/recordings/upload-synthetic-walk/timeline").json()["available"] is False

    _upload(client, tmp_path)
    client.put("/api/rooms/front-room/regions", json={"regions": [_shelf_box(), _counter_box()]})
    _cam, pairs = _pairs()
    client.put("/api/rooms/front-room/cameras/default", json={"correspondences": pairs})
    view = ctrl.view("default")
    shelf = next(r for r in view.regions if r.region_id == "shelf_amoxicillin_500mg")
    cx, cy = np.mean(shelf.polygon, axis=0)
    opening = ctrl.engine.inventory["AMOXICILLIN_500MG"].shelf_counts["shelf_amoxicillin_500mg"]
    # The pickup lands on the shelf; the put-down has no confident hand, so it waits.
    for event, hands in ((events[0], [(cx, cy, 0.9)]), (events[1], [])):
        ctrl.store.apply_event(path.name, event, hands, (WIDTH, HEIGHT), regions=view.regions,
                               layout_id="default", calibration_version=view.calibration_version)
    ctrl.store.save()

    res = client.get("/api/recordings/upload-synthetic-walk/timeline").json()
    assert res["available"] is True, res
    tl = res["timeline"]
    assert set(tl) == {"schema", "recording", "label", "fps", "frame_size", "duration_ms", "room", "medications", "inputs_key",
                       "cameras", "track", "actions", "start_counts", "rebuilt", "inputs_sha256"}
    assert tl["schema"] == "timeline/1" and tl["fps"] == 30 and len(tl["track"]["frames"]) == 60
    assert tl["cameras"][0]["registered"] and tl["cameras"][0]["registration_revision"] == 1
    pickup, putdown, later = tl["actions"]
    assert pickup == {**pickup, "action_id": "evt-0", "type": "pickup", "contact_ms": 500, "status": "decided",
                      "region_id": "shelf_amoxicillin_500mg", "outcome": "in_hand",
                      "medication_key": "AMOXICILLIN_500MG", "original_shelf_id": "shelf_amoxicillin_500mg"}
    assert putdown["status"] == "pending" and putdown["region_id"] is None and putdown["outcome"] == "pending"
    assert putdown["bottle_id"] == pickup["bottle_id"]
    assert later["status"] == "not_applied" and later["outcome"] is None
    assert tl["start_counts"]["source"] == "before_first_signal"
    assert tl["start_counts"]["shelves"]["shelf_amoxicillin_500mg"]["AMOXICILLIN_500MG"] == opening
    assert "wrist" not in json.dumps(tl["actions"]) and "raw_event" not in json.dumps(tl)

    # Exporting again gives the same bytes and leaves the file alone.
    written = path / "timeline.json"
    mtime = written.stat().st_mtime_ns
    again = client.get("/api/recordings/upload-synthetic-walk/timeline").json()["timeline"]
    assert again == tl and written.stat().st_mtime_ns == mtime

    # An employee confirms the put-down at the counter: the action and the hash follow.
    alert = next(a for a in ctrl.engine.alerts.values() if a.alert_type == "uncertainty" and a.status == "open")
    assert client.post(f"/api/inventory/confirmations/{alert.alert_id}",
                       json={"resolved_region_id": "counter_01"}).status_code == 200
    confirmed = client.get("/api/recordings/upload-synthetic-walk/timeline").json()["timeline"]
    assert confirmed["actions"][1] == {**confirmed["actions"][1], "status": "confirmed",
                                       "region_id": "counter_01", "outcome": "counter"}
    assert confirmed["actions"][0]["outcome"] == "in_hand"
    assert confirmed["inputs_sha256"] != tl["inputs_sha256"]
    assert json.loads(written.read_text())["inputs_sha256"] == confirmed["inputs_sha256"]
