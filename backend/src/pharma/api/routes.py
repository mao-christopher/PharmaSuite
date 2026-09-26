"""REST API route handlers for Pharmacy Inventory Dashboard & Replay System."""

from pathlib import Path
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from pharma.api.replay_stream import ReplayController
from pharma.config import Settings

router = APIRouter(prefix="/api")

# Global controller instance (initialized in main.py)
controller: Optional[ReplayController] = None


def get_controller() -> ReplayController:
    if controller is None:
        raise HTTPException(status_code=500, detail="ReplayController not initialized")
    return controller


class ScenarioLoadRequest(BaseModel):
    scenario_name: str


class ReplayControlRequest(BaseModel):
    action: str  # play | pause | restart | seek
    media_time_ms: Optional[int] = None


class DisposalSubmitRequest(BaseModel):
    selected_receipt_id: str
    explicit_quantity: Optional[int] = None


class PrescriptionStatusRequest(BaseModel):
    status: str  # created | confirmed_fill | paid | cancelled


class ConfirmationRequest(BaseModel):
    resolved_medication_key: Optional[str] = None
    resolved_region_id: Optional[str] = None


@router.get("/scenarios")
def list_scenarios(ctrl: ReplayController = Depends(get_controller)):
    """List available replay scenario bundles."""
    scenarios_dir = ctrl.scenarios_dir
    if not scenarios_dir.exists():
        return {"scenarios": []}

    scenarios = []
    for item in scenarios_dir.iterdir():
        if item.is_dir() and not item.name.startswith("."):
            scenarios.append({
                "name": item.name,
                "has_video": (item / "video.mp4").exists(),
                "has_events": (item / "imu_events.jsonl").exists(),
                "has_regions": (item / "regions.json").exists(),
            })
    return {"scenarios": scenarios}


@router.post("/scenarios/{scenario_name}/load")
def load_scenario(scenario_name: str, ctrl: ReplayController = Depends(get_controller)):
    """Reset state and load scenario into replay engine."""
    try:
        res = ctrl.load_scenario(scenario_name)
        return {"status": "success", "data": res}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/inventory")
def get_inventory_status(ctrl: ReplayController = Depends(get_controller)):
    """Fetch current inventory metrics, active alerts, disposals, and transactions."""
    if not ctrl.engine:
        raise HTTPException(status_code=400, detail="No scenario currently loaded")

    return {
        "scenario": ctrl.current_scenario_name,
        "media_time_ms": ctrl.current_media_time_ms,
        "is_playing": ctrl.is_playing,
        "inventory": {k: v.model_dump() for k, v in ctrl.engine.inventory.items()},
        "sessions": {k: v.model_dump() for k, v in ctrl.engine.sessions.items()},
        "disposals": {k: v.model_dump() for k, v in ctrl.engine.disposals.items()},
        "alerts": {k: v.model_dump() for k, v in ctrl.engine.alerts.items()},
        "receipts": [r.model_dump() for r in ctrl.engine.receipts.values()],
        "transactions": {k: v.model_dump() for k, v in ctrl.engine.transactions.items()},
    }


@router.post("/replay/control")
async def control_replay(req: ReplayControlRequest, ctrl: ReplayController = Depends(get_controller)):
    """Play, pause, restart, or seek the replay stream."""
    if req.action == "play":
        ctrl.is_playing = True
    elif req.action == "pause":
        ctrl.is_playing = False
    elif req.action == "restart":
        ctrl.current_media_time_ms = 0
        ctrl.is_playing = True
    elif req.action == "seek" and req.media_time_ms is not None:
        ctrl.current_media_time_ms = req.media_time_ms

    await ctrl.broadcast_state_snapshot()
    return {"status": "success", "is_playing": ctrl.is_playing, "media_time_ms": ctrl.current_media_time_ms}


@router.post("/inventory/disposals/{disposal_id}")
async def submit_disposal_form(disposal_id: str, req: DisposalSubmitRequest, ctrl: ReplayController = Depends(get_controller)):
    """Submit employee selection and discarded tablet quantity for a disposal record."""
    if not ctrl.engine:
        raise HTTPException(status_code=400, detail="No active engine")

    try:
        record = ctrl.engine.resolve_disposal(
            disposal_id=disposal_id,
            selected_receipt_id=req.selected_receipt_id,
            explicit_quantity=req.explicit_quantity,
        )
        await ctrl.broadcast_state_snapshot()
        return {"status": "success", "disposal": record.model_dump()}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/transactions/{transaction_id}/status")
async def update_transaction_status(transaction_id: str, req: PrescriptionStatusRequest, ctrl: ReplayController = Depends(get_controller)):
    """Update prescription transaction status and apply idempotent tablet deduction."""
    if not ctrl.engine:
        raise HTTPException(status_code=400, detail="No active engine")

    applied = ctrl.engine.process_prescription_deduction(transaction_id, req.status)
    await ctrl.broadcast_state_snapshot()
    return {
        "status": "success",
        "transaction_id": transaction_id,
        "new_status": req.status,
        "deduction_applied": applied,
    }


@router.post("/inventory/confirmations/{alert_id}")
async def resolve_confirmation_alert(alert_id: str, req: ConfirmationRequest, ctrl: ReplayController = Depends(get_controller)):
    """Resolve an employee uncertainty confirmation alert."""
    if not ctrl.engine:
        raise HTTPException(status_code=400, detail="No active engine")

    alert = ctrl.engine.alerts.get(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    alert.status = "resolved"
    await ctrl.broadcast_state_snapshot()
    return {"status": "success", "alert": alert.model_dump()}
