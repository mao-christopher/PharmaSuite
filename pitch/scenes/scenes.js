// Code-generated scenes for the pitch video. Every scene is a pure function of the global
// video time T (seconds), so the renderer can step frames at exactly 60 fps. Narration word
// times (from the transcribed voice memos) are passed in so type lands on the spoken word.

const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const lerp = (a, b, t) => a + (b - a) * t;
const E = {
  out: (t) => 1 - Math.pow(1 - t, 3),
  out5: (t) => 1 - Math.pow(1 - t, 5),
  inOut: (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2),
  back: (t) => { const c1 = 1.5, c3 = c1 + 1; return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2); },
};
const prog = (T, start, dur = 0.5) => clamp((T - start) / dur);

let WORDS = [];
const norm = (s) => s.toLowerCase().replace(/[^a-z0-9%.]/g, '');
// Global time of the first narration word at/after `from` that starts with `q`.
function at(q, from = 0) {
  const n = norm(q);
  const w = WORDS.find((w) => w.s >= from - 0.05 && norm(w.w).startsWith(n));
  if (!w) throw new Error(`word not found: ${q} after ${from}`);
  return w.s;
}

function h(tag, props = {}, parent, html) {
  const ns = ['svg', 'path', 'circle', 'rect', 'line', 'polyline', 'g', 'ellipse', 'defs', 'linearGradient', 'stop', 'text', 'polygon'];
  const node = ns.includes(tag) ? document.createElementNS('http://www.w3.org/2000/svg', tag) : document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === 'style' && typeof v === 'object') Object.assign(node.style, v);
    else if (k === 'class') node.setAttribute('class', v);
    else node.setAttribute(k, v);
  }
  if (html != null) node.innerHTML = html;
  if (parent) parent.appendChild(node);
  return node;
}
const div = (cls, style, parent, html) => h('div', { class: cls || '', style: style || {} }, parent, html);

// Fade/rise/blur-in used by nearly every element.
function reveal(node, T, start, { dur = 0.55, dy = 26, dx = 0, blur = 10, scale = 1, from = 0.96 } = {}) {
  const k = prog(T, start, dur);
  const e = E.out(k);
  node.style.opacity = e;
  const s = scale === 1 ? lerp(from, 1, e) : lerp(scale, 1, E.back(k));
  node.style.transform = `translate(${lerp(dx, 0, e)}px, ${lerp(dy, 0, e)}px) scale(${s})`;
  node.style.filter = blur ? `blur(${lerp(blur, 0, e)}px)` : '';
  return k;
}

function background(root, { a = [0.16, 0.1], b = [0.86, 0.92], tint = 'teal' } = {}) {
  const bg = div('bg', {}, root);
  const glowA = tint === 'red' ? 'rgba(244,63,94,0.20)' : 'rgba(45,212,191,0.17)';
  div('glow', { left: `${a[0] * 1920 - 520}px`, top: `${a[1] * 1080 - 520}px`, width: '1040px', height: '1040px', background: `radial-gradient(circle, ${glowA}, transparent 65%)` }, bg);
  div('glow', { left: `${b[0] * 1920 - 620}px`, top: `${b[1] * 1080 - 620}px`, width: '1240px', height: '1240px', background: 'radial-gradient(circle, rgba(59,130,246,0.18), transparent 65%)' }, bg);
  div('grid', {}, bg);
  return bg;
}

