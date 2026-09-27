"""Mix narration memos with the synthesized bed and mux onto the composited video.

    backend/.venv/bin/python pitch/compose/audio.py   ->  build/audio/mix.wav, build/pharmasuite_pitch.mp4
"""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TL = json.loads((ROOT / 'edit/timeline.json').read_text())
MEMOS = json.loads((ROOT / 'edit/vo_words.json').read_text())['memos']
AUDIO = ROOT / 'build/audio'
DUR = TL['duration']

inputs, chains, labels = [], [], []
for i, m in enumerate(MEMOS):
    inputs += ['-i', str(ROOT.parent / 'video' / m['file'])]
    ms = int(m['at'] * 1000)
    chains.append(
        f"[{i}:a]aresample=48000,aformat=channel_layouts=mono,highpass=f=85,lowpass=f=13500,"
        f"afftdn=nf=-30,equalizer=f=220:t=q:w=1.2:g=-2,equalizer=f=3500:t=q:w=1.5:g=2.5,"
        f"acompressor=threshold=-22dB:ratio=3:attack=6:release=140:makeup=3,"
        f"loudnorm=I=-17:TP=-2:LRA=6,aformat=channel_layouts=stereo,adelay={ms}|{ms}[v{i}]")
    labels.append(f'[v{i}]')
k = len(MEMOS)
inputs += ['-i', str(AUDIO / 'music.wav')]
graph = ';'.join(chains) + ';' + \
    f"{''.join(labels)}amix=inputs={k}:normalize=0:duration=longest,apad=whole_dur={DUR}[vo];" \
    f"[vo]asplit[vo1][vo2];" \
    f"[{k}:a]volume=-9dB[mus];" \
    f"[mus][vo2]sidechaincompress=threshold=0.02:ratio=5:attack=40:release=600:makeup=1[duck];" \
    f"[vo1][duck]amix=inputs=2:normalize=0:duration=longest,atrim=0:{DUR}," \
    f"loudnorm=I=-15:TP=-1.5:LRA=9,aresample=48000[out]"
mix = AUDIO / 'mix.wav'
subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', *inputs, '-filter_complex', graph, '-map', '[out]',
                '-ar', '48000', '-c:a', 'pcm_s16le', str(mix)], check=True)
print('wrote', mix)

video = ROOT / 'build/video.mp4'
final = ROOT / 'build/pharmasuite_pitch.mp4'
if video.exists():
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', str(video), '-i', str(mix), '-map', '0:v', '-map', '1:a',
                    '-c:v', 'copy', '-af', 'apad', '-c:a', 'aac', '-b:a', '256k', '-t', str(DUR), '-movflags', '+faststart', str(final)],
                   check=True)
    print('wrote', final)
