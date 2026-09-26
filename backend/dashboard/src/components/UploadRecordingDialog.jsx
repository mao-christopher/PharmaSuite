import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { UploadSimpleIcon } from '@phosphor-icons/react';
import { useLive } from '../lib/live';
import { errorMessage, request } from '../lib/api';
import { plural } from '../lib/format';
import { Dialog } from './ui';

const EXAMPLE = 'time_s,event\n2.4,pickup\n7.9,release';

export default function UploadRecordingDialog({ onClose }) {
  const { loadRecording } = useLive();
  const navigate = useNavigate();
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
        const { recordings } = await request('/api/recordings');
        const r = recordings.find((x) => x.name === job.name);
        if (!r) return;
        setProgress(r.progress ?? 0);
        if (r.status === 'error') {
          setError(`Skeleton extraction failed: ${r.error}. The recording is saved; retry it from Recordings.`);
          setPhase('form');
        } else if (r.status === 'ready') {
          await loadRecording(job.name);
          setPhase('done');
        }
      } catch (e) {
        setError(e.message);
      }
    }, 700);
    return () => clearInterval(timer);
  }, [phase, job, loadRecording]);

  const submit = async (e) => {
    e.preventDefault();
    if (!video || !events) {
      setError('Choose both a video and a timestamps file.');
      return;
    }
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
      width={520}
      footer={
        phase === 'done' ? (
          <>
            <button type="button" className="btn btn-ghost" onClick={onClose}>
              Close
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => {
                onClose();
                navigate('/');
              }}
            >
              Go to player
            </button>
          </>
        ) : (
          <>
            <button type="button" className="btn btn-ghost" onClick={onClose}>
              {busy ? 'Close, keep processing' : 'Cancel'}
            </button>
            <button className="btn btn-primary" type="submit" form="upload-form" disabled={busy}>
              <UploadSimpleIcon size={14} aria-hidden="true" />
              {phase === 'uploading' ? 'Uploading…' : phase === 'processing' ? 'Processing…' : 'Upload and process'}
            </button>
          </>
        )
      }
    >
      {phase === 'done' ? (
        <div className="form" role="status">
          <p className="lead">
            <strong>{job.label}</strong> is stored and open in the player with {plural(job.events, 'signal')}. Its signals
            update inventory when it plays, or apply them without playing from Recordings.
          </p>
          {job.warnings?.map((w) => (
            <p key={w} className="banner banner-amber">
              {w}
            </p>
          ))}
        </div>
      ) : (
        <form id="upload-form" className="form" onSubmit={submit} noValidate>
          <label className="field">
            <span className="label">Video</span>
            <input
              className="file-input"
              type="file"
              name="video"
              accept="video/*,.mp4,.mov,.m4v,.avi,.mkv,.webm"
              disabled={busy}
              onChange={(e) => setVideo(e.target.files[0] || null)}
            />
          </label>
          <label className="field">
            <span className="label">Pickup and put-down timestamps</span>
            <input
              className="file-input"
              type="file"
              name="events"
              accept=".csv,.json,.jsonl,.txt"
              disabled={busy}
              onChange={(e) => setEvents(e.target.files[0] || null)}
            />
            <span className="hint">
              CSV, JSON or JSONL with a time (<code>time_s</code> or <code>media_time_ms</code>) and an event (
              <code>pickup</code>/<code>grab</code> or <code>release</code>/<code>drop</code>). For example:
            </span>
            <pre className="code-sample">{EXAMPLE}</pre>
          </label>
          <label className="field">
            <span className="label">Name (optional)</span>
            <input
              className="input"
              name="recording-name"
              autoComplete="off"
              placeholder="Morning restock…"
              value={name}
              disabled={busy}
              onChange={(e) => setName(e.target.value)}
            />
            <span className="hint">Defaults to the video's file name.</span>
          </label>
          {busy && (
            <div className="field" role="status">
              <span className="hint">
                {phase === 'uploading' ? 'Uploading…' : `Running pose estimation… ${Math.round(progress * 100)}%`}
              </span>
              <div className="progress">
                <div className="progress-fill" style={{ width: `${phase === 'uploading' ? 5 : Math.max(5, progress * 100)}%` }} />
              </div>
            </div>
          )}
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
        </form>
      )}
    </Dialog>
  );
}
