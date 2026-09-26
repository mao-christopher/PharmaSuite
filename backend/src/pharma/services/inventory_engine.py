"""Pharmacy Inventory Domain Engine.

Enforces non-negotiable rules for inventory tracking, movement state machine,
disposal defaults, prescription deductions, and alerts as specified in AGENTS.md & plan.md.
"""

from typing import Dict, List, Optional, Any, Tuple
from pharma.db.models import (
    InventoryState,
    MovementSession,
    PrescriptionTransaction,
    DisposalRecord,
    Alert,
    Receipt,
    Region,
)


def point_in_bbox(x: float, y: float, bbox: List[float]) -> bool:
    """Check if normalized coordinate (x, y) lies inside bounding box [xmin, ymin, xmax, ymax]."""
    x_min, y_min, x_max, y_max = bbox
    return x_min <= x <= x_max and y_min <= y <= y_max


def associate_region(wrist_xy: Tuple[float, float], confidence: float, regions: List[Region], min_conf: float = 0.35) -> Tuple[Optional[Region], bool]:
    """Associate wrist keypoint with camera region. Returns (matching_region, is_uncertain)."""
    if confidence < min_conf:
        return None, True

    x, y = wrist_xy
    matches = [r for r in regions if point_in_bbox(x, y, r.bbox)]

    if not matches:
        return None, False  # Hand is in un-mapped area

    if len(matches) > 1:
        return None, True  # Ambiguous region overlap

    return matches[0], False


