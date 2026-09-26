"""Tests for shared camera layouts: validation, persistence, and derived starting stock."""

import pytest
from pydantic import ValidationError

from pharma.db.models import Layout, medication_key_for
from pharma.services.layout import build_initial_state, load_layout, save_layout


def square(x0, y0, size=0.2):
    return [[x0, y0], [x0 + size, y0], [x0 + size, y0 + size], [x0, y0 + size]]


def layout_payload(**overrides):
    payload = {
        "layout_id": "test",
        "medications": [
            {"medication_key": "AMOXICILLIN_500MG", "name": "Amoxicillin", "strength": "500mg"},
            {"medication_key": "AMOXICILLIN_250MG", "name": "Amoxicillin", "strength": "250mg"},
        ],
        "regions": [
            {"region_id": "shelf_01", "region_type": "designated_shelf", "medication_key": "AMOXICILLIN_500MG", "polygon": square(0.0, 0.0)},
            {"region_id": "shelf_02", "region_type": "designated_shelf", "medication_key": "AMOXICILLIN_250MG", "polygon": square(0.3, 0.0)},
            {"region_id": "counter_01", "region_type": "dispensing_counter", "polygon": square(0.6, 0.0)},
            {"region_id": "counter_02", "region_type": "dispensing_counter", "polygon": square(0.6, 0.3)},
            {"region_id": "disposal_01", "region_type": "disposal", "polygon": square(0.6, 0.6)},
        ],
        "receipts": [
            {"receipt_id": "R1", "medication_key": "AMOXICILLIN_500MG", "bottle_count": 2, "tablets_per_bottle": 100,
             "expiry_date": "2027-01-31", "lot_number": "L1", "received_at": "2026-09-01T00:00:00Z"},
            {"receipt_id": "R2", "medication_key": "AMOXICILLIN_500MG", "bottle_count": 3, "tablets_per_bottle": 50,
             "expiry_date": "2026-08-31", "received_at": "2026-09-02T00:00:00Z"},
        ],
    }
    payload.update(overrides)
    return payload


def test_medication_key_combines_drug_and_strength():
    assert medication_key_for("Amoxicillin", "500mg") == "AMOXICILLIN_500MG"
    assert medication_key_for(" amoxicillin ", "500 MG") == "AMOXICILLIN_500MG"
    assert medication_key_for("Levothyroxine Sodium", "0.05mg") == "LEVOTHYROXINE_SODIUM_0.05MG"
    assert medication_key_for("Amoxicillin", "250mg") != medication_key_for("Amoxicillin", "500mg")


def test_same_drug_different_strengths_are_separate_pools():
    layout = Layout(**layout_payload())
    _, inventory, receipts = build_initial_state(layout)

    assert inventory["AMOXICILLIN_500MG"].pooled_tablets == 2 * 100 + 3 * 50
    assert inventory["AMOXICILLIN_500MG"].total_bottles == 5
    assert inventory["AMOXICILLIN_500MG"].shelf_counts == {"shelf_01": 5}
    # A medication with a shelf but no receipts starts at zero stock.
    assert inventory["AMOXICILLIN_250MG"].total_bottles == 0
    assert inventory["AMOXICILLIN_250MG"].shelf_counts == {"shelf_02": 0}

    by_id = {r.receipt_id: r for r in receipts}
    assert by_id["R2"].total_tablets == 150
    assert by_id["R2"].remaining_bottles == 3
    assert by_id["R2"].expiry_date == "2026-08-31"


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda p: p["regions"].pop(1), "AMOXICILLIN_250MG has no shelf drawn"),
        (lambda p: p["regions"][1].update(medication_key="AMOXICILLIN_500MG"), "more than one shelf"),
        (lambda p: p["regions"][0].update(medication_key="UNKNOWN_1MG"), "no known medication"),
        (lambda p: p["regions"][2].update(medication_key="AMOXICILLIN_500MG"), "cannot hold a medication"),
        (lambda p: p["medications"].append(dict(p["medications"][0])), "Duplicate medication"),
        (lambda p: p["medications"][0].update(medication_key="AMOX"), "should be AMOXICILLIN_500MG"),
        (lambda p: p["receipts"][1].update(receipt_id="R1"), "Duplicate receipt ID R1"),
        (lambda p: p["receipts"][0].update(medication_key="UNKNOWN_1MG"), "unknown medication"),
        (lambda p: p["receipts"][0].update(expiry_date="31/01/2027"), "Invalid isoformat"),
        (lambda p: p["receipts"][0].update(bottle_count=0), "greater than or equal to 1"),
        (lambda p: p.update(layout_id="../escape"), "String should match pattern"),
    ],
)
def test_layout_validation_rejects_inconsistent_setup(mutate, message):
    payload = layout_payload()
    mutate(payload)
    with pytest.raises(ValidationError, match=message):
        Layout(**payload)


def test_save_layout_bumps_calibration_version(tmp_path):
    first = save_layout(tmp_path, Layout(**layout_payload(calibration_version=42)))
    assert first.calibration_version == 1
    assert first.updated_at

    second = save_layout(tmp_path, Layout(**layout_payload()))
    assert second.calibration_version == 2
    assert load_layout(tmp_path, "test") == second
    assert not list(tmp_path.rglob("*.tmp"))
