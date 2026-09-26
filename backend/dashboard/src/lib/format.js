export const REGION_TYPES = {
  designated_shelf: { label: 'Shelf', plural: 'Shelves', color: '#2563eb', prefix: 'shelf' },
  dispensing_counter: { label: 'Counter', plural: 'Counters', color: '#d97706', prefix: 'counter' },
  disposal: { label: 'Disposal', plural: 'Disposal', color: '#dc2626', prefix: 'disposal' },
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
  if (!region) return regionId;
  if (region.region_type === 'designated_shelf') {
    return `${medLabel(layout.medications, region.medication_key)} shelf`;
  }
  const sameType = layout.regions.filter((r) => r.region_type === region.region_type);
  const base = REGION_TYPES[region.region_type].label;
  return sameType.length > 1 ? `${base} ${sameType.indexOf(region) + 1}` : base;
}

export function designatedShelfId(layout, medKey) {
  return layout?.regions.find((r) => r.region_type === 'designated_shelf' && r.medication_key === medKey)
    ?.region_id;
}

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
  if (c.onShelf === 0 && c.atCounter > 0) return { label: 'None on shelf · at counter', tone: 'amber' };
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

export function formatDate(iso) {
  if (!iso) return '—';
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

export function uniqueSessions(sessions) {
  const byId = new Map();
  Object.values(sessions || {}).forEach((s) => byId.set(s.session_id, s));
  return [...byId.values()];
}
