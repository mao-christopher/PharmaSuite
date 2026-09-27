"""Electronic supplier parity, strict intake, replay-linked stocking and durability."""

import copy
import json
from pathlib import Path

import pytest
from pharma.services import shipment_import as importer, stocking
from tests.test_api import client, make_controller  # noqa: F401 (client is a fixture)

FIXTURES = Path(__file__).resolve().parents[2] / "demo/synthetic_shipments"
AT = "2026-09-27T00:00:00+00:00"


def test_every_supplier_matches_manifest():
    canonical = importer.parse_shipments(
        "manifest.json", (FIXTURES / "shipments_manifest.json").read_bytes()
    )
    expected = {importer.shipment_id(s): importer.content_key(s) for s in canonical}
    seen = set()
    for ext in ("json", "xml", "csv", "edi"):
        for path in (FIXTURES / ext).glob("*"):
            for s in importer.parse_shipments(path.name, path.read_bytes()):
                key = importer.shipment_id(s)
                assert importer.content_key(s) == expected[key]
                seen.add(key)
    assert len(seen) == len(expected) == 8


def one_shipment(count=2):
    data = json.loads((FIXTURES / "shipments_manifest.json").read_text())["shipments"][0]
    data["lines"] = data["lines"][:1]
    r = data["lines"][0]
    r.update(
        bottles_ordered=count,
        bottles_shipped=count,
        bottles_accepted=count,
        total_units_shipped=count * r["units_per_bottle"],
        total_units_accepted=count * r["units_per_bottle"],
    )
    return data


def import_one(engine, count=2):
    raw = json.dumps(one_shipment(count)).encode()
    rows = importer.parse_shipments("test.json", raw)
    return importer.import_shipments(engine, rows, "test.json", raw, AT)[0]["id"]


def event(kind, n, session="one"):
    return {
        "event_id": f"{kind}-{n}",
        "session_id": session,
        "event_type": kind,
        "media_time_ms": n * 100,
        "timestamp": n,
    }


def setup_stocking(tmp_path, count=2):
    ctrl = make_controller(tmp_path)
    ctrl.load_scenario("demo_scenario_01")
    key = import_one(ctrl.engine, count)
    stocking.start(ctrl.engine, key, ctrl.current.name, ctrl.layout.regions, AT)
    return ctrl, key


def apply(ctrl, e, hands):
    return ctrl.store.apply_event(
        ctrl.current.name, e, hands, (1280, 720), regions=ctrl.layout.regions
    )


def point(ctrl, key):
    r = next(r for r in ctrl.layout.regions if r.medication_key == key)
    return [
        (
            sum(p[0] for p in r.polygon) / len(r.polygon),
            sum(p[1] for p in r.polygon) / len(r.polygon),
            0.99,
        )
    ]


def test_import_duplicate_conflict_atomic_and_no_stock_change(tmp_path):
    ctrl = make_controller(tmp_path)
    before = copy.deepcopy(ctrl.engine.to_dict()["inventory"])
    key = import_one(ctrl.engine)
    assert import_one(ctrl.engine) == key
    assert ctrl.engine.to_dict()["inventory"] == before
    with pytest.raises(ValueError, match="different lines"):
        import_one(ctrl.engine, 3)
    assert len(ctrl.engine.shipments) == 1
    raw = json.loads((FIXTURES / "shipments_manifest.json").read_text())
    with pytest.raises(ValueError):
        importer.import_shipments(
            ctrl.engine,
            importer.parse_shipments("m.json", json.dumps(raw).encode()),
            "m.json",
            b"raw",
            AT,
        )
    assert len(ctrl.engine.shipments) == 1


@pytest.mark.parametrize(
    "extension,content",
    [
        ("pdf", b"%PDF"),
        ("xml", b'<!DOCTYPE a [<!ENTITY x "boom">]><a/>'),
        ("json", b"{}"),
        ("json", b"["),
        ("csv", b"wrong\nheader"),
        ("json", b"x" * 2_000_001),
    ],
)
def test_reject_bad_documents(extension, content):
    with pytest.raises(ValueError):
        importer.parse_shipments("input." + extension, content)


@pytest.mark.parametrize(
    "field,value",
    [
        ("bottles_shipped", 1.5),
        ("bottles_damaged", 9),
        ("total_units_accepted", 1),
        ("expiry_date", "2026-02-30"),
        ("medication_key", "WRONG"),
    ],
)
def test_reject_bad_lines(field, value):
    s = one_shipment()
    s["lines"][0][field] = value
    with pytest.raises(ValueError):
        importer.parse_shipments("x.json", json.dumps(s).encode())


