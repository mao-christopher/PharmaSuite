"""Package synchronized rendered cameras for the dashboard and export a CV-driven POV demo.

Only camera videos, calibration/setup, initial stock, sensor events and YOLO
observations are inputs. This tool never reads evaluator_only or simulation bones.
"""
import argparse
import json
import shutil
from pathlib import Path
import sys
import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend/src'))
from pharma.services.multicamera import CameraGroup
from pharma.db.models import medication_key_for, shelf_region_id
from pose_videos import writer, draw_pose, text

MEDICATIONS = {
    'vitamin-d-50000-iu': ('Vitamin D', '50000 IU'),
    'amoxicillin-500-mg': ('Amoxicillin', '500 mg'),
    'metformin-500-mg': ('Metformin', '500 mg'),
    'atorvastatin-20-mg': ('Atorvastatin', '20 mg'),
    'lisinopril-10-mg': ('Lisinopril', '10 mg'),
    'omeprazole-20-mg': ('Omeprazole', '20 mg'),
}


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2)+'\n')


def package(front, side, front_cv, side_cv, output):
    if output.exists() and any(output.iterdir()): raise ValueError('Output must be new or empty')
    data = output/'dashboard-data'
    recording = data/'scenarios/multicamera-demo'
    recording.mkdir(parents=True)
    sources = [(front, front_cv, 'room-camera-01', 'default'), (side, side_cv, 'room-camera-02', 'side')]
    specs = []
    clock = None
    for index, (source, cv, camera_id, layout_id) in enumerate(sources):
        manifest = json.loads((source/'manifest.json').read_text())
        summary = json.loads((cv/'summary.json').read_text())
        if summary['source_video_sha256'] != manifest['video_sha256']: raise ValueError('CV/video provenance mismatch')
        current = (manifest['fps'], manifest['frame_count'], manifest['duration_ms'])
        if clock and current != clock: raise ValueError('Unsynchronized videos')
        clock = current
        video_name = 'video.mp4' if index == 0 else 'side.mp4'
        poses_name = 'poses.json' if index == 0 else 'side-poses.json'
        shutil.copy2(source/'camera.mp4', recording/video_name)
        observations = [json.loads(line) for line in (cv/'observations.jsonl').read_text().splitlines()]
        frames = []
        for frame, row in enumerate(observations):
            if row['frame'] != frame or abs(row['media_time_ms'] - frame*1000/manifest['fps']) > .01:
                raise ValueError('Invalid observation clock')
            if len(row['people']) != 1:
                frames.append(None); continue
            person = row['people'][0]
            frames.append([[x/manifest['width'], y/manifest['height'], c]
                           for (x,y),c in zip(person['keypoints_xy'], person['keypoints_confidence'])])
        if len(frames) != manifest['frame_count']: raise ValueError('Missing observations')
        write(recording/poses_name, dict(fps=manifest['fps'], width=manifest['width'], height=manifest['height'], frames=frames))
        specs.append(dict(camera_id=camera_id, layout_id=layout_id, calibration_version=1, video=video_name, poses=poses_name))
        calibration = json.loads((source/'calibration.json').read_text())
        regions = []
        for r in calibration['regions']:
            med = medication_key_for(*MEDICATIONS[r['medication_id']]) if r['kind']=='shelf' else None
            rid = shelf_region_id(med) if med else r['region_id']
            x0,y0,x1,y1 = [min(1,max(0,r[k])) for k in ('x_min','y_min','x_max','y_max')]
            regions.append(dict(region_id=rid, region_type={'shelf':'designated_shelf','counter':'dispensing_counter','disposal':'disposal'}[r['kind']], medication_key=med,
                                polygon=[[x0,y0],[x1,y0],[x1,y1],[x0,y1]]))
        write(data/f'layouts/{layout_id}/layout.json',dict(layout_id=layout_id, name=camera_id, calibration_version=1,
              frame_width=manifest['width'],frame_height=manifest['height'], regions=regions,updated_at='2026-09-26T00:00:00Z'))
    inventory = json.loads((front/'initial_inventory.json').read_text())
    write(data/'catalog.json', dict(medications=[dict(medication_key=medication_key_for(name,strength),name=name,strength=strength,unit='tablets') for name,strength in MEDICATIONS.values()],
          receipts=[dict(receipt_id=r['receipt_id'],medication_key=medication_key_for(*MEDICATIONS[r['medication_id']]),bottle_count=r['bottle_count'],tablets_per_bottle=r['initial_tablets'],expiry_date=r['expires_on'],lot_number=r['lot'],received_at='2026-09-26T00:00:00Z') for r in inventory['receipts']]))
    events=[];session=0
    for event in [json.loads(line) for line in (front/'imu_events.jsonl').read_text().splitlines()]:
        if event['event_type'] not in ('pickup','release'):continue
        if event['event_type']=='pickup':session+=1
        events.append({**event,'session_id':f'movement-{session:03d}', 'timestamp':event['media_time_ms']/1000})
    (recording/'imu_events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))
    write(recording/'transactions.json', [dict(transaction_id='demo-rx-001',medication_key=medication_key_for(*MEDICATIONS['vitamin-d-50000-iu']),quantity=30,status='created',deducted=False)])
    write(recording/'scenario.json',dict(label='Synchronized pharmacy cameras',source='multicamera',layout_id='default'))
    write(recording/'multicam.json',dict(schema_version=1,clock='shared_zero_origin',cameras=specs))
    group=CameraGroup.load(recording)
    captures={key:cv2.VideoCapture(str(cam.video_path)) for key,cam in group.cameras.items()}
    w,h=1920,1080
    raw_writer=writer(output/'automatic-pov.mp4',w,h,group.fps)
    board_writer=writer(output/'camera-handoff-comparison.mp4',1920,1080,group.fps)
    switches=[];uncertain=0
    try:
        for i,selected in enumerate(group.timeline):
            images={}
            for key,cap in captures.items():
                ok,img=cap.read()
                if not ok:raise ValueError('Early video EOF')
                images[key]=img
            key=selected['camera_id'];raw=images[key].copy()
            cv2.rectangle(raw,(0,0),(w,76),(28,25,21),-1)
            text(raw,f"{key} | {i/group.fps:.2f}s | "+('ARM VISIBLE' if selected['reliable_arm'] else 'ARM UNCERTAIN'),(25,32),.8)
            text(raw,'POV selected from YOLO arm confidence | shared clock and sensor stream',(25,63),.6)
            raw_writer.send(raw)
            board=np.full((1080,1920,3),(22,18,14),np.uint8)
            for col,(cid,cam) in enumerate(group.cameras.items()):
                img=images[cid].copy();points=cam.poses.frames[i]
                if points:
                    arr=np.array(points);draw_pose(img,arr[None,:,:2]*[cam.poses.width,cam.poses.height],arr[None,:,2],.5)
                small=cv2.resize(img,(960,540));board[80:620,col*960:(col+1)*960]=small
                text(board,cid+('  SELECTED' if cid==key else ''),(col*960+25,48),.8,(70,224,255) if cid==key else (200,200,200))
                text(board,f"Arm score {selected['scores'][cid]:.2f}",(col*960+25,663),.8)
            board[700:1037,30:630]=cv2.resize(images[key],(600,337))
            text(board,f"{i/group.fps:.2f}s | {selected['reason']}",(675,767),.8)
            text(board,'Two real YOLO passes on synchronized Unity footage.',(675,819),.64)
            text(board,'No simulation skeleton or hidden-joint truth drives switching.',(675,859),.58)
            text(board,'No reliable arm: retain uncertainty; do not infer a stock location.',(675,899),.58)
            board_writer.send(board)
            if selected['switched']:switches.append(selected)
            uncertain+=not selected['reliable_arm']
            if i in (240,720,1260):cv2.imwrite(str(output/f'preview-{i:06d}.jpg'),board)
    finally:
        raw_writer.close();board_writer.close()
        for cap in captures.values():cap.release()
    (output/'camera-selection.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in group.timeline))
    summary=dict(frame_count=group.frame_count,fps=group.fps,switches=switches,uncertain_frames=uncertain,
                 sensor_events=len(events),threshold=.5,loss_seconds=.2,acquire_seconds=.1,
                 selection_source='YOLO image keypoints only; at least one complete arm',
                 limitation='Confidence is not proof of true visibility. Single technician; synchronized prerecorded cameras only.')
    write(output/'summary.json',summary)
    print(json.dumps(summary,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for arg in ('front','side','front-cv','side-cv','output'):p.add_argument('--'+arg,type=Path,required=True)
    a=p.parse_args();package(a.front,a.side,a.front_cv,a.side_cv,a.output)
