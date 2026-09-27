import React, { useCallback, useEffect, useRef, useState } from 'react';
import { CubeIcon, XIcon } from '@phosphor-icons/react';
import { request } from '../lib/api';
import { useJobs } from '../lib/jobs';
import { formatDateTime } from '../lib/format';
import { Badge } from './ui';

const url = (name) => `/api/recordings/${encodeURIComponent(name)}/render`;

/**
 * The recording's render status from the server, refreshed while its render is queued or
 * running. `initial` (from the recordings list) avoids a request per row.
 */
export function useRender(name, initial, refreshKey) {
  const { renders } = useJobs();
  const [render, setRender] = useState(initial || null);
  const reload = useCallback(
    () => request(url(name)).then(setRender).catch(() => {}),
    [name],
  );
  const job = renders.find((r) => r.name === name);
  useEffect(() => {
    if (initial) setRender(initial);
  }, [initial]);
  useEffect(() => {
    if (!initial) reload();
  }, [reload, initial, refreshKey]);
  // While the job is active, the jobs poll carries its progress; reload when it ends.
  const active = Boolean(job);
  const wasActive = useRef(active);
  useEffect(() => {
    if (wasActive.current && !active) reload();
    wasActive.current = active;
  }, [active, reload]);
  if (job && render) return [{ ...render, state: job.state, progress: job.progress, step: job.step, queue_position: job.queue_position }, reload];
  return [render, reload];
}

/**
 * "Render simulation": export the timeline and queue a Unity re-enactment. Disabled with
 * the reason when the server has no Unity editor or the recording can't be re-enacted
 * yet. A render whose inputs changed (for example an employee correction) is stale and
 * offers "Re-render"; the same inputs reuse the finished render.
 */
export default function RenderButton({ name, initial, refreshKey, onDone, size = 'sm', showReason = false }) {
  const { trackRender } = useJobs();
  const [render, reload] = useRender(name, initial, refreshKey);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const act = async (method) => {
    setBusy(true);
    setError(null);
    try {
      await request(url(name), { method });
      await trackRender();
      await reload();
      onDone?.();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  if (!render) return null;
  const btn = `btn btn-${size}`;
  const { state } = render;

  if (state === 'queued' || state === 'running') {
    const pct = Math.round((render.progress || 0) * 100);
    return (
      <span className="render-status">
        <Badge tone="blue">
          {state === 'queued' ? `Queued${render.queue_position > 1 ? ` (${render.queue_position})` : ''}` : `Rendering ${pct}%`}
        </Badge>
        <button type="button" className="icon-btn" aria-label="Cancel render" title="Cancel render" disabled={busy} onClick={() => act('DELETE')}>
          <XIcon aria-hidden="true" />
        </button>
      </span>
    );
  }

  const label = state === 'stale' ? 'Re-render' : state === 'failed' ? 'Retry render' : 'Render simulation';
  const title = render.reason
    || (state === 'done' ? `Rendered ${formatDateTime(render.rendered_at)} from the current decisions` : undefined)
    || (state === 'stale' ? 'Decisions, the room or the camera changed since this render.' : undefined)
    || 'Re-enact this recording in Unity from its floor track and decisions';
  return (
    <span className="render-status">
      {state === 'done' && <Badge tone="green" title={title}>Simulation ready</Badge>}
      {state === 'stale' && <Badge tone="amber" title={title}>Simulation out of date</Badge>}
      {state === 'failed' && <Badge tone="red" title={render.error || undefined}>Render failed</Badge>}
      {state !== 'done' && (
        <button type="button" className={btn} disabled={!render.can_render || busy} title={title} onClick={() => act('POST')}>
          <CubeIcon size={13} aria-hidden="true" /> {busy ? 'Starting…' : label}
        </button>
      )}
      {showReason && render.reason && state !== 'done' && <span className="hint">{render.reason}</span>}
      {error && <span className="form-error">{error}</span>}
    </span>
  );
}
