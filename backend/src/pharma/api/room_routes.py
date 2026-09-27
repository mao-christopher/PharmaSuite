"""REST routes for scanned rooms, 3D regions, camera registration, and the per-recording floor track.

None of these touch live inventory, so they run outside the inventory lock (see main.py):
importing a large scan must not stall playback.
"""

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from pharma.api.replay_stream import ReplayController, scenario_meta
from pharma.api.routes import get_controller
from pharma.db.models import CameraRegistration, Correspondence, Region3D, Room
from pharma.services import floor_track, region_projection, render_jobs, room_rebuild, timeline
from pharma.services.camera_geometry import Camera, box_corners, solve_registration
from pharma.services.recordings import POSES_FILE, find_video
from pharma.services.room import (
    MESH_FILE, ROOM_FILES, ScanError, delete_room, import_scan, list_rooms, load_obstacles, load_room,
    registration_for_view, room_dir, save_room, validate_regions,
)

router = APIRouter(prefix="/api")

MAX_SCAN_BYTES = 500 * 1024 * 1024
UPLOADS_DIR = ".uploads"
GRID_STEP_M = 0.5


class RegionsRequest(BaseModel):
    regions: List[Region3D]
    room_version: Optional[int] = Field(default=None, description="Reject the save if the room changed since")


class SolveRequest(BaseModel):
    layout_id: str
    correspondences: List[Correspondence]


class RegisterRequest(BaseModel):
    correspondences: List[Correspondence]


def _room(ctrl: ReplayController, room_id: str) -> Room:
    try:
        return load_room(ctrl.rooms_dir, room_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


def _files(room: Room) -> Dict[str, str]:
    base = f"/api/rooms/{room.room_id}/files"
    return {"mesh": f"{base}/{room.mesh.file}?v={room.mesh.sha256[:12]}",
            "plan": f"{base}/{room.plan.image}?v={room.mesh.sha256[:12]}"}


def _room_payload(room: Room) -> Dict[str, Any]:
    return {**room.model_dump(mode="json"), "files": _files(room)}


def _summary(room: Room) -> Dict[str, Any]:
    return {
        "room_id": room.room_id,
        "name": room.name or room.room_id,
        "room_version": room.room_version,
        "created_at": room.created_at,
        "updated_at": room.updated_at,
        "triangles": room.mesh.triangles,
        "bytes": room.mesh.bytes,
        "source_name": room.mesh.source_name,
        "regions": len(room.regions),
        "cameras": [{"layout_id": c.layout_id, "rms_px": c.rms_px, "revision": c.revision} for c in room.cameras],
        "bounds_min": room.bounds_min,
        "bounds_max": room.bounds_max,
    }


# ---------------------------------------------------------------- rooms


@router.get("/rooms")
def get_rooms(ctrl: ReplayController = Depends(get_controller)):
    """Scanned rooms, newest first."""
    rooms = sorted(list_rooms(ctrl.rooms_dir), key=lambda r: r.created_at, reverse=True)
    return {"rooms": [_summary(r) for r in rooms]}


@router.post("/rooms")
def upload_room(scan: UploadFile = File(...), name: Optional[str] = Form(None),
                ctrl: ReplayController = Depends(get_controller)):
    """Import an iPhone room scan exported as GLB. The file is kept as is; import levels it."""
    staging = ctrl.rooms_dir / UPLOADS_DIR
    staging.mkdir(parents=True, exist_ok=True)
    tmp = staging / f"scan-{uuid.uuid4().hex[:12]}.glb"
    try:
        written = 0
        with tmp.open("wb") as out:
            while block := scan.file.read(1 << 20):
                written += len(block)
                if written > MAX_SCAN_BYTES:
                    raise HTTPException(status_code=413, detail="The scan is larger than 500 MB. Export it at a lower quality.")
                out.write(block)
        try:
            room = import_scan(ctrl.rooms_dir, tmp, name, source_name=scan.filename)
        except ScanError as e:
            raise HTTPException(status_code=400, detail=str(e))
    finally:
        tmp.unlink(missing_ok=True)
    return _room_payload(room)


@router.get("/rooms/{room_id}")
def get_room(room_id: str, ctrl: ReplayController = Depends(get_controller)):
    return _room_payload(_room(ctrl, room_id))


@router.delete("/rooms/{room_id}")
async def remove_room(room_id: str, ctrl: ReplayController = Depends(get_controller)):
    """Delete a scan with its regions and camera registrations.

    Views whose regions came from this room are left with none, so their signals ask for
    confirmation instead of matching regions that no longer exist.
    """
    room = _room(ctrl, room_id)
    delete_room(ctrl.rooms_dir, room_id)
    notes = await _sync_views(ctrl, [c.layout_id for c in room.cameras])
    return {"deleted": room_id, "view_updates": notes}


@router.get("/rooms/{room_id}/rebuilt")
def room_rebuilt(room_id: str, ctrl: ReplayController = Depends(get_controller)):
    """The room rebuilt from colored boxes (walls, shelf units, counter, furniture blocks).

    Built from the scan, its plan and the 3D tags the first time it is asked for at each
    room version, then cached. The Room page previews it; the simulation renders it.
    """
    room = _room(ctrl, room_id)
    try:
        return room_rebuild.load_or_rebuild(ctrl.rooms_dir, room, ctrl.catalog)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=f"The scan files are missing: {e}")


