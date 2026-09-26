"""Comprehensive pytest suite for Pharma InventoryEngine rules and state machine."""

import pytest
from pharma.db.models import (
    InventoryState,
    Region,
    Receipt,
    PrescriptionTransaction,
)
from pharma.services.inventory_engine import MAX_REGION_DISTANCE, InventoryEngine, nearest_region


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


@pytest.fixture
def base_setup():
    regions = [
        Region(
            region_id="shelf_amoxicillin_500mg",
            region_type="designated_shelf",
            medication_key="AMOXICILLIN_500MG",
            polygon=rect(0.1, 0.1, 0.4, 0.4),
        ),
        Region(
            region_id="shelf_ibuprofen_200mg",
            region_type="designated_shelf",
            medication_key="IBUPROFEN_200MG",
            polygon=rect(0.1, 0.5, 0.4, 0.8),
        ),
        Region(
            region_id="counter_01",
            region_type="dispensing_counter",
            medication_key=None,
            polygon=rect(0.5, 0.1, 0.8, 0.4),
        ),
        Region(
            region_id="disposal_01",
            region_type="disposal",
            medication_key=None,
            polygon=rect(0.85, 0.7, 0.98, 0.95),
        ),
    ]

    inventory = {
        "AMOXICILLIN_500MG": InventoryState(
            medication_key="AMOXICILLIN_500MG",
            pooled_tablets=500,
            total_bottles=5,
            shelf_counts={"shelf_amoxicillin_500mg": 5},
            held_bottles=0,
            counter_bottles=0,
            disposed_bottles=0,
        ),
        "IBUPROFEN_200MG": InventoryState(
            medication_key="IBUPROFEN_200MG",
            pooled_tablets=200,
            total_bottles=2,
            shelf_counts={"shelf_ibuprofen_200mg": 2},
            held_bottles=0,
            counter_bottles=0,
            disposed_bottles=0,
        ),
    }

    receipts = [
        Receipt(
            receipt_id="REC_AMX_01",
            medication_key="AMOXICILLIN_500MG",
            bottle_count=5,
            remaining_bottles=5,
            total_tablets=500,
            expiry_date="2026-10-01",
            received_at="2026-09-01T00:00:00Z",
        )
    ]

    transactions = {
        "TX_1001": PrescriptionTransaction(
            transaction_id="TX_1001",
            medication_key="AMOXICILLIN_500MG",
            quantity=30,
            status="created",
            deducted=False,
        )
    }

    engine = InventoryEngine(
        inventory=inventory,
        regions=regions,
        receipts=receipts,
        transactions=transactions,
    )
    return engine


def test_nearest_region_inside_near_and_too_far():
    shelf = Region(region_id="shelf_1", region_type="designated_shelf", medication_key="MED_A",
                   polygon=rect(0.0, 0.0, 0.5, 0.5))
    reg, _ = nearest_region([(0.2, 0.2, 0.9)], [shelf])
    assert reg is not None and reg.region_id == "shelf_1"

    # Just outside the shelf edge but within the max distance still counts.
    reg, ev = nearest_region([(0.52, 0.2, 0.9)], [shelf])
    assert reg is not None and 0 < ev["distance"] <= MAX_REGION_DISTANCE

    # Far from every region -> uncertain rather than forcing the nearest shelf.
    reg, ev = nearest_region([(0.9, 0.9, 0.9)], [shelf])
    assert reg is None and ev["reason"] == "too_far" and ev["nearest_region_id"] == "shelf_1"

    # Low-confidence wrists are ignored.
    reg, ev = nearest_region([(0.2, 0.2, 0.1)], [shelf])
    assert reg is None and ev["reason"] == "no_confident_hand"


def test_nearest_region_uses_closest_of_both_hands():
    a = Region(region_id="a", region_type="designated_shelf", medication_key="A", polygon=rect(0.0, 0.0, 0.2, 0.2))
    b = Region(region_id="b", region_type="designated_shelf", medication_key="B", polygon=rect(0.6, 0.6, 0.8, 0.8))
    reg, _ = nearest_region([(0.25, 0.1, 0.9), (0.7, 0.7, 0.9)], [a, b])
    assert reg.region_id == "b"  # right hand is inside b; left hand is only near a
    reg, _ = nearest_region([(0.25, 0.1, 0.9), (0.7, 0.7, 0.2)], [a, b])
    assert reg.region_id == "a"  # right hand not confident


