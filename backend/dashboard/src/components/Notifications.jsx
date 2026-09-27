import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { BellIcon, ClockCountdownIcon, InfoIcon, PackageIcon, TrashIcon, TrendDownIcon, WarningCircleIcon, WarningIcon, XIcon } from '@phosphor-icons/react';
import { useLive } from '../lib/live';
import { request } from '../lib/api';
import { useDialogs } from '../lib/dialogs';
import { ALERT_TYPES, LIVE_REASONS, SEVERITY_RANK, SEVERITY_TONE, formatDate, medLabel, plural, regionLabel } from '../lib/format';
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
    case 'uncertainty':
      if (m.reason === 'which_bottle') {
        const kinds = [...new Set((m.bottle_options || []).map((o) => medLabel(layout?.medications, o.medication_key)))];
        return `Picked up from the ${regionLabel(layout, m.region_id)}, which holds ${kinds.join(' and ')}. Confirm which bottle was taken.`;
      }
      return m.live_reason && LIVE_REASONS[m.live_reason]
        ? `${LIVE_REASONS[m.live_reason]}. Watch the clip and confirm where it happened.`
        : alert.description;
    default:
      return alert.description;
  }
}

function AlertAction({ alert, onResolve, onConfirm, onReceive, onDispose }) {
  switch (alert.alert_type) {
    case 'uncertainty':
      return (
        <button type="button" className="btn btn-sm btn-primary" onClick={onConfirm}>
          {alert.metadata?.reason === 'which_bottle' ? 'Confirm bottle' : 'Confirm location'}
        </button>
      );
    case 'expiry':
      return (
        <>
          <button type="button" className="btn btn-sm btn-primary" onClick={onDispose}>
            Dispose
          </button>
          <Link className="btn btn-sm btn-ghost" to={`/inventory?receipt=${encodeURIComponent(alert.metadata.receipt_id)}`}>
            View batch
          </Link>
        </>
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

const SUGGESTION_ICONS = { low_stock: PackageIcon, last_bottle: PackageIcon, runout: TrendDownIcon, expiring_soon: ClockCountdownIcon };

/** Proactive, derived suggestions: nothing has gone wrong yet, but acting now avoids an alert later. */
function Suggestions({ suggestions, meds, onReceive }) {
  const { refresh } = useLive();
  const [busy, setBusy] = useState(null);
  const dismiss = async (id) => {
    setBusy(id);
    try {
      await request(`/api/suggestions/${encodeURIComponent(id)}/dismiss`, { method: 'POST' });
      await refresh();
    } finally {
      setBusy(null);
    }
  };
  if (suggestions.length === 0) return null;
  return (
    <>
      <div className="suggestion-head">
        <span>Suggested actions</span>
        <span>{plural(suggestions.length, 'suggestion')}</span>
      </div>
      <ul className="notice-list">
        {suggestions.map((sg) => {
          const Icon = SUGGESTION_ICONS[sg.kind] || InfoIcon;
          return (
            <li key={sg.id} className="notice">
              <span className={`notice-icon tone-${sg.severity === 'warning' ? 'amber' : 'blue'}`} aria-hidden="true">
                <Icon />
              </span>
              <div className="notice-content">
                <div className="notice-title">{sg.title}</div>
                <div className="notice-med">{medLabel(meds, sg.medication_key)}</div>
                <p className="notice-text">{sg.message}</p>
              </div>
              <div className="notice-action">
                {sg.action === 'view_batch' ? (
                  <Link className="btn btn-sm" to={`/inventory?receipt=${encodeURIComponent(sg.receipt_id)}`}>
                    View batch
                  </Link>
                ) : (
                  <button type="button" className="btn btn-sm" onClick={() => onReceive(sg.medication_key)}>
                    Receive stock
                  </button>
                )}
                <button
                  type="button"
                  className="icon-btn"
                  aria-label={`Dismiss: ${sg.title}, ${medLabel(meds, sg.medication_key)}`}
                  title="Dismiss until the situation changes"
                  disabled={busy === sg.id}
                  onClick={() => dismiss(sg.id)}
                >
                  <XIcon aria-hidden="true" />
                </button>
              </div>
            </li>
          );
        })}
      </ul>
    </>
  );
}

export default function Notifications() {
  const { state, resolveAlert } = useLive();
  const { openDisposal, openConfirm, openReceive, openDisposeBatch } = useDialogs();
  const [showResolved, setShowResolved] = useState(false);
  const meds = state.layout?.medications;
  const alerts = Object.values(state.alerts);
  const open = alerts
    .filter((a) => a.status === 'open')
    .sort((a, b) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity]);
  const resolved = alerts.filter((a) => a.status !== 'open');
  const pendingDisposals = Object.values(state.disposals).filter((d) => d.status === 'pending_employee_entry');
  const count = open.length + pendingDisposals.length;
  const suggestions = state.suggestions || [];

  return (
    <Card
      title="Notifications"
      className="area-alerts"
      actions={
        count > 0 ? (
          <Badge tone="red">{count} open</Badge>
        ) : suggestions.length > 0 ? (
          <Badge tone="blue">{plural(suggestions.length, 'suggestion')}</Badge>
        ) : (
          <Badge tone="green">All clear</Badge>
        )
      }
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
        {count === 0 && suggestions.length === 0 && (
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
                  <div className="notice-title">{a.metadata?.reason === 'which_bottle' ? 'Which bottle?' : ALERT_TYPES[a.alert_type] || a.alert_type}</div>
                  <div className="notice-med">
                    {a.metadata?.reason === 'which_bottle'
                      ? [...new Set(a.metadata.bottle_options.map((o) => medLabel(meds, o.medication_key)))].join(' or ')
                      : medLabel(meds, a.medication_key)}
                  </div>
                  <p className="notice-text">{describe(a, state.layout, state.receipts)}</p>
                  {a.alert_type === 'uncertainty' && a.metadata?.live_reason !== undefined && a.metadata?.recording && (
                    <button type="button" className="notice-thumb" onClick={() => openConfirm(a.alert_id)} aria-label="Open the clip and confirm the location">
                      <img
                        src={`/api/recordings/${encodeURIComponent(a.metadata.recording)}/thumbnail`}
                        alt=""
                        loading="lazy"
                        onError={(e) => { e.currentTarget.parentElement.hidden = true; }}
                      />
                    </button>
                  )}
                </div>
                <div className="notice-action">
                  <AlertAction
                    alert={a}
                    onResolve={() => resolveAlert(a.alert_id)}
                    onConfirm={() => openConfirm(a.alert_id)}
                    onReceive={() => openReceive(a.medication_key)}
                    onDispose={() => openDisposeBatch(a.metadata.receipt_id)}
                  />
                </div>
              </li>
            );
          })}
        </ul>
        <Suggestions suggestions={suggestions} meds={meds} onReceive={openReceive} />
      </div>
    </Card>
  );
}
