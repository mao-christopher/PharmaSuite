"""Pydantic schemas and MongoDB document definitions for Pharma inventory management."""

from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field, ConfigDict


class Medication(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    medication_key: str = Field(..., description="Unique key e.g. AMOXICILLIN_500MG")
    name: str = Field(..., description="Display name e.g. Amoxicillin")
    strength: str = Field(..., description="Strength e.g. 500mg")
    unit: str = Field(default="tablets", description="Dosage form unit")


class Region(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    region_id: str = Field(..., description="Unique region ID e.g. shelf_amoxicillin_500mg")
    region_type: str = Field(..., description="designated_shelf | dispensing_counter | disposal")
    medication_key: Optional[str] = Field(default=None, description="Designated medication key if shelf")
    bbox: List[float] = Field(..., description="[x_min, y_min, x_max, y_max] in normalized 0.0-1.0 coordinates")


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
