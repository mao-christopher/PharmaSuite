"""Server-driven video streamer and WebSocket event broadcaster for scenario replay."""

import asyncio
import cv2
import json
import numpy as np
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Any
from fastapi import WebSocket

from pharma.services.inventory_engine import InventoryEngine
from pharma.services.fixture_loader import load_json, load_jsonl
from pharma.db.models import Region, SensorEvent, InventoryState, Receipt, PrescriptionTransaction


class ReplayController:
    def __init__(self, scenarios_dir: Path):
        self.scenarios_dir = scenarios_dir
        self.current_scenario_name: Optional[str] = None
        self.scenario_path: Optional[Path] = None
        self.engine: Optional[InventoryEngine] = None
        self.events: List[Dict[str, Any]] = []
        self.regions: List[Region] = []
        self.active_websockets: Set[WebSocket] = set()
        self.is_playing: bool = False
        self.current_media_time_ms: int = 0
        self.duration_ms: int = 10000
        self.fps: int = 30
        self._replay_task: Optional[asyncio.Task] = None

    def load_scenario(self, scenario_name: str) -> Dict[str, Any]:
        path = self.scenarios_dir / scenario_name
        if not path.exists() or not path.is_dir():
            raise FileNotFoundError(f"Scenario scenario '{scenario_name}' not found at {path}")

        self.current_scenario_name = scenario_name
        self.scenario_path = path

        # Load region configs
        regions_raw = load_json(path / "regions.json")
        self.regions = [Region(**r) for r in regions_raw]

        # Load inventory state
        inv_raw = load_json(path / "initial_inventory.json")
        inventory = {i["medication_key"]: InventoryState(**i) for i in inv_raw}

        # Load receipts
        receipts_raw = load_json(path / "receipts.json")
        receipts = [Receipt(**r) for r in receipts_raw]

        # Load transactions
        tx_raw = load_json(path / "transactions.json")
        transactions = {t["transaction_id"]: PrescriptionTransaction(**t) for t in tx_raw}

        # Load IMU events
        self.events = load_jsonl(path / "imu_events.jsonl")

        # Initialize engine
        self.engine = InventoryEngine(
            inventory=inventory,
            regions=self.regions,
            receipts=receipts,
            transactions=transactions,
        )

        self.current_media_time_ms = 0
        self.is_playing = False

        return {
            "scenario_name": scenario_name,
            "regions_count": len(self.regions),
            "events_count": len(self.events),
            "inventory_keys": list(inventory.keys()),
        }

    async def register_websocket(self, websocket: WebSocket):
        await websocket.accept()
        self.active_websockets.add(websocket)
        # Send initial state snapshot
        await self.broadcast_state_snapshot()

    def unregister_websocket(self, websocket: WebSocket):
        self.active_websockets.discard(websocket)

    async def broadcast_json(self, data: Dict[str, Any]):
        to_remove = set()
        for ws in self.active_websockets:
            try:
                await ws.send_json(data)
            except Exception:
                to_remove.add(ws)
        for ws in to_remove:
            self.active_websockets.discard(ws)

    async def broadcast_state_snapshot(self):
        if not self.engine:
            return
        snapshot = {
            "type": "state_snapshot",
            "scenario": self.current_scenario_name,
            "media_time_ms": self.current_media_time_ms,
            "is_playing": self.is_playing,
            "inventory": {k: v.model_dump() for k, v in self.engine.inventory.items()},
            "sessions": {k: v.model_dump() for k, v in self.engine.sessions.items()},
            "disposals": {k: v.model_dump() for k, v in self.engine.disposals.items()},
            "alerts": {k: v.model_dump() for k, v in self.engine.alerts.items()},
        }
        await self.broadcast_json(snapshot)

    def generate_annotated_frame(self, media_time_ms: int, width: int = 1280, height: int = 720) -> np.ndarray:
        """Generate/annotate video frame with camera regions and skeleton keypoints."""
        # Create dark background frame
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        frame[:] = (30, 30, 35)  # Dark slate background

        # Draw camera regions
        for r in self.regions:
            x_min = int(r.bbox[0] * width)
            y_min = int(r.bbox[1] * height)
            x_max = int(r.bbox[2] * width)
            y_max = int(r.bbox[3] * height)

            # Color scheme by region type
            if r.region_type == "designated_shelf":
                color = (0, 255, 120)  # Emerald green
                label = f"SHELF: {r.medication_key}"
            elif r.region_type == "dispensing_counter":
                color = (255, 180, 0)  # Gold amber
                label = "DISPENSING COUNTER"
            elif r.region_type == "disposal":
                color = (0, 80, 255)  # Crimson red
                label = "DISPOSAL / TRASH"
            else:
                color = (200, 200, 200)
                label = r.region_id

            # Draw rectangle
            cv2.rectangle(frame, (x_min, y_min), (x_max, y_max), color, 2)
            cv2.putText(frame, label, (x_min + 10, y_min + 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

        # Draw simulated Technician wrist keypoint moving over time
        # Interpolate wrist position over time
        progress = (media_time_ms % 10000) / 10000.0
        wrist_x = int((0.2 + 0.6 * progress) * width)
        wrist_y = int((0.25 + 0.1 * np.sin(progress * 2 * np.pi)) * height)

        # Draw hand/wrist marker
        cv2.circle(frame, (wrist_x, wrist_y), 10, (255, 0, 255), -1)
        cv2.circle(frame, (wrist_x, wrist_y), 16, (255, 255, 255), 2)
        cv2.putText(frame, "Technician Wrist Proxy", (wrist_x + 15, wrist_y + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

        # HUD Overlay: media timestamp & scenario title
        cv2.putText(
            frame,
            f"SCENARIO: {self.current_scenario_name or 'None'}  |  MEDIA TIME: {media_time_ms} ms",
            (20, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 255, 255),
            2,
        )

        return frame

    def mjpeg_generator(self):
        """Generator yielding MJPEG multipart HTTP frame stream."""
        frame_interval = 1.0 / self.fps
        while True:
            if self.is_playing:
                self.current_media_time_ms = (self.current_media_time_ms + int(frame_interval * 1000)) % self.duration_ms
                # Process active events matching media time
                if self.engine:
                    for evt in self.events:
                        if abs(evt["media_time_ms"] - self.current_media_time_ms) < 50:
                            # Evaluate event
                            width, height = 1280, 720
                            progress = (self.current_media_time_ms % 10000) / 10000.0
                            wrist_x_norm = 0.2 + 0.6 * progress
                            wrist_y_norm = 0.25 + 0.1 * np.sin(progress * 2 * np.pi)

                            if evt["event_type"] == "pickup":
                                self.engine.handle_pickup(evt["session_id"], (wrist_x_norm, wrist_y_norm), 0.9, time.time())
                            elif evt["event_type"] == "release":
                                self.engine.handle_release(evt["session_id"], (wrist_x_norm, wrist_y_norm), 0.9, time.time())

            frame = self.generate_annotated_frame(self.current_media_time_ms)
            ok, jpeg = cv2.imencode(".jpg", frame)
            if ok:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + jpeg.tobytes() + b"\r\n"
                )
            time.sleep(frame_interval)
