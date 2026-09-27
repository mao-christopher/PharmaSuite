import React, { useEffect, useState } from 'react';
import { CheckIcon } from '@phosphor-icons/react';
import { useLive } from '../lib/live';
import { request } from '../lib/api';
import CameraViewFields from './CameraViewFields';
import { Dialog } from './ui';

/**
 * After an upload: suggest the saved camera view that looks most like the video, then use
 * it, update its photo with the video's frame, or add a new view. Regions are shown
 * read-only; they come from the room's 3D tags. A multi-camera recording is reviewed one
 * camera at a time.
 */
export default function ViewReviewDialog({ recording, onClose }) {
  const { refresh } = useLive();
  const cameras = recording.cameras || [];
  const multi = cameras.length > 1;
  const [cameraId, setCameraId] = useState(multi ? cameras[0].camera_id : null);
  const [done, setDone] = useState(() => new Set(cameras.filter((c) => c.view_confirmed).map((c) => c.camera_id)));
  const [info, setInfo] = useState(null);
  const [baseId, setBaseId] = useState(null);
  const [base, setBase] = useState(null);
  const [action, setAction] = useState('use');
  const [newName, setNewName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const camera = cameras.find((c) => c.camera_id === cameraId);
  const query = cameraId ? `?camera=${encodeURIComponent(cameraId)}` : '';

  useEffect(() => {
    setInfo(null);
    setBase(null);
    setError(null);
    setBusy(false);
    setAction('use');
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
      .then(setBase)
      .catch((e) => setError(e.message));
  }, [baseId, cameraId]);

  const decide = async () => {
    setBusy(true);
    setError(null);
    try {
      await request(`/api/recordings/${encodeURIComponent(recording.name)}/view`, {
        method: 'POST',
        body: {
          action,
          layout_id: action === 'new' ? undefined : baseId,
          camera_id: cameraId,
          name: action === 'new' ? newName.trim() || undefined : undefined,
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
      setBusy(false);
    }
  };

  const baseName = base?.name || baseId;
  // Another camera of this recording already uses this view; one view can't fit two angles.
  const sharedWith = multi
    ? (info?.cameras || []).filter((c) => c.camera_id !== cameraId && c.layout_id === baseId && (done.has(c.camera_id) || c.view_confirmed))
    : [];
  const label = { use: `Use ${baseName}`, replace: `Update ${baseName}'s photo`, new: 'Add camera view' }[action];

  return (
    <Dialog
      title={`Camera view for ${info?.label || recording.label || recording.name}`}
      onClose={onClose}
      width={1000}
      footer={
        <>
          <span className="hint push-right">Skeleton extraction keeps running while you decide.</span>
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            Later
          </button>
          <button type="button" className="btn btn-primary" disabled={(!base && action !== 'new') || busy} onClick={decide}>
            {busy ? 'Saving…' : label}
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
              disabled={busy}
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
          action={action}
          onAction={setAction}
          imageUrl={`/api/recordings/${encodeURIComponent(recording.name)}/frame${query}`}
          multi={multi}
          sharedWith={sharedWith.map((c) => c.label)}
          newName={newName}
          onNewName={setNewName}
          fitHeight={multi ? '(100dvh - 520px)' : '(100dvh - 470px)'}
        >
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
