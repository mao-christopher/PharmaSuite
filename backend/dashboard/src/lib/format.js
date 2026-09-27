export const REGION_TYPES = {
  designated_shelf: { label: 'Shelf', plural: 'Shelves', color: '#1f6ce0', prefix: 'shelf' },
  dispensing_counter: { label: 'Counter', plural: 'Counters', color: '#c47d0a', prefix: 'counter' },
  disposal: { label: 'Disposal', plural: 'Disposal', color: '#ce3438', prefix: 'disposal' },
};

export function medicationKeyFor(name, strength) {
  const namePart = name.trim().toUpperCase().replace(/[^A-Z0-9]+/g, '_').replace(/^_+|_+$/g, '');
  const strengthPart = strength.trim().toUpperCase().replace(/[^A-Z0-9.]+/g, '');
  return `${namePart}_${strengthPart}`;
}

export function medLabel(medications, key) {
  const med = medications?.find((m) => m.medication_key === key);
  return med ? `${med.name} ${med.strength}` : key === 'UNKNOWN' ? 'Unknown medication' : key;
}

export function regionLabel(layout, regionId) {
  if (!regionId || regionId === 'UNKNOWN') return 'Unknown location';
  const region = layout?.regions.find((r) => r.region_id === regionId);
  if (!region) {
    // Shelves share canonical IDs across camera views, so they resolve even when not in this view.
    const med = layout?.medications.find((m) => `shelf_${m.medication_key.toLowerCase()}` === regionId);
    if (med) return `${med.name} ${med.strength} shelf`;
    if (regionId.startsWith('counter')) return 'Counter';
    if (regionId.startsWith('disposal')) return 'Disposal';
    return regionId;
  }
  if (region.region_type === 'designated_shelf') {
    return `${medLabel(layout.medications, region.medication_key)} shelf`;
  }
  const sameType = layout.regions.filter((r) => r.region_type === region.region_type);
  const base = REGION_TYPES[region.region_type].label;
  return sameType.length > 1 ? `${base} ${sameType.indexOf(region) + 1}` : base;
}

export function designatedShelfId(layout, medKey) {
  return medKey ? `shelf_${medKey.toLowerCase()}` : undefined;
}

const JOINT_LABEL = {
  wrist: 'wrist',
  elbow: 'elbow (wrist hidden)',
  shoulder: 'shoulder (wrist and elbow hidden)', // older recordings
};

/** Where a signal's hand position came from when it wasn't the wrist at that moment, or null. */
export function jointNote(joint, offsetMs) {
  if (!joint || joint === 'wrist') return null;
  const s = (Math.max(Math.abs(offsetMs || 0), 100) / 1000).toFixed(1);
  if (joint === 'last_seen_wrist') return `the wrist seen ${s} s earlier`;
  if (joint === 'next_seen_wrist') return `the wrist seen ${s} s later`;
  return `the ${JOINT_LABEL[joint] || joint}`;
}

/** A signal row's joint offset (older rows stored how long before the signal, as joint_age_ms). */
export const jointOffset = (row) => row.joint_offset_ms ?? -(row.joint_age_ms || 0);

export function bottleCounts(layout, inv) {
  const shelfId = designatedShelfId(layout, inv.medication_key);
  const onShelf = inv.shelf_counts[shelfId] ?? 0;
  const misplaced = Object.entries(inv.shelf_counts)
    .filter(([id]) => id !== shelfId)
    .reduce((sum, [, n]) => sum + n, 0);
  return {
    onShelf,
    misplaced,
    held: inv.held_bottles,
    atCounter: inv.counter_bottles,
    offShelf: inv.held_bottles + inv.counter_bottles,
    total: inv.total_bottles,
  };
}

export function stockStatus(layout, inv) {
  const c = bottleCounts(layout, inv);
  if (c.total === 0) return { label: 'Out of stock', tone: 'red' };
  if (inv.uncertain_location) return { label: 'Location uncertain', tone: 'amber' };
  if (c.misplaced > 0) return { label: 'Misplaced bottle', tone: 'red' };
  if (inv.pooled_tablets === 0) return { label: 'Check tablet count', tone: 'amber' };
  if (c.onShelf === 0 && c.atCounter > 0) return { label: 'Shelf empty, bottle at counter', tone: 'amber' };
  if (c.offShelf > 0) return { label: 'Bottle off shelf', tone: 'blue' };
  return { label: 'In stock', tone: 'green' };
}

export const ALERT_TYPES = {
  misplacement: 'Misplaced bottle',
  uncertainty: 'Confirm location',
  expiry: 'Expired stock',
  out_of_stock: 'Out of stock',
  reconciliation_issue: 'Reconciliation needed',
};

