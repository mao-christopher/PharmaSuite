"""Main FastAPI Application Entrypoint for Pharma Inventory Platform."""

from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

from pharma.api import routes
from pharma.api.replay_stream import ReplayController
from pharma.config import Settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Setup paths
    cfg = Settings()
    scenarios_dir = cfg.data_dir / "scenarios"
    scenarios_dir.mkdir(parents=True, exist_ok=True)

    # Initialize ReplayController
    controller = ReplayController(scenarios_dir=scenarios_dir)
    routes.controller = controller

    # Auto-load demo scenario if present
    demo_path = scenarios_dir / "demo_scenario_01"
    if demo_path.exists():
        try:
            controller.load_scenario("demo_scenario_01")
        except Exception as e:
            print(f"Warning loading demo scenario on startup: {e}")

    yield

    # Cleanup shutdown
    routes.controller = None


app = FastAPI(
    title="Pharma Inventory Platform API",
    description="Pharmacy inventory tracking system combining YOLO pose estimation, IMU event fusion, and MongoDB state management.",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount REST API routes
app.include_router(routes.router)


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
            # Receive client ping/control messages if any
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        routes.controller.unregister_websocket(websocket)
