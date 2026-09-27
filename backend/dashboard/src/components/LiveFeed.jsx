import React, { useEffect, useRef, useState } from 'react';
import { GearSixIcon, ShieldCheckIcon, VideoCameraIcon } from '@phosphor-icons/react';
import { useLive } from '../lib/live';
import { POST_ROLL_MS, PRE_ROLL_MS, useLiveCapture } from '../lib/liveCapture';
import { REGION_TYPES, plural, regionLabel } from '../lib/format';
import { Card, EmptyState } from './ui';

/** Live camera / player switch shown in the main tile's header while live capture runs. */
export function PanelSwitch() {
  const capture = useLiveCapture();
  if (capture.camera === 'off') return null;
  return (
    <div className="segmented" role="group" aria-label="What the main tile shows">
      {[
        ['live', 'Live camera'],
        ['player', 'Player'],
      ].map(([id, label]) => (
        <button key={id} type="button" className={capture.panel === id ? 'active' : ''} aria-pressed={capture.panel === id} onClick={() => capture.setPanel(id)}>
          {id === 'live' && <span className="live-dot" aria-hidden="true" />}
          {label}
        </button>
      ))}
    </div>
  );
}

/** The view's regions over the live picture, so a moved camera is obvious. Drawing only; no pose runs here. */
function RegionOverlay({ view, size, medications }) {
  const [w, h] = size;
  const layout = { ...view, medications };
  return (
    <svg className="live-regions" viewBox={`0 0 ${w} ${h}`} aria-hidden="true">
      {view.regions.map((r) => {
        const points = r.polygon.map(([x, y]) => `${x * w},${y * h}`).join(' ');
        const color = REGION_TYPES[r.region_type].color;
        const [lx, ly] = r.polygon.reduce(([mx, my], [x, y]) => [Math.min(mx, x), Math.min(my, y)], [1, 1]);
        return (
          <g key={r.region_id}>
            <polygon points={points} fill={color} fillOpacity="0.1" stroke={color} strokeWidth={Math.max(2, w / 500)} />
            <text x={lx * w + 6} y={ly * h + Math.max(16, w / 60)} fill="#fff" stroke="rgba(0,0,0,0.55)" strokeWidth={3} paintOrder="stroke" fontSize={Math.max(12, w / 70)}>
              {regionLabel(layout, r.region_id)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

export default function LiveFeed() {
  const { state } = useLive();
  const capture = useLiveCapture();
  const video = useRef(null);
  const [flash, setFlash] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const { view, videoSize, stream } = capture;

  useEffect(() => {
    if (video.current && video.current.srcObject !== stream) video.current.srcObject = stream;
  }, [stream]);

  // A short pulse around the picture when the band reports an action.
  useEffect(() => {
    if (!capture.lastEventAt) return undefined;
    setFlash(true);
    const t = setTimeout(() => setFlash(false), 900);
    return () => clearTimeout(t);
  }, [capture.lastEventAt]);

  // Buffer countdown after the camera starts.
  useEffect(() => {
    if (capture.bufferReady || capture.camera !== 'on') return undefined;
    const started = Date.now();
    setElapsed(0);
    const t = setInterval(() => setElapsed(Date.now() - started), 250);
    return () => clearInterval(t);
  }, [capture.bufferReady, capture.camera]);

  const size = videoSize?.[0] ? videoSize : [view?.frame_width || 16, view?.frame_height || 9];
  const aspectMismatch = view && videoSize?.[0] && Math.abs(videoSize[0] / videoSize[1] - view.frame_width / view.frame_height) > 0.02;
  const bandOn = capture.band.status === 'connected';
  const bufferLeft = Math.max(0, Math.ceil((PRE_ROLL_MS - elapsed) / 1000));
  const status = capture.status;

  return (
    <Card
      title={
        <>
          Live camera
          {capture.camera === 'on' && <span className="badge badge-red live-badge">Live</span>}
        </>
      }
      subtitle={`${view?.name || 'No view'}, ${capture.wrist} wrist${capture.devices.find((d) => d.deviceId === capture.deviceId) ? `, ${capture.devices.find((d) => d.deviceId === capture.deviceId).label}` : ''}`}
      className="area-player"
      flush
      actions={
        <>
          <PanelSwitch />
          <button type="button" className="icon-btn bordered" onClick={capture.openSetup} aria-label="Live capture settings" title="Live capture settings">
            <GearSixIcon aria-hidden="true" />
          </button>
        </>
      }
    >
      {capture.camera !== 'on' ? (
        <EmptyState icon={VideoCameraIcon} title="Starting the camera…">
          Allow camera access if Chrome asks.
        </EmptyState>
      ) : (
        <div className={`live-stage ${flash ? 'is-flash' : ''}`}>
          <div className="live-frame" style={{ aspectRatio: `${size[0]} / ${size[1]}`, width: `min(100%, calc((100dvh - 330px) * ${size[0] / size[1]}))` }}>
            <video ref={video} autoPlay muted playsInline aria-label="Live camera picture" />
            {view && !aspectMismatch && <RegionOverlay view={view} size={size} medications={state.layout?.medications} />}
            <div className="live-chips">
              <span className={`live-chip ${capture.bufferReady ? 'ok' : ''}`}>
                {capture.bufferReady ? 'Ready' : `Buffering ${bufferLeft} s`}
              </span>
              <span className={`live-chip ${bandOn ? 'ok' : capture.devMode ? '' : 'warn'}`}>
                {bandOn ? capture.band.name.replace('Wristband-', 'Band ') : capture.devMode ? 'Dev keys' : 'No band'}
              </span>
              {capture.analyzing > 0 && <span className="live-chip busy">Analyzing {plural(capture.analyzing, 'clip')}</span>}
            </div>
            {aspectMismatch && (
              <div className="live-warning">
                This camera's frame shape doesn't match the view {view.name}. Choose the view registered for this camera.
              </div>
            )}
          </div>
        </div>
      )}
      <div className="player-note" aria-live="polite">
        {status ? (
          <span className={`live-status tone-text-${status.tone}`}>{status.text}</span>
        ) : (
          <span className="live-status">
            <ShieldCheckIcon size={14} aria-hidden="true" /> Only the {(PRE_ROLL_MS + POST_ROLL_MS) / 1000} s around each band event is saved.
          </span>
        )}
        {capture.pendingCount > capture.analyzing && (
          <button type="button" className="link-btn push" onClick={() => capture.retryPending()}>
            Retry {plural(capture.pendingCount - capture.analyzing, 'queued event')}
          </button>
        )}
        {capture.devMode && <span className={`hint ${capture.pendingCount > capture.analyzing ? '' : 'push'}`}><kbd>Space</kbd> pickup / put-down</span>}
      </div>
    </Card>
  );
}
