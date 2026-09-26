#!/usr/bin/env python3
"""Run YOLO11 pose estimation (skeleton keypoints) from the CLI."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pharma.pose import run_pose, KEYPOINT_NAMES


def main():
    parser = argparse.ArgumentParser(description="YOLO11 pose estimation — skeleton keypoints")
    parser.add_argument("source", help="Image/video path, directory, URL, or 0 for webcam")
    parser.add_argument("--model", default=None,
                        help="Pose weights (default: yolo11n-pose.pt). "
                             "Options: yolo11n/s/m/l/x-pose.pt")
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--iou",  type=float, default=0.45)
    parser.add_argument("--no-save", action="store_true", help="Skip saving annotated output")
    parser.add_argument("--show",    action="store_true", help="Display live in a window")
    args = parser.parse_args()

    results = run_pose(
        source=args.source,
        model_path=args.model,
        conf=args.conf,
        iou=args.iou,
        save=not args.no_save,
        show=args.show,
    )

    for i, r in enumerate(results):
        if r.keypoints is None:
            print(f"Frame {i}: no people detected")
            continue
        n = len(r.keypoints)
        print(f"\nFrame {i}: {n} person(s) detected")
        for p, kp in enumerate(r.keypoints):
            print(f"  Person {p}:")
            xy   = kp.xy[0].tolist()
            conf = kp.conf[0].tolist() if kp.conf is not None else [None] * 17
            for j, (name, (x, y), c) in enumerate(zip(KEYPOINT_NAMES, xy, conf)):
                conf_str = f"  conf={c:.2f}" if c is not None else ""
                print(f"    [{j:2d}] {name:<16} x={x:.1f}  y={y:.1f}{conf_str}")

    print(f"\nDone. Results saved to runs/pose/")


if __name__ == "__main__":
    main()
