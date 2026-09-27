"""Pytest suite for FastAPI REST API endpoints using TestClient."""

import cv2
import numpy as np
import pytest
import cv2
import numpy as np
from fastapi.testclient import TestClient
from pharma.api.main import app


def make_controller(tmp_path):
    """Controller over a private copy of the data directory (layouts, recordings, state)."""
    import shutil
    from pathlib import Path
    from pharma.api.replay_stream import ReplayController

    from tests.conftest import FIXTURE_LAYOUTS

    data_dir = Path(__file__).resolve().parents[1] / "data"
    skip = shutil.ignore_patterns("upload-*", "live-*", ".live-staging", "video.*", "poses.json", "thumb.jpg")
    if not (tmp_path / "scenarios").exists():
        shutil.copytree(data_dir / "scenarios", tmp_path / "scenarios", ignore=skip)
    if not (tmp_path / "layouts").exists():
        shutil.copytree(FIXTURE_LAYOUTS, tmp_path / "layouts")
        shutil.copy(FIXTURE_LAYOUTS.parent / "catalog.json", tmp_path / "catalog.json")
    return ReplayController(scenarios_dir=tmp_path / "scenarios")


@pytest.fixture
def client(tmp_path):
    from pharma.api import routes

    with TestClient(app) as c:
        routes.controller = make_controller(tmp_path)
        routes.controller.load_scenario("demo_scenario_01")
        yield c


isolated_client = client


def test_list_recordings(client):
    response = client.get("/api/recordings")
    assert response.status_code == 200
    data = response.json()
    assert data["current"] == "demo_scenario_01"
    demo = next(r for r in data["recordings"] if r["name"] == "demo_scenario_01")
    assert demo["label"] == "Scripted demo (no video)"
    assert (demo["events_total"], demo["events_applied"], demo["in_player"]) == (2, 0, True)


def test_load_scenario(client):
    response = client.post("/api/recordings/demo_scenario_01/load")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["data"]["scenario_name"] == "demo_scenario_01"


def test_get_inventory(client):
    # Ensure scenario loaded
    client.post("/api/recordings/demo_scenario_01/load")
    response = client.get("/api/inventory")
    assert response.status_code == 200
    data = response.json()
    assert data["scenario"] == "demo_scenario_01"
    assert "inventory" in data
    assert "AMOXICILLIN_500MG" in data["inventory"]
    assert data["inventory"]["AMOXICILLIN_500MG"]["pooled_tablets"] == 500


def test_replay_control(client):
    client.post("/api/recordings/demo_scenario_01/load")

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
    client.post("/api/recordings/demo_scenario_01/load")

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


def test_inventory_includes_layout_and_derived_stock(client):
    client.post("/api/recordings/demo_scenario_01/load")
    data = client.get("/api/inventory").json()
    assert data["layout"]["layout_id"] == "default"
    keys = {m["medication_key"] for m in data["layout"]["medications"]}
    assert {"AMOXICILLIN_500MG", "IBUPROFEN_200MG"} <= keys
    assert data["inventory"]["IBUPROFEN_200MG"]["total_bottles"] == 7
    assert all(len(r["polygon"]) >= 3 for r in data["layout"]["regions"])


def test_recordings_report_layout(client):
    recording = client.get("/api/recordings").json()["recordings"][0]
    assert recording["layout_id"] == "default"


def test_video_still_is_jpeg(client):
    res = client.get("/api/video/still")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/jpeg"
    assert res.content[:2] == b"\xff\xd8"


def test_put_layout_keeps_live_inventory_unless_reset(isolated_client):
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
    assert body["inventory_reset"] is False
    assert body["layout"]["calibration_version"] == layout["calibration_version"] + 1
    state = c.get("/api/inventory").json()
    assert state["layout"]["calibration_version"] == body["layout"]["calibration_version"]
    # Opening stock only matters on reset; the live count and deduction are kept.
    assert state["inventory"]["AMOXICILLIN_500MG"]["pooled_tablets"] == 470

    res = c.put("/api/layouts/default?reset_inventory=true", json=body["layout"])
    assert res.status_code == 200 and res.json()["inventory_reset"] is True
    state = c.get("/api/inventory").json()
    assert state["inventory"]["AMOXICILLIN_500MG"]["pooled_tablets"] == 500 + 120
    assert state["inventory"]["AMOXICILLIN_500MG"]["total_bottles"] == 7
    assert state["transactions"]["TX_RX_1001"]["deducted"] is False


