import json
from pharma.api.replay_stream import ReplayController


def test_authored_video_requires_explicit_metadata_and_stays_separate(tmp_path):
    recording = tmp_path / 'recording'
    recording.mkdir()
    meta = recording / 'scenario.json'
    meta.write_text('{}')
    video = recording / 'presentation.mp4'
    video.write_bytes(b'fixture')
    ctrl = ReplayController.__new__(ReplayController)
    ctrl.scenarios_dir = tmp_path
    assert ctrl.presentation_video(recording) is None
    meta.write_text(json.dumps({'presentation_only': True}))
    assert ctrl.sim_video('recording') == video
    assert ctrl.sim_video('recording', 'side_by_side.mp4') is None
    result = ctrl.render_summary(recording)
    assert result['presentation_only'] and not result['can_render']
    assert 'inventory evidence' in result['reason']
    assert not list(recording.glob('renders/**/manifest.json'))