def test_nearest_region_overlap_is_ambiguous():
    a = Region(region_id="a", region_type="designated_shelf", medication_key="A", polygon=rect(0.0, 0.0, 0.5, 0.5))
    b = Region(region_id="b", region_type="dispensing_counter", polygon=rect(0.4, 0.4, 0.8, 0.8))
    reg, ev = nearest_region([(0.45, 0.45, 0.9)], [a, b])
    assert reg is None and ev["reason"] == "ambiguous"


def test_nearest_region_uses_polygon_not_bounding_box():
    triangle = Region(region_id="trash_1", region_type="disposal", polygon=[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)])
    reg, _ = nearest_region([(0.2, 0.2, 0.9)], [triangle])
    assert reg is not None
    # Inside the triangle's bounding box, but far from the triangle itself.
    reg, _ = nearest_region([(0.8, 0.8, 0.9)], [triangle])
    assert reg is None


def test_region_rejects_unnormalized_or_degenerate_polygons():
    with pytest.raises(ValueError):
        Region(region_id="r", region_type="disposal", polygon=[(0.0, 0.0), (1.2, 0.0), (0.0, 1.0)])
    with pytest.raises(ValueError):
        Region(region_id="r", region_type="disposal", polygon=[(0.0, 0.0), (1.0, 0.0)])


def test_pickup_and_counter_placement(base_setup):
    engine = base_setup

    # Pickup from amoxicillin shelf (wrist inside rect [0.1, 0.1, 0.4, 0.4])
    sess = engine.handle_pickup("sess_1", [(0.2, 0.2, 0.9)], 1000.0)
    assert sess.state == "HELD"
    assert sess.medication_key == "AMOXICILLIN_500MG"

    inv = engine.inventory["AMOXICILLIN_500MG"]
    assert inv.shelf_counts["shelf_amoxicillin_500mg"] == 4
    assert inv.held_bottles == 1
    assert inv.total_bottles == 5  # Pickup does not change total stock!

    # Release at counter (wrist inside rect [0.5, 0.1, 0.8, 0.4])
    sess2 = engine.handle_release("sess_1", [(0.6, 0.2, 0.9)], 2000.0)
    assert sess2.state == "AT_COUNTER"
    assert inv.held_bottles == 0
    assert inv.counter_bottles == 1
    assert inv.total_bottles == 5


def test_pickup_from_counter_and_return_to_shelf(base_setup):
    engine = base_setup

    # Park at counter first
    engine.handle_pickup("sess_1", [(0.2, 0.2, 0.9)], 1000.0)
    engine.handle_release("sess_1", [(0.6, 0.2, 0.9)], 2000.0)

    # Pickup from counter
    sess_pick_counter = engine.handle_pickup("sess_2", [(0.6, 0.2, 0.9)], 3000.0)
    assert sess_pick_counter.state == "HELD"
    inv = engine.inventory["AMOXICILLIN_500MG"]
    assert inv.counter_bottles == 0
    assert inv.held_bottles == 1

    # Return to designated shelf
    sess_return = engine.handle_release("sess_2", [(0.2, 0.2, 0.9)], 4000.0)
    assert sess_return.state == "ON_DESIGNATED_SHELF"
    assert inv.held_bottles == 0
    assert inv.shelf_counts["shelf_amoxicillin_500mg"] == 5


def test_wrong_shelf_release_triggers_misplacement_alert(base_setup):
    engine = base_setup

    # Pickup amoxicillin bottle
    engine.handle_pickup("sess_1", [(0.2, 0.2, 0.9)], 1000.0)

    # Release on ibuprofen shelf ([0.1, 0.5, 0.4, 0.8])
    sess = engine.handle_release("sess_1", [(0.2, 0.6, 0.9)], 2000.0)
    assert sess.state == "MISPLACED"
    assert sess.medication_key == "AMOXICILLIN_500MG"  # Identity preserved!

    # Verify misplacement alert raised
    alert = next(a for a in engine.alerts.values() if a.alert_type == "misplacement")
    assert alert.medication_key == "AMOXICILLIN_500MG"
    assert alert.severity == "error"


