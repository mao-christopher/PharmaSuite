import React from 'react';
import { Link } from 'react-router-dom';
import { Plus } from 'lucide-react';
import { useLive } from '../lib/live';
import { useDialogs } from '../lib/dialogs';
import { bottleCounts, stockStatus } from '../lib/format';
import { Badge, Card, Empty } from './ui';

export default function StockSummary() {
  const { state } = useLive();
  const { openReceive } = useDialogs();
  const meds = state.layout?.medications || [];

  return (
    <Card
      title="Stock"
      actions={
        <>
          <button className="btn btn-sm" onClick={() => openReceive()}>
            <Plus size={14} /> Receive stock
          </button>
          <Link to="/inventory" className="link">
            Full inventory →
          </Link>
        </>
      }
      flush
    >
      {meds.length === 0 ? (
        <Empty>No medications configured. Add them on the Setup page.</Empty>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Medication</th>
              <th className="num">On shelf</th>
              <th className="num">Off shelf</th>
              <th className="num">Bottles</th>
              <th className="num">Tablets</th>
              <th>Status</th>
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
                  <td className="num">{inv.pooled_tablets.toLocaleString()}</td>
                  <td>
                    <Badge tone={status.tone}>{status.label}</Badge>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </Card>
  );
}
