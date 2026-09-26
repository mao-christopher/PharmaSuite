import React, { useEffect, useRef, useState } from 'react';
import { CrosshairIcon, PlusIcon, TrashIcon, UploadSimpleIcon } from '@phosphor-icons/react';
import { useJobs } from '../lib/jobs';
import { errorMessage } from '../lib/api';
import { formatMs, plural } from '../lib/format';
import { Dialog } from './ui';

const EXAMPLE = 'time_s,event\n2.4,pickup\n7.9,release';
const MODES = [
  { id: 'manual', label: 'Enter manually' },
  { id: 'file', label: 'Upload file' },
];
const EVENT_LABELS = { pickup: 'Pickup', release: 'Put-down' };

let nextRowId = 1;
const row = (event, time = '') => ({ id: nextRowId++, event, time });

function rowIssues(rows, durationS) {
  const issues = new Map();
  rows.forEach((r) => {
    const t = Number(r.time);
    if (r.time === '' || Number.isNaN(t)) issues.set(r.id, 'Enter a time in seconds.');
    else if (t < 0) issues.set(r.id, 'Time cannot be negative.');
    else if (durationS != null && t > durationS) issues.set(r.id, `Past the end of the video (${durationS.toFixed(1)}s).`);
  });
  return issues;
}

function sequenceWarning(rows) {
  const sorted = [...rows].filter((r) => r.time !== '').sort((a, b) => Number(a.time) - Number(b.time));
  for (let i = 0; i < sorted.length; i += 1) {
    const expected = i % 2 === 0 ? 'pickup' : 'release';
    if (sorted[i].event !== expected) {
      return 'In time order, signals should alternate pickup, put-down, pickup… A put-down with no pickup before it is flagged for reconciliation.';
    }
  }
  return null;
}

function toCsv(rows) {
  const sorted = [...rows].sort((a, b) => Number(a.time) - Number(b.time));
  return ['time_s,event', ...sorted.map((r) => `${Number(r.time)},${r.event}`)].join('\n') + '\n';
}

