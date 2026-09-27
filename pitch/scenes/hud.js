// Footage scenes: real clips (IMG_3537, wristband clips, Edge Impulse recordings, Unity renders)
// with a PharmaSuite HUD drawn on top. The skeleton is the real YOLO11 pose track saved for
// IMG_3537; shelf regions, event cards, IMU traces and database panels are presentation.

// ---------- frame sources (extracted JPEG sequences under build/frames) ----------
const SRC = {
  '3537': { fps: 59.9595, n: 2667 },
  '9010': { fps: 29.97, n: 378 },
  '9011': { fps: 30, n: 237 },
  ei_collect: { fps: 60, n: 1157 },
  ei_dsp: { fps: 60, n: 565 },
  sbs: { fps: 59.96, n: 1200, offset: 20 },
  'cam01-front': { fps: 30, n: 480, offset: 11 },
  'cam02-side': { fps: 30, n: 480, offset: 11 },
  'cam03-rear': { fps: 30, n: 480, offset: 11 },
};
function frameURL(src, t) {
  const m = SRC[src];
  const i = clamp(Math.floor((t - (m.offset || 0)) * m.fps) + 1, 1, m.n);
  return `../build/frames/${src}/${String(i).padStart(5, '0')}.jpg`;
}
class Footage {
  constructor(parent, src, style = {}) {
    this.src = src;
    this.img = h('img', { style: '' }, parent);
    Object.assign(this.img.style, { position: 'absolute', left: '0', top: '0', width: '1920px', height: '1080px', objectFit: 'cover', ...style });
  }
  set(t, src = this.src) {
    const u = frameURL(src, t);
    if (u === this.u) return;
    this.u = u;
    this.img.src = u;
    window.PENDING.push(this.img.decode().catch(() => {}));
  }
}

// ---------- IMG_3537 story ----------
const REGIONS = {
  amox: { label: 'Amoxicillin 500mg', color: '#60a5fa', pts: [[945, 490], [1665, 545], [1665, 655], [945, 612]], tag: [945, 490] },
  ibu: { label: 'Ibuprofen 200mg', color: '#2dd4bf', pts: [[945, 618], [1665, 662], [1640, 905], [925, 790]], tag: [925, 790] },
  counter: { label: 'Counter', color: '#f5a524', pts: [[360, 590], [760, 560], [820, 900], [380, 960]], tag: [360, 590] },
};
const EVENTS = [
  { t: 14.4, type: 'pickup', region: 'ibu' },
  { t: 17.2, type: 'putdown', region: 'counter' },
  { t: 28.6, type: 'pickup', region: 'counter' },
  { t: 31.4, type: 'putdown', region: 'amox', wrong: true },
  { t: 37.3, type: 'pickup', region: 'amox' },
  { t: 39.8, type: 'putdown', region: 'ibu', fix: true },
];
function stockAt(s) {
  const st = { ibu: 7, amox: 5, counter: 0, hand: 0, alert: false, resolved: false };
  for (const e of EVENTS) {
    if (s < e.t) break;
    if (e.type === 'pickup') { if (e.region === 'ibu') st.ibu--; if (e.region === 'counter') st.counter--; if (e.region === 'amox') st.alert = st.alert; st.hand = 1; }
    else { st.hand = 0; if (e.region === 'counter') st.counter++; if (e.wrong) st.alert = true; if (e.fix) { st.ibu++; st.alert = false; st.resolved = true; } }
  }
  return st;
}
const fmtClock = (s) => `14:22:${String(Math.floor(s + 7)).padStart(2, '0')}.${Math.floor((s % 1) * 10)}`;

// Pose track, smoothed once with confidence-weighted Gaussian averaging.
let POSE = null;
function poses() {
  if (POSE) return POSE;
  const p = window.DATA.poses;
  const n = p.frames.length, K = 17, R = 4;
  const out = new Array(n);
  for (let i = 0; i < n; i++) {
    out[i] = [];
    for (let k = 0; k < K; k++) {
      let sx = 0, sy = 0, sw = 0, sc = 0, cn = 0;
      for (let j = Math.max(0, i - R); j <= Math.min(n - 1, i + R); j++) {
        const f = p.frames[j];
        if (!f || !f[k]) continue;
        const [x, y, c] = f[k];
        const w = c * Math.exp(-((j - i) ** 2) / 8);
        sx += x * w; sy += y * w; sw += w; sc += c; cn++;
      }
      out[i][k] = sw > 0 ? [sx / sw * 1920, sy / sw * 1080, sc / cn] : null;
    }
  }
  POSE = { fps: p.fps, f: out };
  return POSE;
}
function poseAt(s) {
  const P = poses();
  return P.f[clamp(Math.round(s * P.fps), 0, P.f.length - 1)];
}
function wristAt(s, spread = 0) {
  if (!spread) { const k = poseAt(s); return k && k[10] ? k[10] : null; }
  let x = 0, y = 0, n = 0;
  for (let d = -spread; d <= spread; d += 0.1) { const w = wristAt(s + d); if (w && w[2] > 0.3) { x += w[0]; y += w[1]; n++; } }
  return n ? [x / n, y / n] : [960, 600];
}
const EDGES = [[5, 7], [7, 9], [6, 8], [8, 10], [5, 6], [5, 11], [6, 12], [11, 12], [11, 13], [13, 15], [12, 14], [14, 16]];

// Signal-shaped IMU traces: quiet noise plus a burst around each band event.
function imuValue(s, ch, events) {
  let v = noise(s * 2.4, ch * 1.7) * 0.12;
  for (const e of events) {
    const g = gesture(s, e.t - 0.25, 0.5);
    v += g * (e.type === 'pickup' ? 1 : -0.85) * Math.sin((s - e.t) * (8 + ch) + ch * 1.3);
  }
  return v;
}

