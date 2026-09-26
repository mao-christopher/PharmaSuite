"""Proactive stock suggestions: low stock, last bottle, run-out forecast and expiring batches."""

from datetime import date, datetime, timedelta, timezone

from pharma.services.forecast import reorder_point, stock_suggestions
from tests.test_api import client, make_controller  # noqa: F401 (client is a fixture)

TODAY = datetime.now(timezone.utc).date().isoformat()


def kinds(suggestions, med=None):
    return sorted(s["kind"] for s in suggestions if med is None or s["medication_key"] == med)


def test_default_reorder_point_is_a_fifth_of_opening_stock(tmp_path):
    ctrl = make_controller(tmp_path)
    amx = next(m for m in ctrl.catalog.medications if m.medication_key == "AMOXICILLIN_500MG")
    assert reorder_point(amx, ctrl.catalog) == 100
    assert reorder_point(amx.model_copy(update={"reorder_point": 20}), ctrl.catalog) == 20


def test_low_stock_and_runout_forecast_from_recent_prescriptions(tmp_path):
    ctrl = make_controller(tmp_path)
    engine = ctrl.engine
    assert kinds(stock_suggestions(engine, ctrl.catalog, TODAY), "AMOXICILLIN_500MG") == []
    engine.add_transaction("AMOXICILLIN_500MG", 420, "RX_BIG", "confirmed_fill")  # 80 left
    found = stock_suggestions(engine, ctrl.catalog, TODAY)
    assert kinds(found, "AMOXICILLIN_500MG") == ["low_stock", "runout"]
    runout = next(s for s in found if s["kind"] == "runout")
    assert runout["daily_usage"] == 30.0 and runout["days_left"] == 2.7  # 420 over 14 days
    # Usage older than the window no longer drives a forecast.
    engine.transactions["RX_BIG"].deducted_at = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    assert kinds(stock_suggestions(engine, ctrl.catalog, TODAY), "AMOXICILLIN_500MG") == ["low_stock"]


def test_last_bottle_replaces_low_stock(tmp_path):
    ctrl = make_controller(tmp_path)
    inv = ctrl.engine.inventory["AMOXICILLIN_500MG"]
    inv.total_bottles, inv.pooled_tablets = 1, 40
    assert kinds(stock_suggestions(ctrl.engine, ctrl.catalog, TODAY), "AMOXICILLIN_500MG") == ["last_bottle"]
    inv.total_bottles, inv.pooled_tablets = 0, 0
    assert kinds(stock_suggestions(ctrl.engine, ctrl.catalog, TODAY), "AMOXICILLIN_500MG") == []  # an alert's job


def test_expiring_batch_warns_and_says_to_order_when_it_is_the_last_stock(tmp_path):
    ctrl = make_controller(tmp_path)
    expiry = date.fromisoformat("2027-06-30")
    month = stock_suggestions(ctrl.engine, ctrl.catalog, (expiry - timedelta(days=20)).isoformat())
    soon = next(s for s in month if s["kind"] == "expiring_soon" and s["medication_key"] == "AMOXICILLIN_500MG")
    # All five Amoxicillin bottles are in that batch, so nothing would be left.
    assert soon["severity"] == "warning" and soon["bottles_left_after"] == 0 and soon["action"] == "receive"
    assert "order more" in soon["message"] and soon["id"].endswith(":month")
    week = stock_suggestions(ctrl.engine, ctrl.catalog, (expiry - timedelta(days=3)).isoformat())
    assert any(s["id"] == "expiring:REC_AMX_2026_01:week" for s in week)  # escalation is a new suggestion
    assert not any(s["kind"] == "expiring_soon" and s["medication_key"] == "AMOXICILLIN_500MG"
                   for s in stock_suggestions(ctrl.engine, ctrl.catalog, (expiry + timedelta(days=1)).isoformat()))


def test_other_batches_remaining_lower_the_urgency(tmp_path):
    ctrl = make_controller(tmp_path)
    # Ibuprofen: 3 bottles expired 2026-08-31, 4 more expiring 2027-12-31. Check the later batch.
    found = stock_suggestions(ctrl.engine, ctrl.catalog, "2027-12-01")
    later = next(s for s in found if s["receipt_id"] == "REC_IBU_2026_02")
    # The expired batch still counts against what would be left, so nothing usable remains.
    assert later["bottles_left_after"] == 0
    ctrl.engine.receipts["REC_IBU_2026_01"].remaining_bottles = 0
    ctrl.engine.inventory["IBUPROFEN_200MG"].total_bottles = 5
    found = stock_suggestions(ctrl.engine, ctrl.catalog, "2027-12-01")
    later = next(s for s in found if s["receipt_id"] == "REC_IBU_2026_02")
    assert later["bottles_left_after"] == 1 and "Only one other bottle" in later["message"]


def test_dismissed_suggestions_stay_hidden_until_restock(client):
    from pharma.api import routes

    c = client
    c.post("/api/transactions", json={"medication_key": "AMOXICILLIN_500MG", "quantity": 420, "status": "paid"})
    suggestions = c.get("/api/inventory").json()["suggestions"]
    low = next(s for s in suggestions if s["kind"] == "low_stock")
    assert c.post(f"/api/suggestions/{low['id']}/dismiss").status_code == 200
    assert low["id"] not in {s["id"] for s in c.get("/api/inventory").json()["suggestions"]}
    assert c.post(f"/api/suggestions/{low['id']}/dismiss").status_code == 404
    # Persisted with the pharmacy state, so it survives a reload from MongoDB.
    routes.controller.store.refresh()
    assert low["id"] in routes.controller.store.dismissed_suggestions
    # Receiving a batch is a new situation: the suggestion comes back if stock is still low.
    c.post("/api/inventory/receipts", json={
        "medication_key": "AMOXICILLIN_500MG", "bottle_count": 1, "tablets_per_bottle": 5, "expiry_date": "2030-01-01",
    })
    assert any(s["kind"] == "low_stock" for s in c.get("/api/inventory").json()["suggestions"])
    assert c.delete("/api/suggestions/dismissed").json()["count"] == 1
