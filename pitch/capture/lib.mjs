// Headless dashboard capture: screencast frames plus a logged cursor path and zoom cues.
// The compositor draws the cursor, click ripples and zooms afterwards, so the page itself
// only needs to be driven; nothing here changes how the dashboard behaves.
import { chromium } from 'playwright-core';
import fs from 'fs';
import path from 'path';

export const BASE = process.env.PITCH_DASHBOARD || 'http://localhost:3012';
export const API = process.env.PITCH_API || 'http://localhost:8012/api';
export const VIEW = { width: 1440, height: 810 };
const OUT = path.resolve(new URL('../build/captures', import.meta.url).pathname);

export async function api(method, route, body) {
  const res = await fetch(API + route, {
    method,
    headers: body ? { 'content-type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`${method} ${route} -> ${res.status} ${text.slice(0, 200)}`);
  return text ? JSON.parse(text) : null;
}

export async function launch() {
  // Forcing the scale factor (instead of emulating it) makes screencast frames 2x, which
  // keeps zoomed-in shots sharp. The window height includes headless chrome's frame.
  const browser = await chromium.launch({
    channel: 'chrome',
    headless: true,
    args: [
      '--force-device-scale-factor=2',
      `--window-size=${VIEW.width},${VIEW.height + 87}`,
      '--autoplay-policy=no-user-gesture-required',
      '--use-fake-ui-for-media-stream',
      '--use-fake-device-for-media-stream',
      '--hide-scrollbars',
    ],
  });
  const context = await browser.newContext({ viewport: null });
  const page = await context.newPage();
  return { browser, context, page };
}

const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

export class Shot {
  constructor(page, name) {
    this.page = page;
    this.name = name;
    this.dir = path.join(OUT, name);
    this.frames = [];
    this.cursor = [];
    this.clicks = [];
    this.marks = [];
    this.pos = { x: VIEW.width * 0.62, y: VIEW.height * 0.7 };
  }

  now() {
    return Date.now() / 1000;
  }

  async start() {
    fs.rmSync(this.dir, { recursive: true, force: true });
    fs.mkdirSync(path.join(this.dir, 'frames'), { recursive: true });
    const size = await this.page.evaluate(() => [innerWidth, innerHeight, devicePixelRatio]);
    if (size[0] !== VIEW.width || size[1] !== VIEW.height || size[2] !== 2) {
      console.warn(`${this.name}: viewport is ${size.join('x')}, expected ${VIEW.width}x${VIEW.height}@2`);
    }
    this.cdp = await this.page.context().newCDPSession(this.page);
    this.pending = [];
    this.cdp.on('Page.screencastFrame', (f) => {
      const i = this.frames.length + 1;
      const file = `frames/${String(i).padStart(6, '0')}.jpg`;
      this.frames.push({ file, t: f.metadata.timestamp });
      this.pending.push(fs.promises.writeFile(path.join(this.dir, file), Buffer.from(f.data, 'base64')));
      this.cdp.send('Page.screencastFrameAck', { sessionId: f.sessionId }).catch(() => {});
    });
    await this.cdp.send('Page.startScreencast', { format: 'jpeg', quality: 92, maxWidth: 3200, maxHeight: 1800 });
    this.t0 = this.now();
    this.logCursor();
    this.mark('start');
  }

  logCursor() {
    this.cursor.push({ t: this.now(), x: this.pos.x, y: this.pos.y });
  }

  mark(label, data = {}) {
    this.marks.push({ t: this.now(), label, ...data });
  }

  // Zoom cue in page CSS pixels; the compositor eases the camera to this rectangle.
  zoom(rect, label = 'zoom') {
    this.mark(label, { zoom: rect });
  }

  unzoom() {
    this.mark('unzoom', { zoom: null });
  }

  async hold(ms) {
    const end = Date.now() + ms;
    while (Date.now() < end) {
      await this.page.waitForTimeout(Math.min(50, end - Date.now()));
      this.logCursor();
    }
  }

  async moveTo(x, y, ms = 650) {
    const from = { ...this.pos };
    const start = Date.now();
    // A slight arc reads as a hand-driven mouse rather than a straight robotic line.
    const bend = Math.min(60, Math.hypot(x - from.x, y - from.y) * 0.12);
    for (;;) {
      const k = Math.min(1, (Date.now() - start) / ms);
      const e = ease(k);
      const arc = Math.sin(Math.PI * e) * bend;
      this.pos = { x: from.x + (x - from.x) * e, y: from.y + (y - from.y) * e - arc };
      await this.page.mouse.move(this.pos.x, this.pos.y);
      this.logCursor();
      if (k >= 1) break;
      await this.page.waitForTimeout(12);
    }
  }

  async box(target) {
    const loc = typeof target === 'string' ? this.page.locator(target).first() : target;
    await loc.scrollIntoViewIfNeeded().catch(() => {});
    const b = await loc.boundingBox();
    if (!b) throw new Error(`${this.name}: no box for ${target}`);
    return b;
  }

  async moveToEl(target, ms) {
    const b = await this.box(target);
    await this.moveTo(b.x + b.width / 2, b.y + b.height / 2, ms);
    return b;
  }

  async click(target, { ms = 650, after = 350 } = {}) {
    const b = target ? await this.moveToEl(target, ms) : null;
    await this.hold(120);
    this.clicks.push({ t: this.now(), x: this.pos.x, y: this.pos.y });
    await this.page.mouse.down();
    await this.page.waitForTimeout(70);
    await this.page.mouse.up();
    this.logCursor();
    await this.hold(after);
    return b;
  }

  // Smooth wheel scrolling; the page's own scroll produces the in-between frames.
  async scroll(dy, ms = 900) {
    const steps = Math.max(1, Math.round(ms / 16));
    let done = 0;
    for (let i = 1; i <= steps; i++) {
      const target = Math.round(dy * ease(i / steps));
      await this.page.mouse.wheel(0, target - done);
      done = target;
      await this.page.waitForTimeout(16);
      this.logCursor();
    }
  }

  async stop() {
    this.mark('end');
    await this.hold(250);
    await this.cdp.send('Page.stopScreencast');
    await Promise.all(this.pending);
    const meta = {
      name: this.name,
      view: VIEW,
      scale: 2,
      t0: this.t0,
      frames: this.frames,
      cursor: this.cursor,
      clicks: this.clicks,
      marks: this.marks,
    };
    fs.writeFileSync(path.join(this.dir, 'meta.json'), JSON.stringify(meta));
    const span = this.frames.at(-1).t - this.frames[0].t;
    console.log(`${this.name}: ${this.frames.length} frames over ${span.toFixed(1)}s (${(this.frames.length / span).toFixed(1)} fps)`);
  }
}

export async function goto(page, route, settle = 1500) {
  await page.goto(BASE + route, { waitUntil: 'networkidle' });
  await page.waitForTimeout(settle);
}
