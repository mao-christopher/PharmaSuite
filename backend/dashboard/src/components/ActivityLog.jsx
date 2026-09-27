import React from 'react';
import { useLive } from '../lib/live';
import { useDialogs } from '../lib/dialogs';
import { NONE, jointNote, jointOffset, SESSION_STATES, formatMs, medLabel, regionLabel } from '../lib/format';
import { Badge, Card, Empty } from './ui';

const REASONS = {
  too_far: 'Too far from any region',
  ambiguous: 'Overlapping or nearby regions',
  no_confident_hand: 'No hand visible',
  nothing_parked_at_counter: 'Nothing parked at the counter',
};

/** Open uncertainty alert for a session, so its row can offer "Resolve". */
export function useResolver() {
  const { state } = useLive();
  const { openConfirm } = useDialogs();
  const open = new Map();
  Object.values(state.alerts).forEach((a) => {
    if (a.alert_type === 'uncertainty' && a.status === 'open') open.set(a.metadata.session_id, a.alert_id);
  });
  return (sessionId) => (open.has(sessionId) ? () => openConfirm(open.get(sessionId)) : null);
}

export function SignalTable({ rows, layout, empty, resolverFor, cameras }) {
  if (rows.length === 0) return <Empty>{empty}</Empty>;
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th scope="col">Time</th>
            <th scope="col">Signal</th>
            <th scope="col">Nearest region</th>
            <th scope="col" className="num">
              Distance
            </th>
            <th scope="col">Result</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((a) => {
            const s = SESSION_STATES[a.state] || { label: a.state, tone: 'gray' };
            return (
              <tr key={`${a.session_id}-${a.event_id}`}>
                <td className="mono">{formatMs(a.media_time_ms)}</td>
                <td className="nowrap">{a.event_type === 'pickup' ? 'Pickup' : 'Put-down'}</td>
                <td>
                  {a.confirmed_region_id ? (
                    <>
                      {regionLabel(layout, a.confirmed_region_id)}
                      <div className="row-sub text-green">Confirmed by employee</div>
                    </>
                  ) : (
                    <>
                      {a.nearest_region_id ? regionLabel(layout, a.nearest_region_id) : NONE}
                      {a.reason && <div className="row-sub text-amber">{REASONS[a.reason] || a.reason}</div>}
                    </>
                  )}
                  {jointNote(a.joint, jointOffset(a)) && <div className="row-sub">From {jointNote(a.joint, jointOffset(a))}</div>}
                  {a.camera_id && cameras?.length > 1 && (
                    <div className="row-sub">Seen by {cameras.find((c) => c.camera_id === a.camera_id)?.label || a.camera_id}</div>
                  )}
                </td>
                <td className="num">{a.distance != null ? `${(a.distance * 100).toFixed(1)}%` : NONE}</td>
                <td>
                  <div className="badges">
                    <Badge tone={s.tone}>{a.held_pending ? 'Waiting on pickup confirmation' : s.label}</Badge>
                    {resolverFor?.(a.session_id) && (a.state === 'NEEDS_CONFIRMATION' || a.held_pending) && (
                      <button type="button" className="btn btn-sm btn-primary" onClick={resolverFor(a.session_id)}>
                        Resolve
                      </button>
                    )}
                  </div>
                  <div className="row-sub">{medLabel(layout?.medications, a.medication_key)}</div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function ActivityLog() {
  const { state } = useLive();
  const resolverFor = useResolver();
  if (!state.recording) return null;
  const upcoming = state.events.filter((e) => !e.processed).length;
  const limit = ((state.max_region_distance ?? 0) * 100).toFixed(0);

  return (
    <Card
      title="Signal log"
      className="area-signals"
      subtitle={`Where the nearest wrist was at each signal. A hand farther than ${limit}% of the frame from every region needs confirmation.`}
      actions={upcoming > 0 && <Badge tone="gray">{upcoming} upcoming</Badge>}
      flush
    >
      <SignalTable
        rows={[...(state.activity || [])].reverse()}
        layout={state.layout}
        cameras={state.recording.cameras}
        resolverFor={resolverFor}
        empty="No signals applied yet. Press Play to run the recording."
      />
    </Card>
  );
}
