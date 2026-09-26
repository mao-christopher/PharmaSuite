"""Pytest suite for FastAPI REST API endpoints using TestClient."""

import pytest
from fastapi.testclient import TestClient
from pharma.api.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_list_scenarios(client):
    response = client.get("/api/scenarios")
    assert response.status_code == 200
    data = response.json()
    assert "scenarios" in data
    assert len(data["scenarios"]) > 0
    assert data["scenarios"][0]["name"] == "demo_scenario_01"


def test_load_scenario(client):
    response = client.post("/api/scenarios/demo_scenario_01/load")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["data"]["scenario_name"] == "demo_scenario_01"


def test_get_inventory(client):
    # Ensure scenario loaded
    client.post("/api/scenarios/demo_scenario_01/load")
    response = client.get("/api/inventory")
    assert response.status_code == 200
    data = response.json()
    assert data["scenario"] == "demo_scenario_01"
    assert "inventory" in data
    assert "AMOXICILLIN_500MG" in data["inventory"]
    assert data["inventory"]["AMOXICILLIN_500MG"]["pooled_tablets"] == 500


def test_replay_control(client):
    client.post("/api/scenarios/demo_scenario_01/load")

    # Play action
    res_play = client.post("/api/replay/control", json={"action": "play"})
    assert res_play.status_code == 200
    assert res_play.json()["is_playing"] is True

    # Pause action
    res_pause = client.post("/api/replay/control", json={"action": "pause"})
    assert res_pause.status_code == 200
    assert res_pause.json()["is_playing"] is False

    # Seek action
    res_seek = client.post("/api/replay/control", json={"action": "seek", "media_time_ms": 3500})
    assert res_seek.status_code == 200
    assert res_seek.json()["media_time_ms"] == 3500


def test_update_transaction_status(client):
    client.post("/api/scenarios/demo_scenario_01/load")

    # Initial pooled tablets = 500, tx quantity = 30
    res = client.post("/api/transactions/TX_RX_1001/status", json={"status": "confirmed_fill"})
    assert res.status_code == 200
    assert res.json()["deduction_applied"] is True

    # Check inventory deducted to 470
    res_inv = client.get("/api/inventory")
    assert res_inv.json()["inventory"]["AMOXICILLIN_500MG"]["pooled_tablets"] == 470

    # Repeat request (idempotent) -> deduction_applied should be False
    res_repeat = client.post("/api/transactions/TX_RX_1001/status", json={"status": "paid"})
    assert res_repeat.status_code == 200
    assert res_repeat.json()["deduction_applied"] is False


@pytest.fixture
def isolated_client(tmp_path):
    """Client whose controller reads/writes a temporary copy of the data directory."""
    import shutil
    from pathlib import Path
    from pharma.api import routes
    from pharma.api.replay_stream import ReplayController

    data_dir = Path(__file__).resolve().parents[1] / "data"
    shutil.copytree(data_dir / "scenarios", tmp_path / "scenarios")
    shutil.copytree(data_dir / "layouts", tmp_path / "layouts")
    with TestClient(app) as c:
        routes.controller = ReplayController(scenarios_dir=tmp_path / "scenarios")
        routes.controller.load_scenario("demo_scenario_01")
        yield c


def test_inventory_includes_layout_and_derived_stock(client):
    client.post("/api/scenarios/demo_scenario_01/load")
    data = client.get("/api/inventory").json()
    assert data["layout"]["layout_id"] == "default"
    keys = {m["medication_key"] for m in data["layout"]["medications"]}
    assert {"AMOXICILLIN_500MG", "IBUPROFEN_200MG"} <= keys
    assert data["inventory"]["IBUPROFEN_200MG"]["total_bottles"] == 7
    assert all(len(r["polygon"]) >= 3 for r in data["layout"]["regions"])


def test_scenarios_report_layout(client):
    scenario = client.get("/api/scenarios").json()["scenarios"][0]
    assert scenario["layout_id"] == "default"


def test_video_still_is_jpeg(client):
    res = client.get("/api/video/still")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/jpeg"
    assert res.content[:2] == b"\xff\xd8"