def test_staged_placement_duplicate_restart_and_shortage(tmp_path):
    ctrl, key = setup_stocking(tmp_path)
    med = "AMOXICILLIN_500MG"
    inv = ctrl.engine.inventory[med]
    before = inv.model_dump()
    assert inv.staged_bottles == 2
    stocking.start(ctrl.engine, key, ctrl.current.name, ctrl.layout.regions, AT)
    assert inv.model_dump() == before
    e = event("pickup", 1)
    with pytest.raises(ValueError):
        apply(ctrl, e, [])
    stocking.arm(ctrl.engine, key, "1", e["event_id"])
    apply(ctrl, e, [])
    apply(ctrl, event("release", 2), point(ctrl, med))
    assert apply(ctrl, e, []) is None
    assert stocking.report(ctrl.engine, ctrl.engine.shipments[key])["lines"][0]["correct"] == 1
    assert (
        inv.total_bottles == before["total_bottles"]
        and inv.pooled_tablets == before["pooled_tablets"]
    )
    ctrl.store.save()
    ctrl.store.refresh()
    assert ctrl.engine.shipments[key]["status"] == "stocking"
    with pytest.raises(ValueError):
        stocking.finish(ctrl.engine, key, AT)
    stocking.shortage(ctrl.engine, key, "1", 1, "Not delivered", "fix-1", AT)
    after = copy.deepcopy(ctrl.engine.to_dict()["inventory"])
    stocking.shortage(ctrl.engine, key, "1", 1, "Not delivered", "fix-1", AT)
    assert ctrl.engine.to_dict()["inventory"] == after
    assert stocking.finish(ctrl.engine, key, AT)["status"] == "completed_with_discrepancies"
    assert stocking.finish(ctrl.engine, key, AT)["status"] == "completed_with_discrepancies"


def test_uncertain_release_and_wrong_shelf_correction(tmp_path):
    ctrl, key = setup_stocking(tmp_path, 1)
    stocking.arm(ctrl.engine, key, "1", "pickup-1")
    apply(ctrl, event("pickup", 1), [])
    apply(ctrl, event("release", 2), [])
    with pytest.raises(ValueError):
        stocking.arm(ctrl.engine, key, "1", "pickup-3")
    alert = next(a for a in ctrl.engine.alerts.values() if a.alert_type == "uncertainty")
    other = next(
        r
        for r in ctrl.layout.regions
        if r.region_type == "designated_shelf" and r.medication_key != "AMOXICILLIN_500MG"
    )
    ctrl.engine.confirm_location(alert.alert_id, other.region_id)
    assert stocking.report(ctrl.engine, ctrl.engine.shipments[key])["lines"][0]["unresolved"] == 1
    # A low-confidence corrective pickup keeps the original shipment/lot identity.
    apply(ctrl, event("pickup", 3, "correction"), [])
    alert = next(
        a
        for a in ctrl.engine.alerts.values()
        if a.alert_type == "uncertainty" and a.status == "open"
    )
    ctrl.engine.confirm_location(alert.alert_id, other.region_id)
    apply(ctrl, event("release", 4, "correction"), point(ctrl, "AMOXICILLIN_500MG"))
    assert stocking.finish(ctrl.engine, key, AT)["status"] == "completed"
    inv = ctrl.engine.inventory["AMOXICILLIN_500MG"]
    assert inv.held_bottles == inv.staged_bottles == 0
    assert inv.shelf_counts.get(other.region_id, 0) == 0


def test_import_api_preview_and_start(client):
    from pharma.api import routes

    raw = json.dumps(one_shipment()).encode()
    res = client.post(
        "/api/shipments/import?preview=true", files={"file": ("s.json", raw, "application/json")}
    )
    assert res.status_code == 200 and not routes.controller.engine.shipments
    res = client.post("/api/shipments/import", files={"file": ("s.json", raw, "application/json")})
    assert res.status_code == 200
    key = res.json()["results"][0]["id"]
    assert client.post(f"/api/shipments/{key}/start").status_code == 200
    assert client.get("/api/shipments").json()["shipments"][0]["report"]["lines"][0]["staged"] == 2
    assert client.post(f"/api/shipments/{key}/finish").status_code == 400
    assert (
        client.post(
            f"/api/shipments/{key}/select", json={"line_id": "1", "event_id": "not-a-real-event"}
        ).status_code
        == 400
    )


def test_supplier_files_after_manifest_do_not_receive_twice(tmp_path):
    ctrl = make_controller(tmp_path)
    raw = (FIXTURES / "shipments_manifest.json").read_bytes()
    importer.import_shipments(
        ctrl.engine, importer.parse_shipments("all.json", raw), "all.json", raw, AT
    )
    for ext in ("json", "csv", "xml", "edi"):
        for p in (FIXTURES / ext).glob("*"):
            result = importer.import_shipments(
                ctrl.engine,
                importer.parse_shipments(p.name, p.read_bytes()),
                p.name,
                p.read_bytes(),
                AT,
            )
            assert all(r["duplicate"] for r in result)
    assert len(ctrl.engine.shipments) == 8


