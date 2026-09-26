import React, { Fragment, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { ArrowCounterClockwiseIcon, CaretDownIcon, CaretRightIcon, MagnifyingGlassIcon, PlusIcon } from '@phosphor-icons/react';
import { useLive } from '../lib/live';
import { useDialogs } from '../lib/dialogs';
import { request } from '../lib/api';
import {
  NONE,
  TX_STATUS,
  bottleCounts,
  formatDate,
  formatDateTime,
  formatNumber,
  inventoryTotals,
  isExpired,
  medLabel,
  regionLabel,
  stockStatus,
} from '../lib/format';
import { Badge, Card, ConfirmDialog, Empty, Metric, Metrics, PageHeader } from '../components/ui';

const HISTORY_KINDS = {
  signal: 'Signal',
  receive_stock: 'Received',
  disposal: 'Disposal',
  correction: 'Correction',
  prescription: 'Prescription',
  reset: 'Reset',
  layout: 'Layout',
  recording_uploaded: 'Upload',
  recording_deleted: 'Deleted',
};

function ReceiptTable({ receipts, unit, highlight, alertsByReceipt, onDispose }) {
  if (receipts.length === 0) return <Empty>No received batches recorded.</Empty>;
  return (
    <table className="table table-inner">
      <thead>
        <tr>
          <th scope="col">Batch</th>
          <th scope="col">Lot</th>
          <th scope="col">Received</th>
          <th scope="col">Expires</th>
          <th scope="col" className="num">Bottles left</th>
          <th scope="col" className="num">Received {unit}</th>
          <th scope="col" className="actions">
            <span className="sr-only">Actions</span>
          </th>
        </tr>
      </thead>
      <tbody>
        {receipts.map((r) => (
          <tr key={r.receipt_id} id={`receipt-${r.receipt_id}`} className={r.receipt_id === highlight ? 'highlight' : ''}>
            <td>
              <span className="mono" translate="no">{r.receipt_id}</span>
              {alertsByReceipt.has(r.receipt_id) && <div className="row-sub text-red">Expiry alert open: find and dispose</div>}
            </td>
            <td>{r.lot_number || NONE}</td>
            <td className="nowrap">{formatDate(r.received_at)}</td>
            <td className="nowrap">
              {formatDate(r.expiry_date)} {isExpired(r.expiry_date) && r.remaining_bottles > 0 && <Badge tone="red">Expired</Badge>}
            </td>
            <td className="num">
              {r.remaining_bottles} / {r.bottle_count}
            </td>
            <td className="num">{formatNumber(r.total_tablets)}</td>
            <td className="actions">
              {r.remaining_bottles > 0 && (
                <button
                  type="button"
                  className={`btn btn-sm ${alertsByReceipt.has(r.receipt_id) ? 'btn-danger' : 'btn-ghost'}`}
                  onClick={() => onDispose(r.receipt_id)}
                >
                  Dispose…
                </button>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const SIGNAL_TEXT = {
  HELD: (med, where) => `${med} picked up from the ${where}`,
  AT_COUNTER: (med, where) => `${med} put down at the ${where}`,
  ON_DESIGNATED_SHELF: (med, where) => `${med} returned to the ${where}`,
  MISPLACED: (med, where) => `${med} put on the ${where}, which is the wrong shelf`,
  DISPOSED: (med) => `${med} put in the trash`,
  NEEDS_CONFIRMATION: (med, where) => `${med} moved, location unclear (nearest ${where})`,
};

function describeHistory(h, layout) {
  if (h.kind !== 'signal') return { text: h.summary };
  const med = h.medication_key && h.medication_key !== 'UNKNOWN' ? medLabel(layout?.medications, h.medication_key) : 'A bottle';
  const where = h.region_id ? regionLabel(layout, h.region_id) : 'unknown spot';
  const text = (SIGNAL_TEXT[h.result] || ((m) => `${m} moved`))(med, where);
  return { text: `${text}.`, sub: h.summary.replace(/\.$/, '') };
}

function HistoryText({ item, layout }) {
  const { text, sub } = describeHistory(item, layout);
  return (
    <span className="choice-main">
      {text}
      {sub && <span className="row-sub"> {sub}</span>}
    </span>
  );
}

function History() {
  const { state } = useLive();
  const [items, setItems] = useState(null);
  const [total, setTotal] = useState(0);
  const count = state.store?.history_count;

  useEffect(() => {
    let alive = true;
    request('/api/history?limit=40')
      .then((r) => {
        if (!alive) return;
        setItems(r.history);
        setTotal(r.total);
      })
      .catch(() => alive && setItems([]));
    return () => {
      alive = false;
    };
  }, [count]);

  return (
    <Card
      title="Stock history"
      subtitle="Every signal, shipment, disposal and correction, newest first."
      actions={total > 40 && <span className="hint">Latest 40 of {formatNumber(total)}</span>}
      flush
      className="section-gap"
    >
      {!items ? (
        <Empty>Loading history…</Empty>
      ) : items.length === 0 ? (
        <Empty>Nothing has happened yet.</Empty>
      ) : (
        <ul className="history">
          {items.map((h, i) => (
            <li key={`${h.at}-${i}`}>
              <span className="history-time">{formatDateTime(h.at)}</span>
              <HistoryText item={h} layout={state.layout} />
              <Badge tone={h.kind === 'correction' ? 'blue' : h.kind === 'reset' ? 'amber' : 'gray'}>
                {HISTORY_KINDS[h.kind] || h.kind}
              </Badge>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export default function Inventory() {
  const { state, resetInventory } = useLive();
  const { openReceive, openDisposeBatch, openAddPrescription } = useDialogs();
  const [params, setParams] = useSearchParams();
  const highlight = params.get('receipt');
  const query = params.get('q') || '';
  const [expanded, setExpanded] = useState(() => new Set());
  const [resetting, setResetting] = useState(false);
  const [resetBusy, setResetBusy] = useState(false);
  const [resetError, setResetError] = useState(null);
  const alertsByReceipt = new Set(
    Object.values(state.alerts)
      .filter((a) => a.alert_type === 'expiry' && a.status === 'open')
      .map((a) => a.metadata.receipt_id),
  );

  useEffect(() => {
    const receipt = state.receipts.find((r) => r.receipt_id === highlight);
    if (!receipt) return;
    setExpanded((prev) => new Set(prev).add(receipt.medication_key));
    requestAnimationFrame(() =>
      document.getElementById(`receipt-${highlight}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }),
    );
  }, [highlight]);

  const layout = state.layout;
  const meds = layout?.medications || [];
  const rows = meds
    .filter((m) => `${m.name} ${m.strength}`.toLowerCase().includes(query.trim().toLowerCase()))
    .map((m) => ({ med: m, inv: state.inventory[m.medication_key] }))
    .filter((r) => r.inv);
  const totals = inventoryTotals(state);

  const setQuery = (value) => {
    const next = new URLSearchParams(params);
    value ? next.set('q', value) : next.delete('q');
    setParams(next, { replace: true });
  };

  const toggle = (key) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });

  const doReset = async () => {
    setResetBusy(true);
    setResetError(null);
    try {
      await resetInventory();
      setResetting(false);
    } catch (e) {
      setResetError(e.message);
    } finally {
      setResetBusy(false);
    }
  };

  const disposals = Object.values(state.disposals);
  const txs = Object.values(state.transactions);

  return (
    <>
      <PageHeader title="Inventory" subtitle="Tablet balances are pooled per medication and strength across all bottles. Counts carry over from one recording to the next.">
        <label className="search">
          <MagnifyingGlassIcon size={15} aria-hidden="true" />
          <input
            type="search"
            name="medication-search"
            autoComplete="off"
            aria-label="Search medications"
            placeholder="Search medications…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <button type="button" className="btn btn-ghost" onClick={() => setResetting(true)}>
          <ArrowCounterClockwiseIcon size={14} aria-hidden="true" /> Reset to opening stock
        </button>
        <button type="button" className="btn btn-primary" onClick={() => openReceive()}>
          <PlusIcon size={14} aria-hidden="true" /> Receive stock
        </button>
      </PageHeader>

      <Metrics label="Inventory totals">
        <Metric label="Medications" value={meds.length} />
        <Metric label="Bottles in pharmacy" value={formatNumber(totals.bottles)} hint="Undisposed, any location" />
        <Metric label="On shelves" value={formatNumber(totals.onShelf)} hint={totals.misplaced ? `${totals.misplaced} on the wrong shelf` : undefined} />
        <Metric label="Off shelf" value={totals.offShelf} hint="In hand or at the counter" />
        <Metric label="Out of stock" value={totals.out} tone={totals.out ? 'red' : undefined} />
        <Metric label="Expired batches" value={totals.expired} tone={totals.expired ? 'red' : undefined} hint="Still on shelves" />
      </Metrics>

      <Card title="Medications" flush>
        {rows.length === 0 ? (
          <Empty>{meds.length ? 'No medications match your search.' : 'No medications configured yet.'}</Empty>
        ) : (
          <div className="table-wrap">
            <table className="table table-expandable">
              <thead>
                <tr>
                  <th scope="col" className="chevron-cell">
                    <span className="sr-only">Batches</span>
                  </th>
                  <th scope="col">Medication</th>
                  <th scope="col" className="num">On shelf</th>
                  <th scope="col" className="num">In hand</th>
                  <th scope="col" className="num">At counter</th>
                  <th scope="col" className="num">Total bottles</th>
                  <th scope="col" className="num">Tablets</th>
                  <th scope="col" className="num">Disposed</th>
                  <th scope="col">Status</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(({ med, inv }) => {
                  const c = bottleCounts(layout, inv);
                  const status = stockStatus(layout, inv);
                  const isOpen = expanded.has(med.medication_key);
                  const receipts = state.receipts.filter((r) => r.medication_key === med.medication_key);
                  return (
                    <Fragment key={med.medication_key}>
                      <tr className="clickable" onClick={() => toggle(med.medication_key)}>
                        <td className="chevron-cell">
                          <button
                            type="button"
                            className="icon-btn"
                            aria-expanded={isOpen}
                            aria-label={`${isOpen ? 'Hide' : 'Show'} batches for ${med.name} ${med.strength}`}
                            onClick={(e) => {
                              e.stopPropagation();
                              toggle(med.medication_key);
                            }}
                          >
                            {isOpen ? <CaretDownIcon aria-hidden="true" /> : <CaretRightIcon aria-hidden="true" />}
                          </button>
                        </td>
                        <td>
                          <span className="strong">{med.name}</span> <span className="muted">{med.strength}</span>
                          <div className="row-sub mono" translate="no">{med.medication_key}</div>
                        </td>
                        <td className="num">
                          {c.onShelf}
                          {c.misplaced > 0 && <div className="row-sub text-red">+{c.misplaced} misplaced</div>}
                        </td>
                        <td className="num">{c.held}</td>
                        <td className="num">{c.atCounter}</td>
                        <td className="num strong">{c.total}</td>
                        <td className="num nowrap">
                          {formatNumber(inv.pooled_tablets)} <span className="muted">{med.unit}</span>
                        </td>
                        <td className="num">{inv.disposed_bottles}</td>
                        <td>
                          <div className="badges">
                            <Badge tone={status.tone}>{status.label}</Badge>
                            {receipts.some((r) => alertsByReceipt.has(r.receipt_id)) && <Badge tone="red">Expired batch</Badge>}
                          </div>
                        </td>
                      </tr>
                      {isOpen && (
                        <tr className="expanded-row">
                          <td />
                          <td colSpan={8}>
                            <ReceiptTable receipts={receipts} unit={med.unit} highlight={highlight} alertsByReceipt={alertsByReceipt} onDispose={openDisposeBatch} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <div className="two-col section-gap">
        <Card title="Disposals" flush>
          {disposals.length === 0 ? (
            <Empty>No disposals recorded.</Empty>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Medication</th>
                  <th scope="col">Batch</th>
                  <th scope="col" className="num">Discarded</th>
                  <th scope="col">Status</th>
                </tr>
              </thead>
              <tbody>
                {disposals.map((d) => (
                  <tr key={d.disposal_id}>
                    <td>{medLabel(meds, d.medication_key)}</td>
                    <td className="mono">{d.selected_receipt_id || NONE}</td>
                    <td className="num">
                      {d.quantity_deducted}
                      {d.is_default_quantity && <div className="row-sub">Default, no entry</div>}
                    </td>
                    <td>
                      {d.status === 'resolved' ? <Badge tone="green">Identified</Badge> : <Badge tone="amber">Needs identification</Badge>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>

        <Card
          title="Prescriptions"
          actions={
            <button type="button" className="btn btn-sm" onClick={openAddPrescription}>
              <PlusIcon size={13} aria-hidden="true" /> Add prescriptions
            </button>
          }
          flush
        >
          {txs.length === 0 ? (
            <Empty>No prescriptions.</Empty>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Rx</th>
                  <th scope="col">Medication</th>
                  <th scope="col" className="num">Qty</th>
                  <th scope="col">Status</th>
                </tr>
              </thead>
              <tbody>
                {txs.map((tx) => {
                  const s = TX_STATUS[tx.status] || { label: tx.status, tone: 'gray' };
                  return (
                    <tr key={tx.transaction_id}>
                      <td className="mono" translate="no">{tx.transaction_id}</td>
                      <td>{medLabel(meds, tx.medication_key)}</td>
                      <td className="num">{tx.quantity}</td>
                      <td>
                        <Badge tone={s.tone}>{s.label}</Badge> {tx.deducted && <span className="muted">Deducted</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </Card>
      </div>

      <History />

      {resetting && (
        <ConfirmDialog
          title="Reset inventory to opening stock?"
          confirmLabel="Reset inventory"
          busyLabel="Resetting…"
          busy={resetBusy}
          error={resetError}
          destructive
          onConfirm={doReset}
          onClose={() => setResetting(false)}
        >
          Live counts, received shipments, alerts and disposals are replaced with the opening stock from Setup. Every
          recording goes back to not applied, so its signals will count again the next time it plays. The history keeps a
          record of the reset.
        </ConfirmDialog>
      )}
    </>
  );
}
