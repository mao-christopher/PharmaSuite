import React from 'react';
import { Link } from 'react-router-dom';
import { ArrowRightIcon, PlusIcon } from '@phosphor-icons/react';
import { useLive } from '../lib/live';
import { useDialogs } from '../lib/dialogs';
import { bottleCounts, formatNumber, stockStatus } from '../lib/format';
import { Badge, Card, Empty } from './ui';

export default function StockSummary() {
  const { state } = useLive();
  const { openReceive } = useDialogs();
  const meds = state.layout?.medications || [];

  return (
    <Card
      title="Stock"
      className="area-stock"
      actions={
        <>
          <Link to="/inventory" className="btn btn-ghost btn-sm">
            Full inventory <ArrowRightIcon size={13} aria-hidden="true" />
          </Link>
          <button type="button" className="btn btn-sm" onClick={() => openReceive()}>
            <PlusIcon size={13} aria-hidden="true" /> Receive stock
          </button>
        </>
      }
      flush
    >
      {meds.length === 0 ? (
        <Empty>No medications configured. Add them on the Setup page.</Empty>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Medication</th>
                <th scope="col" className="num">On shelf</th>
                <th scope="col" className="num">Off shelf</th>
                <th scope="col" className="num">Bottles</th>
                <th scope="col" className="num">Tablets</th>
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {meds.map((m) => {
                const inv = state.inventory[m.medication_key];
                if (!inv) return null;
                const c = bottleCounts(state.layout, inv);
                const status = stockStatus(state.layout, inv);
                return (
                  <tr key={m.medication_key}>
                    <td>
                      <span className="strong">{m.name}</span> <span className="muted">{m.strength}</span>
                    </td>
                    <td className="num">{c.onShelf}</td>
                    <td className="num">{c.offShelf}</td>
                    <td className="num">{c.total}</td>
                    <td className="num">{formatNumber(inv.pooled_tablets)}</td>
                    <td>
                      <Badge tone={status.tone}>{status.label}</Badge>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
