import React, { useState } from 'react';
import { useLive } from '../lib/live';
import { errorMessage, request } from '../lib/api';
import { plural } from '../lib/format';
import { Dialog } from './ui';

const MODES = [
  { id: 'form', label: 'Enter manually' },
  { id: 'file', label: 'Upload file' },
];
const STATUSES = [
  { id: 'created', label: 'Waiting', hint: 'Not deducted until filled or paid.' },
  { id: 'confirmed_fill', label: 'Filled', hint: 'Deducts its tablets now, once.' },
  { id: 'paid', label: 'Paid', hint: 'Deducts its tablets now, once.' },
];
const EXAMPLE = 'rx,medication,strength,quantity,status\nRX_2001,Amoxicillin,500mg,30,waiting\nRX_2002,Ibuprofen,200mg,60,filled';

export default function AddPrescriptionDialog({ onClose }) {
  const { state, refresh } = useLive();
  const meds = state.layout?.medications || [];
  const [mode, setMode] = useState('form');
  const [med, setMed] = useState(meds[0]?.medication_key || '');
  const [quantity, setQuantity] = useState('30');
  const [rx, setRx] = useState('');
  const [status, setStatus] = useState('created');
  const [file, setFile] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [done, setDone] = useState(null);

  const q = Number(quantity);
  const validForm = med && Number.isInteger(q) && q >= 1;
  const unit = meds.find((m) => m.medication_key === med)?.unit || 'tablets';

  const submit = async (e) => {
    e.preventDefault();
    setError(null);
    if (mode === 'form' && !validForm) return setError('Choose a medication and a quantity of at least 1.');
    if (mode === 'file' && !file) return setError('Choose a CSV or JSON file.');
    setSaving(true);
    try {
      if (mode === 'form') {
        await request('/api/transactions', {
          method: 'POST',
          body: { medication_key: med, quantity: q, transaction_id: rx.trim() || null, status },
        });
        await refresh();
        onClose();
        return undefined;
      }
      const body = new FormData();
      body.append('file', file);
      const res = await fetch('/api/transactions/import', { method: 'POST', body });
      if (!res.ok) throw new Error(await errorMessage(res));
      const result = await res.json();
      await refresh();
      setDone(result.transactions.length);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
    return undefined;
  };

  return (
    <Dialog
      title="Add prescriptions"
      onClose={onClose}
      width={500}
      footer={
        done != null ? (
          <button type="button" className="btn btn-primary" onClick={onClose}>
            Done
          </button>
        ) : (
          <>
            <button type="button" className="btn btn-ghost" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" form="rx-form" className="btn btn-primary" disabled={saving}>
              {saving ? 'Adding…' : mode === 'form' ? 'Add prescription' : 'Import prescriptions'}
            </button>
          </>
        )
      }
    >
      {done != null ? (
        <p className="lead" role="status">
          Imported {plural(done, 'prescription')}. Filled and paid ones were deducted from stock.
        </p>
      ) : (
        <form id="rx-form" className="form" onSubmit={submit} noValidate>
          <div className="segmented" role="group" aria-label="How to add prescriptions">
            {MODES.map((m) => (
              <button key={m.id} type="button" className={mode === m.id ? 'active' : ''} aria-pressed={mode === m.id} onClick={() => setMode(m.id)}>
                {m.label}
              </button>
            ))}
          </div>
          {mode === 'form' ? (
            <>
              <label className="field">
                <span className="label">Medication</span>
                <select className="input" name="medication" value={med} onChange={(e) => setMed(e.target.value)}>
                  {meds.map((m) => (
                    <option key={m.medication_key} value={m.medication_key}>
                      {m.name} {m.strength}
                    </option>
                  ))}
                </select>
              </label>
              <div className="grid-2">
                <label className="field">
                  <span className="label">Quantity ({unit})</span>
                  <input className="input" type="number" name="quantity" autoComplete="off" inputMode="numeric" min="1" value={quantity} onChange={(e) => setQuantity(e.target.value)} />
                </label>
                <label className="field">
                  <span className="label">Rx number (optional)</span>
                  <input className="input" name="rx" autoComplete="off" spellCheck={false} placeholder="RX_2001…" value={rx} onChange={(e) => setRx(e.target.value)} />
                </label>
              </div>
              <fieldset className="field" aria-labelledby="rx-status-label">
                <span id="rx-status-label" className="label">
                  Status
                </span>
                <div className="choice-list">
                  {STATUSES.map((s) => (
                    <label key={s.id} className={`choice ${status === s.id ? 'selected' : ''}`}>
                      <input type="radio" name="status" checked={status === s.id} onChange={() => setStatus(s.id)} />
                      <span className="choice-main">
                        <span className="row-title">{s.label}</span>
                        <span className="row-sub">{s.hint}</span>
                      </span>
                    </label>
                  ))}
                </div>
              </fieldset>
            </>
          ) : (
            <label className="field">
              <span className="label">Prescriptions file</span>
              <input className="file-input" type="file" name="prescriptions" accept=".csv,.json,.jsonl,.txt" onChange={(e) => setFile(e.target.files[0] || null)} />
              <span className="hint">
                CSV with a header row, or JSON. Give <code>medication_key</code>, or <code>medication</code> and{' '}
                <code>strength</code>, plus <code>quantity</code>. Optional: <code>rx</code> and <code>status</code> (waiting,
                filled, paid). Nothing is added if a row is invalid. For example:
              </span>
              <pre className="code-sample">{EXAMPLE}</pre>
            </label>
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