def test_put_layout_rejects_invalid_setup_without_writing(isolated_client):
    c = isolated_client
    before = c.get("/api/layouts/default").json()
    bad = {**before, "regions": [{**r, "medication_key": "NOPE_1MG"} if r["region_type"] == "designated_shelf" else r
                                 for r in before["regions"]]}
    res = c.put("/api/layouts/default", json=bad)
    assert res.status_code == 422
    assert "no known medication" in res.text
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


def fake_keypoints(video_path, total_frames, model_path=None, conf=0.25, progress=None, settings=None, imgsz=None):
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
        s = next(s for s in c.get("/api/recordings").json()["recordings"] if s["name"] == name)
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
    assert c.post(f"/api/recordings/{name}/load").status_code == 200
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
    client.post("/api/recordings/demo_scenario_01/load")
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
    routes.controller.store.save()
    assert c.post(f"/api/inventory/confirmations/{alert_id}", json={}).status_code == 400
    res = c.post(f"/api/inventory/confirmations/{alert_id}", json={"resolved_region_id": "shelf_amoxicillin_500mg"})
    assert res.status_code == 200 and res.json()["alert"]["status"] == "resolved"
    assert routes.controller.engine.inventory["AMOXICILLIN_500MG"].held_bottles == 1


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


def upload(c, tmp_path, label, csv=b"time_s,event\n0.5,pickup\n2.0,drop\n"):
    make_video(tmp_path / "clip.mp4")
    with open(tmp_path / "clip.mp4", "rb") as video:
        res = c.post(
            "/api/recordings",
            files={"video": ("clip.mp4", video, "video/mp4"), "events": ("events.csv", csv, "text/csv")},
            data={"name": label},
        )
    assert res.status_code == 200, res.text
    name = res.json()["name"]
    assert wait_ready(c, name)["status"] == "ready"
    return name


def test_recordings_are_stored_and_inventory_carries_across_them(isolated_client, tmp_path, monkeypatch):
    import pharma.pose

    monkeypatch.setattr(pharma.pose, "extract_video_keypoints", fake_keypoints)
    c = isolated_client
    first = upload(c, tmp_path, "First shift")
    second = upload(c, tmp_path, "Second shift")

    # Applying without playback is idempotent per recording.
    assert c.post(f"/api/recordings/{first}/apply").json()["applied"] == 2
    assert c.post(f"/api/recordings/{first}/apply").json()["applied"] == 0
    assert c.post(f"/api/recordings/{second}/apply").json()["applied"] == 2

    state = c.get("/api/inventory").json()
    # Both recordings moved one Amoxicillin bottle onto the Ibuprofen shelf: counts accumulate.
    assert state["inventory"]["AMOXICILLIN_500MG"]["shelf_counts"] == {
        "shelf_amoxicillin_500mg": 3, "shelf_ibuprofen_200mg": 2,
    }
    misplaced = [a for a in state["alerts"].values() if a["alert_type"] == "misplacement"]
    assert len({a["metadata"]["session_id"] for a in misplaced}) == 2  # sess_001 of each, kept apart

    listing = {r["name"]: r for r in c.get("/api/recordings").json()["recordings"]}
    assert list(listing)[:2] == [second, first]  # newest upload first
    assert (listing[first]["events_applied"], listing[first]["alerts_open"]) == (2, 1)
    assert listing[first]["source"] == "upload" and listing[first]["video_filename"] == "clip.mp4"

    # Replaying an applied recording shows it again but changes nothing.
    assert c.post(f"/api/recordings/{first}/load").status_code == 200
    c.post("/api/replay/control", json={"action": "seek", "media_time_ms": 3000})
    c.post("/api/replay/control", json={"action": "restart"})
    again = c.get("/api/inventory").json()["inventory"]["AMOXICILLIN_500MG"]["shelf_counts"]
    assert again == state["inventory"]["AMOXICILLIN_500MG"]["shelf_counts"]

    detail = c.get(f"/api/recordings/{first}").json()
    assert [a["event_type"] for a in detail["activity"]] == ["pickup", "release"]
    assert len(detail["alerts"]) == 1 and all(e["processed"] for e in detail["events"])

    thumb = c.get(f"/api/recordings/{first}/thumbnail")
    assert thumb.status_code == 200 and thumb.content[:2] == b"\xff\xd8"

    kinds = {h["kind"] for h in c.get("/api/history").json()["history"]}
    assert {"recording_uploaded", "signal"} <= kinds

    assert c.delete(f"/api/recordings/{second}").status_code == 200
    assert second not in {r["name"] for r in c.get("/api/recordings").json()["recordings"]}
    assert c.get("/api/inventory").json()["inventory"]["AMOXICILLIN_500MG"]["total_bottles"] == 5
    assert c.delete("/api/recordings/demo_scenario_01").status_code == 409  # bundled fixture
    assert c.delete("/api/recordings/nope").status_code == 404


