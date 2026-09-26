import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from validate_recording import validate


def save(path, data):
    path.write_text(json.dumps(data))


@pytest.fixture
def pack(tmp_path):
    (tmp_path / "camera.mp4").write_bytes(b"fixture-video")
    manifest = {"session_id": "test", "video": "camera.mp4", "imu_events": "imu.jsonl",
                "calibration": "calibration.json", "initial_inventory": "inventory.json",
                "business_events": "business.jsonl", "fps": 30, "frame_count": 90,
                "duration_ms": 3000, "width": 1280, "height": 720,
                "video_sha256": hashlib.sha256(b"fixture-video").hexdigest()}
    save(tmp_path / "manifest.json", manifest)
    save(tmp_path / "calibration.json", {"width": 1280, "height": 720, "coordinates": "normalized_top_left",
         "regions": [{"region_id": "shelf-a", "x_min": .1, "y_min": .1, "x_max": .3, "y_max": .3}]})
    save(tmp_path / "inventory.json", {"medications": [{"medication_id": "a", "designated_region": "shelf-a", "total_bottles": 2, "tablets": 200}],
         "receipts": [{"medication_id": "a", "bottle_count": 2, "initial_tablets": 200}]})
    (tmp_path / "business.jsonl").write_text("")
    events = [{"schema_version": 1, "event_id": f"e-{i}", "session_id": "test", "event_type": kind,
               "sensor_id": "mock-imu-01", "media_time_ms": t}
              for i, (kind, t) in enumerate([("pickup", 1000), ("release", 2000)])]
    rewrite_events(tmp_path, events)
    return tmp_path, events


def rewrite_events(root, events):
    (root / "imu.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))


def test_valid_clock_and_single_bottle(pack):
    assert validate(pack[0])["frame_count"] == 90


@pytest.mark.parametrize("field", ["region_id", "medication_id", "bottle_id", "world_position"])
def test_sensor_ground_truth_leak_is_rejected(pack, field):
    root, events = pack
    events[0][field] = "answer"
    rewrite_events(root, events)
    with pytest.raises(ValueError, match="leakage"):
        validate(root)


def test_duplicate_event_cannot_be_packaged(pack):
    root, events = pack
    events[1]["event_id"] = events[0]["event_id"]
    rewrite_events(root, events)
    with pytest.raises(ValueError, match="Duplicate"):
        validate(root)


@pytest.mark.parametrize("timestamp", [999, 3000, -1])
def test_invalid_clock_is_rejected(pack, timestamp):
    root, events = pack
    events[1]["media_time_ms"] = timestamp
    rewrite_events(root, events)
    with pytest.raises(ValueError, match="timestamp|aligned"):
        validate(root)


def test_second_pickup_is_rejected(pack):
    root, events = pack
    events[1]["event_type"] = "pickup"
    rewrite_events(root, events)
    with pytest.raises(ValueError, match="one-bottle"):
        validate(root)


def test_runtime_manifest_cannot_read_evaluator_files(pack):
    root, _ = pack
    manifest = json.loads((root / "manifest.json").read_text())
    manifest["imu_events"] = "evaluator_only/ground_truth.jsonl"
    save(root / "manifest.json", manifest)
    with pytest.raises(ValueError, match="ground truth"):
        validate(root)


def test_mismatched_video_fails(pack):
    root, _ = pack
    (root / "camera.mp4").write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash"):
        validate(root)


def test_receiving_totals_must_match(pack):
    root, _ = pack
    inventory = json.loads((root / "inventory.json").read_text())
    inventory["receipts"][0]["initial_tablets"] = 100
    save(root / "inventory.json", inventory)
    with pytest.raises(ValueError, match="tablet stock"):
        validate(root)
