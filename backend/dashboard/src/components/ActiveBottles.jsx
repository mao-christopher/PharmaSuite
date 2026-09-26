import React from 'react';
import { useLive } from '../lib/live';
import { SESSION_STATES, medLabel, regionLabel, uniqueSessions } from '../lib/format';
import { Badge, Card, Empty } from './ui';

const ACTIVE = new Set(['HELD', 'AT_COUNTER', 'MISPLACED', 'NEEDS_CONFIRMATION']);

export default function ActiveBottles() {
  const { state } = useLive();
  const active = uniqueSessions(state.sessions).filter((s) => ACTIVE.has(s.state));

  return (
    <Card title="Off-shelf bottles">
      {active.length === 0 ? (
        <Empty>Every tracked bottle is on its shelf.</Empty>
      ) : (
        <ul className="rows">
          {active.map((s) => {
            const meta = SESSION_STATES[s.state];
            return (
              <li key={s.session_id} className="row">
                <div>
                  <div className="row-title">{medLabel(state.layout?.medications, s.medication_key)}</div>
                  <div className="row-sub">
                    Belongs on {regionLabel(state.layout, s.original_shelf_id)}
                    {s.state !== 'HELD' && s.current_location_id && ` · now at ${regionLabel(state.layout, s.current_location_id)}`}
                  </div>
                </div>
                <Badge tone={meta.tone}>{meta.label}</Badge>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}
