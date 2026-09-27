// Scripted dashboard shots for the pitch video. Run against the isolated pitch backend:
//   node capture/shots.mjs [shot ...]
// Each shot resets or seeds its own state through the API first, so shots can be
// re-captured individually and in any order.
import path from 'path';
import { api, goto, launch, Shot } from './lib.mjs';

const REPO = path.resolve(new URL('../..', import.meta.url).pathname);
const MISPLACE = 'upload-img-3537-20260927-013513';
const WALK = 'upload-img-3515-20260927-022743';

const pad = (b, px = 24) => ({ x: b.x - px, y: b.y - px, width: b.width + 2 * px, height: b.height + 2 * px });
const union = (...bs) => {
  const x = Math.min(...bs.map((b) => b.x));
  const y = Math.min(...bs.map((b) => b.y));
  return { x, y, width: Math.max(...bs.map((b) => b.x + b.width)) - x, height: Math.max(...bs.map((b) => b.y + b.height)) - y };
};

const card = (page, title) =>
  page.locator('section.card').filter({ has: page.locator('h2.card-title', { hasText: new RegExp(`^${title}$`) }) }).first();
const PLAYER = '.area-player img, .area-player video';

async function freshStock({ reorderAmox = 470 } = {}) {
  await api('POST', '/replay/control', { action: 'pause' }).catch(() => {});
  await api('POST', '/inventory/reset');
  const cat = await api('GET', '/catalog');
  const data = cat.data || cat;
  for (const m of data.medications) if (m.medication_key === 'AMOXICILLIN_500MG') m.reorder_point = reorderAmox;
  await api('PUT', '/catalog', { medications: data.medications, receipts: data.receipts });
}

async function loadRecording(name, ms = 0) {
  await api('POST', `/recordings/${name}/load`);
  await api('POST', '/replay/control', { action: 'pause' });
  await api('POST', '/replay/control', { action: 'seek', media_time_ms: ms });
}

async function playerSource(page, label) {
  await page.getByRole('group', { name: 'What the player shows' }).getByRole('button', { name: label }).click();
}

