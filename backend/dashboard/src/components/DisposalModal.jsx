import React, { useState } from 'react';
import { Trash2, AlertCircle, Check } from 'lucide-react';

export default function DisposalModal({ disposal, receipts, onSubmit, onClose }) {
  const [selectedReceipt, setSelectedReceipt] = useState(receipts[0]?.receipt_id || '');
  const [discardQty, setDiscardQty] = useState(disposal.quantity_deducted || 0);

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!selectedReceipt) return;
    onSubmit(disposal.disposal_id, selectedReceipt, parseInt(discardQty, 10));
  };

  return (
    <div className="modal-overlay">
      <div className="glass-panel modal-content">
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', fontSize: '1.1rem', fontWeight: 600, color: '#f43f5e' }}>
          <Trash2 size={22} />
          <span>BOTTLE DISPOSAL IDENTIFICATION FORM</span>
        </div>

        <div style={{ fontSize: '0.875rem', color: '#9ca3af', lineHeight: 1.5 }}>
          A bottle disposal in the trash region was detected for <strong style={{ color: '#fff' }}>{disposal.medication_key}</strong>.
          Please identify the received batch and document any discarded tablet quantity.
        </div>

        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <div>
            <label style={{ display: 'block', fontSize: '0.8rem', textTransform: 'uppercase', color: '#9ca3af', marginBottom: '0.4rem' }}>
              Select Candidate Receiving Batch / Expiry
            </label>
            <select
              value={selectedReceipt}
              onChange={(e) => setSelectedReceipt(e.target.value)}
              style={{
                width: '100%',
                padding: '0.65rem',
                borderRadius: '8px',
                background: 'rgba(0,0,0,0.4)',
                color: '#fff',
                border: '1px solid rgba(255,255,255,0.15)'
              }}
              required
            >
              {receipts.map((r) => (
                <option key={r.receipt_id} value={r.receipt_id}>
                  {r.receipt_id} — Exp: {r.expiry_date} (Lot: {r.lot_number || 'N/A'}, Remaining: {r.remaining_bottles} btl)
                </option>
              ))}
            </select>
          </div>

          <div>
            <label style={{ display: 'block', fontSize: '0.8rem', textTransform: 'uppercase', color: '#9ca3af', marginBottom: '0.4rem' }}>
              Discarded Tablet Quantity (Default Applied: {disposal.quantity_deducted} tabs)
            </label>
            <input
              type="number"
              min="0"
              value={discardQty}
              onChange={(e) => setDiscardQty(e.target.value)}
              style={{
                width: '100%',
                padding: '0.65rem',
                borderRadius: '8px',
                background: 'rgba(0,0,0,0.4)',
                color: '#fff',
                border: '1px solid rgba(255,255,255,0.15)'
              }}
            />
            <span style={{ fontSize: '0.75rem', color: '#6b7280', marginTop: '0.25rem', display: 'block' }}>
              Rule: 0 default assumes bottle was empty. Explicit entries update pooled balance.
            </span>
          </div>

          <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'flex-end', marginTop: '0.5rem' }}>
            <button type="button" className="btn btn-secondary" onClick={onClose}>
              Cancel / Pending
            </button>
            <button type="submit" className="btn btn-primary">
              <Check size={16} />
              Confirm Disposal
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
