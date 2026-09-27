import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { PlusIcon, TrashIcon } from '@phosphor-icons/react';
import { useLive } from '../lib/live';
import { request } from '../lib/api';
import { formatNumber, medicationKeyFor, nextId, todayIso } from '../lib/format';
import { Badge, Card, Dialog, Empty } from './ui';

const UNITS = ['tablets', 'capsules'];

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
  });
  draft.receipts.forEach((r) => {
    if (!Number.isInteger(r.bottle_count) || r.bottle_count < 1) issues.push(`${r.receipt_id}: bottles must be at least 1`);
    if (!Number.isInteger(r.tablets_per_bottle) || r.tablets_per_bottle < 0)
      issues.push(`${r.receipt_id}: quantity per bottle must be 0 or more`);
    if (!r.expiry_date) issues.push(`${r.receipt_id}: expiry date is required`);
  });
  return [...new Set(issues)];
}

/** Which 3D shelves hold each medication, across every scanned room. */
function useShelves() {
  const [shelves, setShelves] = useState({});
  const load = useCallback(async () => {
    try {
      const { rooms } = await request('/api/rooms');
      const full = await Promise.all(rooms.map((r) => request(`/api/rooms/${encodeURIComponent(r.room_id)}`)));
      const map = {};
      full.forEach((room) =>
        room.regions
          .filter((r) => r.region_type === 'designated_shelf' && r.medication_key)
          .forEach((r) => (map[r.medication_key] ||= []).push({ room_id: room.room_id, room: room.name || room.room_id })),
      );
      setShelves(map);
    } catch {
      setShelves({});
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);
  return shelves;
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

function MedicationCard({ med, shelves, receipts, onChange, onRemove, onAddReceipt, onChangeReceipt, onRemoveReceipt }) {
  const bottles = receipts.reduce((s, r) => s + (r.bottle_count || 0), 0);
  const units = receipts.reduce((s, r) => s + (r.bottle_count || 0) * (r.tablets_per_bottle || 0), 0);
  // The key is the drug + strength, and 3D shelves hold the key: renaming would orphan them.
  const locked = shelves.length > 0;
  const lockHint = locked ? 'To rename, first clear this shelf’s medication on the Room page.' : undefined;
  return (
    <div className="med-card">
      <div className="med-head">
        <input className="input input-strong" name="drug" autoComplete="off" value={med.name} readOnly={locked} title={lockHint} onChange={(e) => onChange({ name: e.target.value })} aria-label="Drug" />
        <input className="input w-strength" name="strength" autoComplete="off" value={med.strength} readOnly={locked} title={lockHint} onChange={(e) => onChange({ strength: e.target.value })} aria-label="Strength" />
        <select className="input w-unit" name="unit" value={med.unit} onChange={(e) => onChange({ unit: e.target.value })} aria-label="Unit">
          {UNITS.map((u) => (
            <option key={u}>{u}</option>
          ))}
        </select>
        <button type="button" className="icon-btn" onClick={onRemove} disabled={locked} title={locked ? 'Remove its shelf on the Room page first' : undefined} aria-label={`Remove ${med.name} ${med.strength}`}>
          <TrashIcon aria-hidden="true" />
        </button>
      </div>
      <div className="med-meta">
        <span className="mono muted" translate="no">
          {med.medication_key}
        </span>
        {locked ? (
          <Link to={`/room?room=${encodeURIComponent(shelves[0].room_id)}`} className="link-btn">
            Shelf in {shelves[0].room}
          </Link>
        ) : (
          <Link to="/room" className="btn btn-sm btn-warn">
            Tag its shelf
          </Link>
        )}
      </div>

      <div className="receipts">
        {receipts.map((r) => (
          <div key={r.receipt_id} className="receipt">
            <div className="receipt-head">
              <span className="mono" translate="no">
                {r.receipt_id}
              </span>
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
      <label className="field reorder-field">
        <span className="label">Suggest reordering at</span>
        <input
          className="input"
          type="number"
          name="reorder-point"
          autoComplete="off"
          inputMode="numeric"
          min="0"
          placeholder={`${formatNumber(Math.round(units * 0.2))} (20% of opening stock)…`}
          value={med.reorder_point ?? ''}
          onChange={(e) => onChange({ reorder_point: e.target.value === '' ? null : Math.max(0, Math.floor(Number(e.target.value))) })}
        />
        <span className="hint">A &ldquo;Running low&rdquo; suggestion appears when {med.unit} in stock fall to this. Leave blank for the default.</span>
      </label>
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

/**
 * Medications and their opening stock (receiving records, expiry, lots), shared by every
 * camera view. Shelves are tagged in 3D on the Room page and hold a medication by key.
 */
export default function StockEditor({ id }) {
  const { refresh } = useLive();
  const shelves = useShelves();
  const [draft, setDraft] = useState(null);
  const [savedJson, setSavedJson] = useState('');
  const [loadError, setLoadError] = useState(null);
  const [confirming, setConfirming] = useState(false);
  const [resetOnSave, setResetOnSave] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState(null);

  const adopt = (catalog) => {
    const next = { medications: catalog.medications, receipts: catalog.receipts };
    setDraft(next);
    setSavedJson(JSON.stringify(next));
  };
  useEffect(() => {
    request('/api/catalog').then(adopt).catch((e) => setLoadError(e.message));
  }, []);

  const dirty = draft && JSON.stringify(draft) !== savedJson;
  const issues = useMemo(() => (draft ? validate(draft) : []), [draft]);

  useEffect(() => {
    if (!dirty) return undefined;
    const warn = (e) => e.preventDefault();
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  const patch = (fn) => setDraft((d) => ({ ...d, ...fn(d) }));

  const updateMedication = (oldKey, change) =>
    patch((d) => {
      const current = d.medications.find((m) => m.medication_key === oldKey);
      const next = { ...current, ...change };
      next.medication_key = medicationKeyFor(next.name, next.strength);
      return {
        medications: d.medications.map((m) => (m.medication_key === oldKey ? next : m)),
        receipts: d.receipts.map((r) => (r.medication_key === oldKey ? { ...r, medication_key: next.medication_key } : r)),
      };
    });

  const removeMedication = (key) => {
    const med = draft.medications.find((m) => m.medication_key === key);
    if (!window.confirm(`Remove ${med.name} ${med.strength} and its batches?`)) return;
    patch((d) => ({
      medications: d.medications.filter((m) => m.medication_key !== key),
      receipts: d.receipts.filter((r) => r.medication_key !== key),
    }));
  };

  const addReceipt = (medKey) =>
    patch((d) => {
      const today = todayIso();
      const receipt_id = nextId(`REC_${medKey}`, d.receipts.map((r) => r.receipt_id));
      return {
        receipts: [
          ...d.receipts,
          { receipt_id, medication_key: medKey, bottle_count: 1, tablets_per_bottle: 100, expiry_date: plusYears(today, 1), lot_number: null, received_at: `${today}T00:00:00Z` },
        ],
      };
    });
  const changeReceipt = (rid, change) => patch((d) => ({ receipts: d.receipts.map((r) => (r.receipt_id === rid ? { ...r, ...change } : r)) }));
  const removeReceipt = (rid) => patch((d) => ({ receipts: d.receipts.filter((r) => r.receipt_id !== rid) }));

  const save = async () => {
    setSaving(true);
    setMessage(null);
    try {
      const body = { ...draft, receipts: draft.receipts.map((r) => ({ ...r, lot_number: r.lot_number?.trim() || null })) };
      const res = await request(`/api/catalog${resetOnSave ? '?reset_inventory=true' : ''}`, { method: 'PUT', body });
      adopt(res.catalog);
      const extra = res.inventory_reset
        ? ' Inventory was reset to the opening stock.'
        : res.notes?.length
          ? ` Live inventory kept. ${res.notes.join(' ')}`
          : ' Live inventory was kept.';
      setMessage({ tone: 'green', text: `Saved ${res.catalog.medications.length} medications.${extra}` });
      await refresh();
    } catch (e) {
      setMessage({ tone: 'red', text: e.message });
    } finally {
      setSaving(false);
      setConfirming(false);
    }
  };

  const noShelf = draft ? draft.medications.filter((m) => !shelves[m.medication_key]) : [];

  return (
    <Card
      id={id}
      title="Medications & opening stock"
      subtitle="Shared by every camera view. Tag each medication's shelf in 3D on the Room page."
      className="section-gap"
      actions={
        draft && (
          <>
            {dirty ? <Badge tone="amber">Unsaved changes</Badge> : null}
            <button type="button" className="btn btn-ghost btn-sm" disabled={!dirty || saving} onClick={() => setDraft(JSON.parse(savedJson))}>
              Discard
            </button>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              disabled={!dirty || issues.length > 0 || saving}
              onClick={() => {
                setResetOnSave(false);
                setConfirming(true);
              }}
            >
              Save
            </button>
          </>
        )
      }
    >
      {loadError ? (
        <Empty>Could not load medications: {loadError}</Empty>
      ) : !draft ? (
        <Empty>Loading medications…</Empty>
      ) : (
        <div className="stock-editor">
          {message && (
            <div className={`banner banner-${message.tone}`} role="status">
              {message.text}
            </div>
          )}
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
          {noShelf.length > 0 && (
            <p className="hint text-amber">
              {noShelf.map((m) => `${m.name} ${m.strength}`).join(', ')} {noShelf.length === 1 ? 'has' : 'have'} no shelf tagged yet.{' '}
              <Link to="/room">Tag shelves on the Room page</Link>.
            </p>
          )}
          <AddMedication existingKeys={draft.medications.map((m) => m.medication_key)} onAdd={(med) => patch((d) => ({ medications: [...d.medications, med] }))} />
          <div className="med-grid">
            {draft.medications.map((m, i) => (
              <MedicationCard
                key={i}
                med={m}
                shelves={shelves[m.medication_key] || []}
                receipts={draft.receipts.filter((r) => r.medication_key === m.medication_key)}
                onChange={(change) => updateMedication(m.medication_key, change)}
                onRemove={() => removeMedication(m.medication_key)}
                onAddReceipt={() => addReceipt(m.medication_key)}
                onChangeReceipt={changeReceipt}
                onRemoveReceipt={removeReceipt}
              />
            ))}
          </div>
        </div>
      )}

      {confirming && (
        <Dialog
          title="Save medications and opening stock?"
          onClose={() => setConfirming(false)}
          footer={
            <>
              <button type="button" className="btn btn-ghost" onClick={() => setConfirming(false)}>
                Cancel
              </button>
              <button type="button" className={`btn ${resetOnSave ? 'btn-danger-solid' : 'btn-primary'}`} onClick={save} disabled={saving}>
                {saving ? 'Saving…' : resetOnSave ? 'Save and reset inventory' : 'Save'}
              </button>
            </>
          }
        >
          <div className="form">
            <p className="lead">This saves the medications and opening stock for every camera view.</p>
            <div className="choice-list" role="radiogroup" aria-label="Live inventory">
              <label className={`choice ${!resetOnSave ? 'selected' : ''}`}>
                <input type="radio" name="save-mode" checked={!resetOnSave} onChange={() => setResetOnSave(false)} />
                <span className="choice-main">
                  <span className="row-title">Keep live inventory</span>
                  <span className="row-sub">Counts, alerts and applied recordings stay. New medications start with their opening stock.</span>
                </span>
              </label>
              <label className={`choice ${resetOnSave ? 'selected' : ''}`}>
                <input type="radio" name="save-mode" checked={resetOnSave} onChange={() => setResetOnSave(true)} />
                <span className="choice-main">
                  <span className="row-title">Reset inventory to opening stock</span>
                  <span className="row-sub">Replaces live counts, shipments, alerts and disposals. Every recording becomes unapplied.</span>
                </span>
              </label>
            </div>
          </div>
        </Dialog>
      )}
    </Card>
  );
}
