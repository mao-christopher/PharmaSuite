"""Stream real YOLO observations into synchronized overlay, skeleton and comparison videos.

Reads camera, calibration and abstract IMU events only. Never reads evaluator truth
or Unity bones. Skeleton edges are drawn only when BOTH keypoints pass confidence.
"""
import argparse
import contextlib
import hashlib
import json
from pathlib import Path
import sys
import time

import cv2
import imageio_ffmpeg
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend/src"))
from pharma.config import Settings
from pharma.pose import run_pose, KEYPOINT_NAMES

from pose_observations import region_observation, visible_edges

ARMS = {(5, 7), (7, 9), (6, 8), (8, 10)}


def text(image, message, xy, size=.65, color=(226, 233, 237), thickness=1):
    cv2.putText(image, message, xy, cv2.FONT_HERSHEY_SIMPLEX, size, color, thickness, cv2.LINE_AA)


def draw_pose(image, points, confidence, threshold):
    for person, conf in zip(points, confidence):
        for a, b in visible_edges(conf, threshold):
            color = (70, 224, 255) if (a, b) in ARMS else (191, 168, 105)
            cv2.line(image, tuple(np.rint(person[a]).astype(int)), tuple(np.rint(person[b]).astype(int)),
                     color, 5 if (a, b) in ARMS else 2, cv2.LINE_AA)
        for i, (point, score) in enumerate(zip(person, conf)):
            if score >= threshold:
                cv2.circle(image, tuple(np.rint(point).astype(int)), 5 if i in (7,8,9,10) else 3,
                           (70,224,255) if i in (5,6,7,8,9,10) else (211,187,134), -1, cv2.LINE_AA)


def writer(path, width, height, fps):
    w = imageio_ffmpeg.write_frames(str(path), (width, height), fps=fps, codec="libx264",
          pix_fmt_in="bgr24", pix_fmt_out="yuv420p", macro_block_size=1,
          output_params=["-crf", "19", "-preset", "fast", "-movflags", "+faststart"], ffmpeg_log_level="error")
    w.send(None)
    return w


