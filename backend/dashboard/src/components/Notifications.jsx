import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { BellIcon, InfoIcon, TrashIcon, WarningCircleIcon, WarningIcon } from '@phosphor-icons/react';
import { useLive } from '../lib/live';
import { useDialogs } from '../lib/dialogs';
import { ALERT_TYPES, SEVERITY_RANK, SEVERITY_TONE, formatDate, medLabel, regionLabel } from '../lib/format';
import { Badge, Card } from './ui';

const ICONS = { red: WarningCircleIcon, amber: WarningIcon, blue: InfoIcon };

function describe(alert, layout, receipts) {
  const m = alert.metadata || {};
  switch (alert.alert_type) {
    case 'misplacement':
      return `Put on the ${regionLabel(layout, m.placed_shelf)}. It belongs on the ${regionLabel(layout, m.original_shelf)}.`;
    case 'out_of_stock':
      return 'No undisposed bottles remain in the pharmacy.';
    case 'expiry': {
      const r = receipts.find((x) => x.receipt_id === m.receipt_id);
      return `Batch ${m.receipt_id}${r?.lot_number ? ` (lot ${r.lot_number})` : ''} expired ${formatDate(m.expiry_date)}. Find these bottles and dispose of them.`;
    }
    default:
      return alert.description;
  }
}

function AlertAction({ alert, onResolve, onConfirm, onReceive }) {
  switch (alert.alert_type) {
    case 'uncertainty':
      return (
        <button type="button" className="btn btn-sm btn-primary" onClick={onConfirm}>
          Confirm location
        </button>
      );
    case 'expiry':
      return (
        <Link className="btn btn-sm" to={`/inventory?receipt=${encodeURIComponent(alert.metadata.receipt_id)}`}>
          View batch
        </Link>
      );
    case 'out_of_stock':
      return (
        <button type="button" className="btn btn-sm" onClick={onReceive}>
          Receive stock
        </button>
      );
    default:
      return (
        <button type="button" className="btn btn-sm" onClick={onResolve}>
          Mark resolved
        </button>
      );
  }
}

export default function Notifications() {
  const { state, resolveAlert } = useLive();
  const { openDisposal, openConfirm, openReceive } = useDialogs();
  const [showResolved, setShowResolved] = useState(false);
  const meds = state.layout?.medications;
  const alerts = Object.values(state.alerts);
  const open = alerts
    .filter((a) => a.status === 'open')
    .sort((a, b) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity]);
  const resolved = alerts.filter((a) => a.status !== 'open');
  const pendingDisposals = Object.values(state.disposals).filter((d) => d.status === 'pending_employee_entry');
  const count = open.length + pendingDisposals.length;

  return (
    <Card
      title="Notifications"
      className="area-alerts"
      actions={count > 0 ? <Badge tone="red">{count} open</Badge> : <Badge tone="green">All clear</Badge>}
      flush
      footer={
        resolved.length > 0 && (
          <>
            <button type="button" className="link-btn" aria-expanded={showResolved} onClick={() => setShowResolved((v) => !v)}>
              {showResolved ? 'Hide' : 'Show'} resolved ({resolved.length})
            </button>
            {showResolved && (
              <ul className="resolved-list">
                {resolved.map((a) => (
                  <li key={a.alert_id}>
                    <span>{ALERT_TYPES[a.alert_type] || a.alert_type}</span>
                    <span className="muted">{medLabel(meds, a.medication_key)}</span>
                  </li>
                ))}
              </ul>
            )}
          </>
        )
      }
    >
      <div aria-live="polite">
        {count === 0 && (
          <div className="notice-empty">
            <span className="notice-icon tone-green" aria-hidden="true">
              <BellIcon />
            </span>
            <span>Nothing needs attention.</span>
          </div>
        )}
        <ul className="notice-list">
          {pendingDisposals.map((d) => (
            <li key={d.disposal_id} className="notice">
              <span className="notice-icon tone-red" aria-hidden="true">
                <TrashIcon />
              </span>
              <div className="notice-content">
                <div className="notice-title">Identify disposed bottle</div>
                <div className="notice-med">{medLabel(meds, d.medication_key)}</div>
                <p className="notice-text">A bottle went into the trash. Choose its batch and enter any discarded tablets.</p>
              </div>
              <div className="notice-action">
                <button type="button" className="btn btn-primary btn-sm" onClick={() => openDisposal(d.disposal_id)}>
                  Identify
                </button>
              </div>
            </li>
          ))}
          {open.map((a) => {
            const tone = SEVERITY_TONE[a.severity] || 'amber';
            const Icon = ICONS[tone] || WarningIcon;
            return (
              <li key={a.alert_id} className="notice">
                <span className={`notice-icon tone-${tone}`} aria-hidden="true">
                  <Icon />
                </span>
                <div className="notice-content">
                  <div className="notice-title">{ALERT_TYPES[a.alert_type] || a.alert_type}</div>
                  <div className="notice-med">{medLabel(meds, a.medication_key)}</div>
                  <p className="notice-text">{describe(a, state.layout, state.receipts)}</p>
                </div>
                <div className="notice-action">
                  <AlertAction
                    alert={a}
                    onResolve={() => resolveAlert(a.alert_id)}
                    onConfirm={() => openConfirm(a.alert_id)}
                    onReceive={() => openReceive(a.medication_key)}
                  />
                </div>
              </li>
            );
          })}
        </ul>
      </div>
    </Card>
  );
}
