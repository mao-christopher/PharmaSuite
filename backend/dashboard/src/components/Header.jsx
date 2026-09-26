import React from 'react';
import { Activity, ShieldCheck, Play, Pause, RotateCcw } from 'lucide-react';

export default function Header({ scenarioName, isPlaying, onTogglePlay, onRestart, isConnected }) {
  return (
    <header className="app-header glass-panel">
      <div className="brand-title">
        <Activity className="text-cyan" style={{ color: '#00f0ff' }} size={24} />
        <span>PHARMA AI <span style={{ fontWeight: 300, opacity: 0.7 }}>INVENTORY & REPLAY</span></span>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '1.5rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <button className="btn btn-secondary" onClick={onTogglePlay}>
            {isPlaying ? <Pause size={16} /> : <Play size={16} />}
            {isPlaying ? 'Pause Replay' : 'Play Replay'}
          </button>
          <button className="btn btn-secondary" onClick={onRestart}>
            <RotateCcw size={16} />
            Restart
          </button>
        </div>

        <div className="status-badge">
          <div className="status-dot" style={{ backgroundColor: isConnected ? '#10b981' : '#f43f5e' }} />
          <span>{isConnected ? 'LIVE WS CONNECTED' : 'OFFLINE'}</span>
        </div>
      </div>
    </header>
  );
}
