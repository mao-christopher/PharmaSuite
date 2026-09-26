import React, { useState } from 'react';
import { useLive } from '../lib/live';
import { designatedShelfId, formatNumber, plural, todayIso } from '../lib/format';
import { Dialog } from './ui';

function nextYear() {
  const [y, m, d] = todayIso().split('-');
  return `${Number(y) + 1}-${m}-${d}`;
}

export default function ReceiveStockDialog({ medicationKey, onClose }) {
  const { state, receiveStock } = useLive();
  const meds = state.layout?.medications || [];
  const [med, setMed] = useState(medicationKey || meds[0]?.medication_key || '');
  const [bottles, setBottles] = useState('1');
  const [perBottle, setPerBottle] = useState('100');
  const [expiry, setExpiry] = useState(nextYear());
  const [lot, setLot] = useState('');
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);

  const unit = meds.find((m) => m.medication_key === med)?.unit || 'tablets';
  const bottleN = Number(bottles);
  const perN = Number(perBottle);
  const valid = med && Number.isInteger(bottleN) && bottleN >= 1 && Number.isInteger(perN) && perN >= 0 && expiry;
  const hasShelf = Boolean(designatedShelfId(state.layout, med));

  const submit = async (e) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await receiveStock({
        medication_key: med,
        bottle_count: bottleN,
        tablets_per_bottle: perN,
        expiry_date: expiry,
        lot_number: lot || null,
      });
      onClose();
    } catch (err) {
      setError(err.message);
      setSaving(false);
    }
  };

  return (
    <Dialog
      title="Receive stock"
      onClose={onClose}
      width={480}
      footer={
        <>
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" type="submit" form="receive-form" disabled={!valid || !hasShelf || saving}>
            {saving ? 'Adding…' : 'Add to stock'}
          </button>
        </>
      }
    >
      <form id="receive-form" className="form" onSubmit={submit}>
        {meds.length === 0 ? (
          <p className="lead">Add medications on the Setup page first.</p>
        ) : (
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
                <span className="label">Bottles</span>
                <input className="input" type="number" name="bottles" autoComplete="off" inputMode="numeric" min="1" value={bottles} onChange={(e) => setBottles(e.target.value)} />
              </label>
              <label className="field">
                <span className="label">{unit} per bottle</span>
                <input className="input" type="number" name="per-bottle" autoComplete="off" inputMode="numeric" min="0" value={perBottle} onChange={(e) => setPerBottle(e.target.value)} />
              </label>
              <label className="field">
                <span className="label">Expires</span>
                <input className="input" type="date" name="expiry" autoComplete="off" value={expiry} onChange={(e) => setExpiry(e.target.value)} />
              </label>
              <label className="field">
                <span className="label">Lot (optional)</span>
                <input className="input" name="lot" autoComplete="off" spellCheck={false} value={lot} placeholder="LOT-12345…" onChange={(e) => setLot(e.target.value)} />
              </label>
            </div>
            <p className="hint">
              {valid
                ? `Adds ${plural(bottleN, 'bottle')} (${formatNumber(bottleN * perN)} ${unit}) straight onto the shelf. Live counts are kept.`
                : 'Enter at least 1 bottle and a quantity per bottle.'}
            </p>
            {!hasShelf && <p className="form-error">This medication has no shelf. Draw one on the Setup page.</p>}
          </>
        )}
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
      </form>
    </Dialog>
  );
}
