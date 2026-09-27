"""Streaming exports must preserve caller-selected inference settings."""
from types import SimpleNamespace
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def test_pose_forwards_streaming_resolution_and_returns_iterator(monkeypatch, tmp_path):
    import pharma.pose as pose
    calls = {}
    stream = iter([object()])
    class Model:
        def __init__(self, path): calls['model'] = path
        def predict(self, **kwargs):
            calls.update(kwargs)
            return stream
    monkeypatch.setattr(pose, 'YOLO', Model)
    settings = SimpleNamespace(default_model='weights.pt', outputs_dir=tmp_path, device='cpu')
    result = pose.run_pose('camera.mp4', save=False, settings=settings, stream=True, imgsz=960, verbose=False)
    assert result is stream
    assert calls['stream'] is True and calls['imgsz'] == 960
    assert calls['save'] is False and calls['verbose'] is False


def test_upload_extraction_uses_the_configured_inference_size(monkeypatch):
    import pharma.pose as pose
    calls = {}
    class Model:
        def __init__(self, path): pass
        def predict(self, **kwargs):
            calls.update(kwargs)
            return iter([])
    monkeypatch.setattr(pose, 'YOLO', Model)
    settings = SimpleNamespace(device='cpu', pose_imgsz=960)
    assert pose.extract_video_keypoints('camera.mp4', 0, 'weights.pt', settings=settings) == []
    assert calls['imgsz'] == 960
    pose.extract_video_keypoints('camera.mp4', 0, 'weights.pt', settings=settings, imgsz=640)
    assert calls['imgsz'] == 640
