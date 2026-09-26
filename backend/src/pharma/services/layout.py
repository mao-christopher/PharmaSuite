"""Shared camera layouts: regions, medications, and preset received stock."""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

from pharma.db.models import LAYOUT_ID_PATTERN, InventoryState, Layout, Receipt, Region

LAYOUT_FILE = "layout.json"


def layout_path(layouts_dir: Path, layout_id: str) -> Path:
    if not re.fullmatch(LAYOUT_ID_PATTERN, layout_id):
        raise ValueError(f"Invalid layout ID {layout_id!r}")
    return layouts_dir / layout_id / LAYOUT_FILE


def list_layout_ids(layouts_dir: Path) -> List[str]:
    if not layouts_dir.exists():
        return []
    return sorted(p.parent.name for p in layouts_dir.glob(f"*/{LAYOUT_FILE}"))


def load_layout(layouts_dir: Path, layout_id: str) -> Layout:
    path = layout_path(layouts_dir, layout_id)
    if not path.exists():
        raise FileNotFoundError(f"Layout '{layout_id}' not found at {path}")
    return Layout.model_validate_json(path.read_text(encoding="utf-8"))


def save_layout(layouts_dir: Path, layout: Layout) -> Layout:
    """Persist a layout, bumping calibration_version past the stored one."""
    path = layout_path(layouts_dir, layout.layout_id)
    previous = 0
    if path.exists():
        previous = load_layout(layouts_dir, layout.layout_id).calibration_version
    saved = layout.model_copy(
        update={
            "calibration_version": previous + 1,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(saved.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return saved


def build_initial_state(
    layout: Layout,
) -> Tuple[List[Region], Dict[str, InventoryState], List[Receipt]]:
    """Derive runtime regions, pooled inventory, and receipts from preset received batches."""
    shelf_for = {
        r.medication_key: r.region_id for r in layout.regions if r.region_type == "designated_shelf"
    }
    receipts = [
        Receipt(
            receipt_id=r.receipt_id,
            medication_key=r.medication_key,
            bottle_count=r.bottle_count,
            remaining_bottles=r.bottle_count,
            total_tablets=r.bottle_count * r.tablets_per_bottle,
            expiry_date=r.expiry_date,
            lot_number=r.lot_number,
            received_at=r.received_at,
        )
        for r in layout.receipts
    ]
    inventory: Dict[str, InventoryState] = {}
    for med in layout.medications:
        batches = [r for r in receipts if r.medication_key == med.medication_key]
        bottles = sum(r.bottle_count for r in batches)
        inventory[med.medication_key] = InventoryState(
            medication_key=med.medication_key,
            pooled_tablets=sum(r.total_tablets for r in batches),
            total_bottles=bottles,
            shelf_counts={shelf_for[med.medication_key]: bottles},
        )
    return list(layout.regions), inventory, receipts
