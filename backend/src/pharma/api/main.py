"""Main FastAPI Application Entrypoint for Pharma Inventory Platform."""

import asyncio
import json
import re
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse, HTMLResponse, JSONResponse, FileResponse
from pharma.db.repository import StorageUnavailable, StateConflict, StateTooLarge
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from pharma.api import live_routes, room_routes, routes
from pharma.api.replay_stream import ReplayController
from pharma.config import Settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Setup paths
    cfg = Settings()
    scenarios_dir = cfg.data_dir / "scenarios"
    scenarios_dir.mkdir(parents=True, exist_ok=True)

    # Live inventory is persisted under data/state and carries across recordings.
    setup_path = cfg.data_dir / 'demo' / 'setup.json'
    setup = json.loads(setup_path.read_text()) if setup_path.exists() else {}
    controller = ReplayController(scenarios_dir=scenarios_dir, layout_id=setup.get('camera_layout_id', 'default'))
    routes.controller = controller
    controller.restore_player(fallback=setup.get('recording', 'demo_scenario_01'))

    clock = asyncio.create_task(controller.run_clock())
    try:
        yield
    finally:
        clock.cancel()
        import contextlib
        with contextlib.suppress(asyncio.CancelledError):
            await clock
        controller.store.repository.close()
        routes.controller = None


app = FastAPI(
    title="Pharma Inventory Platform API",
    description="Pharmacy inventory tracking system combining YOLO pose estimation, IMU event fusion, and MongoDB state management.",
    version="0.1.0",
    lifespan=lifespan,
)

# Reading rooms, importing a scan and trying a camera solve never touch inventory, and a
# large scan import mustn't stall playback. Saving 3D regions or a registration does:
# it regenerates camera views' regions, so those writes take the lock like any other.
# Live band events take the lock themselves, only around state reads and the mutation,
# so seconds of clip encoding and pose don't stall playback (see live_routes).
LOCK_FREE_PATHS = re.compile(r"^/api/(video/feed$|recordings/[^/]+/(floor-track|video)$|live/events$)")
LOCK_FREE_ROOM_READS = re.compile(r"^/api/rooms(/|$)")
LOCK_FREE_ROOM_WRITES = re.compile(r"^/api/rooms(/[^/]+/cameras/solve)?$")


def lock_free(method: str, path: str) -> bool:
    if LOCK_FREE_PATHS.match(path):
        return True
    if method in ("GET", "HEAD"):
        return bool(LOCK_FREE_ROOM_READS.match(path))
    return method == "POST" and bool(LOCK_FREE_ROOM_WRITES.match(path))


@app.middleware("http")
async def inventory_consistency(request, call_next):
    """Serialize local read/modify/write operations with replay; Mongo revisions guard other workers."""
    ctrl = routes.controller
    if ctrl is None or not request.url.path.startswith("/api/") or lock_free(request.method, request.url.path):
        return await call_next(request)
    async with ctrl.inventory_lock:
        try:
            ctrl.store.refresh()
            ctrl.storage_error = None
            active = active_shipment(ctrl.engine)
            path = request.url.path
            if active and request.method not in ("GET", "HEAD"):
                # Freeze shelf identity/calibration and the recording while receipts
                # are being reconciled. Reject before filesystem writes can occur.
                setup_write = path.startswith(("/api/catalog", "/api/layouts", "/api/rooms/"))
                reset = path == "/api/inventory/reset"
                recording = re.fullmatch(r"/api/recordings/([^/]+)(?:/(load|apply|process|view))?", path)
                unsafe_recording = recording and (recording[2] != "load" or recording[1] != active["stocking"]["recording"])
                if setup_write or reset or unsafe_recording:
                    return JSONResponse(status_code=409, content={"detail": "Finish shipment stocking before changing calibration, resetting inventory or changing its recording."})
            response = await call_next(request)
            if response.status_code >= 400:
                ctrl.store.rollback()
            return response
        except (StorageUnavailable, StateConflict, StateTooLarge) as exc:
            ctrl.store.rollback()
            ctrl.is_playing = False
            ctrl.storage_error = str(exc)
            status = 409 if isinstance(exc, StateConflict) else 507 if isinstance(exc, StateTooLarge) else 503
            await ctrl.broadcast_json({"type": "storage_error", "message": str(exc)})
            return JSONResponse(status_code=status, content={"detail": str(exc)})
        except Exception:
            ctrl.store.rollback()
            raise


# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount REST API routes
from pharma.api import shipment_routes
from pharma.services.stocking import active_shipment

app.include_router(shipment_routes.router)
app.include_router(routes.router)
app.include_router(room_routes.router)
app.include_router(live_routes.router)
from pharma.api import demo_routes
app.include_router(demo_routes.router)


@app.get("/api/video/feed")
def video_feed():
    """Server-driven MJPEG video stream with YOLO pose & region overlays."""
    if routes.controller is None:
        return StreamingResponse(iter([]), status_code=500)

    return StreamingResponse(
        routes.controller.mjpeg_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket):
    """WebSocket endpoint for broadcasting live scenario replay events and state snapshots."""
    if routes.controller is None:
        await websocket.close(code=1011)
        return

    await routes.controller.register_websocket(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        routes.controller.unregister_websocket(websocket)


dashboard_dist = Path(__file__).resolve().parents[3] / "dashboard" / "dist"
if dashboard_dist.exists():
    app.mount("/assets", StaticFiles(directory=str(dashboard_dist / "assets")), name="dashboard-assets")
    app.mount("/dashboard", StaticFiles(directory=str(dashboard_dist), html=True), name="dashboard")


@app.get("/recordings", response_class=HTMLResponse)
@app.get("/inventory", response_class=HTMLResponse)
@app.get("/shipments", response_class=HTMLResponse)
@app.get("/setup", response_class=HTMLResponse)
@app.get("/live", response_class=HTMLResponse)
@app.get("/room", response_class=HTMLResponse)
@app.get("/demo", response_class=HTMLResponse)
@app.get("/", response_class=HTMLResponse)
def root_dashboard():
    """Root landing page linking to API docs and Dashboard."""
    index_file = dashboard_dist / "index.html"
    if index_file.exists():
        return HTMLResponse(content=index_file.read_text(), status_code=200)

    return HTMLResponse(
        content="""
        <!DOCTYPE html>
        <html>
          <head>
            <title>Pharma AI Platform Server</title>
            <style>
              body { background: #0b0f19; color: #f3f4f6; font-family: sans-serif; padding: 2rem; }
              a { color: #00f0ff; text-decoration: none; }
              .card { background: rgba(18,24,38,0.8); border: 1px solid rgba(255,255,255,0.1); padding: 1.5rem; border-radius: 12px; max-width: 600px; margin-top: 1rem; }
            </style>
          </head>
          <body>
            <h1>Pharma AI Inventory Platform API</h1>
            <p>API Server and Server-Driven Replay Engine is running.</p>
            <div class="card">
              <h3>Endpoints & Dashboard:</h3>
              <ul>
                <li><a href="/docs">Swagger API Documentation (/docs)</a></li>
                <li><a href="/api/inventory">Current Inventory API (/api/inventory)</a></li>
                <li><a href="/api/recordings">Recordings (/api/recordings)</a></li>
                <li><a href="/api/video/feed">Live MJPEG Video Feed (/api/video/feed)</a></li>
              </ul>
            </div>
          </body>
        </html>
        """,
        status_code=200,
    )


@app.get("/favicon.svg")
def dashboard_favicon():
    path = dashboard_dist / "favicon.svg"
    return FileResponse(path) if path.exists() else HTMLResponse("", status_code=404)