// ---------- HUD ----------
class Hud {
  constructor(root, o) {
    this.o = o;
    this.cam = div('abs', { left: '0', top: '0', width: '1920px', height: '1080px', transformOrigin: '0 0' }, root);
    this.video = new Footage(this.cam, '3537');
    this.video.img.style.filter = 'contrast(1.06) saturate(0.92) brightness(0.9)';
    this.svg = h('svg', { width: 1920, height: 1080, style: 'position:absolute;left:0;top:0' }, this.cam);
    const defs = h('defs', {}, this.svg);
    defs.innerHTML = `<filter id="glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="5" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>`;
    this.regions = {};
    for (const [id, r] of Object.entries(REGIONS)) {
      const g = h('g', {}, this.svg);
      const poly = h('polygon', { points: r.pts.map((p) => p.join(',')).join(' '), fill: r.color, 'fill-opacity': 0.1, stroke: r.color, 'stroke-width': 3, 'stroke-linejoin': 'round' }, g);
      const len = r.pts.reduce((a, p, i) => a + Math.hypot(p[0] - r.pts[(i + 1) % 4][0], p[1] - r.pts[(i + 1) % 4][1]), 0);
      poly.style.strokeDasharray = `${len}`;
      const tag = div('abs', { left: `${r.tag[0]}px`, top: `${r.tag[1] - 40}px`, padding: '7px 14px', background: r.color, color: '#051018', font: '700 21px var(--sans)', borderRadius: '8px 8px 8px 0', whiteSpace: 'nowrap' }, this.cam, r.label);
      this.regions[id] = { g, poly, tag, len, r };
    }
    this.trail = h('polyline', { fill: 'none', stroke: '#e0fffa', 'stroke-width': 4, 'stroke-linecap': 'round', opacity: 0.5 }, this.svg);
    this.bones = EDGES.map(() => h('line', { stroke: '#5eead4', 'stroke-width': 5, 'stroke-linecap': 'round', filter: 'url(#glow)' }, this.svg));
    this.joints = Array.from({ length: 17 }, (_, k) => h('circle', { r: k === 9 || k === 10 ? 9 : 5.5, fill: '#fff', stroke: '#0f766e', 'stroke-width': 2 }, this.svg));
    this.head = h('circle', { r: 26, fill: 'none', stroke: '#5eead4', 'stroke-width': 4, filter: 'url(#glow)' }, this.svg);
    this.ring = h('circle', { r: 22, fill: 'none', stroke: '#fff', 'stroke-width': 3 }, this.svg);
    this.assoc = h('line', { stroke: '#fff', 'stroke-width': 2.5, 'stroke-dasharray': '8 8' }, this.svg);
    this.wristTag = div('chip abs', { font: '600 19px var(--sans)', padding: '7px 13px' }, this.cam, '');

    this.vignette = div('layer', { background: 'radial-gradient(ellipse at 50% 50%, transparent 55%, rgba(3,6,12,0.55) 100%)' }, root);
    // Screen-space chrome.
    const top = div('abs', { left: '40px', top: '32px', right: '40px', height: '56px', display: 'flex', alignItems: 'center', gap: '18px' }, root);
    this.top = top;
    const brand = div('chip', { padding: '8px 16px 8px 8px', gap: '12px', background: 'rgba(6,10,19,0.72)', backdropFilter: 'blur(10px)' }, top);
    logoMark(34, brand);
    brand.appendChild(h('span', { style: 'font:700 22px var(--sans)' }, null, 'PharmaSuite'));
    div('chip', { background: 'rgba(6,10,19,0.72)', color: 'var(--muted)', font: '500 20px var(--mono)' }, top, 'CAM 01 · BENCH');
    div('', { flex: '1' }, top);
    this.live = div('chip', { background: 'rgba(6,10,19,0.72)', font: '600 20px var(--mono)' }, top, '');
    this.clock = div('chip', { background: 'rgba(6,10,19,0.72)', font: '500 20px var(--mono)', color: 'var(--muted)' }, top, '');

    this.toasts = EVENTS.map((e) => {
      const r = REGIONS[e.region];
      const color = e.wrong ? '#f43f5e' : e.type === 'pickup' ? '#2dd4bf' : '#f5a524';
      const title = e.type === 'pickup' ? 'PICK UP' : 'PUT DOWN';
      const where = e.type === 'pickup' ? `from ${r.label}${e.region === 'counter' ? '' : ' shelf'}` : e.region === 'counter' ? 'at the Counter' : `on ${r.label} shelf`;
      const c = div('card abs', { left: '1430px', width: '450px', height: '136px', padding: '18px 22px', display: 'flex', gap: '16px', alignItems: 'center', background: 'rgba(8,13,24,0.82)', backdropFilter: 'blur(14px)', borderColor: `${color}66` }, root);
      const b = div('center', { width: '54px', height: '54px', borderRadius: '14px', background: `${color}22`, border: `1px solid ${color}88`, flex: 'none' }, c);
      icon('band', 32, color, b);
      div('', { flex: '1' }, c, `<div style="display:flex;justify-content:space-between;font:800 21px var(--sans);letter-spacing:.06em;color:${color}">${title}<span class="mono" style="font:500 17px var(--mono);color:var(--dim);letter-spacing:0">${fmtClock(e.t)}</span></div>
        <div style="font:500 21px var(--sans);margin-top:4px">Ibuprofen 200mg <span style="color:var(--muted)">${where}</span></div>
        <div class="mono" style="font:500 16px var(--mono);color:var(--dim);margin-top:6px">IMU ${(0.93 + ((e.t * 7) % 5) / 100).toFixed(2)} · CV ${(0.9 + ((e.t * 3) % 8) / 100).toFixed(2)}</div>`);
      return { c, e };
    });

    const stat = div('card abs', { left: '40px', top: '888px', width: '690px', height: '150px', padding: '20px 26px', background: 'rgba(8,13,24,0.8)', backdropFilter: 'blur(14px)' }, root);
    div('', { font: '600 16px var(--mono)', color: 'var(--muted)', letterSpacing: '.08em' }, stat, 'INVENTORY · LIVE');
    const row = div('', { display: 'flex', gap: '30px', marginTop: '14px' }, stat);
    this.nums = {};
    [['ibu', 'Ibuprofen shelf', '#2dd4bf'], ['amox', 'Amoxicillin shelf', '#60a5fa'], ['counter', 'Counter', '#f5a524'], ['hand', 'In hand', '#e2e8f0']].forEach(([k, lab, color]) => {
      const cell = div('', {}, row);
      const n = div('', { font: '700 46px/1 var(--sans)', color }, cell, '0');
      div('', { font: '500 17px var(--sans)', color: 'var(--muted)', marginTop: '6px', whiteSpace: 'nowrap' }, cell, lab);
      this.nums[k] = n;
    });
    this.stat = stat;

    const imu = div('card abs', { left: '1430px', top: '888px', width: '450px', height: '150px', padding: '14px 18px', background: 'rgba(8,13,24,0.8)', backdropFilter: 'blur(14px)' }, root);
    div('', { font: '600 16px var(--mono)', color: 'var(--muted)', letterSpacing: '.08em' }, imu, 'WRISTBAND IMU · 50 HZ');
    this.imuSvg = h('svg', { width: 414, height: 100, style: 'position:absolute;left:18px;top:42px' }, imu);
    this.imuPaths = ['#f87171', '#fbbf24', '#34d399'].map((c) => h('path', { stroke: c, 'stroke-width': 2.2, fill: 'none' }, this.imuSvg));
    this.imu = imu;

    const alert = div('card abs', { left: '560px', top: '118px', width: '800px', padding: '24px 30px', display: 'flex', gap: '22px', alignItems: 'center', background: 'rgba(40,8,18,0.86)', border: '2px solid #f43f5e', backdropFilter: 'blur(14px)' }, root);
    div('center', { width: '70px', height: '70px', borderRadius: '50%', background: 'rgba(244,63,94,0.2)', border: '2px solid #f43f5e', font: '800 40px var(--sans)', color: '#fda4af', flex: 'none' }, alert, '!');
    div('', {}, alert, `<div style="font:800 36px var(--sans)">Wrong shelf</div><div style="font:500 23px/1.35 var(--sans);color:#fecdd3;margin-top:4px">Ibuprofen 200mg placed on the Amoxicillin 500mg shelf.<br>It belongs on the Ibuprofen 200mg shelf.</div>`);
    this.alert = alert;
    this.blocked = div('abs center', { left: '760px', top: '300px', width: '400px', height: '64px', gap: '14px', borderRadius: '14px', background: '#f43f5e', color: '#fff', font: '800 26px var(--sans)', letterSpacing: '.04em', boxShadow: '0 20px 60px rgba(244,63,94,0.45)' }, root);
    icon('lock', 30, '#fff', this.blocked);
    this.blocked.appendChild(document.createTextNode('TRANSACTION BLOCKED'));
    this.expired = div('chip abs', { left: '560px', top: '300px', borderColor: 'rgba(245,165,36,0.7)', color: '#fcd34d', background: 'rgba(30,18,4,0.85)' }, root, '⚠ Expired lot LOT-22817 · sale blocked');

    const ok = div('card abs', { left: '560px', top: '118px', width: '800px', padding: '24px 30px', display: 'flex', gap: '22px', alignItems: 'center', background: 'rgba(4,32,26,0.86)', border: '2px solid #34d399', backdropFilter: 'blur(14px)' }, root);
    icon('check', 70, '#34d399', ok);
    div('', {}, ok, `<div style="font:800 36px var(--sans)">Back on the right shelf</div><div style="font:500 23px/1.35 var(--sans);color:#bbf7d0;margin-top:4px">Ibuprofen 200mg returned. Misplacement alert cleared.</div>`);
    this.ok = ok;

    const db = div('card abs', { left: '40px', top: '300px', width: '560px', padding: '20px 24px', background: 'rgba(8,13,24,0.86)', backdropFilter: 'blur(14px)' }, root);
    div('', { display: 'flex', alignItems: 'center', gap: '12px', font: '600 17px var(--mono)', color: '#6ee7b7', letterSpacing: '.06em' }, db, `${ICON.db.replace('<svg', '<svg width="24" height="24"')} MONGODB · pharma.events`);
    this.dbBody = div('mono', { font: '500 18px/1.55 var(--mono)', color: '#cbd5e1', marginTop: '12px', whiteSpace: 'pre' }, db, '');
    this.db = db;
    for (const el of [this.alert, this.blocked, this.expired, this.ok, this.db, this.stat, this.imu, this.top, ...this.toasts.map((t) => t.c)]) el.style.opacity = 0;
  }

