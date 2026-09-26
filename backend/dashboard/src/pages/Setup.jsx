import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { CursorIcon, GridFourIcon, ImageSquareIcon, PlusIcon, TrashIcon } from '@phosphor-icons/react';
import { useLive } from '../lib/live';
import { errorMessage, request } from '../lib/api';
import { REGION_TYPES, formatNumber, medicationKeyFor, regionLabel, todayIso } from '../lib/format';
import RegionCanvas from '../components/RegionCanvas';
import { Badge, Dialog, Empty, PageHeader } from '../components/ui';

const TOOLS = [
  { id: 'select', label: 'Select', icon: CursorIcon },
  { id: 'designated_shelf', label: 'Shelf' },
  { id: 'dispensing_counter', label: 'Counter' },
  { id: 'disposal', label: 'Disposal' },
];
const UNITS = ['tablets', 'capsules'];
const isTyping = (e) => ['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target.tagName);

function nextId(prefix, existing) {
  const taken = new Set(existing);
  let n = 1;
  while (taken.has(`${prefix}_${String(n).padStart(2, '0')}`)) n += 1;
  return `${prefix}_${String(n).padStart(2, '0')}`;
}

function plusYears(iso, years) {
  const [y, m, d] = iso.split('-');
  return `${Number(y) + years}-${m}-${d}`;
}

function validate(draft) {
  const issues = [];
  const keys = draft.medications.map((m) => m.medication_key);
  draft.medications.forEach((m) => {
    const label = `${m.name || '(unnamed)'} ${m.strength}`.trim();
    if (!m.name.trim() || !m.strength.trim()) issues.push(`${label}: name and strength are required`);
    if (keys.filter((k) => k === m.medication_key).length > 1) issues.push(`${label} is listed twice`);
    const shelves = draft.regions.filter((r) => r.medication_key === m.medication_key);
    if (shelves.length === 0) issues.push(`${label} needs a shelf drawn`);
    if (shelves.length > 1) issues.push(`${label} is assigned to ${shelves.length} shelves`);
  });
  draft.regions
    .filter((r) => r.region_type === 'designated_shelf' && !keys.includes(r.medication_key))
    .forEach((r) => issues.push(`${r.region_id} has no medication assigned`));
  draft.receipts.forEach((r) => {
    if (!Number.isInteger(r.bottle_count) || r.bottle_count < 1) issues.push(`${r.receipt_id}: bottles must be at least 1`);
    if (!Number.isInteger(r.tablets_per_bottle) || r.tablets_per_bottle < 0)
      issues.push(`${r.receipt_id}: quantity per bottle must be 0 or more`);
    if (!r.expiry_date) issues.push(`${r.receipt_id}: expiry date is required`);
  });
  return [...new Set(issues)];
}

function NumberInput({ value, onChange, min = 0, name }) {
  return (
    <input
      className="input"
      type="number"
      name={name}
      autoComplete="off"
      inputMode="numeric"
      min={min}
      value={Number.isNaN(value) ? '' : value}
      onChange={(e) => onChange(e.target.value === '' ? NaN : Math.floor(Number(e.target.value)))}
    />
  );
}

function AddMedication({ existingKeys, onAdd }) {
  const [name, setName] = useState('');
  const [strength, setStrength] = useState('');
  const [unit, setUnit] = useState('tablets');
  const key = name.trim() && strength.trim() ? medicationKeyFor(name, strength) : '';
  const duplicate = key && existingKeys.includes(key);

  const submit = (e) => {
    e.preventDefault();
    if (!key || duplicate) return;
    onAdd({ medication_key: key, name: name.trim(), strength: strength.trim(), unit });
    setName('');
    setStrength('');
  };

  return (
    <form className="add-med" onSubmit={submit}>
      <div className="grid-3">
        <label className="field">
          <span className="label">Drug</span>
          <input className="input" name="drug" autoComplete="off" placeholder="Amoxicillin…" value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field">
          <span className="label">Strength</span>
          <input className="input" name="strength" autoComplete="off" placeholder="500mg…" value={strength} onChange={(e) => setStrength(e.target.value)} />
        </label>
        <label className="field">
          <span className="label">Unit</span>
          <select className="input" name="unit" value={unit} onChange={(e) => setUnit(e.target.value)}>
            {UNITS.map((u) => (
              <option key={u}>{u}</option>
            ))}
          </select>
        </label>
      </div>
      <div className="add-med-footer">
        <span className={duplicate ? 'hint text-red' : 'hint mono'}>
          {duplicate ? `${key} already exists` : key || 'Each drug + strength is its own medication'}
        </span>
        <button className="btn btn-sm" type="submit" disabled={!key || duplicate}>
          <PlusIcon size={13} aria-hidden="true" /> Add medication
        </button>
      </div>
    </form>
  );
}

function MedicationCard({ med, shelf, receipts, onChange, onRemove, onDrawShelf, onSelectShelf, onAddReceipt, onChangeReceipt, onRemoveReceipt }) {
  const bottles = receipts.reduce((s, r) => s + (r.bottle_count || 0), 0);
  const units = receipts.reduce((s, r) => s + (r.bottle_count || 0) * (r.tablets_per_bottle || 0), 0);
  return (
    <div className="med-card">
      <div className="med-head">
        <input className="input input-strong" name="drug" autoComplete="off" value={med.name} onChange={(e) => onChange({ name: e.target.value })} aria-label="Drug" />
        <input className="input w-strength" name="strength" autoComplete="off" value={med.strength} onChange={(e) => onChange({ strength: e.target.value })} aria-label="Strength" />
        <select className="input w-unit" name="unit" value={med.unit} onChange={(e) => onChange({ unit: e.target.value })} aria-label="Unit">
          {UNITS.map((u) => (
            <option key={u}>{u}</option>
          ))}
        </select>
        <button type="button" className="icon-btn" onClick={onRemove} aria-label={`Remove ${med.name} ${med.strength}`}>
          <TrashIcon aria-hidden="true" />
        </button>
      </div>
      <div className="med-meta">
        <span className="mono muted" translate="no">{med.medication_key}</span>
        {shelf ? (
          <button type="button" className="link-btn" onClick={onSelectShelf}>
            Show shelf
          </button>
        ) : (
          <button type="button" className="btn btn-sm btn-warn" onClick={onDrawShelf}>
            Draw shelf
          </button>
        )}
      </div>

      <div className="receipts">
        {receipts.map((r) => (
          <div key={r.receipt_id} className="receipt">
            <div className="receipt-head">
              <span className="mono" translate="no">{r.receipt_id}</span>
              <button type="button" className="icon-btn" onClick={() => onRemoveReceipt(r.receipt_id)} aria-label={`Remove batch ${r.receipt_id}`}>
                <TrashIcon size={14} aria-hidden="true" />
              </button>
            </div>
            <div className="grid-2">
              <label className="field">
                <span className="label">Bottles</span>
                <NumberInput min={1} name="bottles" value={r.bottle_count} onChange={(v) => onChangeReceipt(r.receipt_id, { bottle_count: v })} />
              </label>
              <label className="field">
                <span className="label">{med.unit} per bottle</span>
                <NumberInput name="per-bottle" value={r.tablets_per_bottle} onChange={(v) => onChangeReceipt(r.receipt_id, { tablets_per_bottle: v })} />
              </label>
              <label className="field">
                <span className="label">Expires</span>
                <input className="input" type="date" name="expiry" autoComplete="off" value={r.expiry_date} onChange={(e) => onChangeReceipt(r.receipt_id, { expiry_date: e.target.value })} />
              </label>
              <label className="field">
                <span className="label">Lot (optional)</span>
                <input className="input" name="lot" autoComplete="off" spellCheck={false} value={r.lot_number || ''} placeholder="LOT-12345…" onChange={(e) => onChangeReceipt(r.receipt_id, { lot_number: e.target.value })} />
              </label>
              <label className="field">
                <span className="label">Received</span>
                <input
                  className="input"
                  type="date"
                  name="received"
                  autoComplete="off"
                  value={r.received_at.slice(0, 10)}
                  onChange={(e) => e.target.value && onChangeReceipt(r.receipt_id, { received_at: `${e.target.value}T00:00:00Z` })}
                />
              </label>
            </div>
          </div>
        ))}
      </div>
      <div className="med-footer">
        <span className="muted num">
          Opening stock: {bottles} bottles, {formatNumber(units)} {med.unit}
        </span>
        <button type="button" className="btn btn-sm" onClick={onAddReceipt}>
          <PlusIcon size={13} aria-hidden="true" /> Add batch
        </button>
      </div>
    </div>
  );
}

export default function Setup() {
  const { state, refresh } = useLive();
  const layoutId = state.layout?.layout_id || 'default';
  const [draft, setDraft] = useState(null);
  const [savedJson, setSavedJson] = useState('');
  const [loadError, setLoadError] = useState(null);
  const [tool, setTool] = useState('select');
  const [selectedId, setSelectedId] = useState(null);
  const [pendingMed, setPendingMed] = useState(null);
  const [params, setParams] = useSearchParams();
  const tab = params.get('tab') === 'stock' ? 'stock' : 'regions';
  const setTab = (next) => setParams(next === 'stock' ? { tab: 'stock' } : {}, { replace: true });
  const [showGrid, setShowGrid] = useState(true);
  const [confirming, setConfirming] = useState(false);
  const [resetOnSave, setResetOnSave] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState(null);
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef(null);

  const adopt = (layout) => {
    setDraft(layout);
    setSavedJson(JSON.stringify(layout));
  };

  useEffect(() => {
    request(`/api/layouts/${layoutId}`)
      .then(adopt)
      .catch((e) => setLoadError(e.message));
  }, [layoutId]);

  const dirty = draft && JSON.stringify(draft) !== savedJson;
  const issues = useMemo(() => (draft ? validate(draft) : []), [draft]);

  useEffect(() => {
    if (!dirty) return undefined;
    const warn = (e) => e.preventDefault();
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  const patch = (fn) => setDraft((d) => ({ ...d, ...fn(d) }));

  const setPolygon = useCallback((id, polygon) => {
    patch((d) => ({ regions: d.regions.map((r) => (r.region_id === id ? { ...r, polygon } : r)) }));
  }, []);

  const createRegion = useCallback(
    (polygon) => {
      const id = nextId(REGION_TYPES[tool].prefix, draft.regions.map((r) => r.region_id));
      const medication_key = tool === 'designated_shelf' ? pendingMed : null;
      patch((d) => ({ regions: [...d.regions, { region_id: id, region_type: tool, medication_key, polygon }] }));
      setSelectedId(id);
      setTool('select');
      setPendingMed(null);
      setTab('regions');
    },
    [tool, pendingMed, draft],
  );

  const deleteRegion = (id) => {
    patch((d) => ({ regions: d.regions.filter((r) => r.region_id !== id) }));
    setSelectedId(null);
  };

  useEffect(() => {
    if (tool !== 'select' || !selectedId) return undefined;
    const onKey = (e) => {
      if (isTyping(e)) return;
      if (e.key === 'Delete' || e.key === 'Backspace') {
        e.preventDefault();
        deleteRegion(selectedId);
      } else if (e.key === 'Escape') setSelectedId(null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [tool, selectedId]);

  const assignShelf = (regionId, medKey) =>
    patch((d) => ({
      regions: d.regions.map((r) => (r.region_id === regionId ? { ...r, medication_key: medKey || null } : r)),
    }));

  const addMedication = (med) => patch((d) => ({ medications: [...d.medications, med] }));

  const updateMedication = (oldKey, change) =>
    patch((d) => {
      const current = d.medications.find((m) => m.medication_key === oldKey);
      const next = { ...current, ...change };
      next.medication_key = medicationKeyFor(next.name, next.strength);
      const remap = (k) => (k === oldKey ? next.medication_key : k);
      return {
        medications: d.medications.map((m) => (m.medication_key === oldKey ? next : m)),
        regions: d.regions.map((r) => ({ ...r, medication_key: r.medication_key && remap(r.medication_key) })),
        receipts: d.receipts.map((r) => ({ ...r, medication_key: remap(r.medication_key) })),
      };
    });

  const removeMedication = (key) => {
    const med = draft.medications.find((m) => m.medication_key === key);
    if (!window.confirm(`Remove ${med.name} ${med.strength} and its batches? Its shelf will become unassigned.`)) return;
    patch((d) => ({
      medications: d.medications.filter((m) => m.medication_key !== key),
      receipts: d.receipts.filter((r) => r.medication_key !== key),
      regions: d.regions.map((r) => (r.medication_key === key ? { ...r, medication_key: null } : r)),
    }));
  };

  const addReceipt = (medKey) =>
    patch((d) => {
      const today = todayIso();
      const receipt_id = nextId(`REC_${medKey}`, d.receipts.map((r) => r.receipt_id));
      return {
        receipts: [
          ...d.receipts,
          {
            receipt_id,
            medication_key: medKey,
            bottle_count: 1,
            tablets_per_bottle: 100,
            expiry_date: plusYears(today, 1),
            lot_number: null,
            received_at: `${today}T00:00:00Z`,
          },
        ],
      };
    });

  const changeReceipt = (id, change) =>
    patch((d) => ({ receipts: d.receipts.map((r) => (r.receipt_id === id ? { ...r, ...change } : r)) }));

  const removeReceipt = (id) => patch((d) => ({ receipts: d.receipts.filter((r) => r.receipt_id !== id) }));

  const importPhoto = async (file) => {
    if (!file) return;
    setUploading(true);
    setMessage(null);
    try {
      const body = new FormData();
      body.append('image', file);
      const res = await fetch(`/api/layouts/${draft.layout_id}/background`, { method: 'POST', body });
      if (!res.ok) throw new Error(await errorMessage(res));
      const img = await res.json();
      patch(() => ({ background_image: img.background_image, frame_width: img.width, frame_height: img.height }));
      setMessage({ tone: 'green', text: `Imported a ${img.width}×${img.height} photo. Trace the regions on it, then save the layout.` });
    } catch (e) {
      setMessage({ tone: 'red', text: `Photo import failed: ${e.message}` });
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  const save = async () => {
    setSaving(true);
    setMessage(null);
    try {
      const body = { ...draft, receipts: draft.receipts.map((r) => ({ ...r, lot_number: r.lot_number?.trim() || null })) };
      const res = await request(`/api/layouts/${draft.layout_id}${resetOnSave ? '?reset_inventory=true' : ''}`, { method: 'PUT', body });
      adopt(res.layout);
      const extra = res.inventory_reset
        ? ' Inventory was reset to the opening stock.'
        : res.notes?.length
          ? ` Live inventory kept. ${res.notes.join(' ')}`
          : ' Live inventory was kept.';
      setMessage({ tone: 'green', text: `Saved as calibration v${res.layout.calibration_version}.${extra}` });
      await refresh();
    } catch (e) {
      setMessage({ tone: 'red', text: e.message });
    } finally {
      setSaving(false);
      setConfirming(false);
    }
  };

  if (loadError) return <Empty>Could not load layout: {loadError}</Empty>;
  if (!draft) return <Empty>Loading layout…</Empty>;

  const selected = draft.regions.find((r) => r.region_id === selectedId);
  const labelFor = (r) =>
    r.region_type === 'designated_shelf' && !draft.medications.some((m) => m.medication_key === r.medication_key)
      ? 'Unassigned shelf'
      : regionLabel(draft, r.region_id);
  const takenShelfMeds = new Set(draft.regions.filter((r) => r.region_id !== selectedId).map((r) => r.medication_key));
  const pendingMedLabel = draft.medications.find((m) => m.medication_key === pendingMed);

  const hint = !draft.background_image && tool === 'select' && !selected
    ? 'Start by importing a photo taken from the camera angle, then trace shelves, counters and trash cans on it.'
    : tool === 'select'
      ? selected
        ? 'Drag the region or its corners to adjust. Click a midpoint to add a corner; Shift-click a corner to remove it. Delete removes the region.'
        : 'Click a region to edit it, or pick a tool to draw a new one.'
      : `Click to place corners${pendingMedLabel ? ` of the ${pendingMedLabel.name} ${pendingMedLabel.strength} shelf` : ''}. Click the first corner or press Enter to finish. Backspace undoes a corner; Esc cancels.`;

  return (
    <>
      <PageHeader
        title="Setup"
        subtitle={`Annotate the fixed camera view and set the opening stock. Layout "${draft.layout_id}" is shared by every recording.`}
      >
        <span className="save-status">
          {dirty ? <Badge tone="amber">Unsaved changes</Badge> : <span className="muted">Calibration v{draft.calibration_version}</span>}
        </span>
        <button type="button" className="btn btn-ghost" disabled={!dirty || saving} onClick={() => adopt(JSON.parse(savedJson))}>
          Discard changes
        </button>
        <button
          type="button"
          className="btn btn-primary"
          disabled={!dirty || issues.length > 0 || saving}
          onClick={() => {
            setResetOnSave(false);
            setConfirming(true);
          }}
        >
          Save layout
        </button>
      </PageHeader>

      {message && (
        <div className={`banner banner-${message.tone}`} role="status">
          {message.text}
        </div>
      )}

      <div className="setup">
        <div className="setup-main card">
          <div className="toolbar">
            <div className="segmented" role="toolbar" aria-label="Annotation tools">
              {TOOLS.map((t) => {
                const Icon = t.icon;
                return (
                  <button
                    key={t.id}
                    type="button"
                    className={tool === t.id ? 'active' : ''}
                    aria-pressed={tool === t.id}
                    onClick={() => {
                      setTool(t.id);
                      setPendingMed(null);
                    }}
                  >
                    {Icon ? <Icon size={14} aria-hidden="true" /> : <span className="dot" aria-hidden="true" style={{ background: REGION_TYPES[t.id].color }} />}
                    {t.label}
                  </button>
                );
              })}
            </div>
            <div className="toolbar-right">
              <input ref={fileRef} type="file" name="camera-photo" accept="image/png,image/jpeg" hidden onChange={(e) => importPhoto(e.target.files[0])} />
              <button type="button" className="btn btn-sm" onClick={() => fileRef.current?.click()} disabled={uploading}>
                <ImageSquareIcon size={14} aria-hidden="true" /> {uploading ? 'Importing…' : draft.background_image ? 'Replace photo' : 'Import photo'}
              </button>
              <button type="button" className={`btn btn-ghost btn-sm ${showGrid ? 'active' : ''}`} aria-pressed={showGrid} onClick={() => setShowGrid((v) => !v)}>
                <GridFourIcon size={14} aria-hidden="true" /> Grid
              </button>
            </div>
          </div>
          <RegionCanvas
            frameWidth={draft.frame_width}
            frameHeight={draft.frame_height}
            regions={draft.regions}
            labelFor={labelFor}
            selectedId={selectedId}
            onSelect={setSelectedId}
            tool={tool}
            onCreate={createRegion}
            onCancelTool={() => {
              setTool('select');
              setPendingMed(null);
            }}
            onChangePolygon={setPolygon}
            showGrid={showGrid}
            imageUrl={
              draft.background_image
                ? `/api/layouts/${draft.layout_id}/files/${draft.background_image}`
                : `/api/video/still?v=${draft.calibration_version}`
            }
          />
          <p className="hint canvas-hint" aria-live="polite">
            {hint}
          </p>
        </div>

        <aside className="setup-side card">
          <div className="tabs" role="tablist">
            <button type="button" role="tab" aria-selected={tab === 'regions'} className={tab === 'regions' ? 'active' : ''} onClick={() => setTab('regions')}>
              Regions <span className="count">{draft.regions.length}</span>
            </button>
            <button type="button" role="tab" aria-selected={tab === 'stock'} className={tab === 'stock' ? 'active' : ''} onClick={() => setTab('stock')}>
              Medications &amp; opening stock <span className="count">{draft.medications.length}</span>
            </button>
          </div>

          {issues.length > 0 && (
            <div className="issues">
              <div className="issues-title">
                {issues.length} {issues.length === 1 ? 'issue' : 'issues'} to fix before saving
              </div>
              <ul>
                {issues.map((i) => (
                  <li key={i}>{i}</li>
                ))}
              </ul>
            </div>
          )}

          {tab === 'regions' && (
            <div className="side-body">
              {selected && (
                <div className="selected-panel">
                  <div className="selected-head">
                    <span className="dot" aria-hidden="true" style={{ background: REGION_TYPES[selected.region_type].color }} />
                    <span className="strong">{labelFor(selected)}</span>
                    <span className="mono muted">{selected.region_id}</span>
                  </div>
                  {selected.region_type === 'designated_shelf' && (
                    <label className="field">
                      <span className="label">Medication on this shelf</span>
                      <select
                        className="input"
                        name="shelf-medication"
                        value={draft.medications.some((m) => m.medication_key === selected.medication_key) ? selected.medication_key : ''}
                        onChange={(e) => assignShelf(selected.region_id, e.target.value)}
                      >
                        <option value="">Choose a medication…</option>
                        {draft.medications.map((m) => (
                          <option key={m.medication_key} value={m.medication_key} disabled={takenShelfMeds.has(m.medication_key)}>
                            {m.name} {m.strength}
                            {takenShelfMeds.has(m.medication_key) ? ' (has a shelf)' : ''}
                          </option>
                        ))}
                      </select>
                      {draft.medications.length === 0 && (
                        <button type="button" className="link-btn" onClick={() => setTab('stock')}>
                          Add a medication first
                        </button>
                      )}
                    </label>
                  )}
                  <div className="selected-foot">
                    <span className="muted">{selected.polygon.length} corners</span>
                    <button type="button" className="btn btn-sm btn-danger" onClick={() => deleteRegion(selected.region_id)}>
                      <TrashIcon size={13} aria-hidden="true" /> Delete region
                    </button>
                  </div>
                </div>
              )}

              {draft.regions.length === 0 ? (
                <Empty>Pick Shelf, Counter or Disposal above, then click on the frame to trace a region.</Empty>
              ) : (
                Object.entries(REGION_TYPES).map(([type, meta]) => {
                  const list = draft.regions.filter((r) => r.region_type === type);
                  if (list.length === 0) return null;
                  return (
                    <div key={type} className="region-group">
                      <div className="group-title">{meta.plural}</div>
                      <ul className="region-list">
                        {list.map((r) => {
                          const unassigned = type === 'designated_shelf' && labelFor(r) === 'Unassigned shelf';
                          return (
                            <li key={r.region_id}>
                              <button type="button" className={r.region_id === selectedId ? 'selected' : ''} aria-pressed={r.region_id === selectedId} onClick={() => setSelectedId(r.region_id)}>
                                <span className="dot" aria-hidden="true" style={{ background: meta.color }} />
                                <span className={unassigned ? 'text-red' : ''}>{labelFor(r)}</span>
                                <span className="mono muted push">{r.region_id}</span>
                              </button>
                            </li>
                          );
                        })}
                      </ul>
                    </div>
                  );
                })
              )}
            </div>
          )}

          {tab === 'stock' && (
            <div className="side-body">
              <AddMedication existingKeys={draft.medications.map((m) => m.medication_key)} onAdd={addMedication} />
              {draft.medications.map((m, i) => (
                <MedicationCard
                  key={i}
                  med={m}
                  shelf={draft.regions.find((r) => r.region_type === 'designated_shelf' && r.medication_key === m.medication_key)}
                  receipts={draft.receipts.filter((r) => r.medication_key === m.medication_key)}
                  onChange={(change) => updateMedication(m.medication_key, change)}
                  onRemove={() => removeMedication(m.medication_key)}
                  onDrawShelf={() => {
                    setTool('designated_shelf');
                    setPendingMed(m.medication_key);
                  }}
                  onSelectShelf={() => {
                    const shelf = draft.regions.find((r) => r.medication_key === m.medication_key);
                    setSelectedId(shelf.region_id);
                    setTool('select');
                    setTab('regions');
                  }}
                  onAddReceipt={() => addReceipt(m.medication_key)}
                  onChangeReceipt={changeReceipt}
                  onRemoveReceipt={removeReceipt}
                />
              ))}
            </div>
          )}
        </aside>
      </div>

      {confirming && (
        <Dialog
          title="Save layout?"
          onClose={() => setConfirming(false)}
          footer={
            <>
              <button type="button" className="btn btn-ghost" onClick={() => setConfirming(false)}>
                Cancel
              </button>
              <button type="button" className={`btn ${resetOnSave ? 'btn-danger-solid' : 'btn-primary'}`} onClick={save} disabled={saving}>
                {saving ? 'Saving…' : resetOnSave ? 'Save and reset inventory' : 'Save layout'}
              </button>
            </>
          }
        >
          <div className="form">
            <p className="lead">
              This saves calibration v{draft.calibration_version + 1} for every recording that uses layout "{draft.layout_id}".
              New regions apply to signals from now on.
            </p>
            <div className="choice-list" role="radiogroup" aria-label="Live inventory">
              <label className={`choice ${!resetOnSave ? 'selected' : ''}`}>
                <input type="radio" name="save-mode" checked={!resetOnSave} onChange={() => setResetOnSave(false)} />
                <span className="choice-main">
                  <span className="row-title">Keep live inventory</span>
                  <span className="row-sub">
                    Counts, alerts and applied recordings stay. New medications start with their opening stock; bottles on a
                    redrawn shelf move to its replacement.
                  </span>
                </span>
              </label>
              <label className={`choice ${resetOnSave ? 'selected' : ''}`}>
                <input type="radio" name="save-mode" checked={resetOnSave} onChange={() => setResetOnSave(true)} />
                <span className="choice-main">
                  <span className="row-title">Reset inventory to opening stock</span>
                  <span className="row-sub">
                    Replaces live counts, shipments, alerts and disposals. Every recording becomes unapplied.
                  </span>
                </span>
              </label>
            </div>
          </div>
        </Dialog>
      )}
    </>
  );
}
