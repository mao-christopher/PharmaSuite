import React, { useState } from 'react';
import { useLive } from '../lib/live';
import { formatDate, isExpired, medLabel } from '../lib/format';
import { Badge, Dialog, Empty } from './ui';

export default function DisposalDialog({ disposal, onClose }) {
  const { state, submitDisposal } = useLive();
  const med = state.layout?.medications.find((m) => m.medication_key === disposal.medication_key);
  const unit = med?.unit || 'tablets';
  const candidates = state.receipts.filter(
    (r) => r.medication_key === disposal.medication_key && r.remaining_bottles > 0,
  );
  const [receiptId, setReceiptId] = useState(candidates.length === 1 ? candidates[0].receipt_id : '');
  const [quantity, setQuantity] = useState('');
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);

  const inv = state.inventory[disposal.medication_key];
  const wasLastBottle = inv?.total_bottles === 0;
  const defaultQty = disposal.quantity_deducted;

  const submit = async (e) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await submitDisposal(disposal.disposal_id, receiptId, quantity === '' ? null : Number(quantity));
      onClose();
    } catch (err) {
      setError(err.message);
      setSaving(false);
    }
  };

  return (
    <Dialog
      title="Identify disposed bottle"
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            Later
          </button>
          <button className="btn btn-primary" form="disposal-form" type="submit" disabled={!receiptId || saving}>
            Confirm disposal
          </button>
        </>
      }
    >
      <form id="disposal-form" onSubmit={submit} className="form">
        <p className="lead">
          One bottle of <strong>{medLabel(state.layout?.medications, disposal.medication_key)}</strong> was placed in
          the trash and removed from stock. Which batch did it come from?
        </p>

        <fieldset className="field">
          <legend className="label">Received batch</legend>
          {candidates.length === 0 ? (
            <Empty>No batches with remaining bottles. Record this as a reconciliation issue.</Empty>
          ) : (
            <div className="choice-list">
              {candidates.map((r) => (
                <label key={r.receipt_id} className={`choice ${receiptId === r.receipt_id ? 'selected' : ''}`}>
                  <input
                    type="radio"
                    name="receipt"
                    value={r.receipt_id}
                    checked={receiptId === r.receipt_id}
                    onChange={() => setReceiptId(r.receipt_id)}
                  />
                  <div className="choice-main">
                    <div className="row-title">
                      {r.lot_number ? `Lot ${r.lot_number}` : r.receipt_id}
                      {isExpired(r.expiry_date) && <Badge tone="red">Expired</Badge>}
                    </div>
                    <div className="row-sub">
                      Expires {formatDate(r.expiry_date)} · {r.remaining_bottles} of {r.bottle_count} bottles left ·{' '}
                      {r.receipt_id}
                    </div>
                  </div>
                </label>
              ))}
            </div>
          )}
        </fieldset>

        <label className="field">
          <span className="label">Discarded {unit}</span>
          <input
            className="input"
            type="number"
            min="0"
            inputMode="numeric"
            placeholder={`Leave blank to keep ${defaultQty}`}
            value={quantity}
            onChange={(e) => setQuantity(e.target.value)}
          />
          <span className="hint">
            {wasLastBottle
              ? `This was the last bottle, so a blank entry discards its whole remaining balance (${defaultQty} ${unit}).`
              : `Other bottles remain, so a blank entry assumes this bottle was empty (0 ${unit}). You can correct it later.`}
          </span>
        </label>
        {error && <p className="form-error">{error}</p>}
      </form>
    </Dialog>
  );
}
