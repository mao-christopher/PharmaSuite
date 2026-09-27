#!/usr/bin/env python3
"""Import a Unity render's scene geometry as a dashboard room with its exact camera.

    python scripts/import_sim_room.py ../simulation/Exports/demo-001 --api http://127.0.0.1:8000

Creates the room (solid boxes as its mesh), the 3D shelf/counter/disposal regions, a
camera view with the first rendered frame as its photo, and a registration from exact
point pairs. Pass --layout-id to register an existing view (for example the one an
uploaded render already uses) instead of creating one.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import cv2
import httpx

from pharma.db.models import Catalog
from pharma.services.sim_scene import import_scene

SCENE_FILE = "scene_geometry.json"


def first_frame_png(export: Path) -> bytes:
    frame = export / "frames" / "000000.png"
    if frame.exists():
        return frame.read_bytes()
    cap = cv2.VideoCapture(str(export / "camera.mp4"))
    ok, image = cap.read()
    cap.release()
    if not ok:
        raise SystemExit(f"No frames/000000.png or readable camera.mp4 in {export}")
    return cv2.imencode(".png", image)[1].tobytes()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("export", type=Path, help="Render output folder containing scene_geometry.json")
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--name", default="Simulated pharmacy", help="Room name")
    parser.add_argument("--layout-id", default=None, help="Register this existing camera view")
    parser.add_argument("--view-name", default=None, help="Name for a new camera view")
    args = parser.parse_args()

    scene_path = args.export / SCENE_FILE
    if not scene_path.exists():
        raise SystemExit(f"{scene_path} is missing. Re-render with the current exporter.")
    scene = json.loads(scene_path.read_text(encoding="utf-8"))
    with httpx.Client(base_url=args.api, timeout=120) as api:
        catalog = Catalog.model_validate(api.get("/api/catalog").raise_for_status().json())
        result = import_scene(api, scene, catalog, first_frame_png(args.export), name=args.name,
                              layout_id=args.layout_id, view_name=args.view_name)
    print(json.dumps(result, indent=2))
    for note in result["skipped"]:
        print(f"Skipped region {note}. Add the medication on the Inventory page and run again.", file=sys.stderr)


if __name__ == "__main__":
    main()
