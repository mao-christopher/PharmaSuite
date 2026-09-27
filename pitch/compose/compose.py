"""Composite the pitch video from rendered scenes and framed dashboard captures.

    backend/.venv/bin/python pitch/compose/compose.py [--from T] [--to T] [--stills T,T,...]

Reads edit/timeline.json, build/scenes/<id>.mp4 (+ .json), build/captures/<shot>/meta.json
and edit/vo_words.json. Writes build/video.mp4 (silent; audio.py muxes the mix).
"""
import argparse
import bisect
import json
import math
import subprocess
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
TL = json.loads((ROOT / 'edit/timeline.json').read_text())
FPS, W, H = TL['fps'], TL['width'], TL['height']
XF = TL['crossfade']
FONT = '/System/Library/Fonts/SFNS.ttf'
MONO = '/System/Library/Fonts/SFNSMono.ttf'


def font(size, weight='Regular', path=FONT):
    f = ImageFont.truetype(path, size)
    try:
        f.set_variation_by_name(weight)
    except Exception:
        pass
    return f


def ease(x):
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def ease_out(x):
    x = min(1.0, max(0.0, x))
    return 1 - (1 - x) ** 3


# ---------------------------------------------------------------- scenes
class SceneClip:
    def __init__(self, seg):
        info = json.loads((ROOT / f"build/scenes/{seg['id']}.json").read_text())
        self.t0, self.n = info['t0'], info['frames']
        self.path = str(ROOT / f"build/scenes/{seg['id']}.mp4")
        self.cap, self.idx, self.frame = None, -1, None

    def at(self, T):
        i = min(self.n - 1, max(0, round((T - self.t0) * FPS)))
        if self.cap is None or i < self.idx:
            self.cap = cv2.VideoCapture(self.path)
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, i)
            self.idx = i - 1
        while self.idx < i:
            ok, f = self.cap.read()
            if not ok:
                break
            self.frame, self.idx = f, self.idx + 1
        return self.frame

    def close(self):
        if self.cap is not None:
            self.cap.release()
            self.cap = None


# ---------------------------------------------------------------- captures
# Window geometry on the plate: a light browser window, Recordly-style.
CW, CH, BAR = 1536, 864, 38
WX, WY = (W - CW) // 2, 44
RADIUS = 18
ROUTES = {'shipments': '/shipments', 'inventory': '/inventory', 'room': '/room', 'history': '/inventory'}


def rounded_mask(w, h, r, ss=4):
    m = Image.new('L', (w * ss, h * ss), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w * ss - 1, h * ss - 1], r * ss, fill=255)
    return np.asarray(m.resize((w, h), Image.LANCZOS), np.float32) / 255.0


def build_plate():
    plate = cv2.imread(str(ROOT / 'build/scenes/plate.png')).astype(np.float32)
    wh = CH + BAR
    mask = rounded_mask(CW, wh, RADIUS)
    shadow = np.zeros((H, W), np.float32)
    shadow[WY + 26:WY + 26 + wh, WX:WX + CW] = mask
    shadow = cv2.GaussianBlur(shadow, (0, 0), 38) * 0.75
    plate *= (1 - shadow)[..., None]
    # Soft teal rim light around the window.
    rim = np.zeros((H, W), np.float32)
    rim[WY:WY + wh, WX:WX + CW] = mask
    rim = cv2.GaussianBlur(rim, (0, 0), 60) * 0.10
    plate += rim[..., None] * np.array([191, 212, 45], np.float32)
    return np.clip(plate, 0, 255), mask


def title_bar(route):
    img = Image.new('RGB', (CW, BAR), (236, 238, 242))
    d = ImageDraw.Draw(img)
    for i, c in enumerate([(255, 95, 87), (254, 188, 46), (40, 200, 64)]):
        cx = 22 + i * 20
        d.ellipse([cx - 6, BAR / 2 - 6, cx + 6, BAR / 2 + 6], fill=c)
    pw = 420
    x0 = (CW - pw) // 2
    d.rounded_rectangle([x0, 7, x0 + pw, BAR - 7], 7, fill=(222, 225, 231))
    f = font(15, 'Medium')
    text = 'pharmasuite.local' + route
    tw = d.textlength(text, font=f)
    d.text(((CW - tw) / 2, BAR / 2), text, font=f, fill=(90, 98, 112), anchor='lm')
    d.line([0, BAR - 1, CW, BAR - 1], fill=(214, 217, 223))
    return cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)


