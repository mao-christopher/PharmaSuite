import React, { Fragment, useCallback, useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { CaretDownIcon, CaretRightIcon, FilmStripIcon, PlayIcon, TrashIcon, UploadSimpleIcon, VideoCameraIcon } from '@phosphor-icons/react';
import { useLive } from '../lib/live';
import { useDialogs } from '../lib/dialogs';
import { request } from '../lib/api';
import { ALERT_TYPES, NONE, appliedState, formatDateTime, formatMs, medLabel, plural } from '../lib/format';
import { SignalTable, useResolver } from '../components/ActivityLog';
import { Badge, Card, ConfirmDialog, Empty, EmptyState, PageHeader } from '../components/ui';
import RenderButton from '../components/RenderButton';

const FILTERS = [
  { id: 'all', label: 'All' },
  { id: 'pending', label: 'To apply' },
  { id: 'applied', label: 'Applied' },
];

const isLive = (r) => r.source === 'live';
// Live clips are applied once when they arrive, never from here.
const isPending = (r) => !isLive(r) && r.status === 'ready' && r.events_applied < r.events_total;

function matches(filter, r) {
  if (filter === 'pending') return !isLive(r) && (r.status !== 'ready' || r.events_applied < r.events_total);
  if (filter === 'applied') return isLive(r) || (r.status === 'ready' && r.events_total > 0 && r.events_applied === r.events_total);
  return true;
}

/**
 * Library rows, newest first: uploads and live sessions (each grouping its clips, oldest
 * first, numbered by movement), then bundled fixtures.
 */
function libraryRows(recordings) {
  const sessions = new Map();
  const rows = [];
  recordings.forEach((r) => {
    if (!isLive(r)) {
      rows.push({ kind: 'recording', rec: r, at: r.uploaded_at });
      return;
    }
    const id = r.live?.live_session_id || 'unknown';
    if (!sessions.has(id)) {
      const group = { kind: 'session', id, clips: [] };
      sessions.set(id, group);
      rows.push(group);
    }
    sessions.get(id).clips.push(r);
  });
  sessions.forEach((group) => {
    group.clips.sort((a, b) => (a.uploaded_at || '').localeCompare(b.uploaded_at || ''));
    const movements = new Map();
    group.clips.forEach((c) => {
      const key = c.live?.movement_id;
      if (!movements.has(key)) movements.set(key, movements.size + 1);
      c.movementNumber = movements.get(key);
    });
    group.movements = movements.size;
    group.at = group.clips[group.clips.length - 1].uploaded_at;
    group.started = group.clips[0].uploaded_at;
  });
  return rows.sort((a, b) => (b.at != null) - (a.at != null) || (b.at || '').localeCompare(a.at || ''));
}

function liveMeta(r) {
  const live = r.live || {};
  const source = live.source === 'dev' ? 'dev key' : live.band_id ? `band ${live.band_id}` : 'wristband';
  const number = r.movementNumber ? `Movement ${r.movementNumber}, ` : '';
  return `${number}${live.event_type === 'release' ? 'put-down' : 'pickup'} from ${source}, ${live.wrist || 'unknown'} wrist`;
}

/** One live capture session: its clips, paired into pickup → put-down movements, open below it. */
function SessionRow({ group, open, onToggle }) {
  const alertsOpen = group.clips.reduce((n, c) => n + (c.alerts_open || 0), 0);
  const pickups = group.clips.filter((c) => c.live?.event_type === 'pickup').length;
  const releases = group.clips.length - pickups;
  const day = formatDateTime(group.started);
  const end = group.at ? new Date(group.at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';
  return (
    <tr className="session-row">
      <td className="chevron-cell">
        <button type="button" className="icon-btn" aria-expanded={open} aria-label={`${open ? 'Hide' : 'Show'} clips of this live session`} onClick={onToggle}>
          {open ? <CaretDownIcon aria-hidden="true" /> : <CaretRightIcon aria-hidden="true" />}
        </button>
      </td>
      <td>
        <button type="button" className="rec-cell session-toggle" onClick={onToggle}>
          <span className="session-icon" aria-hidden="true">
            <VideoCameraIcon size={18} />
          </span>
          <span className="choice-main">
            <span className="rec-name">
              <span className="truncate">Live session</span>
              <Badge tone="red">Live</Badge>
            </span>
            <span className="rec-meta truncate">
              {day}
              {end && group.clips.length > 1 ? ` to ${end}` : ''}
            </span>
            <span className="rec-meta">
              {plural(group.clips.length, 'clip')}, {plural(group.movements, 'movement')}
            </span>
          </span>
        </button>
      </td>
      <td className="num mono">{NONE}</td>
      <td className="nowrap">
        {plural(pickups, 'pickup')}
        <div className="row-sub">{plural(releases, 'put-down')}</div>
      </td>
      <td>
        <Badge tone="green">Applied live</Badge>
      </td>
      <td>{alertsOpen ? <Badge tone="red">{alertsOpen} open</Badge> : <span className="muted">None open</span>}</td>
      <td className="actions" />
    </tr>
  );
}

function SkeletonStatus({ rec, onRetry }) {
  if (rec.status === 'processing') {
    const pct = Math.round((rec.progress || 0) * 100);
    return (
      <div className="rec-status">
        <span className="hint">Extracting skeletons… {pct}%</span>
        <div className="progress" role="progressbar" aria-label={`Skeleton extraction for ${rec.label}`} aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
          <div className="progress-fill" style={{ width: `${Math.max(4, pct)}%` }} />
        </div>
      </div>
    );
  }
  if (rec.status === 'error' || rec.status === 'unprocessed') {
    return (
      <div className="rec-status">
        <Badge tone={rec.status === 'error' ? 'red' : 'amber'} title={rec.error || undefined}>
          {rec.status === 'error' ? 'Skeletons failed' : 'Needs skeletons'}
        </Badge>
        <button type="button" className="link-btn" onClick={onRetry}>
          {rec.status === 'error' ? 'Retry extraction' : 'Extract skeletons'}
        </button>
      </div>
    );
  }
  const s = appliedState(rec);
  return (
    <div className="rec-status">
      <Badge tone={s.tone}>{s.label}</Badge>
      {rec.last_applied_at && <span className="row-sub">Last applied {formatDateTime(rec.last_applied_at)}</span>}
    </div>
  );
}

function RecordingDetail({ rec, layout }) {
  const resolverFor = useResolver();
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let alive = true;
    request(`/api/recordings/${encodeURIComponent(rec.name)}`)
      .then((d) => alive && setDetail(d))
      .catch((e) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [rec.name, rec.events_applied, rec.alerts_open]);

  if (error) return <p className="form-error">Couldn't load this recording: {error}</p>;
  if (!detail) return <p className="hint">Loading signals…</p>;
  const pending = detail.events.filter((e) => !e.processed);

  return (
    <div className="rec-detail">
      <div>
        <h3>Signal decisions</h3>
        <div className="card">
          <SignalTable
            rows={detail.activity}
            layout={layout}
            cameras={detail.cameras}
            resolverFor={resolverFor}
            empty={`None yet. ${plural(pending.length, 'signal')} will apply when it plays.`}
          />
        </div>
        {detail.activity.length > 0 && pending.length > 0 && (
          <p className="hint section-gap">
            Still to apply: {pending.map((e) => `${e.event_type === 'pickup' ? 'pickup' : 'put-down'} at ${formatMs(e.media_time_ms)}`).join(', ')}.
          </p>
        )}
      </div>
      <div>
        <h3>Alerts raised</h3>
        <div className="card">
          {detail.alerts.length === 0 ? (
            <div className="card-body">
              <Empty>No alerts from this recording.</Empty>
            </div>
          ) : (
            <ul className="rows">
              {detail.alerts.map((a) => (
                <li key={a.alert_id} className="row">
                  <div className="choice-main">
                    <div className="row-title">{ALERT_TYPES[a.alert_type] || a.alert_type}</div>
                    <div className="row-sub">{medLabel(layout?.medications, a.medication_key)}</div>
                  </div>
                  <Badge tone={a.status === 'open' ? 'red' : 'gray'}>{a.status === 'open' ? 'Open' : 'Resolved'}</Badge>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

export default function Recordings() {
  const { state, loadRecording, applyRecording, deleteRecording } = useLive();
  const { openUpload, openViewReview } = useDialogs();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const filter = FILTERS.some((f) => f.id === params.get('show')) ? params.get('show') : 'all';
  const [recordings, setRecordings] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [expanded, setExpanded] = useState(() => new Set());
  const [openSessions, setOpenSessions] = useState(() => new Set());
  const [busy, setBusy] = useState(null);
  const [actionError, setActionError] = useState(null);
  const [deleting, setDeleting] = useState(null);
  const [ordering, setOrdering] = useState(null); // recording whose earlier uploads aren't applied yet
  const [notice, setNotice] = useState(null);

  const load = useCallback(
    () =>
      request('/api/recordings')
        .then((r) => {
          setRecordings(r.recordings);
          setLoadError(null);
        })
        .catch((e) => setLoadError(e.message)),
    [],
  );

  const processing = recordings?.some((r) => r.status === 'processing');
  useEffect(() => {
    load();
  }, [load, state.scenario, state.recording?.events_applied, state.store?.history_count]);
  useEffect(() => {
    if (!processing) return undefined;
    const timer = setInterval(load, 1500);
    return () => clearInterval(timer);
  }, [processing, load]);

  const run = async (name, fn) => {
    setBusy(name);
    setActionError(null);
    try {
      await fn();
      await load();
    } catch (e) {
      setActionError(e.message);
    } finally {
      setBusy(null);
    }
  };

  const open = (r) => run(r.name, async () => {
    await loadRecording(r.name);
    navigate('/');
  });

  const flip = (setter) => (key) =>
    setter((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  const toggle = flip(setExpanded);
  const toggleSession = flip(setOpenSessions);

  const confirmDelete = async () => {
    setBusy(deleting.name);
    setActionError(null);
    try {
      await deleteRecording(deleting.name);
      setDeleting(null);
      await load();
    } catch (e) {
      setActionError(e.message);
    } finally {
      setBusy(null);
    }
  };

  const apply = (r, includeEarlier) =>
    run(r.name, async () => {
      const res = await applyRecording(r.name, includeEarlier);
      setOrdering(null);
      const waiting = res.earlier_waiting || [];
      setNotice(
        waiting.length
          ? `Applied ${r.label}. ${plural(waiting.length, 'earlier recording')} still ${waiting.length === 1 ? 'is' : 'are'} extracting skeletons; apply ${waiting.length === 1 ? 'it' : 'them'} when ready.`
          : res.earlier_applied?.length
            ? `Applied ${plural(res.earlier_applied.length, 'earlier recording')} first, then ${r.label}.`
            : null,
      );
    });
  const startApply = (r) => (r.earlier_pending > 0 ? setOrdering(r) : apply(r, false));

  const shown = libraryRows((recordings || []).filter((r) => matches(filter, r)));
  const pendingCount = (recordings || []).filter(isPending).length;

  const renderRow = (r, child = false) => {
    const isOpen = expanded.has(r.name);
    const ready = r.status === 'ready';
    const remaining = r.events_total - r.events_applied;
    return (
      <Fragment key={r.name}>
        <tr className={[r.in_player && 'current', child && 'session-child'].filter(Boolean).join(' ')}>
          <td className="chevron-cell">
            <button
              type="button"
              className="icon-btn"
              aria-expanded={isOpen}
              aria-label={`${isOpen ? 'Hide' : 'Show'} signals for ${r.label}`}
              onClick={() => toggle(r.name)}
              disabled={!ready}
            >
              {isOpen ? <CaretDownIcon aria-hidden="true" /> : <CaretRightIcon aria-hidden="true" />}
            </button>
          </td>
          <td>
            <div className="rec-cell">
              <img
                className="thumb"
                src={`/api/recordings/${encodeURIComponent(r.name)}/thumbnail`}
                alt=""
                width={112}
                height={63}
                loading="lazy"
              />
              <div className="choice-main">
                <div className="rec-name">
                  <span className="truncate">{r.label}</span>
                  {r.in_player && <Badge tone="gray">In player</Badge>}
                </div>
                <div className="rec-meta truncate">
                  {isLive(r)
                    ? liveMeta(r)
                    : r.source === 'upload'
                      ? `Upload ${r.upload_index}: ${r.cameras.length > 1 ? `${r.cameras.length} cameras` : r.video_filename || 'video'}, ${formatDateTime(r.uploaded_at)}`
                      : 'Bundled demo fixture'}
                </div>
                <div className="rec-meta">
                  {r.cameras.length > 1
                    ? `Views: ${r.cameras.map((c) => `${c.label} (${c.view_name || c.layout_id})`).join(', ')}`
                    : `View: ${r.view_name || r.layout_id}`}
                  {r.has_video && !r.view_confirmed && (
                    <button
                      type="button"
                      className="link-btn view-check"
                      onClick={() => openViewReview({ name: r.name, label: r.label, cameras: r.cameras })}
                    >
                      Check camera {r.cameras.length > 1 ? 'views' : 'view'}
                    </button>
                  )}
                </div>
              </div>
            </div>
          </td>
          <td className="num mono">{r.duration_ms != null ? formatMs(r.duration_ms) : NONE}</td>
          <td className="nowrap">
            {plural(r.pickups, 'pickup')}
            <div className="row-sub">{plural(r.releases, 'put-down')}</div>
          </td>
          <td>
            <SkeletonStatus rec={r} onRetry={() => run(r.name, () => request(`/api/recordings/${encodeURIComponent(r.name)}/process`, { method: 'POST' }))} />
          </td>
          <td>
            {r.alerts_total === 0 ? (
              <span className="muted">None</span>
            ) : (
              <Badge tone={r.alerts_open ? 'red' : 'gray'}>
                {r.alerts_open ? `${r.alerts_open} open` : `${r.alerts_total} resolved`}
              </Badge>
            )}
          </td>
          <td className="actions">
            {ready && r.has_video && r.render && <RenderButton name={r.name} initial={r.render} onDone={load} />}
            {ready && remaining > 0 && !isLive(r) && (
              <button type="button" className="btn btn-sm" disabled={busy === r.name} onClick={() => startApply(r)}>
                {busy === r.name ? 'Applying…' : 'Apply'}
              </button>
            )}
            <button type="button" className="btn btn-sm btn-primary" disabled={!ready || busy === r.name} onClick={() => open(r)}>
              <PlayIcon size={13} aria-hidden="true" /> {r.in_player ? 'Watch' : 'Open'}
            </button>
            {(r.source === 'upload' || isLive(r)) && (
              <button
                type="button"
                className="icon-btn"
                aria-label={`Delete ${r.label}`}
                title={isLive(r) ? 'Delete clip' : 'Delete recording'}
                disabled={r.status === 'processing'}
                onClick={() => setDeleting(r)}
              >
                <TrashIcon aria-hidden="true" />
              </button>
            )}
          </td>
        </tr>
        {isOpen && (
          <tr className="expanded-row">
            <td />
            <td colSpan={6}>
              <RecordingDetail rec={r} layout={state.layout} />
            </td>
          </tr>
        )}
      </Fragment>
    );
  };

  return (
    <>
      <PageHeader
        title="Recordings"
        subtitle="Uploaded clips and live wristband clips are kept here, newest first. An upload's signals update inventory once, the first time they play or when you apply it; a live clip was applied when its band event arrived. Replays never count twice; uploading the same footage again counts as new events."
      >
        <div className="segmented" role="group" aria-label="Filter recordings">
          {FILTERS.map((f) => (
            <button
              key={f.id}
              type="button"
              className={filter === f.id ? 'active' : ''}
              aria-pressed={filter === f.id}
              onClick={() => setParams(f.id === 'all' ? {} : { show: f.id }, { replace: true })}
            >
              {f.label}
              {f.id === 'pending' && pendingCount > 0 && <span className="count">{pendingCount}</span>}
            </button>
          ))}
        </div>
        <button type="button" className="btn btn-primary" onClick={openUpload}>
          <UploadSimpleIcon size={14} aria-hidden="true" /> Upload recording
        </button>
      </PageHeader>

      {actionError && (
        <div className="banner banner-red" role="alert">
          {actionError}
        </div>
      )}
      {notice && (
        <div className="banner banner-green" role="status">
          {notice}
        </div>
      )}

      <Card flush>
        {loadError ? (
          <EmptyState icon={FilmStripIcon} title="Couldn't load recordings">
            {loadError}
          </EmptyState>
        ) : !recordings ? (
          <Empty>Loading recordings…</Empty>
        ) : recordings.length === 0 ? (
          <EmptyState
            icon={FilmStripIcon}
            title="No recordings yet"
            actions={
              <button type="button" className="btn btn-primary" onClick={openUpload}>
                <UploadSimpleIcon size={14} aria-hidden="true" /> Upload recording
              </button>
            }
          >
            Upload a video from the fixed camera with a file of pickup and put-down timestamps.
          </EmptyState>
        ) : shown.length === 0 ? (
          <Empty>No recordings match this filter.</Empty>
        ) : (
          <div className="table-wrap">
            <table className="table table-expandable">
              <thead>
                <tr>
                  <th scope="col" className="chevron-cell">
                    <span className="sr-only">Details</span>
                  </th>
                  <th scope="col">Recording</th>
                  <th scope="col" className="num">
                    Length
                  </th>
                  <th scope="col">Signals</th>
                  <th scope="col">Inventory</th>
                  <th scope="col">Alerts</th>
                  <th scope="col" className="actions">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {shown.map((item) =>
                  item.kind === 'session' ? (
                    <Fragment key={`session-${item.id}`}>
                      <SessionRow group={item} open={openSessions.has(item.id)} onToggle={() => toggleSession(item.id)} />
                      {openSessions.has(item.id) && item.clips.map((clip) => renderRow(clip, true))}
                    </Fragment>
                  ) : (
                    renderRow(item.rec)
                  ),
                )}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {ordering && (
        <ConfirmDialog
          title="Apply earlier recordings first?"
          confirmLabel="Apply in upload order"
          busyLabel="Applying…"
          busy={busy === ordering.name}
          error={actionError}
          secondaryLabel="Only this one"
          onSecondary={() => apply(ordering, false)}
          onConfirm={() => apply(ordering, true)}
          onClose={() => {
            setOrdering(null);
            setActionError(null);
          }}
        >
          {plural(ordering.earlier_pending, 'recording')} uploaded before <strong>{ordering.label}</strong>{' '}
          {ordering.earlier_pending === 1 ? 'has' : 'have'} signals that haven't been applied. Uploads are treated as happening in
          upload order, so applying the earlier ones first keeps stock changes in sequence.
        </ConfirmDialog>
      )}

      {deleting && (
        <ConfirmDialog
          title="Delete recording?"
          confirmLabel="Delete recording"
          busyLabel="Deleting…"
          busy={busy === deleting.name}
          error={actionError}
          destructive
          onConfirm={confirmDelete}
          onClose={() => {
            setDeleting(null);
            setActionError(null);
          }}
        >
          <strong>{deleting.label}</strong> and its video, skeletons and timestamps will be removed.{' '}
          {isLive(deleting)
            ? 'The stock change from its band event stays in place and remains in the history.'
            : deleting.events_applied > 0
            ? 'Inventory changes it already made stay in place and remain in the history.'
            : 'None of its signals have been applied, so inventory is unaffected.'}
        </ConfirmDialog>
      )}
    </>
  );
}
