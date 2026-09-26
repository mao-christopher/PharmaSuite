"""Comprehensive pytest suite for Pharma InventoryEngine rules and state machine."""

import pytest
from pharma.db.models import (
    InventoryState,
    Region,
    Receipt,
    PrescriptionTransaction,
)
from pharma.services.inventory_engine import InventoryEngine, associate_region


@pytest.fixture
def base_setup():
    regions = [
        Region(
            region_id="shelf_amx_500",
            region_type="designated_shelf",
            medication_key="AMOXICILLIN_500MG",
            bbox=[0.1, 0.1, 0.4, 0.4],
        ),
        Region(
            region_id="shelf_ibup_200",
            region_type="designated_shelf",
            medication_key="IBUPROFEN_200MG",
            bbox=[0.1, 0.5, 0.4, 0.8],
        ),
        Region(
            region_id="counter_01",
            region_type="dispensing_counter",
            medication_key=None,
            bbox=[0.5, 0.1, 0.8, 0.4],
        ),
        Region(
            region_id="disposal_01",
            region_type="disposal",
            medication_key=None,
            bbox=[0.85, 0.7, 0.98, 0.95],
        ),
    ]

    inventory = {
        "AMOXICILLIN_500MG": InventoryState(
            medication_key="AMOXICILLIN_500MG",
            pooled_tablets=500,
            total_bottles=5,
            shelf_counts={"shelf_amx_500": 5},
            held_bottles=0,
            counter_bottles=0,
            disposed_bottles=0,
        ),
        "IBUPROFEN_200MG": InventoryState(
            medication_key="IBUPROFEN_200MG",
            pooled_tablets=200,
            total_bottles=2,
            shelf_counts={"shelf_ibup_200": 2},
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


def test_associate_region():
    regions = [
        Region(
            region_id="shelf_1",
            region_type="designated_shelf",
            medication_key="MED_A",
            bbox=[0.0, 0.0, 0.5, 0.5],
        )
    ]
    # Match inside box
    reg, uncertain = associate_region((0.2, 0.2), 0.9, regions)
    assert reg is not None
    assert reg.region_id == "shelf_1"
    assert not uncertain

    # Low confidence
    reg, uncertain = associate_region((0.2, 0.2), 0.1, regions)
    assert reg is None
    assert uncertain


def test_pickup_and_counter_placement(base_setup):
    engine = base_setup

    # Pickup from amoxicillin shelf (wrist inside bbox [0.1, 0.1, 0.4, 0.4])
    sess = engine.handle_pickup("sess_1", (0.2, 0.2), 0.9, 1000.0)
    assert sess.state == "HELD"
    assert sess.medication_key == "AMOXICILLIN_500MG"

    inv = engine.inventory["AMOXICILLIN_500MG"]
    assert inv.shelf_counts["shelf_amx_500"] == 4
    assert inv.held_bottles == 1
    assert inv.total_bottles == 5  # Pickup does not change total stock!

    # Release at counter (wrist inside bbox [0.5, 0.1, 0.8, 0.4])
    sess2 = engine.handle_release("sess_1", (0.6, 0.2), 0.9, 2000.0)
    assert sess2.state == "AT_COUNTER"
    assert inv.held_bottles == 0
    assert inv.counter_bottles == 1
    assert inv.total_bottles == 5


def test_pickup_from_counter_and_return_to_shelf(base_setup):
    engine = base_setup

    # Park at counter first
    engine.handle_pickup("sess_1", (0.2, 0.2), 0.9, 1000.0)
    engine.handle_release("sess_1", (0.6, 0.2), 0.9, 2000.0)

    # Pickup from counter
    sess_pick_counter = engine.handle_pickup("sess_2", (0.6, 0.2), 0.9, 3000.0)
    assert sess_pick_counter.state == "HELD"
    inv = engine.inventory["AMOXICILLIN_500MG"]
    assert inv.counter_bottles == 0
    assert inv.held_bottles == 1

    # Return to designated shelf
    sess_return = engine.handle_release("sess_2", (0.2, 0.2), 0.9, 4000.0)
    assert sess_return.state == "ON_DESIGNATED_SHELF"
    assert inv.held_bottles == 0
    assert inv.shelf_counts["shelf_amx_500"] == 5


def test_wrong_shelf_release_triggers_misplacement_alert(base_setup):
    engine = base_setup

    # Pickup amoxicillin bottle
    engine.handle_pickup("sess_1", (0.2, 0.2), 0.9, 1000.0)

    # Release on ibuprofen shelf ([0.1, 0.5, 0.4, 0.8])
    sess = engine.handle_release("sess_1", (0.2, 0.6), 0.9, 2000.0)
    assert sess.state == "MISPLACED"
    assert sess.medication_key == "AMOXICILLIN_500MG"  # Identity preserved!

    # Verify misplacement alert raised
    alert = next(a for a in engine.alerts.values() if a.alert_type == "misplacement")
    assert alert.medication_key == "AMOXICILLIN_500MG"
    assert alert.severity == "error"


def test_uncertain_location_triggers_uncertainty_alert(base_setup):
    engine = base_setup

    # Low confidence wrist keypoint pickup
    sess = engine.handle_pickup("sess_1", (0.2, 0.2), 0.1, 1000.0)
    assert sess.state == "NEEDS_CONFIRMATION"

    alert = next(a for a in engine.alerts.values() if a.alert_type == "uncertainty")
    assert alert.status == "open"


def test_disposal_multiple_bottles_defaults(base_setup):
    engine = base_setup
    inv = engine.inventory["AMOXICILLIN_500MG"]
    assert inv.total_bottles == 5  # Multiple bottles present

    # Pickup and release in disposal region ([0.85, 0.7, 0.98, 0.95])
    engine.handle_pickup("sess_1", (0.2, 0.2), 0.9, 1000.0)
    sess_disp = engine.handle_release("sess_1", (0.9, 0.8), 0.9, 2000.0)
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
    engine.handle_pickup("sess_ibup", (0.2, 0.6), 0.9, 1000.0)
    engine.handle_release("sess_ibup", (0.9, 0.8), 0.9, 2000.0)

    # Exactly 1 bottle -> default discards entire pooled tablet balance!
    assert inv.total_bottles == 0
    assert inv.pooled_tablets == 0
    disp_record = engine.disposals["disp_sess_ibup"]
    assert disp_record.quantity_deducted == 100


def test_disposal_explicit_quantity_adjustment(base_setup):
    engine = base_setup
    inv = engine.inventory["AMOXICILLIN_500MG"]

    engine.handle_pickup("sess_1", (0.2, 0.2), 0.9, 1000.0)
    engine.handle_release("sess_1", (0.9, 0.8), 0.9, 2000.0)

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
    engine.handle_pickup("sess_1", (0.2, 0.2), 0.9, 1000.0)
    engine.handle_release("sess_1", (0.9, 0.8), 0.9, 2000.0)

    # Fast forward remaining bottles to 1 before resolve
    engine.receipts["REC_AMX_01"].remaining_bottles = 1

    engine.resolve_disposal("disp_sess_1", "REC_AMX_01", explicit_quantity=0)
    assert alert.status == "resolved"


def test_zero_bottle_out_of_stock_alert(base_setup):
    engine = base_setup
    inv = engine.inventory["IBUPROFEN_200MG"]
    inv.total_bottles = 1

    engine.handle_pickup("sess_1", (0.2, 0.6), 0.9, 1000.0)
    engine.handle_release("sess_1", (0.9, 0.8), 0.9, 2000.0)

    assert inv.total_bottles == 0
    alert = next(a for a in engine.alerts.values() if a.alert_type == "out_of_stock")
    assert alert.medication_key == "IBUPROFEN_200MG"
    assert alert.severity == "error"
