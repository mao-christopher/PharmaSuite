"""Pydantic schemas and MongoDB document definitions for Pharma inventory management."""

import re
from datetime import date
from typing import Dict, List, Literal, Optional, Any, Tuple
from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator

RegionType = Literal["designated_shelf", "dispensing_counter", "disposal"]
LAYOUT_ID_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,63}$"
BACKGROUND_PATTERN = r"^background-[0-9a-f]{12}\.(jpg|png)$"


def medication_key_for(name: str, strength: str) -> str:
    """Derive the medication + strength key, e.g. ("Amoxicillin", "500 mg") -> AMOXICILLIN_500MG."""
    name_part = re.sub(r"[^A-Z0-9]+", "_", name.strip().upper()).strip("_")
    strength_part = re.sub(r"[^A-Z0-9.]+", "", strength.strip().upper())
    return f"{name_part}_{strength_part}"


class Medication(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    medication_key: str = Field(..., description="Unique key e.g. AMOXICILLIN_500MG")
    name: str = Field(..., min_length=1, description="Display name e.g. Amoxicillin")
    strength: str = Field(..., min_length=1, description="Strength e.g. 500mg")
    unit: str = Field(default="tablets", description="Dosage form unit")


class Region(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    region_id: str = Field(..., min_length=1, description="Stable region ID e.g. shelf_01")
    region_type: RegionType
    medication_key: Optional[str] = Field(default=None, description="Designated medication key if shelf")
    polygon: List[Tuple[float, float]] = Field(
        ..., min_length=3, description="Closed polygon vertices [[x, y], ...] in normalized 0.0-1.0 frame coordinates"
    )

    @field_validator("polygon")
    @classmethod
    def _normalized(cls, pts: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
        for x, y in pts:
            if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                raise ValueError("polygon coordinates must be normalized to 0.0-1.0")
        return pts


class Receipt(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    receipt_id: str = Field(..., description="Unique receipt/batch ID")
    medication_key: str = Field(..., description="Medication key")
    bottle_count: int = Field(..., ge=0, description="Initial bottle count received")
    remaining_bottles: int = Field(..., ge=0, description="Remaining un-disposed bottle count")
    total_tablets: int = Field(..., ge=0, description="Initial total tablets received")
    expiry_date: str = Field(..., description="Expiration date ISO string YYYY-MM-DD")
    lot_number: Optional[str] = Field(default=None, description="Manufacturer lot number")
    received_at: str = Field(..., description="Receipt timestamp ISO string")


class InventoryState(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    medication_key: str = Field(..., description="Medication key")
    pooled_tablets: int = Field(default=0, ge=0, description="Pooled tablet balance")
    total_bottles: int = Field(default=0, ge=0, description="Total undisposed bottles in pharmacy")
    shelf_counts: Dict[str, int] = Field(default_factory=dict, description="Region ID -> on-shelf bottle count")
    held_bottles: int = Field(default=0, ge=0, description="Bottles currently being held")
    counter_bottles: int = Field(default=0, ge=0, description="Bottles temporarily at counter")
    disposed_bottles: int = Field(default=0, ge=0, description="Total disposed bottles")
    uncertain_location: bool = Field(default=False, description="True if location tracking is uncertain")


class SensorEvent(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    event_id: str = Field(..., description="Unique event UUID")
    schema_version: str = Field(default="1.0")
    session_id: str = Field(..., description="Active session ID")
    timestamp: float = Field(..., description="Event epoch timestamp")
    media_time_ms: int = Field(..., description="Video media timestamp in milliseconds")
    event_type: str = Field(..., description="pickup | movement | release")
    sensor_id: str = Field(..., description="Sensor identifier")
    details: Dict[str, Any] = Field(default_factory=dict)


class MovementSession(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    session_id: str = Field(..., description="Movement session ID")
    medication_key: str = Field(..., description="Original medication key being moved")
    original_shelf_id: str = Field(..., description="Designated original shelf region ID")
    state: str = Field(
        default="ON_DESIGNATED_SHELF",
        description="ON_DESIGNATED_SHELF | HELD | AT_COUNTER | MISPLACED | DISPOSED | NEEDS_CONFIRMATION",
    )
    current_location_id: Optional[str] = Field(default=None, description="Current region ID")
    evidence: Dict[str, Any] = Field(default_factory=dict, description="CV joint keypoint proxy evidence")


class PrescriptionTransaction(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    transaction_id: str = Field(..., description="Unique transaction ID")
    medication_key: str = Field(..., description="Medication key")
    quantity: int = Field(..., gt=0, description="Tablet quantity to deduct")
    status: str = Field(default="created", description="created | confirmed_fill | paid | cancelled")
    deducted: bool = Field(default=False, description="True if tablet deduction has been applied")


class DisposalRecord(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    disposal_id: str = Field(..., description="Unique disposal record ID")
    session_id: str = Field(..., description="Movement session ID that triggered disposal")
    medication_key: str = Field(..., description="Medication key")
    selected_receipt_id: Optional[str] = Field(default=None, description="Employee selected receipt ID")
    quantity_deducted: int = Field(default=0, ge=0, description="Tablets deducted on disposal")
    is_default_quantity: bool = Field(default=True, description="True if auto default quantity applied")
    status: str = Field(default="pending_employee_entry", description="pending_employee_entry | resolved")


class Alert(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    alert_id: str = Field(..., description="Unique alert ID")
    alert_type: str = Field(
        ...,
        description="misplacement | uncertainty | expiry | out_of_stock | reconciliation_issue",
    )
    severity: str = Field(default="warning", description="info | warning | error")
    medication_key: str = Field(..., description="Target medication key")
    description: str = Field(..., description="Human-readable alert message")
    status: str = Field(default="open", description="open | resolved")
    metadata: Dict[str, Any] = Field(default_factory=dict)


class LayoutReceipt(BaseModel):
    """Preset received batch; runtime Receipt totals are derived from it on scenario load."""

    receipt_id: str = Field(..., min_length=1)
    medication_key: str
    bottle_count: int = Field(..., ge=1)
    tablets_per_bottle: int = Field(..., ge=0)
    expiry_date: str = Field(..., description="YYYY-MM-DD")
    lot_number: Optional[str] = None
    received_at: str = Field(..., description="Receipt timestamp ISO string")

    @field_validator("expiry_date")
    @classmethod
    def _iso_date(cls, value: str) -> str:
        date.fromisoformat(value)
        return value


class Layout(BaseModel):
    """Fixed-camera setup shared by every recording that references it."""

    layout_id: str = Field(..., pattern=LAYOUT_ID_PATTERN)
    calibration_version: int = Field(default=0, ge=0)
    frame_width: int = Field(default=1280, gt=0)
    frame_height: int = Field(default=720, gt=0)
    updated_at: Optional[str] = None
    background_image: Optional[str] = Field(
        default=None, pattern=BACKGROUND_PATTERN, description="Imported camera photo in the layout folder"
    )
    medications: List[Medication] = Field(default_factory=list)
    regions: List[Region] = Field(default_factory=list)
    receipts: List[LayoutReceipt] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> "Layout":
        issues: List[str] = []
        med_keys = [m.medication_key for m in self.medications]
        for m in self.medications:
            expected = medication_key_for(m.name, m.strength)
            if m.medication_key != expected:
                issues.append(f"Medication key {m.medication_key} should be {expected}")
        for key in {k for k in med_keys if med_keys.count(k) > 1}:
            issues.append(f"Duplicate medication {key}")

        region_ids = [r.region_id for r in self.regions]
        for rid in {r for r in region_ids if region_ids.count(r) > 1}:
            issues.append(f"Duplicate region ID {rid}")

        shelves_by_med: Dict[str, List[str]] = {k: [] for k in med_keys}
        for r in self.regions:
            if r.region_type == "designated_shelf":
                if r.medication_key not in shelves_by_med:
                    issues.append(f"Shelf {r.region_id} has no known medication assigned")
                else:
                    shelves_by_med[r.medication_key].append(r.region_id)
            elif r.medication_key is not None:
                issues.append(f"Region {r.region_id} is not a shelf and cannot hold a medication")
        for key, shelves in shelves_by_med.items():
            if not shelves:
                issues.append(f"{key} has no shelf drawn")
            elif len(shelves) > 1:
                issues.append(f"{key} is assigned to more than one shelf: {', '.join(shelves)}")

        receipt_ids = [r.receipt_id for r in self.receipts]
        for rid in {r for r in receipt_ids if receipt_ids.count(r) > 1}:
            issues.append(f"Duplicate receipt ID {rid}")
        for r in self.receipts:
            if r.medication_key not in shelves_by_med:
                issues.append(f"Receipt {r.receipt_id} references unknown medication {r.medication_key}")

        if issues:
            raise ValueError("; ".join(sorted(issues)))
        return self
