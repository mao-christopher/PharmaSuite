"""Synthesize the music bed in code (no samples): pad, pluck arpeggio, soft pulse, risers.

    backend/.venv/bin/python pitch/compose/music.py   ->  build/audio/music.wav
Section cues come from edit/timeline.json so the score follows the edit.
"""
import json
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
TL = json.loads((ROOT / 'edit/timeline.json').read_text())
SR = 48000
DUR = TL['duration']
N = int(DUR * SR)
CUE = {s['id']: s['start'] for s in TL['segments']}
BPM = 100
BEAT = 60 / BPM
rng = np.random.default_rng(7)
t = np.arange(N) / SR


def hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def fft_filter(x, lo=None, hi=None):
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    g = np.ones_like(f)
    if hi:
        g /= np.sqrt(1 + (f / hi) ** 4)
    if lo:
        g *= 1 / np.sqrt(1 + (lo / np.maximum(f, 1e-3)) ** 4)
    return np.fft.irfft(X * g, len(x))


def smooth_gate(t0, t1, fade):
    a = np.clip((t - t0) / fade, 0, 1)
    b = np.clip((t1 - t) / fade, 0, 1)
    return (0.5 - 0.5 * np.cos(np.pi * a)) * (0.5 - 0.5 * np.cos(np.pi * b))


def ramp(points):
    xs, ys = zip(*points)
    return np.interp(t, xs, ys)


# Fmaj7 - Cadd9 - Am7 - G6 (two bars each), voiced around middle C.
CHORDS = [[53, 57, 60, 64], [48, 55, 62, 64], [45, 52, 60, 64], [43, 50, 59, 64]]
BASS = [41, 36, 45, 43]
CHORD_LEN = 8 * BEAT


