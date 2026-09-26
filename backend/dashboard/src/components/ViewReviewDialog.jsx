import React, { useCallback, useEffect, useState } from 'react';
import { CheckIcon } from '@phosphor-icons/react';
import { useLive } from '../lib/live';
import { request } from '../lib/api';
import { useRegionEditor } from './RegionEditor';
import CameraViewFields, { hasUnassignedShelf, isEdited } from './CameraViewFields';
import { Dialog } from './ui';

/**
 * After an upload: suggest the saved camera view that looks most like the video, let the
 * employee adjust its regions on the video's own frame, then keep, replace, or add a view.
 * A multi-camera recording is reviewed one camera at a time.
 */
export default function ViewReviewDialog({ recording, onClose }) {
  const { refresh } = useLive();
  const editor = useRegionEditor();
  const cameras = recording.cameras || [];
  const multi = cameras.length > 1;
  const [cameraId, setCameraId] = useState(multi ? cameras[0].camera_id : null);
  const [done, setDone] = useState(() => new Set(cameras.filter((c) => c.view_confirmed).map((c) => c.camera_id)));
  const [info, setInfo] = useState(null);
  const [baseId, setBaseId] = useState(null);
  const [base, setBase] = useState(null);
  const [regions, setRegions] = useState(null);
  const [newName, setNewName] = useState('');
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);
  const camera = cameras.find((c) => c.camera_id === cameraId);
  const query = cameraId ? `?camera=${encodeURIComponent(cameraId)}` : '';

  useEffect(() => {
    setInfo(null);
    setBase(null);
    setRegions(null);
    setError(null);
    setBusy(null);
    request(`/api/recordings/${encodeURIComponent(recording.name)}/views${query}`)
      .then((r) => {
        setInfo(r);
        setBaseId(r.layout_id || r.suggestions[0]?.layout_id);
        const cam = r.cameras?.find((c) => c.camera_id === cameraId);
        setNewName(`View from ${r.label}${cam ? `, ${cam.label}` : ''}`);
      })
      .catch((e) => setError(e.message));
  }, [recording.name, cameraId]);

  useEffect(() => {
    if (!baseId) return;
    setBase(null);
    request(`/api/layouts/${encodeURIComponent(baseId)}`)
      .then((layout) => {
        setBase(layout);
        setRegions(layout.regions);
        editor.setSelectedId(null);
      })
      .catch((e) => setError(e.message));
  }, [baseId, cameraId]);

  const onRegions = useCallback((fn) => setRegions((rs) => fn(rs)), []);

  const decide = async (action) => {
    setBusy(action);
    setError(null);
    try {
      await request(`/api/recordings/${encodeURIComponent(recording.name)}/view`, {
        method: 'POST',
        body: {
          action,
          layout_id: baseId,
          camera_id: cameraId,
          name: action === 'new' ? newName.trim() || undefined : undefined,
          regions: action === 'use' ? undefined : regions,
        },
      });
      await refresh();
      const next = new Set(done).add(cameraId);
      const remaining = cameras.find((c) => !next.has(c.camera_id));
      if (multi && remaining) {
        setDone(next);
        setCameraId(remaining.camera_id);
      } else {
        onClose();
      }
    } catch (e) {
      setError(e.message);
      setBusy(null);
    }
  };

  const edited = isEdited(base, regions);
  const baseName = base?.name || baseId;
  const unassigned = hasUnassignedShelf(regions);
  // Another camera of this recording already uses this view; one view can't fit two angles.
  const sharedWith = multi
    ? (info?.cameras || []).filter((c) => c.camera_id !== cameraId && c.layout_id === baseId && (done.has(c.camera_id) || c.view_confirmed))
    : [];

  return (
    <Dialog
      title={`Camera view for ${info?.label || recording.label || recording.name}`}
      onClose={onClose}
      width={1000}
      footer={
        <>
          <span className="hint push-right">Skeleton extraction keeps running while you decide.</span>
          <button type="button" className="btn btn-ghost" disabled={!base || busy} onClick={() => decide('use')}>
            {busy === 'use' ? 'Saving…' : edited ? "Don't save edits" : `Use ${baseName} as is`}
          </button>
          <button type="button" className="btn" disabled={!base || busy || unassigned} onClick={() => decide('replace')}>
            {busy === 'replace' ? 'Saving…' : `Replace ${baseName}`}
          </button>
          <button type="button" className="btn btn-primary" disabled={!base || busy || unassigned} onClick={() => decide('new')}>
            {busy === 'new' ? 'Saving…' : 'Save as new view'}
          </button>
        </>
      }
    >
      {multi && (
        <div className="camera-tabs segmented" role="tablist" aria-label="Cameras">
          {cameras.map((c, i) => (
            <button
              key={c.camera_id}
              type="button"
              role="tab"
              aria-selected={c.camera_id === cameraId}
              className={`camera-tab ${c.camera_id === cameraId ? 'active' : ''}`}
              disabled={Boolean(busy)}
              onClick={() => setCameraId(c.camera_id)}
            >
              {done.has(c.camera_id) && <CheckIcon size={13} aria-label="View chosen" />}
              {i + 1}. {c.label}
            </button>
          ))}
        </div>
      )}
      {!info ? (
        <p className="hint">{error || `Comparing ${camera ? camera.label : 'this video'} with saved views…`}</p>
      ) : (
        <CameraViewFields
          info={info}
          baseId={baseId}
          onBaseId={setBaseId}
          base={base}
          regions={regions}
          onRegions={onRegions}
          editor={editor}
          imageUrl={`/api/recordings/${encodeURIComponent(recording.name)}/frame${query}`}
          multi={multi}
          sharedWith={sharedWith.map((c) => c.label)}
          newName={newName}
          onNewName={setNewName}
          fitHeight={multi ? '(100dvh - 520px)' : '(100dvh - 470px)'}
        >
          <p className="hint">
            Replace updates {baseName} for every recording that uses it, with this {multi ? "camera's" : "video's"} frame as its
            photo. Save as new view keeps {baseName} and adds this angle. Either way the regions match this video exactly.
          </p>
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
        </CameraViewFields>
      )}
    </Dialog>
  );
}
