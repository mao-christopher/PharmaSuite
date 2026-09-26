"""Multi-camera uploads, per-camera views, upload-order application, and simulator event files."""

import json

from tests.test_api import client, fake_keypoints, make_video, upload, wait_ready  # noqa: F401 (client is a fixture)

UNITY_EVENTS = "".join(json.dumps(e) + "\n" for e in [
    {"schema_version": 1, "event_id": "s-0000", "session_id": "s", "event_type": "pickup", "sensor_id": "mock-imu-01", "media_time_ms": 500},
    {"schema_version": 1, "event_id": "s-0001", "session_id": "s", "event_type": "movement", "sensor_id": "mock-imu-01", "media_time_ms": 1000},
    {"schema_version": 1, "event_id": "s-0002", "session_id": "s", "event_type": "release", "sensor_id": "mock-imu-01", "media_time_ms": 2000},
]).encode()


def side_only_keypoints(video_path, total_frames, model_path=None, conf=0.25, progress=None, settings=None, imgsz=None):
    """Nobody is visible to the first camera; the second sees a complete right arm on the shelf."""
    if "camera-2" not in str(video_path):
        frames = [None] * total_frames
    else:
        frames = []
        for i in range(total_frames):
            kps = [[0.0, 0.0, 0.0]] * 17
            wrist = [0.25, 0.25, 0.9] if i < 15 else [0.25, 0.65, 0.9]
            kps = kps[:6] + [[0.3, 0.1, 0.9]] + kps[7:8] + [[0.28, 0.18, 0.9]] + kps[9:10] + [wrist] + kps[11:]
            frames.append(kps)
    if progress:
        progress(total_frames, total_frames)
    return frames


def test_multi_camera_upload_switches_to_the_camera_that_sees_the_arm(client, tmp_path, monkeypatch):
    import pharma.pose
    from pharma.api import routes

    monkeypatch.setattr(pharma.pose, "extract_video_keypoints", side_only_keypoints)
    c = client
    make_video(tmp_path / "front.mp4")
    make_video(tmp_path / "side.mp4", frames=32)
    with open(tmp_path / "front.mp4", "rb") as front, open(tmp_path / "side.mp4", "rb") as side:
        res = c.post("/api/recordings", files=[
            ("video", ("front.mp4", front, "video/mp4")),
            ("extra_videos", ("side.mp4", side, "video/mp4")),
            ("events", ("events.jsonl", UNITY_EVENTS, "application/json")),
        ], data={"name": "Two cameras"})
    assert res.status_code == 200, res.text
    body = res.json()
    name = body["name"]
    assert [cam["camera_id"] for cam in body["cameras"]] == ["camera-1", "camera-2"]
    assert body["events"] == 2  # the movement sample is skipped...
    path = routes.controller.scenario_dir(name)
    assert "movement" in next(path.glob("events-source.*")).read_text()  # ...but the source file is kept
    assert wait_ready(c, name)["status"] == "ready"
    assert (path / "camera-2-poses.json").exists()

    # Each camera has its own frame and suggestions; give camera 2 its own view.
    views = c.get(f"/api/recordings/{name}/views", params={"camera": "camera-2"}).json()
    assert views["camera_id"] == "camera-2" and len(views["cameras"]) == 2
    assert c.get(f"/api/recordings/{name}/frame", params={"camera": "camera-2"}).content[:2] == b"\xff\xd8"
    regions = c.get("/api/layouts/default").json()["regions"]
    res = c.post(f"/api/recordings/{name}/view", json={
        "action": "new", "camera_id": "camera-2", "name": "Side angle", "regions": regions,
    })
    assert res.status_code == 200, res.text
    side_view = res.json()["layout"]["layout_id"]
    summary = next(r for r in c.get("/api/recordings").json()["recordings"] if r["name"] == name)
    cams = {cam["camera_id"]: cam for cam in summary["cameras"]}
    assert cams["camera-2"]["layout_id"] == side_view and cams["camera-2"]["view_confirmed"]
    assert not summary["view_confirmed"]  # camera 1 is still unconfirmed
    assert c.post(f"/api/recordings/{name}/view", json={"action": "use", "camera_id": "camera-1", "layout_id": "default"}).status_code == 200
    assert next(r for r in c.get("/api/recordings").json()["recordings"] if r["name"] == name)["view_confirmed"]
    usage = {v["layout_id"]: v["recordings"] for v in c.get("/api/layouts").json()["layouts"]}
    assert any(u["camera_id"] == "camera-2" and u["width"] == 320 for u in usage[side_view])

    assert c.post(f"/api/recordings/{name}/load").status_code == 200
    c.post("/api/replay/control", json={"action": "seek", "media_time_ms": 3000})
    state = c.get("/api/inventory").json()
    assert [(a["camera_id"], a["joint"], a["layout_id"]) for a in state["activity"]] == [
        ("camera-2", "wrist", side_view), ("camera-2", "wrist", side_view),
    ]
    assert state["camera_selection"]["label"] == "side.mp4"
    shelves = state["inventory"]["AMOXICILLIN_500MG"]["shelf_counts"]
    assert shelves == {"shelf_amoxicillin_500mg": 4, "shelf_ibuprofen_200mg": 1}


def test_no_person_in_any_camera_asks_for_confirmation(client, tmp_path, monkeypatch):
    import pharma.pose

    monkeypatch.setattr(pharma.pose, "extract_video_keypoints",
                        lambda video_path, total_frames, *a, progress=None, **k: [None] * total_frames)
    c = client
    make_video(tmp_path / "a.mp4")
    make_video(tmp_path / "b.mp4")
    with open(tmp_path / "a.mp4", "rb") as a, open(tmp_path / "b.mp4", "rb") as b:
        res = c.post("/api/recordings", files=[
            ("video", ("a.mp4", a, "video/mp4")), ("extra_videos", ("b.mp4", b, "video/mp4")),
            ("events", ("t.csv", b"time_s,event\n0,pickup\n2,release\n", "text/csv")),
        ])
    name = res.json()["name"]
    assert wait_ready(c, name)["status"] == "ready"
    assert c.post(f"/api/recordings/{name}/apply").status_code == 200
    activity = c.get(f"/api/recordings/{name}").json()["activity"]
    assert [a["joint"] for a in activity] == [None, None]
    assert any(a["alert_type"] == "uncertainty" for a in c.get("/api/inventory").json()["alerts"].values())


def test_uploads_apply_in_upload_order(client, tmp_path, monkeypatch):
    import pharma.pose

    monkeypatch.setattr(pharma.pose, "extract_video_keypoints", fake_keypoints)
    c = client
    first = upload(c, tmp_path, "Morning")
    second = upload(c, tmp_path, "Afternoon")
    listing = {r["name"]: r for r in c.get("/api/recordings").json()["recordings"]}
    assert (listing[first]["upload_index"], listing[second]["upload_index"]) == (1, 2)
    assert listing[second]["earlier_pending"] == 1
    res = c.post(f"/api/recordings/{second}/apply", params={"include_earlier": True}).json()
    assert res["earlier_applied"] == [first] and res["applied"] == 2
    listing = {r["name"]: r for r in c.get("/api/recordings").json()["recordings"]}
    assert listing[second]["earlier_pending"] == 0
    assert listing[first]["events_applied"] == listing[first]["events_total"] == 2
    # Re-uploading the same footage is a new recording whose events count again.
    third = upload(c, tmp_path, "Morning")
    assert third not in (first, second)
    assert c.post(f"/api/recordings/{third}/apply").json()["applied"] == 2
