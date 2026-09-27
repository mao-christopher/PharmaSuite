import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { CheckCircleIcon, WarningCircleIcon, XIcon } from '@phosphor-icons/react';
import { request } from './api';
import { useLive } from './live';

const JobsContext = createContext(null);
const POLL_MS = 1200;
const TOAST_TONE = { ready: 'tone-green', notes: 'tone-amber', error: 'tone-red' };

/**
 * Tracks background work and tells the user when each job is ready (or failed):
 * recordings whose skeletons are still being extracted after the upload window closes,
 * and simulation renders (one runs at a time on the server; the rest wait).
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

  // Simulation renders: the server's queue is the source of truth.
  const [renders, setRenders] = useState([]);
  const rendersRef = useRef(renders);
  rendersRef.current = renders;
  const pollRenders = useCallback(async () => {
    let active;
    try {
      ({ jobs: active } = await request('/api/renders'));
    } catch {
      return;
    }
    const still = new Set(active.map((j) => j.name));
    const ended = rendersRef.current.filter((j) => !still.has(j.name));
    setRenders(active);
    for (const job of ended) {
      try {
        const r = await request(`/api/recordings/${encodeURIComponent(job.name)}/render`);
        if (r.state === 'done' || r.state === 'failed') {
          setToasts((ts) => [...ts, { id: `${job.name}-render-${Date.now()}`, name: job.name, label: job.label, kind: 'render',
            status: r.state === 'done' ? 'ready' : 'error', error: r.error }]);
        }
      } catch {
        /* the recording was deleted */
      }
    }
  }, []);
  const trackRender = useCallback(() => pollRenders(), [pollRenders]);
  useEffect(() => {
    pollRenders();
  }, [pollRenders]);
  useEffect(() => {
    if (renders.length === 0) return undefined;
    const timer = setInterval(pollRenders, POLL_MS);
    return () => clearInterval(timer);
  }, [renders.length, pollRenders]);

  const value = useMemo(() => ({ jobs, track, renders, trackRender }), [jobs, track, renders, trackRender]);
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
                {t.kind === 'render'
                  ? t.status === 'ready' ? `Simulation of ${t.label} is ready` : `Simulation of ${t.label} failed`
                  : t.status === 'ready' ? `${t.label} is ready` : t.status === 'notes' ? `Check ${t.label}` : `${t.label} failed`}
              </div>
              <p className="notice-text">
                {t.kind === 'render'
                  ? t.status === 'ready'
                    ? 'Switch the player to Simulation or Side by side to watch it.'
                    : `Unity render failed: ${t.error}. Retry it from Recordings.`
                  : t.status === 'ready'
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

/** Compact top-bar status while recordings are processing or rendering in the background. */
export function JobsIndicator() {
  const { jobs, renders } = useJobs();
  const all = [...jobs, ...renders];
  if (all.length === 0) return null;
  const pct = Math.round((all.reduce((s, j) => s + (j.progress || 0), 0) / all.length) * 100);
  const queued = renders.filter((r) => r.state === 'queued').length;
  let label;
  if (all.length > 1) label = `${all.length} jobs`;
  else if (jobs.length) label = `Processing ${jobs[0].label}`;
  else label = `${renders[0].state === 'queued' ? 'Queued' : 'Rendering'} ${renders[0].label}`;
  const title = [
    jobs.length && `Skeleton extraction: ${jobs.map((j) => j.label).join(', ')}`,
    renders.length && `Simulation renders (one at a time${queued ? `, ${queued} waiting` : ''}): ${renders.map((r) => r.label).join(', ')}`,
  ].filter(Boolean).join('. ');
  return (
    <Link to="/recordings" className="jobs-pill" title={title}>
      <span className="jobs-ring" style={{ '--pct': `${pct}%` }} aria-hidden="true" />
      <span className="truncate">{label}</span>
      <span className="num muted">{pct}%</span>
    </Link>
  );
}
