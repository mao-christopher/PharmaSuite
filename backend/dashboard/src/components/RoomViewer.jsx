import React, { useEffect, useRef, useState } from 'react';
import { CornersOutIcon } from '@phosphor-icons/react';
import { RoomScene } from '../lib/room3d';
import { request } from '../lib/api';

const CLICK_SLOP_PX = 5;

/** The room rebuilt from boxes (fetched only while `enabled`); rebuilt per room version. */
export function useRebuilt(room, enabled) {
  const [state, setState] = useState({ key: null, data: null, error: null });
  const key = `${room.room_id}:${room.room_version}`;
  useEffect(() => {
    if (!enabled || state.key === key) return undefined;
    let cancelled = false;
    setState({ key, data: null, error: null });
    request(`/api/rooms/${encodeURIComponent(room.room_id)}/rebuilt`)
      .then((data) => !cancelled && setState({ key, data, error: null }))
      .catch((e) => !cancelled && setState({ key, data: null, error: e.message }));
    return () => {
      cancelled = true;
    };
  }, [enabled, key]); // eslint-disable-line react-hooks/exhaustive-deps
  return state.key === key ? state : { key, data: null, error: null };
}

function rebuiltSummary(rebuilt) {
  const { report } = rebuilt;
  const doors = report.walls.reduce((n, w) => n + w.openings, 0);
  const parts = [
    `${report.walls.length} walls${doors ? ` (${doors} doorway${doors === 1 ? '' : 's'})` : ''}`,
    `${report.shelf_units.length} shelf unit${report.shelf_units.length === 1 ? '' : 's'}`,
    `${report.blocks.length} other furniture`,
  ];
  if (report.max_wall_offset_m != null) parts.push(`walls within ${Math.round(report.max_wall_offset_m * 100)} cm of the scan`);
  return parts.join(', ');
}

/**
 * Orbitable 3D view of a scanned room. Drag to orbit, right-drag (or two fingers) to
 * pan, scroll to zoom. A click that isn't a drag calls `onPick` with the scan point
 * under it when `picking`, otherwise `onSelectRegion` with the region box under it.
 */
export default function RoomViewer({
  room, regions = [], selectedId = null, labelFor, markers = [], cameras = [], picking = false,
  onPick, onSelectRegion, className = '', label = 'Scanned room',
}) {
  const [mode, setMode] = useState('scan');
  const rebuilt = useRebuilt(room, mode === 'rebuilt');
  const hostRef = useRef(null);
  const sceneRef = useRef(null);
  const downRef = useRef(null);
  const [status, setStatus] = useState('loading');
  const [error, setError] = useState(null);

  useEffect(() => {
    const scene = new RoomScene(hostRef.current);
    sceneRef.current = scene;
    setStatus('loading');
    setError(null);
    scene
      .load(room.files.mesh, room.mesh_to_room)
      .then(() => {
        scene.frame();
        setStatus('ready');
      })
      .catch((e) => {
        setStatus('error');
        setError(e.message || 'Could not load the scan');
      });
    return () => {
      scene.dispose();
      sceneRef.current = null;
    };
  }, [room.room_id, room.files.mesh]);

  useEffect(() => {
    sceneRef.current?.setRegions(regions, { selectedId, labelFor });
  }, [regions, selectedId, labelFor, status]);

  useEffect(() => {
    sceneRef.current?.setMarkers(markers);
  }, [markers, status]);

  useEffect(() => {
    if (status === 'ready') sceneRef.current?.setRebuilt(mode === 'rebuilt' ? rebuilt.data : null);
  }, [mode, rebuilt.data, status]);

  useEffect(() => {
    sceneRef.current?.setCameras(cameras);
  }, [cameras, status]);

  const onPointerDown = (e) => {
    downRef.current = { x: e.clientX, y: e.clientY };
  };
  const onPointerUp = (e) => {
    const down = downRef.current;
    downRef.current = null;
    if (!down || e.button !== 0 || Math.hypot(e.clientX - down.x, e.clientY - down.y) > CLICK_SLOP_PX) return;
    const scene = sceneRef.current;
    if (!scene || status !== 'ready') return;
    if (picking) {
      const hit = scene.pick(e.clientX, e.clientY);
      if (hit) onPick?.(hit);
    } else {
      onSelectRegion?.(scene.pickRegion(e.clientX, e.clientY));
    }
  };

  return (
    <div className={`room-viewer ${picking ? 'is-picking' : ''} ${className}`}>
      <div
        ref={hostRef}
        className="room-host"
        role="img"
        aria-label={label}
        onPointerDown={onPointerDown}
        onPointerUp={onPointerUp}
        onContextMenu={(e) => e.preventDefault()}
      />
      {status !== 'ready' && (
        <div className="room-status" role="status">
          {status === 'loading' ? 'Loading scan…' : `Could not show the scan: ${error}`}
        </div>
      )}
      <div className="room-mode">
        <div className="segmented" role="group" aria-label="Show the room as">
          {[
            ['scan', 'Scan'],
            ['rebuilt', 'Rebuilt'],
          ].map(([id, text]) => (
            <button key={id} type="button" className={mode === id ? 'active' : ''} aria-pressed={mode === id} onClick={() => setMode(id)}>
              {text}
            </button>
          ))}
        </div>
        {mode === 'rebuilt' && (
          <span className="room-mode-note" role="status">
            {rebuilt.error ? `Could not rebuild: ${rebuilt.error}` : rebuilt.data ? rebuiltSummary(rebuilt.data) : 'Rebuilding from the scan…'}
          </span>
        )}
      </div>
      <button type="button" className="icon-btn bordered room-reset" onClick={() => sceneRef.current?.frame()} aria-label="Reset the 3D view" title="Reset view">
        <CornersOutIcon size={15} aria-hidden="true" />
      </button>
    </div>
  );
}

/** The scan drawn from a registered camera, transparent, to lay over that camera's photo. */
export function ScanOverlay({ room, registration, opacity, rebuilt = null }) {
  const hostRef = useRef(null);
  const sceneRef = useRef(null);
  const loaded = useRef(false);
  const rebuiltRef = useRef(rebuilt);

  useEffect(() => {
    const scene = new RoomScene(hostRef.current);
    sceneRef.current = scene;
    loaded.current = false;
    scene
      .load(room.files.mesh, room.mesh_to_room, opacity)
      .then(() => {
        loaded.current = true;
        scene.setRebuilt(rebuiltRef.current);
        scene.setOpacity(opacity);
      })
      .catch(() => {});
    return () => {
      scene.dispose();
      sceneRef.current = null;
    };
  }, [room.room_id, room.files.mesh]);

  useEffect(() => {
    if (registration) sceneRef.current?.lookThrough(registration);
  }, [registration]);

  useEffect(() => {
    rebuiltRef.current = rebuilt;
    if (!loaded.current) return;
    sceneRef.current?.setRebuilt(rebuilt);
    sceneRef.current?.setOpacity(opacity);
  }, [rebuilt]);

  useEffect(() => {
    sceneRef.current?.setOpacity(opacity);
  }, [opacity]);

  return <div ref={hostRef} className="scan-overlay" aria-hidden="true" />;
}
