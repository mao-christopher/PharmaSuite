"""Keep through-wall simulation presentation explicit and independent of CV observations."""
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'simulation/tools'))
from simulation_view import draw_rig,validate_rig_frame


def frame():
    return {'source':'unity_rig_ground_truth','frame':30,'media_time_ms':1000,
            'joints_pixels':[{'x':20,'y':20,'z':3},{'x':80,'y':80,'z':3}],
            'visible_to_camera':[False,False]}


def test_all_occluded_joints_are_still_drawn():
    record=frame();validate_rig_frame(record,30,30,2)
    image=np.zeros((100,100,3),dtype=np.uint8)
    draw_rig(image,record,[0,1])
    assert image[50,50].sum()>0
    assert image[20,20].sum()>0 and image[80,80].sum()>0


def test_unlabeled_or_cv_skeleton_cannot_be_presented_as_unity_truth():
    record=frame();record['source']='yolo'
    with pytest.raises(ValueError,match='explicitly labeled'):
        validate_rig_frame(record,30,30,2)


def test_xray_must_share_camera_clock():
    with pytest.raises(ValueError,match='clocks'):
        validate_rig_frame(frame(),31,30,2)


def test_incomplete_rig_is_rejected():
    with pytest.raises(ValueError,match='Incomplete'):
        validate_rig_frame(frame(),30,30,16)
