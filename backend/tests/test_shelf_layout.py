"""Shelf layouts from shipments: mix-up detection, separation, the Meta Llama planner and apply."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from pharma.api.main import app
from pharma.db.models import RoomBox
from pharma.services import shelf_layout as sl
from tests.test_api import make_controller
from tests.test_room_api import _upload
from tests.test_shipments import FIXTURES


def med(name, strength, bottles=1, shelved=False):
    from pharma.db.models import medication_key_for

    return {"medication_key": medication_key_for(name, strength), "name": name, "strength": strength,
            "unit": "tablets", "bottles": bottles, "bottles_on_hand": bottles, "incoming_bottles": 0,
            "earliest_expiry": None, "in_catalog": True, "shelved": shelved, "shipments": []}


def grid(rows=2, cols=3, units=1):
    """Synthetic shelving: `units` side by side, each `rows` high and `cols` slots wide."""
    slots, cells = [], {}
    for u in range(1, units + 1):
        for r in range(1, rows + 1):
            for p in range(1, cols + 1):
                s = sl.Slot(slot_id=f"u{u}-r{r}-p{p}", unit=u, row=r, position=p,
                            box=RoomBox(center=(u * 3 + p * 0.3, 0.3 + r * 0.4, 0), size=(0.28, 0.35, 0.4)),
                            band=sl._band(0.3 + r * 0.4), counter_m=float(u + p))
                cells[(u, r, p)] = s
                slots.append(s)
    for (u, r, p), s in cells.items():
        s.neighbors = [cells[k].slot_id for k in ((u, r, p - 1), (u, r, p + 1), (u, r - 1, p), (u, r + 1, p))
                       if k in cells]
    return sl.Shelving(slots=slots, rows=[], current={})


def kinds(meds):
    return {frozenset(p["medication_keys"]): p["kind"] for p in sl.risk_pairs(meds)}


# ---------------------------------------------------------------- mix-up detection


@pytest.mark.parametrize("a, b, kind", [
    (("Atorvastatin Calcium", "10mg"), ("Atorvastatin Calcium", "20mg"), "same drug, different strength or form"),
    (("Metoprolol Tartrate", "25mg"), ("Metoprolol Succinate ER", "25mg"), "same drug, different strength or form"),
    (("Metformin HCl", "500mg"), ("Metronidazole", "500mg"), "known mix-up"),
    (("Hydroxyzine HCl", "25mg"), ("Hydralazine HCl", "25mg"), "known mix-up"),
    (("Tramadol HCl", "50mg"), ("Trazodone HCl", "50mg"), "known mix-up"),
    (("Celebrex", "200mg"), ("Celexa", "20mg"), "known mix-up"),
    (("Zyrtek", "10mg"), ("Zyrtec", "10mg"), "sound-alike names"),
    (("Hydrochlorothiazide", "25mg"), ("Hydrocortisone", "10mg"), "look-alike names"),
    (("Lisinopril", "10mg"), ("Enalapril", "10mg"), "same drug class"),
])
def test_mixup_pairs_are_flagged(a, b, kind):
    ma, mb = med(*a), med(*b)
    assert kinds([ma, mb]) == {frozenset((ma["medication_key"], mb["medication_key"])): kind}


def test_unrelated_names_are_not_flagged():
    meds = [med("Amoxicillin", "500mg"), med("Ibuprofen", "200mg"), med("Levothyroxine Sodium", "50mcg"),
            med("Omeprazole DR", "20mg"), med("Sertraline HCl", "50mg")]
    assert sl.risk_pairs(meds) == []


def test_extra_pairs_drop_unknown_duplicate_and_self_pairs():
    meds = [med("Amoxicillin", "500mg"), med("Ibuprofen", "200mg"), med("Atorvastatin", "10mg"),
            med("Atorvastatin", "20mg")]
    known = sl.risk_pairs(meds)
    keys = [m["medication_key"] for m in meds]
    proposed = [
        {"medication_keys": [keys[1], keys[0]], "kind": "sound-alike names", "reason": "Test pair."},
        {"medication_keys": [keys[2], keys[3]], "kind": "known mix-up", "reason": "Already known."},
        {"medication_keys": [keys[0], keys[0]], "kind": "known mix-up", "reason": "Self."},
        {"medication_keys": [keys[0], "NOT_A_MED"], "kind": "known mix-up", "reason": "Unknown."},
        {"medication_keys": [keys[0]], "kind": "known mix-up", "reason": "Short."},
        "garbage",
    ]
    extra = sl.extra_pairs(proposed, meds, known, "meta-llama")
    assert [p["medication_keys"] for p in extra] == [sorted(keys[:2])]
    assert extra[0]["kind"] == "sound-alike names" and extra[0]["source"] == "meta-llama"


# ---------------------------------------------------------------- built-in planner


def test_builtin_puts_every_pair_on_different_shelves():
    meds = [med("Atorvastatin", "10mg", 9), med("Atorvastatin", "20mg", 8), med("Metformin HCl", "500mg", 7),
            med("Metronidazole", "500mg", 6), med("Amoxicillin", "500mg", 5)]
    shelf = grid(rows=2, cols=3)
    risks = sl.risk_pairs(meds)
    assert len(risks) == 2
    plan = sl.builtin_plan(meds, shelf, risks)
    assert sl.conflicts(risks, plan, shelf.by_id()) == []
    assert len(set(plan.values())) == len(meds)
    placed = sl.separation(risks, plan, shelf.by_id())
    assert all(p["separation"] == "different shelves, same unit" and not p["warning"] for p in placed)


def test_builtin_prefers_another_unit_when_there_is_room():
    meds = [med("Atorvastatin", "10mg"), med("Atorvastatin", "20mg")]
    shelf = grid(rows=2, cols=2, units=2)
    risks = sl.risk_pairs(meds)
    plan = sl.builtin_plan(meds, shelf, risks)
    assert [p["separation"] for p in sl.separation(risks, plan, shelf.by_id())] == ["different units"]


def test_builtin_keeps_shelved_medications_unless_they_clash():
    meds = [med("Atorvastatin", "10mg", 9, shelved=True), med("Atorvastatin", "20mg", 3, shelved=True),
            med("Amoxicillin", "500mg", 5, shelved=True)]
    shelf = grid(rows=2, cols=3)
    a10, a20, amx = (m["medication_key"] for m in meds)
    shelf.current = {a10: ["u1-r1-p1"], a20: ["u1-r1-p2"], amx: ["u1-r1-p3"]}
    risks = sl.risk_pairs(meds)
    plan = sl.builtin_plan(meds, shelf, risks)
    assert plan[a10] == "u1-r1-p1" and plan[amx] == "u1-r1-p3"  # the busier one and the unrelated one stay
    assert plan[a20].startswith("u1-r2") and plan[a20] != "u1-r2-p1"  # moved off the row, not directly above
    assert sl.conflicts(risks, plan, shelf.by_id()) == []


def test_single_shelf_reports_pairs_it_cannot_separate(tmp_path, monkeypatch):
    monkeypatch.delenv("META_API_KEY", raising=False)
    monkeypatch.delenv("LLAMA_API_KEY", raising=False)
    meds = [med("Atorvastatin", "10mg"), med("Atorvastatin", "20mg")]
    shelf = grid(rows=1, cols=3)
    monkeypatch.setattr(sl, "shelving", lambda *_: shelf)
    monkeypatch.setattr(sl, "demand", lambda *_: meds)
    plan = sl.plan_layout(tmp_path, _room_stub(), None, {}, {})
    assert plan["source"] == "built-in" and plan["unresolved_risks"] == 1
    assert plan["risks"][0]["warning"] and plan["risks"][0]["separation"] == "same shelf"
    assert any("could not be put on different shelves" in n for n in plan["notes"])


# ---------------------------------------------------------------- Meta Llama planner


def _room_stub():
    class Stub:
        room_id, room_version = "front-room", 3

    return Stub()


def _llama(answer=None, status=200):
    """An httpx client whose Llama API answers with `answer`, recording each request."""
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        if status != 200:
            return httpx.Response(status, json={"detail": "nope"})
        return httpx.Response(200, json={"completion_message": {"role": "assistant", "stop_reason": "stop",
                                                                "content": {"type": "text", "text": json.dumps(answer)}}})

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


@pytest.fixture
def planned(tmp_path, monkeypatch):
    """plan_layout over synthetic shelving with a token set; call with a mock client."""
    monkeypatch.setenv("META_API_KEY", "test-token")
    meds = [med("Atorvastatin", "10mg", 9), med("Atorvastatin", "20mg", 8), med("Amoxicillin", "500mg", 7),
            med("Ibuprofen", "200mg", 6)]
    shelf = grid(rows=2, cols=3)
    monkeypatch.setattr(sl, "shelving", lambda *_: shelf)
    monkeypatch.setattr(sl, "demand", lambda *_: meds)
    keys = [m["medication_key"] for m in meds]
    return (lambda client: sl.plan_layout(tmp_path, _room_stub(), None, {}, {}, client=client)), keys


def test_meta_plan_is_used_and_its_mixups_are_kept_apart(planned):
    run, (a10, a20, amx, ibu) = planned
    answer = {
        "mixup_pairs": [{"medication_keys": [amx, ibu], "kind": "look-alike names", "reason": "Test pair."}],
        "assignments": [
            {"medication_key": a10, "slot_id": "u1-r2-p1", "reason": "Busiest, eye height."},
            {"medication_key": a20, "slot_id": "u1-r1-p3", "reason": "Other shelf from the 10 mg."},
            {"medication_key": amx, "slot_id": "u1-r2-p3", "reason": "Away from ibuprofen."},
            {"medication_key": ibu, "slot_id": "u1-r1-p1", "reason": "Away from amoxicillin."},
        ],
        "notes": ["Two strengths of atorvastatin split across shelves."],
    }
    client, seen = _llama(answer)
    plan = run(client)
    assert plan["source"] == "meta-llama" and plan["fallback_reason"] is None
    assert {tuple(r["medication_keys"]): r["source"] for r in plan["risks"]} == {
        (a10, a20): "built-in", tuple(sorted((amx, ibu))): "meta-llama"}
    assert plan["unresolved_risks"] == 0
    assert {a["medication_key"]: a["reason"] for a in plan["assignments"]}[a20] == "Other shelf from the 10 mg."
    sent = seen[0]
    assert sent["response_format"]["json_schema"]["name"] == "shelf_layout"
    context = json.loads(sent["messages"][1]["content"])
    assert context["risk_pairs"][0]["medication_keys"] == [a10, a20]
    assert {s["shelf_id"] for s in context["slots"]} == {"u1-r1", "u1-r2"}
    assert "different shelves" in sent["messages"][0]["content"]


def test_meta_plan_that_leaves_a_pair_together_falls_back(planned):
    run, (a10, a20, amx, ibu) = planned
    answer = {
        "mixup_pairs": [{"medication_keys": [amx, ibu], "kind": "look-alike names", "reason": "Test pair."}],
        "assignments": [  # the model's own pair lands side by side
            {"medication_key": a10, "slot_id": "u1-r2-p1", "reason": "."},
            {"medication_key": a20, "slot_id": "u1-r1-p3", "reason": "."},
            {"medication_key": amx, "slot_id": "u1-r2-p2", "reason": "."},
            {"medication_key": ibu, "slot_id": "u1-r2-p3", "reason": "."},
        ],
    }
    plan = run(_llama(answer)[0])
    assert plan["source"] == "built-in" and "on one shelf or side by side" in plan["fallback_reason"]
    assert plan["unresolved_risks"] == 0 and len(plan["risks"]) == 2  # the fallback separates the model's pair too


@pytest.mark.parametrize("answer, status, reason", [
    (None, 401, "rejected the token"),
    (None, 500, "HTTP 500"),
    ({"mixup_pairs": [], "assignments": [{"medication_key": "X", "slot_id": "u1-r1-p1", "reason": "."}]}, 200,
     "Every medication needs exactly one slot"),
])
def test_unusable_meta_answers_fall_back(planned, answer, status, reason):
    run, _ = planned
    plan = run(_llama(answer, status)[0])
    assert plan["source"] == "built-in" and reason in plan["fallback_reason"]
    assert plan["unresolved_risks"] == 0


def test_no_token_uses_builtin(planned, monkeypatch):
    run, _ = planned
    monkeypatch.delenv("META_API_KEY")
    client, seen = _llama({})
    plan = run(client)
    assert plan["source"] == "built-in" and "No Meta API token" in plan["fallback_reason"] and not seen


# ---------------------------------------------------------------- API


@pytest.fixture
def client(tmp_path, monkeypatch):
    from pharma.api import routes

    monkeypatch.delenv("META_API_KEY", raising=False)
    monkeypatch.delenv("LLAMA_API_KEY", raising=False)
    with TestClient(app) as c:
        routes.controller = make_controller(tmp_path)
        yield c


def _tag_shelving(client, tmp_path, upload=True):
    if upload:
        _upload(client, tmp_path)
    shelves = [{"region_id": "a", "region_type": "designated_shelf", "medication_key": key,
                "box": {"center": [0, y, -2.0], "size": [1.6, 0.35, 0.4], "yaw_deg": 0}}
               for key, y in (("AMOXICILLIN_500MG", 0.5), ("IBUPROFEN_200MG", 1.2))]
    counter = {"region_id": "counter", "region_type": "dispensing_counter",
               "box": {"center": [-1.8, 0.95, 0.5], "size": [0.6, 0.1, 1.2], "yaw_deg": 0}}
    res = client.put("/api/rooms/front-room/regions", json={"regions": shelves + [counter]})
    assert res.status_code == 200, res.text


def _import_shipment(client):
    data = json.loads((FIXTURES / "shipments_manifest.json").read_text())["shipments"][0]
    raw = json.dumps(data).encode()
    res = client.post("/api/shipments/import", files={"file": ("s.json", raw, "application/json")})
    assert res.status_code == 200, res.text


def _apply_body(plan, **extra):
    return {"room_id": plan["room_id"], "room_version": plan["room_version"], "source": plan["source"],
            "assignments": [{"medication_key": a["medication_key"], "slot_id": a["slot_id"]}
                            for a in plan["assignments"]], **extra}


def test_plan_and_apply_from_a_shipment(client, tmp_path):
    from pharma.api import routes

    _tag_shelving(client, tmp_path)
    status = client.get("/api/shelf-layout/status").json()
    assert status["meta"] == {"configured": False, "model": sl.meta_llama.DEFAULT_MODEL}
    assert status["rooms"][0]["shelves"] == 2
    _import_shipment(client)
    ctrl = routes.controller
    stock_before = {k: v.total_bottles for k, v in ctrl.engine.inventory.items()}

    plan = client.post("/api/shelf-layout/plan", json={"room_id": "front-room"}).json()
    by_key = {a["medication_key"]: a for a in plan["assignments"]}
    assert set(by_key) == {"AMOXICILLIN_500MG", "IBUPROFEN_200MG", "LEVOTHYROXINE_SODIUM_50MCG", "METFORMIN_HCL_500MG"}
    assert plan["slots_total"] == 12 and plan["source"] == "built-in"
    assert by_key["AMOXICILLIN_500MG"]["status"] == "kept" and by_key["METFORMIN_HCL_500MG"]["status"] == "new"
    assert not by_key["METFORMIN_HCL_500MG"]["in_catalog"]
    assert plan["risks"] == []

    res = client.post("/api/shelf-layout/apply", json=_apply_body(plan))
    assert res.status_code == 200, res.text
    applied = res.json()
    assert sorted(applied["added_medications"]) == ["LEVOTHYROXINE_SODIUM_50MCG", "METFORMIN_HCL_500MG"]
    shelves = {r["medication_key"]: r for r in applied["regions"] if r["region_type"] == "designated_shelf"}
    assert set(shelves) == set(by_key)
    assert any(r["region_type"] == "dispensing_counter" for r in applied["regions"])
    assert {m.medication_key for m in ctrl.catalog.medications} >= set(by_key)
    assert {k: v.total_bottles for k, v in ctrl.engine.inventory.items() if k in stock_before} == stock_before
    assert (tmp_path / "rooms" / "front-room" / sl.SHELVING_FILE).exists()

    # Replanning the applied room keeps everything where it is, with the empty slots still there.
    again = client.post("/api/shelf-layout/plan", json={"room_id": "front-room"}).json()
    assert again["slots_total"] == 12
    assert {a["medication_key"]: (a["status"], a["slot_id"]) for a in again["assignments"]} == {
        k: ("kept", a["slot_id"]) for k, a in by_key.items()}
    stale = client.post("/api/shelf-layout/apply", json=_apply_body(plan))
    assert stale.status_code == 409 and "Plan again" in stale.json()["detail"]


def test_apply_needs_confirmation_to_leave_a_mixup_together(client, tmp_path):
    _tag_shelving(client, tmp_path)
    plan = client.post("/api/shelf-layout/plan", json={"room_id": "front-room"}).json()
    body = _apply_body(plan, mixup_pairs=[{"medication_keys": ["AMOXICILLIN_500MG", "IBUPROFEN_200MG"]}])
    body["assignments"] = [{"medication_key": "AMOXICILLIN_500MG", "slot_id": "u1-r1-p1"},
                           {"medication_key": "IBUPROFEN_200MG", "slot_id": "u1-r1-p2"}]
    res = client.post("/api/shelf-layout/apply", json=body)
    assert res.status_code == 409 and "Amoxicillin 500mg / Ibuprofen 200mg" in res.json()["detail"]
    res = client.post("/api/shelf-layout/apply", json={**body, "acknowledge_mixups": True})
    assert res.status_code == 200 and res.json()["mixups_together"] == [["AMOXICILLIN_500MG", "IBUPROFEN_200MG"]]


def test_plan_needs_shelving_and_apply_rejects_bad_slots(client, tmp_path):
    _upload(client, tmp_path)
    res = client.post("/api/shelf-layout/plan", json={"room_id": "front-room"})
    assert res.status_code == 422 and "Tag at least one shelf" in res.json()["detail"]
    _tag_shelving(client, tmp_path, upload=False)
    plan = client.post("/api/shelf-layout/plan", json={"room_id": "front-room"}).json()
    body = _apply_body(plan)
    body["assignments"][0]["slot_id"] = "u9-r9-p9"
    assert client.post("/api/shelf-layout/apply", json=body).status_code == 422
    assert client.post("/api/shelf-layout/plan", json={"room_id": "nope"}).status_code == 404


def test_lookalike_delivery_lands_on_different_shelves(client, tmp_path):
    _tag_shelving(client, tmp_path)
    raw = (FIXTURES / "mixups" / "lookalike_delivery.json").read_bytes()
    assert client.post("/api/shipments/import", files={"file": ("lookalike_delivery.json", raw)}).status_code == 200
    plan = client.post("/api/shelf-layout/plan", json={"room_id": "front-room"}).json()
    pairs = {frozenset(r["medication_keys"]): r for r in plan["risks"]}
    assert set(pairs) == {frozenset(p) for p in [
        ("HYDROXYZINE_HCL_25MG", "HYDRALAZINE_HCL_25MG"), ("METFORMIN_HCL_500MG", "METRONIDAZOLE_500MG"),
        ("TRAMADOL_HCL_50MG", "TRAZODONE_HCL_50MG"), ("ATORVASTATIN_CALCIUM_20MG", "ATORVASTATIN_CALCIUM_40MG")]}
    assert plan["unresolved_risks"] == 0
    assert all(r["separation"] in ("different shelves, same unit", "different units") for r in pairs.values())
    assert client.post("/api/shelf-layout/apply", json=_apply_body(plan)).status_code == 200
