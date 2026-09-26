import React from 'react';
import { Check } from 'lucide-react';
import { useLive } from '../lib/live';
import { TX_STATUS, medLabel } from '../lib/format';
import { Badge, Card, Empty } from './ui';

export default function Prescriptions() {
  const { state, setTransactionStatus } = useLive();
  const meds = state.layout?.medications;
  const txs = Object.values(state.transactions);

  return (
    <Card title="Prescriptions">
      {txs.length === 0 ? (
        <Empty>No prescriptions in this scenario.</Empty>
      ) : (
        <ul className="rows">
          {txs.map((tx) => {
            const unit = meds?.find((m) => m.medication_key === tx.medication_key)?.unit || 'tablets';
            const status = TX_STATUS[tx.status] || { label: tx.status, tone: 'gray' };
            const closed = tx.status === 'paid' || tx.status === 'cancelled';
            return (
              <li key={tx.transaction_id} className="row row-wrap">
                <div>
                  <div className="row-title">
                    {medLabel(meds, tx.medication_key)} · {tx.quantity} {unit}
                  </div>
                  <div className="row-sub">
                    {tx.transaction_id}
                    {tx.deducted && (
                      <span className="inline-ok">
                        <Check size={13} /> deducted from stock
                      </span>
                    )}
                  </div>
                </div>
                <div className="row-actions">
                  <Badge tone={status.tone}>{status.label}</Badge>
                  {tx.status === 'created' && (
                    <button className="btn btn-sm" onClick={() => setTransactionStatus(tx.transaction_id, 'confirmed_fill')}>
                      Confirm fill
                    </button>
                  )}
                  {!closed && (
                    <button className="btn btn-sm" onClick={() => setTransactionStatus(tx.transaction_id, 'paid')}>
                      Mark paid
                    </button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}
