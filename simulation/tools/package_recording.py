"""Encode a Unity frame export and add synthetic business fixtures + replay manifest."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def add_fixtures(root, capture):
    calibration = json.loads((root / "calibration.json").read_text())
    medications, receipts = [], []
    for region in calibration["regions"]:
        if region["kind"] != "shelf":
            continue
        key = region["medication_id"]
        count = 2 if region["region_id"] == "shelf-a" else 4
        medications.append({"medication_id": key, "designated_region": region["region_id"],
                            "bottles_on_shelf": count, "total_bottles": count,
                            "tablets": count * 100})
        for n in range(count):
            receipts.append({"receipt_id": f"receipt-{region['region_id']}-{n+1}",
                             "medication_id": key, "bottle_count": 1,
                             "initial_tablets": 100, "lot": f"DEMO-{region['region_id']}-{n+1}",
                             "expires_on": "2026-09-01" if key == "vitamin-d-50000-iu" and n == 0 else "2027-12-31"})
    write_json(root / "initial_inventory.json", {
        "schema_version": 1, "synthetic": True,
        "pharmacy_time": "2026-09-26T09:00:00-04:00", "timezone": "America/New_York",
        "medications": medications, "receipts": receipts,
    })
    offset = capture.get("workflow_offset_ms", 0)
    scale = capture.get("workflow_time_scale", 1)
    events = []
    for n, (time, status) in enumerate([(0, "arrived"), (8000, "confirmed_filled"), (10000, "payment_receipt")]):
        events.append({"schema_version": 1, "event_id": f"{capture['session_id']}-rx-{n}",
                       "session_id": capture["session_id"], "media_time_ms": time * scale + offset,
                       "transaction_id": "demo-rx-001", "medication_id": "vitamin-d-50000-iu",
                       "quantity": 30, "status": status})
    (root / "business_events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    # These are evaluation expectations, never runtime feeds or prefilled employee answers.
    write_json(root / "evaluator_only/expected_workflow.json", {
        "manual_disposal_confirmation": {
            "after_time_ms": offset + 29000 * scale,
            "select_receipt": "receipt-shelf-a-1", "discarded_tablets": 70,
            "explanation": "Employee identifies the expired bottle and enters discarded tablets."
        },
        "expected_tablets_after_prescription": 170,
        "expected_tablets_after_expired_disposal_confirmation": 100,
        "last_disposal_without_quantity": {"time_ms": offset + 41000 * scale, "expected_total_bottles": 0, "expected_tablets": 0},
        "note": "Expected inventory outcomes require the future inventory service/dashboard; the simulator does not implement them."
    })


def package(root):
    import imageio_ffmpeg
    capture = json.loads((root / "capture.json").read_text())
    frames = sorted((root / "frames").glob("*.png"))
    if [p.name for p in frames] != [f"{i:06d}.png" for i in range(capture["frame_count"])]:
        raise ValueError("Missing, extra, or incorrectly numbered frames")
    output = root / "camera.mp4"
    if output.exists():
        raise FileExistsError(output)
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-framerate", str(capture["fps"]),
                    "-i", str(root / "frames/%06d.png"), "-c:v", "libx264", "-crf", "18",
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output)], check=True)
    add_fixtures(root, capture)
    manifest = {k: v for k, v in capture.items() if k != "frames"}
    manifest.update({"video": "camera.mp4", "video_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                     "duration_ms": capture["frame_count"] * 1000 / capture["fps"],
                     "timebase": "constant_fps_media_clock", "synthetic": True})
    write_json(root / "manifest.json", manifest)
    from validate_recording import validate
    validate(root)
    print(f"Packaged and validated: {root / 'manifest.json'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording", type=Path)
    package(parser.parse_args().recording.resolve())
