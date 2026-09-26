import React, { useState } from 'react';
import { useLive } from '../lib/live';
import { REGION_TYPES, formatMs, regionLabel } from '../lib/format';
import { Dialog } from './ui';

const REASONS = {
  too_far: 'The hand was too far from every region.',
  ambiguous: 'The hand was inside two overlapping regions.',
  no_confident_hand: 'No wrist was confidently visible in the video at that moment.',
  nothing_parked_at_counter: 'The hand was at a counter, but no bottle was parked there.',
  no_regions: 'No regions are configured.',
};

export default function ConfirmLocationDialog({ alert, onClose }) {
  const { state, confirmLocation } = useLive();
  const phase = alert.metadata.phase;
  const types = phase === 'pickup' ? ['designated_shelf', 'dispensing_counter'] : Object.keys(REGION_TYPES);
  const regions = state.layout.regions.filter((r) => types.includes(r.region_type));
  const [regionId, setRegionId] = useState(alert.metadata.nearest_region_id || '');
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);
  const event = state.activity?.find((a) => a.session_id === alert.metadata.session_id && a.event_type === phase);

  const submit = async (e) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await confirmLocation(alert.alert_id, regionId);
      onClose();
    } catch (err) {
      setError(err.message);
      setSaving(false);
    }
  };

  return (
    <Dialog
      title={phase === 'pickup' ? 'Where was the bottle picked up?' : 'Where was the bottle put down?'}
      onClose={onClose}
      width={460}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            Later
          </button>
          <button className="btn btn-primary" type="submit" form="confirm-form" disabled={!regionId || saving}>
            Confirm location
          </button>
        </>
      }
    >
      <form id="confirm-form" className="form" onSubmit={submit}>
        <p className="lead">
          {REASONS[alert.metadata.reason] || 'The location was uncertain.'}
          {event && ` Signal at ${formatMs(event.media_time_ms)}.`}
          {alert.metadata.nearest_region_id &&
            ` Nearest was ${regionLabel(state.layout, alert.metadata.nearest_region_id)}` +
              (alert.metadata.distance != null ? ` (${(alert.metadata.distance * 100).toFixed(1)}% of the frame away).` : '.')}
        </p>
        <div className="choice-list">
          {regions.map((r) => (
            <label key={r.region_id} className={`choice ${regionId === r.region_id ? 'selected' : ''}`}>
              <input type="radio" name="region" checked={regionId === r.region_id} onChange={() => setRegionId(r.region_id)} />
              <span className="dot" style={{ background: REGION_TYPES[r.region_type].color, marginTop: 5 }} />
              <span className="choice-main">{regionLabel(state.layout, r.region_id)}</span>
            </label>
          ))}
        </div>
        {error && <p className="form-error">{error}</p>}
      </form>
    </Dialog>
  );
}
