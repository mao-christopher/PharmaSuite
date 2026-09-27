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


def shelf_region_id(medication_key: str) -> str:
    """Every view names a medication's shelf the same way, so counts line up across views."""
    return f"shelf_{medication_key.lower()}"


class Medication(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    medication_key: str = Field(..., description="Unique key e.g. AMOXICILLIN_500MG")
    name: str = Field(..., min_length=1, description="Display name e.g. Amoxicillin")
    strength: str = Field(..., min_length=1, description="Strength e.g. 500mg")
    unit: str = Field(default="tablets", description="Dosage form unit")
    reorder_point: Optional[int] = Field(
        default=None, ge=0, description="Suggest reordering at or below this many units; None uses the default"
    )


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
    staged_bottles: int = Field(default=0, ge=0, description="Received bottles awaiting shelf placement")
    held_bottles: int = Field(default=0, ge=0, description="Bottles currently being held")
    counter_bottles: int = Field(default=0, ge=0, description="Bottles temporarily at counter")
    disposed_bottles: int = Field(default=0, ge=0, description="Total disposed bottles")
    uncertain_location: bool = Field(default=False, description="True if location tracking is uncertain")


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
    deducted_at: Optional[str] = Field(default=None, description="When the deduction was applied (ISO time)")


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


def _catalog_issues(medications: List["Medication"], receipts: List["LayoutReceipt"]) -> List[str]:
    issues: List[str] = []
    med_keys = [m.medication_key for m in medications]
    for m in medications:
        expected = medication_key_for(m.name, m.strength)
        if m.medication_key != expected:
            issues.append(f"Medication key {m.medication_key} should be {expected}")
    for key in {k for k in med_keys if med_keys.count(k) > 1}:
        issues.append(f"Duplicate medication {key}")
    receipt_ids = [r.receipt_id for r in receipts]
    for rid in {r for r in receipt_ids if receipt_ids.count(r) > 1}:
        issues.append(f"Duplicate receipt ID {rid}")
    for r in receipts:
        if r.medication_key not in med_keys:
            issues.append(f"Receipt {r.receipt_id} references unknown medication {r.medication_key}")
    return issues


class Catalog(BaseModel):
    """Medications and their opening stock, shared by every camera view."""

    updated_at: Optional[str] = None
    medications: List[Medication] = Field(default_factory=list)
    receipts: List[LayoutReceipt] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> "Catalog":
        issues = _catalog_issues(self.medications, self.receipts)
        if issues:
            raise ValueError("; ".join(sorted(issues)))
        return self


class RegionsSource(BaseModel):
    """Where a view's region polygons came from, when they were generated from a room's 3D boxes."""

    room_id: str
    room_version: int = Field(..., ge=1)
    registration_revision: int = Field(..., ge=1)
    algorithm: str


class Layout(BaseModel):
    """One fixed-camera view: frame, background photo, and region polygons.

    On disk a view holds only geometry; the API merges the shared catalog's
    medications and opening stock in (and splits them out again on save).
    `regions_source` is set when the polygons are generated from a scanned room's 3D
    boxes through this camera's registration; None means older hand-drawn polygons.
    """

    layout_id: str = Field(..., pattern=LAYOUT_ID_PATTERN)
    name: Optional[str] = Field(default=None, max_length=80)
    calibration_version: int = Field(default=0, ge=0)
    frame_width: int = Field(default=1280, gt=0)
    frame_height: int = Field(default=720, gt=0)
    updated_at: Optional[str] = None
    background_image: Optional[str] = Field(
        default=None, pattern=BACKGROUND_PATTERN, description="Imported camera photo in the layout folder"
    )
    medications: List[Medication] = Field(default_factory=list)
    regions: List[Region] = Field(default_factory=list)
    regions_source: Optional[RegionsSource] = None
    receipts: List[LayoutReceipt] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> "Layout":
        issues: List[str] = _catalog_issues(self.medications, self.receipts)
        med_keys = {m.medication_key for m in self.medications}

        # Shelves take their medication's canonical ID (and keep it across views).
        for r in self.regions:
            if r.region_type == "designated_shelf" and r.medication_key:
                r.region_id = shelf_region_id(r.medication_key)

        region_ids = [r.region_id for r in self.regions]
        shelves_by_med: Dict[str, int] = {}
        for r in self.regions:
            if r.region_type == "designated_shelf":
                if not r.medication_key or (self.medications and r.medication_key not in med_keys):
                    issues.append(f"Shelf {r.region_id} has no known medication assigned")
                else:
                    shelves_by_med[r.medication_key] = shelves_by_med.get(r.medication_key, 0) + 1
            elif r.medication_key is not None:
                issues.append(f"Region {r.region_id} is not a shelf and cannot hold a medication")
        for key, count in shelves_by_med.items():
            if count > 1:
                issues.append(f"{key} is assigned to more than one shelf in this view")
        for rid in {r for r in region_ids if region_ids.count(r) > 1}:
            if not rid.startswith("shelf_") or rid.split("shelf_", 1)[1].upper() not in shelves_by_med:
                issues.append(f"Duplicate region ID {rid}")

        if issues:
            raise ValueError("; ".join(sorted(issues)))
        return self

    def view_only(self) -> "Layout":
        return self.model_copy(update={"medications": [], "receipts": []})


# ---------------------------------------------------------------- scanned rooms
#
# Room frame: meters, right-handed, Y up, floor at y = 0 (the glTF convention), with
# the scan's walls turned to line up with X and Z. Scan geometry and registrations are
# setup data for the floor map and the simulation; inventory never reads them.

Vec3 = Tuple[float, float, float]
Matrix3 = Tuple[Vec3, Vec3, Vec3]


class RoomBox(BaseModel):
    """An upright box: `size` is (width along its own X, height, depth along its own Z)."""

    center: Vec3
    size: Vec3
    yaw_deg: float = Field(default=0.0, description="Rotation about +Y, counterclockwise seen from above")

    @field_validator("size")
    @classmethod
    def _positive(cls, size: Vec3) -> Vec3:
        if any(s <= 0 or s > 20 for s in size):
            raise ValueError("box sizes must be between 0 and 20 m")
        return size


class Region3D(BaseModel):
    """A shelf, counter or disposal region in the room, named like its camera-view polygons."""

    region_id: str = Field(..., min_length=1)
    region_type: RegionType
    medication_key: Optional[str] = None
    box: RoomBox


class Correspondence(BaseModel):
    """One point clicked in both the camera photo (normalized 0-1) and the 3D room."""

    image: Tuple[float, float]
    room: Vec3

    @field_validator("image")
    @classmethod
    def _normalized(cls, pt: Tuple[float, float]) -> Tuple[float, float]:
        if not (0.0 <= pt[0] <= 1.0 and 0.0 <= pt[1] <= 1.0):
            raise ValueError("image points must be normalized to 0.0-1.0")
        return pt


class CameraIntrinsics(BaseModel):
    """Pinhole lens in pixels of `CameraRegistration.frame_size`; no distortion model yet."""

    fx: float = Field(..., gt=0)
    fy: float = Field(..., gt=0)
    cx: float
    cy: float


class CameraRegistration(BaseModel):
    """Where one fixed camera (a camera view) sits in the room.

    Pose is OpenCV's world-to-camera transform: x_cam = rotation @ x_room + translation,
    with camera x right, y down, z forward.
    """

    layout_id: str = Field(..., pattern=LAYOUT_ID_PATTERN)
    revision: int = Field(default=1, ge=1)
    frame_size: Tuple[int, int]
    view_calibration_version: int = Field(..., ge=0, description="The view's calibration when registered")
    background_image: Optional[str] = Field(default=None, description="The view photo the points were clicked on")
    intrinsics: CameraIntrinsics
    rotation: Matrix3
    translation: Vec3
    position: Vec3 = Field(..., description="Camera center in room coordinates")
    rms_px: float = Field(..., ge=0)
    max_px: float = Field(..., ge=0)
    correspondences: List[Correspondence] = Field(..., min_length=6)
    solved_at: str


class RoomPlan(BaseModel):
    """Top-down images of the room: column -> +X, row -> +Z, starting at `origin` (x, z)."""

    image: str
    obstacles: str
    origin: Tuple[float, float]
    resolution_m: float = Field(..., gt=0)
    width: int = Field(..., gt=0)
    height: int = Field(..., gt=0)


class RoomMesh(BaseModel):
    file: str
    source_name: Optional[str] = None
    sha256: str
    bytes: int = Field(..., ge=0)
    triangles: int = Field(..., ge=0)


class Room(BaseModel):
    room_id: str = Field(..., pattern=LAYOUT_ID_PATTERN)
    name: Optional[str] = Field(default=None, max_length=80)
    room_version: int = Field(default=1, ge=1)
    created_at: str
    updated_at: str
    mesh: RoomMesh
    mesh_to_room: Tuple[Tuple[float, float, float, float], ...] = Field(
        ..., min_length=4, max_length=4, description="4x4 row-major transform from scan to room coordinates"
    )
    bounds_min: Vec3
    bounds_max: Vec3
    floor_fit: Dict[str, Any] = Field(default_factory=dict, description="How the floor was leveled")
    plan: RoomPlan
    regions: List[Region3D] = Field(default_factory=list)
    cameras: List[CameraRegistration] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> "Room":
        issues: List[str] = []
        shelves_by_med: Dict[str, int] = {}
        for r in self.regions:
            if r.region_type == "designated_shelf":
                if not r.medication_key:
                    issues.append(f"Shelf {r.region_id} has no medication assigned")
                    continue
                r.region_id = shelf_region_id(r.medication_key)
                shelves_by_med[r.medication_key] = shelves_by_med.get(r.medication_key, 0) + 1
            elif r.medication_key is not None:
                issues.append(f"Region {r.region_id} is not a shelf and cannot hold a medication")
        for key, count in shelves_by_med.items():
            if count > 1:
                issues.append(f"{key} is assigned to more than one shelf")
        ids = [r.region_id for r in self.regions]
        for rid in {i for i in ids if ids.count(i) > 1}:
            if not rid.startswith("shelf_"):
                issues.append(f"Duplicate region ID {rid}")
        views = [c.layout_id for c in self.cameras]
        for vid in {v for v in views if views.count(v) > 1}:
            issues.append(f"Camera view {vid} is registered twice")
        if issues:
            raise ValueError("; ".join(sorted(issues)))
        return self