// ---------- icons (simple line art, drawn in code) ----------
const ICON = {
  band: `<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M22 6h20l2 12H20z"/><path d="M20 46h24l-2 12H22z"/><rect x="14" y="18" width="36" height="28" rx="8"/><path d="M26 32h3l3-6 4 12 3-6h3"/></svg>`,
  camera: `<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="18" width="40" height="28" rx="6"/><path d="M46 28l12-7v22l-12-7"/><circle cx="20" cy="30" r="2.5"/><path d="M20 33v6M20 36l-4 3M20 36l4 3M20 39l-3 5M20 39l3 5"/></svg>`,
  db: `<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><ellipse cx="32" cy="14" rx="20" ry="7"/><path d="M12 14v36c0 4 9 7 20 7s20-3 20-7V14"/><path d="M12 26c0 4 9 7 20 7s20-3 20-7M12 38c0 4 9 7 20 7s20-3 20-7"/></svg>`,
  shield: `<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M32 6l20 8v14c0 14-9 24-20 30C21 52 12 42 12 28V14z"/><path d="M23 32l6 6 12-13"/></svg>`,
  check: `<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"><circle cx="32" cy="32" r="24"/><path d="M21 33l8 8 15-17"/></svg>`,
  lock: `<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><rect x="12" y="28" width="40" height="28" rx="6"/><path d="M20 28v-8a12 12 0 0124 0v8"/><circle cx="32" cy="42" r="3"/></svg>`,
  cpu: `<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><rect x="16" y="16" width="32" height="32" rx="5"/><rect x="25" y="25" width="14" height="14" rx="2"/><path d="M24 8v8M32 8v8M40 8v8M24 48v8M32 48v8M40 48v8M8 24h8M8 32h8M8 40h8M48 24h8M48 32h8M48 40h8"/></svg>`,
  box: `<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="3" stroke-linejoin="round"><path d="M8 20l24-12 24 12v24L32 56 8 44z"/><path d="M8 20l24 12 24-12M32 32v24"/></svg>`,
  rules: `<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><rect x="10" y="8" width="44" height="48" rx="6"/><path d="M20 22l4 4 7-8M36 22h10M20 38l4 4 7-8M36 38h10"/></svg>`,
  screen: `<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="10" width="52" height="34" rx="5"/><path d="M24 54h16M32 44v10M14 34l9-9 7 6 12-12"/></svg>`,
  bottle: `<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="3" stroke-linejoin="round"><rect x="18" y="6" width="28" height="10" rx="3"/><path d="M20 16h24v38a4 4 0 01-4 4H24a4 4 0 01-4-4z"/><rect x="20" y="28" width="24" height="14"/></svg>`,
  eyeoff: `<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M6 32s10-16 26-16 26 16 26 16-10 16-26 16S6 32 6 32z"/><circle cx="32" cy="32" r="7"/><path d="M10 54L54 10"/></svg>`,
};
const icon = (name, size, color, parent) => {
  const d = div('', { width: `${size}px`, height: `${size}px`, color }, parent, ICON[name]);
  d.firstChild.setAttribute('width', size);
  d.firstChild.setAttribute('height', size);
  return d;
};

function logoMark(size, parent) {
  const d = div('', { width: `${size}px`, height: `${size}px`, position: 'relative' }, parent);
  d.innerHTML = `<svg viewBox="0 0 100 100" width="${size}" height="${size}">
    <defs><linearGradient id="lg${size}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#2dd4bf"/><stop offset="1" stop-color="#3b82f6"/></linearGradient></defs>
    <rect x="4" y="4" width="92" height="92" rx="26" fill="url(#lg${size})"/>
    <g transform="rotate(-40 50 50)"><rect x="22" y="36" width="56" height="28" rx="14" fill="none" stroke="#fff" stroke-width="7"/>
    <path d="M50 36v28" stroke="#fff" stroke-width="7"/><rect x="22" y="36" width="28" height="28" rx="14" fill="#fff" opacity="0.95"/></g></svg>`;
  return d;
}

function wordmark(px, parent) {
  return div('', { font: `700 ${px}px/1 var(--sans)`, letterSpacing: '-0.03em', whiteSpace: 'nowrap' }, parent,
    `Pharma<span style="color:var(--teal);font-weight:600">Suite</span>`);
}

