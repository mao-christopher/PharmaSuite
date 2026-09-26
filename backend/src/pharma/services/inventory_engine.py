"""Pharmacy Inventory Domain Engine.

Enforces non-negotiable rules for inventory tracking, movement state machine,
disposal defaults, prescription deductions, and alerts as specified in AGENTS.md & plan.md.
"""

import math
from typing import Dict, List, Optional, Any, Tuple
from pharma.db.models import (
    InventoryState,
    MovementSession,
    PrescriptionTransaction,
    DisposalRecord,
    Alert,
    Receipt,
    Region,
    shelf_region_id,
)

# (x, y, confidence) with x/y normalized to the frame.
Hand = Tuple[float, float, float]

MIN_KEYPOINT_CONF = 0.35
# Proposed, unvalidated: a hand farther than this fraction of the frame diagonal from every
# region is treated as "not at any region" and needs employee confirmation.
MAX_REGION_DISTANCE = 0.06
PICKUP_REGION_TYPES = ("designated_shelf", "dispensing_counter")


def point_in_polygon(x: float, y: float, polygon: List[Tuple[float, float]]) -> bool:
    """Even-odd ray cast for a normalized point against a closed polygon."""
    inside = False
    n = len(polygon)
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _segment_distance(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    t = 0.0 if length_sq == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def distance_to_region(x: float, y: float, region: Region, frame_size: Tuple[int, int]) -> float:
    """Distance from a normalized point to a region, as a fraction of the frame diagonal (0 inside)."""
    if point_in_polygon(x, y, region.polygon):
        return 0.0
    w, h = frame_size
    pts = [(px * w, py * h) for px, py in region.polygon]
    best = min(
        _segment_distance(x * w, y * h, *pts[i], *pts[(i + 1) % len(pts)]) for i in range(len(pts))
    )
    return best / math.hypot(w, h)


def nearest_region(
    hands: List[Hand],
    regions: List[Region],
    frame_size: Tuple[int, int] = (1280, 720),
    max_distance: float = MAX_REGION_DISTANCE,
    min_conf: float = MIN_KEYPOINT_CONF,
) -> Tuple[Optional[Region], Dict[str, Any]]:
    """Pick the region closest to any confident hand. Returns (region or None, evidence).

    None means the location is uncertain: no confident hand, nothing within max_distance,
    or two regions tied at the minimum distance (overlapping regions).
    """
    evidence: Dict[str, Any] = {"hands": [list(h) for h in hands], "max_distance": max_distance}
    confident = [h for h in hands if h[2] >= min_conf]
    if not confident or not regions:
        evidence["reason"] = "no_confident_hand" if not confident else "no_regions"
        return None, evidence

    scored = sorted(
        (distance_to_region(x, y, r, frame_size), r.region_id, r)
        for x, y, _ in confident
        for r in regions
    )
    best_dist, _, best = scored[0]
    ranked: Dict[str, float] = {}
    for dist, rid, _ in scored:
        ranked.setdefault(rid, dist)
    # Every region by its closest hand, nearest first, for the employee's confirmation choices.
    evidence["candidates"] = [{"region_id": rid, "distance": round(d, 4)} for rid, d in ranked.items()]
    evidence["distance"] = round(best_dist, 4)
    evidence["nearest_region_id"] = best.region_id
    if best_dist > max_distance:
        evidence["reason"] = "too_far"
        return None, evidence
    rival = next((s for s in scored[1:] if s[2].region_id != best.region_id), None)
    if rival and rival[0] - best_dist < 1e-9:
        evidence["reason"] = "ambiguous"
        evidence["tied_region_id"] = rival[2].region_id
        return None, evidence
    return best, evidence


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
        frame_size: Tuple[int, int] = (1280, 720),
        max_region_distance: float = MAX_REGION_DISTANCE,
    ):
        self.inventory = inventory  # medication_key -> InventoryState
        self.regions = {r.region_id: r for r in regions}
        self.receipts = {r.receipt_id: r for r in receipts}
        self.transactions = transactions  # transaction_id -> PrescriptionTransaction
        self.sessions = sessions or {}  # session_id -> MovementSession (aliases allowed)
        self.disposals = disposals or {}  # disposal_id -> DisposalRecord
        self.alerts = alerts or {}  # alert_id -> Alert
        self.frame_size = frame_size
        self.max_region_distance = max_region_distance
        self._alert_counter = 1
        self._receipt_counter = 1

    # ------------------------------------------------------------------ persistence

    def to_dict(self) -> Dict[str, Any]:
        """Serializable state. Regions come from the layout and are not included."""
        groups: Dict[int, Dict[str, Any]] = {}
        for key, session in self.sessions.items():
            group = groups.setdefault(id(session), {"keys": [], "session": session.model_dump()})
            group["keys"].append(key)
        return {
            "inventory": {k: v.model_dump() for k, v in self.inventory.items()},
            "receipts": [r.model_dump() for r in self.receipts.values()],
            "transactions": {k: v.model_dump() for k, v in self.transactions.items()},
            # Aliased sessions (a parked bottle picked up again) share one object.
            "sessions": list(groups.values()),
            "disposals": {k: v.model_dump() for k, v in self.disposals.items()},
            "alerts": {k: v.model_dump() for k, v in self.alerts.items()},
            "alert_counter": self._alert_counter,
            "receipt_counter": self._receipt_counter,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], regions: List[Region], **kwargs: Any) -> "InventoryEngine":
        sessions: Dict[str, MovementSession] = {}
        for group in data.get("sessions", []):
            session = MovementSession(**group["session"])
            for key in group["keys"]:
                sessions[key] = session
        engine = cls(
            inventory={k: InventoryState(**v) for k, v in data.get("inventory", {}).items()},
            regions=regions,
            receipts=[Receipt(**r) for r in data.get("receipts", [])],
            transactions={k: PrescriptionTransaction(**v) for k, v in data.get("transactions", {}).items()},
            sessions=sessions,
            disposals={k: DisposalRecord(**v) for k, v in data.get("disposals", {}).items()},
            alerts={k: Alert(**v) for k, v in data.get("alerts", {}).items()},
            **kwargs,
        )
        engine._alert_counter = data.get("alert_counter", len(engine.alerts) + 1)
        engine._receipt_counter = data.get("receipt_counter", 1)
        return engine

    # ------------------------------------------------------------------ helpers

    def _add_alert(self, alert_type: str, severity: str, medication_key: str, description: str,
                   metadata: Optional[Dict[str, Any]] = None) -> Alert:
        alert_id = f"alert_{self._alert_counter:04d}"
        self._alert_counter += 1
        alert = Alert(
            alert_id=alert_id,
            alert_type=alert_type,
            severity=severity,
            medication_key=medication_key,
            description=description,
            metadata=metadata or {},
        )
        self.alerts[alert_id] = alert
        return alert

    def _resolve_alerts(self, alert_type: str, **match: Any) -> None:
        for alert in self.alerts.values():
            if alert.alert_type != alert_type or alert.status != "open":
                continue
            if all(
                (alert.medication_key if k == "medication_key" else alert.metadata.get(k)) == v
                for k, v in match.items()
            ):
                alert.status = "resolved"

    def _locate(self, hands: List[Hand], region_types: Tuple[str, ...]) -> Tuple[Optional[Region], Dict[str, Any]]:
        candidates = [r for r in self.regions.values() if r.region_type in region_types]
        return nearest_region(hands, candidates, self.frame_size, self.max_region_distance)

    def _uncertain(self, session: MovementSession, phase: str, evidence: Dict[str, Any]) -> None:
        session.state = "NEEDS_CONFIRMATION"
        session.evidence = {**evidence, "awaiting": phase}
        what = "picked up from" if phase == "pickup" else "put down at"
        self._add_alert(
            "uncertainty",
            "warning",
            session.medication_key,
            f"Couldn't tell which region the bottle was {what}. Confirm the location.",
            {"session_id": session.session_id, "phase": phase, **evidence},
        )

    # ------------------------------------------------------------------ movement

    def handle_pickup(self, session_id: str, hands: List[Hand], timestamp: float) -> MovementSession:
        """Pickup signal: the bottle came from the region nearest the technician's hand."""
        region, evidence = self._locate(hands, PICKUP_REGION_TYPES)
        if region is None:
            session = MovementSession(
                session_id=session_id, medication_key="UNKNOWN", original_shelf_id="UNKNOWN"
            )
            self.sessions[session_id] = session
            self._uncertain(session, "pickup", evidence)
            return session
        return self._apply_pickup(session_id, region, evidence)

    def _parked_at(self, region: Region) -> Optional[MovementSession]:
        parked_state = "AT_COUNTER" if region.region_type == "dispensing_counter" else "MISPLACED"
        return next(
            (
                s for s in self.sessions.values()
                if s.state == parked_state and s.current_location_id == region.region_id
            ),
            None,
        )

    def _apply_pickup(self, session_id: str, region: Region, evidence: Dict[str, Any]) -> MovementSession:
        existing = self.sessions.get(session_id)
        pending_release = existing.evidence.get("pending_release") if existing else None

        # A bottle parked at the counter or misplaced on this shelf is assumed to be the one
        # being picked up; it keeps its original medication and home shelf.
        parked = self._parked_at(region)
        if parked:
            parked_state = parked.state
            inv = self.inventory.get(parked.medication_key)
            if inv:
                if parked_state == "AT_COUNTER":
                    inv.counter_bottles = max(0, inv.counter_bottles - 1)
                else:
                    inv.shelf_counts[region.region_id] = max(0, inv.shelf_counts.get(region.region_id, 0) - 1)
                inv.held_bottles += 1
            parked.state = "HELD"
            parked.evidence = evidence
            self.sessions[session_id] = parked
            if pending_release is not None:
                self._resume_release(parked, pending_release)
            return parked

        if region.region_type == "dispensing_counter":
            # Nothing parked at the counter: we can't know what was picked up.
            session = existing or MovementSession(
                session_id=session_id, medication_key="UNKNOWN", original_shelf_id="UNKNOWN"
            )
            self.sessions[session_id] = session
            self._uncertain(session, "pickup", {**evidence, "reason": "nothing_parked_at_counter"})
            return session

        med_key = region.medication_key or "UNKNOWN"
        inv = self.inventory.get(med_key)
        if inv and inv.shelf_counts.get(region.region_id, 0) > 0:
            inv.shelf_counts[region.region_id] -= 1
            inv.held_bottles += 1
        elif inv:
            self._add_alert(
                "reconciliation_issue", "warning", med_key,
                "Pickup detected from a shelf whose recorded count is already zero.",
                {"session_id": session_id, "region_id": region.region_id},
            )

        session = existing or MovementSession(session_id=session_id, medication_key=med_key,
                                              original_shelf_id=region.region_id)
        session.medication_key = med_key
        session.original_shelf_id = region.region_id
        session.state = "HELD"
        session.current_location_id = region.region_id
        session.evidence = evidence
        self.sessions[session_id] = session
        if pending_release is not None:
            self._resume_release(session, pending_release)
        return session

    def handle_release(self, session_id: str, hands: List[Hand], timestamp: float) -> MovementSession:
        """Release signal: the bottle went to the region nearest the technician's hand."""
        session = self.sessions.get(session_id)
        region, evidence = self._locate(hands, ("designated_shelf", "dispensing_counter", "disposal"))

        if session and session.state == "NEEDS_CONFIRMATION" and session.evidence.get("awaiting") == "pickup":
            # Hold the release until an employee confirms where the bottle came from.
            session.evidence["pending_release"] = {
                "region_id": region.region_id if region else None,
                "evidence": evidence,
            }
            return session

        if not session or session.state != "HELD":
            self._add_alert(
                "reconciliation_issue", "warning", session.medication_key if session else "UNKNOWN",
                "Put-down signal received with no bottle in hand.",
                {"session_id": session_id},
            )
            return session or MovementSession(
                session_id=session_id, medication_key="UNKNOWN", original_shelf_id="UNKNOWN",
                state="NEEDS_CONFIRMATION",
            )

        if region is None:
            self._uncertain(session, "release", evidence)
            return session
        return self._apply_release(session, region, evidence)

    def _resume_release(self, session: MovementSession, pending: Dict[str, Any]) -> None:
        region = self.regions.get(pending.get("region_id") or "")
        if region is None:
            self._uncertain(session, "release", pending.get("evidence", {}))
        else:
            self._apply_release(session, region, pending.get("evidence", {}))

    def _apply_release(self, session: MovementSession, region: Region, evidence: Dict[str, Any]) -> MovementSession:
        med_key = session.medication_key
        inv = self.inventory.get(med_key)
        if inv and inv.held_bottles > 0:
            inv.held_bottles -= 1
        session.current_location_id = region.region_id
        session.evidence = evidence
        # Wherever it lands now, any earlier misplacement of this bottle is over.
        self._resolve_alerts("misplacement", session_id=session.session_id)

        if region.region_type == "dispensing_counter":
            session.state = "AT_COUNTER"
            if inv:
                inv.counter_bottles += 1
            return session

        if region.region_type == "disposal":
            return self._dispose(session, inv)

        if inv:
            inv.shelf_counts[region.region_id] = inv.shelf_counts.get(region.region_id, 0) + 1
        if region.region_id == session.original_shelf_id:
            session.state = "ON_DESIGNATED_SHELF"
            return session

        session.state = "MISPLACED"
        self._add_alert(
            "misplacement", "error", med_key,
            f"{med_key} bottle placed on wrong shelf {region.region_id}.",
            {"session_id": session.session_id, "original_shelf": session.original_shelf_id,
             "placed_shelf": region.region_id},
        )
        return session

    def _dispose(self, session: MovementSession, inv: Optional[InventoryState]) -> MovementSession:
        session.state = "DISPOSED"
        med_key = session.medication_key
        total_before = inv.total_bottles if inv else 0
        default_qty = 0
        if inv:
            inv.total_bottles = max(0, inv.total_bottles - 1)
            inv.disposed_bottles += 1
            # Rule 8: last bottle -> whole pooled balance; otherwise assume the bottle was empty.
            if total_before == 1:
                default_qty = inv.pooled_tablets
                inv.pooled_tablets = 0

        disposal_id = f"disp_{session.session_id}"
        self.disposals[disposal_id] = DisposalRecord(
            disposal_id=disposal_id,
            session_id=session.session_id,
            medication_key=med_key,
            quantity_deducted=default_qty,
            is_default_quantity=True,
            status="pending_employee_entry",
        )
        if inv and inv.total_bottles == 0:
            self._add_alert(
                "out_of_stock", "error", med_key,
                f"Out of Stock: Total bottle count for {med_key} reached zero.",
            )
        return session

    def confirm_location(self, alert_id: str, region_id: str) -> MovementSession:
        """Apply an employee's answer to an uncertainty alert."""
        alert = self.alerts.get(alert_id)
        if not alert or alert.alert_type != "uncertainty":
            raise ValueError(f"Uncertainty alert {alert_id} not found.")
        if alert.status != "open":
            raise ValueError(f"Alert {alert_id} is already resolved.")
        region = self.regions.get(region_id)
        if region is None:
            raise ValueError(f"Unknown region {region_id}.")
        phase = alert.metadata.get("phase")
        session = self.sessions.get(alert.metadata.get("session_id", ""))
        if session is None:
            raise ValueError("The movement this alert refers to no longer exists.")

        evidence = {"confirmed_by_employee": True, "region_id": region_id}
        if phase == "pickup":
            if region.region_type not in PICKUP_REGION_TYPES:
                raise ValueError("A bottle can only be picked up from a shelf or counter.")
            if region.region_type == "dispensing_counter" and not self._parked_at(region):
                raise ValueError("No bottle is parked at that counter.")
            pending = session.evidence.get("pending_release")
            alert.status = "resolved"
            alert.metadata["resolved_region_id"] = region_id
            session.evidence = {"pending_release": pending} if pending is not None else {}
            self._apply_pickup(session.session_id, region, evidence)
        else:
            alert.status = "resolved"
            alert.metadata["resolved_region_id"] = region_id
            session.state = "HELD"
            self._apply_release(session, region, evidence)
        return session

    # ------------------------------------------------------------------ stock

    def receive_stock(
        self,
        medication_key: str,
        bottle_count: int,
        tablets_per_bottle: int,
        expiry_date: str,
        received_at: str,
        lot_number: Optional[str] = None,
        today_iso: Optional[str] = None,
    ) -> Receipt:
        """Add a received batch to live stock; its bottles go straight onto the designated shelf."""
        inv = self.inventory.get(medication_key)
        if inv is None:
            raise ValueError(f"{medication_key} is not a configured medication.")
        shelf_id = shelf_region_id(medication_key)
        if bottle_count < 1 or tablets_per_bottle < 0:
            raise ValueError("Bottle count must be at least 1 and tablets per bottle 0 or more.")

        receipt_id = f"RCV_{medication_key}_{self._receipt_counter:03d}"
        while receipt_id in self.receipts:
            self._receipt_counter += 1
            receipt_id = f"RCV_{medication_key}_{self._receipt_counter:03d}"
        self._receipt_counter += 1

        receipt = Receipt(
            receipt_id=receipt_id,
            medication_key=medication_key,
            bottle_count=bottle_count,
            remaining_bottles=bottle_count,
            total_tablets=bottle_count * tablets_per_bottle,
            expiry_date=expiry_date,
            lot_number=lot_number,
            received_at=received_at,
        )
        self.receipts[receipt_id] = receipt
        inv.total_bottles += bottle_count
        inv.pooled_tablets += receipt.total_tablets
        inv.shelf_counts[shelf_id] = inv.shelf_counts.get(shelf_id, 0) + bottle_count
        self._resolve_alerts("out_of_stock", medication_key=medication_key)
        if today_iso:
            self.trigger_expiry_alerts(today_iso)
        return receipt

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
        if record.status == "resolved":
            raise ValueError(f"Disposal {disposal_id} has already been identified.")
        receipt = self.receipts.get(selected_receipt_id)
        if not receipt or receipt.medication_key != record.medication_key:
            raise ValueError(f"Receipt {selected_receipt_id} is not a batch of {record.medication_key}.")
        if explicit_quantity is not None and explicit_quantity < 0:
            raise ValueError("Discard quantity cannot be negative.")

        record.selected_receipt_id = selected_receipt_id
        inv = self.inventory.get(record.medication_key)
        if explicit_quantity is not None:
            diff = explicit_quantity - record.quantity_deducted
            if inv:
                inv.pooled_tablets = max(0, inv.pooled_tablets - diff)
            record.quantity_deducted = explicit_quantity
            record.is_default_quantity = False
        record.status = "resolved"

        if receipt.remaining_bottles > 0:
            receipt.remaining_bottles -= 1
        # Rule 9: clear the expiry alert only once every bottle of that expired batch is gone.
        if receipt.remaining_bottles == 0:
            self._resolve_alerts("expiry", receipt_id=receipt.receipt_id)
        return record

    def dispose_batch(self, receipt_id: str, bottles: int, tablets: Optional[int] = None) -> DisposalRecord:
        """Employee removes bottles of a known batch from its shelf (e.g. expired stock).

        Rule 8 defaults apply when no tablet count is entered: zero if other bottles of the
        medication remain, the whole pooled balance if these were the last bottles.
        """
        receipt = self.receipts.get(receipt_id)
        if receipt is None:
            raise ValueError(f"Batch {receipt_id} not found.")
        if bottles < 1:
            raise ValueError("Dispose of at least one bottle.")
        if bottles > receipt.remaining_bottles:
            raise ValueError(f"Batch {receipt_id} has only {receipt.remaining_bottles} bottle(s) left.")
        if tablets is not None and tablets < 0:
            raise ValueError("Discarded tablets cannot be negative.")
        inv = self.inventory.get(receipt.medication_key)
        if inv is None:
            raise ValueError(f"{receipt.medication_key} is not a configured medication.")
        shelf_id = shelf_region_id(receipt.medication_key)
        on_shelf = inv.shelf_counts.get(shelf_id, 0)
        if bottles > on_shelf:
            raise ValueError(
                f"Only {on_shelf} bottle(s) are on the shelf. Return the others to the shelf, or let "
                "the camera record them going into the trash."
            )
        if tablets is not None and tablets > inv.pooled_tablets:
            raise ValueError(f"Only {inv.pooled_tablets} tablets are recorded for {receipt.medication_key}.")

        last_bottles = inv.total_bottles == bottles
        quantity = tablets if tablets is not None else (inv.pooled_tablets if last_bottles else 0)
        inv.shelf_counts[shelf_id] = on_shelf - bottles
        inv.total_bottles -= bottles
        inv.disposed_bottles += bottles
        inv.pooled_tablets -= quantity
        receipt.remaining_bottles -= bottles

        n = sum(1 for d in self.disposals if d.startswith("disp_manual_")) + 1
        record = DisposalRecord(
            disposal_id=f"disp_manual_{n:03d}",
            session_id="manual",
            medication_key=receipt.medication_key,
            selected_receipt_id=receipt_id,
            quantity_deducted=quantity,
            is_default_quantity=tablets is None,
            status="resolved",
        )
        self.disposals[record.disposal_id] = record
        if receipt.remaining_bottles == 0:
            self._resolve_alerts("expiry", receipt_id=receipt_id)
        if inv.total_bottles == 0:
            self._add_alert(
                "out_of_stock", "error", receipt.medication_key,
                f"Out of Stock: Total bottle count for {receipt.medication_key} reached zero.",
            )
        return record

    def add_transaction(
        self, medication_key: str, quantity: int, transaction_id: Optional[str] = None, status: str = "created"
    ) -> PrescriptionTransaction:
        """Record a prescription. Filled or paid ones deduct their tablets once, immediately."""
        if medication_key not in self.inventory:
            raise ValueError(f"{medication_key} is not a configured medication.")
        if quantity < 1:
            raise ValueError("Quantity must be at least 1.")
        if status not in ("created", "confirmed_fill", "paid", "cancelled"):
            raise ValueError(f"Unknown prescription status {status!r}.")
        if transaction_id:
            if transaction_id in self.transactions:
                raise ValueError(f"Prescription {transaction_id} already exists.")
        else:
            n = len(self.transactions) + 1
            while f"RX_{n:04d}" in self.transactions:
                n += 1
            transaction_id = f"RX_{n:04d}"
        tx = PrescriptionTransaction(transaction_id=transaction_id, medication_key=medication_key, quantity=quantity)
        self.transactions[transaction_id] = tx
        if status != "created":
            self.process_prescription_deduction(transaction_id, status)
        return tx

    def process_prescription_deduction(self, transaction_id: str, status: str) -> bool:
        """Deduct prescription tablets ONCE on confirmed_fill or paid status (idempotent)."""
        tx = self.transactions.get(transaction_id)
        if not tx:
            return False

        tx.status = status
        if status in ("confirmed_fill", "paid") and not tx.deducted:
            inv = self.inventory.get(tx.medication_key)
            if inv:
                inv.pooled_tablets = max(0, inv.pooled_tablets - tx.quantity)
            tx.deducted = True
            return True
        return False

    def trigger_expiry_alerts(self, current_date_iso: str):
        """Open one expiry alert per expired batch that still has bottles."""
        for receipt in self.receipts.values():
            if receipt.expiry_date < current_date_iso and receipt.remaining_bottles > 0:
                existing = any(
                    a.alert_type == "expiry" and a.metadata.get("receipt_id") == receipt.receipt_id
                    for a in self.alerts.values()
                )
                if not existing:
                    self._add_alert(
                        "expiry", "warning", receipt.medication_key,
                        f"Batch {receipt.receipt_id} for {receipt.medication_key} expired on {receipt.expiry_date}.",
                        {"receipt_id": receipt.receipt_id, "expiry_date": receipt.expiry_date},
                    )