@router.get("/rooms/{room_id}/files/{filename}")
def room_file(room_id: str, filename: str, ctrl: ReplayController = Depends(get_controller)):
    if filename not in ROOM_FILES:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        path = room_dir(ctrl.rooms_dir, room_id) / filename
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Not found")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Not found")
    media = "model/gltf-binary" if filename == MESH_FILE else "image/png"
    return FileResponse(path, media_type=media, headers={"Cache-Control": "max-age=86400"})


async def _sync_views(ctrl: ReplayController, layout_ids, adopt: Optional[str] = None) -> List[str]:
    """Regenerate the 2D regions of these views (and adopt them for `adopt`), then tell the dashboard."""
    notes = ctrl.sync_room_views([i for i in layout_ids if i != adopt])
    if adopt:
        note = ctrl.sync_view_regions(adopt, adopt=True)
        if note:
            notes.append(f"{ctrl._view_name(adopt) or adopt}: {note}")
    await ctrl.broadcast_state_snapshot()
    return notes


@router.put("/rooms/{room_id}/regions")
async def put_regions(room_id: str, req: RegionsRequest, ctrl: ReplayController = Depends(get_controller)):
    """Replace the room's 3D shelves, counters and disposal regions.

    Every camera registered in the room gets its 2D regions regenerated from these.
    """
    room = _room(ctrl, room_id)
    if req.room_version is not None and req.room_version != room.room_version:
        raise HTTPException(status_code=409, detail="The room changed since you opened it. Reload and try again.")
    try:
        validate_regions(req.regions, ctrl.catalog)
        saved = save_room(ctrl.rooms_dir, Room.model_validate({**room.model_dump(mode="json"),
                                                               "regions": [r.model_dump() for r in req.regions]}))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    notes = await _sync_views(ctrl, [c.layout_id for c in saved.cameras])
    return {**_room_payload(saved), "view_updates": notes}


# ---------------------------------------------------------------- camera registration


def _view(ctrl: ReplayController, layout_id: str):
    try:
        return ctrl.view(layout_id)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=404, detail=str(e))


def _polylines(cam: Camera, points: np.ndarray) -> List[List[List[float]]]:
    """Normalized image polylines of a sampled 3D line, split where it leaves the view."""
    px, z = cam.project(points)
    lines, current = [], []
    for (u, v), depth in zip(px, z):
        nu, nv = u / cam.width, v / cam.height
        if depth > 0.1 and -0.5 <= nu <= 1.5 and -0.5 <= nv <= 1.5:
            current.append([round(float(nu), 5), round(float(nv), 5)])
        elif current:
            lines.append(current)
            current = []
    if len(current) > 1:
        lines.append(current)
    return [line for line in lines if len(line) > 1]