def test_reset_inventory_restores_opening_stock_and_unapplies_signals(client):
    client.post("/api/replay/control", json={"action": "seek", "media_time_ms": 7000})
    client.post("/api/inventory/receipts", json={
        "medication_key": "AMOXICILLIN_500MG", "bottle_count": 1, "tablets_per_bottle": 10, "expiry_date": "2030-01-01",
    })
    amx = client.get("/api/inventory").json()["inventory"]["AMOXICILLIN_500MG"]
    assert (amx["counter_bottles"], amx["total_bottles"]) == (1, 6)

    assert client.post("/api/inventory/reset").status_code == 200
    state = client.get("/api/inventory").json()
    amx = state["inventory"]["AMOXICILLIN_500MG"]
    assert (amx["counter_bottles"], amx["total_bottles"], amx["pooled_tablets"]) == (0, 5, 500)
    assert not any(e["processed"] for e in state["events"]) and state["activity"] == []
    # The expired opening batch raises its alert again after the reset.
    assert [a["alert_type"] for a in state["alerts"].values()] == ["expiry"]


def test_upload_suggests_a_view_and_saves_edits_as_new_or_replacement(isolated_client, tmp_path, monkeypatch):
    import cv2
    import numpy as np
    import pharma.pose
    from pharma.api import routes

    monkeypatch.setattr(pharma.pose, "extract_video_keypoints", fake_keypoints)
    c = isolated_client
    # Give the default view a photo that matches the test video's look.
    ok, png = cv2.imencode(".png", np.full((180, 320, 3), 200, np.uint8))
    bg = c.post("/api/layouts/default/background", files={"image": ("room.png", png.tobytes(), "image/png")}).json()
    view = c.get("/api/layouts/default").json()
    view.update(background_image=bg["background_image"], frame_width=320, frame_height=180, name="Main camera")
    assert c.put("/api/layouts/default", json=view).status_code == 200

    make_video(tmp_path / "clip.mp4")
    with open(tmp_path / "clip.mp4", "rb") as video:
        res = c.post("/api/recordings", files={"video": ("clip.mp4", video), "events": ("e.csv", b"0.5,pickup\n")},
                     data={"name": "Angle test"})
    body = res.json()
    assert body["layout_id"] == "default" and body["view_suggestions"][0]["layout_id"] == "default"
    assert body["view_suggestions"][0]["same_aspect"] is True
    name = body["name"]
    frame = c.get(f"/api/recordings/{name}/frame")
    assert frame.status_code == 200 and frame.content[:2] == b"\xff\xd8"

    res = c.post(f"/api/recordings/{name}/view", json={"action": "new", "name": "Side angle"})
    assert res.status_code == 200, res.text
    new = res.json()["layout"]
    assert new["layout_id"] == "side-angle" and (new["frame_width"], new["frame_height"]) == (320, 180)
    assert new["background_image"] and len(new["medications"]) == len(view["medications"])
    # Regions come from the room once this camera is registered; a new view starts with none.
    assert new["regions"] == [] and new["regions_source"] is None
    listing = {r["name"]: r for r in c.get("/api/recordings").json()["recordings"]}
    assert listing[name]["layout_id"] == "side-angle" and listing[name]["view_confirmed"] is True
    views = {v["layout_id"]: v for v in c.get("/api/layouts").json()["layouts"]}
    assert set(views) == {"default", "side-angle"} and views["side-angle"]["recordings"][0]["name"] == name

    before = c.get("/api/layouts/default").json()["calibration_version"]
    old_regions = c.get("/api/layouts/default").json()["regions"]
    res = c.post(f"/api/recordings/{name}/view", json={"action": "replace", "layout_id": "default"})
    replaced = res.json()["layout"]
    assert res.status_code == 200 and replaced["calibration_version"] == before + 1
    assert replaced["regions"] == old_regions and replaced["background_image"] != bg["background_image"]
    assert c.post(f"/api/recordings/{name}/view", json={"action": "use", "layout_id": "side-angle"}).status_code == 200
    assert c.post(f"/api/recordings/{name}/view", json={"action": "sideways"}).status_code == 422

    # Signals from a recording apply with its own view's regions.
    assert wait_ready(c, name)["status"] == "ready"
    c.post(f"/api/recordings/{name}/apply")
    assert routes.controller.store.recordings[name]["activity"][0]["layout_id"] == "side-angle"

    res = c.post("/api/layouts/default/background-from-recording", json={"recording": name})
    assert res.status_code == 200 and (res.json()["width"], res.json()["height"]) == (320, 180)


