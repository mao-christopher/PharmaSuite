"""Live pharmacy state that carries across recordings, persisted atomically in MongoDB.

Holds the inventory engine's state, which recording signals have already been applied
(so replays, seeks and server restarts never apply a signal twice), and an append-only
history of every inventory-affecting action.
"""

import json
import copy
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pharma.db.models import Catalog, MovementSession, PrescriptionTransaction, Region, shelf_region_id
from pharma.services.inventory_engine import MIN_KEYPOINT_CONF, Hand, InventoryEngine
from pharma.services.layout import build_initial_state
from pharma.db.repository import MongoStateRepository, StateConflict, StorageUnavailable

STORE_VERSION = 1


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def session_key(recording: str, session_id: str) -> str:
    """Movement sessions are namespaced per recording; every upload restarts at sess_001."""
    return f"{recording}:{session_id}"


class PharmacyStore:
    def __init__(self, repository):
        self.repository = repository
        self.revision = None
        self.migration = None
        self._durable = None
        self.layout_id: Optional[str] = None
        self.engine: Optional[InventoryEngine] = None
        self.created_at: Optional[str] = None
        self.current_recording: Optional[str] = None
        self.recordings: Dict[str, Dict[str, Any]] = {}  # name -> applied event IDs + activity
        self.history: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------ lifecycle

    @classmethod
    def open(cls, path: Path, catalog: Catalog, repository=None) -> "PharmacyStore":
        """Import legacy JSON only if MongoDB has no pharmacy document; keep the source intact."""
        store = cls(repository if repository is not None else MongoStateRepository.configured())
        data = store.repository.load()
        if data is not None:
            store._load(data, catalog)
            return store
        if path.exists():
            content = path.read_bytes()
            data = json.loads(content)
            store._load(data, catalog)
            store.revision = None
            store.migration = {"source": str(path), "sha256": hashlib.sha256(content).hexdigest(),
                               "imported_at": now_iso()}
            store.record("migration", "Imported existing JSON inventory into MongoDB.")
        else:
            store.reset(catalog, note="Started from the opening stock.")
        try:
            store.save()
        except StateConflict:
            # A racing startup may win initialization. Never overwrite that winner.
            store.refresh()
        return store

    def refresh(self):
        data = self.repository.load()
        if data is None:
            raise StorageUnavailable("MongoDB inventory document is missing; refusing to reseed live stock.")
        regions = list(self.engine.regions.values()) if self.engine else []
        self._load(data)
        self.engine.regions = {r.region_id: r for r in regions}

    def rollback(self):
        if self._durable is not None:
            regions = list(self.engine.regions.values()) if self.engine else []
            self._load(copy.deepcopy(self._durable))
            self.engine.regions = {r.region_id: r for r in regions}

    def _load(self, data: Dict[str, Any], catalog: Optional[Catalog] = None) -> None:
        if data.get("version") != STORE_VERSION:
            raise ValueError("Unsupported inventory schema version; migration required.")
        self.revision = data.get("revision")
        self.migration = data.get("migration")
        self.layout_id = data.get("layout_id")
        self.created_at = data.get("created_at")
        self.current_recording = data.get("current_recording")
        self.recordings = data.get("recordings", {})
        self.history = data.get("history", [])
        self.engine = InventoryEngine.from_dict(data["engine"], regions=[])
        self._durable = copy.deepcopy(data)

    def reset(self, catalog: Catalog, note: str = "Inventory reset to opening stock.") -> None:
        _, inventory, receipts = build_initial_state(catalog)
        transactions = self.engine.transactions if self.engine else {}
        for tx in transactions.values():
            tx.status, tx.deducted = "created", False
        self.engine = InventoryEngine(inventory=inventory, regions=[], receipts=receipts,
                                      transactions=transactions)
        self.created_at = now_iso()
        self.recordings = {}
        self.record("reset", note)

    def save(self) -> None:
        payload = {
            "version": STORE_VERSION,
            "migration": self.migration,
            "layout_id": self.layout_id,
            "created_at": self.created_at,
            "saved_at": now_iso(),
            "current_recording": self.current_recording,
            "engine": self.engine.to_dict(),
            "recordings": self.recordings,
            "history": self.history,
        }
        try:
            saved = self.repository.save(payload, self.revision)
        except Exception:
            self.rollback()
            raise
        self.revision = saved["revision"]
        self._durable = copy.deepcopy(saved)

    def record(self, kind: str, summary: str, **detail: Any) -> None:
        """Append to the history (raw events and employee corrections are never rewritten)."""
        self.history.append({"at": now_iso(), "kind": kind, "summary": summary, **detail})

    # ------------------------------------------------------------------ layout changes

    def sync_catalog(self, catalog: Catalog, record: bool = True, note: Optional[str] = None) -> List[str]:
        """Adopt a re-saved catalog without losing live counts.

        New medications start with their opening stock. Bottles counted under an old,
        non-canonical shelf ID (and not explained by a misplaced bottle there) move to
        their medication's shelf, so every camera view counts the same shelves.
        """
        engine = self.engine
        notes: List[str] = []
        _, opening, receipts = build_initial_state(catalog)
        for key, inv in opening.items():
            if key not in engine.inventory:
                engine.inventory[key] = inv
                for r in receipts:
                    if r.medication_key == key and r.receipt_id not in engine.receipts:
                        engine.receipts[r.receipt_id] = r
                notes.append(f"Added {key} with its opening stock.")

        shelves = {shelf_region_id(k) for k in engine.inventory}
        sessions = self.unique_sessions()
        for s in sessions:
            home = shelf_region_id(s.medication_key) if s.medication_key in engine.inventory else None
            if home and s.original_shelf_id not in shelves and s.original_shelf_id != "UNKNOWN":
                s.original_shelf_id = home
            loc = s.current_location_id or ""
            if s.state == "MISPLACED" and home and loc.startswith("shelf_") and loc not in shelves:
                s.state, s.current_location_id = "ON_DESIGNATED_SHELF", home
                engine._resolve_alerts("misplacement", session_id=s.session_id)
                notes.append(f"A misplaced {s.medication_key} bottle was on a shelf that no longer exists; counted as returned.")

        for key, inv in engine.inventory.items():
            home = shelf_region_id(key)
            for region_id in list(inv.shelf_counts):
                if region_id == home:
                    continue
                misplaced_here = sum(
                    1 for s in sessions
                    if s.medication_key == key and s.state == "MISPLACED" and s.current_location_id == region_id
                )
                extra = inv.shelf_counts[region_id] - (misplaced_here if region_id in shelves else 0)
                if extra > 0:
                    inv.shelf_counts[region_id] -= extra
                    inv.shelf_counts[home] = inv.shelf_counts.get(home, 0) + extra
                    notes.append(f"Moved {extra} {key} bottle(s) from {region_id} to {home}.")
                if inv.shelf_counts[region_id] == 0:
                    del inv.shelf_counts[region_id]
            inv.shelf_counts.setdefault(home, 0)

        if record:
            self.record("layout", note or "Medications and opening stock saved.", notes=notes)
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

    def mark_confirmed(self, session_id: str, confirmed: Dict[str, str]) -> None:
        """After an employee confirms a location, show it on that session's signal-log rows.

        `confirmed` maps phase (pickup/release) to the chosen region. The original
        evidence stays in the history; only the displayed outcome is updated.
        """
        session = self.engine.sessions.get(session_id)
        recording = session_id.split(":", 1)[0]
        for row in self.recordings.get(recording, {}).get("activity", []):
            if row.get("session_id") != session_id:
                continue
            region = confirmed.get(row["event_type"])
            if region:
                row["confirmed_region_id"] = region
            if session is not None and (region or row.get("held_pending")):
                row["state"] = session.state
                row["medication_key"] = session.medication_key
                row["held_pending"] = False

    def merge_transactions(self, transactions: List[Dict[str, Any]]) -> bool:
        added = False
        for tx in transactions:
            if tx["transaction_id"] not in self.engine.transactions:
                self.engine.transactions[tx["transaction_id"]] = PrescriptionTransaction(**tx)
                added = True
        return added

    def apply_event(
        self,
        recording: str,
        event: Dict[str, Any],
        hands: List[Hand],
        frame_size,
        label: Optional[str] = None,
        regions: Optional[List[Region]] = None,
        layout_id: Optional[str] = None,
        joint: Optional[str] = "wrist",
        camera_id: Optional[str] = None,
        calibration_version: Optional[int] = None,
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
        if regions is not None:
            engine.regions = {r.region_id: r for r in regions}
        before = set(engine.alerts)
        sid = session_key(recording, event["session_id"])
        if event["event_type"] == "pickup":
            session = engine.handle_pickup(sid, hands, event.get("timestamp", 0.0))
        else:
            session = engine.handle_release(sid, hands, event.get("timestamp", 0.0))
        for alert_id in set(engine.alerts) - before:
            engine.alerts[alert_id].metadata.update(recording=recording, layout_id=layout_id, joint=joint,
                                                         camera_id=camera_id, calibration_version=calibration_version)
        evidence = session.evidence.get("pending_release", {}).get("evidence") or session.evidence
        activity = {
            "raw_event": copy.deepcopy(event),
            "camera_id": camera_id,
            "calibration_version": calibration_version,
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
            "joint": joint,
            "layout_id": layout_id,
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
