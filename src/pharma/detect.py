"""Run YOLO inference on images, video, or a webcam stream."""

from pathlib import Path
from typing import Union
from ultralytics import YOLO
from .config import Settings


def run_detection(
    source: Union[str, int, Path],
    model_path: str = None,
    conf: float = 0.25,
    iou: float = 0.45,
    save: bool = True,
    show: bool = False,
    settings: Settings = None,
):
    """
    Run object detection on a source.

    Args:
        source: Image path, video path, directory, URL, or webcam index (0).
        model_path: Path or name of YOLO model weights. Defaults to settings default.
        conf: Confidence threshold (0–1).
        iou: IoU threshold for NMS (0–1).
        save: Save annotated results to disk.
        show: Display results in a window (requires a display).
        settings: Settings instance; uses module-level default if None.

    Returns:
        List of ultralytics Results objects.
    """
    cfg = settings or Settings()
    model_path = model_path or cfg.default_model

    model = YOLO(model_path)
    results = model.predict(
        source=source,
        conf=conf,
        iou=iou,
        save=save,
        show=show,
        project=str(cfg.outputs_dir),
        name="detect",
        device=cfg.device,
    )
    return results