const shots = {
  // Wide look at the dashboard: stat cards, player with regions, notifications.
  async overview(page) {
    await freshStock();
    await loadRecording(WALK, 1500);
    await goto(page, '/');
    const s = new Shot(page, 'overview');
    await s.start();
    await s.hold(900);
    await s.moveTo(640, 150, 1100);
    await s.hold(600);
    await s.moveTo(1180, 300, 1100);
    await s.hold(900);
    await s.stop();
  },

  // Supplier file -> reviewed lots and expiry -> imported shipment.
  async shipments(page) {
    await freshStock();
    await loadRecording(WALK, 0);
    await goto(page, '/shipments');
    const s = new Shot(page, 'shipments');
    await s.start();
    await s.hold(500);
    const input = page.getByLabel('Shipment file');
    await s.click(input, { after: 200 });
    await input.setInputFiles(path.join(REPO, 'demo/synthetic_shipments/json/SHP-2026-0004_SGDX.json'));
    await s.hold(500);
    await s.click(page.getByRole('button', { name: 'Review file' }), { after: 900 });
    const table = await s.box('.shipment-preview');
    s.zoom(pad(table, 20), 'preview');
    await s.moveTo(table.x + table.width * 0.3, table.y + table.height * 0.55, 1200);
    await s.hold(900);
    await s.moveTo(table.x + table.width * 0.52, table.y + table.height * 0.62, 1000);
    await s.hold(1100);
    s.unzoom();
    await s.hold(400);
    await s.click(page.getByRole('button', { name: 'Import reviewed shipments' }), { after: 900 });
    await s.scroll(420, 1100);
    await s.hold(1300);
    await s.stop();
  },

  // Pooled balances, then the batch list with lots and expiry dates.
  async inventory(page) {
    await freshStock();
    await goto(page, '/inventory');
    const s = new Shot(page, 'inventory');
    await s.start();
    await s.hold(600);
    await s.moveTo(700, 240, 900);
    await s.hold(500);
    await s.click(page.getByRole('button', { name: /Show batches for Ibuprofen/ }), { after: 900 });
    const table = await s.box('.table-expandable');
    s.zoom(pad(table, 16), 'batches');
    await s.moveTo(table.x + table.width * 0.45, table.y + table.height * 0.6, 1300);
    await s.hold(2200);
    s.unzoom();
    await s.hold(900);
    await s.stop();
  },

  // Filling a prescription deducts tablets once, and the pooled balance crossing the
  // reorder point raises a "Running low" suggestion in Notifications.
  async fill(page) {
    await freshStock();
    await loadRecording(WALK, 0);
    await goto(page, '/');
    const s = new Shot(page, 'fill');
    await s.start();
    const rx = card(page, 'Prescriptions');
    await rx.scrollIntoViewIfNeeded();
    await page.evaluate(() => window.scrollTo({ top: 0 }));
    await s.hold(300);
    await s.scroll(520, 1000);
    await s.hold(300);
    const rxBox = await s.box(rx);
    s.zoom(pad(rxBox, 18), 'prescriptions');
    await s.hold(700);
    await s.click(rx.getByRole('button', { name: 'Confirm fill' }).first(), { after: 1300 });
    await s.hold(600);
    s.unzoom();
    await s.scroll(-520, 1000);
    const notes = await s.box(card(page, 'Notifications'));
    s.zoom(pad(notes, 18), 'running-low');
    await s.moveTo(notes.x + notes.width * 0.55, notes.y + Math.min(notes.height * 0.5, 240), 1100);
    await s.hold(2600);
    await s.stop();
  },

  // The real misplacement: CV follows the wrist, the put-down lands on the wrong shelf,
  // and the alert appears in Notifications while the recording plays.
  async misplace(page) {
    await freshStock();
    await loadRecording(MISPLACE, 11000);
    await goto(page, '/');
    await playerSource(page, 'Real');
    const s = new Shot(page, 'misplace');
    await s.start();
    const player = await s.box(PLAYER);
    s.zoom(pad(player, 30), 'player');
    await s.hold(400);
    await api('POST', '/replay/control', { action: 'play' });
    await s.moveTo(player.x + player.width * 0.8, player.y + player.height * 0.9, 900);
    await s.hold(6800);
    const notes = await s.box(card(page, 'Notifications'));
    s.zoom(pad({ ...notes, height: Math.min(notes.height, 360) }, 18), 'alert');
    await s.moveTo(notes.x + notes.width * 0.5, notes.y + 110, 1000);
    await s.hold(3200);
    await api('POST', '/replay/control', { action: 'pause' });
    await s.stop();
  },

  // Every recording is kept and can be replayed; the player can show the Unity re-enactment.
  async replay(page) {
    await freshStock();
    await loadRecording(WALK, 0);
    await goto(page, '/recordings');
    const s = new Shot(page, 'replay');
    await s.start();
    await s.hold(700);
    const table = await s.box('.table-expandable');
    await s.moveTo(table.x + table.width * 0.3, table.y + 90, 1000);
    await s.hold(500);
    const row = page.locator('tr', { hasText: 'IMG_3515' }).first();
    await s.click(row.getByRole('button', { name: /Watch|Open/ }), { after: 1400 });
    await page.waitForURL(/\/$/).catch(() => {});
    await s.hold(500);
    const toggle = page.getByRole('group', { name: 'What the player shows' });
    await s.click(toggle.getByRole('button', { name: 'Side by side' }), { after: 400 });
    const player = await s.box(PLAYER);
    s.zoom(pad(player, 20), 'side-by-side');
    await api('POST', '/replay/control', { action: 'play' });
    await s.hold(5200);
    await api('POST', '/replay/control', { action: 'pause' });
    await s.stop();
  },

  // Shelf and counter regions tagged in the 3D room scan.
  async room(page) {
    await goto(page, '/room', 2500);
    const s = new Shot(page, 'room');
    await s.start();
    const canvas = await s.box('canvas');
    const cx = canvas.x + canvas.width * 0.5;
    const cy = canvas.y + canvas.height * 0.5;
    s.zoom(pad(canvas, 10), 'scan');
    await s.moveTo(cx, cy, 700);
    await s.hold(300);
    await page.mouse.down();
    await s.moveTo(cx - 260, cy + 30, 3200);
    await s.moveTo(cx - 120, cy - 20, 1800);
    await page.mouse.up();
    await s.hold(400);
    for (let i = 0; i < 18; i++) { await page.mouse.wheel(0, -18); await s.hold(30); }
    await s.hold(1400);
    await s.stop();
  },

  // Go live: capture stays in browser memory; only clips around band events are saved.
  async live(page) {
    await freshStock();
    await goto(page, '/');
    const s = new Shot(page, 'live');
    await s.start();
    await s.hold(500);
    await s.click(page.getByRole('button', { name: 'Go live' }), { after: 1200 });
    const dialog = await s.box('[role="dialog"]');
    s.zoom(pad(dialog, 20), 'dialog');
    const note = page.locator('[role="dialog"]').getByText(/stays in this browser/);
    const nb = await s.box(note);
    await s.moveTo(nb.x + nb.width * 0.7, nb.y + nb.height + 26, 1100);
    await s.hold(900);
    s.zoom(pad(nb, 60), 'privacy');
    await s.hold(3000);
    await s.stop();
  },

  // YOLO skeleton on the real recording, then the per-signal region evidence.
  async pose(page) {
    await freshStock();
    await loadRecording(MISPLACE, 12500);
    await goto(page, '/');
    await playerSource(page, 'Real');
    const s = new Shot(page, 'pose');
    await s.start();
    const player = await s.box(PLAYER);
    s.zoom(pad(player, -60), 'skeleton');
    await api('POST', '/replay/control', { action: 'play' });
    await s.hold(6500);
    await api('POST', '/replay/control', { action: 'pause' });
    s.unzoom();
    await s.hold(300);
    const log = card(page, 'Signal log');
    await log.scrollIntoViewIfNeeded();
    await page.evaluate(() => window.scrollTo({ top: 0 }));
    await s.moveTo(700, 700, 600);
    await s.scroll(1150, 1400);
    const lb = await s.box(log);
    s.zoom(pad({ ...lb, height: Math.min(lb.height, 520) }, 16), 'signal-log');
    await s.moveTo(lb.x + lb.width * 0.62, lb.y + 200, 1000);
    await s.hold(2600);
    await s.stop();
  },

  // Database-backed history of every change.
  async history(page) {
    await goto(page, '/inventory');
    const s = new Shot(page, 'history');
    await s.start();
    const h = card(page, 'Stock history');
    await h.scrollIntoViewIfNeeded();
    await page.evaluate(() => window.scrollTo({ top: 0 }));
    await s.hold(300);
    const target = await h.boundingBox();
    await s.scroll(Math.max(0, target.y - 90), 1500);
    const hb = await s.box(h);
    s.zoom(pad({ ...hb, height: Math.min(hb.height, 600) }, 16), 'history');
    await s.moveTo(hb.x + hb.width * 0.4, hb.y + 160, 900);
    await s.hold(2600);
    await s.stop();
  },
};

const wanted = process.argv.slice(2).length ? process.argv.slice(2) : Object.keys(shots);
const { browser, page } = await launch();
try {
  for (const name of wanted) {
    if (!shots[name]) throw new Error(`unknown shot ${name}`);
    await shots[name](page);
  }
} finally {
  await api('POST', '/replay/control', { action: 'pause' }).catch(() => {});
  await browser.close();
}