  // s = source time in IMG_3537, T = global video time.
  update(T, s) {
    const o = this.o;
    this.video.set(s);
    const z = o.zoom ? o.zoom(T, s) : { s: 1, x: 0, y: 0 };
    this.cam.style.transform = `translate(${-z.x}px, ${-z.y}px) scale(${z.s})`;
    const st = stockAt(s);

    // Regions draw in, then highlight when the wrist is inside around an event.
    const near = EVENTS.find((e) => Math.abs(s - e.t) < 0.9);
    for (const [id, R] of Object.entries(this.regions)) {
      const k = o.regionsAt == null ? 1 : E.inOut(prog(T, o.regionsAt + Object.keys(REGIONS).indexOf(id) * 0.25, 0.9));
      R.poly.style.strokeDashoffset = `${R.len * (1 - k)}`;
      R.tag.style.opacity = E.out(prog(k, 0.6, 0.4));
      R.g.style.opacity = o.regions === false ? 0 : Math.min(1, k * 3);
      R.tag.style.display = o.regions === false ? 'none' : '';
      const active = near && near.region === id ? 1 - Math.abs(s - near.t) / 0.9 : 0;
      const wrong = id === 'amox' && st.alert ? 0.5 + 0.5 * Math.sin(T * 8) : 0;
      R.poly.setAttribute('fill', wrong ? '#f43f5e' : R.r.color);
      R.poly.setAttribute('stroke', wrong ? '#f43f5e' : R.r.color);
      R.poly.setAttribute('fill-opacity', (0.1 + active * 0.22 + wrong * 0.25).toFixed(3));
      R.poly.setAttribute('stroke-width', 3 + active * 3);
      R.tag.style.background = wrong ? '#f43f5e' : R.r.color;
    }

    // Skeleton (real YOLO pose track).
    const kp = poseAt(s);
    const sk = o.skeletonAt == null ? 1 : E.out(prog(T, o.skeletonAt, 0.6));
    const vis = (o.skeleton === false ? 0 : sk) * (o.skeletonGate ? o.skeletonGate(T, s) : 1);
    EDGES.forEach(([a, b], i) => {
      const A = kp && kp[a], B = kp && kp[b];
      const ok = A && B && A[2] > 0.35 && B[2] > 0.35;
      const l = this.bones[i];
      l.style.opacity = ok ? vis : 0;
      if (ok) { l.setAttribute('x1', A[0]); l.setAttribute('y1', A[1]); l.setAttribute('x2', B[0]); l.setAttribute('y2', B[1]); }
    });
    this.joints.forEach((c, k) => {
      const P = kp && kp[k];
      const ok = P && P[2] > 0.35 && k >= 5;
      c.style.opacity = ok ? vis : 0;
      if (ok) { c.setAttribute('cx', P[0]); c.setAttribute('cy', P[1]); }
    });
    const nose = kp && kp[0];
    this.head.style.opacity = nose && nose[2] > 0.3 ? vis : 0;
    if (nose) { this.head.setAttribute('cx', nose[0]); this.head.setAttribute('cy', nose[1]); }
    const w = kp && kp[10];
    const wOk = w && w[2] > 0.35;
    this.ring.style.opacity = wOk ? vis : 0;
    if (wOk) { this.ring.setAttribute('cx', w[0]); this.ring.setAttribute('cy', w[1]); this.ring.setAttribute('r', 20 + 4 * Math.sin(T * 6)); }
    const pts = [];
    for (let d = -0.7; d <= 0; d += 1 / 30) { const q = wristAt(s + d); if (q && q[2] > 0.35) pts.push(`${q[0].toFixed(1)},${q[1].toFixed(1)}`); }
    this.trail.setAttribute('points', pts.join(' '));
    this.trail.style.opacity = o.trail === false ? 0 : 0.55 * vis;

    // Wrist -> region association around each event.
    const a = near && wOk && o.assoc !== false ? (1 - Math.abs(s - near.t) / 0.9) * vis : 0;
    this.assoc.style.opacity = a;
    this.wristTag.style.opacity = a;
    if (near && wOk) {
      const tag = REGIONS[near.region].tag;
      this.assoc.setAttribute('x1', w[0]); this.assoc.setAttribute('y1', w[1]);
      this.assoc.setAttribute('x2', tag[0] + 30); this.assoc.setAttribute('y2', tag[1] - 10);
      this.wristTag.style.left = `${w[0] + 26}px`;
      this.wristTag.style.top = `${w[1] + 18}px`;
      this.wristTag.textContent = `Right wrist → ${REGIONS[near.region].label}`;
    }

    // Chrome.
    const chrome = o.chromeAt == null ? 1 : E.out(prog(T, o.chromeAt, 0.5));
    this.top.style.opacity = chrome;
    const tracking = o.tracking ? o.tracking(T, s) : true;
    this.live.innerHTML = tracking ? '<span style="color:#f43f5e">●</span> TRACKING' : '<span style="color:var(--dim)">●</span> IDLE · PRIVATE';
    this.clock.textContent = fmtClock(s);

    const shownToasts = o.toasts === false ? [] : this.toasts.filter((t) => s >= t.e.t - 0.05 && s < t.e.t + 60).slice(-3);
    this.toasts.forEach((t) => { if (!shownToasts.includes(t)) t.c.style.opacity = 0; });
    shownToasts.slice().reverse().forEach((t, i) => {
      const k = E.out(prog(s, t.e.t - 0.05, 0.35));
      t.c.style.opacity = k * chrome;
      t.c.style.top = `${120 + i * 148}px`;
      t.c.style.transform = `translateX(${(1 - k) * 60}px)`;
    });

    this.stat.style.opacity = (o.status === false ? 0 : 1) * (o.statusAt == null ? 1 : E.out(prog(T, o.statusAt, 0.5)));
    for (const k of Object.keys(this.nums)) this.nums[k].textContent = st[k];

    this.imu.style.opacity = (o.imu === false ? 0 : 1) * (o.imuAt == null ? 1 : E.out(prog(T, o.imuAt, 0.5)));
    this.imuPaths.forEach((p, ch) => {
      let d = '';
      for (let i = 0; i <= 120; i++) {
        const u = s - 4 + (i / 120) * 4;
        d += `${i ? 'L' : 'M'}${(i / 120) * 414} ${(18 + ch * 32 - imuValue(u, ch, EVENTS) * 26).toFixed(1)}`;
      }
      p.setAttribute('d', d);
    });

    const alertK = o.alerts === false ? 0 : st.alert ? E.out(prog(s, 31.4, 0.4)) : 0;
    this.alert.style.opacity = alertK;
    this.alert.style.transform = `translateY(${(1 - alertK) * -20}px) scale(${lerp(0.96, 1, alertK)})`;
    this.blocked.style.opacity = o.blockedAt ? E.out(prog(T, o.blockedAt, 0.3)) * alertK : 0;
    this.blocked.style.transform = `scale(${lerp(0.8, 1, E.back(prog(T, o.blockedAt || 0, 0.4)))})`;
    this.expired.style.opacity = o.expiredAt ? E.out(prog(T, o.expiredAt, 0.3)) * alertK * (1 - E.out(prog(T, (o.blockedAt || 1e9) - 0.2, 0.2))) : 0;
    const okK = o.alerts === false ? 0 : st.resolved ? E.out(prog(s, 39.8, 0.4)) : 0;
    this.ok.style.opacity = okK;

    if (o.db) {
      const lines = o.db(s);
      this.db.style.opacity = lines ? 1 : 0;
      if (lines) this.dbBody.innerHTML = lines;
    }
  }
}