def test_dispose_batch_endpoint_and_expiry_alert(client):
    alerts = client.get("/api/inventory").json()["alerts"].values()
    expiry = next(a for a in alerts if a["alert_type"] == "expiry")
    res = client.post(f"/api/inventory/receipts/{expiry['metadata']['receipt_id']}/dispose", json={"bottles": 3})
    assert res.status_code == 200, res.text
    state = client.get("/api/inventory").json()
    assert state["alerts"][expiry["alert_id"]]["status"] == "resolved"
    assert state["inventory"]["IBUPROFEN_200MG"]["total_bottles"] == 4
    assert client.post("/api/inventory/receipts/REC_IBU_2026_01/dispose", json={"bottles": 1}).status_code == 400
    assert any(h["kind"] == "disposal" for h in client.get("/api/history").json()["history"])


def test_add_and_import_prescriptions(client):
    res = client.post("/api/transactions", json={"medication_key": "IBUPROFEN_200MG", "quantity": 12})
    assert res.status_code == 200 and res.json()["transaction"]["transaction_id"].startswith("RX_")
    assert client.post("/api/transactions", json={"medication_key": "NOPE_1MG", "quantity": 1}).status_code == 400

    csv = b"rx,medication,strength,quantity,status\nRX_9001,Ibuprofen,200mg,20,filled\nRX_9002,Amoxicillin,500 mg,10,\n"
    res = client.post("/api/transactions/import", files={"file": ("rx.csv", csv, "text/csv")})
    assert res.status_code == 200, res.text
    state = client.get("/api/inventory").json()
    assert state["transactions"]["RX_9001"]["deducted"] is True
    assert state["inventory"]["IBUPROFEN_200MG"]["pooled_tablets"] == 700 - 20
    assert state["transactions"]["RX_9002"]["status"] == "created"

    bad = b'[{"medication_key": "IBUPROFEN_200MG", "quantity": 5}, {"medication_key": "IBUPROFEN_200MG", "quantity": 0}]'
    res = client.post("/api/transactions/import", files={"file": ("rx.json", bad)})
    assert res.status_code == 400 and "Row 2" in res.text
    again = client.post("/api/transactions/import", files={"file": ("rx.csv", csv)})
    assert again.status_code == 400 and "already used" in again.text
    assert len(client.get("/api/inventory").json()["transactions"]) == 4  # nothing partial


def test_confirm_pickup_and_put_down_together(isolated_client):
    from pharma.api import routes

    c = isolated_client
    ctrl = routes.controller
    ctrl.engine.regions = {r.region_id: r for r in ctrl.layout.regions}
    far = [(0.45, 0.95, 0.9)]
    ctrl.engine.handle_pickup("demo_scenario_01:s9", far, 0)
    ctrl.engine.handle_release("demo_scenario_01:s9", far, 0)  # held until the pickup is confirmed
    alert = next(a for a in ctrl.engine.alerts.values() if a.alert_type == "uncertainty")
    assert [cand["region_id"] for cand in alert.metadata["candidates"]]
    ctrl.store.save()
    res = c.post(f"/api/inventory/confirmations/{alert.alert_id}", json={
        "resolved_region_id": "shelf_amoxicillin_500mg", "release_region_id": "shelf_ibuprofen_200mg",
    })
    assert res.status_code == 200, res.text
    state = c.get("/api/inventory").json()
    assert not [a for a in state["alerts"].values() if a["alert_type"] == "uncertainty" and a["status"] == "open"]
    assert state["sessions"]["demo_scenario_01:s9"]["state"] == "MISPLACED"


def test_confirmation_updates_the_signal_log(isolated_client, monkeypatch):
    from pharma.api import replay_stream, routes

    c = isolated_client
    ctrl = routes.controller
    monkeypatch.setattr(replay_stream, "synthetic_hand", lambda t: (0.99, 0.01, 0.9))  # far from every region
    c.post("/api/replay/control", json={"action": "seek", "media_time_ms": 10000})
    rows = c.get("/api/inventory").json()["activity"]
    assert rows[0]["state"] == "NEEDS_CONFIRMATION" and rows[1]["held_pending"] is True
    alert = next(a for a in ctrl.engine.alerts.values() if a.alert_type == "uncertainty")
    assert alert.metadata["layout_id"] == "default" and alert.metadata["recording"] == "demo_scenario_01"
    res = c.post(f"/api/inventory/confirmations/{alert.alert_id}", json={
        "resolved_region_id": "shelf_amoxicillin_500mg", "release_region_id": "counter_dispensing_01",
    })
    assert res.status_code == 200, res.text
    rows = c.get("/api/inventory").json()["activity"]
    assert [r["confirmed_region_id"] for r in rows] == ["shelf_amoxicillin_500mg", "counter_dispensing_01"]
    assert all(r["state"] == "AT_COUNTER" and not r["held_pending"] for r in rows)


