import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { CheckCircleIcon, WarningCircleIcon, XIcon } from '@phosphor-icons/react';
import { request } from './api';
import { useLive } from './live';

const JobsContext = createContext(null);
const POLL_MS = 1200;
const TOAST_TONE = { ready: 'tone-green', notes: 'tone-amber', error: 'tone-red' };

/**
 * Tracks recordings whose skeletons are still being extracted after the upload window
 * closes, and tells the user when each one is ready (or failed).
 */
export function JobsProvider({ children }) {
  const { loadRecording } = useLive();
  const navigate = useNavigate();
  const [jobs, setJobs] = useState([]);
  const [toasts, setToasts] = useState([]);
  const known = useRef(new Set());
  const jobsRef = useRef(jobs);
  jobsRef.current = jobs;

  const track = useCallback((name, label, warnings = []) => {
    if (warnings.length) setToasts((ts) => [...ts, { id: `${name}-notes`, name, label, status: 'notes', notes: warnings }]);
    if (known.current.has(name)) return;
    known.current.add(name);
    setJobs((js) => [...js, { name, label, progress: 0 }]);
  }, []);

  // After a reload, pick up extractions that are still running on the server.
  useEffect(() => {
    request('/api/recordings')
      .then(({ recordings }) => recordings.filter((r) => r.status === 'processing').forEach((r) => track(r.name, r.label)))
      .catch(() => {});
  }, [track]);

  useEffect(() => {
    if (jobs.length === 0) return undefined;
    const timer = setInterval(async () => {
      let recordings;
      try {
        ({ recordings } = await request('/api/recordings'));
      } catch {
        return;
      }
      const byName = new Map(recordings.map((r) => [r.name, r]));
      const still = [];
      const finished = [];
      jobsRef.current.forEach((job) => {
        const r = byName.get(job.name);
        if (r && r.status !== 'ready' && r.status !== 'error') {
          still.push({ ...job, label: r.label, progress: r.progress || 0 });
          return;
        }
        known.current.delete(job.name);
        if (r) finished.push({ id: `${job.name}-${Date.now()}`, name: job.name, label: r.label, status: r.status, error: r.error });
      });
      setJobs(still);
      if (finished.length) setToasts((ts) => [...ts, ...finished]);
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [jobs.length]);

  const dismiss = (id) => setToasts((ts) => ts.filter((t) => t.id !== id));
  const open = async (toast) => {
    dismiss(toast.id);
    await loadRecording(toast.name);
    navigate('/');
  };

  const value = useMemo(() => ({ jobs, track }), [jobs, track]);
  return (
    <JobsContext.Provider value={value}>
      {children}
      <div className="toasts" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className="toast" role="status">
            <span className={`notice-icon ${TOAST_TONE[t.status]}`} aria-hidden="true">
              {t.status === 'ready' ? <CheckCircleIcon /> : <WarningCircleIcon />}
            </span>
            <div className="notice-content">
              <div className="notice-title">
                {t.status === 'ready' ? `${t.label} is ready` : t.status === 'notes' ? `Check ${t.label}` : `${t.label} failed`}
              </div>
              <p className="notice-text">
                {t.status === 'ready'
                  ? 'Skeletons are extracted. Its signals apply when it plays.'
                  : t.status === 'notes'
                    ? t.notes.join(' ')
                    : `Skeleton extraction failed: ${t.error}. Retry it from Recordings.`}
              </p>
              <div className="toast-actions">
                {t.status === 'ready' ? (
                  <button type="button" className="btn btn-primary btn-sm" onClick={() => open(t)}>
                    Open in player
                  </button>
                ) : (
                  <Link className="btn btn-sm" to="/recordings" onClick={() => dismiss(t.id)}>
                    View recordings
                  </Link>
                )}
              </div>
            </div>
            <button type="button" className="icon-btn" aria-label="Dismiss" onClick={() => dismiss(t.id)}>
              <XIcon aria-hidden="true" />
            </button>
          </div>
        ))}
      </div>
    </JobsContext.Provider>
  );
}

export function useJobs() {
  return useContext(JobsContext);
}

/** Compact top-bar status while recordings are processing in the background. */
export function JobsIndicator() {
  const { jobs } = useJobs();
  if (jobs.length === 0) return null;
  const pct = Math.round((jobs.reduce((s, j) => s + j.progress, 0) / jobs.length) * 100);
  const label = jobs.length === 1 ? jobs[0].label : `${jobs.length} recordings`;
  return (
    <Link to="/recordings" className="jobs-pill" title="Skeleton extraction running in the background">
      <span className="jobs-ring" style={{ '--pct': `${pct}%` }} aria-hidden="true" />
      <span className="truncate">Processing {label}</span>
      <span className="num muted">{pct}%</span>
    </Link>
  );
}
