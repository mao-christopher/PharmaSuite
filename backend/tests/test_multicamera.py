import json
from pathlib import Path
import cv2
import numpy as np
import pytest
from pharma.services.multicamera import CameraGroup, CameraTrack, arm_score
from pharma.services.recordings import PoseTrack


def pose(conf=.9):
    points = [[.2,.2,0] for _ in range(17)]
    for j in (6,8,10):points[j]=[.2,.2,conf]
    return points


def group(front, side, fps=10):
    return CameraGroup({key:CameraTrack(key,key,1,Path(key+'.mp4'),PoseTrack(fps,128,72,frames))
                        for key,frames in [('front',front),('side',side)]},fps,len(front))


def test_complete_arm_required_and_nan_rejected():
    p=pose();assert arm_score(p)==.9
    p[8][2]=.1;assert arm_score(p)==.1
    p[8][2]=float('nan');assert arm_score(p)==0
    assert arm_score(None)==0


def test_handoff_debounced_and_no_future_observations():
    g=group([pose(),None,None,None,pose(),pose()], [pose()]*6)
    assert g.at(100)['camera_id']=='front'
    # Front lost its arm but hasn't handed off yet; the fallback uses its wrist a frame earlier.
    fix=g.locate(100)
    assert (fix.joint,fix.camera_id,fix.offset_ms)==('wrist','front',-100.0)
    assert g.at(200)['camera_id']=='side'
    assert g.at(200)['switched']
    assert g.at(400)['camera_id']=='side'  # no jump back while current camera remains usable
    assert g.at(0)['camera_id']=='front'  # seeking is deterministic


def test_no_visible_camera_preserves_uncertainty():
    g=group([None]*5,[None]*5)
    assert not any(row['reliable_arm'] for row in g.timeline)
    assert not any(row['switched'] for row in g.timeline)
    assert g.hands_at(400)==([],None)
    assert g.hands_at(0)==([],None)  # nobody in view from the very start: no error, no location


def elbow_only(conf=.9):
    points=[[.2,.2,0] for _ in range(17)]
    points[8]=[.4,.6,conf]
    return points


def test_fallback_uses_elbows_in_any_camera_then_nearest_wrist():
    # Neither camera has a complete arm; the side camera shows an elbow.
    g=group([None]*6,[elbow_only()]*6)
    fix=g.locate(300)
    assert (fix.joint,fix.camera_id,fix.points[1])==('elbow','side',(.4,.6,.9))
    # A wrist seen 0.7 s earlier in the front camera, nothing since: last-seen wrist.
    g=group([pose()]+[None]*11,[None]*12)
    fix=g.locate(700)
    assert (fix.joint,fix.camera_id,fix.offset_ms)==('last_seen_wrist','front',-700.0)
    assert g.locate(0).joint=='wrist'
    # The next known wrist counts up to a second ahead; further than that, confirm.
    g=group([None]*15+[pose()],[None]*16)
    fix=g.locate(700)
    assert (fix.joint,fix.offset_ms)==('next_seen_wrist',800.0)
    assert g.locate(0).joint is None
    # Camera selection itself never looks ahead.
    assert not g.at(1400)['reliable_arm']


def test_uploaded_cameras_may_differ_in_length(tmp_path):
    specs=[write_camera(tmp_path,'front',[pose()]*3),write_camera(tmp_path,'side',[pose()]*4)]
    (tmp_path/'multicam.json').write_text(json.dumps({'schema_version':1,'clock':'media_time','cameras':specs}))
    g=CameraGroup.load(tmp_path)
    assert g.frame_count==3 and g.cameras['side'].keypoints_at(350) is not None
    assert g.cameras['front'].keypoints_at(350) is None  # past the end of the shorter camera


def test_multiple_switches_return_to_recovered_camera():
    g=group([pose(),None,None,pose(),pose()], [pose(),pose(),pose(),None,None])
    assert [r['camera_id'] for r in g.timeline]==['front','front','side','side','front']


def write_camera(root, name, frames, fps=10):
    path=root/(name+'.mp4')
    w=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'mp4v'),fps,(128,72))
    for _ in frames:w.write(np.zeros((72,128,3),np.uint8))
    w.release()
    PoseTrack(fps,128,72,frames).save(root/(name+'.json'),'test')
    return dict(camera_id=name,layout_id='default',calibration_version=1,video=path.name,poses=name+'.json')


def test_group_rejects_mismatched_clock(tmp_path):
    specs=[write_camera(tmp_path,'front',[pose()]*3),write_camera(tmp_path,'side',[pose()]*4)]
    (tmp_path/'multicam.json').write_text(json.dumps({'schema_version':1,'cameras':specs}))
    with pytest.raises(ValueError,match='share'):CameraGroup.load(tmp_path)


def test_inventory_event_only_once_across_handoff_restart(tmp_path, mongo_store):
    from tests.test_api import make_controller
    ctrl=make_controller(tmp_path)
    root=tmp_path/'scenarios/demo_scenario_01'
    # Existing signals at 1s/5s: front hand visible initially, side handles the rest.
    front=[pose()]*7+[None]*93;side=[pose()]*100
    specs=[write_camera(root,'front',front),write_camera(root,'side',side)]
    # Library primary video and pose; the group uses explicit per-camera files.
    import shutil
    shutil.copy2(root/'front.mp4',root/'video.mp4');shutil.copy2(root/'front.json',root/'poses.json')
    (root/'multicam.json').write_text(json.dumps({'schema_version':1,'cameras':specs}))
    ctrl.load_scenario('demo_scenario_01')
    ctrl.seek(2000)
    assert ctrl.activity[0]['camera_id']=='side'
    assert ctrl.activity[0]['calibration_version']==1
    assert ctrl.engine.inventory['AMOXICILLIN_500MG'].held_bottles==1
    ctrl.seek(0);ctrl.seek(2000)
    assert len(ctrl.activity)==1
    restarted=make_controller(tmp_path);restarted.restore_player();restarted.seek(2000)
    assert len(restarted.activity)==1
    assert restarted.engine.inventory['AMOXICILLIN_500MG'].held_bottles==1
    assert restarted.state_dict()['camera_selection']['camera_id']=='side'