def _overlay(cam: Camera, room: Room, correspondences: List[Correspondence]) -> Dict[str, Any]:
    """What the solved camera would see: the clicked 3D points, region boxes, and a floor grid."""
    pts, _ = cam.project(np.array([c.room for c in correspondences]))
    edges = []
    for r in room.regions:
        corners = box_corners(r.box.center, r.box.size, r.box.yaw_deg)
        # corner index bits: x (4), y (2), z (1)
        pairs = [(a, b) for a in range(8) for b in range(a + 1, 8) if bin(a ^ b).count("1") == 1]
        segs = []
        for a, b in pairs:
            segs += _polylines(cam, np.linspace(corners[a], corners[b], 12))
        edges.append(segs)
    lo, hi = np.array(room.bounds_min), np.array(room.bounds_max)
    grid = []
    for x in np.arange(np.ceil(lo[0] / GRID_STEP_M) * GRID_STEP_M, hi[0], GRID_STEP_M):
        grid += _polylines(cam, np.linspace([x, 0, lo[2]], [x, 0, hi[2]], 60))
    for z in np.arange(np.ceil(lo[2] / GRID_STEP_M) * GRID_STEP_M, hi[2], GRID_STEP_M):
        grid += _polylines(cam, np.linspace([lo[0], 0, z], [hi[0], 0, z], 60))
    generated, report = region_projection.regions_for_camera(room, cam)
    return {
        "points": [[float(u / cam.width), float(v / cam.height)] for u, v in pts],
        "regions": [{"region_id": r.region_id, "region_type": r.region_type, "edges": e}
                    for r, e in zip(room.regions, edges)],
        "grid": grid,
        # The 2D regions this camera would get: what the inventory matches hands against.
        "generated_regions": [r.model_dump(mode="json") for r in generated],
        "region_report": report,
    }


def _solve(ctrl: ReplayController, room: Room, layout_id: str, correspondences: List[Correspondence]):
    view = _view(ctrl, layout_id)
    try:
        solution = solve_registration([c.image for c in correspondences], [c.room for c in correspondences],
                                      view.frame_width, view.frame_height)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return view, solution


@router.post("/rooms/{room_id}/cameras/solve")
def solve_camera(room_id: str, req: SolveRequest, ctrl: ReplayController = Depends(get_controller)):
    """Solve a camera view's lens and position from point pairs without saving it."""
    room = _room(ctrl, room_id)
    view, solution = _solve(ctrl, room, req.layout_id, req.correspondences)
    cam = Camera(solution["intrinsics"]["fx"], solution["intrinsics"]["fy"], solution["intrinsics"]["cx"],
                 solution["intrinsics"]["cy"], np.array(solution["rotation"]), np.array(solution["translation"]),
                 view.frame_width, view.frame_height)
    return {**solution, "overlay": _overlay(cam, room, req.correspondences)}


