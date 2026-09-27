import React from 'react';
import { ArrowRightIcon, PlayIcon } from '@phosphor-icons/react';
import { useLive } from '../lib/live';
import { useDialogs } from '../lib/dialogs';
import { useLiveCapture } from '../lib/liveCapture';
import { LIVE_REASONS, SESSION_STATES, medLabel, regionLabel } from '../lib/format';
import { Badge, Card, Empty } from './ui';

const clock = (iso) => (iso ? new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '');

function Step({ step, kind, layout, onReview, waiting }) {
  const verb = kind === 'pickup' ? 'Pickup' : 'Put-down';
  if (!step) {
    return (
      <div className="move-step is-waiting">
        <div className="move-thumb" aria-hidden="true" />
        <div className="choice-main">
          <div className="move-step-title">{verb}</div>
          <div className="row-sub">{waiting ? 'Bottle in hand. Waiting for the put-down…' : 'No put-down yet'}</div>
        </div>
      </div>
    );
  }
  const where = step.region_id ? regionLabel(layout, step.region_id) : 'Location uncertain';
  return (
    <div className="move-step">
      {step.recording ? (
        <button type="button" className="move-thumb" onClick={() => onReview(step.recording)} aria-label={`Review the ${verb.toLowerCase()} clip`} title="Review in the player">
          <img src={`/api/recordings/${encodeURIComponent(step.recording)}/thumbnail`} alt="" loading="lazy" />
          <span className="move-thumb-play" aria-hidden="true">
            <PlayIcon size={12} weight="fill" />
          </span>
        </button>
      ) : (
        <div className="move-thumb no-clip" title="No clip: the camera was off or the clip was deleted">
          No clip
        </div>
      )}
      <div className="choice-main">
        <div className="move-step-title">
          {verb} <span className="muted num">{clock(step.captured_at)}</span>
          {step.source === 'dev' && <Badge tone="gray">Dev key</Badge>}
        </div>
        <div className={`row-sub ${step.region_id ? '' : 'text-amber'}`}>
          {where}
          {step.confirmed && <span className="text-green"> · confirmed</span>}
        </div>
        {!step.region_id && step.reason && <div className="row-sub">{LIVE_REASONS[step.reason] || step.reason}.</div>}
      </div>
    </div>
  );
}

/** Recent live movements: each pickup and the next put-down are one bottle, across two clips. */
export default function LiveMovements() {
  const { state, loadRecording } = useLive();
  const { openConfirm } = useDialogs();
  const capture = useLiveCapture();
  const movements = state.live?.movements || [];
  const held = state.live?.held_movement_id;
  const layout = state.layout;

  const review = async (name) => {
    await loadRecording(name);
    capture.setPanel('player');
    document.getElementById('main')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  return (
    <Card
      title="Live movements"
      subtitle="A pickup and the next put-down are tracked as one bottle."
      className="area-movements"
      actions={held ? <Badge tone="blue">Bottle in hand</Badge> : movements.length > 0 && <Badge tone="gray">{movements.length} recent</Badge>}
      flush
    >
      {movements.length === 0 ? (
        <Empty>{capture.camera === 'on' ? 'No band events yet. Pick up a bottle to start.' : 'No live movements yet.'}</Empty>
      ) : (
        <ul className="rows">
          {movements.map((m, i) => {
            const meta = SESSION_STATES[m.state] || { label: m.state || 'Unknown', tone: 'gray' };
            const isHeld = m.movement_id === held;
            return (
              <li key={m.movement_id} className={`movement ${i === 0 ? 'is-latest' : ''}`}>
                <div className="movement-head">
                  <div className="choice-main">
                    <div className="row-title">
                      {medLabel(layout?.medications, m.medication_key)}
                      {i === 0 && <span className="movement-latest">Latest</span>}
                    </div>
                    {m.original_shelf_id && m.original_shelf_id !== 'UNKNOWN' && (
                      <div className="row-sub">Belongs on {regionLabel(layout, m.original_shelf_id)}</div>
                    )}
                  </div>
                  <div className="row-actions">
                    {m.alert_id && (
                      <button type="button" className="btn btn-sm btn-primary" onClick={() => openConfirm(m.alert_id)}>
                        Confirm location
                      </button>
                    )}
                    <Badge tone={isHeld ? 'blue' : meta.tone}>{isHeld ? 'In hand' : meta.label}</Badge>
                  </div>
                </div>
                <div className="movement-steps">
                  <Step step={m.pickup} kind="pickup" layout={layout} onReview={review} />
                  <ArrowRightIcon className="movement-arrow" aria-hidden="true" />
                  <Step step={m.release} kind="release" layout={layout} onReview={review} waiting={isHeld} />
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}
