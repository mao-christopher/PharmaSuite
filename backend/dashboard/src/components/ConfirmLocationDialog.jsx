import React, { useState } from 'react';
import { useLive } from '../lib/live';
import { LIVE_REASONS, REGION_TYPES, bottleOptions, jointNote, jointOffset, formatMs, medLabel, regionLabel } from '../lib/format';
import { Dialog } from './ui';

const REASONS = {
  too_far: 'The hand was too far from every region.',
  ambiguous: 'The hand was inside two overlapping regions.',
  no_confident_hand: 'No wrist or elbow was confidently visible in any camera, and no wrist was seen within a second before or after.',
  nothing_parked_at_counter: 'The hand was at a counter, but no bottle was parked there.',
  which_bottle: 'The shelf holds more than one kind of bottle, so the pickup alone doesn\'t say which was taken.',
  no_regions: 'No regions are configured.',
};
const PICKUP_TYPES = ['designated_shelf', 'dispensing_counter'];

/** Regions of the alert's view, nearest to the hand first. */
function rankRegions(view, types, candidates = []) {
  const regions = view.regions.filter((r) => types.includes(r.region_type));
  const dist = new Map(candidates.map((c) => [c.region_id, c.distance]));
  return regions
    .map((r) => ({ region: r, distance: dist.get(r.region_id) }))
    .sort((a, b) => (a.distance ?? Infinity) - (b.distance ?? Infinity));
}

