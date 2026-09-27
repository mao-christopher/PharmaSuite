// Render code-generated scenes frame by frame at the timeline's fps:
//   node scenes/render.mjs [scene ...]      (default: every scene segment, plus the plate)
// Output: build/scenes/<id>.mp4 covering [start - handle, end + handle], and
// build/scenes/plate.png (the background behind framed dashboard captures).
import { chromium } from 'playwright-core';
import { spawn } from 'child_process';
import fs from 'fs';
import path from 'path';

const ROOT = path.resolve(new URL('..', import.meta.url).pathname);
const timeline = JSON.parse(fs.readFileSync(path.join(ROOT, 'edit/timeline.json'), 'utf8'));
const { words } = JSON.parse(fs.readFileSync(path.join(ROOT, 'edit/vo_words.json'), 'utf8'));
const poses = JSON.parse(fs.readFileSync(path.join(ROOT, 'build/data/scenarios/upload-img-3537-20260927-013513/poses.json'), 'utf8'));
const setupArgs = (seg) => ({ words, scene: seg.id, seg, poses: { fps: poses.fps, frames: poses.frames }, team: timeline.team });
const OUT = path.join(ROOT, 'build/scenes');
fs.mkdirSync(OUT, { recursive: true });

const scenes = timeline.segments.filter((s) => s.kind === 'scene');
const args = process.argv.slice(2);
const previewMode = args.includes('--preview');
const wanted = args.filter((a) => !a.startsWith('--'));
const todo = wanted.length ? scenes.filter((s) => wanted.includes(s.id)) : scenes;

const browser = await chromium.launch({ channel: 'chrome', headless: true, args: ['--allow-file-access-from-files'] });
const page = await browser.newPage({ viewport: { width: timeline.width, height: timeline.height }, deviceScaleFactor: 1 });
await page.goto('file://' + path.join(ROOT, 'scenes/scenes.html'));
await page.evaluate(() => document.fonts.ready);

if (!wanted.length || wanted.includes('plate')) {
  await page.evaluate((a) => window.setup(a), { words, scene: 'plate', seg: {} });
  await page.screenshot({ path: path.join(OUT, 'plate.png') });
  console.log('plate.png');
}

page.on('pageerror', (e) => console.error('page error:', e.message));
if (previewMode) {
  // Stills at 30/60/90% of each scene for a quick look.
  fs.mkdirSync(path.join(ROOT, 'build/shots'), { recursive: true });
  for (const seg of todo) {
    await page.evaluate((a) => window.setup(a), setupArgs(seg));
    for (const f of [0.3, 0.6, 0.9]) {
      await page.evaluate(async (T) => { await window.renderAt(T); }, seg.start + (seg.end - seg.start) * f);
      await page.screenshot({ path: path.join(ROOT, `build/shots/scene_${seg.id}_${Math.round(f * 100)}.jpg`), type: 'jpeg', quality: 70 });
    }
  }
  await browser.close();
  process.exit(0);
}
for (const seg of todo) {
  const t0 = seg.start - timeline.handle;
  const t1 = seg.end + timeline.handle;
  const n = Math.round((t1 - t0) * timeline.fps);
  await page.evaluate((a) => window.setup(a), setupArgs(seg));
  const file = path.join(OUT, `${seg.id}.mp4`);
  const ff = spawn('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', String(timeline.fps), '-i', '-',
    '-c:v', 'libx264', '-preset', 'medium', '-crf', '12', '-pix_fmt', 'yuv420p', '-r', String(timeline.fps), file], { stdio: ['pipe', 'inherit', 'inherit'] });
  const started = Date.now();
  for (let i = 0; i < n; i++) {
    await page.evaluate(async (T) => { await window.renderAt(T); }, t0 + i / timeline.fps);
    const buf = await page.screenshot({ type: 'jpeg', quality: 94 });
    if (!ff.stdin.write(buf)) await new Promise((r) => ff.stdin.once('drain', r));
  }
  ff.stdin.end();
  await new Promise((r) => ff.on('close', r));
  fs.writeFileSync(path.join(OUT, `${seg.id}.json`), JSON.stringify({ id: seg.id, t0, fps: timeline.fps, frames: n }));
  console.log(`${seg.id}: ${n} frames in ${((Date.now() - started) / 1000).toFixed(0)}s`);
}
await browser.close();