export const SEVERITY_TONE = { error: 'red', warning: 'amber', info: 'blue' };
export const SEVERITY_RANK = { error: 0, warning: 1, info: 2 };

export const SESSION_STATES = {
  HELD: { label: 'In hand', tone: 'blue' },
  AT_COUNTER: { label: 'At counter', tone: 'amber' },
  MISPLACED: { label: 'Misplaced', tone: 'red' },
  NEEDS_CONFIRMATION: { label: 'Needs confirmation', tone: 'amber' },
  ON_DESIGNATED_SHELF: { label: 'Returned', tone: 'green' },
  DISPOSED: { label: 'Disposed', tone: 'gray' },
};

/** Why a live clip's location needs confirmation (backend live_capture / live_routes reasons). */
export const LIVE_REASONS = {
  no_stable_intersection: 'The wrist never settled inside one region',
  overlapping_regions: 'The wrist was inside overlapping regions',
  competing_regions: 'The wrist settled in more than one region',
  multiple_people: 'More than one person was in view',
  camera_aspect_mismatch: "The camera's frame shape doesn't match its view",
  no_camera_frames: 'The camera was off',
  incomplete_clip_window: 'The clip was incomplete',
  calibration_changed: 'The camera view changed after the clip was captured',
};

export const TX_STATUS = {
  created: { label: 'Waiting', tone: 'gray' },
  confirmed_fill: { label: 'Filled', tone: 'blue' },
  paid: { label: 'Paid', tone: 'green' },
  cancelled: { label: 'Cancelled', tone: 'gray' },
};

export function formatMs(ms = 0) {
  const total = Math.max(0, ms) / 1000;
  const minutes = Math.floor(total / 60);
  return `${minutes}:${(total % 60).toFixed(1).padStart(4, '0')}`;
}

export function todayIso() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export function isExpired(expiryDate) {
  return Boolean(expiryDate) && expiryDate < todayIso();
}

export const NONE = '-';

const dateFmt = new Intl.DateTimeFormat(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
const dateTimeFmt = new Intl.DateTimeFormat(undefined, {
  month: 'short',
  day: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
});
const numberFmt = new Intl.NumberFormat();

export function formatDate(iso) {
  if (!iso) return NONE;
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
  return dateFmt.format(new Date(y, m - 1, d));
}

export function formatDateTime(iso) {
  if (!iso) return NONE;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : dateTimeFmt.format(d);
}

export function formatNumber(n) {
  return n == null ? NONE : numberFmt.format(n);
}

export function plural(n, one, many = `${one}s`) {
  return `${formatNumber(n)} ${n === 1 ? one : many}`;
}

export function inventoryTotals(state) {
  const totals = { bottles: 0, onShelf: 0, offShelf: 0, misplaced: 0, out: 0 };
  (state.layout?.medications || []).forEach((m) => {
    const inv = state.inventory[m.medication_key];
    if (!inv) return;
    const c = bottleCounts(state.layout, inv);
    totals.bottles += c.total;
    totals.onShelf += c.onShelf + c.misplaced;
    totals.misplaced += c.misplaced;
    totals.offShelf += c.offShelf;
    if (c.total === 0) totals.out += 1;
  });
  const alerts = Object.values(state.alerts);
  totals.expired = alerts.filter((a) => a.alert_type === 'expiry' && a.status === 'open').length;
  totals.attention =
    alerts.filter((a) => a.status === 'open').length +
    Object.values(state.disposals).filter((d) => d.status === 'pending_employee_entry').length;
  return totals;
}

export function appliedState(rec) {
  if (!rec || rec.events_total === 0) return { label: 'No signals', tone: 'gray' };
  if (rec.events_applied === 0) return { label: 'Not applied', tone: 'amber' };
  if (rec.events_applied < rec.events_total)
    return { label: `${rec.events_applied} of ${rec.events_total} applied`, tone: 'blue' };
  return { label: 'Applied', tone: 'green' };
}

export function uniqueSessions(sessions) {
  const byId = new Map();
  Object.values(sessions || {}).forEach((s) => byId.set(s.session_id, s));
  return [...byId.values()];
}

export function nextId(prefix, existing) {
  const taken = new Set(existing);
  let n = 1;
  while (taken.has(`${prefix}_${String(n).padStart(2, '0')}`)) n += 1;
  return `${prefix}_${String(n).padStart(2, '0')}`;
}

/** Shelves are named after their medication, so every view counts the same shelf. */
export const shelfIdFor = (medKey) => `shelf_${medKey.toLowerCase()}`;