def export(root, output, weights, threshold=.5, image_size=960):
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Output must be new or empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((root / "manifest.json").read_text())
    video = root / manifest['video']
    if hashlib.sha256(video.read_bytes()).hexdigest() != manifest['video_sha256']:
        raise ValueError("Video hash differs from replay manifest")
    calibration = json.loads((root / manifest['calibration']).read_text())
    events = [json.loads(l) for l in (root / manifest['imu_events']).read_text().splitlines()]
    width, height, fps = manifest['width'], manifest['height'], manifest['fps']
    settings = Settings(); settings.device = 'cpu'
    writers = [writer(output / 'pose-overlay.mp4', width, height, fps),
               writer(output / 'skeleton-only.mp4', width, height, fps),
               writer(output / 'comparison.mp4', 1920, 720, fps)]
    start = time.perf_counter()
    frame_count = person_frames = wrist_frames = 0
    event_index = 0; last_event = None
    try:
        with (output / 'observations.jsonl').open('w') as observations, (output / 'inference.log').open('w') as log, contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            results = run_pose(str(video), model_path=str(weights), save=False, show=False, settings=settings, stream=True, imgsz=image_size, verbose=False)
            for frame_count, result in enumerate(results, start=1):
                frame = frame_count - 1
                media_ms = frame * 1000 / fps
                raw = result.orig_img
                if raw.shape[:2] != (height,width): raise ValueError('Unexpected video dimensions')
                points = result.keypoints.xy.cpu().numpy() if result.keypoints is not None else np.empty((0,17,2))
                confidence = result.keypoints.conf.cpu().numpy() if result.keypoints is not None else np.empty((0,17))
                person_frames += bool(len(points))
                wrist_frames += bool(len(points)==1 and confidence[0][10]>=threshold)
                observation = region_observation(points,confidence,calibration['regions'],width,height,threshold)
                synchronized_events = []
                while event_index < len(events) and events[event_index]['media_time_ms'] <= media_ms + .01:
                    last_event=events[event_index]; synchronized_events.append(last_event); event_index+=1
                observations.write(json.dumps({'frame':frame,'media_time_ms':media_ms,'people':[
                    {'keypoints_xy':p.tolist(),'keypoints_confidence':c.tolist()} for p,c in zip(points,confidence)],
                    'right_wrist_region':observation,'imu_event_ids':[e['event_id'] for e in synchronized_events]})+'\n')
                overlay = raw.copy(); skeleton = np.full_like(raw,(22,18,14))
                for region in calibration['regions']:
                    a=(round(region['x_min']*width),round(region['y_min']*height))
                    b=(round(region['x_max']*width),round(region['y_max']*height))
                    cv2.rectangle(overlay,a,b,(128,150,88),1)
                    text(overlay,region['region_id'],(a[0],a[1]-5),.38,(160,205,153))
                draw_pose(overlay,points,confidence,threshold);draw_pose(skeleton,points,confidence,threshold)
                status=f"{media_ms/1000:05.2f}s | people {len(points)} | wrist: {observation['region'] or 'uncertain'}"
                for target,title in [(overlay,'YOLO POSE / rendered pixels'),(skeleton,'YOLO SKELETON / no Unity joint data')]:
                    cv2.rectangle(target,(0,0),(width,82),(27,24,20),-1)
                    text(target,title,(25,32),.7); text(target,status,(25,65),.6,(70,224,255))
                text(skeleton,'Arms highlighted | low-confidence joints omitted | no hidden-joint reconstruction',
                     (25,height-28),.6,(150,162,172))
                board=np.full((720,1920,3),(22,18,14),np.uint8)
                text(board,'PHARMA / CAMERA TO POSE',(35,48),1.05,thickness=2)
                text(board,'Three shelf banks / single fixed camera / actual YOLO inference',(35,82),.64,(168,177,188))
                for i,(img,title) in enumerate([(raw,'01  UNITY CAMERA'),(overlay,'02  CV OVERLAY'),(skeleton,'03  ESTIMATED SKELETON')]):
                    text(board,title,(i*640+20,125),.64,(70,224,255))
                    board[145:505,i*640:(i+1)*640]=cv2.resize(img,(640,360))
                text(board,f'MEDIA CLOCK   {media_ms/1000:05.2f} / {manifest["duration_ms"]/1000:.0f} s',(30,551),.72)
                text(board,f'RIGHT WRIST   {observation["region"] or "UNCERTAIN / no unique region"}',(650,551),.65)
                conf=observation.get('right_wrist_confidence')
                text(board,f'CONFIDENCE   {conf:.2f}' if conf is not None else 'CONFIDENCE   unavailable',(1350,551),.65)
                event_label='none yet' if last_event is None else f'{last_event["event_type"]} at {last_event["media_time_ms"]/1000:.2f}s'
                text(board,'LATEST MOCK IMU   '+event_label,(30,603),.68,(70,224,255))
                text(board,'2D wrist-region candidates are observations, not confirmed inventory updates.',(30,658),.64,(168,177,188))
                text(board,'Shelves can hide people. YOLO can miss or guess joints; confidence is not proof of visibility.',(30,690),.59,(168,177,188))
                for w,img in zip(writers,[overlay,skeleton,board]):w.send(np.ascontiguousarray(img))
                if frame in (240,720,1260,1800): cv2.imwrite(str(output/f'preview-{frame:06d}.jpg'),board)
    finally:
        for w in writers:w.close()
    if frame_count != manifest['frame_count']:raise ValueError('Frame count mismatch')
    elapsed=time.perf_counter()-start
    summary={'session_id':manifest['session_id'], 'calibration_version':manifest['calibration_version'],
             'frame_count':frame_count,'fps':fps,'duration_ms':manifest['duration_ms'],
             'frames_with_person':person_frames,'frames_with_confident_right_wrist':wrist_frames,
             'keypoint_threshold':threshold,'elapsed_seconds':round(elapsed,2),
             'model':weights.name,'inference_image_size':image_size,'source_video_sha256':manifest['video_sha256'],
             'keypoint_names':KEYPOINT_NAMES,'coordinates':'top_left_pixels',
             'notes':'YOLO estimates only. No ground truth read. 2D region candidates do not resolve depth or prove grasp/release.'}
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('recording',type=Path);parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--weights',required=True,type=Path);parser.add_argument('--confidence',type=float,default=.5)
    parser.add_argument('--image-size',type=int,default=960)
    args=parser.parse_args()
    export(args.recording.resolve(),args.output.resolve(),args.weights.resolve(),args.confidence,args.image_size)
