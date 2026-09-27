import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { request } from '../lib/api';
import { Badge, Card, Empty } from './ui';

const STATUS_TONE = { new: 'blue', kept: 'gray', moved: 'amber' };

// Plan shelf slots for imported shipments (Meta Llama when a token is configured), review
// mix-up separation, then apply. Applying changes shelf regions only, never stock.
export default function ShelfLayoutCard({ busy: pageBusy, stocking, onApplied }) {
  const [status, setStatus] = useState(null);
  const [roomId, setRoomId] = useState('');
  const [useMeta, setUseMeta] = useState(true);
  const [plan, setPlan] = useState(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');

  useEffect(() => {
    request('/api/shelf-layout/status').then((s) => {
      setStatus(s);
      setRoomId((id) => id || s.rooms[0]?.room_id || '');
      if (!s.meta.configured) setUseMeta(false);
    }).catch((e) => setError(e.message));
  }, []);

  async function run(task) {
    setBusy(true); setError(''); setMessage('');
    try { await task(); } catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  const makePlan = () => run(async () => {
    setPlan(null); setAcknowledged(false);
    setPlan(await request('/api/shelf-layout/plan', { method: 'POST', body: { room_id: roomId, use_meta: useMeta } }));
  });
  const applyPlan = () => run(async () => {
    const res = await request('/api/shelf-layout/apply', {
      method: 'POST',
      body: {
        room_id: plan.room_id, room_version: plan.room_version, source: plan.source,
        assignments: plan.assignments.map((a) => ({ medication_key: a.medication_key, slot_id: a.slot_id })),
        mixup_pairs: plan.risks.filter((r) => r.source !== 'built-in').map((r) => ({ medication_keys: r.medication_keys, kind: r.kind, reason: r.reason })),
        acknowledge_mixups: acknowledged,
      },
    });
    setPlan(null);
    setMessage(`Layout applied: ${plan.assignments.length} shelves, ${res.added_medications.length} medication(s) added to the catalog, ${res.moved.length} moved. Stock counts did not change.`);
    onApplied?.();
  });

  if (!status) return <Card title="Shelf layout">{error ? <p className="text-red" role="alert">{error}</p> : <p className="muted">Loading…</p>}</Card>;
  const names = Object.fromEntries((plan?.assignments || []).map((a) => [a.medication_key, `${a.name} ${a.strength}`]));
  const warnings = plan?.risks.filter((r) => r.warning) || [];

  return <Card title="Shelf layout from shipments"
    subtitle="Proposes a slot for every medication shelved in the room and every accepted line of an imported shipment. Names that sound or look alike, other strengths of one drug and known mix-ups go on different shelves."
    actions={<Badge tone={status.meta.configured ? 'green' : 'gray'} title={status.meta.model}>{status.meta.configured ? 'Meta Llama connected' : 'No Meta API token'}</Badge>}>
    {error && <p className="text-red" role="alert">{error}</p>}
    {message && <p role="status">{message}</p>}
    {!status.rooms.length ? <Empty>Scan a room and tag its shelving on the <Link to="/room">Room</Link> page first.</Empty> : <>
      <div className="shipment-controls">
        <label>Room <select value={roomId} disabled={busy} onChange={(e) => { setRoomId(e.target.value); setPlan(null); }}>
          {status.rooms.map((r) => <option key={r.room_id} value={r.room_id}>{r.name} ({r.shelves} shelves)</option>)}
        </select></label>
        <label><input type="checkbox" checked={useMeta} disabled={busy || !status.meta.configured} onChange={(e) => setUseMeta(e.target.checked)} /> Plan with Meta Llama</label>
        <button className="btn" disabled={busy || pageBusy || !roomId} onClick={makePlan}>{busy && !plan ? 'Planning…' : 'Plan layout'}</button>
      </div>
      {!status.meta.configured && <p className="muted">Set META_API_KEY in backend/.env to plan with Meta Llama. The built-in planner applies the same separation rules.</p>}
    </>}
    {plan && <div className="shelf-plan">
      <p>
        <Badge tone={plan.source === 'meta-llama' ? 'blue' : 'gray'}>{plan.source === 'meta-llama' ? `Meta Llama · ${plan.model}` : 'Built-in planner'}</Badge>{' '}
        {plan.assignments.length} medications in {plan.slots_total} slots.
      </p>
      {plan.fallback_reason && <p className="text-amber">{plan.fallback_reason}</p>}
      {plan.notes.map((n) => <p key={n} className="muted">{n}</p>)}
      <h3>Mix-up risks ({plan.risks.length})</h3>
      {!plan.risks.length ? <p className="muted">No similar names or strengths found among these medications.</p> :
        <div className="table-wrap"><table className="table"><thead><tr><th>Pair</th><th>Why</th><th>Placement</th></tr></thead>
          <tbody>{plan.risks.map((r) => <tr key={r.medication_keys.join('|')}>
            <td>{r.medication_keys.map((k) => names[k] || k).join(' / ')}</td>
            <td>{r.kind}<div className="row-sub">{r.reason}{r.source === 'meta-llama' ? ' (flagged by Meta Llama)' : ''}</div></td>
            <td><Badge tone={r.warning ? 'red' : 'green'}>{r.separation}</Badge></td>
          </tr>)}</tbody></table></div>}
      <h3>Slots</h3>
      <div className="table-wrap"><table className="table"><thead><tr><th>Medication / strength</th><th>Slot</th><th>Bottles</th><th>Reason</th></tr></thead>
        <tbody>{plan.assignments.map((a) => <tr key={a.medication_key}>
          <td>{a.name} {a.strength}<div className="row-sub">{a.in_catalog ? 'In catalog' : 'Added to the catalog on apply'}{a.shipments.length ? ` · ${a.shipments.join(', ')}` : ''}</div></td>
          <td>{a.label}<div className="row-sub"><Badge tone={STATUS_TONE[a.status]}>{a.status}</Badge> {a.band} height{a.counter_m != null ? ` · ${a.counter_m} m to counter` : ''}</div></td>
          <td>{a.bottles_on_hand} on hand{a.incoming_bottles ? ` + ${a.incoming_bottles} incoming` : ''}</td>
          <td>{a.reason}</td>
        </tr>)}</tbody></table></div>
      {warnings.length > 0 && <label className="text-red">
        <input type="checkbox" checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)} />{' '}
        {warnings.length} mix-up pair(s) stay on one shelf or side by side. I will label or separate them by hand.
      </label>}
      <div className="shipment-controls">
        <button className="btn btn-primary" disabled={busy || pageBusy || stocking || (warnings.length > 0 && !acknowledged)} onClick={applyPlan}>Apply layout</button>
        <button className="btn" disabled={busy} onClick={() => setPlan(null)}>Discard</button>
        {stocking && <span className="muted">Finish stocking before changing the layout.</span>}
      </div>
    </div>}
  </Card>;
}
