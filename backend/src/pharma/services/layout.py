"""Camera views (region geometry per camera angle) and the shared medication catalog.

`data/layouts/<id>/layout.json` holds one view: frame size, background photo, and region
polygons. `data/catalog.json` holds medications and their opening stock for the whole
pharmacy. Older layouts that still carry medications/receipts seed the catalog once.
"""

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import cv2
import numpy as np

from pharma.db.models import (
    LAYOUT_ID_PATTERN, Catalog, InventoryState, Layout, Receipt, Region, shelf_region_id,
)

LAYOUT_FILE = "layout.json"
CATALOG_FILE = "catalog.json"
DEFAULT_LAYOUT_ID = "default"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


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
        raise FileNotFoundError(f"Camera view '{layout_id}' not found at {path}")
    return Layout.model_validate_json(path.read_text(encoding="utf-8"))


def save_layout(layouts_dir: Path, layout: Layout, bump: bool = True) -> Layout:
    """Persist a view (geometry only), bumping calibration_version past the stored one.

    bump=False keeps the stored version, for changes that leave the region geometry as
    it was (a new name, or the provenance of unchanged generated regions).
    """
    path = layout_path(layouts_dir, layout.layout_id)
    previous = load_layout(layouts_dir, layout.layout_id).calibration_version if path.exists() else 0
    version = previous + 1 if bump or not previous else previous
    saved = layout.model_copy(update={"calibration_version": version, "updated_at": _now()})
    _atomic_write(path, saved.view_only().model_dump(mode="json"))
    return saved


def new_layout_id(layouts_dir: Path, name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "view"
    if not re.match(r"^[a-z0-9]", base):
        base = f"view-{base}"
    candidate, n = base, 2
    while layout_path(layouts_dir, candidate).exists():
        candidate, n = f"{base}-{n}", n + 1
    return candidate


# ---------------------------------------------------------------- catalog


def catalog_path(layouts_dir: Path) -> Path:
    return layouts_dir.parent / CATALOG_FILE


def load_catalog(layouts_dir: Path) -> Catalog:
    path = catalog_path(layouts_dir)
    if path.exists():
        return Catalog.model_validate_json(path.read_text(encoding="utf-8"))
    # Migration: take medications and opening stock from the first view that has them.
    ids = list_layout_ids(layouts_dir)
    for layout_id in sorted(ids, key=lambda i: (i != DEFAULT_LAYOUT_ID, i)):
        layout = load_layout(layouts_dir, layout_id)
        if layout.medications:
            return Catalog(medications=layout.medications, receipts=layout.receipts)
    return Catalog()


def save_catalog(layouts_dir: Path, catalog: Catalog) -> Catalog:
    saved = catalog.model_copy(update={"updated_at": _now()})
    _atomic_write(catalog_path(layouts_dir), saved.model_dump(mode="json"))
    return saved


def merge_view(view: Layout, catalog: Catalog) -> Layout:
    """A view with the shared medications and opening stock filled in (validated together)."""
    payload = view.model_dump(mode="json")
    payload.update(medications=[m.model_dump() for m in catalog.medications],
                   receipts=[r.model_dump() for r in catalog.receipts])
    return Layout.model_validate(payload)


def split_view(layout: Layout) -> Tuple[Layout, Catalog]:
    return layout.view_only(), Catalog(medications=layout.medications, receipts=layout.receipts)


# ---------------------------------------------------------------- backgrounds


def save_background(folder: Path, data: bytes, decoded: np.ndarray, is_png: bool) -> str:
    filename = f"background-{hashlib.sha256(data).hexdigest()[:12]}.{'png' if is_png else 'jpg'}"
    folder.mkdir(parents=True, exist_ok=True)
    if is_png or data[:3] == b"\xff\xd8\xff":
        (folder / filename).write_bytes(data)
    else:
        cv2.imwrite(str(folder / filename), decoded)
    return filename


def save_frame_background(folder: Path, frame: np.ndarray) -> str:
    ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok:
        raise RuntimeError("Failed to encode frame")
    return save_background(folder, jpeg.tobytes(), frame, is_png=False)


# ---------------------------------------------------------------- stock


def build_initial_state(
    source: Union[Catalog, Layout],
) -> Tuple[List[Region], Dict[str, InventoryState], List[Receipt]]:
    """Opening stock: pooled tablets and bottles per medication, all on its shelf."""
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
        for r in source.receipts
    ]
    inventory: Dict[str, InventoryState] = {}
    for med in source.medications:
        batches = [r for r in receipts if r.medication_key == med.medication_key]
        bottles = sum(r.bottle_count for r in batches)
        inventory[med.medication_key] = InventoryState(
            medication_key=med.medication_key,
            pooled_tablets=sum(r.total_tablets for r in batches),
            total_bottles=bottles,
            shelf_counts={shelf_region_id(med.medication_key): bottles},
        )
    return list(getattr(source, "regions", [])), inventory, receipts


def frame_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """0-1 score for how alike two camera frames look (layout, not detail).

    Blends normalized cross-correlation of small grayscale thumbnails with an HSV
    color-histogram match, penalized by aspect-ratio difference. A heuristic for
    suggesting a view; the employee confirms or edits the suggestion.
    """
    def thumb(img):
        gray = cv2.cvtColor(cv2.resize(img, (64, 48), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
        g = gray.astype(np.float32)
        return (g - g.mean()) / (g.std() + 1e-6)

    def hist(img):
        hsv = cv2.cvtColor(cv2.resize(img, (160, 120), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2HSV)
        h = cv2.calcHist([hsv], [0, 1], None, [18, 8], [0, 180, 0, 256])
        return cv2.normalize(h, h).flatten()

    ncc = float((thumb(a) * thumb(b)).mean())
    color = float(cv2.compareHist(hist(a), hist(b), cv2.HISTCMP_CORREL))
    ar_a, ar_b = a.shape[1] / a.shape[0], b.shape[1] / b.shape[0]
    aspect = min(ar_a, ar_b) / max(ar_a, ar_b)
    score = (0.6 * max(0.0, ncc) + 0.4 * max(0.0, color)) * aspect
    return round(max(0.0, min(1.0, score)), 3)


def load_background(layouts_dir: Path, layout: Layout) -> Optional[np.ndarray]:
    if not layout.background_image:
        return None
    return cv2.imread(str(layouts_dir / layout.layout_id / layout.background_image))