ARROW = np.array([(0, 0), (0, 17.5), (4.2, 13.6), (7.2, 20.4), (9.8, 19.3), (6.9, 12.7), (12.6, 12.7)], np.float32)


def draw_cursor(img, x, y, scale, press):
    s = 1.55 * scale * (1 - 0.14 * press)
    pts = ARROW * s + np.array([x, y], np.float32)
    shadow = img.copy()
    cv2.fillPoly(shadow, [(pts + [1.5, 3]).astype(np.int32)], (0, 0, 0), cv2.LINE_AA)
    cv2.addWeighted(shadow, 0.35, img, 0.65, 0, img)
    p = (pts * 16).astype(np.int32)
    cv2.fillPoly(img, [p], (20, 20, 20), cv2.LINE_AA, shift=4)
    inner = ARROW * (s * 0.78) + np.array([x + 1.35 * s, y + 3.0 * s], np.float32)
    cv2.fillPoly(img, [(inner * 16).astype(np.int32)], (255, 255, 255), cv2.LINE_AA, shift=4)


class Capture:
    def __init__(self, seg, plate, mask):
        self.seg = seg
        d = ROOT / f"build/captures/{seg['shot']}"
        m = json.loads((d / 'meta.json').read_text())
        t0 = m['t0']
        self.dir = d
        self.vw, self.vh = m['view']['width'], m['view']['height']
        self.frames = [(f['t'] - t0, f['file']) for f in m['frames']]
        self.ft = [f[0] for f in self.frames]
        self.cursor = [(c['t'] - t0, c['x'], c['y']) for c in m['cursor']]
        self.ct = [c[0] for c in self.cursor]
        self.clicks = [c['t'] - t0 for c in m['clicks']]
        self.marks = [(k['t'] - t0, k['label'], k.get('zoom')) for k in m['marks']]
        self.plate, self.mask = plate, mask
        self.bar = title_bar(ROUTES.get(seg['shot'], '/'))
        self.cache = (None, None)
        f = seg.get('from', 0)
        self.u0 = 0.0 if f == 'start' else self.mark_time(f)
        to = seg.get('to')
        dur = seg['end'] - seg['start']
        self.rate = 1.0 if to is None else (self.mark_time(to) - self.u0) / dur

    def mark_time(self, v):
        if isinstance(v, (int, float)):
            return float(v)
        return next(t for t, label, _ in self.marks if label == v)

    def image(self, u):
        i = max(0, bisect.bisect_right(self.ft, u) - 1)
        if self.cache[0] != i:
            self.cache = (i, cv2.imread(str(self.dir / self.frames[i][1])))
        return self.cache[1]

    def view_rect(self, u):
        """Zoom window in CSS px: eased in at a zoom mark, out at the next mark."""
        full = (self.vw / 2, self.vh / 2, float(self.vw))
        best = (0.0, full)
        for j, (t, _, z) in enumerate(self.marks):
            if not z:
                continue
            te = next((m[0] for m in self.marks[j + 1:]), 1e9)
            k = ease((u - t) / 0.9) * (1 - ease((u - te) / 0.9))
            if k <= best[0]:
                continue
            cx, cy = z['x'] + z['width'] / 2, z['y'] + z['height'] / 2
            # Every zoom cue reads as a zoom: between 1.25x and 2x.
            w = max(z['width'] * 0.8, z['height'] * 1.1 * 16 / 9)
            w = min(max(w, self.vw / 2.0), self.vw * 0.8)
            h = w * 9 / 16
            cx = min(max(cx, w / 2), self.vw - w / 2)
            cy = min(max(cy, h / 2), self.vh - h / 2)
            best = (k, (cx, cy, w))
        k, (cx, cy, w) = best
        fw = math.exp((1 - k) * math.log(full[2]) + k * math.log(w))
        # Interpolate the centre so the zoom feels anchored, not sliding.
        a = (full[2] - fw) / max(1e-6, full[2] - w) if w < full[2] else 0
        return full[0] + (cx - full[0]) * a, full[1] + (cy - full[1]) * a, fw

    def cursor_at(self, u):
        if not self.cursor:
            return None
        i = bisect.bisect_right(self.ct, u)
        if i == 0:
            return self.cursor[0][1:]
        if i >= len(self.cursor):
            return self.cursor[-1][1:]
        (ta, xa, ya), (tb, xb, yb) = self.cursor[i - 1], self.cursor[i]
        k = (u - ta) / max(1e-6, tb - ta)
        return xa + (xb - xa) * k, ya + (yb - ya) * k

    def at(self, T):
        u = self.u0 + (T - self.seg['start']) * self.rate
        src = self.image(u)
        sh, sw = src.shape[:2]
        sx = sw / self.vw
        cx, cy, vw = self.view_rect(u)
        vh = vw * 9 / 16
        x0, y0 = (cx - vw / 2), (cy - vh / 2)
        # Crop + resize in one affine warp for sub-pixel smooth zooms.
        s = CW / (vw * sx)
        M = np.array([[s, 0, -x0 * sx * s], [0, s, -y0 * sx * s]], np.float32)
        content = cv2.warpAffine(src, M, (CW, CH), flags=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC,
                                 borderMode=cv2.BORDER_REPLICATE)
        win = np.vstack([self.bar, content]).astype(np.float32)
        out = self.plate.copy()
        region = out[WY:WY + CH + BAR, WX:WX + CW]
        region[:] = region * (1 - self.mask[..., None]) + win * self.mask[..., None]
        out = out.astype(np.uint8)
        # Click ripples and cursor, in window space.
        zoom = self.vw / vw
        c = self.cursor_at(u)
        if c is not None:
            px = WX + (c[0] - x0) / vw * CW
            py = WY + BAR + (c[1] - y0) / vh * CH
            press = 0.0
            for tc in self.clicks:
                dt = u - tc
                if -0.12 < dt < 0.12:
                    press = max(press, 1 - abs(dt) / 0.12)
                if 0 <= dt < 0.55:
                    k = ease_out(dt / 0.55)
                    ov = out.copy()
                    cv2.circle(ov, (int(px), int(py)), int(12 + 34 * k * zoom ** 0.5), (191, 212, 45), 3, cv2.LINE_AA)
                    cv2.circle(ov, (int(px), int(py)), int(8 + 20 * k), (191, 212, 45), -1, cv2.LINE_AA)
                    a = 0.55 * (1 - k)
                    cv2.addWeighted(ov, a, out, 1 - a, 0, out)
            if WX <= px <= WX + CW and WY + BAR <= py <= WY + BAR + CH:
                draw_cursor(out, px, py, zoom ** 0.35, press)
        return out

    def close(self):
        pass


