import React, { useEffect, useRef } from 'react';
import { Link, NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { IconContext, PillIcon, UploadSimpleIcon } from '@phosphor-icons/react';
import { LiveProvider, useLive } from './lib/live';
import { DialogProvider, useDialogs } from './lib/dialogs';
import Dashboard from './pages/Dashboard';
import Recordings from './pages/Recordings';
import Inventory from './pages/Inventory';
import Setup from './pages/Setup';

const ICONS = { size: 16, weight: 'bold' };

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
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="topbar">
        <div className="topbar-inner">
          <Link to="/" className="brand" aria-label="Pharma inventory home">
            <span className="brand-mark" aria-hidden="true">
              <PillIcon size={15} />
            </span>
            <span>Pharma</span>
          </Link>
          <nav className="nav" aria-label="Main">
            <NavLink to="/" end>
              Dashboard
              {openCount > 0 && (
                <span className="nav-count" aria-label={`${openCount} need attention`}>
                  {openCount}
                </span>
              )}
            </NavLink>
            <NavLink to="/recordings">Recordings</NavLink>
            <NavLink to="/inventory">Inventory</NavLink>
            <NavLink to="/setup">Setup</NavLink>
          </nav>
          <div className="topbar-right">
            <span
              className={`conn ${connected ? 'on' : 'off'}`}
              role="status"
              title={connected ? 'Live updates connected' : 'Reconnecting to the server…'}
            >
              <span className="conn-dot" aria-hidden="true" />
              {connected ? 'Live' : 'Reconnecting…'}
            </span>
            <button type="button" className="btn btn-primary btn-sm" onClick={openUpload}>
              <UploadSimpleIcon size={14} aria-hidden="true" /> Upload recording
            </button>
          </div>
        </div>
      </header>

      <main id="main" className="content" tabIndex={-1}>
        {!state ? (
          <div className="blank-state" role="status">
            {error ? (
              <>
                <h2>Can't reach the pharmacy server</h2>
                <p className="muted">{error}. Start the API on port 8000, then reload this page.</p>
              </>
            ) : (
              <p className="muted">Loading…</p>
            )}
          </div>
        ) : (
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/recordings" element={<Recordings />} />
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
    <IconContext.Provider value={ICONS}>
      <LiveProvider>
        <DialogProvider>
          <Shell />
        </DialogProvider>
      </LiveProvider>
    </IconContext.Provider>
  );
}