def chord_index(tt):
    return int(tt // CHORD_LEN) % len(CHORDS)


# ---------------------------------------------------------------- pad
pad = np.zeros(N)
n_chords = int(DUR // CHORD_LEN) + 2
for k in range(n_chords):
    t0 = k * CHORD_LEN
    env = smooth_gate(t0 - 0.8, t0 + CHORD_LEN + 0.8, 1.6)
    idx = np.nonzero(env)[0]
    if not len(idx):
        continue
    tt = t[idx]
    sig = np.zeros(len(idx))
    for m in CHORDS[k % 4]:
        for det in (-0.07, 0.0, 0.07):
            f0 = hz(m + det)
            ph = rng.uniform(0, 2 * np.pi)
            for h in range(1, 7):
                sig += np.sin(2 * np.pi * f0 * h * tt + ph * h) / (h ** 1.6)
    pad[idx] += sig * env[idx]
pad = fft_filter(pad, lo=90, hi=1400)
pad *= 1 + 0.12 * np.sin(2 * np.pi * 0.11 * t)  # slow breathing

# ---------------------------------------------------------------- bass
bass = np.zeros(N)
for k in range(n_chords):
    t0 = k * CHORD_LEN
    env = smooth_gate(t0 - 0.05, t0 + CHORD_LEN - 0.05, 0.4)
    idx = np.nonzero(env)[0]
    if len(idx):
        f0 = hz(BASS[k % 4] - 12)
        bass[idx] += (np.sin(2 * np.pi * f0 * t[idx]) + 0.25 * np.sin(4 * np.pi * f0 * t[idx])) * env[idx]

# ---------------------------------------------------------------- pluck arpeggio (8ths)
arp = np.zeros(N)
pattern = [0, 2, 1, 3, 2, 1, 3, 2]
step = BEAT / 2
for j in range(int(DUR / step)):
    t0 = j * step
    ch = CHORDS[chord_index(t0)]
    m = ch[pattern[j % 8]] + 12 + (12 if j % 16 == 13 else 0)
    f0 = hz(m)
    i0 = int(t0 * SR)
    L = int(0.9 * SR)
    seg = np.arange(min(L, N - i0)) / SR
    if not len(seg):
        break
    env = np.exp(-seg / 0.22) * np.minimum(1, seg / 0.004)
    note = (np.sin(2 * np.pi * f0 * seg) + 0.35 * np.sin(4 * np.pi * f0 * seg) * np.exp(-seg / 0.08)) * env
    arp[i0:i0 + len(seg)] += note * (0.8 if j % 2 else 1.0)
# Tempo-synced echo.
d = int(BEAT * 0.75 * SR)
echo = np.zeros(N)
for r, g in enumerate([0.38, 0.18, 0.08], start=1):
    echo[d * r:] += arp[:-d * r] * g
arp = fft_filter(arp + echo, lo=250, hi=5200)

# ---------------------------------------------------------------- pulse: soft kick + shaker
kick = np.zeros(N)
hat = np.zeros(N)
kL = int(0.35 * SR)
ks = np.arange(kL) / SR
kick_one = np.sin(2 * np.pi * (45 * ks + (110 - 45) * 0.045 * (1 - np.exp(-ks / 0.045)))) * np.exp(-ks / 0.13)
hL = int(0.05 * SR)
hat_one = rng.standard_normal(hL) * np.exp(-np.arange(hL) / SR / 0.012)
for j in range(int(DUR / BEAT)):
    i0 = int(j * BEAT * SR)
    if i0 + kL < N:
        kick[i0:i0 + kL] += kick_one
    i1 = int((j + 0.5) * BEAT * SR)
    if i1 + hL < N:
        hat[i1:i1 + hL] += hat_one * (1.0 if j % 2 else 0.7)
hat = fft_filter(hat, lo=6000)

# ---------------------------------------------------------------- risers and hits
def riser(t_hit, length):
    env = np.clip((t - (t_hit - length)) / length, 0, 1) ** 2.2 * (t < t_hit)
    noise = fft_filter(rng.standard_normal(N), lo=1500, hi=9000)
    return noise * env


def hit(t_hit):
    s = np.clip(t - t_hit, 0, None) * (t >= t_hit)
    boom = np.sin(2 * np.pi * 50 * s) * np.exp(-s / 0.9) * (t >= t_hit)
    return boom


fx = np.zeros(N)
for cue, length in [('logo', 2.0), ('monitor', 1.2), ('outro', 1.6)]:
    fx += riser(CUE[cue], length) * 0.35 + hit(CUE[cue]) * 0.8

# ---------------------------------------------------------------- arrangement
logo, monitor, outro = CUE['logo'], CUE['monitor'], CUE['outro']
pad_lvl = ramp([(0, 0), (2.5, 0.55), (17.9, 0.6), (logo, 0.85), (monitor, 0.6), (outro, 0.75), (DUR - 4, 0.8), (DUR, 0)])
arp_lvl = ramp([(0, 0), (CUE['device'], 0), (CUE['device'] + 3, 0.5), (logo, 0.65), (monitor, 0.55),
                (outro, 0.7), (DUR - 3, 0.6), (DUR, 0)])
pulse_lvl = ramp([(0, 0), (monitor, 0), (monitor + 0.01, 1), (outro - 0.6, 1), (outro, 0), (outro + 2, 0.8),
                  (DUR - 3.5, 0.8), (DUR - 2.5, 0)])
bass_lvl = ramp([(0, 0.3), (CUE['device'], 0.5), (monitor, 0.7), (DUR - 3, 0.7), (DUR, 0)])


def norm(x):
    return x / (np.max(np.abs(x)) + 1e-9)


mix_l = (norm(pad) * 0.30 * pad_lvl + norm(bass) * 0.22 * bass_lvl + norm(kick) * 0.20 * pulse_lvl
         + norm(fx) * 0.35)
arp_n = norm(arp) * 0.20 * arp_lvl
hat_n = norm(hat) * 0.05 * pulse_lvl
# Gentle stereo: arp and shaker pan in opposite directions.
L = mix_l + arp_n * 0.8 + hat_n * 0.5
R = mix_l + arp_n * 0.5 + hat_n * 0.8

# Cheap reverb: decaying-noise impulse response, FFT convolution.
ir_len = int(2.4 * SR)
ir_t = np.arange(ir_len) / SR
size = 1 << int(np.ceil(np.log2(N + ir_len)))
out = []
for ch, seed in ((L, 1), (R, 2)):
    ir = np.random.default_rng(seed).standard_normal(ir_len) * np.exp(-ir_t / 0.55)
    ir = fft_filter(np.pad(ir, (0, 0)), hi=5000)[:ir_len]
    wet = np.fft.irfft(np.fft.rfft(ch, size) * np.fft.rfft(ir / np.sqrt(np.sum(ir ** 2)), size), size)[:N]
    out.append(ch * 0.8 + wet * 0.22)
stereo = np.stack(out, 1)
stereo /= np.max(np.abs(stereo)) / 0.89

path = ROOT / 'build/audio/music.wav'
path.parent.mkdir(parents=True, exist_ok=True)
with wave.open(str(path), 'wb') as w:
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes((stereo * 32767).astype('<i2').tobytes())
print('wrote', path)
