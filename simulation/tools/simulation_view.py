"""Render an explicitly labeled, always-visible Unity-rig skeleton for demonstration.

This is an evaluator/presentation tool. Its truth data must never feed CV or stock logic.
"""
import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from pose_videos import writer, text


def validate_rig_frame(record, frame, fps, joints):
    if record.get('source') != 'unity_rig_ground_truth':
        raise ValueError('Skeleton source must be explicitly labeled Unity rig ground truth')
    if record['frame'] != frame or abs(record['media_time_ms'] - frame * 1000 / fps) > .05:
        raise ValueError('Rig and camera clocks differ')
    if len(record['joints_pixels']) != joints or len(record['visible_to_camera']) != joints:
        raise ValueError('Incomplete simulation skeleton')
    if any(not all(np.isfinite(p[k]) for k in ('x','y','z')) or p['z'] <= 0 for p in record['joints_pixels']):
        raise ValueError('Invalid projected rig joint')


def draw_rig(image, record, edges):
    points = record['joints_pixels']; visible = record['visible_to_camera']
    for a,b in zip(edges[::2],edges[1::2]):
        pa = (round(points[a]['x']),round(points[a]['y']))
        pb = (round(points[b]['x']),round(points[b]['y']))
        color = (255,229,69) if visible[a] and visible[b] else (74,175,255)
        cv2.line(image,pa,pb,(24,22,18),9,cv2.LINE_AA)
        cv2.line(image,pa,pb,color,5,cv2.LINE_AA)
    for p,seen in zip(points,visible):
        xy = (round(p['x']),round(p['y']))
        cv2.circle(image,xy,7,(24,22,18),-1,cv2.LINE_AA)
        cv2.circle(image,xy,4,(255,229,69) if seen else (74,175,255),-1,cv2.LINE_AA)


def detail_view(raw,record,index,fps,crop_center):
    height,width=raw.shape[:2]
    points=record['joints_pixels']
    center=np.array([(min(p['x'] for p in points)+max(p['x'] for p in points))/2,
                     (min(p['y'] for p in points)+max(p['y'] for p in points))/2])
    crop_center=center if crop_center is None else crop_center*.85+center*.15
    cx=int(np.clip(crop_center[0],160,width-160));cy=int(np.clip(crop_center[1],200,height-200))
    detail=cv2.resize(raw[cy-200:cy+200,cx-160:cx+160],(720,900))
    cv2.rectangle(detail,(0,0),(720,70),(25,22,18),-1)
    text(detail,'MOTION DETAIL / rendered character',(16,27),.62,(74,175,255))
    text(detail,f'{index/fps:06.2f}s | rig-centered crop, not CV input',(16,56),.54)
    return detail,crop_center