def test_put_layout_saves_new_version_and_resets_scenario(isolated_client):
    c = isolated_client
    c.post("/api/transactions/TX_RX_1001/status", json={"status": "confirmed_fill"})
    assert c.get("/api/inventory").json()["inventory"]["AMOXICILLIN_500MG"]["pooled_tablets"] == 470

    layout = c.get("/api/layouts/default").json()
    layout["receipts"].append({
        "receipt_id": "REC_AMX_NEW", "medication_key": "AMOXICILLIN_500MG", "bottle_count": 2,
        "tablets_per_bottle": 60, "expiry_date": "2028-01-31", "lot_number": None,
        "received_at": "2026-09-20T00:00:00Z",
    })
    res = c.put("/api/layouts/default", json=layout)
    assert res.status_code == 200
    body = res.json()
    assert body["scenario_reloaded"] is True
    assert body["layout"]["calibration_version"] == layout["calibration_version"] + 1

    state = c.get("/api/inventory").json()
    assert state["layout"]["calibration_version"] == body["layout"]["calibration_version"]
    # Reload rebuilt state from the presets: prior deduction is gone, new batch included.
    assert state["inventory"]["AMOXICILLIN_500MG"]["pooled_tablets"] == 500 + 120
    assert state["inventory"]["AMOXICILLIN_500MG"]["total_bottles"] == 7


def test_put_layout_rejects_invalid_setup_without_writing(isolated_client):
    c = isolated_client
    before = c.get("/api/layouts/default").json()
    bad = {**before, "regions": [r for r in before["regions"] if r["region_type"] != "designated_shelf"]}
    res = c.put("/api/layouts/default", json=bad)
    assert res.status_code == 422
    assert "has no shelf drawn" in res.text
    assert c.get("/api/layouts/default").json() == before

    res = c.put("/api/layouts/other", json=before)
    assert res.status_code == 400


