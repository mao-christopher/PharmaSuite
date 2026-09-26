"""Causal arm-visibility handoff for synchronized, calibrated prerecorded cameras.

Uses only estimated keypoints from pixels. No rig truth or inventory event generation.
Camera selection never looks ahead; only the hand fallback (see locate_hands) may use
frames shortly after a signal. A single recording owns the shared sensor stream.
"""
from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Dict, List, Optional
from pharma.services.inventory_engine import MIN_KEYPOINT_CONF
from pharma.services.recordings import HandFix, PoseTrack, locate_hands, probe_video

MULTICAM_FILE = "multicam.json"
# Bundles from the simulator share one frame clock; uploads from separate cameras only
# share a zero time origin, so their frame rates and lengths may differ.
STRICT_CLOCK, MEDIA_CLOCK = "shared_zero_origin", "media_time"


@dataclass
class CameraTrack:
    camera_id: str
    layout_id: str
    calibration_version: Optional[int]  # None: follow the view's current calibration
    video_path: Path
    poses: PoseTrack
    label: Optional[str] = None

    def keypoints_at(self, media_ms):
        idx = self.poses.index_at(media_ms)
        return self.poses.frames[idx] if idx is not None else None


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
            media_ms = frame * 1000 / fps
            scores = {key: arm_score(cam.keypoints_at(media_ms)) for key, cam in cameras.items()}
            for key, score in scores.items(): good[key] = good[key] + 1 if score >= threshold else 0
            lost = lost + 1 if scores[active] < threshold else 0
            previous = active
            if lost >= loss_frames:
                candidates = [key for key in cameras if key != active and good[key] >= acquire_frames]
                if candidates:
                    active = max(candidates, key=lambda key: scores[key])
                    lost = 0
            reliable = scores[active] >= threshold
            self.timeline.append({"frame": frame, "media_time_ms": media_ms,
                                  "camera_id": active, "reliable_arm": reliable, "scores": scores,
                                  "switched": active != previous,
                                  "reason": "arm_lost_handoff" if active != previous else
                                            "visible_arm" if reliable else "no_reliable_active_arm"})

    def at(self, media_ms):
        # Causal sample: never borrow a future frame to decide a switch or an event.
        return self.timeline[max(0, min(self.frame_count - 1, int(media_ms * self.fps / 1000 + 1e-6)))]

    def camera_at(self, media_ms):
        return self.cameras[self.at(media_ms)["camera_id"]]

    def locate(self, media_ms, min_conf=MIN_KEYPOINT_CONF) -> HandFix:
        """The hand at a signal: the selected camera's complete arm, else the fallback chain.

        Camera switching comes first. When no camera has a complete arm, try wrists and
        then elbows within a few frames in every camera (selected one first, then by arm
        score), then the nearest wrist within a second either way; else nothing, and
        the employee confirms.
        """
        selection = self.at(media_ms)
        camera = self.cameras[selection["camera_id"]]
        if selection["reliable_arm"]:
            points = camera.keypoints_at(media_ms) or []
            hands = []
            for shoulder, elbow, wrist in ((5, 7, 9), (6, 8, 10)):
                joints = [points[j] for j in (shoulder, elbow, wrist)] if len(points) == 17 else []
                if joints and min(p[2] for p in joints) >= self.threshold and all(0 <= p[0] <= 1 and 0 <= p[1] <= 1 for p in joints):
                    hands.append(tuple(points[wrist]))
            if hands:
                return HandFix(hands, "wrist", camera.camera_id)
        order = [camera.camera_id] + sorted((k for k in self.cameras if k != camera.camera_id),
                                            key=lambda k: -selection["scores"].get(k, 0))
        fix = locate_hands([(k, self.cameras[k].poses) for k in order], media_ms, min_conf)
        fix.camera_id = fix.camera_id or camera.camera_id
        return fix

    def hands_at(self, media_ms):
        fix = self.locate(media_ms)
        return fix.points, fix.joint

    @classmethod
    def load(cls, root):
        data = json.loads((root / MULTICAM_FILE).read_text())
        if data.get("schema_version") != 1:
            raise ValueError("Unsupported camera group schema")
        flexible = data.get("clock") == MEDIA_CLOCK
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
            if not flexible and clock is not None and current != clock:
                raise ValueError("Camera videos must share frame count, FPS and zero-time origin")
            # A container's frame count is an estimate; uploads only need the rate and size to agree.
            length_ok = flexible or len(poses.frames) == info.frame_count
            if abs(poses.fps - info.fps) > 1e-3 or not length_ok or (poses.width, poses.height) != (info.width, info.height):
                raise ValueError("Camera pose track does not match its video")
            # The first camera's clock drives the player and handoff.
            clock = clock or ((info.fps, len(poses.frames)) if flexible else current)
            cameras[key] = CameraTrack(key, spec["layout_id"], spec.get("calibration_version"), video, poses,
                                       spec.get("label"))
        return cls(cameras, *(clock or (0, 0)))


def read_spec(root: Path) -> Optional[dict]:
    path = root / MULTICAM_FILE
    return json.loads(path.read_text()) if path.exists() else None


def write_spec(root: Path, spec: dict) -> None:
    tmp = root / (MULTICAM_FILE + ".tmp")
    tmp.write_text(json.dumps(spec, indent=2) + "\n")
    tmp.replace(root / MULTICAM_FILE)


def camera_specs(root: Path) -> List[dict]:
    spec = read_spec(root)
    return spec["cameras"] if spec else []