def test_database_failure_pauses_changes_and_returns_503(client, monkeypatch):
    from pharma.api import routes
    from pymongo.errors import AutoReconnect
    ctrl = routes.controller
    collection = ctrl.store.repository.collection
    real_replace = collection.replace_one
    def unavailable(*args, **kwargs):
        raise AutoReconnect("simulated outage")
    monkeypatch.setattr(collection, "replace_one", unavailable)
    response = client.post('/api/transactions/TX_RX_1001/status', json={'status': 'confirmed_fill'})
    assert response.status_code == 503
    assert ctrl.engine.inventory['AMOXICILLIN_500MG'].pooled_tablets == 500
    assert not ctrl.is_playing
    monkeypatch.setattr(collection, "replace_one", real_replace)
    assert client.get('/api/inventory').json()['inventory']['AMOXICILLIN_500MG']['pooled_tablets'] == 500
    assert client.post('/api/transactions/TX_RX_1001/status', json={'status': 'paid'}).json()['deduction_applied']
    assert client.get('/api/inventory').json()['inventory']['AMOXICILLIN_500MG']['pooled_tablets'] == 470


def test_built_dashboard_serves_assets_and_page_routes(client):
    import re
    from pharma.api.main import dashboard_dist
    if not (dashboard_dist / 'index.html').exists():
        pytest.skip('Build the dashboard to verify its static assets')
    for page in ('/', '/recordings', '/inventory', '/setup'):
        response = client.get(page)
        assert response.status_code == 200
        assert 'id="root"' in response.text
    for asset in re.findall(r'(?:src|href)="(/assets/[^"]+)"', response.text):
        assert client.get(asset).status_code == 200


def test_camera_views_are_created_renamed_and_deleted(client, tmp_path):
    c = client
    made = c.post("/api/layouts", json={"name": "Back room"}).json()["layout"]
    assert made["layout_id"] == "back-room" and made["regions"] == [] and made["background_image"] is None
    ok, png = cv2.imencode(".png", np.zeros((90, 160, 3), np.uint8))
    bg = c.post("/api/layouts/back-room/background", files={"image": ("p.png", png.tobytes(), "image/png")}).json()
    photo = c.patch("/api/layouts/back-room", json={"background_image": bg["background_image"]}).json()["layout"]
    assert (photo["frame_width"], photo["frame_height"]) == (160, 90)
    assert photo["calibration_version"] == made["calibration_version"] + 1
    renamed = c.patch("/api/layouts/back-room", json={"name": "Stock room"}).json()["layout"]
    assert renamed["name"] == "Stock room" and renamed["calibration_version"] == photo["calibration_version"]
    assert c.patch("/api/layouts/back-room", json={"background_image": "background-000000000000.jpg"}).status_code == 400
    assert c.delete("/api/layouts/default").status_code == 409
    assert c.delete("/api/layouts/back-room").status_code == 200
    assert "back-room" not in {v["layout_id"] for v in c.get("/api/layouts").json()["layouts"]}
    assert c.delete("/api/layouts/back-room").status_code == 404


def test_catalog_is_saved_on_its_own(client):
    c = client
    catalog = c.get("/api/catalog").json()
    assert {m["medication_key"] for m in catalog["medications"]} == {"AMOXICILLIN_500MG", "IBUPROFEN_200MG"}
    extra = {"medication_key": "ASPIRIN_81MG", "name": "Aspirin", "strength": "81mg"}
    res = c.put("/api/catalog", json={**catalog, "medications": catalog["medications"] + [extra]})
    assert res.status_code == 200, res.text
    assert "ASPIRIN_81MG" in c.get("/api/inventory").json()["inventory"]
    # The fixture view still has a hand-drawn amoxicillin shelf, so amoxicillin can't go.
    without = [m for m in catalog["medications"] if m["medication_key"] != "AMOXICILLIN_500MG"]
    receipts = [r for r in catalog["receipts"] if r["medication_key"] != "AMOXICILLIN_500MG"]
    blocked = c.put("/api/catalog", json={"medications": without, "receipts": receipts})
    assert blocked.status_code == 422 and "Reassign these shelves" in blocked.json()["detail"]
