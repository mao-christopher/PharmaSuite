"""Live pharmacy state that carries across recordings, persisted as JSON until MongoDB.

Holds the inventory engine's state, which recording signals have already been applied
(so replays, seeks and server restarts never apply a signal twice), and an append-only
history of every inventory-affecting action.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pharma.db.models import Layout, MovementSession, PrescriptionTransaction
from pharma.services.inventory_engine import MIN_KEYPOINT_CONF, Hand, InventoryEngine
from pharma.services.layout import build_initial_state

STORE_VERSION = 1


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def session_key(recording: str, session_id: str) -> str:
    """Movement sessions are namespaced per recording; every upload restarts at sess_001."""
    return f"{recording}:{session_id}"


class PharmacyStore:
    def __init__(self, path: Path):
        self.path = path
        self.layout_id: Optional[str] = None
        self.engine: Optional[InventoryEngine] = None
        self.created_at: Optional[str] = None
        self.current_recording: Optional[str] = None
        self.recordings: Dict[str, Dict[str, Any]] = {}  # name -> applied event IDs + activity
        self.history: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------ lifecycle

    @classmethod
    def open(cls, path: Path, layout: Layout) -> "PharmacyStore":
        """Load the saved state for this layout, or start from the layout's opening stock."""
        store = cls(path)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("layout_id") == layout.layout_id:
                store._load(data, layout)
                return store
        store.reset(layout, note="Started from the layout's opening stock.")
        return store

    def _load(self, data: Dict[str, Any], layout: Layout) -> None:
        self.layout_id = data["layout_id"]
        self.created_at = data.get("created_at")
        self.current_recording = data.get("current_recording")
        self.recordings = data.get("recordings", {})
        self.history = data.get("history", [])
        self.engine = InventoryEngine.from_dict(data["engine"], regions=list(layout.regions))
        self.sync_layout(layout, record=False)

    def reset(self, layout: Layout, note: str = "Inventory reset to opening stock.") -> None:
        regions, inventory, receipts = build_initial_state(layout)
        transactions = self.engine.transactions if self.engine else {}
        for tx in transactions.values():
            tx.status, tx.deducted = "created", False
        self.engine = InventoryEngine(inventory=inventory, regions=regions, receipts=receipts,
                                      transactions=transactions)
        self.layout_id = layout.layout_id
        self.created_at = now_iso()
        self.recordings = {}
        self.record("reset", note)

    def save(self) -> None:
        payload = {
            "version": STORE_VERSION,
            "layout_id": self.layout_id,
            "created_at": self.created_at,
            "saved_at": now_iso(),
            "current_recording": self.current_recording,
            "engine": self.engine.to_dict(),
            "recordings": self.recordings,
            "history": self.history,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, self.path)

    def record(self, kind: str, summary: str, **detail: Any) -> None:
        """Append to the history (raw events and employee corrections are never rewritten)."""
        self.history.append({"at": now_iso(), "kind": kind, "summary": summary, **detail})

    # ------------------------------------------------------------------ layout changes

    def sync_layout(self, layout: Layout, record: bool = True) -> List[str]:
        """Adopt a re-saved layout without losing live counts.

        New medications start with their opening stock. Bottles counted on a shelf that
        was deleted or reassigned (and not explained by a misplaced bottle) move to their
        medication's current shelf.
        """
        engine = self.engine
        engine.regions = {r.region_id: r for r in layout.regions}
        home = {r.medication_key: r.region_id for r in layout.regions if r.region_type == "designated_shelf"}
        notes: List[str] = []

        _, opening, receipts = build_initial_state(layout)
        for key, inv in opening.items():
            if key not in engine.inventory:
                engine.inventory[key] = inv
                for r in receipts:
                    if r.medication_key == key and r.receipt_id not in engine.receipts:
                        engine.receipts[r.receipt_id] = r
                notes.append(f"Added {key} with its opening stock.")

        sessions = self.unique_sessions()
        for s in sessions:
            target = home.get(s.medication_key)
            if target and s.original_shelf_id not in engine.regions:
                s.original_shelf_id = target
            if s.state == "MISPLACED" and s.current_location_id not in engine.regions and target:
                s.state, s.current_location_id = "ON_DESIGNATED_SHELF", target
                engine._resolve_alerts("misplacement", session_id=s.session_id)
                notes.append(f"A misplaced {s.medication_key} bottle was on a deleted shelf; counted as returned.")

        for key, inv in engine.inventory.items():
            target = home.get(key)
            if not target:
                continue
            for region_id in list(inv.shelf_counts):
                if region_id == target:
                    continue
                misplaced_here = sum(
                    1 for s in sessions
                    if s.medication_key == key and s.state == "MISPLACED" and s.current_location_id == region_id
                )
                extra = inv.shelf_counts[region_id] - misplaced_here
                if extra > 0:
                    inv.shelf_counts[region_id] -= extra
                    inv.shelf_counts[target] = inv.shelf_counts.get(target, 0) + extra
                    notes.append(f"Moved {extra} {key} bottle(s) from {region_id} to {target}.")
                if inv.shelf_counts[region_id] == 0:
                    del inv.shelf_counts[region_id]
            inv.shelf_counts.setdefault(target, 0)

        self.layout_id = layout.layout_id
        if record:
            self.record("layout", f"Layout saved as calibration v{layout.calibration_version}.",
                        calibration_version=layout.calibration_version, notes=notes)
        return notes

    # ------------------------------------------------------------------ recordings

    def unique_sessions(self) -> List[MovementSession]:
        seen: Dict[int, MovementSession] = {}
        for s in self.engine.sessions.values():
            seen.setdefault(id(s), s)
        return list(seen.values())

    def recording_entry(self, name: str) -> Dict[str, Any]:
        return self.recordings.setdefault(name, {"applied_event_ids": [], "activity": []})

    def applied_event_ids(self, name: str) -> set:
        return set(self.recordings.get(name, {}).get("applied_event_ids", []))

    def merge_transactions(self, transactions: List[Dict[str, Any]]) -> bool:
        added = False
        for tx in transactions:
            if tx["transaction_id"] not in self.engine.transactions:
                self.engine.transactions[tx["transaction_id"]] = PrescriptionTransaction(**tx)
                added = True
        return added

    def apply_event(
        self, recording: str, event: Dict[str, Any], hands: List[Hand], frame_size, label: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Apply one pickup/release signal exactly once. Returns its activity entry, or None if seen."""
        entry = self.recording_entry(recording)
        if event["event_id"] in entry["applied_event_ids"]:
            return None
        entry["applied_event_ids"].append(event["event_id"])
        entry.setdefault("first_applied_at", now_iso())
        entry["last_applied_at"] = now_iso()
        if event["event_type"] not in ("pickup", "release"):
            return None

        engine = self.engine
        engine.frame_size = tuple(frame_size)
        sid = session_key(recording, event["session_id"])
        if event["event_type"] == "pickup":
            session = engine.handle_pickup(sid, hands, event.get("timestamp", 0.0))
        else:
            session = engine.handle_release(sid, hands, event.get("timestamp", 0.0))
        evidence = session.evidence.get("pending_release", {}).get("evidence") or session.evidence
        activity = {
            "event_id": event["event_id"],
            "media_time_ms": event["media_time_ms"],
            "event_type": event["event_type"],
            "session_id": sid,
            "medication_key": session.medication_key,
            "state": session.state,
            "held_pending": "pending_release" in session.evidence,
            "nearest_region_id": evidence.get("nearest_region_id"),
            "distance": evidence.get("distance"),
            "reason": evidence.get("reason"),
            "hands_seen": sum(1 for h in hands if h[2] >= MIN_KEYPOINT_CONF),
        }
        entry["activity"].append(activity)
        verb = "Pickup" if event["event_type"] == "pickup" else "Put-down"
        self.record(
            "signal", f"{verb} signal from {label or recording}.",
            recording=recording, event_id=event["event_id"], media_time_ms=event["media_time_ms"],
            medication_key=session.medication_key, region_id=evidence.get("nearest_region_id"),
            result=session.state,
        )
        return activity
