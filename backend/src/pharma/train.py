"""Fine-tune a YOLO model on a custom dataset."""

from pathlib import Path
from typing import Union
from ultralytics import YOLO
from .config import Settings


def train(
    data_yaml: Union[str, Path],
    model_path: str = None,
    epochs: int = 100,
    imgsz: int = 640,
    batch: int = 16,
    name: str = "train",
    settings: Settings = None,
):
    """
    Fine-tune YOLO on a labelled dataset.

    Args:
        data_yaml: Path to dataset YAML file (Ultralytics format).
        model_path: Starting weights. Defaults to pretrained nano model.
        epochs: Number of training epochs.
        imgsz: Input image size (pixels, square).
        batch: Batch size per GPU (-1 = auto).
        name: Run name inside outputs_dir/train/.
        settings: Settings instance; uses module-level default if None.

    Returns:
        ultralytics.engine.results.Results object with training metrics.
    """
    cfg = settings or Settings()
    model_path = model_path or cfg.default_model

    model = YOLO(model_path)
    results = model.train(
        data=str(data_yaml),
        epochs=epochs,
        imgsz=imgsz,
        batch=batch,
        project=str(cfg.outputs_dir),
        name=name,
        device=cfg.device,
    )
    return results


def export(
    weights: Union[str, Path],
    format: str = "onnx",
):
    """Export trained weights to a deployment format."""
    model = YOLO(str(weights))
    model.export(format=format)
