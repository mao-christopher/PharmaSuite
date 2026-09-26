"""Causal arm-visibility handoff for synchronized, calibrated prerecorded cameras.

Uses only estimated keypoints from pixels. No rig truth, future-frame fallback,
or inventory event generation. A single recording owns the shared sensor stream.
"""
from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Dict
from pharma.services.recordings import PoseTrack, probe_video


@dataclass
class CameraTrack:
    camera_id: str
    layout_id: str
    calibration_version: int
    video_path: Path
    poses: PoseTrack


def arm_score(keypoints, threshold=.5):
    if not keypoints or len(keypoints) != 17:
        return 0.0
    # All three joints on the same arm must be confident and inside the image.
    scores = []
    for joints in ((5, 7, 9), (6, 8, 10)):
        scores.append(min((keypoints[j][2] if 0 <= keypoints[j][0] <= 1 and 0 <= keypoints[j][1] <= 1
                           and all(math.isfinite(v) for v in keypoints[j]) else 0) for j in joints))
    return max(scores)


class CameraGroup:
    def __init__(self, cameras: Dict[str, CameraTrack], fps, frame_count, threshold=.5,
                 lost_seconds=.2, acquire_seconds=.1):
        if len(cameras) < 2 or fps <= 0 or frame_count < 1:
            raise ValueError("A synchronized group needs at least two cameras and a valid clock")
        self.cameras, self.fps, self.frame_count = cameras, fps, frame_count
        self.threshold = threshold
        self.timeline = []
        active = next(iter(cameras))
        good = {key: 0 for key in cameras}
        lost = 0
        loss_frames = max(1, math.ceil(lost_seconds * fps))
        acquire_frames = max(1, math.ceil(acquire_seconds * fps))
        for frame in range(frame_count):
            scores = {key: arm_score(cam.poses.frames[frame]) for key, cam in cameras.items()}
            for key, score in scores.items(): good[key] = good[key] + 1 if score >= threshold else 0
            lost = lost + 1 if scores[active] < threshold else 0
            previous = active
            if lost >= loss_frames:
                candidates = [key for key in cameras if key != active and good[key] >= acquire_frames]
                if candidates:
                    active = max(candidates, key=lambda key: scores[key])
                    lost = 0
            reliable = scores[active] >= threshold
            self.timeline.append({"frame": frame, "media_time_ms": frame * 1000 / fps,
                                  "camera_id": active, "reliable_arm": reliable, "scores": scores,
                                  "switched": active != previous,
                                  "reason": "arm_lost_handoff" if active != previous else
                                            "visible_arm" if reliable else "no_reliable_active_arm"})

    def at(self, media_ms):
        # Causal sample: never borrow a future frame to decide a switch or an event.
        return self.timeline[max(0, min(self.frame_count - 1, int(media_ms * self.fps / 1000 + 1e-6)))]

    def camera_at(self, media_ms):
        return self.cameras[self.at(media_ms)["camera_id"]]

    def hands_at(self, media_ms):
        selection = self.at(media_ms)
        if not selection["reliable_arm"]:
            return [], None
        camera = self.cameras[selection["camera_id"]]
        points = camera.poses.frames[selection["frame"]]
        hands = []
        for shoulder, elbow, wrist in ((5, 7, 9), (6, 8, 10)):
            if min(points[j][2] for j in (shoulder, elbow, wrist)) >= self.threshold:
                if all(0 <= points[j][0] <= 1 and 0 <= points[j][1] <= 1 for j in (shoulder, elbow, wrist)):
                    hands.append(tuple(points[wrist]))
        return hands, "wrist" if hands else None

    @classmethod
    def load(cls, root):
        data = json.loads((root / "multicam.json").read_text())
        if data.get("schema_version") != 1:
            raise ValueError("Unsupported camera group schema")
        cameras = {}
        clock = None
        def local_file(name):
            path = (root / name).resolve()
            if not path.is_relative_to(root.resolve()) or not path.is_file():
                raise ValueError("Camera assets must be existing files inside the recording")
            return path
        for spec in data["cameras"]:
            key = spec["camera_id"]
            if key in cameras: raise ValueError("Duplicate camera ID")
            video = local_file(spec["video"])
            poses = PoseTrack.load(local_file(spec["poses"]))
            info = probe_video(video)
            current = (info.fps, info.frame_count)
            if clock is not None and current != clock:
                raise ValueError("Camera videos must share frame count, FPS and zero-time origin")
            if poses.fps != info.fps or len(poses.frames) != info.frame_count or (poses.width, poses.height) != (info.width, info.height):
                raise ValueError("Camera pose track does not match its video")
            clock = current
            cameras[key] = CameraTrack(key, spec["layout_id"], spec["calibration_version"], video, poses)
        return cls(cameras, *(clock or (0, 0)))
