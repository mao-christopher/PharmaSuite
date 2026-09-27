import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from pharma.api.main import app
from pharma.api.routes import get_controller
from pharma.api import demo_routes


def test_demo_rejects_bad_signals_and_deduplicates(tmp_path, monkeypatch):
    root = tmp_path / 'demo'
    root.mkdir()
    (root / 'video.mp4').write_bytes(b'fixture')
    (root / 'setup.json').write_text(json.dumps({'room_id': 'room', 'annotation_source': 'video review'}))
    ctrl = SimpleNamespace(scenarios_dir=tmp_path / 'scenarios', scenario_dir=lambda n: tmp_path / n)
    app.dependency_overrides[get_controller] = lambda: ctrl
    calls = []

    def finish(*args):
        calls.append(args)
        output = tmp_path / 'new-recording'
        output.mkdir()
        (output / 'imu_events.jsonl').write_text('{"sensor_id":"mock"}\n')
        return {'name': 'new-recording'}

    monkeypatch.setattr(demo_routes, '_stage_videos', lambda *a: tmp_path / 'draft')
    monkeypatch.setattr(demo_routes, '_finish_upload', finish)
    monkeypatch.setattr(demo_routes, '_discard_draft', lambda *a: None)
    try:
        client = TestClient(app)
        for body in ({'signals': []}, {'signals': [{'time_s': -1, 'event': 'pickup'}]},
                     {'signals': [{'time_s': 1, 'event': 'invented'}]}):
            assert client.post('/api/demo/prepare', json=body).status_code == 422
        payload = {'signals': [{'time_s': 2.5, 'event': 'pickup'}]}
        assert client.post('/api/demo/prepare', json=payload).json()['name'] == 'new-recording'
        assert client.post('/api/demo/prepare', json=payload).json()['already_created']
        assert len(calls) == 1
        event = json.loads((tmp_path / 'new-recording/imu_events.jsonl').read_text())
        assert event['sensor_id'] == 'video_review_annotation'
        assert event['details']['timing_uncertainty_ms'] == 250
    finally:
        app.dependency_overrides.pop(get_controller)
