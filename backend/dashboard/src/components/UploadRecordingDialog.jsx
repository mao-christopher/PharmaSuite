import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowLeftIcon, ArrowRightIcon, CheckIcon, CrosshairIcon, PlusIcon, TrashIcon, UploadSimpleIcon, VideoCameraIcon } from '@phosphor-icons/react';
import { useJobs } from '../lib/jobs';
import { errorMessage, request } from '../lib/api';
import { formatMs, plural } from '../lib/format';
import { useRegionEditor } from './RegionEditor';
import CameraViewFields, { hasUnassignedShelf, isEdited, pickView } from './CameraViewFields';
import { Badge, Dialog } from './ui';

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

const fileKey = (f) => `${f.name}-${f.size}-${f.lastModified}`;

/** One or more camera videos of the same moment. The first is the main camera. */
function CameraFiles({ videos, setVideos, disabled, draft }) {
  const add = (files) => {
    const chosen = Array.from(files); // copy now: the input is cleared right after
    setVideos((vs) => {
      const known = new Set(vs.map(fileKey));
      return [...vs, ...chosen.filter((f) => !known.has(fileKey(f)))];
    });
  };
  const remove = (i) => setVideos((vs) => vs.filter((_, j) => j !== i));
  const makeMain = (i) => setVideos((vs) => [vs[i], ...vs.filter((_, j) => j !== i)]);

  return (
    <div className="field">
      <span className="label" id="upload-videos-label">
        {videos.length > 1 ? `Videos (${videos.length} cameras)` : 'Video'}
      </span>
      {videos.length > 0 && (
        <ul className="camera-files" aria-labelledby="upload-videos-label">
          {videos.map((v, i) => (
            <li key={fileKey(v)} className="camera-file">
              <VideoCameraIcon size={16} aria-hidden="true" className="muted" />
              <span className="truncate" title={v.name}>
                {v.name}
              </span>
              {videos.length > 1 &&
                (i === 0 ? (
                  <Badge tone="blue">Main camera</Badge>
                ) : (
                  <button type="button" className="link-btn" disabled={disabled} onClick={() => makeMain(i)}>
                    Make main
                  </button>
                ))}
              <button type="button" className="icon-btn push-right" aria-label={`Remove ${v.name}`} disabled={disabled} onClick={() => remove(i)}>
                <TrashIcon aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      )}
      <input
        className="file-input"
        type="file"
        name="videos"
        multiple
        aria-label={videos.length ? 'Add another camera video' : 'Choose video'}
        accept="video/*,.mp4,.mov,.m4v,.avi,.mkv,.webm"
        disabled={disabled}
        onChange={(e) => {
          add(e.target.files);
          e.target.value = '';
        }}
      />
      <DraftStatus draft={draft} />
      <span className="hint">
        {videos.length > 1
          ? 'Filmed at the same time and started together. The player switches to whichever camera sees the arm; times below follow the main camera.'
          : 'Add more videos of the same moment from other cameras, and the player switches to whichever one sees the arm.'}
      </span>
    </div>
  );
}

/** How the background video upload is going, and whether the cameras' lengths agree. */
function DraftStatus({ draft }) {
  if (!draft) return null;
  if (draft.status === 'uploading') {
    return (
      <div className="draft-status">
        <div className="progress" role="progressbar" aria-label="Uploading videos" aria-valuenow={Math.round(draft.progress * 100)}>
          <div className="progress-fill" style={{ width: `${Math.round(draft.progress * 100)}%` }} />
        </div>
        <span className="hint">{draft.progress < 1 ? `Uploading ${Math.round(draft.progress * 100)}%…` : 'Reading the videos…'}</span>
      </div>
    );
  }
  if (draft.status === 'error') {
    return (
      <p className="form-error" role="alert">
        {draft.error}{' '}
        <button type="button" className="link-btn" onClick={draft.retry}>
          Try again
        </button>
      </p>
    );
  }
  const [main, ...rest] = draft.data.cameras;
  const mismatched = rest.filter((c) => Math.abs(c.duration_ms - main.duration_ms) > 1000);
  return (
    <>
      <span className="hint text-green">
        <CheckIcon size={13} aria-hidden="true" /> Uploaded ({formatMs(main.duration_ms)}, {main.width}×{main.height})
      </span>
      {mismatched.length > 0 && (
        <p className="hint text-amber">
          {mismatched.map((c) => `${c.label} is ${formatMs(c.duration_ms)}`).join(', ')} but {main.label} is{' '}
          {formatMs(main.duration_ms)}. Cameras are matched from their first frame, so check they started together.
        </p>
      )}
    </>
  );
}

/**
 * Upload the videos in the background as soon as they're chosen, so the next step can
 * show each camera's frame. The staged upload is discarded if the window closes first.
 */
function useStagedUpload(videos, keepRef) {
  const [draft, setDraft] = useState(null);
  const [attempt, setAttempt] = useState(0);
  const retry = useCallback(() => setAttempt((n) => n + 1), []);
  const key = videos.map(fileKey).join('|');

  useEffect(() => {
    if (!videos.length) {
      setDraft(null);
      return undefined;
    }
    let created = null;
    let cancelled = false;
    const xhr = new XMLHttpRequest();
    const body = new FormData();
    body.append('video', videos[0]);
    videos.slice(1).forEach((v) => body.append('extra_videos', v));
    xhr.open('POST', '/api/uploads');
    xhr.upload.onprogress = (e) => e.lengthComputable && setDraft((d) => ({ ...d, progress: e.loaded / e.total }));
    xhr.onload = () => {
      let res = {};
      try {
        res = JSON.parse(xhr.responseText);
      } catch {
        // fall through to the status check
      }
      if (xhr.status !== 200) {
        setDraft({ status: 'error', error: res.detail || `Upload failed (${xhr.status}).`, retry });
        return;
      }
      created = res.draft_id;
      if (cancelled) fetch(`/api/uploads/${created}`, { method: 'DELETE' }).catch(() => {});
      else setDraft({ status: 'ready', progress: 1, data: res });
    };
    xhr.onerror = () => setDraft({ status: 'error', error: 'The upload failed. Check the connection.', retry });
    setDraft({ status: 'uploading', progress: 0 });
    // A short wait lets several quick picks (or a reordering) go up as one upload.
    const timer = setTimeout(() => xhr.send(body), 300);
    return () => {
      cancelled = true;
      clearTimeout(timer);
      xhr.abort();
      if (created && !keepRef.current) fetch(`/api/uploads/${created}`, { method: 'DELETE' }).catch(() => {});
    };
  }, [key, attempt]); // eslint-disable-line react-hooks/exhaustive-deps

  return draft;
}

/** What to do with each camera's view: unchanged cameras use it as is. */
function viewChoices(cameras, views) {
  return cameras
    .map(({ camera_id }) => {
      const v = views[camera_id];
      if (!v?.base) return null;
      if (!isEdited(v.base, v.regions)) return { camera_id, action: 'use', layout_id: v.baseId };
      return {
        camera_id,
        action: v.saveAs,
        layout_id: v.baseId,
        name: v.saveAs === 'new' ? v.newName.trim() || undefined : undefined,
        regions: v.regions,
      };
    })
    .filter(Boolean);
}

function CameraViewsStep({ draft, views, setViews, cameraId, setCameraId, visited, error, errorRef }) {
  const editor = useRegionEditor();
  const cameras = draft.cameras;
  const multi = cameras.length > 1;
  const camera = cameras.find((c) => c.camera_id === cameraId) || cameras[0];
  const view = views[camera.camera_id];
  const update = (change) => setViews((vs) => ({ ...vs, [camera.camera_id]: { ...vs[camera.camera_id], ...change } }));
  const onRegions = useCallback(
    (fn) => setViews((vs) => ({ ...vs, [camera.camera_id]: { ...vs[camera.camera_id], regions: fn(vs[camera.camera_id].regions) } })),
    [camera.camera_id, setViews],
  );
  const chooseBase = (layoutId) => {
    update({ baseId: layoutId, base: null, regions: null });
    request(`/api/layouts/${encodeURIComponent(layoutId)}`)
      .then((layout) => update({ base: layout, regions: layout.regions }))
      .catch((e) => update({ error: e.message }));
    editor.setSelectedId(null);
  };
  useEffect(() => editor.setSelectedId(null), [camera.camera_id]); // eslint-disable-line react-hooks/exhaustive-deps

  const edited = view && isEdited(view.base, view.regions);
  const keepsView = (v) => v?.base && !(isEdited(v.base, v.regions) && v.saveAs === 'new');
  // Another camera uses the same view unchanged; one view can't fit two angles.
  const sharedWith =
    multi && view && keepsView(view)
      ? cameras.filter((c) => c.camera_id !== camera.camera_id && views[c.camera_id]?.baseId === view.baseId && keepsView(views[c.camera_id])).map((c) => c.label)
      : [];
  const baseName = view?.base?.name || view?.baseId;

  return (
    <>
      {multi && (
        <div className="camera-tabs segmented" role="tablist" aria-label="Cameras">
          {cameras.map((c, i) => (
            <button
              key={c.camera_id}
              type="button"
              role="tab"
              aria-selected={c.camera_id === camera.camera_id}
              className={`camera-tab ${c.camera_id === camera.camera_id ? 'active' : ''}`}
              onClick={() => setCameraId(c.camera_id)}
            >
              {visited.has(c.camera_id) && <CheckIcon size={13} aria-label="Checked" />}
              {i + 1}. {c.label}
            </button>
          ))}
        </div>
      )}
      {!view?.info ? (
        <p className={view?.error ? 'form-error' : 'hint'}>{view?.error || `Comparing ${camera.label} with saved views…`}</p>
      ) : (
        <CameraViewFields
          info={view.info}
          baseId={view.baseId}
          onBaseId={chooseBase}
          base={view.base}
          regions={view.regions}
          onRegions={onRegions}
          editor={editor}
          imageUrl={`/api/uploads/${draft.draft_id}/frame?camera=${encodeURIComponent(camera.camera_id)}`}
          multi={multi}
          sharedWith={sharedWith}
          newName={view.newName}
          onNewName={edited && view.saveAs === 'new' ? (newName) => update({ newName }) : null}
          fitHeight={multi ? '(100dvh - 600px)' : '(100dvh - 550px)'}
          decision={
            edited ? (
              <div className="field-head">
                <span className="label">Save your edits as</span>
                <div className="segmented" role="group" aria-label="Save edits as">
                  {[
                    ['new', 'A new view'],
                    ['replace', `An update to ${baseName}`],
                  ].map(([id, label]) => (
                    <button key={id} type="button" className={view.saveAs === id ? 'active' : ''} aria-pressed={view.saveAs === id} onClick={() => update({ saveAs: id })}>
                      {label}
                    </button>
                  ))}
                </div>
                <button type="button" className="link-btn" onClick={() => update({ regions: view.base.regions })}>
                  Discard edits
                </button>
              </div>
            ) : (
              <p className="hint">
                No edits, so this {multi ? 'camera' : 'video'} uses {baseName} as is. Drag or draw boxes if they don't line up.
              </p>
            )
          }
        >
          {edited && view.saveAs === 'replace' && (
            <p className="hint text-amber">This changes {baseName} for every recording that uses it.</p>
          )}
        </CameraViewFields>
      )}
      {error && (
        <p ref={errorRef} className="form-error" role="alert">
          {error}
        </p>
      )}
    </>
  );
}

const STEPS = ['Video and times', 'Shelf boxes'];

export default function UploadRecordingDialog({ onClose, onUploaded }) {
  const { track } = useJobs();
  const videoRef = useRef(null);
  const keepDraft = useRef(false);
  const [step, setStep] = useState(0);
  const [videos, setVideos] = useState([]); // first one is the main camera
  const video = videos[0] || null;
  const [videoUrl, setVideoUrl] = useState(null);
  const [currentS, setCurrentS] = useState(0);
  const [durationS, setDurationS] = useState(null);
  const [previewFailed, setPreviewFailed] = useState(false);
  const [mode, setMode] = useState('manual');
  const [events, setEvents] = useState(null);
  const [rows, setRows] = useState(() => [row('pickup'), row('release')]);
  const [name, setName] = useState('');
  const [views, setViews] = useState({});
  const [cameraId, setCameraId] = useState(null);
  const [visited, setVisited] = useState(() => new Set());
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const errorRef = useRef(null);
  const draft = useStagedUpload(videos, keepDraft);
  const draftData = draft?.status === 'ready' ? draft.data : null;

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
  }, [videoUrl, mode, step]);

  // New videos mean new frames: start the views over.
  useEffect(() => {
    setViews({});
    setVisited(new Set());
    setCameraId(draftData?.cameras[0].camera_id ?? null);
  }, [draftData?.draft_id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (step === 1 && cameraId) setVisited((v) => (v.has(cameraId) ? v : new Set(v).add(cameraId)));
  }, [step, cameraId]);

  // Suggest a view for each camera (best match first, one view per camera where possible).
  useEffect(() => {
    if (step !== 1 || !draftData) return undefined;
    let stopped = false;
    (async () => {
      const taken = new Set(Object.values(views).map((v) => v.baseId).filter(Boolean));
      const label = name.trim() || draftData.label;
      for (const cam of draftData.cameras) {
        if (views[cam.camera_id]) continue;
        const set = (value) => !stopped && setViews((vs) => ({ ...vs, [cam.camera_id]: value }));
        try {
          const info = await request(`/api/uploads/${draftData.draft_id}/views?camera=${encodeURIComponent(cam.camera_id)}`);
          const baseId = pickView(info.suggestions, taken);
          taken.add(baseId);
          const base = await request(`/api/layouts/${encodeURIComponent(baseId)}`);
          const newName = `View from ${label}${draftData.cameras.length > 1 ? `, ${cam.label}` : ''}`;
          set({ info, baseId, base, regions: base.regions, newName, saveAs: 'new' });
        } catch (e) {
          set({ error: e.message });
        }
        if (stopped) return;
      }
    })();
    return () => {
      stopped = true;
    };
  }, [step, draftData?.draft_id]); // eslint-disable-line react-hooks/exhaustive-deps

  // The form is long; bring a validation message into view.
  useEffect(() => {
    if (error) errorRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [error]);

  const issues = rowIssues(rows, durationS);

  const eventsFile = () => {
    if (mode === 'file') return events;
    const filled = rows.filter((r) => r.time !== '');
    return new File([toCsv(filled)], 'manual-timestamps.csv', { type: 'text/csv' });
  };

  const next = (e) => {
    e.preventDefault();
    if (!video) return setError('Choose at least one video.');
    if (mode === 'file' && !events) return setError('Choose a timestamps file, or switch to Enter manually.');
    if (mode === 'manual') {
      const filled = rows.filter((r) => r.time !== '');
      if (filled.length === 0) return setError('Add at least one pickup or put-down time.');
      if (filled.some((r) => issues.has(r.id))) return setError('Fix the highlighted times first.');
    }
    if (draft?.status === 'error') return setError('The videos did not upload. Try again, or choose them again.');
    setError(null);
    videoRef.current?.pause();
    setStep(1);
    return undefined;
  };

  const cameras = draftData?.cameras || [];
  const loading = cameras.some((c) => !views[c.camera_id]?.base && !views[c.camera_id]?.error);
  const unassigned = cameras.some((c) => {
    const v = views[c.camera_id];
    return v?.base && isEdited(v.base, v.regions) && hasUnassignedShelf(v.regions);
  });

  const submit = async (e) => {
    e.preventDefault();
    if (!draftData || loading || unassigned) return;
    setSubmitting(true);
    setError(null);
    const body = new FormData();
    const file = eventsFile();
    body.append('events', file, file.name);
    if (name.trim()) body.append('name', name.trim());
    body.append('views', JSON.stringify(viewChoices(cameras, views)));
    try {
      const res = await fetch(`/api/uploads/${draftData.draft_id}/finish`, { method: 'POST', body });
      if (!res.ok) throw new Error(await errorMessage(res));
      const job = await res.json();
      keepDraft.current = true; // it is a recording now
      // Skeletons are extracted in the background; the window closes.
      track(job.name, job.label, job.warnings);
      onUploaded(job);
    } catch (err) {
      setError(err.message);
      setSubmitting(false);
    }
  };

  const waiting = step === 1 && (!draftData || loading);

  return (
    <Dialog
      title="Upload recording"
      onClose={onClose}
      width={step === 0 ? 600 : 1000}
      footer={
        step === 0 ? (
          <>
            <button type="button" className="btn btn-ghost" onClick={onClose}>
              Cancel
            </button>
            <button className="btn btn-primary" type="submit" form="upload-form">
              Next: shelf boxes <ArrowRightIcon size={14} aria-hidden="true" />
            </button>
          </>
        ) : (
          <>
            <button type="button" className="btn btn-ghost" disabled={submitting} onClick={() => setStep(0)}>
              <ArrowLeftIcon size={14} aria-hidden="true" /> Back
            </button>
            <span className="hint push-right">
              {cameras.length > 1 && visited.size < cameras.length
                ? `${plural(cameras.length - visited.size, 'camera')} not checked will use the suggested view.`
                : 'Skeleton extraction starts when you upload.'}
            </span>
            <button className="btn btn-primary" type="submit" form="upload-views-form" disabled={submitting || waiting || unassigned}>
              <UploadSimpleIcon size={14} aria-hidden="true" />
              {submitting ? 'Uploading…' : waiting ? 'Preparing…' : 'Upload'}
            </button>
          </>
        )
      }
    >
      <ol className="steps" aria-label="Steps">
        {STEPS.map((label, i) => (
          <li key={label} className={i === step ? 'active' : i < step ? 'done' : ''} aria-current={i === step ? 'step' : undefined}>
            <span className="step-num">{i < step ? <CheckIcon size={12} aria-hidden="true" /> : i + 1}</span> {label}
          </li>
        ))}
      </ol>
      {step === 0 ? (
        <form id="upload-form" className="form" onSubmit={next} noValidate>
          <CameraFiles videos={videos} setVideos={setVideos} disabled={false} draft={draft} />

          <fieldset className="field" aria-labelledby="upload-times-label">
            <div className="field-head">
              <span id="upload-times-label" className="label">
                Pickup and put-down times
              </span>
              <div className="segmented" role="group" aria-label="How to provide times">
                {MODES.map((m) => (
                  <button key={m.id} type="button" className={mode === m.id ? 'active' : ''} aria-pressed={mode === m.id} onClick={() => setMode(m.id)}>
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
                disabled={false}
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
                  onChange={(e) => setEvents(e.target.files[0] || null)}
                />
                {events && <span className="hint">Using {events.name}.</span>}
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
              onChange={(e) => setName(e.target.value)}
            />
            <span className="hint">Defaults to the video's file name.</span>
          </label>
          <p className="hint">Next you'll check the shelf boxes on each camera's frame, then upload. Pose estimation runs in the background.</p>
          {error && (
            <p ref={errorRef} className="form-error" role="alert">
              {error}
            </p>
          )}
        </form>
      ) : (
        <form id="upload-views-form" onSubmit={submit} noValidate>
          {!draftData ? (
            draft?.status === 'error' ? (
              <p className="form-error" role="alert">
                {draft.error}{' '}
                <button type="button" className="link-btn" onClick={draft.retry}>
                  Try again
                </button>
              </p>
            ) : (
              <DraftStatus draft={draft} />
            )
          ) : (
            <CameraViewsStep
              draft={draftData}
              views={views}
              setViews={setViews}
              cameraId={cameraId}
              setCameraId={setCameraId}
              visited={visited}
              error={error}
              errorRef={errorRef}
            />
          )}
        </form>
      )}
    </Dialog>
  );
}