def make_video(path, frames=30, fps=10, size=(320, 180)):
    import cv2
    import numpy as np

    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    for i in range(frames):
        frame = np.full((size[1], size[0], 3), 200, np.uint8)
        cv2.putText(frame, str(i), (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
        writer.write(frame)
    writer.release()


def fake_keypoints(video_path, total_frames, model_path=None, conf=0.25, progress=None, settings=None):
    # Right wrist sits on the Amoxicillin shelf, then over the Ibuprofen shelf from frame 15.
    frames = []
    for i in range(total_frames):
        kps = [[0.0, 0.0, 0.0]] * 17
        wrist = [0.25, 0.25, 0.9] if i < 15 else [0.25, 0.65, 0.9]
        frames.append(kps[:10] + [wrist] + kps[11:])
        if progress:
            progress(i + 1, total_frames)
    return frames


def wait_ready(c, name, timeout=10.0):
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        s = next(s for s in c.get("/api/scenarios").json()["scenarios"] if s["name"] == name)
        if s["status"] in ("ready", "error"):
            return s
        time.sleep(0.05)
    raise AssertionError("processing did not finish")


def test_upload_recording_extracts_skeletons_and_replays(isolated_client, tmp_path, monkeypatch):
    import pharma.pose

    monkeypatch.setattr(pharma.pose, "extract_video_keypoints", fake_keypoints)
    c = isolated_client
    make_video(tmp_path / "clip.mp4")
    with open(tmp_path / "clip.mp4", "rb") as video:
        res = c.post(
            "/api/recordings",
            files={
                "video": ("clip.mp4", video, "video/mp4"),
                "events": ("events.csv", b"time_s,event\n0.5,pickup\n2.0,drop\n", "text/csv"),
            },
            data={"name": "Wrong shelf test"},
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["events"] == 2 and body["duration_ms"] == 3000
    assert body["warnings"] == []  # 320x180 has the same 16:9 aspect as the layout
    name = body["name"]

    status = wait_ready(c, name)
    assert status["status"] == "ready", status
    assert c.post(f"/api/scenarios/{name}/load").status_code == 200
    assert c.post("/api/replay/control", json={"action": "seek", "media_time_ms": 3000}).status_code == 200

    state = c.get("/api/inventory").json()
    assert state["has_video"] is True
    amx = state["inventory"]["AMOXICILLIN_500MG"]
    assert amx["shelf_counts"] == {"shelf_amoxicillin_500mg": 4, "shelf_ibuprofen_200mg": 1}
    misplaced = [a for a in state["alerts"].values() if a["alert_type"] == "misplacement"]
    assert len(misplaced) == 1 and misplaced[0]["medication_key"] == "AMOXICILLIN_500MG"
    assert all(e["processed"] for e in state["events"])


def test_upload_rejects_bad_timestamps_and_cleans_up(isolated_client, tmp_path):
    from pharma.api import routes

    c = isolated_client
    make_video(tmp_path / "clip.mp4")
    before = {p.name for p in routes.controller.scenarios_dir.iterdir()}
    with open(tmp_path / "clip.mp4", "rb") as video:
        res = c.post(
            "/api/recordings",
            files={"video": ("clip.mp4", video), "events": ("e.csv", b"99,pickup\n9000,release\n")},
        )
    assert res.status_code == 400 and "past the end" in res.text
    assert {p.name for p in routes.controller.scenarios_dir.iterdir()} == before

    res = c.post("/api/recordings", files={"video": ("clip.txt", b"x"), "events": ("e.csv", b"1,pickup")})
    assert res.status_code == 400 and "Unsupported video type" in res.text


def test_receive_stock_endpoint_adds_live_stock(isolated_client):
    c = isolated_client
    c.post("/api/replay/control", json={"action": "seek", "media_time_ms": 2000})  # bottle in hand
    res = c.post("/api/inventory/receipts", json={
        "medication_key": "IBUPROFEN_200MG", "bottle_count": 2, "tablets_per_bottle": 50,
        "expiry_date": "2027-03-31", "lot_number": " LOT-9 ",
    })
    assert res.status_code == 200, res.text
    assert res.json()["receipt"]["lot_number"] == "LOT-9"
    state = c.get("/api/inventory").json()
    assert state["inventory"]["IBUPROFEN_200MG"]["total_bottles"] == 9
    assert state["inventory"]["IBUPROFEN_200MG"]["pooled_tablets"] == 800
    assert state["inventory"]["AMOXICILLIN_500MG"]["held_bottles"] == 1  # live state kept

    bad = c.post("/api/inventory/receipts", json={
        "medication_key": "NOPE_1MG", "bottle_count": 1, "tablets_per_bottle": 1, "expiry_date": "2027-01-01",
    })
    assert bad.status_code == 400
    bad = c.post("/api/inventory/receipts", json={
        "medication_key": "IBUPROFEN_200MG", "bottle_count": 0, "tablets_per_bottle": 1, "expiry_date": "2027-01-01",
    })
    assert bad.status_code == 422


def test_expired_batch_in_layout_raises_alert_on_load(client):
    client.post("/api/scenarios/demo_scenario_01/load")
    alerts = client.get("/api/inventory").json()["alerts"].values()
    expiry = [a for a in alerts if a["alert_type"] == "expiry"]
    assert [a["metadata"]["receipt_id"] for a in expiry] == ["REC_IBU_2026_01"]
    res = client.post(f"/api/inventory/confirmations/{expiry[0]['alert_id']}", json={})
    assert res.status_code == 400  # expiry clears only through disposal


def test_confirm_uncertainty_requires_and_applies_region(isolated_client):
    from pharma.api import routes

    c = isolated_client
    engine = routes.controller.engine
    engine.handle_pickup("s_manual", [(0.6, 0.95, 0.9)], 0)
    alert_id = next(a.alert_id for a in engine.alerts.values() if a.alert_type == "uncertainty")
    assert c.post(f"/api/inventory/confirmations/{alert_id}", json={}).status_code == 400
    res = c.post(f"/api/inventory/confirmations/{alert_id}", json={"resolved_region_id": "shelf_amoxicillin_500mg"})
    assert res.status_code == 200 and res.json()["alert"]["status"] == "resolved"
    assert engine.inventory["AMOXICILLIN_500MG"].held_bottles == 1


def test_background_upload_and_save(isolated_client):
    import cv2
    import numpy as np

    c = isolated_client
    ok, png = cv2.imencode(".png", np.full((360, 640, 3), 128, np.uint8))
    res = c.post("/api/layouts/default/background", files={"image": ("room.png", png.tobytes(), "image/png")})
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["width"], body["height"]) == (640, 360)
    assert c.get(f"/api/layouts/default/files/{body['background_image']}").status_code == 200
    assert c.get("/api/layouts/default/files/..%2Flayout.json").status_code == 404

    layout = c.get("/api/layouts/default").json()
    layout.update(background_image=body["background_image"], frame_width=640, frame_height=360)
    assert c.put("/api/layouts/default", json=layout).status_code == 200
    assert c.get("/api/inventory").json()["layout"]["background_image"] == body["background_image"]

    layout["background_image"] = "background-000000000000.jpg"
    assert c.put("/api/layouts/default", json=layout).status_code == 400

    res = c.post("/api/layouts/default/background", files={"image": ("x.png", b"not an image")})
    assert res.status_code == 400
