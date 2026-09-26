import React from 'react';
import { AlertTriangle, Bell, CheckCircle2, ShieldAlert } from 'lucide-react';

export default function AlertCenter({ alerts, onResolveAlert }) {
  const alertList = Object.values(alerts || {}).filter(a => a.status === 'open');

  return (
    <div className="glass-panel" style={{ padding: '1.25rem', display: 'flex', flexDirection: 'column', gap: '1rem', flex: 1 }}>
      <div className="panel-header">
        <div className="panel-title">
          <ShieldAlert size={20} style={{ color: alertList.length > 0 ? '#f43f5e' : '#10b981' }} />
          <span>ACTIVE ALERTS & CONFIRMATIONS</span>
        </div>
        <span style={{ fontSize: '0.8rem', background: alertList.length > 0 ? 'rgba(244,63,94,0.15)' : 'rgba(16,185,129,0.15)', color: alertList.length > 0 ? '#f43f5e' : '#10b981', padding: '2px 8px', borderRadius: '12px', fontWeight: 600 }}>
          {alertList.length} OPEN
        </span>
      </div>

      {alertList.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '2rem 1rem', color: '#9ca3af', fontSize: '0.875rem' }}>
          <CheckCircle2 size={32} style={{ color: '#10b981', marginBottom: '0.5rem' }} />
          <div>No active inventory or misplacement alerts.</div>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', overflowY: 'auto', maxHeight: '280px' }}>
          {alertList.map((alert) => (
            <div key={alert.alert_id} className={`alert-card ${alert.severity === 'error' ? 'error' : ''}`}>
              <div className="alert-title">
                <span style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                  <AlertTriangle size={16} style={{ color: alert.severity === 'error' ? '#f43f5e' : '#f59e0b' }} />
                  {alert.alert_type.toUpperCase()}: {alert.medication_key}
                </span>
                <button
                  className="btn btn-secondary"
                  style={{ padding: '2px 8px', fontSize: '0.75rem' }}
                  onClick={() => onResolveAlert(alert.alert_id)}
                >
                  Resolve
                </button>
              </div>
              <div className="alert-desc">{alert.description}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
