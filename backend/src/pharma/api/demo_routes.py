"""Guided setup for the deployment's supplied scan and recording.

Source media and room identity are provisioned by the deployer. User annotations
go through the normal upload pipeline; they never contain location answers.
"""
import json
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from pharma.api.routes import get_controller, _stage_videos, _finish_upload, _discard_draft, UploadViewChoice
from pharma.services.room import load_room
from pharma.services.privacy import skeleton_visible

router = APIRouter(prefix='/api/demo')


def files(ctrl):
    return ctrl.scenarios_dir.parent / 'demo'


def config(ctrl):
    path = files(ctrl) / 'setup.json'
    if not path.exists():
        raise HTTPException(404, 'No supplied demo has been configured on this server.')
    return json.loads(path.read_text(encoding='utf-8'))


@router.get('')
def status(ctrl=Depends(get_controller)):
    data = config(ctrl)
    room = load_room(ctrl.rooms_dir, data['room_id'])
    rec = data.get('recording')
    summary = ctrl.recording_summary(ctrl.scenario_dir(rec)) if rec else None
    layout_id = summary['layout_id'] if summary else None
    return {**data, 'simulation_ready': (files(ctrl) / 'simulation.mp4').exists(), 'video_url': '/api/demo/video', 'regions': len(room.regions),
            'camera_registered': any(c.layout_id == layout_id for c in room.cameras),
            'recording_summary': summary, 'renderer_reason': ctrl.renders.unavailable_reason()}


@router.get('/video')
def video(ctrl=Depends(get_controller)):
    config(ctrl)
    path = files(ctrl) / 'video.mp4'
    if not path.exists():
        raise HTTPException(404, 'Demo video is still being prepared.')
    return FileResponse(path, media_type='video/mp4')


@router.get('/presentation')
def presentation(ctrl=Depends(get_controller)):
    data = config(ctrl)
    path = ctrl.scenario_dir(data['recording'])
    poses = json.loads((path / 'poses.json').read_text())
    events = [json.loads(line) for line in (path / 'imu_events.jsonl').read_text().splitlines() if line.strip()]
    poses['frames'] = [frame if skeleton_visible(i * 1000 / poses['fps'], events) else None
                       for i, frame in enumerate(poses['frames'])]
    return {**poses, 'regions': data.get('presentation_regions', []), 'lead_s': 1.0}


@router.get('/simulation')
def simulation(ctrl=Depends(get_controller)):
    path = files(ctrl) / 'simulation.mp4'
    if not path.exists():
        raise HTTPException(404, 'Unity simulation has not been rendered yet.')
    return FileResponse(path, media_type='video/mp4')


class Signal(BaseModel):
    time_s: float = Field(ge=0, allow_inf_nan=False)
    event: Literal['pickup', 'release']


class Prepare(BaseModel):
    signals: list[Signal] = Field(min_length=1, max_length=1000)


@router.post('/prepare')
def prepare(body: Prepare, ctrl=Depends(get_controller)):
    data = config(ctrl)
    # Serialized by the inventory middleware: retries cannot create duplicate recordings.
    if data.get('recording'):
        return {'name': data['recording'], 'already_created': True}
    source = files(ctrl) / 'video.mp4'
    if not source.exists():
        raise HTTPException(409, 'Demo video is still being prepared.')
    events = 'time_s,event\n' + ''.join(f'{s.time_s},{s.event}\n' for s in sorted(body.signals, key=lambda s: s.time_s))
    with source.open('rb') as stream:
        draft = _stage_videos(ctrl, [UploadFile(filename='IMG_3537.mp4', file=stream)])
    try:
        result = _finish_upload(ctrl, draft, events.encode(), 'manual-timestamps.csv', 'IMG_3537', None,
                                [UploadViewChoice(camera_id='camera-1', action='new', name='Pharmacy security camera')])
    finally:
        _discard_draft(ctrl, draft)
    if data.get('annotation_source'):
        event_file = ctrl.scenario_dir(result['name']) / 'imu_events.jsonl'
        rows = [json.loads(line) for line in event_file.read_text().splitlines() if line.strip()]
        for item in rows:
            item['sensor_id'] = 'video_review_annotation'
            item['details'] = {'source': data['annotation_source'], 'timing_uncertainty_ms': 250}
        event_file.write_text(''.join(json.dumps(item) + '\n' for item in rows), encoding='utf-8')
    data['recording'] = result['name']
    data['signals'] = [s.model_dump() for s in body.signals]
    target = files(ctrl) / 'setup.json'
    tmp = target.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2), encoding='utf-8')
    tmp.replace(target)
    return result