# ---------------------------------------------------------------- captions
FIX_WORD = {'Pharmasweet': 'PharmaSuite', 'Sheppard': 'Shepard', 'covered': 'converted',
            'meta': 'Meta', '-API': 'API', 'evading': 'invading', 'obviously': 'automatically', '5000': '5,000'}


def caption_tokens():
    words = json.loads((ROOT / 'edit/vo_words.json').read_text())['words']
    toks = []
    for i, w in enumerate(words):
        text = w['w']
        core = text.strip('.,?!')
        if core in FIX_WORD:
            text = text.replace(core, FIX_WORD[core])
        # "So the next to our project" -> "So for the next part of our project"
        seq = [x['w'] for x in words[i - 1:i + 4]] if i else []
        if seq[:5] == ['So', 'the', 'next', 'to', 'our']:
            text = 'for the'
        elif [x['w'] for x in words[i - 2:i + 3]][:4] == ['So', 'the', 'next', 'to']:
            text = 'next part'
        elif [x['w'] for x in words[i - 3:i + 2]][:5] == ['So', 'the', 'next', 'to', 'our']:
            text = 'of'
        if toks and text[:1] in '-.%' and text != 'API':
            glue = text.replace('-', '–') if toks[-1]['w'][-1:].isdigit() else text
            toks[-1] = {**toks[-1], 'w': toks[-1]['w'] + glue, 'e': w['e']}
            continue
        toks.append({'w': text, 's': w['s'], 'e': w['e']})
    return toks