function dbDoc(e, extra) {
  const r = REGIONS[e.region];
  return `<span style="color:#6ee7b7">insertOne</span>({
  event: <span style="color:#fcd34d">"${e.type === 'pickup' ? 'pickup' : 'release'}"</span>,
  medication: <span style="color:#fcd34d">"IBUPROFEN_200MG"</span>,
  region: <span style="color:#fcd34d">"${r.label}"</span>,
  camera: <span style="color:#fcd34d">"cam01"</span>, t: <span style="color:#93c5fd">${e.t.toFixed(1)}</span>
})  <span style="color:#34d399">✓ ${extra}</span>`;
}

function footageScene(opts) {
  return {
    build(root) {
      this.hud = new Hud(root, opts(this.seg));
      this.title = null;
      if (this.seg.title) {
        this.title = div('abs', { left: '40px', top: '112px', font: '700 44px/1.1 var(--sans)', letterSpacing: '-0.02em', textShadow: '0 4px 30px rgba(0,0,0,0.6)' }, root, this.seg.title);
      }
    },
    update(T) {
      const s = this.seg.src + (T - this.seg.start) * (this.seg.speed || 1);
      this.hud.update(T, s);
      if (this.title) reveal(this.title, T, this.seg.titleAt || this.seg.start, { dy: 16 });
    },
  };
}

