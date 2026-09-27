#!/usr/bin/env python3
"""Compare a camera view's hand-drawn regions with the ones generated from its room's 3D boxes.

Replays the location decision for every pickup/put-down signal of the recordings that
use the view, once per region set, and reports agreements, changed decisions, and
abstentions separately. Optionally draws both sets on the view's photo. Read-only: it
never saves a view, a room, or inventory.

    python scripts/compare_regions.py --layout default --image /tmp/regions.jpg
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from pharma.config import Settings  # noqa: E402
from pharma.services.inventory_engine import MIN_KEYPOINT_CONF, PICKUP_REGION_TYPES, nearest_region  # noqa: E402
from pharma.services.layout import load_layout  # noqa: E402
from pharma.services.recordings import POSES_FILE, PoseTrack, locate_hands  # noqa: E402
from pharma.services.region_projection import generate_regions  # noqa: E402
from pharma.services.room import registration_for_view  # noqa: E402

RELEASE_TYPES = ("designated_shelf", "dispensing_counter", "disposal")
COLORS = {"designated_shelf": (224, 108, 31), "dispensing_counter": (10, 125, 196), "disposal": (56, 52, 206)}


def decide(hands, regions, event_type, frame_size):
    types = PICKUP_REGION_TYPES if event_type == "pickup" else RELEASE_TYPES
    region, evidence = nearest_region(hands, [r for r in regions if r.region_type in types], frame_size)
    return (region.region_id if region else None), evidence.get("reason")


def recordings_for(scenarios: Path, layout_id: str):
    for path in sorted(p for p in scenarios.iterdir() if (p / "scenario.json").exists()):
        meta = json.loads((path / "scenario.json").read_text())
        if (meta.get("layout_id") or "default") == layout_id and (path / POSES_FILE).exists():
            yield path, meta


def draw(image_path: Path, out: Path, drawn, generated):
    img = cv2.imread(str(image_path))
    h, w = img.shape[:2]
    for regions, dashed in ((drawn, False), (generated, True)):
        for r in regions:
            pts = (np.array(r.polygon) * [w, h]).astype(np.int32)
            color = COLORS.get(r.region_type, (200, 200, 200))
            if dashed:
                overlay = img.copy()
                cv2.fillPoly(overlay, [pts], color)
                img = cv2.addWeighted(overlay, 0.25, img, 0.75, 0)
                cv2.polylines(img, [pts], True, color, max(2, w // 500), cv2.LINE_AA)
            else:
                cv2.polylines(img, [pts], True, (255, 255, 255), max(3, w // 300), cv2.LINE_AA)
            x, y = pts.min(axis=0)
            cv2.putText(img, ("gen " if dashed else "drawn ") + r.region_id, (int(x) + 6, int(y) + (60 if dashed else 28)),
                        cv2.FONT_HERSHEY_SIMPLEX, w / 1800, (255, 255, 255), max(1, w // 900), cv2.LINE_AA)
    cv2.imwrite(str(out), img)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--layout", default="default", help="Camera view ID")
    parser.add_argument("--data-dir", type=Path, default=Settings().data_dir)
    parser.add_argument("--image", type=Path, help="Write both region sets over the view's photo here")
    args = parser.parse_args()

    layout = load_layout(args.data_dir / "layouts", args.layout)
    found = registration_for_view(args.data_dir / "rooms", args.layout)
    if not found:
        sys.exit(f"Camera view {args.layout} isn't registered in a scanned room.")
    room, reg = found
    generated, report = generate_regions(room, reg)
    drawn = layout.regions
    print(f"View {args.layout}: {len(drawn)} current regions "
          f"({'generated' if layout.regions_source else 'hand-drawn'}), {len(generated)} generated from "
          f"room {room.room_id} v{room.room_version}, registration rev {reg.revision} ({reg.rms_px:.1f} px RMS)")
    for entry in report:
        print(f"  {entry['region_id']:28s} visible={entry['visible']!s:5s} share={entry['image_share']:.3f}")
    only_drawn = sorted({r.region_id for r in drawn} - {r.region_id for r in generated})
    only_generated = sorted({r.region_id for r in generated} - {r.region_id for r in drawn})
    if only_drawn or only_generated:
        print(f"  IDs only in the current set: {only_drawn or '-'}; only generated: {only_generated or '-'}")

    # A counter or disposal region may carry a different ID in each set; when a type has
    # exactly one region in both, treat the two IDs as the same place.
    renamed = {}
    for kind in ("dispensing_counter", "disposal"):
        a = [r.region_id for r in drawn if r.region_type == kind]
        b = [r.region_id for r in generated if r.region_type == kind]
        if len(a) == 1 and len(b) == 1 and a != b:
            renamed[a[0]] = b[0]
    if renamed:
        print(f"  Treated as the same place: {renamed}")

    totals = {"signals": 0, "same": 0, "renamed": 0, "changed": 0, "new_abstention": 0, "resolved_abstention": 0}
    for path, meta in recordings_for(args.data_dir / "scenarios", args.layout):
        track = PoseTrack.load(path / POSES_FILE)
        frame_size = (track.width, track.height)
        events = [json.loads(line) for line in (path / "imu_events.jsonl").read_text().splitlines() if line.strip()]
        print(f"\n{meta.get('label') or path.name} ({path.name}), {len(events)} signals")
        for evt in events:
            if evt.get("event_type") not in ("pickup", "release"):
                continue
            fix = locate_hands([(None, track)], evt["media_time_ms"], MIN_KEYPOINT_CONF)
            a, why_a = decide(fix.points, drawn, evt["event_type"], frame_size)
            b, why_b = decide(fix.points, generated, evt["event_type"], frame_size)
            totals["signals"] += 1
            if a == b:
                kind = "same"
            elif a and renamed.get(a) == b:
                kind = "renamed"
            elif a and b:
                kind = "changed"
            elif a and not b:
                kind = "new_abstention"
            else:
                kind = "resolved_abstention"
            totals[kind] += 1
            print(f"  {evt['media_time_ms'] / 1000:7.2f}s {evt['event_type']:8s} current={a or '? (' + str(why_a) + ')':34s} "
                  f"generated={b or '? (' + str(why_b) + ')':34s} {kind}")
    print("\nTotals:", json.dumps(totals))
    if args.image and layout.background_image:
        draw(args.data_dir / "layouts" / args.layout / layout.background_image, args.image, drawn, generated)
        print(f"Wrote {args.image}")


if __name__ == "__main__":
    main()