def build_phrases(max_chars=40):
    toks = caption_tokens()
    clauses, cur = [], []
    for t in toks:
        if cur and (t['s'] - cur[-1]['e'] > 0.32 or cur[-1]['w'][-1] in '.,?!;'):
            clauses.append(cur)
            cur = []
        cur.append(t)
    if cur:
        clauses.append(cur)
    # Merge very short clauses into the next when they follow without a pause.
    merged = []
    for c in clauses:
        if merged and len(' '.join(x['w'] for x in merged[-1])) < 14 and c[0]['s'] - merged[-1][-1]['e'] < 0.32 \
                and not merged[-1][-1]['w'].endswith('.'):
            merged[-1] = merged[-1] + c
        else:
            merged.append(c)
    # Split long clauses into balanced lines.
    phrases = []
    for c in merged:
        text = ' '.join(x['w'] for x in c)
        k = math.ceil(len(text) / max_chars)
        if k <= 1:
            phrases.append(c)
            continue
        target, acc, start = len(text) / k, 0, 0
        for j, t in enumerate(c):
            acc += len(t['w']) + 1
            if j < len(c) - 1 and acc >= target:
                phrases.append(c[start:j + 1])
                start, acc = j + 1, 0
        if start < len(c):
            phrases.append(c[start:])
    out = []
    for i, p in enumerate(phrases):
        s, e = p[0]['s'] - 0.08, p[-1]['e'] + 0.35
        join = False
        if i + 1 < len(phrases):
            nxt = phrases[i + 1][0]['s'] - 0.08
            # Back-to-back lines swap in place instead of fading out and in.
            join = nxt - p[-1]['e'] < 0.45
            e = nxt if join else min(e, nxt)
        out.append({'s': s, 'e': e, 'words': p, 'join_next': join, 'join_prev': bool(out and out[-1]['join_next'])})
    return out


def seg_at(T):
    for s in TL['segments']:
        if s['start'] <= T < s['end']:
            return s
    return TL['segments'][-1]