/** Which bottle a pickup took from a shelf holding its own stock and misplaced bottles. */
function BottleChoices({ view, options, value, onChange }) {
  return (
    <fieldset className="field" aria-labelledby="bottle-legend">
      <span id="bottle-legend" className="label">
        Which bottle was picked up?
      </span>
      <div className="choice-list">
        {options.map((o) => (
          <label key={o.bottle} className={`choice ${value === o.bottle ? 'selected' : ''}`}>
            <input type="radio" name="bottle" checked={value === o.bottle} onChange={() => onChange(o.bottle)} />
            <span className="choice-main">
              <span className="row-title">{medLabel(view.medications, o.medication_key)}</span>
              <span className="row-sub">
                {o.bottle === 'shelf' ? "One of this shelf's own bottles" : `The misplaced bottle; belongs on the ${regionLabel(view, o.original_shelf_id)}`}
              </span>
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

function RegionChoices({ legend, name, view, options, value, onChange }) {
  return (
    <fieldset className="field" aria-labelledby={`${name}-legend`}>
      <span id={`${name}-legend`} className="label">
        {legend}
      </span>
      <div className="choice-list">
        {options.map(({ region, distance }, i) => (
          <label key={region.region_id} className={`choice ${value === region.region_id ? 'selected' : ''}`}>
            <input type="radio" name={name} checked={value === region.region_id} onChange={() => onChange(region.region_id)} />
            <span className="dot" aria-hidden="true" style={{ background: REGION_TYPES[region.region_type].color, marginTop: 6 }} />
            <span className="choice-main">
              <span className="row-title">{regionLabel(view, region.region_id)}</span>
              {distance != null && (
                <span className="row-sub">
                  {distance === 0 ? 'Hand inside' : `${(distance * 100).toFixed(1)}% of the frame away`}
                  {i === 0 ? ', nearest' : ''}
                </span>
              )}
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

export default function ConfirmLocationDialog({ alert, onClose }) {
  const { state, confirmLocation } = useLive();
  const m = alert.metadata;
  const view = { ...(state.views?.[m.layout_id] || state.layout), medications: state.layout.medications };
  const phase = m.phase;
  const session = state.sessions[m.session_id];
  const pending = phase === 'pickup' ? session?.evidence?.pending_release : null;
  const askRelease = pending && !pending.region_id;

  const pickupOptions = rankRegions(view, phase === 'pickup' ? PICKUP_TYPES : Object.keys(REGION_TYPES), m.candidates);
  const releaseOptions = askRelease ? rankRegions(view, Object.keys(REGION_TYPES), pending.evidence?.candidates) : [];
  const whichBottle = m.reason === 'which_bottle';
  const [regionId, setRegion] = useState(
    whichBottle ? m.region_id : pickupOptions[0]?.distance != null ? pickupOptions[0].region.region_id : '',
  );
  // No bottle is preselected: choosing one is the employee's answer, not a default.
  const [bottle, setBottle] = useState('');
  const setRegionId = (id) => {
    setRegion(id);
    setBottle('');
  };
  const bottles = phase === 'pickup' ? bottleOptions(state, view.regions.find((r) => r.region_id === regionId)) : [];
  const askBottle = bottles.length > 1;
  const [releaseId, setReleaseId] = useState(releaseOptions[0]?.distance != null ? releaseOptions[0].region.region_id : '');
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);
  const events = (state.activity || []).filter((a) => a.session_id === m.session_id);
  // Live band events carry their clip; watching it is the quickest way to decide.
  const liveClip = 'live_reason' in m ? m.recording : null;
  const [clipMissing, setClipMissing] = useState(false);
  const at = (type) => events.find((a) => a.event_type === type);

  const submit = async (e) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await confirmLocation(alert.alert_id, regionId, askRelease ? releaseId : null, askBottle ? bottle : null);
      onClose();
    } catch (err) {
      setError(err.message);
      setSaving(false);
    }
  };

  return (
    <Dialog
      title={
        whichBottle
          ? 'Which bottle was picked up?'
          : askRelease
            ? 'Where was the bottle picked up and put down?'
            : phase === 'pickup'
              ? 'Where was the bottle picked up?'
              : 'Where was the bottle put down?'
      }
      onClose={onClose}
      width={askRelease ? 720 : 480}
      footer={
        <>
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            Later
          </button>
          <button className="btn btn-primary" type="submit" form="confirm-form" disabled={!regionId || (askRelease && !releaseId) || (askBottle && !bottle) || saving}>
            {saving ? 'Saving…' : askRelease ? 'Confirm both' : whichBottle ? 'Confirm bottle' : 'Confirm location'}
          </button>
        </>
      }
    >
      <form id="confirm-form" className="form" onSubmit={submit}>
        {liveClip && !clipMissing && (
          <video
            className="confirm-clip"
            src={`/api/recordings/${encodeURIComponent(liveClip)}/video`}
            controls
            autoPlay
            muted
            loop
            playsInline
            onError={() => setClipMissing(true)}
          />
        )}
        <p className="lead">
          {whichBottle
            ? REASONS.which_bottle
            : liveClip !== null
              ? `${LIVE_REASONS[m.live_reason] || 'The location was uncertain'}.`
              : REASONS[m.reason] || 'The location was uncertain.'}
          {jointNote(m.joint, jointOffset(m)) && ` Position came from ${jointNote(m.joint, jointOffset(m))}.`}
          {session && session.medication_key !== 'UNKNOWN' && ` Bottle: ${medLabel(state.layout.medications, session.medication_key)}.`}
          {m.recording && m.recording !== state.scenario && liveClip === null && ` Recording: ${m.recording}.`}
          {' '}
          {whichBottle ? 'Choose the bottle that was taken.' : 'Choose where it happened; the nearest options are listed first.'}
        </p>
        <div className={askRelease ? 'grid-2 align-start' : 'form'}>
          <RegionChoices
            legend={`${phase === 'pickup' ? 'Picked up from' : 'Put down at'}${at(phase) ? ` (${formatMs(at(phase).media_time_ms)})` : ''}`}
            name="region"
            view={view}
            options={pickupOptions}
            value={regionId}
            onChange={setRegionId}
          />
          {askBottle && !askRelease && <BottleChoices view={view} options={bottles} value={bottle} onChange={setBottle} />}
          {askRelease && (
            <RegionChoices
              legend={`Put down at${at('release') ? ` (${formatMs(at('release').media_time_ms)})` : ''}`}
              name="release-region"
              view={view}
              options={releaseOptions}
              value={releaseId}
              onChange={setReleaseId}
            />
          )}
        </div>
        {askBottle && askRelease && <BottleChoices view={view} options={bottles} value={bottle} onChange={setBottle} />}
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
      </form>
    </Dialog>
  );
}
