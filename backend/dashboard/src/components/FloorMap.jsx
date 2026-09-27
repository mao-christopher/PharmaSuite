import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowClockwiseIcon } from '@phosphor-icons/react';
import { useLive } from '../lib/live';
import { request } from '../lib/api';
import { REGION_TYPES, medLabel } from '../lib/format';
import { Badge, Card } from './ui';

const TRAIL_S = 2;
const PAD_M = 0.4;
const WEDGE_M = 1.4;
// The server sends the clock every 0.25 s; never run further ahead of it than this.
const MAX_LEAD_MS = 400;

/** Media time that advances smoothly between the server's clock messages. */
function useSmoothTime(mediaMs, playing) {
  const [t, setT] = useState(mediaMs);
  const anchor = useRef({ ms: mediaMs, at: performance.now() });

  useEffect(() => {
    anchor.current = { ms: mediaMs, at: performance.now() };
    setT(mediaMs);
  }, [mediaMs]);

  useEffect(() => {
    if (!playing) return undefined;
    let raf;
    const tick = () => {
      const { ms, at } = anchor.current;
      setT(ms + Math.min(performance.now() - at, MAX_LEAD_MS));
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing]);

  return t;
}

function wedge([x, , z], headingDeg, hfovDeg) {
  const a = ((headingDeg - hfovDeg / 2) * Math.PI) / 180;
  const b = ((headingDeg + hfovDeg / 2) * Math.PI) / 180;
  return `M${x},${z} L${x + WEDGE_M * Math.sin(a)},${z + WEDGE_M * Math.cos(a)} L${x + WEDGE_M * Math.sin(b)},${z + WEDGE_M * Math.cos(b)} Z`;
}

const SOURCE_TEXT = {
  ankles: 'Feet on the floor',
  hips: 'Estimated from hips (feet hidden)',
  bridged: 'Filled in across a short gap',
};

export default function FloorMap() {
  const { state } = useLive();
  const scenario = state.scenario;
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(() => {
    if (!scenario) {
      setData(null);
      return;
    }
    setLoading(true);
    request(`/api/recordings/${encodeURIComponent(scenario)}/floor-track`)
      .then(setData)
      .catch((e) => setData({ available: false, reason: e.message, rooms: 1 }))
      .finally(() => setLoading(false));
  }, [scenario]);
  useEffect(load, [load]);

  const t = useSmoothTime(state.media_time_ms || 0, state.is_playing);
  const track = data?.available ? data.track : null;

  // For each frame, the latest frame at or before it where the technician was placed.
  const lastSeen = useMemo(() => {
    if (!track) return null;
    const out = new Int32Array(track.frames.length);
    let last = -1;
    track.frames.forEach((f, i) => {
      if (f) last = i;
      out[i] = last;
    });
    return out;
  }, [track]);

  if (!scenario || !data) return null;
  if (!data.available && !data.rooms) return null; // nobody has scanned a room yet

  const title = 'Floor map';
  const subtitle = 'Where the technician stands and faces, from the camera’s skeleton. Presentation only: inventory never reads it.';

  if (!data.available) {
    return (
      <Card title={title} subtitle={subtitle}>
        <div className="floor-map-empty">
          <p className="muted">{data.reason}</p>
          <div className="toolbar-right">
            <Link className="btn btn-sm" to="/room?tab=cameras">
              Room setup
            </Link>
            <button type="button" className="btn btn-ghost btn-sm" onClick={load} disabled={loading}>
              <ArrowClockwiseIcon size={13} aria-hidden="true" /> {loading ? 'Checking…' : 'Check again'}
            </button>
          </div>
        </div>
      </Card>
    );
  }

  const { room, camera } = data;
  const { plan } = room;
  const frameIdx = Math.min(track.frames.length - 1, Math.max(0, Math.round((t / 1000) * track.fps)));
  const seenIdx = lastSeen[frameIdx];
  const frame = track.frames[frameIdx];
  const shown = seenIdx >= 0 ? track.frames[seenIdx] : null;
  const inView = Boolean(frame);
  const trailStart = Math.max(0, frameIdx - Math.round(TRAIL_S * track.fps));
  const trail = [];
  for (let i = trailStart; i <= frameIdx; i++) if (track.frames[i]) trail.push(track.frames[i]);

  const xs = [room.bounds_min[0], room.bounds_max[0], camera.position[0]];
  const zs = [room.bounds_min[2], room.bounds_max[2], camera.position[2]];
  const minX = Math.min(...xs) - PAD_M;
  const minZ = Math.min(...zs) - PAD_M;
  const w = Math.max(...xs) + PAD_M - minX;
  const h = Math.max(...zs) + PAD_M - minZ;
  const medications = state.layout?.medications || [];
  const stats = track.stats;

  return (
    <Card
      title={title}
      subtitle={subtitle}
      actions={
        inView ? (
          <Badge tone={frame[3] === 'ankles' ? 'green' : 'amber'} title={SOURCE_TEXT[frame[3]]}>
            {frame[3] === 'ankles' ? 'In view' : frame[3] === 'hips' ? 'Feet hidden' : 'Gap filled'}
          </Badge>
        ) : (
          <Badge tone="gray">{shown ? 'Not in view, holding' : 'Not seen yet'}</Badge>
        )
      }
    >
      <div className="floor-map">
        <svg viewBox={`${minX} ${minZ} ${w} ${h}`} role="img" aria-label={`Top-down map of ${room.name} with the technician's position`}>
          <image href={plan.url} x={plan.origin[0]} y={plan.origin[1]} width={plan.width * plan.resolution_m} height={plan.height * plan.resolution_m} preserveAspectRatio="none" />
          {room.regions.map((r) => (
            <g key={r.region_id} transform={`translate(${r.box.center[0]} ${r.box.center[2]}) rotate(${-r.box.yaw_deg})`}>
              <rect
                x={-r.box.size[0] / 2}
                y={-r.box.size[2] / 2}
                width={r.box.size[0]}
                height={r.box.size[2]}
                className="map-region"
                style={{ '--region-color': REGION_TYPES[r.region_type]?.color }}
              >
                <title>{r.region_type === 'designated_shelf' ? `${medLabel(medications, r.medication_key)} shelf` : REGION_TYPES[r.region_type]?.label}</title>
              </rect>
            </g>
          ))}
          <path d={wedge(camera.position, camera.heading_deg, camera.hfov_deg)} className="map-fov" />
          <circle cx={camera.position[0]} cy={camera.position[2]} r={0.09} className="map-camera">
            <title>Camera, {camera.position[1].toFixed(2)} m up</title>
          </circle>
          {trail.length > 1 && <polyline points={trail.map((f) => `${f[0]},${f[1]}`).join(' ')} className="map-trail" />}
          {shown && (
            <g transform={`translate(${shown[0]} ${shown[1]}) rotate(${-shown[2]})`} className={`map-tech ${inView ? '' : 'held'} ${frame?.[5] ? 'bridged' : ''}`}>
              <path d="M0,0.42 L-0.11,0.2 L0.11,0.2 Z" className="map-facing" />
              <circle r={0.17} className="map-body" />
            </g>
          )}
        </svg>
      </div>
      <dl className="floor-map-stats">
        <div>
          <dt>Placed</dt>
          <dd className="num">
            {Math.round((100 * stats.observed) / Math.max(1, stats.frames))}% of frames
          </dd>
        </div>
        <div>
          <dt>Feet hidden</dt>
          <dd className="num">{Math.round((100 * stats.from_hips) / Math.max(1, stats.observed))}%</dd>
        </div>
        <div>
          <dt>Registration</dt>
          <dd className="num">{camera.rms_px.toFixed(1)} px</dd>
        </div>
        {shown && (
          <div>
            <dt>Position</dt>
            <dd className="num">
              {shown[0].toFixed(2)}, {shown[1].toFixed(2)} m · {Math.round(shown[2])}°
            </dd>
          </div>
        )}
      </dl>
      {camera.stale_photo && (
        <p className="hint text-amber floor-map-note">The camera view’s photo changed after registration. If the camera moved, register it again.</p>
      )}
      {!track.hip_height_measured && stats.from_hips > 0 && (
        <p className="hint floor-map-note">Hip height is assumed ({track.hip_height_m} m) because the feet were rarely visible.</p>
      )}
    </Card>
  );
}
