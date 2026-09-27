"""Staged uploads: videos first, then times and each camera's view, finished in one step."""

import json

from tests.test_api import client, fake_keypoints, make_video, wait_ready  # noqa: F401 (client is a fixture)

TIMES = b"time_s,event\n0.5,pickup\n2,release\n"


def stage(c, tmp_path, *names):
    files = []
    for n, name in enumerate(names):
        make_video(tmp_path / name)
        files.append(("video" if n == 0 else "extra_videos", (name, (tmp_path / name).read_bytes(), "video/mp4")))
    res = c.post("/api/uploads", files=files)
    assert res.status_code == 200, res.text
    return res.json()


def test_staged_upload_saves_each_cameras_view_and_starts_processing(client, tmp_path, monkeypatch):
    import pharma.pose
    from pharma.api import routes

    monkeypatch.setattr(pharma.pose, "extract_video_keypoints", fake_keypoints)
    c = client
    draft = stage(c, tmp_path, "front.mp4", "side.mp4")
    draft_id = draft["draft_id"]
    assert [cam["camera_id"] for cam in draft["cameras"]] == ["camera-1", "camera-2"]
    assert draft["label"] == "front" and draft["width"] == 320
    # Not a recording yet.
    assert all(r["name"] != draft_id for r in c.get("/api/recordings").json()["recordings"])
    assert c.get(f"/api/uploads/{draft_id}/frame", params={"camera": "camera-2"}).content[:2] == b"\xff\xd8"
    views = c.get(f"/api/uploads/{draft_id}/views", params={"camera": "camera-2"}).json()
    assert views["width"] == 320 and any(s["layout_id"] == "default" for s in views["suggestions"])

    choices = [
        {"camera_id": "camera-1", "action": "use", "layout_id": "default"},
        {"camera_id": "camera-2", "action": "new", "name": "Side angle"},
    ]
    res = c.post(f"/api/uploads/{draft_id}/finish", files={"events": ("times.csv", TIMES, "text/csv")},
                 data={"name": "Morning restock", "views": json.dumps(choices)})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["label"] == "Morning restock" and body["events"] == 2 and body["view_confirmed"]
    cams = {cam["camera_id"]: cam for cam in body["cameras"]}
    assert cams["camera-1"]["layout_id"] == "default"
    side = c.get(f"/api/layouts/{cams['camera-2']['layout_id']}").json()
    assert side["name"] == "Side angle" and side["frame_width"] == 320 and side["regions"] == []
    assert not routes.controller.drafts_dir.exists()  # moved into the recording
    assert wait_ready(c, body["name"])["status"] == "ready"
    summary = next(r for r in c.get("/api/recordings").json()["recordings"] if r["name"] == body["name"])
    assert summary["view_confirmed"] and all(cam["view_confirmed"] for cam in summary["cameras"])


def test_single_camera_without_decisions_gets_the_suggested_view_unconfirmed(client, tmp_path, monkeypatch):
    import pharma.pose

    monkeypatch.setattr(pharma.pose, "extract_video_keypoints", fake_keypoints)
    c = client
    draft_id = stage(c, tmp_path, "clip.mp4")["draft_id"]
    res = c.post(f"/api/uploads/{draft_id}/finish", files={"events": ("times.csv", TIMES, "text/csv")})
    assert res.status_code == 200, res.text
    assert res.json()["cameras"] == [] and not res.json()["view_confirmed"]


def test_rejected_times_keep_the_draft_and_cancel_removes_it(client, tmp_path):
    from pharma.api import routes

    c = client
    draft_id = stage(c, tmp_path, "clip.mp4")["draft_id"]
    res = c.post(f"/api/uploads/{draft_id}/finish", files={"events": ("t.csv", b"99,pickup\n9000,release\n", "text/csv")})
    assert res.status_code == 400 and "past the end" in res.text
    bad_camera = json.dumps([{"camera_id": "camera-9", "action": "use", "layout_id": "default"}])
    res = c.post(f"/api/uploads/{draft_id}/finish", files={"events": ("t.csv", TIMES, "text/csv")}, data={"views": bad_camera})
    assert res.status_code == 400 and "camera-9" in res.text
    assert c.get(f"/api/uploads/{draft_id}/frame").status_code == 200  # still there to fix and retry

    assert c.delete(f"/api/uploads/{draft_id}").status_code == 200
    assert c.get(f"/api/uploads/{draft_id}/frame").status_code == 404
    assert not routes.controller.drafts_dir.exists()
    assert c.get("/api/uploads/not-a-draft/frame").status_code == 404
