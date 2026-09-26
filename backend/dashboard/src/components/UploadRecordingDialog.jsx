import React, { useEffect, useState } from 'react';
import { Upload } from 'lucide-react';
import { useLive } from '../lib/live';
import { errorMessage, request } from '../lib/api';
import { Dialog } from './ui';

const EXAMPLE = 'time_s,event\n2.4,pickup\n7.9,release';

export default function UploadRecordingDialog({ onClose }) {
  const { loadScenario } = useLive();
  const [video, setVideo] = useState(null);
  const [events, setEvents] = useState(null);
  const [name, setName] = useState('');
  const [phase, setPhase] = useState('form'); // form | uploading | processing | done
  const [job, setJob] = useState(null);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (phase !== 'processing' || !job) return undefined;
    const timer = setInterval(async () => {
      try {
        const { scenarios } = await request('/api/scenarios');
        const s = scenarios.find((x) => x.name === job.name);
        if (!s) return;
        setProgress(s.progress ?? 0);
        if (s.status === 'error') {
          setError(`Skeleton extraction failed: ${s.error}`);
          setPhase('form');
        } else if (s.status === 'ready') {
          await loadScenario(job.name);
          setPhase('done');
        }
      } catch (e) {
        setError(e.message);
      }
    }, 700);
    return () => clearInterval(timer);
  }, [phase, job, loadScenario]);

  const submit = async (e) => {
    e.preventDefault();
    setError(null);
    setPhase('uploading');
    const body = new FormData();
    body.append('video', video);
    body.append('events', events);
    if (name.trim()) body.append('name', name.trim());
    try {
      const res = await fetch('/api/recordings', { method: 'POST', body });
      if (!res.ok) throw new Error(await errorMessage(res));
      setJob(await res.json());
      setProgress(0);
      setPhase('processing');
    } catch (err) {
      setError(err.message);
      setPhase('form');
    }
  };

  const busy = phase === 'uploading' || phase === 'processing';

  return (
    <Dialog
      title="Upload recording"
      onClose={onClose}
      width={500}
      footer={
        phase === 'done' ? (
          <button className="btn btn-primary" onClick={onClose}>
            Done
          </button>
        ) : (
          <>
            <button className="btn btn-ghost" onClick={onClose}>
              {busy ? 'Close (keeps processing)' : 'Cancel'}
            </button>
            <button className="btn btn-primary" type="submit" form="upload-form" disabled={!video || !events || busy}>
              <Upload size={15} /> Upload and process
            </button>
          </>
        )
      }
    >
      {phase === 'done' ? (
        <div className="form">
          <p className="lead">
            <strong>{job.label}</strong> is loaded with {job.events} timestamp{job.events === 1 ? '' : 's'}. Press Play on
            the dashboard to run it.
          </p>
          {job.warnings?.map((w) => (
            <p key={w} className="form-error">
              {w}
            </p>
          ))}
        </div>
      ) : (
        <form id="upload-form" className="form" onSubmit={submit}>
          <label className="field">
            <span className="label">Video</span>
            <input className="file-input" type="file" accept="video/*,.mp4,.mov,.m4v,.avi,.mkv,.webm" disabled={busy} onChange={(e) => setVideo(e.target.files[0] || null)} />
          </label>
          <label className="field">
            <span className="label">Pickup / release timestamps</span>
            <input className="file-input" type="file" accept=".csv,.json,.jsonl,.txt" disabled={busy} onChange={(e) => setEvents(e.target.files[0] || null)} />
            <span className="hint">
              CSV, JSON or JSONL with a time (<code>time_s</code> or <code>media_time_ms</code>) and an event
              (<code>pickup</code>/<code>grab</code> or <code>release</code>/<code>drop</code>). Example:
            </span>
            <pre className="code-sample">{EXAMPLE}</pre>
          </label>
          <label className="field">
            <span className="label">Name</span>
            <input className="input" placeholder="Optional, defaults to the file name" value={name} disabled={busy} onChange={(e) => setName(e.target.value)} />
          </label>
          {busy && (
            <div className="field">
              <span className="hint">
                {phase === 'uploading' ? 'Uploading…' : `Running pose estimation… ${Math.round(progress * 100)}%`}
              </span>
              <div className="timeline-track">
                <div className="timeline-fill" style={{ width: `${phase === 'uploading' ? 5 : Math.max(5, progress * 100)}%` }} />
              </div>
            </div>
          )}
          {error && <p className="form-error">{error}</p>}
        </form>
      )}
    </Dialog>
  );
}