def test_uncertain_location_triggers_uncertainty_alert(base_setup):
    engine = base_setup

    # Low confidence wrist keypoint pickup
    sess = engine.handle_pickup("sess_1", [(0.2, 0.2, 0.1)], 1000.0)
    assert sess.state == "NEEDS_CONFIRMATION"

    alert = next(a for a in engine.alerts.values() if a.alert_type == "uncertainty")
    assert alert.status == "open"


def test_disposal_multiple_bottles_defaults(base_setup):
    engine = base_setup
    inv = engine.inventory["AMOXICILLIN_500MG"]
    assert inv.total_bottles == 5  # Multiple bottles present

    # Pickup and release in disposal region ([0.85, 0.7, 0.98, 0.95])
    engine.handle_pickup("sess_1", [(0.2, 0.2, 0.9)], 1000.0)
    sess_disp = engine.handle_release("sess_1", [(0.9, 0.8, 0.9)], 2000.0)
    assert sess_disp.state == "DISPOSED"

    assert inv.total_bottles == 4  # Total bottles reduced by 1
    disp_record = engine.disposals["disp_sess_1"]
    assert disp_record.quantity_deducted == 0  # Absent quantity default = 0 (assumed empty)
    assert disp_record.is_default_quantity is True


def test_disposal_single_bottle_defaults(base_setup):
    engine = base_setup
    inv = engine.inventory["IBUPROFEN_200MG"]
    inv.total_bottles = 1  # Exactly 1 bottle remaining before disposal
    inv.pooled_tablets = 100

    # Pickup and dispose ibuprofen
    engine.handle_pickup("sess_ibup", [(0.2, 0.6, 0.9)], 1000.0)
    engine.handle_release("sess_ibup", [(0.9, 0.8, 0.9)], 2000.0)

    # Exactly 1 bottle -> default discards entire pooled tablet balance!
    assert inv.total_bottles == 0
    assert inv.pooled_tablets == 0
    disp_record = engine.disposals["disp_sess_ibup"]
    assert disp_record.quantity_deducted == 100


def test_disposal_explicit_quantity_adjustment(base_setup):
    engine = base_setup
    inv = engine.inventory["AMOXICILLIN_500MG"]

    engine.handle_pickup("sess_1", [(0.2, 0.2, 0.9)], 1000.0)
    engine.handle_release("sess_1", [(0.9, 0.8, 0.9)], 2000.0)

    # Resolve disposal form with explicit quantity entry (e.g. 15 tablets left in bottle)
    record = engine.resolve_disposal("disp_sess_1", "REC_AMX_01", explicit_quantity=15)
    assert record.quantity_deducted == 15
    assert record.is_default_quantity is False
    assert inv.pooled_tablets == 485  # 500 - 15


def test_idempotent_prescription_deductions(base_setup):
    engine = base_setup
    inv = engine.inventory["AMOXICILLIN_500MG"]
    assert inv.pooled_tablets == 500

    # 1. First confirmed fill -> deducts 30 tablets once
    deducted = engine.process_prescription_deduction("TX_1001", "confirmed_fill")
    assert deducted is True
    assert inv.pooled_tablets == 470

    # 2. Subsequent payment event with SAME transaction_id -> does NOT deduct again!
    deducted_again = engine.process_prescription_deduction("TX_1001", "paid")
    assert deducted_again is False
    assert inv.pooled_tablets == 470


def test_expiry_alert_lifecycle(base_setup):
    engine = base_setup

    # Trigger expiry alert for date past expiry_date "2026-10-01"
    engine.trigger_expiry_alerts("2026-10-15")
    alert = next(a for a in engine.alerts.values() if a.alert_type == "expiry")
    assert alert.status == "open"

    # Expiry alone does NOT deduct inventory!
    inv = engine.inventory["AMOXICILLIN_500MG"]
    assert inv.total_bottles == 5

    # Dispose bottle and select expired receipt REC_AMX_01
    engine.handle_pickup("sess_1", [(0.2, 0.2, 0.9)], 1000.0)
    engine.handle_release("sess_1", [(0.9, 0.8, 0.9)], 2000.0)

    # Fast forward remaining bottles to 1 before resolve
    engine.receipts["REC_AMX_01"].remaining_bottles = 1

    engine.resolve_disposal("disp_sess_1", "REC_AMX_01", explicit_quantity=0)
    assert alert.status == "resolved"


