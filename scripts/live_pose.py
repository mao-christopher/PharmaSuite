#!/usr/bin/env python3
"""
Live YOLO11 pose estimation with skeleton overlay.

Sources
-------
  Webcam (default)  : python scripts/live_pose.py
  Webcam index      : python scripts/live_pose.py --source 1
  Phone (Android)   : python scripts/live_pose.py --source "http://192.168.x.x:8080/video"
  Phone (RTSP)      : python scripts/live_pose.py --source "rtsp://192.168.x.x:8554/video"
  Video file        : python scripts/live_pose.py --source path/to/video.mp4

Phone camera apps
-----------------
  Android : "IP Webcam"  (Play Store) → stream URL shown in app
  iPhone  : "EpocCam", "Camo", or any app that exposes an RTSP / HTTP stream
  DroidCam: cross-platform — installs as a virtual webcam (shows as index 1 or 2)

Keys while running
------------------
  q / ESC  : quit
  s        : save current frame to runs/pose/snapshots/
  p        : pause / resume
"""

import argparse
import sys
import time
from pathlib import Path
from datetime import datetime

import cv2
from ultralytics import YOLO

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from pharma.config import Settings


def parse_source(raw: str):
    """Return an int index for local cameras, otherwise keep the string."""
    try:
        return int(raw)
    except ValueError:
        return raw


def open_capture(source, width: int, height: int, fps: int):
    cap = cv2.VideoCapture(source)
    if isinstance(source, int):
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        cap.set(cv2.CAP_PROP_FPS, fps)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open source: {source!r}")
    return cap


def main():
    parser = argparse.ArgumentParser(description="Live YOLO11 pose — skeleton overlay")
    parser.add_argument("--source", default="0",
                        help="Camera index (0), RTSP URL, or HTTP stream URL")
    parser.add_argument("--model", default=None,
                        help="Pose weights (default: yolo11n-pose.pt). "
                             "Try yolo11s-pose.pt for better accuracy.")
    parser.add_argument("--conf",   type=float, default=0.40,
                        help="Confidence threshold (default 0.40)")
    parser.add_argument("--iou",    type=float, default=0.45)
    parser.add_argument("--width",  type=int,   default=1280)
    parser.add_argument("--height", type=int,   default=720)
    parser.add_argument("--fps",    type=int,   default=30)
    parser.add_argument("--window", default="YOLO11 Pose — press Q to quit")
    args = parser.parse_args()

    cfg = Settings()
    model_path = args.model or str(Path("models") / cfg.default_model)
    if not Path(model_path).exists():
        model_path = cfg.default_model  # let ultralytics download it

    snapshot_dir = cfg.outputs_dir / "pose" / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading model: {model_path}")
    model = YOLO(model_path)

    source = parse_source(args.source)
    print(f"Opening source: {source!r}")
    cap = open_capture(source, args.width, args.height, args.fps)

    cv2.namedWindow(args.window, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(args.window, args.width, args.height)

    paused = False
    frame_count = 0
    t0 = time.time()

    print("\nRunning — press Q or ESC to quit, S to snapshot, P to pause.\n")

    while True:
        if not paused:
            ok, frame = cap.read()
            if not ok:
                print("Stream ended or frame read failed.")
                break

            results = model(
                frame,
                conf=args.conf,
                iou=args.iou,
                verbose=False,
                device=cfg.device,
            )

            annotated = results[0].plot(
                boxes=True,      # bounding box around each person
                kpt_radius=5,    # keypoint dot size
                kpt_line=True,   # draw skeleton lines
                labels=True,
            )

            # FPS overlay
            frame_count += 1
            elapsed = time.time() - t0
            fps_live = frame_count / elapsed if elapsed > 0 else 0
            n_people = len(results[0].boxes) if results[0].boxes is not None else 0
            cv2.putText(
                annotated,
                f"FPS: {fps_live:.1f}  |  People: {n_people}  |  {model_path}",
                (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2,
            )
            if paused:
                cv2.putText(annotated, "PAUSED", (10, 70),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 255), 3)

            cv2.imshow(args.window, annotated)

        key = cv2.waitKey(1) & 0xFF

        if key in (ord("q"), 27):  # Q or ESC
            break
        elif key == ord("s"):
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            out = snapshot_dir / f"snapshot_{ts}.jpg"
            cv2.imwrite(str(out), annotated)
            print(f"Saved snapshot → {out}")
        elif key == ord("p"):
            paused = not paused
            print("Paused" if paused else "Resumed")

    cap.release()
    cv2.destroyAllWindows()
    print("Done.")


if __name__ == "__main__":
    main()