class Captions:
    def __init__(self):
        self.phrases = [p for p in build_phrases()
                        if seg_at((p['words'][0]['s'] + p['words'][-1]['e']) / 2).get('captions', True)]
        self.font = font(33, 'Semibold')
        self.cache = {}

    def render(self, pi, n):
        key = (pi, n)
        if key in self.cache:
            return self.cache[key]
        words = [w['w'] for w in self.phrases[pi]['words']]
        d = ImageDraw.Draw(Image.new('L', (1, 1)))
        space = d.textlength(' ', font=self.font)
        widths = [d.textlength(w, font=self.font) for w in words]
        tw = sum(widths) + space * (len(words) - 1)
        pad_x, h = 26, 58
        img = Image.new('RGBA', (int(tw + pad_x * 2), h), (0, 0, 0, 0))
        dr = ImageDraw.Draw(img)
        dr.rounded_rectangle([0, 0, img.width - 1, h - 1], 16, fill=(7, 11, 20, 200), outline=(255, 255, 255, 26))
        x = pad_x
        for i, (w, ww) in enumerate(zip(words, widths)):
            col = (240, 245, 252, 255) if i < n else (240, 245, 252, 120)
            dr.text((x, h / 2 + 1), w, font=self.font, fill=col, anchor='lm')
            x += ww + space
        arr = np.asarray(img).astype(np.float32)
        self.cache[key] = arr
        return arr

    def apply(self, frame, T):
        for pi, p in enumerate(self.phrases):
            if p['s'] <= T < p['e']:
                n = sum(1 for w in p['words'] if w['s'] <= T + 0.04)
                a_in = 1.0 if p['join_prev'] else ease((T - p['s']) / 0.12)
                a_out = 1.0 if p['join_next'] else ease((p['e'] - T) / 0.12)
                a = min(a_in, a_out)
                img = self.render(pi, max(1, n))
                h, w = img.shape[:2]
                cy = seg_at(T).get('captionY', 1016)
                x0, y0 = (W - w) // 2, cy - h // 2 + int(8 * (1 - a))
                alpha = img[..., 3:4] / 255.0 * a
                rgb = img[..., 2::-1]
                roi = frame[y0:y0 + h, x0:x0 + w].astype(np.float32)
                frame[y0:y0 + h, x0:x0 + w] = (roi * (1 - alpha) + rgb * alpha).astype(np.uint8)
                break
        return frame


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--from', dest='t_from', type=float, default=0.0)
    ap.add_argument('--to', dest='t_to', type=float, default=TL['duration'])
    ap.add_argument('--stills', default='')
    ap.add_argument('--out', default=str(ROOT / 'build/video.mp4'))
    args = ap.parse_args()

    plate, mask = build_plate()
    segs = TL['segments']
    sources = {}

    def source(seg):
        if seg['id'] not in sources:
            sources[seg['id']] = SceneClip(seg) if seg['kind'] == 'scene' else Capture(seg, plate, mask)
        return sources[seg['id']]

    captions = Captions()

    def frame_at(T):
        i = next((k for k, s in enumerate(segs) if s['start'] <= T < s['end']), len(segs) - 1)
        seg = segs[i]
        f = source(seg).at(T).copy()
        # Crossfade centred on each boundary.
        if i > 0 and T < seg['start'] + XF / 2:
            a = ease((T - (seg['start'] - XF / 2)) / XF)
            g = source(segs[i - 1]).at(T)
            f = cv2.addWeighted(g, 1 - a, f, a, 0)
        elif i + 1 < len(segs) and T >= seg['end'] - XF / 2:
            a = ease((T - (seg['end'] - XF / 2)) / XF)
            g = source(segs[i + 1]).at(T)
            f = cv2.addWeighted(f, 1 - a, g, a, 0)
        # Release readers for segments well behind the playhead.
        for k, s in enumerate(segs):
            if s['end'] + 1 < T and s['id'] in sources:
                sources.pop(s['id']).close()
        f = captions.apply(f, T)
        # Fade from black at the top, to black at the very end.
        fade = min(ease(T / 0.5), ease((TL['duration'] - T) / 1.0))
        if fade < 1:
            f = (f.astype(np.float32) * fade).astype(np.uint8)
        return f

    if args.stills:
        out = ROOT / 'build/stills'
        out.mkdir(parents=True, exist_ok=True)
        for t in [float(x) for x in args.stills.split(',')]:
            cv2.imwrite(str(out / f'still_{t:07.2f}.jpg'), frame_at(t), [cv2.IMWRITE_JPEG_QUALITY, 88])
            print('still', t)
        return

    n0, n1 = round(args.t_from * FPS), round(args.t_to * FPS)
    ff = subprocess.Popen(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'bgr24',
                           '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-', '-c:v', 'libx264', '-preset', 'slow',
                           '-crf', '15', '-pix_fmt', 'yuv420p', '-profile:v', 'high', '-r', str(FPS),
                           '-movflags', '+faststart', args.out], stdin=subprocess.PIPE)
    for n in range(n0, n1):
        ff.stdin.write(frame_at(n / FPS).tobytes())
        if n % 600 == 0:
            print(f'{n / FPS:6.1f}s', flush=True)
    ff.stdin.close()
    ff.wait()
    print('wrote', args.out)


if __name__ == '__main__':
    main()
