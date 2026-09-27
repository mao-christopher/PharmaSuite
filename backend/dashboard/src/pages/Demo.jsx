import React, { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { request } from '../lib/api';
import { Card, PageHeader, Badge } from '../components/ui';
import RenderButton from '../components/RenderButton';

export default function Demo() {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [signals, setSignals] = useState([]);
  const [busy, setBusy] = useState(false);
  const player = useRef(null);
  const refresh = () => request('/api/demo').then(setData).catch(e => setError(e.message));
  useEffect(() => { refresh(); const t = setInterval(refresh, 5000); return () => clearInterval(t); }, []);
  const mark = event => setSignals(s => [...s, { event, time_s: Math.round((player.current?.currentTime || 0) * 100) / 100 }]);
  const save = async () => {
    setBusy(true); setError('');
    try {
      await request('/api/demo/prepare', { method: 'POST', body: { signals } });
      await refresh();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  const rec = data?.recording_summary;
  const roomLink = data ? `/room?room=${encodeURIComponent(data.room_id)}` : '/room';
  return <>
    <PageHeader title="Build your room demo" subtitle="9_27_2026 2.glb + IMG_3537.MOV" />
    {error && <p role="alert" className="form-error">{error}</p>}
    {data && <div style={{ display: 'grid', gap: 20 }}>
      <Card title="1. Mark bottle actions" subtitle="Scrub to each grab or release. These times tell the demo when an action occurred; shelf locations come from your room setup.">
        <video ref={player} src={data.video_url} controls muted playsInline preload="metadata" style={{ width: '100%', maxHeight: '55vh', background: '#111', borderRadius: 8 }} />
        {rec ? <><p><Badge tone="green">Saved</Badge> {rec.label} · {rec.status}{rec.status === 'processing' ? ` ${Math.round((rec.progress || 0) * 100)}%` : ''}</p>
          {data.annotation_source && <p className="hint">Actions annotated from video using Python frame extraction and visual review; approximate timing ±0.25 seconds. These are not wristband measurements.</p>}
          <div className="page-actions" style={{ flexWrap: 'wrap' }}>{data.signals?.map((s, i) => <button className="btn btn-sm" key={i} onClick={() => { if (player.current) { player.current.currentTime = Math.max(0, s.time_s - 0.5); player.current.pause(); } }}>{s.time_s.toFixed(2)}s · {s.event === 'pickup' ? 'Pickup' : 'Put-down'}</button>)}</div>
        </> : <>
          <div className="page-actions" style={{ margin: '12px 0' }}>
            <button className="btn" disabled={busy} onClick={() => mark('pickup')}>Mark pickup here</button>
            <button className="btn" disabled={busy} onClick={() => mark('release')}>Mark put-down here</button>
          </div>
          <table className="table"><thead><tr><th>Action</th><th>Seconds</th><th /></tr></thead><tbody>
            {signals.map((s, i) => <tr key={i}><td>{s.event === 'pickup' ? 'Pickup' : 'Put-down'}</td><td><input className="input" aria-label={`Time for action ${i + 1}`} type="number" min="0" step="0.01" value={s.time_s} disabled={busy} onChange={e => setSignals(items => items.map((v, n) => n === i ? { ...v, time_s: e.target.value } : v))} /></td><td><button className="btn btn-sm" disabled={busy} onClick={() => setSignals(items => items.filter((_, n) => n !== i))}>Remove</button></td></tr>)}
          </tbody></table>
          <button className="btn btn-primary" disabled={busy || !signals.length || signals.some(s => s.time_s === '' || !Number.isFinite(Number(s.time_s)) || Number(s.time_s) < 0)} onClick={save}>{busy ? 'Preparing…' : 'Save times and extract skeleton'}</button>
        </>}
      </Card>
      <Card title="2. Draw shelves, counter and disposal" subtitle="Place boxes on the supplied 3D scan. Assign each shelf its demo medication, then save the regions.">
        <p>{data.regions} regions saved.</p><Link className="btn btn-primary" to={roomLink}>Draw boxes on this room</Link>
      </Card>
      <Card title="3. Align the video camera" subtitle="Match at least six visible points between the scan and video. Spread them across different heights and depths so motion lands in the right place.">
        <p>{data.camera_registered ? 'Camera registered.' : 'Camera registration pending.'}</p>
        {rec ? <Link className="btn" to={`${roomLink}&tab=cameras&view=${encodeURIComponent(rec.layout_id)}`}>Align IMG_3537 camera</Link> : <p className="muted">Save the action times first to create this camera view.</p>}
      </Card>
      <Card title="4. Review and render" subtitle="Review the recording and apply its signals once. Resolve uncertain placements before rendering the bottle actions.">
        <p>The simulation follows the tracked person, your rebuilt room, and confirmed bottle movements. Hidden or uncertain motion stays marked as uncertain.</p>
        {rec && <div className="page-actions"><Link className="btn" to="/recordings">Review recording</Link><RenderButton name={rec.name} initial={rec.render} showReason /></div>}
        {data.renderer_reason && <p className="hint text-amber">{data.renderer_reason}</p>}
      </Card>
    </div>}
  </>;
}
