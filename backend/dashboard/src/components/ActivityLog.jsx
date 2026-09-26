import React from 'react';
import { useLive } from '../lib/live';
import { SESSION_STATES, formatMs, medLabel, regionLabel } from '../lib/format';
import { Badge, Card, Empty } from './ui';

const REASONS = {
  too_far: 'too far from any region',
  ambiguous: 'overlapping regions',
  no_confident_hand: 'no wrist visible',
  nothing_parked_at_counter: 'nothing parked at counter',
};

export default function ActivityLog() {
  const { state } = useLive();
  const upcoming = state.events.filter((e) => !e.processed).length;
  const rows = [...(state.activity || [])].reverse();
  const limit = ((state.max_region_distance ?? 0) * 100).toFixed(0);

  return (
    <Card
      title="Signal log"
      subtitle={`What the camera saw at each pickup / put-down signal. Max hand distance ${limit}% of the frame.`}
      actions={<span className="muted">{upcoming} upcoming</span>}
      flush
    >
      {rows.length === 0 ? (
        <Empty>No signals yet. Press Play to run the recording.</Empty>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Time</th>
              <th>Signal</th>
              <th>Nearest region</th>
              <th className="num">Distance</th>
              <th>Result</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((a) => {
              const s = SESSION_STATES[a.state] || { label: a.state, tone: 'gray' };
              return (
                <tr key={a.event_id}>
                  <td className="mono">{formatMs(a.media_time_ms)}</td>
                  <td>{a.event_type === 'pickup' ? 'Pick up' : 'Put down'}</td>
                  <td>
                    {a.nearest_region_id ? regionLabel(state.layout, a.nearest_region_id) : '—'}
                    {a.reason && <div className="row-sub text-amber">{REASONS[a.reason] || a.reason}</div>}
                  </td>
                  <td className="num">{a.distance != null ? `${(a.distance * 100).toFixed(1)}%` : '—'}</td>
                  <td>
                    <Badge tone={s.tone}>{a.held_pending ? 'Waiting on pickup confirmation' : s.label}</Badge>
                    <div className="row-sub">{medLabel(state.layout?.medications, a.medication_key)}</div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </Card>
  );
}