class InventoryEngine:
    def __init__(
        self,
        inventory: Dict[str, InventoryState],
        regions: List[Region],
        receipts: List[Receipt],
        transactions: Dict[str, PrescriptionTransaction],
        sessions: Optional[Dict[str, MovementSession]] = None,
        disposals: Optional[Dict[str, DisposalRecord]] = None,
        alerts: Optional[Dict[str, Alert]] = None,
    ):
        self.inventory = inventory  # medication_key -> InventoryState
        self.regions = {r.region_id: r for r in regions}
        self.receipts = {r.receipt_id: r for r in receipts}
        self.transactions = transactions  # transaction_id -> PrescriptionTransaction
        self.sessions = sessions or {}  # session_id -> MovementSession
        self.disposals = disposals or {}  # disposal_id -> DisposalRecord
        self.alerts = alerts or {}  # alert_id -> Alert
        self._alert_counter = 1

    def _generate_alert_id(self) -> str:
        aid = f"alert_{self._alert_counter:04d}"
        self._alert_counter += 1
        return aid

    def handle_pickup(
        self,
        session_id: str,
        wrist_xy: Tuple[float, float],
        wrist_conf: float,
        timestamp: float,
    ) -> MovementSession:
        """Handle bottle pickup event."""
        matched_region, uncertain = associate_region(wrist_xy, wrist_conf, list(self.regions.values()))

        if uncertain or matched_region is None or matched_region.region_type not in ("designated_shelf", "dispensing_counter"):
            session = MovementSession(
                session_id=session_id,
                medication_key="UNKNOWN",
                original_shelf_id="UNKNOWN",
                state="NEEDS_CONFIRMATION",
                evidence={"wrist_xy": wrist_xy, "conf": wrist_conf},
            )
            self.sessions[session_id] = session
            alert_id = self._generate_alert_id()
            self.alerts[alert_id] = Alert(
                alert_id=alert_id,
                alert_type="uncertainty",
                severity="warning",
                medication_key="UNKNOWN",
                description="Uncertain pickup location or low keypoint confidence. Employee confirmation required.",
                status="open",
                metadata={"session_id": session_id},
            )
            return session

        med_key = matched_region.medication_key or "UNKNOWN"

        # If picked up from counter, check active parked session
        if matched_region.region_type == "dispensing_counter":
            parked_session = next(
                (s for s in self.sessions.values() if s.state == "AT_COUNTER"), None
            )
            if parked_session:
                session = parked_session
                session.state = "HELD"
                self.sessions[session_id] = session
                med_key = session.medication_key
                inv = self.inventory.get(med_key)
                if inv and inv.counter_bottles > 0:
                    inv.counter_bottles -= 1
                    inv.held_bottles += 1
                return session

        # Pickup from designated shelf
        inv = self.inventory.get(med_key)
        if inv and inv.shelf_counts.get(matched_region.region_id, 0) > 0:
            inv.shelf_counts[matched_region.region_id] -= 1
            inv.held_bottles += 1

        session = MovementSession(
            session_id=session_id,
            medication_key=med_key,
            original_shelf_id=matched_region.region_id,
            state="HELD",
            current_location_id=matched_region.region_id,
            evidence={"wrist_xy": wrist_xy, "conf": wrist_conf},
        )
        self.sessions[session_id] = session
        return session

    def handle_release(
        self,
        session_id: str,
        wrist_xy: Tuple[float, float],
        wrist_conf: float,
        timestamp: float,
    ) -> MovementSession:
        """Handle bottle release event."""
        session = self.sessions.get(session_id)
        if not session or session.state != "HELD":
            # Unmatched release
            alert_id = self._generate_alert_id()
            self.alerts[alert_id] = Alert(
                alert_id=alert_id,
                alert_type="reconciliation_issue",
                severity="warning",
                medication_key="UNKNOWN",
                description="Release detected without prior active held session.",
                status="open",
            )
            return MovementSession(
                session_id=session_id,
                medication_key="UNKNOWN",
                original_shelf_id="UNKNOWN",
                state="NEEDS_CONFIRMATION",
            )

        matched_region, uncertain = associate_region(wrist_xy, wrist_conf, list(self.regions.values()))

        if uncertain or matched_region is None:
            session.state = "NEEDS_CONFIRMATION"
            alert_id = self._generate_alert_id()
            self.alerts[alert_id] = Alert(
                alert_id=alert_id,
                alert_type="uncertainty",
                severity="warning",
                medication_key=session.medication_key,
                description=f"Uncertain release location for {session.medication_key}.",
                status="open",
            )
            return session

        med_key = session.medication_key
        inv = self.inventory.get(med_key)

        # 1. Release at Dispensing Counter
        if matched_region.region_type == "dispensing_counter":
            session.state = "AT_COUNTER"
            session.current_location_id = matched_region.region_id
            if inv:
                if inv.held_bottles > 0:
                    inv.held_bottles -= 1
                inv.counter_bottles += 1
            return session

        # 2. Release at Disposal Region
        if matched_region.region_type == "disposal":
            session.state = "DISPOSED"
            session.current_location_id = matched_region.region_id
            total_before_disposal = inv.total_bottles if inv else 1

            if inv:
                if inv.held_bottles > 0:
                    inv.held_bottles -= 1
                if inv.total_bottles > 0:
                    inv.total_bottles -= 1

            # Determine disposal quantity default rule
            # Rule 8: If exactly 1 bottle total before disposal, default = entire pooled balance.
            # If multiple bottles present before disposal, default = 0 (assumed empty).
            default_qty = 0
            if total_before_disposal == 1 and inv:
                default_qty = inv.pooled_tablets
                inv.pooled_tablets = 0
            elif total_before_disposal > 1 and inv:
                default_qty = 0

            disposal_id = f"disp_{session_id}"
            disposal_record = DisposalRecord(
                disposal_id=disposal_id,
                session_id=session_id,
                medication_key=med_key,
                quantity_deducted=default_qty,
                is_default_quantity=True,
                status="pending_employee_entry",
            )
            self.disposals[disposal_id] = disposal_record

            # Check zero stock alert rule
            if inv and inv.total_bottles == 0:
                alert_id = self._generate_alert_id()
                self.alerts[alert_id] = Alert(
                    alert_id=alert_id,
                    alert_type="out_of_stock",
                    severity="error",
                    medication_key=med_key,
                    description=f"Out of Stock: Total bottle count for {med_key} reached zero.",
                    status="open",
                )
            return session

        # 3. Release at Designated Shelf
        if matched_region.region_type == "designated_shelf":
            # Check if correct shelf
            if matched_region.region_id == session.original_shelf_id or matched_region.medication_key == med_key:
                session.state = "ON_DESIGNATED_SHELF"
                session.current_location_id = matched_region.region_id
                if inv:
                    if inv.held_bottles > 0:
                        inv.held_bottles -= 1
                    inv.shelf_counts[matched_region.region_id] = inv.shelf_counts.get(matched_region.region_id, 0) + 1
                return session
            else:
                # Release on wrong shelf -> MISPLACED alert. Keep original medication identity!
                session.state = "MISPLACED"
                session.current_location_id = matched_region.region_id
                if inv:
                    if inv.held_bottles > 0:
                        inv.held_bottles -= 1
                    inv.shelf_counts[matched_region.region_id] = inv.shelf_counts.get(matched_region.region_id, 0) + 1

                alert_id = self._generate_alert_id()
                self.alerts[alert_id] = Alert(
                    alert_id=alert_id,
                    alert_type="misplacement",
                    severity="error",
                    medication_key=med_key,
                    description=f"Misplaced Stock: {med_key} bottle placed on wrong shelf {matched_region.region_id}.",
                    status="open",
                    metadata={"original_shelf": session.original_shelf_id, "placed_shelf": matched_region.region_id},
                )
                return session

        return session

    def resolve_disposal(
        self,
        disposal_id: str,
        selected_receipt_id: str,
        explicit_quantity: Optional[int] = None,
    ) -> DisposalRecord:
        """Resolve pending disposal form with employee receipt selection & tablet quantity."""
        record = self.disposals.get(disposal_id)
        if not record:
            raise ValueError(f"Disposal record {disposal_id} not found.")

        record.selected_receipt_id = selected_receipt_id
        med_key = record.medication_key
        inv = self.inventory.get(med_key)

        if explicit_quantity is not None:
            # Employee explicitly entered tablet quantity discarded
            if explicit_quantity < 0:
                raise ValueError("Discard quantity cannot be negative.")

            previous_deduction = record.quantity_deducted
            diff = explicit_quantity - previous_deduction

            if inv:
                inv.pooled_tablets = max(0, inv.pooled_tablets - diff)

            record.quantity_deducted = explicit_quantity
            record.is_default_quantity = False

        record.status = "resolved"

        # Update matching receipt remaining bottles
        receipt = self.receipts.get(selected_receipt_id)
        if receipt and receipt.remaining_bottles > 0:
            receipt.remaining_bottles -= 1

        # Check and resolve matching expiry alert for this receipt
        for alert_id, alert in list(self.alerts.items()):
            if alert.alert_type == "expiry" and alert.medication_key == med_key:
                if receipt and receipt.remaining_bottles == 0:
                    alert.status = "resolved"

        return record

    def process_prescription_deduction(self, transaction_id: str, status: str) -> bool:
        """Deduct prescription tablets ONCE on confirmed_fill or paid status (idempotent)."""
        tx = self.transactions.get(transaction_id)
        if not tx:
            return False

        tx.status = status

        # Deduct ONLY on confirmed_fill or paid, and ONLY IF NOT PREVIOUSLY DEDUCTED
        if status in ("confirmed_fill", "paid") and not tx.deducted:
            inv = self.inventory.get(tx.medication_key)
            if inv:
                inv.pooled_tablets = max(0, inv.pooled_tablets - tx.quantity)
            tx.deducted = True
            return True

        return False

    def trigger_expiry_alerts(self, current_date_iso: str):
        """Scan receipts and generate expiry alerts for expired batches."""
        for receipt in self.receipts.values():
            if receipt.expiry_date <= current_date_iso and receipt.remaining_bottles > 0:
                existing = any(
                    a.alert_type == "expiry"
                    and a.metadata.get("receipt_id") == receipt.receipt_id
                    and a.status == "open"
                    for a in self.alerts.values()
                )
                if not existing:
                    alert_id = self._generate_alert_id()
                    self.alerts[alert_id] = Alert(
                        alert_id=alert_id,
                        alert_type="expiry",
                        severity="warning",
                        medication_key=receipt.medication_key,
                        description=f"Expiration Alert: Batch {receipt.receipt_id} for {receipt.medication_key} expired on {receipt.expiry_date}.",
                        status="open",
                        metadata={"receipt_id": receipt.receipt_id, "expiry_date": receipt.expiry_date},
                    )
