import React from 'react';
import { FileText, CheckCircle, DollarSign } from 'lucide-react';

export default function PrescriptionPanel({ transactions, onUpdateStatus }) {
  const txList = Object.values(transactions || {});

  return (
    <div className="glass-panel" style={{ padding: '1.25rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      <div className="panel-header">
        <div className="panel-title">
          <FileText size={20} style={{ color: '#00f0ff' }} />
          <span>ACTIVE PRESCRIPTION TRANSACTIONS</span>
        </div>
      </div>

      {txList.length === 0 ? (
        <div style={{ color: '#9ca3af', fontSize: '0.85rem' }}>No active prescriptions.</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
          {txList.map((tx) => (
            <div key={tx.transaction_id} style={{ background: 'rgba(255,255,255,0.03)', padding: '0.85rem', borderRadius: '8px', border: '1px solid rgba(255,255,255,0.06)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.4rem', fontWeight: 600, fontSize: '0.9rem' }}>
                <span>ID: {tx.transaction_id}</span>
                <span style={{ color: tx.deducted ? '#10b981' : '#f59e0b', fontSize: '0.8rem', padding: '1px 6px', borderRadius: '4px', background: tx.deducted ? 'rgba(16,185,129,0.12)' : 'rgba(245,158,11,0.12)' }}>
                  {tx.deducted ? 'DEDUCTED' : tx.status.toUpperCase()}
                </span>
              </div>
              <div style={{ fontSize: '0.825rem', color: '#9ca3af', marginBottom: '0.6rem' }}>
                Medication: <strong style={{ color: '#fff' }}>{tx.medication_key}</strong> | Qty: <strong style={{ color: '#00f0ff' }}>{tx.quantity} tablets</strong>
              </div>

              <div style={{ display: 'flex', gap: '0.5rem' }}>
                <button
                  className="btn btn-secondary"
                  style={{ flex: 1, padding: '4px 8px', fontSize: '0.75rem' }}
                  onClick={() => onUpdateStatus(tx.transaction_id, 'confirmed_fill')}
                  disabled={tx.deducted}
                >
                  <CheckCircle size={14} />
                  Confirm Fill
                </button>
                <button
                  className="btn btn-secondary"
                  style={{ flex: 1, padding: '4px 8px', fontSize: '0.75rem' }}
                  onClick={() => onUpdateStatus(tx.transaction_id, 'paid')}
                >
                  <DollarSign size={14} />
                  Process Payment
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