def export(recording,cv_folder,output):
    if output.exists() and any(output.iterdir()):raise FileExistsError(output)
    output.mkdir(parents=True,exist_ok=True)
    manifest=json.loads((recording/'manifest.json').read_text())
    definition=json.loads((recording/'evaluator_only/rig_definition.json').read_text())
    cv_summary=json.loads((cv_folder/'summary.json').read_text())
    if definition['source']!='unity_rig_ground_truth':raise ValueError('Unlabeled simulation truth')
    for field in ('width','height','fps','frame_count'):
        if definition[field]!=manifest[field]:raise ValueError('Rig definition differs from camera')
    video=recording/manifest['video']
    if hashlib.sha256(video.read_bytes()).hexdigest()!=manifest['video_sha256'] or cv_summary['source_video_sha256']!=manifest['video_sha256']:
        raise ValueError('Input videos are from different recordings')
    width,height,fps=manifest['width'],manifest['height'],manifest['fps']
    raw_cap=cv2.VideoCapture(str(video));cv_cap=cv2.VideoCapture(str(cv_folder/'pose-overlay.mp4'))
    writers=[writer(output/'simulation-xray.mp4',width,height,fps),
             writer(output/'simulation-skeleton.mp4',width,height,fps),
             writer(output/'comparison.mp4',1920,1200,fps),
             writer(output/'motion-detail.mp4',720,900,fps)]
    count=occluded=fully_occluded=0
    crop_center=None
    try:
        with (recording/'evaluator_only/rig_skeleton.jsonl').open() as rig:
            for index,line in enumerate(rig):
                record=json.loads(line);validate_rig_frame(record,index,fps,len(definition['joint_names']))
                ok,raw=raw_cap.read();ok_cv,cv_image=cv_cap.read()
                if not ok or not ok_cv:raise ValueError('Video shorter than rig recording')
                count+=1;occluded+=not all(record['visible_to_camera']);fully_occluded+=not any(record['visible_to_camera'])
                xray=raw.copy();skeleton=np.full_like(raw,(22,18,14))
                draw_rig(xray,record,definition['edge_indices']);draw_rig(skeleton,record,definition['edge_indices'])
                for image,title in [(xray,'SIMULATION X-RAY / Unity rig ground truth'),(skeleton,'ALWAYS-VISIBLE SKELETON / Unity rig ground truth')]:
                    cv2.rectangle(image,(0,0),(width,86),(25,22,18),-1)
                    text(image,title,(25,32),.78,(74,175,255),2)
                    text(image,f'{index/fps:06.2f}s | visible through geometry by design | not camera detection',(25,68),.62)
                    text(image,'Cyan: clear scene ray   Amber: scene geometry occludes joint   Source: simulator',(25,height-24),.62,(220,220,220))
                board=np.full((1200,1920,3),(22,18,14),np.uint8)
                text(board,'PHARMA / CAMERA, CV AND SIMULATED STATE',(24,39),.9,thickness=2)
                text(board,f'{index/fps:06.2f} / {manifest["duration_ms"]/1000:.0f}s',(1625,39),.75)
                panels=[(raw,'01  RAW CAMERA'),(xray,'02  SIMULATION X-RAY - NOT CV'),
                        (cv_image,'03  ACTUAL YOLO OBSERVATIONS'),(skeleton,'04  ALWAYS-VISIBLE UNITY SKELETON')]
                for i,(img,title) in enumerate(panels):
                    x=(i%2)*960;y=70+(i//2)*540
                    board[y:y+540,x:x+960]=cv2.resize(img,(960,540))
                    cv2.rectangle(board,(x,y),(x+960,y+34),(25,22,18),-1)
                    text(board,title,(x+18,y+25),.6,(74,175,255) if i in (1,3) else (235,235,235))
                text(board,'The X-ray view uses simulator state. YOLO sees rendered pixels and can lose the technician behind shelves.',(24,1184),.65)
                detail,crop_center=detail_view(raw,record,index,fps,crop_center)
                for w,img in zip(writers,[xray,skeleton,board,detail]):w.send(np.ascontiguousarray(img))
                if index in (240,720,1290,2100):cv2.imwrite(str(output/f'preview-{index:06d}.jpg'),board)
        if count!=manifest['frame_count'] or raw_cap.read()[0] or cv_cap.read()[0]:raise ValueError('Frame counts differ')
    finally:
        raw_cap.release();cv_cap.release()
        for w in writers:w.close()
    result={'source':'unity_rig_ground_truth','usage':'presentation_only_not_cv_input','frame_count':count,
            'fps':fps,'duration_ms':manifest['duration_ms'],'frames_with_occluded_joints':occluded,
            'frames_with_all_joints_occluded':fully_occluded,'joints_per_frame':len(definition['joint_names']),
            'source_video_sha256':manifest['video_sha256'],
            'visibility_note':'Ray tests include solid room geometry, not body self-occlusion or translucent materials.'}
    (output/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('recording',type=Path);parser.add_argument('--cv-presentation',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();export(args.recording.resolve(),args.cv_presentation.resolve(),args.output.resolve())
