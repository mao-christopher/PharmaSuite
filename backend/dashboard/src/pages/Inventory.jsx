import React, { Fragment, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { ChevronDown, ChevronRight, Plus, Search } from 'lucide-react';
import { useLive } from '../lib/live';
import { useDialogs } from '../lib/dialogs';
import {
  TX_STATUS,
  bottleCounts,
  formatDate,
  isExpired,
  medLabel,
  stockStatus,
} from '../lib/format';
import { Badge, Card, Empty, PageHeader, Stat } from '../components/ui';

function ReceiptTable({ receipts, unit, highlight, alertsByReceipt }) {
  if (receipts.length === 0) return <Empty>No received batches recorded.</Empty>;
  return (
    <table className="table table-inner">
      <thead>
        <tr>
          <th>Batch</th>
          <th>Lot</th>
          <th>Received</th>
          <th>Expires</th>
          <th className="num">Bottles left</th>
          <th className="num">Received {unit}</th>
        </tr>
      </thead>
      <tbody>
        {receipts.map((r) => (
          <tr key={r.receipt_id} id={`receipt-${r.receipt_id}`} className={r.receipt_id === highlight ? 'highlight' : ''}>
            <td className="mono">
              {r.receipt_id}
              {alertsByReceipt.has(r.receipt_id) && <div className="row-sub text-red">Expiry alert open: find and dispose</div>}
            </td>
            <td>{r.lot_number || '—'}</td>
            <td>{formatDate(r.received_at)}</td>
            <td>
              {formatDate(r.expiry_date)} {isExpired(r.expiry_date) && r.remaining_bottles > 0 && <Badge tone="red">Expired</Badge>}
            </td>
            <td className="num">
              {r.remaining_bottles} / {r.bottle_count}
            </td>
            <td className="num">{r.total_tablets.toLocaleString()}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function Inventory() {
  const { state } = useLive();
  const { openReceive } = useDialogs();
  const [params] = useSearchParams();
  const highlight = params.get('receipt');
  const [query, setQuery] = useState('');
  const [expanded, setExpanded] = useState(() => new Set());
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

  const totals = meds.reduce(
    (acc, m) => {
      const inv = state.inventory[m.medication_key];
      if (!inv) return acc;
      const c = bottleCounts(layout, inv);
      acc.bottles += c.total;
      acc.onShelf += c.onShelf + c.misplaced;
      acc.offShelf += c.offShelf;
      if (c.total === 0) acc.out += 1;
      return acc;
    },
    { bottles: 0, onShelf: 0, offShelf: 0, out: 0 },
  );

  const toggle = (key) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });

  const disposals = Object.values(state.disposals);
  const txs = Object.values(state.transactions);

  return (
    <>
      <PageHeader title="Inventory" subtitle="Tablet balances are pooled per medication and strength across all bottles.">
        <button className="btn btn-primary" onClick={() => openReceive()}>
          <Plus size={15} /> Receive stock
        </button>
        <label className="search">
          <Search size={15} />
          <input placeholder="Search medications" value={query} onChange={(e) => setQuery(e.target.value)} />
        </label>
      </PageHeader>

      <div className="stats">
        <Stat label="Medications" value={meds.length} />
        <Stat label="Bottles in pharmacy" value={totals.bottles} hint="Undisposed, any location" />
        <Stat label="On shelves" value={totals.onShelf} />
        <Stat label="Off shelf" value={totals.offShelf} hint="In hand or at counter" />
        <Stat label="Out of stock" value={totals.out} tone={totals.out ? 'red' : undefined} />
        <Stat label="Expiry alerts" value={alertsByReceipt.size} tone={alertsByReceipt.size ? 'red' : undefined} hint="Expired batches still on shelves" />
      </div>

      <Card title="Medications" flush>
        {rows.length === 0 ? (
          <Empty>{meds.length ? 'No medications match your search.' : 'No medications configured yet.'}</Empty>
        ) : (
          <table className="table table-expandable">
            <thead>
              <tr>
                <th aria-label="Expand" />
                <th>Medication</th>
                <th className="num">On shelf</th>
                <th className="num">In hand</th>
                <th className="num">At counter</th>
                <th className="num">Total bottles</th>
                <th className="num">Tablets</th>
                <th className="num">Disposed</th>
                <th>Status</th>
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
                    <tr className="clickable" onClick={() => toggle(med.medication_key)} aria-expanded={isOpen}>
                      <td className="chevron">{isOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}</td>
                      <td>
                        <span className="strong">{med.name}</span> <span className="muted">{med.strength}</span>
                        <div className="row-sub mono">{med.medication_key}</div>
                      </td>
                      <td className="num">
                        {c.onShelf}
                        {c.misplaced > 0 && <div className="row-sub text-red">+{c.misplaced} misplaced</div>}
                      </td>
                      <td className="num">{c.held}</td>
                      <td className="num">{c.atCounter}</td>
                      <td className="num strong">{c.total}</td>
                      <td className="num">
                        {inv.pooled_tablets.toLocaleString()} <span className="muted">{med.unit}</span>
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
                          <ReceiptTable receipts={receipts} unit={med.unit} highlight={highlight} alertsByReceipt={alertsByReceipt} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        )}
      </Card>

      <div className="two-col">
        <Card title="Disposals" flush>
          {disposals.length === 0 ? (
            <Empty>No disposals recorded.</Empty>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Medication</th>
                  <th>Batch</th>
                  <th className="num">Discarded</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {disposals.map((d) => (
                  <tr key={d.disposal_id}>
                    <td>{medLabel(meds, d.medication_key)}</td>
                    <td className="mono">{d.selected_receipt_id || '—'}</td>
                    <td className="num">
                      {d.quantity_deducted}
                      {d.is_default_quantity && <div className="row-sub">default (no entry)</div>}
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

        <Card title="Prescriptions" flush>
          {txs.length === 0 ? (
            <Empty>No prescriptions.</Empty>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Rx</th>
                  <th>Medication</th>
                  <th className="num">Qty</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {txs.map((tx) => {
                  const s = TX_STATUS[tx.status] || { label: tx.status, tone: 'gray' };
                  return (
                    <tr key={tx.transaction_id}>
                      <td className="mono">{tx.transaction_id}</td>
                      <td>{medLabel(meds, tx.medication_key)}</td>
                      <td className="num">{tx.quantity}</td>
                      <td>
                        <Badge tone={s.tone}>{s.label}</Badge> {tx.deducted && <span className="muted">deducted</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </Card>
      </div>
    </>
  );
}
