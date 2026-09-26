"""Smoke tests — verify YOLO loads and runs inference without errors."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def test_import():
    from pharma.detect import run_detection  # noqa: F401


def test_detect_on_sample_image():
    """Run detection on YOLO's built-in sample so no data download is needed."""
    from ultralytics import YOLO
    from ultralytics.data.utils import IMG_FORMATS  # noqa: F401

    model = YOLO("yolo11n.pt")
    results = model.predict("https://ultralytics.com/images/bus.jpg", save=False, verbose=False)
    assert len(results) > 0
    assert results[0].boxes is not None
