import React from 'react';
import { Pill, Box, Layers, AlertCircle } from 'lucide-react';

export default function InventoryBoard({ inventory }) {
  const items = Object.values(inventory || {});

  return (
    <div className="metrics-grid">
      {items.map((item) => {
        const onShelf = Object.values(item.shelf_counts || {}).reduce((a, b) => a + b, 0);

        return (
          <div key={item.medication_key} className="glass-panel metric-card">
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span className="metric-label">{item.medication_key}</span>
              <Pill size={18} style={{ color: '#00f0ff' }} />
            </div>

            <div className="metric-value" style={{ color: item.pooled_tablets === 0 ? '#f43f5e' : '#f3f4f6' }}>
              {item.pooled_tablets} <span style={{ fontSize: '0.9rem', fontWeight: 400, color: '#9ca3af' }}>tablets</span>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem', marginTop: '0.5rem', paddingTop: '0.5rem', borderTop: '1px solid rgba(255,255,255,0.06)' }}>
              <div>
                <span className="metric-sub">On Shelf</span>
                <div style={{ fontWeight: 600, color: '#10b981' }}>{onShelf} bottles</div>
              </div>
              <div>
                <span className="metric-sub">Counter / Held</span>
                <div style={{ fontWeight: 600, color: '#f59e0b' }}>{item.counter_bottles + item.held_bottles} bottles</div>
              </div>
              <div>
                <span className="metric-sub">Total Undisposed</span>
                <div style={{ fontWeight: 600, color: '#00f0ff' }}>{item.total_bottles} bottles</div>
              </div>
              <div>
                <span className="metric-sub">Disposed Total</span>
                <div style={{ fontWeight: 600, color: '#9ca3af' }}>{item.disposed_bottles} bottles</div>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
