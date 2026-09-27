"""Provision supplied media and reviewed event times through the deployed API."""
import json
import subprocess
from pathlib import Path
import httpx
import imageio_ffmpeg

ROOT = Path('/data/workspace/demo')
ROOT.mkdir(parents=True, exist_ok=True)
output = ROOT / 'video.mp4'
if not output.exists():
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-i', '/data/incoming/IMG_3537.MOV',
                    '-an', '-c:v', 'libx264', '-preset', 'fast', '-crf', '19', '-pix_fmt', 'yuv420p',
                    '-movflags', '+faststart', str(output)], check=True)
signals = [{'time_s': t, 'event': e} for t, e in [(14.6,'pickup'), (16.75,'release'),
    (28.7,'pickup'), (31.65,'release'), (37.3,'pickup'), (40.35,'release')]]
with httpx.Client(base_url='http://127.0.0.1:8000', timeout=300) as api:
    if not (ROOT / 'setup.json').exists():
        with Path('/data/incoming/9_27_2026 2.glb').open('rb') as stream:
            room = api.post('/api/rooms', files={'scan': ('9_27_2026 2.glb', stream, 'model/gltf-binary')},
                            data={'name': 'September 27 demo room'}).raise_for_status().json()
        (ROOT / 'setup.json').write_text(json.dumps({'room_id': room['room_id'], 'signals': signals,
            'annotation_source': 'Python frame extraction and visual review of IMG_3537.MOV'}, indent=2))
    response = api.post('/api/demo/prepare', json={'signals': signals})
    response.raise_for_status()
    print(json.dumps(response.json(), indent=2))
