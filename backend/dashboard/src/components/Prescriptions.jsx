import React, { useState } from 'react';
import { CheckIcon, PlusIcon } from '@phosphor-icons/react';
import { useLive } from '../lib/live';
import { useDialogs } from '../lib/dialogs';
import { TX_STATUS, medLabel } from '../lib/format';
import { Badge, Card, Empty } from './ui';

export default function Prescriptions() {
  const { state, setTransactionStatus } = useLive();
  const { openAddPrescription } = useDialogs();
  const [busy, setBusy] = useState(null);
  const meds = state.layout?.medications;
  const txs = Object.values(state.transactions);

  const update = async (txId, status) => {
    setBusy(txId);
    try {
      await setTransactionStatus(txId, status);
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card
      title="Prescriptions"
      className="area-rx"
      actions={
        <button type="button" className="btn btn-sm" onClick={openAddPrescription}>
          <PlusIcon size={13} aria-hidden="true" /> Add
        </button>
      }
      flush
    >
      {txs.length === 0 ? (
        <Empty>No prescriptions yet.</Empty>
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
                    {medLabel(meds, tx.medication_key)}
                    <span className="muted num">
                      {tx.quantity} {unit}
                    </span>
                  </div>
                  <div className="row-sub">
                    <span className="mono" translate="no">
                      {tx.transaction_id}
                    </span>
                    {tx.deducted && (
                      <span className="inline-ok">
                        <CheckIcon size={13} aria-hidden="true" /> Deducted from stock
                      </span>
                    )}
                  </div>
                </div>
                <div className="row-actions">
                  <Badge tone={status.tone}>{status.label}</Badge>
                  {tx.status === 'created' && (
                    <button type="button" className="btn btn-sm" disabled={busy === tx.transaction_id} onClick={() => update(tx.transaction_id, 'confirmed_fill')}>
                      Confirm fill
                    </button>
                  )}
                  {!closed && (
                    <button type="button" className="btn btn-sm" disabled={busy === tx.transaction_id} onClick={() => update(tx.transaction_id, 'paid')}>
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
