import React, { useState } from 'react';
import { useLive } from '../lib/live';
import { request } from '../lib/api';
import { bottleCounts, formatDate, formatNumber, isExpired, medLabel, plural } from '../lib/format';
import { Badge, Dialog } from './ui';

/** Take bottles of one batch off the shelf and out of stock, e.g. expired ones an employee found. */
export default function DisposeBatchDialog({ receipt, onClose }) {
  const { state, refresh } = useLive();
  const inv = state.inventory[receipt.medication_key];
  const counts = inv ? bottleCounts(state.layout, inv) : { onShelf: 0, total: 0 };
  const onShelf = inv?.shelf_counts[`shelf_${receipt.medication_key.toLowerCase()}`] ?? counts.onShelf;
  const max = Math.min(receipt.remaining_bottles, onShelf);
  const med = state.layout?.medications.find((m) => m.medication_key === receipt.medication_key);
  const unit = med?.unit || 'tablets';
  const [bottles, setBottles] = useState(String(Math.max(1, max)));
  const [tablets, setTablets] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const n = Number(bottles);
  const validBottles = Number.isInteger(n) && n >= 1 && n <= max;
  const lastBottles = inv && validBottles && n === inv.total_bottles;
  const t = tablets === '' ? null : Number(tablets);
  const validTablets = t === null || (Number.isInteger(t) && t >= 0);

  const submit = async (e) => {
    e.preventDefault();
    if (!validBottles || !validTablets) return;
    setSaving(true);
    setError(null);
    try {
      await request(`/api/inventory/receipts/${encodeURIComponent(receipt.receipt_id)}/dispose`, {
        method: 'POST',
        body: { bottles: n, tablets: t },
      });
      await refresh();
      onClose();
    } catch (err) {
      setError(err.message);
      setSaving(false);
    }
  };

  return (
    <Dialog
      title="Dispose of bottles from this batch"
      onClose={onClose}
      width={480}
      footer={
        <>
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form="dispose-batch-form" className="btn btn-danger-solid" disabled={saving || !validBottles || !validTablets || max === 0}>
            {saving ? 'Disposing…' : `Dispose of ${validBottles ? plural(n, 'bottle') : 'bottles'}`}
          </button>
        </>
      }
    >
      <form id="dispose-batch-form" className="form" onSubmit={submit} noValidate>
        <div className="batch-summary">
          <div className="row-title">
            {medLabel(state.layout?.medications, receipt.medication_key)}
            {isExpired(receipt.expiry_date) && <Badge tone="red">Expired</Badge>}
          </div>
          <div className="row-sub">
            Batch <span className="mono">{receipt.receipt_id}</span>
            {receipt.lot_number && `, lot ${receipt.lot_number}`}, expires {formatDate(receipt.expiry_date)}.{' '}
            {plural(receipt.remaining_bottles, 'bottle')} left in this batch, {onShelf} on the shelf.
          </div>
        </div>
        {max === 0 ? (
          <p className="form-error">
            No bottles are on the shelf right now. Return the bottle to its shelf first, or let the camera record it going into the trash.
          </p>
        ) : (
          <div className="grid-2">
            <label className="field">
              <span className="label">Bottles to dispose</span>
              <input
                className="input"
                type="number"
                name="bottles"
                autoComplete="off"
                inputMode="numeric"
                min="1"
                max={max}
                value={bottles}
                aria-invalid={!validBottles || undefined}
                onChange={(e) => setBottles(e.target.value)}
              />
              {!validBottles && <span className="row-sub text-red">Between 1 and {max}.</span>}
            </label>
            <label className="field">
              <span className="label">Discarded {unit}</span>
              <input
                className="input"
                type="number"
                name="tablets"
                autoComplete="off"
                inputMode="numeric"
                min="0"
                placeholder={lastBottles ? `Blank: all ${formatNumber(inv.pooled_tablets)}…` : 'Blank: 0…'}
                value={tablets}
                aria-invalid={!validTablets || undefined}
                onChange={(e) => setTablets(e.target.value)}
              />
            </label>
          </div>
        )}
        <p className="hint">
          {lastBottles
            ? `These are the last bottles of this medication, so a blank entry discards its whole balance (${formatNumber(inv.pooled_tablets)} ${unit}).`
            : `Other bottles remain, so a blank entry assumes these bottles were empty. Enter the tablets inside if you counted them.`}{' '}
          {receipt.remaining_bottles - (validBottles ? n : 0) === 0 && isExpired(receipt.expiry_date) && 'This clears the expiry alert.'}
        </p>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
      </form>
    </Dialog>
  );
}
