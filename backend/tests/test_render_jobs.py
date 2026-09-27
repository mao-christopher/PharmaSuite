"""Simulation renders (M9): queue one at a time, reuse the same inputs, go stale on corrections."""

import json
import threading

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from pharma.api.main import app
from pharma.services.render_jobs import RenderQueue
from tests.room_fixtures import HEIGHT, WIDTH, default_camera, observe, skeleton
from tests.test_api import make_controller
from tests.test_room_api import CANDIDATES, _counter_box, _shelf_box, _upload

NAME = "upload-synthetic-walk"


def _video(path, frames=20, color=(40, 90, 160), size=(WIDTH // 4, HEIGHT // 4)):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30, size)
    for i in range(frames):
        frame = np.full((size[1], size[0], 3), color, np.uint8)
        cv2.putText(frame, str(i), (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        writer.write(frame)
    writer.release()


class FakeUnity:
    """Stands in for simulation/tools/render.py --timeline."""

    def __init__(self, block=False):
        self.calls = []
        self.block = block
        self.started = threading.Event()

    def __call__(self, timeline_path, out_dir, progress, cancel):
        self.calls.append(json.loads(timeline_path.read_text()))
        self.started.set()
        out_dir.mkdir(parents=True)
        progress(0.5)
        if self.block and cancel.wait(5):
            raise RuntimeError("terminated")
        _video(out_dir / "camera.mp4", color=(200, 60, 60), size=(WIDTH // 2, HEIGHT // 2))
        (out_dir / "render_report.json").write_text(json.dumps({"alignment_max_px": 0.4}))
        return out_dir / "camera.mp4"


@pytest.fixture
def setup(tmp_path):
    from pharma.api import routes

    with TestClient(app) as client:
        ctrl = routes.controller = make_controller(tmp_path)
        from pharma.services.recordings import PoseTrack

        cam = default_camera()
        path = ctrl.scenarios_dir / NAME
        path.mkdir()
        (path / "scenario.json").write_text(json.dumps({"layout_id": "default", "label": "walk", "source": "upload"}))
        events = [{"event_id": "evt-0", "session_id": "s1", "timestamp": 1.0, "media_time_ms": 300,
                   "event_type": "release", "sensor_id": "wrist-imu"}]
        (path / "imu_events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
        PoseTrack(30, WIDTH, HEIGHT, [observe(cam, skeleton((-1 + i / 30, 0.5), 90)) for i in range(20)]).save(
            path / "poses.json", "test")
        _video(path / "video.mp4")
        _upload(client, tmp_path)
        client.put("/api/rooms/front-room/regions", json={"regions": [_shelf_box(), _counter_box()]})
        px, _ = cam.project(CANDIDATES)
        norm = px / np.array([WIDTH, HEIGHT])
        seen = np.all((norm > 0.02) & (norm < 0.98), axis=1)
        pairs = [{"image": n.tolist(), "room": p.tolist()} for n, p in zip(norm[seen], CANDIDATES[seen])]
        client.put("/api/rooms/front-room/cameras/default", json={"correspondences": pairs})
        yield client, ctrl, path


def test_without_unity_the_button_says_why(setup):
    client, ctrl, _ = setup
    ctrl.renders = RenderQueue(unity_path="")
    status = client.get(f"/api/recordings/{NAME}/render").json()
    assert status["state"] == "none" and status["can_render"] is False and "UNITY_PATH" in status["reason"]
    res = client.post(f"/api/recordings/{NAME}/render")
    assert res.status_code == 409 and "UNITY_PATH" in res.json()["detail"]
    assert client.get("/api/renders").json()["available"] is False
    listed = next(r for r in client.get("/api/recordings").json()["recordings"] if r["name"] == NAME)
    assert listed["render"]["can_render"] is False


def test_render_is_reused_then_goes_stale_after_a_correction(setup):
    client, ctrl, path = setup
    fake = FakeUnity()
    ctrl.renders = RenderQueue(runner=fake)
    assert client.get(f"/api/recordings/{NAME}/render").json()["can_render"] is True

    started = client.post(f"/api/recordings/{NAME}/render").json()
    assert started["state"] in ("queued", "running", "done")
    assert ctrl.renders.wait_idle()
    done = client.get(f"/api/recordings/{NAME}/render").json()
    assert done["state"] == "done" and done["files"] == ["side_by_side.mp4", "sim.mp4"]
    assert done["rendered_key"] == done["inputs_key"]
    manifest = client.get(f"/api/recordings/{NAME}/render/manifest.json").json()
    assert manifest["render_report"] == {"alignment_max_px": 0.4}
    assert manifest["requested"]["fps"] == 30 and manifest["rendered"]["frames"] == 20
    assert fake.calls[0]["schema"] == "timeline/1"
    assert client.get(f"/api/recordings/{NAME}/render/sim.mp4").status_code == 200

    # Same inputs: no second Unity run.
    again = client.post(f"/api/recordings/{NAME}/render").json()
    assert again["state"] == "done" and len(fake.calls) == 1

    # The put-down is applied (uncertain) and then confirmed: the render is stale.
    view = ctrl.view("default")
    ctrl.store.apply_event(NAME, json.loads((path / "imu_events.jsonl").read_text()), [], (WIDTH, HEIGHT),
                           regions=view.regions, layout_id="default")
    ctrl.store.save()
    stale = client.get(f"/api/recordings/{NAME}/render").json()
    assert stale["state"] == "stale" and stale["rendered_key"] != stale["inputs_key"]
    client.post(f"/api/recordings/{NAME}/render")
    assert ctrl.renders.wait_idle() and len(fake.calls) == 2
    assert fake.calls[1]["actions"][0]["status"] == "pending"
    assert client.get(f"/api/recordings/{NAME}/render").json()["state"] == "done"


def test_a_render_can_be_cancelled(setup):
    client, ctrl, _ = setup
    fake = FakeUnity(block=True)
    ctrl.renders = RenderQueue(runner=fake)
    client.post(f"/api/recordings/{NAME}/render")
    assert fake.started.wait(5)
    assert client.get("/api/renders").json()["jobs"][0]["state"] == "running"
    assert client.delete(f"/api/recordings/{NAME}/render").status_code == 200
    assert ctrl.renders.wait_idle()
    status = client.get(f"/api/recordings/{NAME}/render").json()
    assert status["state"] == "cancelled" and status["files"] == []
    assert client.delete(f"/api/recordings/{NAME}/render").status_code == 404


def test_player_switches_between_real_simulation_and_side_by_side(setup):
    client, ctrl, _ = setup
    ctrl.renders = RenderQueue(runner=FakeUnity())
    assert client.post(f"/api/recordings/{NAME}/load").status_code == 200
    assert client.post("/api/player/source", json={"source": "sim"}).status_code == 409  # nothing rendered yet
    client.post(f"/api/recordings/{NAME}/render")
    assert ctrl.renders.wait_idle()

    def frame():
        chunk = next(ctrl.mjpeg_generator())
        jpeg = chunk.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n", 1)[0]
        return cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)

    real = frame()
    assert client.post("/api/player/source", json={"source": "side"}).json()["player_source"] == "side"
    side = frame()
    assert side.shape[0] == real.shape[0] and side.shape[1] > real.shape[1] * 1.5
    client.post("/api/player/source", json={"source": "sim"})
    sim = frame()
    assert sim.shape[:2] == (HEIGHT // 2, WIDTH // 2)
    assert sim[5, 5, 0] > 150 and sim[5, 5, 2] < 100  # the render's color (BGR), not the real video's
    assert client.post("/api/player/source", json={"source": "nope"}).status_code == 409


def test_queued_render_uses_its_own_timeline_snapshot(tmp_path):
    """A later timeline export cannot silently change a queued job's contents/key."""
    first_started, unblock = threading.Event(), threading.Event()
    seen = []
    def runner(path, out, progress, cancel):
        data = json.loads(path.read_text())
        seen.append(data)
        if len(seen) == 1:
            first_started.set()
            assert unblock.wait(5)
        out.mkdir(parents=True)
        _video(out / "camera.mp4")
        return out / "camera.mp4"
    queue = RenderQueue(runner=runner)
    def submit(name):
        folder = tmp_path / name
        folder.mkdir()
        timeline = dict(inputs_key=name*32, inputs_sha256=name, fps=30,
                        frame_size=[WIDTH//4, HEIGHT//4], duration_ms=667, label=name)
        (folder / "timeline.json").write_text(json.dumps(timeline))
        queue.enqueue(name, name, folder, timeline, None)
        return folder, timeline
    submit("a")
    assert first_started.wait(5)
    folder, original = submit("b")
    (folder / "timeline.json").write_text('{"label":"changed after queueing"}')
    original["label"] = "caller mutated it too"
    unblock.set()
    assert queue.wait_idle()
    assert [entry["label"] for entry in seen] == ["a", "b"]
    from pharma.services.render_jobs import finished, render_dir
    manifest = finished(folder, "b"*32)
    assert manifest is not None
    import hashlib
    snapshot = render_dir(folder, "b"*32) / "timeline.json"
    assert manifest["timeline_sha256"] == hashlib.sha256(snapshot.read_bytes()).hexdigest()


@pytest.mark.skipif(__import__('os').name != 'posix', reason='POSIX process-group cancellation')
def test_cancel_stops_renderer_child_processes(tmp_path):
    """Exercise the real runner with a lightweight wrapper/child, without starting Unity."""
    import time
    from pharma.services.render_jobs import unity_runner
    tools = tmp_path / 'simulation' / 'tools'
    tools.mkdir(parents=True)
    marker = tmp_path / 'child-ready'
    completed = tmp_path / 'child-survived'
    child = ("from pathlib import Path; import time; "
             f"Path({str(marker)!r}).write_text('ready'); time.sleep(2); "
             f"Path({str(completed)!r}).write_text('unexpected')")
    (tools / 'render.py').write_text(
        'import subprocess, sys, time\n'
        f'subprocess.Popen([sys.executable, "-c", {child!r}])\n'
        'time.sleep(30)\n')
    cancel = threading.Event()
    errors = []
    def run():
        try:
            unity_runner('unused', tools.parent)(tmp_path/'timeline.json', tmp_path/'output', lambda p: None, cancel)
        except RuntimeError as exc:
            errors.append(str(exc))
    worker = threading.Thread(target=run)
    worker.start()
    try:
        deadline = time.monotonic()+5
        while not marker.exists() and time.monotonic()<deadline:
            time.sleep(.02)
        assert marker.exists()
        cancel.set()
        worker.join(5)
        assert not worker.is_alive()
        time.sleep(2.1)
        assert not completed.exists(), 'Cancel left a child renderer running'
        assert errors
    finally:
        cancel.set()
        worker.join(5)
