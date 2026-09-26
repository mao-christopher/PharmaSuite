#!/usr/bin/env python3
"""CLI wrapper around src/pharma/detect.py."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pharma.detect import run_detection


def main():
    parser = argparse.ArgumentParser(description="Run YOLO detection")
    parser.add_argument("source", help="Image/video path, directory, URL, or webcam index")
    parser.add_argument("--model", default=None, help="Model weights path or name (e.g. yolo11n.pt)")
    parser.add_argument("--conf", type=float, default=0.25, help="Confidence threshold")
    parser.add_argument("--iou", type=float, default=0.45, help="IoU threshold")
    parser.add_argument("--no-save", action="store_true", help="Do not save annotated results")
    parser.add_argument("--show", action="store_true", help="Display results in a window")
    args = parser.parse_args()

    results = run_detection(
        source=args.source,
        model_path=args.model,
        conf=args.conf,
        iou=args.iou,
        save=not args.no_save,
        show=args.show,
    )
    print(f"\nDetection complete. {len(results)} frame(s) processed.")


if __name__ == "__main__":
    main()
