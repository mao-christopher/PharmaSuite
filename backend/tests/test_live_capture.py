"""Live wrist location decisions use actual pose coordinates, not firmware labels."""

from pharma.db.models import Region
from pharma.services.live_capture import associate_wrist


def shelf(name, x0, x1):
    return Region(region_id=name, region_type="designated_shelf", medication_key="TEST",
                  polygon=[(x0, .2), (x1, .2), (x1, .8), (x0, .8)])


def pose(x, y=.5, confidence=.9):
    joints = [[0, 0, 0] for _ in range(17)]
    joints[10] = [x, y, confidence]
    joints[9] = [.95, .5, .9]
    return joints


def test_unique_right_wrist_intersection():
    region, evidence = associate_wrist([pose(.25)] * 3, [shelf("first", .1, .4)], "right", "pickup")
    assert region == "first"
    assert evidence["visible_frames"] == 3


def test_other_wrist_does_not_assign():
    region, evidence = associate_wrist([pose(.25)] * 3, [shelf("first", .1, .4)], "left", "pickup")
    assert region is None
    assert evidence["reason"] == "no_stable_intersection"


def test_competing_regions_abstain():
    frames = [pose(.25)] * 3 + [pose(.75)] * 3
    region, evidence = associate_wrist(frames, [shelf("first", .1, .4), shelf("second", .6, .9)], "right", "pickup")
    assert region is None
    assert evidence["reason"] == "competing_regions"


def test_overlap_or_occlusion_abstains():
    a, b = shelf("first", .1, .6), shelf("second", .3, .9)
    assert associate_wrist([pose(.4)] * 3, [a, b], "right", "release")[0] is None
    assert associate_wrist([pose(.25, confidence=.1)] * 4, [a], "right", "pickup")[0] is None


def test_clip_window_needs_nine_seconds_before_and_one_after():
    from pharma.services.live_capture import buffer_complete

    full = list(range(1000, 11001, 100))  # notification at 10 000 ms
    assert buffer_complete(full, 10_000)
    assert not buffer_complete(full[50:], 10_000)  # starts 4 s before, as the old window did
    assert not buffer_complete(full[:-8], 10_000)  # ends before the post-roll
    assert not buffer_complete(full[:10] + full[15:], 10_000)  # half-second gap


def test_written_clip_keeps_every_frame(tmp_path):
    import cv2
    import numpy as np
    from pharma.services.live_capture import write_clip

    frames = [np.full((72, 128, 3), i * 20, np.uint8) for i in range(12)]
    codec = write_clip(frames, 10.0, tmp_path / "video.mp4")
    assert codec in ("avc1", "mp4v")
    cap = cv2.VideoCapture(str(tmp_path / "video.mp4"))
    assert int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) == len(frames)
    cap.release()


def test_only_a_live_pickup_still_in_hand_counts_as_held():
    from pharma.db.models import MovementSession
    from pharma.services.live_capture import held_movement, holds_bottle

    held = MovementSession(session_id="live:a", medication_key="TEST", original_shelf_id="s", state="HELD")
    uncertain = MovementSession(session_id="live:b", medication_key="UNKNOWN", original_shelf_id="UNKNOWN",
                                state="NEEDS_CONFIRMATION", evidence={"awaiting": "pickup"})
    released = uncertain.model_copy(update={"evidence": {"awaiting": "pickup", "pending_release": {}}})
    assert holds_bottle(held) and holds_bottle(uncertain) and not holds_bottle(released)

    def entry(movement, at, ignored=None):
        return {"live": {"event_type": "pickup", "movement_id": movement, "ingested_at": at, "ignored": ignored}}

    recordings = {"live-1": entry("a", 1), "live-2": entry("c", 2, ignored="pickup_while_holding")}
    assert held_movement(recordings, {"live:a": held}) == "a"
    assert held_movement(recordings, {"live:a": held.model_copy(update={"state": "AT_COUNTER"})}) is None
