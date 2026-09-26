"""Protect uncertainty and prevent hidden-joint lines in the CV presentation."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from pose_observations import region_observation, visible_edges


def test_arm_edge_requires_both_confident_joints():
    conf = [1.] * 17
    conf[10] = .1
    assert (6, 8) in visible_edges(conf, .5)
    assert (8, 10) not in visible_edges(conf, .5)


def test_low_confidence_wrist_does_not_assign_region():
    points = [[[50, 50]] * 17]; confidence = [[1.] * 17]
    confidence[0][10] = .1
    regions = [dict(region_id='a', x_min=0, x_max=1, y_min=0, y_max=1)]
    assert region_observation(points, confidence, regions, 100, 100, .5)['region'] is None


def test_overlapping_depth_regions_require_abstention():
    points = [[[50, 50]] * 17]; confidence = [[1.] * 17]
    regions = [dict(region_id=r, x_min=0, x_max=1, y_min=0, y_max=1) for r in ['front','rear']]
    result = region_observation(points, confidence, regions, 100, 100, .5)
    assert result['region'] is None and result['candidates'] == ['front','rear']


def test_no_person_is_uncertain():
    assert region_observation([], [], [], 100, 100, .5)['status'] == 'uncertain'