Object.assign(SCENES, {
  // Walk-in footage, dimmed, under the opening line.
  hook: {
    build(root) {
      this.v = new Footage(root, '3537');
      this.v.img.style.filter = 'grayscale(0.55) brightness(0.42) contrast(1.1) blur(2px)';
      div('layer', { background: 'radial-gradient(ellipse at 50% 50%, rgba(6,10,19,0.35), rgba(6,10,19,0.9))' }, root);
      const box = div('layer center', { flexDirection: 'column', gap: '8px', textAlign: 'center' }, root);
      this.a = sentence(box, 'Every year, thousands of Americans die', 0, { color: '#dfe6f2' }, { 'thousands': '#fff', 'die': '#fb7185' });
      this.b = sentence(box, 'due to pharmaceutical drug mishandling.', 2.5, { color: '#dfe6f2' }, { 'drug': '#fb7185', 'mishandling.': '#fb7185' });
    },
    update(T) {
      this.v.set(this.seg.src + (T - this.seg.start));
      this.v.img.style.transform = `scale(${1.08 - T * 0.008})`;
      this.a.update(T);
      this.b.update(T);
    },
  },

  // The team's wristband in use (IMG_9010, IMG_9011) with its spec and a live detection log.
  device: {
    build(root) {
      background(root, { a: [0.15, 0.3], b: [0.9, 0.85] });
      const frame = div('abs', { left: '150px', top: '70px', width: '528px', height: '940px', borderRadius: '44px', overflow: 'hidden', border: '1px solid var(--line-strong)', boxShadow: '0 40px 100px rgba(0,0,0,0.55)' }, root);
      this.frame = frame;
      this.v1 = new Footage(frame, '9010', { width: '528px', height: '940px' });
      this.v2 = new Footage(frame, '9011', { width: '528px', height: '940px' });
      this.tagLive = div('chip abs', { left: '24px', top: '24px', background: 'rgba(6,10,19,0.7)', font: '600 18px var(--mono)' }, frame, '<span style="color:#f43f5e">●</span> WRISTBAND · LIVE');
      this.cut = 24.9;
      this.eyebrow = { el: div('abs eyebrow', { left: '790px', top: '118px' }, root, 'Built by our team'), t: 18.1 };
      this.title = { el: div('abs title', { left: '790px', top: '160px', width: '1000px' }, root, 'Our own edge compute model'), t: at('developing', 18) };
      const specs = [['ESP32-S3', 'cpu', '#2dd4bf', at('edge', 19.8)], ['MPU6050 · 6-axis IMU', 'band', '#60a5fa', at('model', 20.4)], ['3 × 256 neural net · int8', 'cpu', '#a78bfa', at('integrating', 21)], ['BLE → dashboard', 'screen', '#34d399', at('machine', 22)]];
      this.specs = specs.map(([t, ic, color, time], i) => {
        const c = div('chip abs', { left: `${790 + (i % 2) * 470}px`, top: `${300 + Math.floor(i / 2) * 70}px`, borderColor: `${color}77` }, root);
        icon(ic, 24, color, c);
        c.appendChild(document.createTextNode(t));
        return { c, t: time };
      });
      const scope = div('card abs', { left: '790px', top: '470px', width: '960px', height: '250px', padding: '18px 24px' }, root);
      div('', { font: '600 17px var(--mono)', color: 'var(--muted)', letterSpacing: '.06em' }, scope, 'ACCELEROMETER + GYRO · ON-DEVICE');
      this.scopeSvg = h('svg', { width: 912, height: 190, style: 'position:absolute;left:24px;top:50px' }, scope);
      this.paths = ['#f87171', '#fbbf24', '#34d399', '#60a5fa', '#a78bfa', '#f472b6'].map((c) => h('path', { stroke: c, 'stroke-width': 2.2, fill: 'none' }, this.scopeSvg));
      this.scope = { el: scope, t: at('and', 20.8) };
      // Lift moments in the clips, mapped to video time.
      this.events = [
        { t: 17.9 + (4.2 - 0.3), type: 'pickup' }, { t: 17.9 + (7.0 - 0.3), type: 'putdown' },
        { t: this.cut + 0.9, type: 'putdown' }, { t: this.cut + 3.8, type: 'pickup' }, { t: this.cut + 4.6, type: 'putdown' },
      ];
      const log = div('abs', { left: '790px', top: '750px', width: '960px' }, root);
      this.log = this.events.map((e, i) => {
        const color = e.type === 'pickup' ? '#2dd4bf' : '#f5a524';
        const r = div('', { display: 'flex', alignItems: 'center', gap: '16px', padding: '12px 20px', borderRadius: '12px', background: 'rgba(14,22,38,0.9)', border: `1px solid ${color}55`, marginBottom: '10px', font: '600 22px var(--sans)', position: 'absolute', width: '960px' }, log,
          `<span style="color:${color};letter-spacing:.06em;width:150px">${e.type === 'pickup' ? 'PICK UP' : 'PUT DOWN'}</span><span style="color:var(--muted);flex:1">Detected on the band · sent over BLE</span><span class="mono" style="color:var(--dim);font-size:18px">conf ${(0.94 + (i % 3) / 50).toFixed(2)}</span>`);
        return { r, e };
      });
    },
    update(T) {
      const inFirst = T < this.cut;
      this.v1.img.style.opacity = inFirst ? 1 : 1 - E.out(prog(T, this.cut, 0.3));
      if (T < this.cut + 0.4) this.v1.set(0.3 + (T - 17.9));
      if (T > this.cut - 0.1) this.v2.set(T - this.cut);
      this.v2.img.style.opacity = E.out(prog(T, this.cut, 0.3));
      reveal(this.frame, T, 17.9, { dx: -40, dy: 0, dur: 0.7 });
      reveal(this.eyebrow.el, T, this.eyebrow.t);
      reveal(this.title.el, T, this.title.t - 0.1);
      for (const s of this.specs) reveal(s.c, T, s.t - 0.1, { dy: 14 });
      reveal(this.scope.el, T, this.scope.t - 0.2);
      this.paths.forEach((p, ch) => {
        let d = '';
        for (let i = 0; i <= 160; i++) {
          const u = T - 5 + (i / 160) * 5;
          d += `${i ? 'L' : 'M'}${(i / 160) * 912} ${(16 + ch * 30 - imuValue(u, ch, this.events) * 24).toFixed(1)}`;
        }
        p.setAttribute('d', d);
      });
      const shown = this.log.filter((l) => T >= l.e.t).slice(-3);
      this.log.forEach((l) => { l.r.style.opacity = shown.includes(l) ? 1 : 0; });
      shown.slice().reverse().forEach((l, i) => {
        const k = E.out(prog(T, l.e.t, 0.35));
        l.r.style.top = `${i * 66}px`;
        l.r.style.opacity = k * (1 - i * 0.25);
        l.r.style.transform = `translateY(${(1 - k) * -16}px)`;
      });
    },
  },

  monitor: footageScene(() => ({ regionsAt: 37.1, skeletonAt: 38.9, chromeAt: 36.5, statusAt: 40.2, imuAt: 41.3, toasts: false, alerts: false,
    zoom: (T) => { const k = E.inOut(prog(T, 36.3, 7.9)); const s = lerp(1.12, 1, k); return { s, x: (s - 1) * 960, y: (s - 1) * 540 }; } })),

  transaction: footageScene(() => ({ alerts: false,
    db: (s) => {
      if (s >= 17.2) return dbDoc(EVENTS[1], 'counter +1');
      if (s >= 14.4) return dbDoc(EVENTS[0], 'shelf 7 → 6');
      return null;
    } })),

  misplace: footageScene(() => ({ blockedAt: at('transaction', 72) - 0.1, expiredAt: at('sell', 69) - 0.1,
    zoom: (T, s) => { const k = E.inOut(prog(s, 31.0, 2.0)); const z = lerp(1, 1.16, k); return { s: z, x: (z - 1) * 1250, y: (z - 1) * 600 }; } })),

  privacy: {
    build(root) {
      this.hud = new Hud(root, {
        alerts: false, imu: false, status: false, regionsAt: 109.3 + (14.4 - 9.6),
        tracking: (T, s) => s >= 14.4,
        skeletonGate: (T, s) => E.out(prog(s, 14.4, 0.5)),
      });
      this.blur = div('layer center', { flexDirection: 'column', gap: '18px' }, root);
      const lock = div('center', { width: '110px', height: '110px', borderRadius: '30px', background: 'rgba(6,10,19,0.6)', border: '1px solid var(--line-strong)' }, this.blur);
      icon('eyeoff', 64, '#cbd5e1', lock);
      div('', { font: '700 52px var(--sans)', textShadow: '0 4px 30px rgba(0,0,0,.6)' }, this.blur, 'Not tracked');
      div('', { font: '500 26px var(--sans)', color: '#cbd5e1' }, this.blur, 'Camera buffer stays in browser memory');
      this.band = div('chip abs', { left: '760px', top: '470px', padding: '16px 26px', font: '800 30px var(--sans)', borderColor: '#2dd4bf', color: '#5eead4', background: 'rgba(4,30,28,0.88)', gap: '14px' }, root);
      icon('band', 38, '#5eead4', this.band);
      this.band.appendChild(document.createTextNode('Band event: PICK UP'));
      this.saved = div('card abs', { left: '40px', top: '888px', width: '1840px', height: '150px', padding: '20px 28px', background: 'rgba(8,13,24,0.84)', backdropFilter: 'blur(14px)' }, root);
      div('', { font: '600 16px var(--mono)', color: 'var(--muted)', letterSpacing: '.08em' }, this.saved, 'WHAT GETS SAVED FROM THIS 44 s RECORDING');
      const bar = div('abs', { left: '28px', right: '28px', top: '66px', height: '36px', borderRadius: '8px', background: 'rgba(148,163,184,0.12)', overflow: 'hidden' }, this.saved);
      this.windows = [14.4, 17.2, 28.6, 31.4, 37.3, 39.8].map((t) => div('abs', { left: `${((t - 9) / 44.48) * 100}%`, width: `${(10 / 44.48) * 100}%`, top: '0', bottom: '0', background: 'rgba(45,212,191,0.35)', borderLeft: '2px solid #2dd4bf' }, bar));
      this.head = div('abs', { top: '-6px', bottom: '-6px', width: '3px', background: '#fff' }, bar);
      this.savedLab = div('abs', { left: '28px', top: '112px', font: '500 19px var(--sans)', color: 'var(--muted)' }, this.saved, 'Only the 10 s around each band event is kept. Pose runs only on those clips.');
    },
    update(T) {
      const s = this.seg.src + (T - this.seg.start);
      this.hud.update(T, s);
      const k = E.out(prog(s, 14.4, 0.6));
      this.hud.video.img.style.filter = `blur(${lerp(22, 0, k)}px) grayscale(${lerp(0.7, 0, k)}) brightness(${lerp(0.62, 0.9, k)}) contrast(1.06)`;
      this.blur.style.opacity = 1 - k;
      const b = prog(s, 14.35, 0.35);
      this.band.style.opacity = E.out(b) * (1 - E.out(prog(s, 16.0, 0.4)));
      this.band.style.transform = `scale(${lerp(0.7, 1, E.back(b))})`;
      reveal(this.saved, T, this.seg.start + 0.3, { dy: 20 });
      this.windows.forEach((w, i) => { w.style.opacity = E.out(prog(s, [14.4, 17.2, 28.6, 31.4, 37.3, 39.8][i] - 0.1, 0.4)) || (i === 0 ? 0 : 0); });
      // Once the first event arrives, reveal every window so the whole recording's footprint shows.
      const all = E.out(prog(s, 15.3, 0.8));
      this.windows.forEach((w, i) => { if (i > 0) w.style.opacity = all; });
      this.head.style.left = `${(s / 44.48) * 100}%`;
    },
  },

  // Close-up on the wrist with the band's signal and model output beside it.
  wrist: {
    build(root) {
      this.hud = new Hud(root, {
        alerts: false, status: false, imu: false, toasts: false, assoc: false, regions: false,
        zoom: (T, s) => { const w = wristAt(s, 0.8); const z = 2.1; return { s: z, x: w[0] * z - 520, y: w[1] * z - 560 }; },
      });
      div('layer', { background: 'linear-gradient(90deg, transparent 38%, rgba(6,10,19,0.94) 55%)' }, root);
      this.eyebrow = { el: div('abs eyebrow', { left: '1060px', top: '130px' }, root, 'Custom edge model'), t: 118.0 };
      this.title = { el: div('abs', { left: '1060px', top: '170px', width: '800px', font: '700 54px/1.1 var(--sans)', letterSpacing: '-0.02em' }, root, 'The band feels every pickup and put-down'), t: at('trained', 119) - 0.2 };
      const scope = div('card abs', { left: '1060px', top: '340px', width: '800px', height: '330px', padding: '18px 24px' }, root);
      div('', { font: '600 17px var(--mono)', color: 'var(--muted)', letterSpacing: '.06em' }, scope, 'IMU · 6 AXES · 50 Hz · 6 s WINDOW');
      this.scopeSvg = h('svg', { width: 752, height: 270, style: 'position:absolute;left:24px;top:50px' }, scope);
      this.paths = ['#f87171', '#fbbf24', '#34d399', '#60a5fa', '#a78bfa', '#f472b6'].map((c) => h('path', { stroke: c, 'stroke-width': 2.4, fill: 'none' }, this.scopeSvg));
      this.scope = { el: scope, t: at('using', 121) - 0.3 };
      const out = div('card abs', { left: '1060px', top: '700px', width: '800px', height: '250px', padding: '20px 26px' }, root);
      div('', { font: '600 17px var(--mono)', color: 'var(--muted)', letterSpacing: '.06em' }, out, 'MODEL OUTPUT · ON THE ESP32-S3');
      this.bars = [['idle', '#94a3b8'], ['random', '#a78bfa'], ['put_down', '#f5a524'], ['pick_up', '#2dd4bf']].map(([n, c], i) => {
        const r = div('abs', { left: '26px', right: '26px', top: `${58 + i * 46}px`, height: '34px', display: 'flex', alignItems: 'center', gap: '16px' }, out);
        div('mono', { width: '130px', font: '500 20px var(--mono)', color: '#cbd5e1' }, r, n);
        const track = div('', { flex: '1', height: '14px', borderRadius: '7px', background: 'rgba(148,163,184,0.14)', overflow: 'hidden' }, r);
        const fill = div('', { height: '100%', width: '0%', background: c, borderRadius: '7px' }, track);
        const v = div('mono', { width: '64px', textAlign: 'right', font: '600 20px var(--mono)', color: c }, r, '');
        return { fill, v };
      });
      this.out = { el: out, t: at('identify', 124.5) - 0.4 };
    },
    update(T) {
      const s = this.seg.src + (T - this.seg.start);
      this.hud.update(T, s);
      reveal(this.eyebrow.el, T, this.eyebrow.t);
      reveal(this.title.el, T, this.title.t);
      reveal(this.scope.el, T, this.scope.t, { dx: 30, dy: 0 });
      reveal(this.out.el, T, this.out.t, { dx: 30, dy: 0 });
      this.paths.forEach((p, ch) => {
        let d = '';
        for (let i = 0; i <= 180; i++) {
          const u = s - 6 + (i / 180) * 6;
          d += `${i ? 'L' : 'M'}${(i / 180) * 752} ${(22 + ch * 44 - imuValue(u, ch, EVENTS) * 32).toFixed(1)}`;
        }
        p.setAttribute('d', d);
      });
      // Softmax-like scores: idle by default, the matching class spikes at each event.
      const up = Math.max(...EVENTS.filter((e) => e.type === 'pickup').map((e) => gesture(s, e.t - 0.1, 0.45)));
      const dn = Math.max(...EVENTS.filter((e) => e.type === 'putdown').map((e) => gesture(s, e.t - 0.1, 0.45)));
      const rnd = 0.08 + 0.05 * (0.5 + 0.5 * noise(s * 1.3, 4));
      const raw = [Math.max(0.02, 0.88 - up - dn), rnd * (1 - Math.max(up, dn)), 0.02 + dn * 0.95, 0.02 + up * 0.95];
      const sum = raw.reduce((a, b) => a + b, 0);
      this.bars.forEach((b, i) => { const v = raw[i] / sum; b.fill.style.width = `${(v * 100).toFixed(1)}%`; b.v.textContent = v.toFixed(2); });
    },
  },

  pose: footageScene(() => ({ alerts: true, toasts: true })),

  resolve: footageScene(() => ({
    db: (s) => (s >= 39.8 ? `<span style="color:#6ee7b7">updateOne</span>({ medication: <span style="color:#fcd34d">"IBUPROFEN_200MG"</span> },
  { $inc: { on_shelf: <span style="color:#93c5fd">1</span> } })  <span style="color:#34d399">✓ 6 → 7</span>
<span style="color:#6ee7b7">updateOne</span>({ alert: <span style="color:#fcd34d">"misplacement"</span> },
  { $set: { status: <span style="color:#fcd34d">"resolved"</span> } })  <span style="color:#34d399">✓</span>` : null),
  })),

  // The saved evidence: camera clip beside its Unity re-enactment.
  replay: {
    build(root) {
      background(root, { a: [0.1, 0.1], b: [0.9, 0.9] });
      div('abs eyebrow', { left: '100px', top: '86px' }, root, 'Replay');
      div('abs', { left: '100px', top: '124px', font: '700 50px var(--sans)', letterSpacing: '-0.02em' }, root, 'Every transaction, re-enacted and saved');
      const panes = [['Camera · IMG_3537', '0'], ['Simulation · Unity re-enactment', '-860px']];
      this.imgs = panes.map(([label, pos], i) => {
        const c = div('card abs', { left: `${100 + i * 880}px`, top: '230px', width: '840px', height: '472px', overflow: 'hidden', padding: '0' }, root);
        const img = h('img', {}, c);
        Object.assign(img.style, { position: 'absolute', left: i ? '-840px' : '0', top: '0', width: '1680px', height: '472px' });
        div('chip abs', { left: '18px', top: '18px', background: 'rgba(6,10,19,0.75)', font: '600 18px var(--mono)' }, c, label.toUpperCase());
        return img;
      });
      const bar = div('card abs', { left: '100px', top: '740px', width: '1720px', height: '170px', padding: '22px 30px' }, root);
      div('', { display: 'flex', justifyContent: 'space-between', font: '600 17px var(--mono)', color: 'var(--muted)', letterSpacing: '.06em' }, bar, '<span>TRANSACTION TX-RX-1001 · EVIDENCE</span><span>10 s CLIP · POSE TRACK · 6 SIGNALS · MONGODB</span>');
      const track = div('abs', { left: '30px', right: '30px', top: '84px', height: '8px', borderRadius: '4px', background: 'rgba(148,163,184,0.18)' }, bar);
      this.fill = div('abs', { left: '0', top: '0', bottom: '0', borderRadius: '4px', background: 'linear-gradient(90deg,#2dd4bf,#3b82f6)' }, track);
      EVENTS.forEach((e) => div('abs', { left: `calc(${(e.t / 44.48) * 100}% - 7px)`, top: '-4px', width: '14px', height: '14px', borderRadius: '50%', background: e.wrong ? '#f43f5e' : e.type === 'pickup' ? '#2dd4bf' : '#f5a524' }, track));
      this.time = div('mono abs', { left: '30px', top: '112px', font: '500 20px var(--mono)', color: '#cbd5e1' }, bar, '');
    },
    update(T) {
      const s = this.seg.src + (T - this.seg.start);
      const u = frameURL('sbs', s);
      for (const img of this.imgs) if (img.dataset.u !== u) { img.dataset.u = u; img.src = u; window.PENDING.push(img.decode().catch(() => {})); }
      this.fill.style.width = `${(s / 44.48) * 100}%`;
      this.time.textContent = `${s.toFixed(1)} s / 44.5 s`;
    },
  },

  // Stock crossing its reorder point, then an order going to the supplier.
  reorder: {
    build(root) {
      background(root, { a: [0.1, 0.2], b: [0.9, 0.9] });
      div('abs eyebrow', { left: '120px', top: '110px' }, root, 'Low stock');
      this.title = div('abs title', { left: '120px', top: '150px' }, root, 'Reordering before it runs out');
      const chart = div('card abs', { left: '120px', top: '290px', width: '1060px', height: '640px', padding: '28px 34px' }, root);
      div('', { font: '600 18px var(--mono)', color: 'var(--muted)', letterSpacing: '.06em' }, chart, 'AMOXICILLIN 500mg · TABLETS IN STOCK · LAST 14 DAYS');
      const W = 990, H = 500;
      const svg = h('svg', { width: W, height: H, style: 'position:absolute;left:34px;top:90px' }, chart);
      this.data = [760, 742, 730, 700, 688, 662, 640, 628, 590, 572, 548, 530, 500, 470];
      const y = (v) => H - 30 - ((v - 400) / 400) * (H - 60);
      this.y = y; this.W = W;
      for (const v of [500, 600, 700, 800]) {
        h('line', { x1: 0, x2: W, y1: y(v), y2: y(v), stroke: 'rgba(148,163,184,0.12)' }, svg);
        h('text', { x: W - 4, y: y(v) - 8, fill: '#56647d', 'text-anchor': 'end', style: 'font:500 16px var(--mono)' }, svg).textContent = v;
      }
      h('line', { x1: 0, x2: W, y1: y(480), y2: y(480), stroke: '#f5a524', 'stroke-width': 2.5, 'stroke-dasharray': '10 8' }, svg);
      h('text', { x: 8, y: y(480) + 28, fill: '#f5a524', style: 'font:600 18px var(--sans)' }, svg).textContent = 'Reorder point · 480';
      this.area = h('path', { fill: 'rgba(96,165,250,0.12)' }, svg);
      this.line = h('path', { fill: 'none', stroke: '#60a5fa', 'stroke-width': 4, 'stroke-linejoin': 'round' }, svg);
      this.dot = h('circle', { r: 9, fill: '#f5a524', stroke: '#fff', 'stroke-width': 3 }, svg);
      this.low = div('card abs', { left: '1230px', top: '290px', width: '570px', padding: '24px 28px', border: '1px solid rgba(245,165,36,0.6)' }, root,
        `<div style="font:800 20px var(--sans);letter-spacing:.06em;color:#fcd34d">RUNNING LOW</div><div style="font:600 30px var(--sans);margin-top:8px">Amoxicillin 500mg</div><div style="font:400 22px var(--sans);color:var(--muted);margin-top:6px">470 tablets left, below the reorder point.</div>`);
      this.po = div('card abs', { left: '1230px', top: '520px', width: '570px', padding: '24px 28px', border: '1px solid rgba(52,211,153,0.6)' }, root,
        `<div style="display:flex;justify-content:space-between;font:800 20px var(--sans);letter-spacing:.06em;color:#6ee7b7"><span>PURCHASE ORDER SENT</span><span>✓</span></div>
         <div class="mono" style="font:500 19px/1.7 var(--mono);color:#cbd5e1;margin-top:12px">PO-RCP-0142-26140<br>To: Summit Generics Direct<br>Amoxicillin 500mg × 10 bottles<br>Requested delivery: 2 days</div>`);
      this.tLow = at('low', 81);
      this.tAsk = at('automatically', 83);
    },
    update(T) {
      reveal(this.title, T, 80.1);
      const k = E.inOut(prog(T, 80.2, 1.6));
      const n = this.data.length;
      const pts = this.data.map((v, i) => [(i / (n - 1)) * this.W, this.y(v)]);
      const upto = k * (n - 1);
      const vis = [];
      for (let i = 0; i < n; i++) {
        if (i <= upto) vis.push(pts[i]);
        else { const f = upto - (i - 1); const a = pts[i - 1], b = pts[i]; vis.push([lerp(a[0], b[0], f), lerp(a[1], b[1], f)]); break; }
      }
      const d = vis.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join('');
      this.line.setAttribute('d', d);
      const last = vis[vis.length - 1];
      this.area.setAttribute('d', `${d}L${last[0]} 470L0 470Z`);
      this.dot.setAttribute('cx', last[0]); this.dot.setAttribute('cy', last[1]);
      this.dot.setAttribute('fill', k >= 0.97 ? '#f5a524' : '#60a5fa');
      this.dot.setAttribute('r', 9 + (k >= 0.97 ? 3 * Math.sin(T * 8) : 0));
      reveal(this.low, T, this.tLow, { dx: 30, dy: 0 });
      reveal(this.po, T, this.tAsk + 0.3, { dx: 30, dy: 0 });
    },
  },

  // Unity three-camera demo: the view follows whichever camera can see the technician.
  multicam: {
    build(root) {
      background(root, { a: [0.1, 0.1], b: [0.9, 0.9] });
      div('abs eyebrow', { left: '100px', top: '80px' }, root, 'Multi-camera');
      div('abs', { left: '100px', top: '116px', font: '700 48px var(--sans)', letterSpacing: '-0.02em' }, root, 'Tracking follows the technician');
      const main = div('card abs', { left: '100px', top: '210px', width: '1240px', height: '698px', overflow: 'hidden', padding: '0' }, root);
      this.main = new Footage(main, 'cam02-side', { width: '1240px', height: '698px' });
      this.mainTag = div('chip abs', { left: '20px', top: '20px', background: 'rgba(6,10,19,0.75)', font: '600 19px var(--mono)' }, main, '');
      this.cams = [['cam01-front', 'CAM 01 · FRONT'], ['cam02-side', 'CAM 02 · SIDE'], ['cam03-rear', 'CAM 03 · REAR']].map(([src, label], i) => {
        const c = div('card abs', { left: '1380px', top: `${210 + i * 238}px`, width: '420px', height: '222px', overflow: 'hidden', padding: '0' }, root);
        const f = new Footage(c, src, { width: '420px', height: '236px' });
        div('chip abs', { left: '12px', bottom: '12px', background: 'rgba(6,10,19,0.75)', font: '600 15px var(--mono)', padding: '6px 10px' }, c, label);
        const meter = div('abs', { right: '12px', bottom: '14px', font: '600 15px var(--mono)', padding: '6px 10px', borderRadius: '999px', background: 'rgba(6,10,19,0.75)' }, c, '');
        return { c, f, src, label, meter };
      });
      this.handoff = div('chip abs', { left: '460px', top: '930px', font: '600 22px var(--sans)', borderColor: '#2dd4bf', color: '#99f6e4' }, root, '');
    },
    update(T) {
      const s = 12 + (T - this.seg.start) * (14 / (this.seg.end - this.seg.start));
      const active = s < 15.6 ? 1 : s < 25.2 ? 2 : 0;
      const cam = this.cams[active];
      this.main.set(s, cam.src);
      this.mainTag.innerHTML = `<span style="color:#34d399">●</span> ACTIVE · ${cam.label}`;
      this.cams.forEach((c, i) => {
        c.f.set(s);
        const on = i === active;
        c.c.style.borderColor = on ? '#2dd4bf' : 'var(--line)';
        c.c.style.boxShadow = on ? '0 0 0 3px rgba(45,212,191,0.5), 0 30px 80px rgba(0,0,0,.45)' : '';
        c.meter.innerHTML = on ? '<span style="color:#34d399">ARM VISIBLE</span>' : '<span style="color:#56647d">NO VIEW</span>';
      });
      const last = s < 15.6 ? null : s < 25.2 ? 'CAM 02 → CAM 03' : 'CAM 03 → CAM 01';
      this.handoff.style.opacity = last ? 1 : 0;
      this.handoff.textContent = last ? `Handoff ${last} · same session, same event IDs` : '';
    },
  },

  // Real Edge Impulse recordings of the team's data collection, then the model it produced.
  classifier: {
    build(root) {
      background(root, { a: [0.2, 0.2], b: [0.85, 0.85] });
      this.win = div('card abs', { left: '100px', top: '150px', width: '1040px', height: '780px', overflow: 'hidden', padding: '0' }, root);
      const chrome = div('', { height: '44px', display: 'flex', alignItems: 'center', gap: '10px', padding: '0 18px', background: '#0b1322', borderBottom: '1px solid var(--line)' }, this.win);
      ['#f43f5e', '#f5a524', '#34d399'].forEach((c) => div('', { width: '13px', height: '13px', borderRadius: '50%', background: c }, chrome));
      div('mono', { marginLeft: '14px', font: '500 17px var(--mono)', color: 'var(--muted)' }, chrome, 'studio.edgeimpulse.com · PharmaSuite wristband');
      const view = div('abs', { left: '0', top: '44px', width: '1040px', height: '736px', overflow: 'hidden' }, this.win);
      this.ei = new Footage(view, 'ei_collect', { width: '1040px', height: '781px' });
      this.countT = at('collecting', 128.2);
      this.count = div('abs', { left: '1210px', top: '170px', font: '800 170px/1 var(--sans)', letterSpacing: '-0.04em' }, root, '0');
      this.countLab = div('abs', { left: '1220px', top: '355px', width: '600px', font: '500 32px/1.3 var(--sans)', color: 'var(--muted)' }, root, 'data points collected by hand while wearing the band');
      const names = [['Idle', '#94a3b8', at('idle', 132)], ['Random', '#a78bfa', at('random', 133)], ['Put down', '#f5a524', at('putting', 133.5)], ['Pick up', '#2dd4bf', at('picking', 134.5)]];
      this.classes = names.map(([n, c, t], i) => {
        const r = div('chip abs', { left: `${1220 + (i % 2) * 300}px`, top: `${500 + Math.floor(i / 2) * 76}px`, font: '600 26px var(--sans)', borderColor: `${c}88` }, root, `<span style="width:14px;height:14px;border-radius:4px;background:${c}"></span>${n}`);
        return { r, t };
      });
      this.acc = div('abs', { left: '1210px', top: '690px', font: '800 150px/1 var(--sans)', letterSpacing: '-0.04em', background: 'linear-gradient(180deg,#fff,#7ee8d6)', webkitBackgroundClip: 'text', color: 'transparent' }, root, '0%');
      this.accLab = div('abs', { left: '1220px', top: '850px', font: '500 28px var(--sans)', color: 'var(--muted)' }, root, 'accuracy · 3 × 256 net, int8 on ESP32-S3');
      this.accT = at('98', 135.3);
      this.cut = 131.0;
    },
    update(T) {
      reveal(this.win, T, 128.1, { dx: -30, dy: 0 });
      if (T < this.cut) this.ei.set(2.0 + (T - 128.1) * 1.4, 'ei_collect');
      else this.ei.set(0.5 + (T - this.cut) * 1.0, 'ei_dsp');
      const k = E.out(prog(T, this.countT, 1.4));
      this.count.textContent = Math.round(k * 5000).toLocaleString('en-US');
      this.count.style.opacity = E.out(prog(T, this.countT - 0.2, 0.3));
      reveal(this.countLab, T, this.countT + 0.4);
      for (const c of this.classes) reveal(c.r, T, c.t - 0.12, { dy: 14 });
      const a = prog(T, this.accT - 0.2, 1.3);
      this.acc.style.opacity = E.out(prog(T, this.accT - 0.3, 0.3));
      this.acc.textContent = `${(E.out(a) * 98.21).toFixed(2)}%`;
      reveal(this.accLab, T, this.accT + 0.9);
    },
  },
});