def test_zero_bottle_out_of_stock_alert(base_setup):
    engine = base_setup
    inv = engine.inventory["IBUPROFEN_200MG"]
    inv.total_bottles = 1

    engine.handle_pickup("sess_1", [(0.2, 0.6, 0.9)], 1000.0)
    engine.handle_release("sess_1", [(0.9, 0.8, 0.9)], 2000.0)

    assert inv.total_bottles == 0
    alert = next(a for a in engine.alerts.values() if a.alert_type == "out_of_stock")
    assert alert.medication_key == "IBUPROFEN_200MG"
    assert alert.severity == "error"


AMX_HAND = [(0.2, 0.2, 0.9)]
IBU_HAND = [(0.2, 0.6, 0.9)]
COUNTER_HAND = [(0.6, 0.2, 0.9)]
TRASH_HAND = [(0.9, 0.8, 0.9)]
NOWHERE_HAND = [(0.62, 0.95, 0.9)]


def open_alerts(engine, alert_type):
    return [a for a in engine.alerts.values() if a.alert_type == alert_type and a.status == "open"]


def test_misplaced_bottle_is_corrected_by_moving_it_home(base_setup):
    engine = base_setup
    amx, ibu = engine.inventory["AMOXICILLIN_500MG"], engine.inventory["IBUPROFEN_200MG"]

    engine.handle_pickup("s1", AMX_HAND, 0)
    engine.handle_release("s1", IBU_HAND, 0)
    assert len(open_alerts(engine, "misplacement")) == 1
    assert amx.shelf_counts == {"shelf_amoxicillin_500mg": 4, "shelf_ibuprofen_200mg": 1}

    # Picking up from the shelf holding the misplaced bottle is assumed to be the correction.
    sess = engine.handle_pickup("s2", IBU_HAND, 0)
    assert sess.medication_key == "AMOXICILLIN_500MG"
    assert sess.original_shelf_id == "shelf_amoxicillin_500mg"
    assert ibu.shelf_counts == {"shelf_ibuprofen_200mg": 2}  # Ibuprofen untouched
    assert amx.shelf_counts["shelf_ibuprofen_200mg"] == 0

    engine.handle_release("s2", AMX_HAND, 0)
    assert open_alerts(engine, "misplacement") == []
    assert amx.shelf_counts["shelf_amoxicillin_500mg"] == 5 and amx.held_bottles == 0
    assert ibu.shelf_counts == {"shelf_ibuprofen_200mg": 2} and ibu.held_bottles == 0


def test_misplaced_bottle_moved_to_another_wrong_shelf_replaces_alert(base_setup):
    engine = base_setup
    engine.regions["shelf_extra"] = Region(
        region_id="shelf_extra", region_type="designated_shelf", medication_key="IBUPROFEN_200MG",
        polygon=rect(0.5, 0.5, 0.7, 0.65),
    )
    engine.handle_pickup("s1", AMX_HAND, 0)
    engine.handle_release("s1", IBU_HAND, 0)
    engine.handle_pickup("s2", IBU_HAND, 0)
    engine.handle_release("s2", [(0.6, 0.55, 0.9)], 0)
    alerts = open_alerts(engine, "misplacement")
    assert len(alerts) == 1 and alerts[0].metadata["placed_shelf"] == "shelf_extra"


def test_hand_too_far_needs_confirmation_then_applies_pickup(base_setup):
    engine = base_setup
    amx = engine.inventory["AMOXICILLIN_500MG"]
    sess = engine.handle_pickup("s1", NOWHERE_HAND, 0)
    assert sess.state == "NEEDS_CONFIRMATION"
    assert amx.shelf_counts["shelf_amoxicillin_500mg"] == 5  # nothing changes until confirmed

    alert = open_alerts(engine, "uncertainty")[0]
    assert alert.metadata["phase"] == "pickup" and alert.metadata["reason"] == "too_far"
    engine.confirm_location(alert.alert_id, "shelf_amoxicillin_500mg")
    assert alert.status == "resolved"
    assert sess.state == "HELD" and sess.medication_key == "AMOXICILLIN_500MG"
    assert amx.shelf_counts["shelf_amoxicillin_500mg"] == 4 and amx.held_bottles == 1


