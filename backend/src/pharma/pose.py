"""Run YOLO11 pose estimation (skeleton keypoints) on images, video, or webcam."""

import os
from pathlib import Path
from typing import Callable, List, Optional, Union
from ultralytics import YOLO
from .config import Settings


def resolve_model_path(settings: Settings = None) -> str:
    """POSE_MODEL env var, then models_dir, else the bare name (ultralytics downloads it)."""
    cfg = settings or Settings()
    if os.getenv("POSE_MODEL"):
        return os.environ["POSE_MODEL"]
    local = cfg.models_dir / cfg.default_model
    return str(local) if local.exists() else cfg.default_model


def extract_video_keypoints(
    video_path: Union[str, Path],
    total_frames: int,
    model_path: Optional[str] = None,
    conf: float = 0.25,
    progress: Optional[Callable[[int, int], None]] = None,
    settings: Settings = None,
    require_single_person: bool = False,
    person_counts: Optional[List[int]] = None,
) -> List[Optional[List[List[float]]]]:
    """Per-frame keypoints of the most confident person: 17 x [x_norm, y_norm, conf], or None."""
    cfg = settings or Settings()
    model = YOLO(model_path or resolve_model_path(cfg))
    frames: List[Optional[List[List[float]]]] = []
    for result in model.predict(source=str(video_path), conf=conf, stream=True, verbose=False, device=cfg.device):
        kp = result.keypoints
        if person_counts is not None:
            person_counts.append(len(result.boxes) if result.boxes is not None else 0)
        if (kp is None or result.boxes is None or len(result.boxes) == 0 or kp.xyn is None
                or (require_single_person and len(result.boxes) != 1)):
            frames.append(None)
        else:
            best = int(result.boxes.conf.argmax())
            xyn = kp.xyn[best].tolist()
            kconf = kp.conf[best].tolist() if kp.conf is not None else [1.0] * len(xyn)
            frames.append([[round(x, 4), round(y, 4), round(c, 3)] for (x, y), c in zip(xyn, kconf)])
        if progress:
            progress(len(frames), total_frames)
    return frames


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
