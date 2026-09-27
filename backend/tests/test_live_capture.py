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
