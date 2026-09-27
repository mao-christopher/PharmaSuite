"""Shelf layout from imported shipments: plan (optionally with Meta's Llama API), review, apply.

Planning can wait up to a minute on the Llama API, so it runs outside the inventory lock
(see main.py) and only takes the lock to copy its inputs. Applying takes the lock like
any setup write: it adds missing medications to the catalog, replaces the room's shelf
boxes, and regenerates every registered camera's regions. It never changes stock counts.
"""

import copy
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from pharma.api.live_routes import inventory_transaction
from pharma.api.replay_stream import ReplayController
from pharma.api.room_routes import _room, _room_payload, _sync_views
from pharma.api.routes import get_controller
from pharma.db.models import Room
from pharma.services import meta_llama, shelf_layout
from pharma.services.layout import save_catalog
from pharma.services.room import list_rooms, save_room, validate_regions
from pharma.services.stocking import active_shipment

router = APIRouter(prefix="/api/shelf-layout")

BUSY_STATES = ("HELD", "AT_COUNTER", "MISPLACED", "NEEDS_CONFIRMATION")


class PlanRequest(BaseModel):
    room_id: str
    use_meta: bool = Field(default=True, description="Ask Meta's Llama API when a token is configured")


class Assignment(BaseModel):
    medication_key: str
    slot_id: str


class MixupPair(BaseModel):
    medication_keys: List[str] = Field(..., min_length=2, max_length=2)
    kind: Optional[str] = None
    reason: Optional[str] = None


class ApplyRequest(BaseModel):
    room_id: str
    room_version: int
    assignments: List[Assignment] = Field(..., min_length=1)
    source: Optional[Literal["meta-llama", "built-in"]] = None
    mixup_pairs: List[MixupPair] = Field(default_factory=list,
                                         description="Extra pairs from the plan (e.g. the model's) to keep apart")
    acknowledge_mixups: bool = Field(default=False,
                                     description="Apply even though some mix-up pairs share a shelf or touch")


def _on_hand(ctrl: ReplayController):
    return {key: inv.total_bottles for key, inv in ctrl.engine.inventory.items()}


@router.get("/status")
def layout_status(ctrl: ReplayController = Depends(get_controller)):
    """Whether a Meta API token is configured, and the rooms a layout can be planned for."""
    rooms = [{"room_id": r.room_id, "name": r.name or r.room_id, "room_version": r.room_version,
              "shelves": sum(1 for g in r.regions if g.region_type == "designated_shelf")}
             for r in list_rooms(ctrl.rooms_dir)]
    return {"meta": meta_llama.status(), "rooms": rooms}


@router.post("/plan")
async def plan(req: PlanRequest, ctrl: ReplayController = Depends(get_controller)):
    """Propose a shelf layout for the room. Changes nothing."""
    async with inventory_transaction(ctrl):
        room = _room(ctrl, req.room_id)
        catalog = ctrl.catalog.model_copy(deep=True)
        shipments = copy.deepcopy(ctrl.engine.shipments)
        on_hand = _on_hand(ctrl)
    try:
        return await run_in_threadpool(shelf_layout.plan_layout, ctrl.rooms_dir, room, catalog, shipments, on_hand,
                                       use_meta=req.use_meta)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/apply")
async def apply(req: ApplyRequest, ctrl: ReplayController = Depends(get_controller)):
    """Adopt a reviewed layout: add its new medications, then replace the room's shelf boxes."""
    room = _room(ctrl, req.room_id)
    if req.room_version != room.room_version:
        raise HTTPException(status_code=409, detail="The room changed since this layout was planned. Plan again.")
    if active_shipment(ctrl.engine):
        raise HTTPException(status_code=409, detail="Finish shipment stocking before changing the shelf layout.")
    if any(s.state in BUSY_STATES for s in ctrl.engine.sessions.values()):
        raise HTTPException(status_code=409, detail="Resolve bottles that are held, at the counter, misplaced or "
                                                    "awaiting confirmation before changing the shelf layout.")
    assignments = {a.medication_key: a.slot_id for a in req.assignments}
    if len(assignments) != len(req.assignments):
        raise HTTPException(status_code=422, detail="A medication appears twice in the layout.")
    try:
        shelf = shelf_layout.shelving(ctrl.rooms_dir, room)
        meds = shelf_layout.demand(room, ctrl.catalog, ctrl.engine.shipments, _on_hand(ctrl))
        regions = shelf_layout.layout_regions(room, shelf, assignments, meds)
        risks = shelf_layout.risk_pairs(meds)
        risks += shelf_layout.extra_pairs([p.model_dump() for p in req.mixup_pairs], meds, risks, "plan")
        close = shelf_layout.conflicts(risks, assignments, shelf.by_id())
        catalog, added = shelf_layout.catalog_with(ctrl.catalog, meds)
        validate_regions(regions, catalog)
        updated = Room.model_validate({**room.model_dump(mode="json"), "regions": [r.model_dump() for r in regions]})
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if close and not req.acknowledge_mixups:
        names = {m["medication_key"]: f"{m['name']} {m['strength']}" for m in meds}
        pairs = "; ".join(" / ".join(names[k] for k in p["medication_keys"]) for p in close[:3])
        raise HTTPException(status_code=409, detail=f"{len(close)} mix-up pair(s) would share a shelf or sit side by "
                                                    f"side ({pairs}). Confirm to apply anyway.")
    # Everything is validated; now write: catalog first, since shelves must name configured medications.
    if added:
        ctrl.apply_catalog(save_catalog(ctrl.layouts_dir, catalog))
    saved = save_room(ctrl.rooms_dir, updated)
    shelf_layout.save_shelving(ctrl.rooms_dir, saved, shelf.rows)
    notes = await _sync_views(ctrl, [c.layout_id for c in saved.cameras])
    moved = sorted(k for k, sid in assignments.items() if shelf.current.get(k) and sid not in shelf.current[k])
    await ctrl.commit("shelf_layout", f"Applied a shelf layout to {saved.name or saved.room_id} "
                      f"({len(assignments)} shelves, {len(added)} new medications).",
                      room_id=saved.room_id, source=req.source, added=added, moved=moved,
                      assignments=dict(sorted(assignments.items())),
                      mixups_together=[p["medication_keys"] for p in close])
    return {**_room_payload(saved), "added_medications": added, "moved": moved, "view_updates": notes,
            "mixups_together": [p["medication_keys"] for p in close]}
