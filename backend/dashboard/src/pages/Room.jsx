import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { CubeIcon, CursorIcon, FilmStripIcon, ImageSquareIcon, PencilSimpleIcon, PlusIcon, TrashIcon, UploadSimpleIcon } from '@phosphor-icons/react';
import { useLive } from '../lib/live';
import { errorMessage, request } from '../lib/api';
import { REGION_TYPES, medLabel, nextId, shelfIdFor } from '../lib/format';
import { boxFromClicks } from '../lib/room3d';
import CropDialog from '../components/CropDialog';
import RoomViewer, { ScanOverlay, useRebuilt } from '../components/RoomViewer';
import { Badge, EmptyState, Empty, PageHeader } from '../components/ui';

const TOOLS = [
  { id: 'select', label: 'Select', icon: CursorIcon },
  { id: 'designated_shelf', label: 'Shelf' },
  { id: 'dispensing_counter', label: 'Counter' },
  { id: 'disposal', label: 'Disposal' },
];
const MIN_PAIRS = 6;
const PAIR_COLOR = '#0a6ae0';
const PENDING_COLOR = '#c47d0a';

const formatBytes = (n) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.round(n / 1e3)} kB`);
const fmt = (v, digits = 2) => (Number.isFinite(v) ? v.toFixed(digits) : '–');

function uploadScan(file, name, onProgress) {
  return new Promise((resolve, reject) => {
    const body = new FormData();
    body.append('scan', file);
    if (name.trim()) body.append('name', name.trim());
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/rooms');
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => {
      let data = null;
      try {
        data = JSON.parse(xhr.responseText);
      } catch {
        /* not JSON */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new Error(data?.detail || xhr.statusText || 'Upload failed'));
    };
    xhr.onerror = () => reject(new Error('The upload was interrupted.'));
    xhr.send(body);
  });
}

function UploadScan({ onUploaded, compact = false }) {
  const [file, setFile] = useState(null);
  const [name, setName] = useState('');
  const [progress, setProgress] = useState(null);
  const [error, setError] = useState(null);

  const submit = async (e) => {
    e.preventDefault();
    if (!file) return;
    setError(null);
    setProgress(0);
    try {
      const room = await uploadScan(file, name, setProgress);
      setFile(null);
      setName('');
      onUploaded(room);
    } catch (err) {
      setError(err.message);
    } finally {
      setProgress(null);
    }
  };

  return (
    <form className={`scan-upload ${compact ? 'compact' : ''}`} onSubmit={submit}>
      <label className="field">
        <span className="label">Scan file (GLB)</span>
        <input className="input" type="file" name="scan" accept=".glb,model/gltf-binary" onChange={(e) => setFile(e.target.files[0] || null)} />
      </label>
      <label className="field">
        <span className="label">Room name (optional)</span>
        <input className="input" name="room-name" autoComplete="off" placeholder="Front dispensary…" value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <button type="submit" className="btn btn-primary" disabled={!file || progress !== null}>
        <UploadSimpleIcon size={14} aria-hidden="true" />
        {progress === null ? 'Import scan' : progress < 1 ? `Uploading ${Math.round(progress * 100)}%…` : 'Leveling the scan…'}
      </button>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}

function NumField({ label, value, onChange, step = 0.01, unit = 'm' }) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  return (
    <label className="field num-field">
      <span className="label">{label}</span>
      <span className="input-unit">
        <input
          className="input"
          type="number"
          step={step}
          inputMode="decimal"
          autoComplete="off"
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            const v = Number(e.target.value);
            if (e.target.value !== '' && Number.isFinite(v)) onChange(v);
          }}
        />
        <span className="unit">{unit}</span>
      </span>
    </label>
  );
}

function validateRegions(regions, medications) {
  const issues = [];
  const keys = new Set(medications.map((m) => m.medication_key));
  const perMed = {};
  regions.forEach((r) => {
    if (r.region_type === 'designated_shelf') {
      if (!r.medication_key || !keys.has(r.medication_key)) issues.push(`${r.region_id} needs a medication`);
      else perMed[r.medication_key] = (perMed[r.medication_key] || 0) + 1;
    }
  });
  Object.entries(perMed).forEach(([k, n]) => n > 1 && issues.push(`${medLabel(medications, k)} has ${n} shelves`));
  const ids = regions.map((r) => r.region_id);
  new Set(ids.filter((id, i) => ids.indexOf(id) !== i)).forEach((id) => issues.push(`Two regions are called ${id}`));
  return issues;
}

function regionName(r, medications) {
  if (r.region_type === 'designated_shelf') {
    return r.medication_key ? `${medLabel(medications, r.medication_key)} shelf` : 'Unassigned shelf';
  }
  return `${REGION_TYPES[r.region_type].label} (${r.region_id})`;
}

// ---------------------------------------------------------------- shelves & regions

function RegionsTab({ room, medications, onSaved }) {
  const [draft, setDraft] = useState(room.regions);
  const [savedJson, setSavedJson] = useState(JSON.stringify(room.regions));
  const [tool, setTool] = useState('select');
  const [first, setFirst] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState(null);

  useEffect(() => {
    setDraft(room.regions);
    setSavedJson(JSON.stringify(room.regions));
  }, [room.room_id, room.room_version]);

  const dirty = JSON.stringify(draft) !== savedJson;
  const issues = useMemo(() => validateRegions(draft, medications), [draft, medications]);
  const selected = draft.find((r) => r.region_id === selectedId);
  const labelFor = useCallback((r) => regionName(r, medications), [medications]);
  const markers = useMemo(() => (first ? [{ position: first.point, color: PENDING_COLOR, text: '1' }] : []), [first]);

  useEffect(() => {
    const onKey = (e) => {
      if (['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target.tagName)) return;
      if (e.key === 'Escape') {
        setFirst(null);
        setTool('select');
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const chooseTool = (id) => {
    setTool(id);
    setFirst(null);
  };

  const onPick = (hit) => {
    if (!first) {
      setFirst(hit);
      return;
    }
    const box = boxFromClicks(first, hit);
    const ids = draft.map((r) => r.region_id);
    const id = nextId(REGION_TYPES[tool].prefix, ids);
    setDraft((d) => [...d, { region_id: id, region_type: tool, medication_key: null, box }]);
    setSelectedId(id);
    setFirst(null);
    setTool('select');
  };

  const update = (id, change) => setDraft((d) => d.map((r) => (r.region_id === id ? { ...r, ...change } : r)));
  const setBox = (id, key, index, value) =>
    setDraft((d) =>
      d.map((r) => {
        if (r.region_id !== id) return r;
        const box = { ...r.box };
        if (index === null) box[key] = value;
        else box[key] = box[key].map((v, i) => (i === index ? value : v));
        return { ...r, box };
      }),
    );
  const assignShelf = (id, key) => {
    const newId = key ? shelfIdFor(key) : nextId('shelf', draft.map((r) => r.region_id));
    setDraft((d) => d.map((r) => (r.region_id === id ? { ...r, region_id: newId, medication_key: key || null } : r)));
    setSelectedId(newId);
  };
  const remove = (id) => {
    setDraft((d) => d.filter((r) => r.region_id !== id));
    setSelectedId(null);
  };

  const save = async () => {
    setSaving(true);
    setMessage(null);
    try {
      const saved = await request(`/api/rooms/${room.room_id}/regions`, {
        method: 'PUT',
        body: { regions: draft, room_version: room.room_version },
      });
      setMessage({ tone: 'green', text: `Saved ${draft.length} region${draft.length === 1 ? '' : 's'} (room v${saved.room_version}).` });
      onSaved(saved);
    } catch (e) {
      setMessage({ tone: 'red', text: e.message });
    } finally {
      setSaving(false);
    }
  };

  const taken = new Set(draft.filter((r) => r.region_id !== selectedId).map((r) => r.medication_key));
  const hint =
    tool === 'select'
      ? 'Drag to orbit, right-drag to pan, scroll to zoom. Click a box to edit it, or pick a tool to add one.'
      : !first
        ? REGION_TYPES[tool].label === 'Shelf'
          ? 'Click one corner of the shelf row’s front edge.'
          : `Click one corner of the ${REGION_TYPES[tool].label.toLowerCase()} top.`
        : 'Now click the opposite corner. Esc cancels.';

  return (
    <div className="setup">
      <div className="setup-main card">
        <div className="toolbar">
          <div className="segmented" role="toolbar" aria-label="3D region tools">
            {TOOLS.map((t) => {
              const Icon = t.icon;
              return (
                <button key={t.id} type="button" className={tool === t.id ? 'active' : ''} aria-pressed={tool === t.id} onClick={() => chooseTool(t.id)}>
                  {Icon ? <Icon size={14} aria-hidden="true" /> : <span className="dot" aria-hidden="true" style={{ background: REGION_TYPES[t.id].color }} />}
                  {t.label}
                </button>
              );
            })}
          </div>
          <div className="toolbar-right">
            {dirty ? <Badge tone="amber">Unsaved changes</Badge> : <span className="muted">Room v{room.room_version}</span>}
            <button type="button" className="btn btn-ghost btn-sm" disabled={!dirty || saving} onClick={() => setDraft(JSON.parse(savedJson))}>
              Discard
            </button>
            <button type="button" className="btn btn-primary btn-sm" disabled={!dirty || issues.length > 0 || saving} onClick={save}>
              {saving ? 'Saving…' : 'Save regions'}
            </button>
          </div>
        </div>
        <RoomViewer
          room={room}
          regions={draft}
          selectedId={selectedId}
          labelFor={labelFor}
          markers={markers}
          cameras={room.cameras}
          picking={tool !== 'select'}
          onPick={onPick}
          onSelectRegion={setSelectedId}
          label="Scanned room with shelf, counter and disposal regions"
        />
        <p className="hint canvas-hint" aria-live="polite">
          {hint}
        </p>
      </div>

      <aside className="setup-side card">
        <div className="side-body">
          {message && (
            <div className={`banner banner-${message.tone}`} role="status">
              {message.text}
            </div>
          )}
          {issues.length > 0 && (
            <div className="issues">
              <div className="issues-title">Fix before saving</div>
              <ul>
                {issues.map((i) => (
                  <li key={i}>{i}</li>
                ))}
              </ul>
            </div>
          )}
          {medications.length === 0 && (
            <p className="hint text-amber">
              No medications yet. <Link to="/inventory?section=stock">Add them on the Inventory page</Link> to assign shelves.
            </p>
          )}
          {selected && (
            <div className="selected-panel">
              <div className="selected-head">
                <span className="dot" aria-hidden="true" style={{ background: REGION_TYPES[selected.region_type].color }} />
                <span className="strong">{regionName(selected, medications)}</span>
              </div>
              {selected.region_type === 'designated_shelf' ? (
                <label className="field">
                  <span className="label">Medication on this shelf</span>
                  <select className="input" value={selected.medication_key || ''} onChange={(e) => assignShelf(selected.region_id, e.target.value)}>
                    <option value="">Choose a medication…</option>
                    {medications.map((m) => (
                      <option key={m.medication_key} value={m.medication_key} disabled={taken.has(m.medication_key)}>
                        {m.name} {m.strength}
                        {taken.has(m.medication_key) ? ' (has a shelf)' : ''}
                      </option>
                    ))}
                  </select>
                  <Link to="/inventory?section=stock" className="hint">
                    Add or edit medications
                  </Link>
                </label>
              ) : (
                <label className="field">
                  <span className="label">Region ID</span>
                  <input
                    className="input mono"
                    autoComplete="off"
                    spellCheck={false}
                    value={selected.region_id}
                    onChange={(e) => {
                      const id = e.target.value.replace(/[^a-z0-9_-]/gi, '').toLowerCase();
                      if (!id) return;
                      update(selected.region_id, { region_id: id });
                      setSelectedId(id);
                    }}
                  />
                  <span className="hint">Events and alerts name this region by its ID.</span>
                </label>
              )}
              <div className="grid-3">
                {['x', 'y', 'z'].map((axis, i) => (
                  <NumField key={axis} label={`Center ${axis}`} value={selected.box.center[i]} onChange={(v) => setBox(selected.region_id, 'center', i, v)} />
                ))}
                {['Width', 'Height', 'Depth'].map((name, i) => (
                  <NumField key={name} label={name} value={selected.box.size[i]} onChange={(v) => v > 0 && setBox(selected.region_id, 'size', i, v)} />
                ))}
                <NumField label="Turn" unit="°" step={1} value={selected.box.yaw_deg} onChange={(v) => setBox(selected.region_id, 'yaw_deg', null, v)} />
              </div>
              <div className="selected-foot">
                <span className="muted">Depth goes into the shelf from its front.</span>
                <button type="button" className="btn btn-sm btn-danger" onClick={() => remove(selected.region_id)}>
                  <TrashIcon size={13} aria-hidden="true" /> Delete
                </button>
              </div>
            </div>
          )}
          {draft.length === 0 ? (
            <Empty>Pick Shelf, Counter or Disposal, then click two opposite corners on the scan.</Empty>
          ) : (
            Object.entries(REGION_TYPES).map(([type, meta]) => {
              const list = draft.filter((r) => r.region_type === type);
              if (!list.length) return null;
              return (
                <div key={type} className="region-group">
                  <div className="group-title">{meta.plural}</div>
                  <ul className="region-list">
                    {list.map((r) => (
                      <li key={r.region_id}>
                        <button type="button" className={r.region_id === selectedId ? 'selected' : ''} aria-pressed={r.region_id === selectedId} onClick={() => setSelectedId(r.region_id)}>
                          <span className="dot" aria-hidden="true" style={{ background: meta.color }} />
                          <span className={r.region_type === 'designated_shelf' && !r.medication_key ? 'text-red' : ''}>{regionName(r, medications)}</span>
                          <span className="mono muted push">
                            {r.box.size.map((s) => s.toFixed(2)).join(' × ')} m
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              );
            })
          )}
        </div>
      </aside>
    </div>
  );
}

// ---------------------------------------------------------------- cameras

const sameAspect = (w1, h1, w2, h2) => Math.abs(w1 / h1 - w2 / h2) <= 0.02;
const recKey = (r) => `${r.name}::${r.camera_id || ''}`;

function imageSize(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      resolve({ width: img.naturalWidth, height: img.naturalHeight });
      URL.revokeObjectURL(url);
    };
    img.onerror = () => {
      reject(new Error('Not a readable image. Use JPG or PNG.'));
      URL.revokeObjectURL(url);
    };
    img.src = url;
  });
}

function PhotoPairs({ view, pairs, current, solution, selected, onClick, opacity, room, rebuilt, showGrid, showRegions }) {
  const [w, h] = [view.frame_width, view.frame_height];
  const ref = useRef(null);
  const url = `/api/layouts/${view.layout_id}/files/${view.background_image}`;
  const overlay = solution?.overlay;
  const px = ([u, v]) => [u * w, v * h];
  const r = Math.max(w, h) / 160;

  const click = (e) => {
    const rect = ref.current.getBoundingClientRect();
    const u = (e.clientX - rect.left) / rect.width;
    const v = (e.clientY - rect.top) / rect.height;
    if (u >= 0 && u <= 1 && v >= 0 && v <= 1) onClick([u, v]);
  };

  return (
    <div ref={ref} className="canvas photo-pairs" style={{ aspectRatio: `${w} / ${h}` }}>
      <img src={url} alt={`Camera photo of ${view.name}`} draggable={false} />
      {solution && opacity > 0 && <ScanOverlay room={room} registration={solution} opacity={opacity} rebuilt={rebuilt} />}
      <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className="drawing" onClick={click}>
        {overlay && showRegions &&
          overlay.generated_regions.map((reg) => (
            <g key={reg.region_id} className="preview-region" style={{ '--region': REGION_TYPES[reg.region_type]?.color }}>
              <polygon points={reg.polygon.map((p) => px(p).join(',')).join(' ')} />
            </g>
          ))}
        {overlay && showGrid && (
          <g className="solve-grid">
            {overlay.grid.map((line, i) => (
              <polyline key={i} points={line.map((p) => px(p).join(',')).join(' ')} />
            ))}
          </g>
        )}
        {overlay?.regions.map((reg) => (
          <g key={reg.region_id} className="solve-region" style={{ stroke: REGION_TYPES[reg.region_type]?.color }}>
            {reg.edges.map((line, i) => (
              <polyline key={i} points={line.map((p) => px(p).join(',')).join(' ')} />
            ))}
          </g>
        ))}
        {pairs.map((p, i) => {
          const [x, y] = px(p.image);
          const proj = overlay?.points[i] && px(overlay.points[i]);
          return (
            <g key={i} className={`pair-mark ${selected === i ? 'selected' : ''}`}>
              {proj && <line x1={x} y1={y} x2={proj[0]} y2={proj[1]} className="pair-error" />}
              {proj && <circle cx={proj[0]} cy={proj[1]} r={r * 0.55} className="pair-proj" />}
              <circle cx={x} cy={y} r={selected === i ? r * 1.35 : r} fill={selected === i ? '#171717' : PAIR_COLOR} />
              <text x={x} y={y} dy="0.35em" textAnchor="middle" style={{ fontSize: r * 1.3 }}>
                {i + 1}
              </text>
            </g>
          );
        })}
        {current?.image && (
          <g className="pair-mark">
            <circle cx={px(current.image)[0]} cy={px(current.image)[1]} r={r} fill={PENDING_COLOR} />
            <text x={px(current.image)[0]} y={px(current.image)[1]} dy="0.35em" textAnchor="middle" style={{ fontSize: r * 1.3 }}>
              {pairs.length + 1}
            </text>
          </g>
        )}
      </svg>
    </div>
  );
}

/** Pick, add, rename and delete camera views. */
function ViewManager({ views, viewId, registered, dirty, onSelect, onChanged }) {
  const view = views.find((v) => v.layout_id === viewId);
  const [naming, setNaming] = useState(null); // 'new' | 'rename'
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const start = (mode) => {
    setNaming(mode);
    setName(mode === 'rename' ? view?.name || '' : '');
    setError(null);
  };

  const submit = async (e) => {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      if (naming === 'new') {
        const res = await request('/api/layouts', { method: 'POST', body: { name: name.trim() } });
        await onChanged();
        onSelect(res.layout.layout_id);
      } else {
        await request(`/api/layouts/${viewId}`, { method: 'PATCH', body: { name: name.trim() } });
        await onChanged();
      }
      setNaming(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!window.confirm(`Delete the camera view ${view.name}, its photo and its camera registration?`)) return;
    setBusy(true);
    setError(null);
    try {
      await request(`/api/layouts/${viewId}`, { method: 'DELETE' });
      await onChanged();
      onSelect(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="view-manager">
      <label className="field">
        <span className="label">Camera view</span>
        <select
          className="input"
          value={viewId || ''}
          onChange={(e) => {
            if (dirty && !window.confirm('Discard the point pairs for this view?')) return;
            onSelect(e.target.value);
          }}
        >
          {views.map((v) => (
            <option key={v.layout_id} value={v.layout_id}>
              {v.name}
              {registered.has(v.layout_id) ? ' (registered)' : v.background_image ? '' : ' (no photo)'}
            </option>
          ))}
        </select>
      </label>
      {naming ? (
        <form className="inline-form" onSubmit={submit}>
          <input
            className="input input-sm"
            aria-label={naming === 'new' ? 'New camera view name' : 'Camera view name'}
            placeholder={naming === 'new' ? 'Back counter camera…' : ''}
            autoComplete="off"
            autoFocus
            maxLength={80}
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === 'Escape' && setNaming(null)}
          />
          <button type="submit" className="btn btn-primary btn-sm" disabled={busy || !name.trim()}>
            {naming === 'new' ? 'Add' : 'Rename'}
          </button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setNaming(null)}>
            Cancel
          </button>
        </form>
      ) : (
        <div className="button-row">
          <button type="button" className="btn btn-sm" onClick={() => start('new')}>
            <PlusIcon size={13} aria-hidden="true" /> New view
          </button>
          {view && (
            <button type="button" className="btn btn-sm btn-ghost" onClick={() => start('rename')}>
              <PencilSimpleIcon size={13} aria-hidden="true" /> Rename
            </button>
          )}
          {view && (
            <button type="button" className="btn btn-sm btn-ghost push" disabled={busy || view.recordings.length > 0} title={view.recordings.length ? 'Recordings use this view' : undefined} onClick={remove}>
              <TrashIcon size={13} aria-hidden="true" /> Delete
            </button>
          )}
        </div>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

/** Upload a photo or take a recording's frame as the view's photo. */
function usePhotoSource(view, onChanged, setMessage) {
  const [busy, setBusy] = useState(false);
  const [cropping, setCropping] = useState(null);
  const fileRef = useRef(null);
  const videoRecs = (view?.recordings || []).filter((r) => r.has_video && r.width && r.height);
  const mismatched = view?.background_image ? videoRecs.filter((r) => !sameAspect(r.width, r.height, view.frame_width, view.frame_height)) : [];

  const setPhoto = async (img, source) => {
    await request(`/api/layouts/${view.layout_id}`, { method: 'PATCH', body: { background_image: img.background_image } });
    await onChanged();
    setMessage({ tone: 'green', text: `${view.name} now uses ${source} (${img.width}×${img.height}).` });
  };

  const upload = async (file) => {
    setBusy(true);
    setMessage(null);
    try {
      const body = new FormData();
      body.append('image', file);
      const res = await fetch(`/api/layouts/${view.layout_id}/background`, { method: 'POST', body });
      if (!res.ok) throw new Error(await errorMessage(res));
      await setPhoto(await res.json(), 'the uploaded photo');
    } catch (e) {
      setMessage({ tone: 'red', text: `Photo import failed: ${e.message}` });
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  const importFile = async (file) => {
    if (!file) return;
    try {
      const size = await imageSize(file);
      const target = videoRecs[0];
      if (target && !sameAspect(size.width, size.height, target.width, target.height)) {
        setCropping({ file, target: { width: target.width, height: target.height, label: target.label } });
        return;
      }
      await upload(file);
    } catch (e) {
      setMessage({ tone: 'red', text: e.message });
    }
  };

  // A recording (or one camera of a multi-camera recording) is picked as "name::camera".
  const useFrame = async (choice) => {
    if (!choice) return;
    const [recording, cameraId] = choice.split('::');
    setBusy(true);
    setMessage(null);
    try {
      const img = await request(`/api/layouts/${view.layout_id}/background-from-recording`, {
        method: 'POST',
        body: { recording, camera_id: cameraId || null },
      });
      const label = videoRecs.find((r) => recKey(r) === choice)?.label || recording;
      await setPhoto(img, `a frame from ${label}`);
    } catch (e) {
      setMessage({ tone: 'red', text: e.message });
    } finally {
      setBusy(false);
    }
  };

  const controls = view && (
    <>
      <input ref={fileRef} type="file" name="camera-photo" accept="image/png,image/jpeg" hidden onChange={(e) => importFile(e.target.files[0])} />
      {videoRecs.length > 0 && (
        <select className="input input-sm" aria-label="Use a frame from a recording as the photo" value="" disabled={busy} onChange={(e) => useFrame(e.target.value)}>
          <option value="">Use a recording frame…</option>
          {videoRecs.map((r) => (
            <option key={recKey(r)} value={recKey(r)}>
              {r.label} ({r.width}×{r.height})
            </option>
          ))}
        </select>
      )}
      <button type="button" className="btn btn-sm" onClick={() => fileRef.current?.click()} disabled={busy}>
        <ImageSquareIcon size={14} aria-hidden="true" /> {busy ? 'Importing…' : view.background_image ? 'Replace photo' : 'Upload photo'}
      </button>
    </>
  );

  const mismatchBanner = mismatched.length > 0 && (
    <div className="banner banner-amber" role="status">
      <span>
        {mismatched.length === 1 ? `${mismatched[0].label} is` : `${mismatched.length} recordings are`} {mismatched[0].width}×{mismatched[0].height}, but
        this view's photo is {view.frame_width}×{view.frame_height}. Its regions won't line up with the video.
      </span>
      <button type="button" className="btn btn-sm push" disabled={busy} onClick={() => useFrame(recKey(mismatched[0]))}>
        <FilmStripIcon size={13} aria-hidden="true" /> Use its frame
      </button>
    </div>
  );

  const cropDialog = cropping && (
    <CropDialog
      file={cropping.file}
      target={cropping.target}
      onCancel={() => {
        setCropping(null);
        if (fileRef.current) fileRef.current.value = '';
      }}
      onDone={(file) => {
        setCropping(null);
        upload(file);
      }}
    />
  );

  return { controls, mismatchBanner, cropDialog };
}

function CamerasTab({ room, views, viewId: wantedId, onViewId, onSaved, onViewsChanged }) {
  const registered = new Set(room.cameras.map((c) => c.layout_id));
  const fallback = (views.filter((v) => v.background_image).find((v) => registered.has(v.layout_id)) || views.find((v) => v.background_image) || views[0])?.layout_id;
  const viewId = views.some((v) => v.layout_id === wantedId) ? wantedId : fallback || null;
  const view = views.find((v) => v.layout_id === viewId);
  const registration = room.cameras.find((c) => c.layout_id === viewId);
  const [pairs, setPairs] = useState([]);
  const [current, setCurrent] = useState(null);
  const [selected, setSelected] = useState(null);
  const [solution, setSolution] = useState(null);
  const [solveError, setSolveError] = useState(null);
  const [opacity, setOpacity] = useState(0.45);
  const [showGrid, setShowGrid] = useState(true);
  const [showRegions, setShowRegions] = useState(false);
  const [overlayMode, setOverlayMode] = useState('scan');
  const rebuilt = useRebuilt(room, overlayMode === 'rebuilt');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);
  const photo = usePhotoSource(view, onViewsChanged, setMessage);

  useEffect(() => {
    setPairs(registration ? registration.correspondences.map((c) => ({ image: c.image, room: c.room })) : []);
    setCurrent(null);
    setSelected(null);
  }, [viewId, room.room_id, registration?.revision]);
  useEffect(() => setMessage(null), [viewId]);

  const savedJson = JSON.stringify(registration?.correspondences.map((c) => ({ image: c.image, room: c.room })) || []);
  const dirty = JSON.stringify(pairs) !== savedJson;
  const photoKey = `${view?.background_image}:${view?.frame_width}x${view?.frame_height}`;

  useEffect(() => {
    if (!viewId || !view?.background_image || pairs.length < MIN_PAIRS) {
      setSolution(null);
      setSolveError(null);
      return undefined;
    }
    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const sol = await request(`/api/rooms/${room.room_id}/cameras/solve`, {
          method: 'POST',
          body: { layout_id: viewId, correspondences: pairs },
        });
        if (!cancelled) {
          setSolution(sol);
          setSolveError(null);
        }
      } catch (e) {
        if (!cancelled) {
          setSolution(null);
          setSolveError(e.message);
        }
      }
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [pairs, viewId, room.room_id, room.room_version, photoKey]);

  const addPart = (part) => {
    const next = { ...(current || {}), ...part };
    if (next.image && next.room) {
      setPairs((p) => [...p, { image: next.image, room: next.room }]);
      setCurrent(null);
    } else setCurrent(next);
  };

  const markers = useMemo(() => {
    const list = pairs.map((p, i) => ({ position: p.room, text: String(i + 1), color: selected === i ? '#171717' : PAIR_COLOR }));
    if (current?.room) list.push({ position: current.room, text: String(pairs.length + 1), color: PENDING_COLOR });
    return list;
  }, [pairs, current, selected]);
  const frusta = useMemo(() => {
    const cams = room.cameras.filter((c) => c.layout_id !== viewId).map((c) => ({ ...c, label: views.find((v) => v.layout_id === c.layout_id)?.name }));
    const mine = solution || registration;
    if (mine) cams.push({ ...mine, layout_id: viewId, label: view?.name || viewId });
    return cams;
  }, [room.cameras, solution, registration, viewId, views, view]);
  const labelFor = useCallback((r) => (r.region_type === 'designated_shelf' ? r.region_id.replace(/^shelf_/, '') : ''), []);

  const save = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const res = await request(`/api/rooms/${room.room_id}/cameras/${viewId}`, { method: 'PUT', body: { correspondences: pairs } });
      const regions = res.layout.regions.length;
      setMessage({
        tone: 'green',
        text: `Registered ${view?.name || viewId} (revision ${res.registration.revision}). It has ${regions} shelf region${regions === 1 ? '' : 's'} from the 3D tags, and its recordings show on the floor map.`,
      });
      onSaved(res.room);
      await onViewsChanged();
    } catch (e) {
      setMessage({ tone: 'red', text: e.message });
    } finally {
      setBusy(false);
    }
  };

  const unregister = async () => {
    if (!window.confirm(`Remove ${view?.name || viewId}'s registration from this room? Its shelf regions go too, so its signals will ask for confirmation.`)) return;
    setBusy(true);
    try {
      const res = await request(`/api/rooms/${room.room_id}/cameras/${viewId}`, { method: 'DELETE' });
      onSaved(res);
      await onViewsChanged();
      setMessage({ tone: 'green', text: 'Registration removed.' });
    } catch (e) {
      setMessage({ tone: 'red', text: e.message });
    } finally {
      setBusy(false);
    }
  };

  const adoptRegions = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const res = await request(`/api/rooms/${room.room_id}/cameras/${viewId}/adopt-regions`, { method: 'POST' });
      await onViewsChanged();
      setMessage({ tone: 'green', text: `${view.name} now uses ${res.layout.regions.length} regions from the 3D tags.` });
    } catch (e) {
      setMessage({ tone: 'red', text: e.message });
    } finally {
      setBusy(false);
    }
  };

  const manager = (
    <ViewManager
      views={views}
      viewId={viewId}
      registered={registered}
      dirty={dirty}
      onSelect={onViewId}
      onChanged={onViewsChanged}
    />
  );

  if (!views.length) {
    return (
      <div className="card">
        <EmptyState icon={CubeIcon} title="No camera views yet">
          Add one for each fixed camera, give it a photo, then register it here.
        </EmptyState>
        <div className="card-body">{manager}</div>
      </div>
    );
  }

  const needsPhoto = view && !view.background_image;
  const stalePhoto = registration && view?.background_image && registration.background_image !== view.background_image;
  const reshaped = stalePhoto && !sameAspect(registration.frame_size[0], registration.frame_size[1], view.frame_width, view.frame_height);
  const legacy = registration && view && !view.regions_source && view.regions > 0;
  const step = !current ? 'Click a spot in the photo or the scan (tape marks and shelf corners work best).' : current.image ? 'Now click the same spot in the 3D scan.' : 'Now click the same spot in the photo.';

  return (
    <div className="setup">
      <div className="setup-main card">
        <div className="toolbar">
          <span className="muted" aria-live="polite">
            {needsPhoto ? 'Give this view a photo first.' : `${pairs.length < MIN_PAIRS ? `${pairs.length} of ${MIN_PAIRS}` : pairs.length} point pairs. ${step}`}
          </span>
          <div className="toolbar-right">
            {current && (
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => setCurrent(null)}>
                Cancel point
              </button>
            )}
            {photo.controls}
          </div>
        </div>
        {photo.mismatchBanner}
        {needsPhoto ? (
          <EmptyState icon={ImageSquareIcon} title="This view has no photo">
            Registration needs the camera's photo. Upload one, or use a frame from one of this camera's recordings, with the buttons above.
          </EmptyState>
        ) : (
          view && (
            <div className="room-stack">
              <PhotoPairs
                view={view}
                room={room}
                pairs={pairs}
                current={current}
                solution={solution}
                selected={selected}
                opacity={opacity}
                rebuilt={overlayMode === 'rebuilt' ? rebuilt.data : null}
                showGrid={showGrid}
                showRegions={showRegions}
                onClick={(image) => addPart({ image })}
              />
              <RoomViewer
                room={room}
                regions={room.regions}
                labelFor={labelFor}
                markers={markers}
                cameras={frusta}
                picking
                onPick={(hit) => addPart({ room: hit.point.map((v) => Math.round(v * 1000) / 1000) })}
                label="Scanned room: click the spot that matches the photo"
              />
            </div>
          )
        )}
      </div>

      <aside className="setup-side card">
        <div className="side-body">
          {manager}
          {view && (
            <p className="hint">
              {view.frame_width}×{view.frame_height}.{' '}
              {registration
                ? `Registered, revision ${registration.revision}, ${fmt(registration.rms_px, 1)} px error.`
                : 'Not registered in this room yet, so it has no shelf regions and its signals ask for confirmation.'}
            </p>
          )}
          {stalePhoto && (
            <div className="banner banner-amber" role="status">
              {reshaped ? (
                <span>
                  The new photo is a different shape from the one the camera was registered on, so the saved points no longer fit. Clear the
                  pairs and register the camera again.
                </span>
              ) : (
                <>
                  <span>
                    New photo. The saved registration is drawn over it: if the shelf edges and floor grid still line up, keep it. If they don't,
                    the camera moved; clear the pairs and register again.
                  </span>
                  <button type="button" className="btn btn-sm push" disabled={busy || dirty || !solution} onClick={save}>
                    Keep registration
                  </button>
                </>
              )}
            </div>
          )}
          {legacy && (
            <div className="banner banner-amber" role="status">
              <span>This view still uses {view.regions} hand-drawn regions from before the 3D tags.</span>
              <button type="button" className="btn btn-sm push" disabled={busy} onClick={adoptRegions}>
                Use the 3D tags
              </button>
            </div>
          )}
          {message && (
            <div className={`banner banner-${message.tone}`} role="status">
              {message.text}
            </div>
          )}

          {solution && (
            <div className="solve-summary">
              <div className="selected-head">
                <Badge tone={{ good: 'green', check: 'amber', poor: 'red' }[solution.quality]}>
                  {{ good: 'Lines up well', check: 'Check the overlay', poor: 'Doesn’t line up' }[solution.quality]}
                </Badge>
                <span className="muted num">
                  {fmt(solution.rms_px, 1)} px average, {fmt(solution.max_px, 1)} px worst
                </span>
              </div>
              <dl className="kv-grid">
                <dt>Camera height</dt>
                <dd>{fmt(solution.height_m)} m</dd>
                <dt>Looking down</dt>
                <dd>{fmt(solution.tilt_down_deg, 0)}°</dd>
                <dt>Field of view</dt>
                <dd>{fmt(solution.hfov_deg, 0)}° wide</dd>
              </dl>
              {solution.note && <p className="hint text-amber">{solution.note}</p>}
              {solution.quality !== 'good' && (
                <p className="hint">
                  The largest numbers below are the likeliest wrong pairs. If every pair looks right and it still doesn’t line up, the lens
                  may need a calibration board.
                </p>
              )}
              <div className="field">
                <span className="label">Over the photo</span>
                <div className="segmented" role="group" aria-label="Draw over the photo">
                  {[
                    ['scan', 'Scan'],
                    ['rebuilt', 'Rebuilt room'],
                  ].map(([id, text]) => (
                    <button key={id} type="button" className={overlayMode === id ? 'active' : ''} aria-pressed={overlayMode === id} onClick={() => setOverlayMode(id)}>
                      {text}
                    </button>
                  ))}
                </div>
                {overlayMode === 'rebuilt' && (
                  <span className="hint">{rebuilt.error ? `Could not rebuild: ${rebuilt.error}` : rebuilt.data ? 'The simulation renders this room from this camera.' : 'Rebuilding…'}</span>
                )}
              </div>
              <label className="field">
                <span className="label">Opacity</span>
                <input type="range" min="0" max="1" step="0.05" value={opacity} onChange={(e) => setOpacity(Number(e.target.value))} aria-label="Scan overlay opacity" />
              </label>
              <label className="check-row">
                <input type="checkbox" checked={showGrid} onChange={(e) => setShowGrid(e.target.checked)} /> Floor grid (50 cm)
              </label>
              <label className="check-row">
                <input type="checkbox" checked={showRegions} onChange={(e) => setShowRegions(e.target.checked)} /> Shelf regions the inventory will use (
                {solution.overlay.generated_regions.length})
              </label>
              {solution.overlay.region_report.some((r) => !r.visible) && (
                <p className="hint">
                  Not visible from this camera:{' '}
                  {solution.overlay.region_report
                    .filter((r) => !r.visible)
                    .map((r) => r.region_id)
                    .join(', ')}
                  .
                </p>
              )}
            </div>
          )}
          {solveError && (
            <p className="form-error" role="alert">
              {solveError}
            </p>
          )}

          {pairs.length > 0 && (
            <div className="region-group">
              <div className="group-title">Point pairs</div>
              <ul className="region-list pair-list">
                {pairs.map((p, i) => (
                  <li key={i}>
                    <button type="button" className={selected === i ? 'selected' : ''} aria-pressed={selected === i} onClick={() => setSelected(selected === i ? null : i)}>
                      <span className="pair-num">{i + 1}</span>
                      <span className="mono muted">{p.room.map((v) => v.toFixed(2)).join(', ')}</span>
                      <span className={`num push ${solution && solution.errors_px[i] > 3 * Math.max(solution.rms_px, 1) ? 'text-red' : 'muted'}`}>
                        {solution ? `${fmt(solution.errors_px[i], 1)} px` : ''}
                      </span>
                    </button>
                    <button
                      type="button"
                      className="icon-btn"
                      aria-label={`Remove pair ${i + 1}`}
                      onClick={() => {
                        setPairs((list) => list.filter((_, j) => j !== i));
                        setSelected(null);
                      }}
                    >
                      <TrashIcon size={13} aria-hidden="true" />
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="selected-foot">
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              disabled={!pairs.length || busy}
              onClick={() => {
                setPairs([]);
                setCurrent(null);
              }}
            >
              Clear pairs
            </button>
            <span className="push" />
            {registration && (
              <button type="button" className="btn btn-sm btn-danger" disabled={busy} onClick={unregister}>
                Remove
              </button>
            )}
            <button type="button" className="btn btn-primary btn-sm" disabled={!solution || !dirty || busy} onClick={save}>
              {busy ? 'Saving…' : registration ? 'Save registration' : 'Register camera'}
            </button>
          </div>
        </div>
      </aside>
      {photo.cropDialog}
    </div>
  );
}

// ---------------------------------------------------------------- page

export default function Room() {
  const { state } = useLive();
  const [params, setParams] = useSearchParams();
  const [rooms, setRooms] = useState(null);
  const [room, setRoom] = useState(null);
  const [views, setViews] = useState([]);
  const [error, setError] = useState(null);
  const roomId = params.get('room') || rooms?.[0]?.room_id || null;
  const tab = params.get('tab') === 'cameras' ? 'cameras' : 'regions';
  const medications = state.layout?.medications || [];

  const setParam = (key, value) => {
    const next = new URLSearchParams(params);
    value ? next.set(key, value) : next.delete(key);
    setParams(next, { replace: true });
  };

  const loadRooms = useCallback(() => request('/api/rooms').then((r) => setRooms(r.rooms)).catch((e) => setError(e.message)), []);
  const loadViews = useCallback(() => request('/api/layouts').then((r) => setViews(r.layouts)).catch(() => setViews([])), []);
  useEffect(() => {
    loadRooms();
  }, [loadRooms]);
  // Views change from other places too (uploads, regions following the 3D tags).
  useEffect(() => {
    loadViews();
  }, [loadViews, state.store?.history_count]);
  useEffect(() => {
    if (!roomId) {
      setRoom(null);
      return;
    }
    setRoom(null);
    request(`/api/rooms/${encodeURIComponent(roomId)}`).then(setRoom).catch((e) => setError(e.message));
  }, [roomId]);

  const adopt = (saved) => {
    setRoom(saved);
    loadRooms();
  };

  const remove = async () => {
    const cams = room.cameras.map((c) => views?.find((v) => v.layout_id === c.layout_id)?.name || c.layout_id);
    if (
      !window.confirm(
        `Delete the room ${room.name}? Its 3D scan, shelf regions and camera registrations are removed permanently. ` +
          (cams.length
            ? `The camera ${cams.length === 1 ? 'view' : 'views'} ${cams.join(', ')} registered here will have no shelf regions until registered in another room. `
            : '') +
          'Recordings and inventory are kept.',
      )
    )
      return;
    const res = await fetch(`/api/rooms/${room.room_id}`, { method: 'DELETE' });
    if (!res.ok) {
      // Keep the page; a failed delete isn't a failure to load rooms.
      window.alert(`Couldn't delete ${room.name}: ${await errorMessage(res)}`);
      return;
    }
    setParam('room', null);
    setRoom(null);
    loadRooms();
  };

  if (error) return <Empty>Could not load rooms: {error}</Empty>;
  if (!rooms) return <Empty>Loading rooms…</Empty>;

  if (!rooms.length) {
    return (
      <>
        <PageHeader title="Room" subtitle="A 3D scan of the pharmacy, with shelves tagged and cameras placed in it." />
        <div className="card">
          <EmptyState icon={CubeIcon} title="Import a room scan">
            Scan the room with a LiDAR iPhone (for example Polycam or 3D Scanner App), export it as GLB, and import it here. Walk slowly,
            keep the floor in view, and cover the shelf fronts and whatever the cameras see.
          </EmptyState>
          <div className="card-body">
            <UploadScan onUploaded={(r) => { setParam('room', r.room_id); loadRooms(); }} />
          </div>
        </div>
      </>
    );
  }

  const size = room ? room.bounds_max.map((v, i) => v - room.bounds_min[i]) : null;

  return (
    <>
      <PageHeader
        title="Room"
        subtitle={
          room
            ? `${room.name}: ${fmt(size[0], 1)} × ${fmt(size[2], 1)} m floor, ${room.mesh.triangles.toLocaleString()} triangles, ${formatBytes(room.mesh.bytes)}. ${
                room.floor_fit.tilt_corrected ? `Leveled by ${fmt(room.floor_fit.tilt_deg, 1)}°.` : 'Floor tilt looked too large to correct.'
              }`
            : 'Loading…'
        }
      >
        <div className="segmented" role="tablist" aria-label="Room sections">
          <button type="button" role="tab" aria-selected={tab === 'regions'} className={tab === 'regions' ? 'active' : ''} onClick={() => setParam('tab', null)}>
            Shelves &amp; regions <span className="count">{room?.regions.length ?? '–'}</span>
          </button>
          <button type="button" role="tab" aria-selected={tab === 'cameras'} className={tab === 'cameras' ? 'active' : ''} onClick={() => setParam('tab', 'cameras')}>
            Cameras <span className="count">{room?.cameras.length ?? '–'}</span>
          </button>
        </div>
        {rooms.length > 1 && (
          <select className="input input-sm" aria-label="Room" value={roomId || ''} onChange={(e) => setParam('room', e.target.value)}>
            {rooms.map((r) => (
              <option key={r.room_id} value={r.room_id}>
                {r.name}
              </option>
            ))}
          </select>
        )}
        {room && (
          <button type="button" className="icon-btn bordered" onClick={remove} aria-label={`Delete the room ${room.name}`} title="Delete this room">
            <TrashIcon aria-hidden="true" />
          </button>
        )}
        <details className="menu">
          <summary className="btn btn-sm">Scan…</summary>
          <div className="menu-panel card">
            <UploadScan compact onUploaded={(r) => { setParam('room', r.room_id); loadRooms(); }} />
            {room && (
              <button type="button" className="btn btn-sm btn-danger" onClick={remove}>
                <TrashIcon size={13} aria-hidden="true" /> Delete this scan
              </button>
            )}
          </div>
        </details>
      </PageHeader>
      {!room ? (
        <Empty>Loading the scan…</Empty>
      ) : tab === 'regions' ? (
        <RegionsTab key={room.room_id} room={room} medications={medications} onSaved={adopt} />
      ) : (
        <CamerasTab
          key={room.room_id}
          room={room}
          views={views}
          viewId={params.get('view')}
          onViewId={(id) => setParam('view', id)}
          onSaved={adopt}
          onViewsChanged={loadViews}
        />
      )}
    </>
  );
}
