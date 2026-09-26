"""Run YOLO11 pose estimation (skeleton keypoints) on images, video, or webcam."""

from pathlib import Path
from typing import Union
from ultralytics import YOLO
from .config import Settings


def run_pose(
    source: Union[str, int, Path],
    model_path: str = None,
    conf: float = 0.25,
    iou: float = 0.45,
    save: bool = True,
    show: bool = False,
    settings: Settings = None,
    stream: bool = False,
    imgsz: int = 640,
    verbose: bool = True,
):
    """
    Run pose estimation on a source and return keypoints (17-point COCO skeleton).

    Args:
        source: Image path, video path, directory, URL, or webcam index (0).
        model_path: YOLO pose weights. Defaults to yolo11n-pose.pt.
        conf: Confidence threshold.
        iou: IoU threshold for NMS.
        save: Save annotated skeleton results to disk.
        show: Display results in a window.
        settings: Settings instance; uses module-level default if None.
        stream: Yield one result at a time for long videos, instead of retaining frames.
        imgsz: Inference image size; larger values can help with distant people.
        verbose: Print per-frame inference output.

    Returns:
        List of ultralytics Results, or an iterator when stream=True. Each result has:
          .keypoints.xy   — (N, 17, 2) pixel coordinates
          .keypoints.conf — (N, 17)    per-keypoint confidence
          .boxes          — bounding boxes around each person
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
        name="pose",
        device=cfg.device,
        stream=stream,
        imgsz=imgsz,
        verbose=verbose,
    )
    return results


# COCO 17-keypoint skeleton joint names, ordered by index
KEYPOINT_NAMES = [
    "nose",
    "left_eye", "right_eye",
    "left_ear", "right_ear",
    "left_shoulder", "right_shoulder",
    "left_elbow", "right_elbow",
    "left_wrist", "right_wrist",
    "left_hip", "right_hip",
    "left_knee", "right_knee",
    "left_ankle", "right_ankle",
]