def test_release_during_unconfirmed_pickup_is_applied_after_confirmation(base_setup):
    engine = base_setup
    amx = engine.inventory["AMOXICILLIN_500MG"]
    engine.handle_pickup("s1", NOWHERE_HAND, 0)
    engine.handle_release("s1", COUNTER_HAND, 0)
    assert amx.counter_bottles == 0
    assert open_alerts(engine, "reconciliation_issue") == []

    alert = open_alerts(engine, "uncertainty")[0]
    sess = engine.confirm_location(alert.alert_id, "shelf_amoxicillin_500mg")
    assert sess.state == "AT_COUNTER"
    assert amx.shelf_counts["shelf_amoxicillin_500mg"] == 4 and amx.counter_bottles == 1 and amx.held_bottles == 0


def test_uncertain_release_confirmed_to_wrong_shelf_alerts(base_setup):
    engine = base_setup
    engine.handle_pickup("s1", AMX_HAND, 0)
    sess = engine.handle_release("s1", NOWHERE_HAND, 0)
    assert sess.state == "NEEDS_CONFIRMATION"
    alert = open_alerts(engine, "uncertainty")[0]
    assert alert.metadata["phase"] == "release"

    engine.confirm_location(alert.alert_id, "shelf_ibuprofen_200mg")
    assert sess.state == "MISPLACED"
    assert len(open_alerts(engine, "misplacement")) == 1
    with pytest.raises(ValueError, match="already resolved"):
        engine.confirm_location(alert.alert_id, "shelf_amoxicillin_500mg")


def test_confirming_empty_counter_pickup_is_rejected(base_setup):
    engine = base_setup
    engine.handle_pickup("s1", NOWHERE_HAND, 0)
    alert = open_alerts(engine, "uncertainty")[0]
    with pytest.raises(ValueError, match="No bottle is parked"):
        engine.confirm_location(alert.alert_id, "counter_01")
    with pytest.raises(ValueError, match="shelf or counter"):
        engine.confirm_location(alert.alert_id, "disposal_01")


def test_pickup_never_comes_from_trash(base_setup):
    engine = base_setup
    sess = engine.handle_pickup("s1", TRASH_HAND, 0)
    assert sess.state == "NEEDS_CONFIRMATION"


def test_receive_stock_adds_to_live_counts_on_shelf(base_setup):
    engine = base_setup
    amx = engine.inventory["AMOXICILLIN_500MG"]
    engine.handle_pickup("s1", AMX_HAND, 0)  # live state that must survive receiving

    receipt = engine.receive_stock("AMOXICILLIN_500MG", 3, 60, "2027-05-31", "2026-09-26T10:00:00Z", "LOT-1")
    assert receipt.total_tablets == 180 and receipt.remaining_bottles == 3
    assert amx.total_bottles == 8 and amx.pooled_tablets == 680
    assert amx.shelf_counts["shelf_amoxicillin_500mg"] == 7 and amx.held_bottles == 1

    with pytest.raises(ValueError, match="not a configured medication"):
        engine.receive_stock("UNKNOWN_1MG", 1, 1, "2027-01-01", "2026-09-26")


def test_receive_stock_clears_out_of_stock(base_setup):
    engine = base_setup
    engine.inventory["IBUPROFEN_200MG"].total_bottles = 1
    engine.handle_pickup("s1", IBU_HAND, 0)
    engine.handle_release("s1", TRASH_HAND, 0)
    assert len(open_alerts(engine, "out_of_stock")) == 1
    engine.receive_stock("IBUPROFEN_200MG", 2, 100, "2027-01-01", "2026-09-26")
    assert open_alerts(engine, "out_of_stock") == []


def test_receiving_expired_stock_raises_expiry_alert_for_that_batch(base_setup):
    engine = base_setup
    receipt = engine.receive_stock("IBUPROFEN_200MG", 1, 10, "2026-01-01", "2026-09-26", today_iso="2026-09-26")
    alert = open_alerts(engine, "expiry")[0]
    assert alert.metadata["receipt_id"] == receipt.receipt_id
    engine.trigger_expiry_alerts("2026-09-27")
    assert len([a for a in engine.alerts.values() if a.alert_type == "expiry"]) == 1  # no duplicates


def test_disposing_a_different_batch_leaves_expiry_alert_open(base_setup):
    engine = base_setup
    fresh = engine.receive_stock("AMOXICILLIN_500MG", 1, 100, "2028-01-01", "2026-09-26")
    engine.trigger_expiry_alerts("2026-10-15")  # REC_AMX_01 expires 2026-10-01
    engine.receipts["REC_AMX_01"].remaining_bottles = 1

    engine.handle_pickup("s1", AMX_HAND, 0)
    engine.handle_release("s1", TRASH_HAND, 0)
    fresh.remaining_bottles = 1
    engine.resolve_disposal("disp_s1", fresh.receipt_id)
    assert len(open_alerts(engine, "expiry")) == 1  # the expired batch is still on the shelf