// Kinetic sentence: each word appears when it is spoken.
function sentence(parent, text, from, style = {}, colors = {}) {
  const line = div('', { font: '700 88px/1.12 var(--sans)', letterSpacing: '-0.025em', ...style }, parent);
  const words = text.split(' ').map((w) => {
    const span = h('span', { class: 'word' }, line, w + ' ');
    if (colors[w]) span.style.color = colors[w];
    return { span, t: at(w, from) };
  });
  return { line, words, update(T) { for (const w of words) reveal(w.span, T, w.t - 0.08, { dur: 0.42, dy: 18, blur: 12 }); } };
}

// Smooth pseudo-random signal used for illustrative IMU traces.
function noise(x, seed) {
  return Math.sin(x * 1.7 + seed) * 0.5 + Math.sin(x * 3.1 + seed * 2.3) * 0.3 + Math.sin(x * 7.3 + seed * 0.7) * 0.2;
}
function gesture(t, center, width) {
  const u = (t - center) / width;
  return Math.exp(-u * u * 3);
}

// ---------- scenes ----------
const SCENES = {


  stat: {
    build(root) {
      background(root, { a: [0.1, 0.15], b: [0.9, 0.85] });
      const t0 = at('Dr.', 5);
      const cardEl = div('card abs', { left: '160px', top: '130px', padding: '26px 34px 26px 26px', display: 'flex', gap: '24px', alignItems: 'center' }, root);
      div('center', { width: '84px', height: '84px', borderRadius: '50%', background: 'linear-gradient(135deg,#1f3b5c,#12304a)', border: '1px solid var(--line-strong)', font: '600 32px var(--sans)', color: '#9fdcf5' }, cardEl, 'MS');
      div('', {}, cardEl, `<div style="font:600 34px/1.2 var(--sans)">Dr. Marv Shepard</div>
        <div style="font:400 23px/1.4 var(--sans);color:var(--muted);margin-top:6px">Former Chairman, Pharmacy Administration<br>University of Texas</div>`);
      this.card = { el: cardEl, t: t0 };
      this.lead = { el: div('abs', { left: '160px', top: '395px', font: '500 46px var(--sans)', color: 'var(--muted)' }, root, 'The typical pharmacy makes'), t: at('typical', 10) - 0.2 };
      this.num = { el: div('abs', { left: '140px', top: '440px', font: '800 330px/1 var(--sans)', letterSpacing: '-0.05em', background: 'linear-gradient(180deg,#fff,#9ee7da)', webkitBackgroundClip: 'text', color: 'transparent', transformOrigin: 'left center' }, root, '2–4'), t: at('2', 12.5) };
      this.unit = { el: div('abs', { left: '810px', top: '560px', font: '700 76px/1.05 var(--sans)' }, root, 'mistakes<br><span style="color:var(--muted);font-weight:500">a day</span>'), t: at('mistakes', 13) };
      // One day of dispensing, with the errors landing on it.
      const strip = div('abs', { left: '160px', top: '850px', width: '1600px', height: '120px' }, root);
      this.strip = strip;
      const track = div('abs', { left: '0', top: '44px', width: '1600px', height: '6px', borderRadius: '3px', background: 'rgba(148,163,184,0.18)' }, strip);
      this.fill = div('abs', { left: '0', top: '0', height: '6px', borderRadius: '3px', background: 'linear-gradient(90deg,#2dd4bf,#3b82f6)' }, track);
      ['9 AM', '12 PM', '3 PM', '6 PM', '9 PM'].forEach((lab, i) => {
        div('abs', { left: `${i * 400 - 50}px`, top: '70px', width: '100px', textAlign: 'center', font: '500 20px var(--mono)', color: 'var(--dim)', whiteSpace: 'nowrap' }, strip, lab);
        div('abs', { left: `${i * 400 - 1}px`, top: '38px', width: '2px', height: '18px', background: 'rgba(148,163,184,0.3)' }, strip);
      });
      this.stripT = at('which', 14);
      this.errors = [0.23, 0.57, 0.84].map((x) => {
        const m = div('abs center', { left: `${x * 1600 - 26}px`, top: '-10px', width: '52px', height: '52px', borderRadius: '50%', background: 'rgba(244,63,94,0.16)', border: '2px solid #f43f5e', color: '#fda4af', font: '800 28px var(--sans)' }, strip, '!');
        return { m, x };
      });
      this.stakes = at('high', 16);
    },
    update(T) {
      reveal(this.card.el, T, this.card.t - 0.1, { dx: -30, dy: 0 });
      reveal(this.lead.el, T, this.lead.t);
      const k = reveal(this.num.el, T, this.num.t - 0.1, { dur: 0.7, scale: 0.6, dy: 0, blur: 16 });
      void k;
      reveal(this.unit.el, T, this.unit.t - 0.05, { dx: -20, dy: 0 });
      this.strip.style.opacity = E.out(prog(T, this.stripT - 0.3, 0.5));
      const p = E.inOut(prog(T, this.stripT, 2.6));
      this.fill.style.width = `${p * 1600}px`;
      for (const e of this.errors) {
        const hit = prog(p, e.x, 0.04);
        const pulse = T > this.stakes ? 1 + 0.08 * Math.sin((T - this.stakes) * 9) : 1;
        e.m.style.opacity = hit;
        e.m.style.transform = `scale(${lerp(0.4, 1, E.back(hit)) * pulse})`;
        e.m.style.boxShadow = `0 0 ${30 * hit}px rgba(244,63,94,0.55)`;
      }
    },
  },

  logo: {
    build(root) {
      background(root, { a: [0.3, 0.3], b: [0.72, 0.75] });
      const box = div('layer center', { flexDirection: 'column', gap: '40px' }, root);
      this.mark = logoMark(190, box);
      this.word = wordmark(150, box);
      this.tag = div('', { font: '500 38px var(--sans)', color: 'var(--muted)', letterSpacing: '-0.005em' }, box, 'A new tomorrow for trusted healthcare.');
      this.sheen = div('abs', { left: '0', top: '0', width: '240px', height: '1080px', background: 'linear-gradient(90deg,transparent,rgba(255,255,255,0.10),transparent)', transform: 'skewX(-18deg)' }, root);
      this.tMark = 30.3;
      this.tWord = at('Pharmasweet', 30) - 0.15;
      this.tTag = at('new', 32) - 0.1;
    },
    update(T) {
      reveal(this.mark, T, this.tMark, { dur: 0.8, scale: 0.5, dy: 0, blur: 0 });
      this.mark.firstChild.style.transform = `rotate(${lerp(-25, 0, E.out(prog(T, this.tMark, 0.9)))}deg)`;
      reveal(this.word, T, this.tWord, { dur: 0.7, dy: 30, blur: 14 });
      reveal(this.tag, T, this.tTag, { dur: 0.6 });
      const s = prog(T, this.tWord + 0.8, 1.3);
      this.sheen.style.left = `${lerp(300, 1700, E.inOut(s))}px`;
      this.sheen.style.opacity = s > 0 && s < 1 ? 1 : 0;
    },
  },

  stack: {
    build(root) {
      background(root, { a: [0.12, 0.2], b: [0.9, 0.8] });
      this.eyebrow = { el: div('abs eyebrow', { left: '140px', top: '150px' }, root, 'The full stack'), t: at('Through', 86) - 0.2 };
      this.title = { el: div('abs title', { left: '140px', top: '192px' }, root, 'Every step, checked automatically'), t: at('stack', 86) - 0.2 };
      const nodes = [
        ['band', '#2dd4bf', 'Wristband', 'ESP32-S3 · IMU · edge model', 'Detects pickup and put-down'],
        ['camera', '#60a5fa', 'Live capture', 'Browser camera buffer', 'Keeps only 10 s clips'],
        ['cpu', '#818cf8', 'Pose + regions', 'YOLO11 pose · shelf boxes', 'Wrist → shelf, counter, bin'],
        ['rules', '#f5a524', 'Inventory rules', 'Alerts and confirmations', 'Wrong shelf flagged'],
        ['db', '#34d399', 'MongoDB', 'Stock · receipts · history', 'Every change recorded'],
        ['screen', '#e2e8f0', 'Dashboard', 'Staff review and replay', 'People confirm the unsure'],
      ];
      const x0 = 140, w = 250, gap = 22;
      const svg = h('svg', { width: 1920, height: 1080, style: 'position:absolute;left:0;top:0' }, root);
      this.packets = [];
      for (let i = 0; i < nodes.length - 1; i++) {
        const xa = x0 + i * (w + gap) + w, xb = xa + gap;
        h('line', { x1: xa, y1: 560, x2: xb, y2: 560, stroke: 'rgba(148,163,184,0.35)', 'stroke-width': 2 }, svg);
        this.packets.push({ c: h('circle', { cx: xa, cy: 560, r: 5, fill: nodes[i][1] }, svg), xa, xb, i });
      }
      this.nodes = nodes.map(([ic, color, title, sub, check], i) => {
        const x = x0 + i * (w + gap);
        const c = div('card abs', { left: `${x}px`, top: '440px', width: `${w}px`, height: '240px', padding: '26px 22px', display: 'flex', flexDirection: 'column', gap: '12px' }, root);
        const b = div('center', { width: '64px', height: '64px', borderRadius: '18px', background: `${color}1f`, border: `1px solid ${color}55` }, c);
        icon(ic, 38, color, b);
        div('', { font: '700 28px var(--sans)', marginTop: '6px' }, c, title);
        div('', { font: '400 19px/1.35 var(--sans)', color: 'var(--muted)' }, c, sub);
        const chip = div('abs', { left: `${x}px`, top: '712px', width: `${w}px`, display: 'flex', gap: '10px', alignItems: 'flex-start', font: '500 20px/1.3 var(--sans)', color: '#b8f5e5' }, root);
        icon('check', 26, '#34d399', chip);
        chip.appendChild(document.createTextNode(check));
        return { c, chip, t: at('stack', 86) + 0.15 + i * 0.22, tc: at('possible', 88) + i * 0.3 };
      });
    },
    update(T) {
      reveal(this.eyebrow.el, T, this.eyebrow.t);
      reveal(this.title.el, T, this.title.t);
      for (const n of this.nodes) { reveal(n.c, T, n.t, { scale: 0.9, dy: 30 }); reveal(n.chip, T, n.tc, { dy: 14, blur: 6 }); }
      for (const p of this.packets) {
        const live = prog(T, this.nodes[p.i + 1].t + 0.3, 0.3);
        const u = ((T * 0.9 + p.i * 0.23) % 1);
        p.c.setAttribute('cx', lerp(p.xa, p.xb, u));
        p.c.style.opacity = live * (1 - Math.abs(u - 0.5) * 1.2);
      }
    },
  },

  ai: {
    build(root) {
      background(root, { a: [0.5, 0.0], b: [0.5, 1.1] });
      this.title = { el: div('abs title', { left: '0', width: '1920px', textAlign: 'center', top: '210px', fontSize: '84px' }, root, 'AI at every level'), t: at('AI', 92.5) - 0.1 };
      const cols = [
        ['band', '#2dd4bf', 'On the wrist', 'IMU gesture model running on the band'],
        ['camera', '#60a5fa', 'On the camera', 'YOLO pose estimation of the arm'],
        ['box', '#a78bfa', 'In the data', 'Shipment files turned into stock'],
      ];
      const times = [at('integrated', 93), at('project', 94), at('every', 94.8)];
      this.cols = cols.map(([ic, color, t1, t2], i) => {
        const c = div('card abs', { left: `${250 + i * 490}px`, top: '420px', width: '440px', height: '330px', padding: '40px', display: 'flex', flexDirection: 'column', gap: '18px' }, root);
        const b = div('center', { width: '92px', height: '92px', borderRadius: '24px', background: `${color}1f`, border: `1px solid ${color}55` }, c);
        icon(ic, 56, color, b);
        div('', { font: '700 38px var(--sans)', marginTop: '8px' }, c, t1);
        div('', { font: '400 25px/1.4 var(--sans)', color: 'var(--muted)' }, c, t2);
        return { c, t: times[i] };
      });
    },
    update(T) {
      reveal(this.title.el, T, this.title.t, { scale: 0.92 });
      for (const c of this.cols) reveal(c.c, T, c.t - 0.1, { dy: 40, scale: 0.9 });
    },
  },

  layout: {
    build(root) {
      background(root, { a: [0.1, 0.3], b: [0.95, 0.7] });
      const doc = div('card abs', { left: '120px', top: '190px', width: '760px', padding: '34px 36px' }, root);
      div('', { font: '600 20px var(--mono)', color: 'var(--teal)', letterSpacing: '0.06em' }, doc, 'SHIPMENT · SHP-2026-0004 · SGDX');
      div('', { font: '400 20px var(--sans)', color: 'var(--muted)', margin: '8px 0 22px' }, doc, 'Summit Generics Direct (synthetic) · 3 cartons');
      const lines = [
        ['Atorvastatin Calcium 20mg', 'KE5791481', '2027-12-31', 5],
        ['Atorvastatin Calcium 20mg', 'KE20X1025', '2026-11-30', 5],
        ['Metformin HCl 500mg', 'BR26W5855', '2028-07-31', 4],
        ['Omeprazole DR 20mg', 'PI58Y6048', '2029-01-31', 12],
      ];
      this.lines = lines.map(([n, lot, exp, q], i) => {
        const r = div('', { display: 'grid', gridTemplateColumns: '1fr 150px 150px 50px', gap: '10px', padding: '15px 0', borderTop: '1px solid var(--line)', font: '500 22px var(--sans)' }, doc,
          `<span>${n}</span><span class="mono" style="color:var(--muted);font-size:19px">${lot}</span><span class="mono" style="color:var(--muted);font-size:19px">${exp}</span><span style="text-align:right">${q}</span>`);
        return { r, t: at('shipment', 96) + i * 0.12 };
      });
      this.doc = { el: doc, t: at('The', 96.3) - 0.2 };
      const arrow = div('abs center', { left: '905px', top: '420px', width: '110px', height: '80px', font: '700 60px var(--sans)', color: 'var(--teal)' }, root, '→');
      this.arrow = { el: arrow, t: at('stock', 97.8) - 0.15 };
      const shelves = div('card abs', { left: '1040px', top: '190px', width: '760px', height: '600px', padding: '30px' }, root);
      div('', { font: '600 20px var(--mono)', color: 'var(--teal)', letterSpacing: '0.06em' }, shelves, 'STOCK · SHELF REGIONS');
      const stock = [['Atorvastatin 20mg', '10 bottles', '#60a5fa'], ['Metformin 500mg', '4 bottles', '#f5a524'], ['Omeprazole 20mg', '12 bottles', '#a78bfa']];
      this.boxes = stock.map(([n, q, color], i) => {
        const b = div('abs', { left: `${30 + i * 240}px`, top: '110px', width: '216px', height: '440px', border: `3px solid ${color}`, borderRadius: '10px', background: `${color}14` }, shelves);
        div('abs', { left: '-3px', top: '-38px', padding: '6px 12px', background: color, color: '#06101f', font: '700 19px var(--sans)', borderRadius: '6px 6px 6px 0', whiteSpace: 'nowrap' }, b, n);
        for (let s = 0; s < 3; s++) {
          div('abs', { left: '12px', right: '12px', top: `${130 + s * 130}px`, height: '4px', background: 'rgba(148,163,184,0.3)' }, b);
          for (let k = 0; k < 4; k++) {
            const bt = icon('bottle', 36, color, b);
            Object.assign(bt.style, { position: 'absolute', left: `${18 + k * 46}px`, top: `${86 + s * 130}px`, opacity: 0.85 });
          }
        }
        div('abs', { left: '14px', bottom: '14px', font: '500 19px var(--sans)', color: 'var(--muted)' }, b, q);
        return { b, t: at('stock', 97.8) + 0.25 + i * 0.3 };
      });
      this.shelves = { el: shelves, t: at('stock', 97.8) };
    },
    update(T) {
      reveal(this.doc.el, T, this.doc.t, { dx: -30, dy: 0 });
      for (const l of this.lines) reveal(l.r, T, l.t, { dy: 10, blur: 4 });
      reveal(this.arrow.el, T, this.arrow.t, { dx: -30, dy: 0 });
      reveal(this.shelves.el, T, this.shelves.t, { dx: 30, dy: 0 });
      for (const b of this.boxes) reveal(b.b, T, b.t, { scale: 0.85, dy: 20 });
    },
  },







  outro: {
    build(root) {
      background(root, { a: [0.3, 0.2], b: [0.7, 0.9] });
      // Logo lands on the spoken "PharmaSuite", the pillars on their words, credits last.
      this.logoT = at('PharmaSuite', 155) - 0.25;
      const head = div('abs center', { left: 0, right: 0, top: '150px', flexDirection: 'column', gap: '26px' }, root);
      const row = div('center', { gap: '30px' }, head);
      logoMark(118, row);
      wordmark(104, row);
      this.team = div('', { font: '500 30px var(--sans)', color: 'var(--muted)' }, head, window.TEAM || '');
      this.head = head;
      const items = [['shield', '#2dd4bf', 'Safer', 'for patients', at('safer', 157)], ['check', '#60a5fa', 'More trustworthy', 'for every prescription', at('trustworthy', 158)], ['eyeoff', '#a78bfa', 'Private', 'for pharmacy workers', at('privacy', 162)]];
      this.items = items.map(([ic, color, t1, t2, t], i) => {
        const c = div('card abs', { left: `${250 + i * 490}px`, top: '470px', width: '440px', height: '330px', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '20px' }, root);
        const b = div('center', { width: '108px', height: '108px', borderRadius: '30px', background: `${color}1f`, border: `1px solid ${color}55` }, c);
        icon(ic, 64, color, b);
        div('', { font: '700 42px var(--sans)' }, c, t1);
        div('', { font: '400 26px var(--sans)', color: 'var(--muted)' }, c, t2);
        return { c, t };
      });
      this.endT = at('workers', 163) - 0.4;
      this.credits = div('abs', { left: 0, right: 0, top: '880px', textAlign: 'center', font: '500 22px var(--mono)', color: 'var(--dim)', letterSpacing: '0.08em' }, root, 'YOLO11 POSE · EDGE IMPULSE · ESP32-S3 · MONGODB · UNITY');
    },
    update(T) {
      reveal(this.head, T, this.logoT, { dur: 0.9, scale: 0.92, dy: 24, blur: 12 });
      for (const it of this.items) reveal(it.c, T, it.t - 0.2, { scale: 0.85, dy: 30 });
      reveal(this.credits, T, this.endT, { dur: 0.8, dy: 12 });
    },
  },

  // Static plate behind framed dashboard captures, so scenes and captures share one look.
  plate: {
    build(root) { background(root, { a: [0.12, 0.08], b: [0.9, 0.95] }); },
    update() {},
  },
};

let current = null;
window.PENDING = [];
window.setup = (args) => {
  WORDS = args.words;
  window.TEAM = args.team || '';
  window.DATA = args;
  const stage = document.getElementById('stage');
  stage.innerHTML = '';
  current = SCENES[args.scene];
  if (!current) throw new Error(`unknown scene ${args.scene}`);
  current.seg = args.seg || {};
  current.build(stage);
};
window.renderAt = async (T) => {
  window.PENDING = [];
  current.update(T);
  await Promise.all(window.PENDING);
};
