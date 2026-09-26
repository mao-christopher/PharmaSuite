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
