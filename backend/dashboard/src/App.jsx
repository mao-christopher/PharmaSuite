import React, { useEffect, useRef, useState } from 'react';
import { NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { Pill, Upload } from 'lucide-react';
import { LiveProvider, useLive } from './lib/live';
import { DialogProvider, useDialogs } from './lib/dialogs';
import { request } from './lib/api';
import Dashboard from './pages/Dashboard';
import Inventory from './pages/Inventory';
import Setup from './pages/Setup';

const STATUS_SUFFIX = { processing: ' (processing…)', unprocessed: ' (needs skeletons)', error: ' (failed)' };

function ScenarioPicker() {
  const { state, loadScenario } = useLive();
  const [scenarios, setScenarios] = useState([]);
  const [error, setError] = useState(null);
  const busy = scenarios.some((s) => s.status === 'processing');

  useEffect(() => {
    let alive = true;
    const load = () =>
      request('/api/scenarios')
        .then((r) => alive && setScenarios(r.scenarios))
        .catch(() => alive && setScenarios([]));
    load();
    const timer = busy ? setInterval(load, 1500) : null;
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [state?.scenario, busy]);

  if (scenarios.length === 0) return null;
  return (
    <label className="scenario-picker" title={error || undefined}>
      <span className="muted">Recording</span>
      <select
        className="input input-sm"
        value={state?.scenario || ''}
        onChange={(e) => loadScenario(e.target.value).then(() => setError(null), (err) => setError(err.message))}
      >
        {!state?.scenario && <option value="">Choose…</option>}
        {scenarios.map((s) => (
          <option key={s.name} value={s.name} disabled={s.status !== 'ready'}>
            {s.label}
            {STATUS_SUFFIX[s.status] || ''}
          </option>
        ))}
      </select>
    </label>
  );
}

function Shell() {
  const { state, connected, error } = useLive();
  const { openDisposal, openUpload } = useDialogs();
  const location = useLocation();
  const seenDisposals = useRef(new Set());

  const pending = state ? Object.values(state.disposals).filter((d) => d.status === 'pending_employee_entry') : [];
  const openCount = state ? Object.values(state.alerts).filter((a) => a.status === 'open').length + pending.length : 0;

  useEffect(() => {
    const fresh = pending.find((d) => !seenDisposals.current.has(d.disposal_id));
    seenDisposals.current = new Set(pending.map((d) => d.disposal_id));
    if (fresh && !location.pathname.startsWith('/setup')) openDisposal(fresh.disposal_id);
  }, [state?.disposals]);

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-inner">
          <div className="brand">
            <Pill size={18} />
            <span>Pharma</span>
          </div>
          <nav className="nav">
            <NavLink to="/" end>
              Dashboard
              {openCount > 0 && <span className="nav-count">{openCount}</span>}
            </NavLink>
            <NavLink to="/inventory">Inventory</NavLink>
            <NavLink to="/setup">Setup</NavLink>
          </nav>
          <div className="topbar-right">
            <ScenarioPicker />
            <button className="btn btn-sm" onClick={openUpload}>
              <Upload size={14} /> Upload
            </button>
            <span className={`conn ${connected ? 'on' : 'off'}`} title={connected ? 'Live updates connected' : 'Reconnecting…'}>
              <span className="conn-dot" />
              {connected ? 'Live' : 'Offline'}
            </span>
          </div>
        </div>
      </header>

      <main className="content">
        {!state ? (
          <div className="blank-state">
            {error ? (
              <>
                <h2>Can't load the pharmacy state</h2>
                <p className="muted">{error}. Check that the API server is running on port 8000 and a recording is loaded.</p>
              </>
            ) : (
              <p className="muted">Loading…</p>
            )}
          </div>
        ) : (
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/inventory" element={<Inventory />} />
            <Route path="/setup" element={<Setup />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        )}
      </main>
    </div>
  );
}

export default function App() {
  return (
    <LiveProvider>
      <DialogProvider>
        <Shell />
      </DialogProvider>
    </LiveProvider>
  );
}
