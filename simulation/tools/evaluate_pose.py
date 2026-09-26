"""Evaluate rendered video through the existing backend pose helper; ground truth stays here."""
import argparse
import contextlib
import json
from pathlib import Path
import platform
import sys
import time

import cv2

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend/src"))
from pharma.pose import run_pose
from pharma.config import Settings


def evaluate(root, weights, threshold):
    manifest = json.loads((root / "manifest.json").read_text())
    calibration = json.loads((root / manifest["calibration"]).read_text())
    truth = [json.loads(line) for line in (root / "evaluator_only/ground_truth.jsonl").read_text().splitlines()]
    output = root / "evaluator_only/pose"
    output.mkdir(parents=True, exist_ok=True)
    settings = Settings()
    settings.device = "cpu"
    start = time.perf_counter()
    with (output / "inference.log").open("w") as log, contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        results = run_pose(str(root / manifest["video"]), model_path=str(weights), save=False, show=False, settings=settings)
    elapsed = time.perf_counter() - start
    if len(results) != manifest["frame_count"]:
        raise ValueError(f"Inference decoded {len(results)} frames; expected {manifest['frame_count']}")
    records = []
    for expected in truth:
        frame = round(expected["media_time_ms"] * manifest["fps"] / 1000)
        result = results[frame]
        record = {"event_id": expected["event_id"], "frame": frame,
                  "expected_region": expected["region_id"], "intentionally_occluded": expected["intentionally_occluded"],
                  "wrist_confidence": None, "predicted_region": None, "candidate_regions": []}
        if len(result.boxes) == 1 and result.keypoints is not None:
            xy = result.keypoints.xy[0, 10].cpu().tolist()
            confidence = float(result.keypoints.conf[0, 10].cpu())
            candidates = [r["region_id"] for r in calibration["regions"]
                          if r["x_min"] <= xy[0] / manifest["width"] <= r["x_max"]
                          and r["y_min"] <= xy[1] / manifest["height"] <= r["y_max"]]
            record.update(wrist_confidence=confidence, candidate_regions=candidates, wrist_pixels=xy)
            if confidence >= threshold and len(candidates) == 1:
                record["predicted_region"] = candidates[0]
        record["outcome"] = ("abstained" if record["predicted_region"] is None else
                             "correct" if record["predicted_region"] == expected["region_id"] else "wrong")
        # This visualization is evaluator output, never the camera feed used as model input.
        image = result.plot()
        for region in calibration["regions"]:
            w, h = manifest["width"], manifest["height"]
            a = (int(region["x_min"] * w), int(region["y_min"] * h))
            b = (int(region["x_max"] * w), int(region["y_max"] * h))
            cv2.rectangle(image, a, b, (170, 210, 40), 1)
            cv2.putText(image, region["region_id"], (a[0], a[1]-3), cv2.FONT_HERSHEY_SIMPLEX, .4, (40, 70, 10), 1)
        cv2.putText(image, f"{expected['media_time_ms']/1000:.1f}s | {record['outcome']} | wrist threshold {threshold}",
                    (25, 35), cv2.FONT_HERSHEY_SIMPLEX, .65, (20, 30, 30), 2)
        cv2.imwrite(str(output / f"event-{frame:06d}.jpg"), image)
        records.append(record)
    import ultralytics
    summary = {"video_frames": len(results), "frames_with_person": sum(bool(len(r.boxes)) for r in results),
               "action_samples": len(records), "correct": sum(r["outcome"] == "correct" for r in records),
               "abstained": sum(r["outcome"] == "abstained" for r in records),
               "wrong": sum(r["outcome"] == "wrong" for r in records),
               "inference_seconds": round(elapsed, 2), "frames_per_second": round(len(results)/elapsed, 2),
               "model": weights.name, "ultralytics": ultralytics.__version__, "device": "cpu",
               "machine": platform.machine(), "wrist_confidence_threshold": threshold,
               "scope": "Scripted synthetic footage; point-in-region feasibility check, not production event fusion or real-camera accuracy.",
               "events": records}
    (root / "evaluator_only/pose_metrics.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k:v for k,v in summary.items() if k != "events"}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording", type=Path)
    parser.add_argument("--weights", required=True, type=Path)
    parser.add_argument("--wrist-confidence", type=float, default=.5)
    args = parser.parse_args()
    evaluate(args.recording.resolve(), args.weights.resolve(), args.wrist_confidence)