function ManualTimestamps({ rows, setRows, videoRef, videoUrl, previewFailed, currentS, durationS, disabled, issues }) {
  const add = (event, time) => setRows((rs) => [...rs, row(event, time)]);
  const mark = (event) => {
    const time = Math.round(currentS * 10) / 10;
    setRows((rs) => [...rs.filter((r) => r.time !== '' || r.event !== event), row(event, String(time))]
      .sort((a, b) => (a.time === '' ? 1 : b.time === '' ? -1 : Number(a.time) - Number(b.time))));
  };
  const update = (id, change) => setRows((rs) => rs.map((r) => (r.id === id ? { ...r, ...change } : r)));
  const remove = (id) => setRows((rs) => rs.filter((r) => r.id !== id));
  const nextEvent = rows.length && rows[rows.length - 1].event === 'pickup' ? 'release' : 'pickup';
  const warning = sequenceWarning(rows);

  return (
    <div className="field">
      {videoUrl && previewFailed ? (
        <span className="hint text-amber">
          This browser can't preview this video's format (common for OpenCV-written MP4s). Type the times below; the server
          still reads the file.
        </span>
      ) : videoUrl ? (
        <>
          <video ref={videoRef} className="upload-preview" src={videoUrl} controls muted playsInline preload="metadata" />
          <div className="mark-bar">
            <span className="timeline-time">
              {formatMs(currentS * 1000)}
              {durationS != null && ` / ${formatMs(durationS * 1000)}`}
            </span>
            <button type="button" className="btn btn-sm" disabled={disabled} onClick={() => mark('pickup')}>
              <CrosshairIcon size={13} aria-hidden="true" /> Mark pickup here
            </button>
            <button type="button" className="btn btn-sm" disabled={disabled} onClick={() => mark('release')}>
              <CrosshairIcon size={13} aria-hidden="true" /> Mark put-down here
            </button>
          </div>
          <span className="hint">Scrub the video to the moment of each grab or release and mark it, or type times below.</span>
        </>
      ) : (
        <span className="hint">Choose a video to scrub through it and mark signals, or type the times below.</span>
      )}

      {rows.length > 0 && (
        <table className="table table-inner signal-rows">
          <thead>
            <tr>
              <th scope="col">Signal</th>
              <th scope="col">Time (seconds)</th>
              <th scope="col">
                <span className="sr-only">Row actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={r.id}>
                <td>
                  <select
                    className="input input-sm"
                    name={`event-${i}`}
                    aria-label={`Signal ${i + 1} type`}
                    value={r.event}
                    disabled={disabled}
                    onChange={(e) => update(r.id, { event: e.target.value })}
                  >
                    <option value="pickup">{EVENT_LABELS.pickup}</option>
                    <option value="release">{EVENT_LABELS.release}</option>
                  </select>
                </td>
                <td>
                  <input
                    className="input input-sm"
                    type="number"
                    name={`time-${i}`}
                    autoComplete="off"
                    inputMode="decimal"
                    step="0.1"
                    min="0"
                    placeholder="2.4…"
                    aria-label={`Signal ${i + 1} time in seconds`}
                    aria-invalid={issues.has(r.id) && r.time !== '' ? true : undefined}
                    value={r.time}
                    disabled={disabled}
                    onChange={(e) => update(r.id, { time: e.target.value })}
                  />
                  {issues.has(r.id) && r.time !== '' && <div className="row-sub text-red">{issues.get(r.id)}</div>}
                </td>
                <td className="actions">
                  {videoUrl && !previewFailed && (
                    <button
                      type="button"
                      className="icon-btn"
                      aria-label={`Show signal ${i + 1} in the video`}
                      title="Jump the preview to this time"
                      disabled={issues.has(r.id)}
                      onClick={() => {
                        if (videoRef.current) videoRef.current.currentTime = Number(r.time);
                      }}
                    >
                      <CrosshairIcon aria-hidden="true" />
                    </button>
                  )}
                  <button type="button" className="icon-btn" aria-label={`Remove signal ${i + 1}`} disabled={disabled} onClick={() => remove(r.id)}>
                    <TrashIcon aria-hidden="true" />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="add-med-footer">
        <button type="button" className="btn btn-sm" disabled={disabled} onClick={() => add(nextEvent)}>
          <PlusIcon size={13} aria-hidden="true" /> Add {EVENT_LABELS[nextEvent].toLowerCase()}
        </button>
        <span className="hint">{plural(rows.filter((r) => !issues.has(r.id)).length, 'signal')} ready</span>
      </div>
      {warning && <p className="hint text-amber">{warning}</p>}
    </div>
  );
}

export default function UploadRecordingDialog({ onClose, onUploaded }) {
  const { track } = useJobs();
  const videoRef = useRef(null);
  const [video, setVideo] = useState(null);
  const [videoUrl, setVideoUrl] = useState(null);
  const [currentS, setCurrentS] = useState(0);
  const [durationS, setDurationS] = useState(null);
  const [previewFailed, setPreviewFailed] = useState(false);
  const [mode, setMode] = useState('manual');
  const [events, setEvents] = useState(null);
  const [rows, setRows] = useState(() => [row('pickup'), row('release')]);
  const [name, setName] = useState('');
  const [phase, setPhase] = useState('form'); // form | uploading
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!video) return undefined;
    const url = URL.createObjectURL(video);
    setVideoUrl(url);
    setCurrentS(0);
    setDurationS(null);
    setPreviewFailed(false);
    return () => URL.revokeObjectURL(url);
  }, [video]);

  useEffect(() => {
    const el = videoRef.current;
    if (!el) return undefined;
    const onTime = () => setCurrentS(el.currentTime || 0);
    const onMeta = () => Number.isFinite(el.duration) && setDurationS(el.duration);
    const onError = () => setPreviewFailed(true);
    el.addEventListener('error', onError);
    el.addEventListener('timeupdate', onTime);
    el.addEventListener('seeked', onTime);
    el.addEventListener('loadedmetadata', onMeta);
    if (el.readyState >= 1) onMeta();
    return () => {
      el.removeEventListener('timeupdate', onTime);
      el.removeEventListener('seeked', onTime);
      el.removeEventListener('loadedmetadata', onMeta);
      el.removeEventListener('error', onError);
    };
  }, [videoUrl, mode]);

  const issues = rowIssues(rows, durationS);

  const submit = async (e) => {
    e.preventDefault();
    let eventsFile = events;
    if (!video) return setError('Choose a video.');
    if (mode === 'file' && !events) return setError('Choose a timestamps file, or switch to Enter manually.');
    if (mode === 'manual') {
      const filled = rows.filter((r) => r.time !== '');
      if (filled.length === 0) return setError('Add at least one pickup or put-down time.');
      if (filled.some((r) => issues.has(r.id))) return setError('Fix the highlighted times first.');
      eventsFile = new File([toCsv(filled)], 'manual-timestamps.csv', { type: 'text/csv' });
    }
    setError(null);
    setPhase('uploading');
    videoRef.current?.pause();
    const body = new FormData();
    body.append('video', video);
    body.append('events', eventsFile);
    if (name.trim()) body.append('name', name.trim());
    try {
      const res = await fetch('/api/recordings', { method: 'POST', body });
      if (!res.ok) throw new Error(await errorMessage(res));
      const job = await res.json();
      // Skeletons are extracted in the background; the window moves on to the camera view.
      track(job.name, job.label);
      onUploaded(job);
    } catch (err) {
      setError(err.message);
      setPhase('form');
    }
    return undefined;
  };

  const busy = phase === 'uploading';

  return (
    <Dialog
      title="Upload recording"
      onClose={onClose}
      width={600}
      footer={
        <>
          <button type="button" className="btn btn-ghost" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button className="btn btn-primary" type="submit" form="upload-form" disabled={busy}>
            <UploadSimpleIcon size={14} aria-hidden="true" />
            {busy ? 'Uploading…' : 'Upload and continue'}
          </button>
        </>
      }
    >
      {(
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

          <fieldset className="field" aria-labelledby="upload-times-label">
            <div className="field-head">
              <span id="upload-times-label" className="label">
                Pickup and put-down times
              </span>
              <div className="segmented" role="group" aria-label="How to provide times">
                {MODES.map((m) => (
                  <button
                    key={m.id}
                    type="button"
                    className={mode === m.id ? 'active' : ''}
                    aria-pressed={mode === m.id}
                    disabled={busy}
                    onClick={() => setMode(m.id)}
                  >
                    {m.label}
                  </button>
                ))}
              </div>
            </div>
            {mode === 'manual' ? (
              <ManualTimestamps
                rows={rows}
                setRows={setRows}
                videoRef={videoRef}
                videoUrl={videoUrl}
                previewFailed={previewFailed}
                currentS={currentS}
                durationS={durationS}
                disabled={busy}
                issues={issues}
              />
            ) : (
              <>
                <input
                  className="file-input"
                  type="file"
                  name="events"
                  aria-label="Timestamps file"
                  accept=".csv,.json,.jsonl,.txt"
                  disabled={busy}
                  onChange={(e) => setEvents(e.target.files[0] || null)}
                />
                <span className="hint">
                  CSV, JSON or JSONL with a time (<code>time_s</code> or <code>media_time_ms</code>) and an event (
                  <code>pickup</code>/<code>grab</code> or <code>release</code>/<code>drop</code>). For example:
                </span>
                <pre className="code-sample">{EXAMPLE}</pre>
              </>
            )}
          </fieldset>

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
          <p className="hint">
            After the upload you'll confirm which camera view it uses. Pose estimation runs in the background; you'll get
            a notice when it's ready.
          </p>
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
