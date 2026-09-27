"""Six measured YOLO wrist samples from IMG_3537 against photo-annotated regions.

The fixture contains CV observations, not Unity rig positions or sensor answers.
"""
import json
from pathlib import Path
from pharma.api.replay_stream import Recording, ReplayController
from pharma.db.models import Layout
from pharma.services.recordings import PoseTrack, VideoInfo
from pharma.services.layout import save_catalog, save_layout, split_view
from pharma.db.models import Region
from pharma.services.inventory_engine import nearest_region
from tests.test_api import make_controller

IBU, AMX = 'IBUPROFEN_200MG', 'AMOXICILLIN_500MG'
HOME, WRONG = 'shelf_ibuprofen_200mg', 'shelf_amoxicillin_500mg'
TIMES = [14600,16750,28700,31650,37300,40350]
HANDS = [[(.3963,.5622,.808),(.6009,.7028,.951)],[(.3135,.6315,.991),(.3022,.6043,.912)],
         [(.3319,.605,.997),(.3076,.5734,.952)],[(.3779,.4532,.669),(.5192,.4541,.932)],
         [(.3689,.4471,.753),(.5316,.5508,.976)],[(.3997,.5946,.678),(.5692,.6495,.915)]]


def test_current_registration_abstains_at_final_shelf_boundary():
    regions=[Region(**r) for r in json.loads((Path(__file__).parent/'fixtures/img3537-registered-regions.json').read_text())]
    expected=[HOME,'counter_01','counter_01',WRONG,WRONG,None]
    for hands,rid in zip(HANDS,expected):
        region,evidence=nearest_region(hands,regions,min_margin=.01)
        assert (region.region_id if region else None)==rid
        if rid is None:
            assert evidence['reason']=='ambiguous' and evidence['min_margin']==.01


def test_video_events_change_location_once_and_ambiguous_bottle_requires_confirmation(tmp_path):
    ctrl = make_controller(tmp_path)
    regions = json.loads((Path(__file__).resolve().parents[2] / 'deployment/img3537-camera-regions.json').read_text())
    meds = [{'medication_key': key, 'name': name, 'strength': strength} for key,name,strength in [
        ('METFORMIN_500MG','Metformin','500 mg'),('ATORVASTATIN_20MG','Atorvastatin','20 mg'),
        (IBU,'Ibuprofen','200 mg'),(AMX,'Amoxicillin','500 mg')]]
    layout = Layout(layout_id='default',medications=meds,regions=regions,receipts=[{
        'receipt_id':str(i),'medication_key':m['medication_key'],'bottle_count':1,'tablets_per_bottle':30,
        'expiry_date':'2030-12-31','received_at':'2026-09-27T00:00:00Z'} for i,m in enumerate(meds)])
    _, catalog = split_view(layout)
    save_catalog(ctrl.layouts_dir, catalog)
    saved = save_layout(ctrl.layouts_dir, layout)
    ctrl.apply_layout(saved, reset_inventory=True, catalog=catalog)
    frames = [None] * 1335
    for t,hands in zip(TIMES,HANDS):
        points = [[0,0,0] for _ in range(17)]
        points[9],points[10] = hands
        frames[round(t*30/1000)] = points
    poses = PoseTrack(30,1280,720,frames)
    events = [{'event_id':f'evt_{i}','event_type':'pickup' if i%2==0 else 'release','media_time_ms':t,
               'session_id':f'sess_{i//2}','sensor_id':'video_review_annotation'} for i,t in enumerate(TIMES)]
    path=tmp_path/'scenarios'/'img3537';path.mkdir()
    rec=Recording('img3537',path,{'layout_id':'default','source':'upload'},events,None,VideoInfo(30,1335,1280,720),poses)
    ctrl.current=rec
    assert ctrl._apply(rec,14599)==0
    for i,t in enumerate(TIMES[:4]):
        assert ctrl._apply(rec,t)==1
        inv=ctrl.engine.inventory[IBU]
        expected=[(0,1,0),(0,0,1),(0,1,0),(1,0,0)][i]
        assert (sum(inv.shelf_counts.values()),inv.held_bottles,inv.counter_bottles)==expected
        assert inv.total_bottles==1 and inv.pooled_tablets==30
    assert ctrl.activity[-1]['state']=='MISPLACED' and ctrl.activity[-1]['medication_key']==IBU
    # Replay/restart must not repeat deductions.
    restarted=ReplayController(tmp_path/'scenarios')
    restarted.current=rec
    assert restarted._apply(rec,TIMES[3])==0
    ctrl=restarted
    assert ctrl._apply(rec,TIMES[4])==1
    alert=next(a for a in ctrl.engine.alerts.values() if a.status=='open' and a.metadata.get('reason')=='which_bottle')
    assert {o['medication_key'] for o in alert.metadata['bottle_options']}=={IBU,AMX}
    assert ctrl.engine.inventory[IBU].shelf_counts[WRONG]==1
    assert ctrl._apply(rec,TIMES[5])==1
    assert ctrl.activity[-1]['held_pending']
    # Employee chooses the misplaced Ibuprofen; pending put-down then completes once.
    chosen=next(o['bottle'] for o in alert.metadata['bottle_options'] if o['medication_key']==IBU)
    ctrl.engine.confirm_location(alert.alert_id,WRONG,bottle=chosen)
    ctrl.store.mark_confirmed(alert.metadata['session_id'],{'pickup':WRONG,'release':HOME})
    ctrl.store.save()
    inv=ctrl.engine.inventory[IBU]
    assert inv.shelf_counts[HOME]==1 and inv.shelf_counts[WRONG]==0
    assert inv.held_bottles==inv.counter_bottles==0 and inv.total_bottles==1 and inv.pooled_tablets==30
    assert ctrl.activity[-1]['medication_key']==IBU and not ctrl.activity[-1]['held_pending']
    assert ctrl._apply(rec,44500)==0 and len(ctrl.activity)==6
    assert ctrl.engine.inventory[AMX].shelf_counts[WRONG]==1
    # Explicit demo replay policy clears the run, preserves its audit, and starts fresh.
    rec.meta['reset_on_replay'] = True
    ctrl.current = rec
    ctrl.seek(0)
    assert not ctrl.activity and not ctrl.processed_event_ids
    assert ctrl.engine.inventory[IBU].shelf_counts[HOME] == 1
    assert not ctrl.engine.alerts and any(h['kind']=='demo_run' for h in ctrl.store.history)
    assert ctrl._apply(rec,TIMES[0]) == 1
    ctrl.restart()
    assert ctrl.engine.inventory[IBU].held_bottles == 0 and not ctrl.activity
    assert ctrl._apply(rec,TIMES[0]) == 1
    ctrl.current_media_time_ms = ctrl.duration_ms
    ctrl.play()
    assert ctrl.is_playing and ctrl.current_media_time_ms == 0 and not ctrl.activity
    assert ctrl.engine.inventory[IBU].shelf_counts[HOME] == 1
