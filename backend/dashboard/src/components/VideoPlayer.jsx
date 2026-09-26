import React from 'react';
import { Camera, Eye } from 'lucide-react';

export default function VideoPlayer({ mediaTimeMs, scenarioName }) {
  const streamUrl = `/api/video/feed?t=${Date.now()}`;

  return (
    <div className="glass-panel" style={{ padding: '1rem', display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.95rem', fontWeight: 600 }}>
          <Camera size={18} style={{ color: '#00f0ff' }} />
          <span>ROOM CAMERA — YOLO11 POSE & REGION OVERLAY</span>
        </div>
        <div style={{ fontSize: '0.8rem', color: '#9ca3af', fontFamily: 'monospace' }}>
          TIME: {mediaTimeMs} ms
        </div>
      </div>

      <div className="video-player-box">
        <img
          src="/api/video/feed"
          alt="Server-driven YOLO Pose stream"
          className="video-stream-img"
          onError={(e) => {
            e.target.style.display = 'none';
          }}
        />
        <div style={{
          position: 'absolute',
          bottom: '12px',
          left: '12px',
          background: 'rgba(0,0,0,0.7)',
          padding: '4px 10px',
          borderRadius: '6px',
          fontSize: '0.75rem',
          color: '#10b981',
          display: 'flex',
          alignItems: 'center',
          gap: '6px'
        }}>
          <Eye size={14} />
          <span>SCENARIO: {scenarioName || 'demo_scenario_01'}</span>
        </div>
      </div>
    </div>
  );
}