def test_disposal_cannot_be_resolved_twice_or_against_other_medication(base_setup):
    engine = base_setup
    engine.handle_pickup("s1", AMX_HAND, 0)
    engine.handle_release("s1", TRASH_HAND, 0)
    engine.resolve_disposal("disp_s1", "REC_AMX_01")
    assert engine.receipts["REC_AMX_01"].remaining_bottles == 4
    with pytest.raises(ValueError, match="already been identified"):
        engine.resolve_disposal("disp_s1", "REC_AMX_01")
    assert engine.receipts["REC_AMX_01"].remaining_bottles == 4


def test_dispose_batch_defaults_and_clears_expiry(base_setup):
    engine = base_setup
    engine.trigger_expiry_alerts("2026-10-02")
    expiry = next(a for a in engine.alerts.values() if a.alert_type == "expiry")
    amx = engine.inventory["AMOXICILLIN_500MG"]

    record = engine.dispose_batch("REC_AMX_01", 2)  # other bottles remain: blank means empty bottles
    assert (record.quantity_deducted, record.is_default_quantity, record.status) == (0, True, "resolved")
    assert (amx.total_bottles, amx.shelf_counts["shelf_amoxicillin_500mg"], amx.pooled_tablets) == (3, 3, 500)
    assert engine.receipts["REC_AMX_01"].remaining_bottles == 3 and expiry.status == "open"

    engine.dispose_batch("REC_AMX_01", 1, tablets=40)
    assert amx.pooled_tablets == 460

    engine.dispose_batch("REC_AMX_01", 2)  # the last bottles: blank discards the whole balance
    assert (amx.total_bottles, amx.pooled_tablets, amx.disposed_bottles) == (0, 0, 5)
    assert expiry.status == "resolved"
    assert any(a.alert_type == "out_of_stock" and a.status == "open" for a in engine.alerts.values())


def test_dispose_batch_rejects_more_than_on_shelf_or_in_batch(base_setup):
    engine = base_setup
    engine.handle_pickup("s1", AMX_HAND, 0)  # one bottle is in hand, not on the shelf
    with pytest.raises(ValueError, match="Only 4 bottle"):
        engine.dispose_batch("REC_AMX_01", 5)
    with pytest.raises(ValueError, match="only 5 bottle"):
        engine.dispose_batch("REC_AMX_01", 6)
    with pytest.raises(ValueError, match="not found"):
        engine.dispose_batch("NOPE", 1)
    assert engine.inventory["AMOXICILLIN_500MG"].total_bottles == 5


def test_add_transaction_ids_and_immediate_deduction(base_setup):
    engine = base_setup
    tx = engine.add_transaction("IBUPROFEN_200MG", 20)
    assert tx.transaction_id.startswith("RX_") and tx.status == "created" and not tx.deducted
    paid = engine.add_transaction("IBUPROFEN_200MG", 50, "RX_CUSTOM", status="paid")
    assert paid.deducted and engine.inventory["IBUPROFEN_200MG"].pooled_tablets == 150
    assert engine.process_prescription_deduction("RX_CUSTOM", "paid") is False  # still only once
    with pytest.raises(ValueError, match="already exists"):
        engine.add_transaction("IBUPROFEN_200MG", 1, "RX_CUSTOM")
    with pytest.raises(ValueError, match="not a configured"):
        engine.add_transaction("NOPE_1MG", 1)


def test_uncertain_evidence_ranks_candidate_regions(base_setup):
    region, evidence = nearest_region([(0.45, 0.25, 0.9)], list(base_setup.regions.values()), max_distance=0.01)
    assert region is None and evidence["reason"] == "too_far"
    ranked = [c["region_id"] for c in evidence["candidates"]]
    assert ranked[:2] in (["shelf_amoxicillin_500mg", "counter_01"], ["counter_01", "shelf_amoxicillin_500mg"])
    assert [c["distance"] for c in evidence["candidates"]] == sorted(c["distance"] for c in evidence["candidates"])
