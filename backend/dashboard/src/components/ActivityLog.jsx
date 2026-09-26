import React from 'react';
import { useLive } from '../lib/live';
import { NONE, SESSION_STATES, formatMs, medLabel, regionLabel } from '../lib/format';
import { Badge, Card, Empty } from './ui';

const REASONS = {
  too_far: 'Too far from any region',
  ambiguous: 'Overlapping regions',
  no_confident_hand: 'No wrist visible',
  nothing_parked_at_counter: 'Nothing parked at the counter',
};

export function SignalTable({ rows, layout, empty }) {
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
                  {a.nearest_region_id ? regionLabel(layout, a.nearest_region_id) : NONE}
                  {a.reason && <div className="row-sub text-amber">{REASONS[a.reason] || a.reason}</div>}
                </td>
                <td className="num">{a.distance != null ? `${(a.distance * 100).toFixed(1)}%` : NONE}</td>
                <td>
                  <Badge tone={s.tone}>{a.held_pending ? 'Waiting on pickup confirmation' : s.label}</Badge>
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
        empty="No signals applied yet. Press Play to run the recording."
      />
    </Card>
  );
}
