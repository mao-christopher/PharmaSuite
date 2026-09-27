import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { useLive } from '../lib/live';
import { request, errorMessage } from '../lib/api';
import { Badge, Card, Dialog, Empty, PageHeader } from '../components/ui';

export default function Shipments() {
  const { state, refresh } = useLive();
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [shortage, setShortage] = useState(null);
  const shipments = Object.values(state.shipments || {});
  const active = shipments.find((s) => s.status === 'stocking');
  const nextPickup = state.events?.find((e) => e.event_type === 'pickup' && !e.processed);

  async function run(task) {
    setBusy(true); setError(''); setMessage('');
    try { await task(); await refresh(); } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }
  async function upload(isPreview) {
    if (!file) return;
    const form = new FormData(); form.set('file', file);
    const res = await fetch(`/api/shipments/import?preview=${isPreview}`, { method: 'POST', body: form });
    if (!res.ok) throw new Error(await errorMessage(res));
    const data = await res.json();
    if (isPreview) setPreview(data.shipments);
    else {
      setPreview(null); setFile(null);
      setMessage(`${data.results.filter((r) => !r.duplicate).length} shipment(s) imported; ${data.results.filter((r) => r.duplicate).length} already present. Inventory changes when stocking starts.`);
    }
  }
  const post = (id, action, body) => request(`/api/shipments/${id}/${action}`, { method: 'POST', body });

  return <>
    <PageHeader title="Shipments" subtitle="Review incoming deliveries, identify each bottle and reconcile its shelf placement." />
    {error && <p className="text-red" role="alert">{error}</p>}
    {message && <p role="status">{message}</p>}
    <Card title="Import a delivery" subtitle="Supported supplier JSON, CSV, XML and synthetic EDI. PDF packing slips are reference documents only.">
      <div className="shipment-controls">
        <label>Shipment file <input aria-label="Shipment file" type="file" accept=".json,.csv,.xml,.edi" disabled={busy}
          onChange={(e) => { setFile(e.target.files[0] || null); setPreview(null); setError(''); }} /></label>
        <button className="btn" disabled={!file || busy} onClick={() => run(() => upload(true))}>Review file</button>
        {preview && <button className="btn btn-primary" disabled={busy} onClick={() => run(() => upload(false))}>Import reviewed shipments</button>}
      </div>
      {preview?.map((s) => <div key={`${s.supplier}-${s.invoice}`} className="shipment-preview">
        <h3>{s.supplier} · {s.invoice}</h3>
        <p>{s.lines.reduce((n, r) => n + r.accepted, 0)} accepted bottles. Importing does not place them on shelves.</p>
        <div className="table-wrap"><table className="table"><thead><tr><th>Medication / strength</th><th>Lot</th><th>Expiry</th><th>Ordered</th><th>Shipped</th><th>Damaged</th><th>Accepted</th></tr></thead>
          <tbody>{s.lines.map((r) => <tr key={r.line_id}><td>{r.name} {r.strength}</td><td>{r.lot}</td><td>{r.expiry}</td><td>{r.ordered}</td><td>{r.shipped}</td><td>{r.damaged}</td><td>{r.accepted}</td></tr>)}</tbody></table></div>
      </div>)}
    </Card>
    <Card title="Stocking workflow">
      <ol>
        <li>Configure the shipment’s medications and shelf regions in <Link to="/room">Room</Link>. Load a processed stocking video from <Link to="/recordings">Recordings</Link>.</li>
        <li>Pause playback and start the shipment. Accepted bottles enter inventory as staged, off-shelf stock.</li>
        <li>Select the incoming medication and lot for the next pickup, then play. CV checks its release location. Playback waits at the next unassigned pickup.</li>
        <li>Correct wrong placements or answer uncertainty alerts on the <Link to="/">Dashboard</Link>. Finish once the shipment reconciles.</li>
      </ol>
      <p className="muted">One technician and one bottle at a time. Picking from a receiving carton does not identify a medication visually; your line selection supplies that identity.</p>
      <div className="shipment-controls">
        <span>Player: {state.recording?.label || state.scenario || 'No recording'}</span>
        <button className="btn" disabled={busy || !state.scenario} onClick={() => run(() => request('/api/replay/control', { method: 'POST', body: { action: state.is_playing ? 'pause' : 'play' } }))}>{state.is_playing ? 'Pause' : 'Play'}</button>
      </div>
      {active && <p role="status">Active: {active.invoice} · Recording: {active.stocking.recording}. {active.stocking.armed ? `Line ${active.stocking.armed.line_id} selected for ${active.stocking.armed.event_id}.` : 'Select the next line, or resolve the current bottle.'}</p>}
    </Card>
    {!shipments.length && <Empty>No shipments imported yet.</Empty>}
    {shipments.map((s) => <Card key={s.id} title={`${s.supplier} · ${s.invoice}`} subtitle={`Received ${s.received_at} · ${s.reference}`} actions={<>
      <Badge tone={s.status.startsWith('completed') ? 'green' : 'blue'}>{s.status.replaceAll('_', ' ')}</Badge>
      {s.status === 'imported' && <button className="btn btn-primary" disabled={busy || state.is_playing || !!active || !state.scenario} onClick={() => run(() => post(s.id, 'start'))}>Start stocking</button>}
      {s.status === 'stocking' && <button className="btn btn-primary" disabled={busy || state.is_playing || !s.report.can_finish} onClick={() => run(() => post(s.id, 'finish'))}>Finish reconciliation</button>}
    </>}>
      <div className="table-wrap"><table className="table"><thead><tr><th>Medication / strength</th><th>Lot / expiry</th><th>Accepted</th><th>Staged</th><th>Correct</th><th>Unresolved</th><th>Short / disposed</th><th>Actions</th></tr></thead>
      <tbody>{s.report.lines.map((r) => <tr key={r.line_id}>
        <td>{r.name} {r.strength}<div className="row-sub">Line {r.line_id} · {r.units_per_bottle} {r.unit}/bottle</div></td>
        <td>{r.lot}<div className="row-sub">{r.expiry}</div></td><td>{r.accepted}</td><td>{s.status === 'imported' ? 'Not received' : r.staged}</td><td>{r.correct}</td><td>{r.unresolved}</td><td>{r.short} / {r.disposed || 0}</td>
        <td>{s.status === 'stocking' && r.staged > 0 && <div className="shipment-controls">
          <button className="btn btn-sm" disabled={busy || state.is_playing || !nextPickup || s.stocking.recording !== state.scenario || s.report.lines.some((l) => l.unresolved > 0)} onClick={() => run(() => post(s.id, 'select', { line_id: r.line_id, event_id: nextPickup.event_id }))}>Select for next pickup</button>
          <button className="btn btn-sm" disabled={busy || state.is_playing} onClick={() => setShortage({ shipment: s.id, line_id: r.line_id, max: r.staged, quantity: 1, reason: '', operation_id: crypto.randomUUID() })}>Document shortage</button>
        </div>}</td>
      </tr>)}</tbody></table></div>
      {s.report.lines.some((r) => r.unresolved > 0) && <p className="text-amber">A bottle is held, misplaced, at the counter, or awaiting confirmation. Resolve it before selecting another bottle.</p>}
      {s.stocking && <details><summary>Placement and exception evidence</summary>
        {s.report.lines.map((r) => <div key={r.line_id}><strong>Line {r.line_id}</strong>
          {r.movements.map((m) => <p key={m.session_id}>{m.session_id}: {m.state} · {m.current_location_id || 'Unknown location'}</p>)}
          {s.stocking.lines[r.line_id].exceptions.map((e) => <p key={e.operation_id}>{e.quantity} not received: {e.reason} ({e.at})</p>)}
        </div>)}
      </details>}
    </Card>)}
    {shortage && <Dialog title="Document unreceived bottles" onClose={() => !busy && setShortage(null)}>
      <p>Use this only for bottles that never arrived. Physically discarded bottles belong in the disposal workflow. This subtracts the selected bottles and their full starting units from staged stock.</p>
      {error && <p className="text-red" role="alert">{error}</p>}
      <form onSubmit={(e) => { e.preventDefault(); run(async () => { await post(shortage.shipment, 'shortage', shortage); setShortage(null); }); }}>
        <label>Bottles <input type="number" min="1" max={shortage.max} required value={shortage.quantity} onChange={(e) => setShortage({ ...shortage, quantity: Number(e.target.value) })} /></label>
        <label>Reason <textarea required maxLength={500} value={shortage.reason} onChange={(e) => setShortage({ ...shortage, reason: e.target.value })} /></label>
        <button className="btn btn-primary" disabled={busy}>Save shortage</button>
      </form>
    </Dialog>}
  </>;
}
