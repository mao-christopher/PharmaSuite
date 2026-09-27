import { medLabel, regionLabel } from './format.js';

/** Playback notices use persisted activity, never create or reapply inventory events. */
export function movementNotices(state) {
  const seen = new Set();
  return [...(state.activity || [])].reverse().filter(row => {
    if (!['pickup', 'release'].includes(row.event_type) || row.media_time_ms > state.media_time_ms || seen.has(row.event_id)) return false;
    seen.add(row.event_id);
    return true;
  }).slice(0, 6).map(row => {
    const alert = Object.values(state.alerts || {}).find(a => a.status === 'open' && a.alert_type === 'uncertainty' && a.metadata?.session_id === row.session_id);
    const candidates = alert?.metadata?.bottle_options || [];
    const known = row.medication_key && row.medication_key !== 'UNKNOWN';
    const medication = known ? medLabel(state.layout?.medications, row.medication_key)
      : candidates.length ? [...new Set(candidates.map(c => medLabel(state.layout?.medications, c.medication_key)))].join(' or ')
      : 'Medication awaiting confirmation';
    const region = row.confirmed_region_id || row.nearest_region_id;
    let message;
    if (row.held_pending) message = 'Put-down recorded; inventory is waiting for pickup confirmation.';
    else if (row.state === 'NEEDS_CONFIRMATION') message = 'Confirm the bottle or location before inventory changes.';
    else if (row.event_type === 'pickup') message = `Picked up from ${regionLabel(state.layout, region)}. Bottle location updated; total stock unchanged.`;
    else if (row.state === 'AT_COUNTER') message = 'Put down at the counter. Bottle is off shelf; total stock unchanged.';
    else if (row.state === 'MISPLACED') message = `Put down on ${regionLabel(state.layout, region)}. Wrong-shelf alert raised; medication identity retained.`;
    else if (row.state === 'ON_DESIGNATED_SHELF') message = 'Put down on its designated shelf. On-shelf count updated.';
    else if (row.state === 'DISPOSED') message = 'Bottle disposed. Inventory updated; disposal details need confirmation.';
    else message = 'Signal recorded. See the signal log for its inventory status.';
    return { ...row, title: row.event_type === 'pickup' ? 'Wristband detected pickup' : 'Wristband detected put down',
      medication, message, alert, simulated: state.recording?.source !== 'live' };
  });
}