@router.put("/rooms/{room_id}/cameras/{layout_id}")
async def register_camera(room_id: str, layout_id: str, req: RegisterRequest,
                          ctrl: ReplayController = Depends(get_controller)):
    """Save a camera view's registration (solved again here from the clicked pairs).

    A view sits in one room; registering it here removes it from any other room. The
    view's 2D regions are then generated from this room's 3D boxes (the solve overlay
    previews them), replacing any hand-drawn ones.
    """
    room = _room(ctrl, room_id)
    view, solution = _solve(ctrl, room, layout_id, req.correspondences)
    previous = next((c for c in room.cameras if c.layout_id == layout_id), None)
    reg = CameraRegistration(
        layout_id=layout_id,
        revision=(previous.revision + 1) if previous else 1,
        frame_size=(view.frame_width, view.frame_height),
        view_calibration_version=view.calibration_version,
        background_image=view.background_image,
        intrinsics=solution["intrinsics"],
        rotation=solution["rotation"],
        translation=solution["translation"],
        position=solution["position"],
        rms_px=solution["rms_px"],
        max_px=solution["max_px"],
        correspondences=req.correspondences,
        solved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    for other in list_rooms(ctrl.rooms_dir):
        if other.room_id != room_id and any(c.layout_id == layout_id for c in other.cameras):
            save_room(ctrl.rooms_dir, other.model_copy(update={
                "cameras": [c for c in other.cameras if c.layout_id != layout_id]}))
    cameras = [c for c in room.cameras if c.layout_id != layout_id] + [reg]
    saved = save_room(ctrl.rooms_dir, room.model_copy(update={"cameras": cameras}))
    notes = await _sync_views(ctrl, [], adopt=layout_id)
    return {"room": _room_payload(saved), "registration": reg.model_dump(mode="json"), "solution": solution,
            "view_updates": notes, "layout": ctrl.view(layout_id).model_dump(mode="json")}


@router.post("/rooms/{room_id}/cameras/{layout_id}/adopt-regions")
async def adopt_regions(room_id: str, layout_id: str, ctrl: ReplayController = Depends(get_controller)):
    """Switch a registered view from hand-drawn regions to ones generated from the 3D boxes."""
    room = _room(ctrl, room_id)
    if not any(c.layout_id == layout_id for c in room.cameras):
        raise HTTPException(status_code=404, detail=f"Camera view '{layout_id}' isn't registered in this room")
    notes = await _sync_views(ctrl, [], adopt=layout_id)
    return {"view_updates": notes, "layout": ctrl.view(layout_id).model_dump(mode="json")}


@router.delete("/rooms/{room_id}/cameras/{layout_id}")
async def unregister_camera(room_id: str, layout_id: str, ctrl: ReplayController = Depends(get_controller)):
    room = _room(ctrl, room_id)
    if not any(c.layout_id == layout_id for c in room.cameras):
        raise HTTPException(status_code=404, detail=f"Camera view '{layout_id}' isn't registered in this room")
    saved = save_room(ctrl.rooms_dir, room.model_copy(update={
        "cameras": [c for c in room.cameras if c.layout_id != layout_id]}))
    notes = await _sync_views(ctrl, [layout_id])
    return {**_room_payload(saved), "view_updates": notes}


# ---------------------------------------------------------------- floor track


def _recording_track(ctrl: ReplayController, name: str):
    """(scenario path, layout ID, room, registration, track), or an unavailable response."""
    try:
        path: Path = ctrl.scenario_dir(name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    layout_id = scenario_meta(path).get("layout_id") or ctrl.default_layout_id

    def unavailable(reason: str) -> Dict[str, Any]:
        # `rooms` lets the dashboard stay quiet until anyone has imported a scan.
        return {"available": False, "reason": reason, "layout_id": layout_id,
                "rooms": len(list_rooms(ctrl.rooms_dir))}

    found = registration_for_view(ctrl.rooms_dir, layout_id)
    if not found:
        view_name = ctrl._view_name(layout_id) or layout_id
        return unavailable(f"Camera view {view_name} isn't registered in a scanned room yet.")
    room, reg = found
    poses = path / POSES_FILE
    if not poses.exists():
        return unavailable("Skeletons haven't been extracted for this recording yet.")
    try:
        track = floor_track.load_or_compute(path, poses, room, reg, load_obstacles(ctrl.rooms_dir, room))
    except ValueError as e:
        return unavailable(str(e))
    return path, layout_id, room, reg, track


@router.get("/recordings/{name}/floor-track")
def recording_floor_track(name: str, ctrl: ReplayController = Depends(get_controller)):
    """Where the technician stood and faced in each frame, on the scanned room's floor.

    Uses the recording's main camera. Not available (with a reason) until that camera's
    view is registered in a scanned room and its skeletons are extracted.
    """
    found = _recording_track(ctrl, name)
    if isinstance(found, dict):
        return found
    path, layout_id, room, reg, track = found
    cam = Camera.from_registration(reg)
    forward = cam.R[2]
    return {
        "available": True,
        "layout_id": layout_id,
        "track": track,
        "room": {
            "room_id": room.room_id,
            "name": room.name or room.room_id,
            "room_version": room.room_version,
            "plan": {**room.plan.model_dump(mode="json"), "url": _files(room)["plan"]},
            "bounds_min": room.bounds_min,
            "bounds_max": room.bounds_max,
            "regions": [r.model_dump(mode="json") for r in room.regions],
        },
        "camera": {
            "layout_id": reg.layout_id,
            "position": reg.position,
            "heading_deg": float(np.degrees(np.arctan2(forward[0], forward[2]))),
            "hfov_deg": float(np.degrees(2 * np.arctan(cam.width / (2 * cam.fx)))),
            "rms_px": reg.rms_px,
            "stale_photo": reg.background_image != _view(ctrl, layout_id).background_image,
        },
    }



def export_timeline(ctrl: ReplayController, name: str) -> Dict[str, Any]:
    """Write the recording's timeline.json; raises ValueError when it can't be built."""
    path = ctrl.scenario_dir(name)
    collected = ctrl.timeline_inputs(path)
    if "reason" in collected:
        raise ValueError(collected["message"])
    return timeline.export(path, name, collected, ctrl.rooms_dir, ctrl.catalog)


@router.get("/recordings/{name}/timeline")
def recording_timeline(name: str, ctrl: ReplayController = Depends(get_controller)):
    """The re-enactment bundle for Unity (M5), also written to the recording's timeline.json.

    Pending locations stay pending: a put-down the inventory hasn't accepted has no region.
    """
    try:
        data = export_timeline(ctrl, name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        return {"available": False, "reason": str(e)}
    return {"available": True, "timeline": data}


# ---------------------------------------------------------------- simulation renders (M9)


class PlayerSourceRequest(BaseModel):
    source: str = Field(..., description="real | sim | side")


@router.get("/renders")
def render_queue(ctrl: ReplayController = Depends(get_controller)):
    """Whether this server can render, and the renders queued or running."""
    reason = ctrl.renders.unavailable_reason()
    return {"available": reason is None, "reason": reason, "jobs": ctrl.renders.active()}


@router.get("/recordings/{name}/render")
def recording_render(name: str, ctrl: ReplayController = Depends(get_controller)):
    try:
        return ctrl.render_summary(ctrl.scenario_dir(name))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/recordings/{name}/render")
def start_render(name: str, ctrl: ReplayController = Depends(get_controller)):
    """Export the timeline and queue a Unity render. The same inputs reuse the finished one."""
    try:
        path = ctrl.scenario_dir(name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    reason = ctrl.renders.unavailable_reason()
    if reason:
        raise HTTPException(status_code=409, detail=reason)
    try:
        data = export_timeline(ctrl, name)
        ctrl.renders.enqueue(name, scenario_meta(path).get("label") or name, path, data, find_video(path))
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=409, detail=str(e))
    return ctrl.render_summary(path)


@router.delete("/recordings/{name}/render")
def cancel_render(name: str, ctrl: ReplayController = Depends(get_controller)):
    if not ctrl.renders.cancel(name):
        raise HTTPException(status_code=404, detail="No render of this recording is queued or running.")
    return ctrl.render_summary(ctrl.scenario_dir(name))


@router.get("/recordings/{name}/render/{filename}")
def render_file(name: str, filename: str, ctrl: ReplayController = Depends(get_controller)):
    if filename not in (render_jobs.SIM_VIDEO, render_jobs.SIDE_VIDEO, render_jobs.MANIFEST):
        raise HTTPException(status_code=404, detail="Not found")
    try:
        path = ctrl.scenario_dir(name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    last = render_jobs.latest(path)
    target = render_jobs.render_dir(path, last["inputs_key"]) / filename if last else None
    if target is None or not target.exists():
        raise HTTPException(status_code=404, detail="This recording has no finished render.")
    media = "application/json" if filename.endswith(".json") else "video/mp4"
    return FileResponse(target, media_type=media, filename=f"{name}-{filename}")


@router.post("/player/source")
async def player_source(req: PlayerSourceRequest, ctrl: ReplayController = Depends(get_controller)):
    """Show the real video, the simulation render, or both side by side (same clock)."""
    try:
        ctrl.set_player_source(req.source)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    await ctrl.broadcast_state_snapshot()
    return {"player_source": ctrl.effective_player_source()}