def test_clock_waits_for_line_then_uses_pose_release(tmp_path):
    from types import SimpleNamespace

    ctrl, key = setup_stocking(tmp_path, 1)
    inv = ctrl.engine.inventory["AMOXICILLIN_500MG"]
    start = inv.shelf_counts["shelf_amoxicillin_500mg"]
    ctrl.current.locate = lambda ms: SimpleNamespace(
        points=point(ctrl, "AMOXICILLIN_500MG"), camera_id=None, joint="wrist", offset_ms=0
    )
    ctrl.is_playing = True
    assert ctrl.process_events_until(6000) == 0
    assert not ctrl.is_playing and "evt_001" not in ctrl.processed_event_ids
    stocking.arm(ctrl.engine, key, "1", "evt_001")
    assert ctrl.process_events_until(6000) == 2
    assert inv.staged_bottles == 0 and inv.shelf_counts["shelf_amoxicillin_500mg"] == start + 1
    assert stocking.finish(ctrl.engine, key, AT)["status"] == "completed"
    # Snapshot of reconciliation remains stable after later ordinary movement.
    frozen = copy.deepcopy(stocking.report(ctrl.engine, ctrl.engine.shipments[key]))
    ctrl.engine.sessions[f"{ctrl.current.name}:sess_001"].state = "DISPOSED"
    assert stocking.report(ctrl.engine, ctrl.engine.shipments[key]) == frozen


def test_counter_continuity_and_confirmed_disposal(tmp_path):
    ctrl, key = setup_stocking(tmp_path, 1)
    med = "AMOXICILLIN_500MG"
    stocking.arm(ctrl.engine, key, "1", "pickup-1")
    apply(ctrl, event("pickup", 1), [])
    apply(ctrl, event("release", 2), [(0.65, 0.25, 0.99)])
    assert ctrl.engine.inventory[med].counter_bottles == 1
    apply(ctrl, event("pickup", 3, "counter-return"), [(0.65, 0.25, 0.99)])
    apply(ctrl, event("release", 4, "counter-return"), [(0.92, 0.82, 0.99)])
    with pytest.raises(ValueError):
        stocking.finish(ctrl.engine, key, AT)
    disp = next(iter(ctrl.engine.disposals.values()))
    wrong = next(r.receipt_id for r in ctrl.engine.receipts.values() if r.medication_key == med)
    with pytest.raises(ValueError, match="shipment lot"):
        ctrl.engine.resolve_disposal(disp.disposal_id, wrong, 500)
    correct = ctrl.engine.shipments[key]["stocking"]["lines"]["1"]["receipt_id"]
    ctrl.engine.resolve_disposal(disp.disposal_id, correct, 500)
    assert stocking.finish(ctrl.engine, key, AT)["status"] == "completed_with_discrepancies"
    assert stocking.report(ctrl.engine, ctrl.engine.shipments[key])["lines"][0]["disposed"] == 1
    inv = ctrl.engine.inventory[med]
    assert inv.staged_bottles == inv.counter_bottles == inv.held_bottles == 0


def test_active_stocking_blocks_destructive_changes(client):
    raw = json.dumps(one_shipment()).encode()
    key = client.post("/api/shipments/import", files={"file": ("s.json", raw)}).json()["results"][
        0
    ]["id"]
    assert client.post(f"/api/shipments/{key}/start").status_code == 200
    assert client.post("/api/inventory/reset").status_code == 409
    assert client.put("/api/catalog", json={}).status_code == 409
    assert client.delete("/api/recordings/demo_scenario_01").status_code == 409
    assert client.post("/api/recordings/demo_scenario_01/load").status_code == 200


def test_unknown_medications_block_start_without_partial_receipts(tmp_path):
    ctrl = make_controller(tmp_path)
    ctrl.load_scenario("demo_scenario_01")
    raw = (FIXTURES / "shipments_manifest.json").read_bytes()
    key = importer.import_shipments(
        ctrl.engine, importer.parse_shipments("a.json", raw), "a.json", raw, AT
    )[0]["id"]
    before = copy.deepcopy(ctrl.engine.to_dict())
    with pytest.raises(ValueError, match="Configure"):
        stocking.start(ctrl.engine, key, ctrl.current.name, ctrl.layout.regions, AT)
    assert ctrl.engine.to_dict() == before


def test_stocking_rollback_on_failed_mongo_write(tmp_path, monkeypatch):
    from pharma.db.repository import StorageUnavailable

    ctrl = make_controller(tmp_path)
    ctrl.load_scenario("demo_scenario_01")
    key = import_one(ctrl.engine, 1)
    ctrl.store.save()
    before = copy.deepcopy(ctrl.engine.to_dict())
    stocking.start(ctrl.engine, key, ctrl.current.name, ctrl.layout.regions, AT)

    def fail(*a, **k):
        raise StorageUnavailable("test outage")

    monkeypatch.setattr(ctrl.store.repository, "save", fail)
    with pytest.raises(StorageUnavailable):
        ctrl.store.save()
    assert ctrl.engine.to_dict() == before
