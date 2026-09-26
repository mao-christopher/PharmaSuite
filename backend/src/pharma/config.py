from pathlib import Path
from dotenv import load_dotenv
import os

load_dotenv()

ROOT = Path(__file__).resolve().parents[2]


def _cuda_available() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except ImportError:
        return False


class Settings:
    data_dir: Path = ROOT / os.getenv("DATA_DIR", "data")
    models_dir: Path = ROOT / os.getenv("MODELS_DIR", "models")
    outputs_dir: Path = ROOT / os.getenv("OUTPUTS_DIR", "runs")
    default_model: str = "yolo11n-pose.pt"
    # Pose inference size (long side, px). 960 finds distant people that 640 misses, at
    # roughly twice the processing time. The uploaded video itself is never resized.
    pose_imgsz: int = int(os.getenv("POSE_IMGSZ", "960"))
    device: str = "cuda" if _cuda_available() else "cpu"
