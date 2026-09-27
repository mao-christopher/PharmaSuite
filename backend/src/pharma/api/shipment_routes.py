"""Shipment import and employee stocking controls. All writes use the inventory lock."""

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from pharma.api.routes import get_controller
from pharma.services import shipment_import, stocking
from pharma.services.store import now_iso

router = APIRouter(prefix="/api/shipments")


class Selection(BaseModel):
    line_id: str
    event_id: str


class Shortage(BaseModel):
    line_id: str
    quantity: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500)
    operation_id: str = Field(min_length=1, max_length=100)


def checked(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def paused(ctrl):
    if ctrl.is_playing:
        raise HTTPException(
            status_code=409, detail="Pause playback before changing the stocking session."
        )


@router.post("/import")
async def import_file(
    file: UploadFile = File(...), preview: bool = Query(False), ctrl=Depends(get_controller)
):
    content = await file.read(shipment_import.MAX_BYTES + 1)
    items = checked(shipment_import.parse_shipments, file.filename or "", content)
    if preview:
        return {"shipments": [s.model_dump(mode="json") for s in items]}
    results = checked(
        shipment_import.import_shipments, ctrl.engine, items, file.filename, content, now_iso()
    )
    await ctrl.commit("shipment_import", "Imported shipment documents.", results=results)
    return {"results": results}


@router.get("")
def list_shipments(ctrl=Depends(get_controller)):
    return {
        "shipments": [
            {**s, "report": stocking.report(ctrl.engine, s)} for s in ctrl.engine.shipments.values()
        ]
    }


@router.post("/{key}/start")
async def start_shipment(key: str, ctrl=Depends(get_controller)):
    paused(ctrl)
    if not ctrl.current:
        raise HTTPException(status_code=400, detail="Load a processed stocking recording first.")
    # Only the calibrated views belonging to this recording establish destinations.
    view_ids = (
        [c.layout_id for c in ctrl.current.camera_group.cameras.values()]
        if ctrl.current.camera_group
        else [ctrl.current.layout_id]
    )
    regions = [r for view_id in view_ids for r in ctrl.view(view_id).regions]
    checked(stocking.start, ctrl.engine, key, ctrl.current.name, regions, now_iso())
    await ctrl.commit(
        "stocking_started",
        "Started shipment stocking.",
        shipment_id=key,
        recording=ctrl.current.name,
    )
    return {"status": "stocking"}


@router.post("/{key}/select")
async def select_line(key: str, req: Selection, ctrl=Depends(get_controller)):
    paused(ctrl)
    s = checked(stocking.get_shipment, ctrl.engine, key)
    if not s.get("stocking") or not ctrl.current or ctrl.current.name != s["stocking"]["recording"]:
        raise HTTPException(
            status_code=400, detail="Load this shipment’s stocking recording first."
        )
    pending = [
        e
        for e in ctrl.current.events
        if e["event_type"] == "pickup" and e["event_id"] not in ctrl.processed_event_ids
    ]
    if not pending or pending[0]["event_id"] != req.event_id:
        raise HTTPException(status_code=400, detail="Select the next unapplied pickup event.")
    checked(stocking.arm, ctrl.engine, key, req.line_id, req.event_id)
    await ctrl.commit(
        "stocking_selected",
        "Identified incoming bottle and lot.",
        shipment_id=key,
        **req.model_dump(),
    )
    return {"status": "armed"}


@router.post("/{key}/shortage")
async def document_shortage(key: str, req: Shortage, ctrl=Depends(get_controller)):
    paused(ctrl)
    checked(
        stocking.shortage,
        ctrl.engine,
        key,
        req.line_id,
        req.quantity,
        req.reason.strip(),
        req.operation_id,
        now_iso(),
    )
    await ctrl.commit(
        "shipment_shortage",
        "Documented unreceived shipment bottles.",
        shipment_id=key,
        **req.model_dump(),
    )
    return {"status": "recorded"}


@router.post("/{key}/finish")
async def finish_shipment(key: str, ctrl=Depends(get_controller)):
    paused(ctrl)
    result = checked(stocking.finish, ctrl.engine, key, now_iso())
    await ctrl.commit(
        "stocking_finished",
        "Reconciled shipment placements.",
        shipment_id=key,
        result=result["status"],
    )
    return {"status": result["status"]}
