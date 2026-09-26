import React, { useCallback, useEffect, useState } from 'react';
import { useLive } from '../lib/live';
import { request } from '../lib/api';
import RegionEditor, { useRegionEditor } from './RegionEditor';
import { Badge, Dialog } from './ui';

const pct = (score) => `${Math.round(score * 100)}% match`;

/**
 * After an upload: suggest the saved camera view that looks most like the video, let the
 * employee adjust its regions on the video's own frame, then keep, replace, or add a view.
 */
export default function ViewReviewDialog({ recording, onClose }) {
  const { refresh } = useLive();
  const editor = useRegionEditor();
  const [info, setInfo] = useState(null);
  const [baseId, setBaseId] = useState(null);
  const [base, setBase] = useState(null);
  const [regions, setRegions] = useState(null);
  const [newName, setNewName] = useState('');
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    request(`/api/recordings/${encodeURIComponent(recording.name)}/views`)
      .then((r) => {
        setInfo(r);
        setBaseId(r.layout_id || r.suggestions[0]?.layout_id);
        setNewName(`View from ${r.label}`);
      })
      .catch((e) => setError(e.message));
  }, [recording.name]);

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
  }, [baseId]);

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
          name: action === 'new' ? newName.trim() || undefined : undefined,
          regions: action === 'use' ? undefined : regions,
        },
      });
      await refresh();
      onClose();
    } catch (e) {
      setError(e.message);
      setBusy(null);
    }
  };

  const suggestion = info?.suggestions.find((s) => s.layout_id === baseId);
  const edited = base && regions && JSON.stringify(regions) !== JSON.stringify(base.regions);
  const baseName = base?.name || baseId;
  const draft = base && regions && { ...base, regions, frame_width: info.width, frame_height: info.height };
  const unassigned = regions?.some((r) => r.region_type === 'designated_shelf' && !r.medication_key);

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
      {!info ? (
        <p className="hint">{error || 'Comparing this video with saved views…'}</p>
      ) : (
        <div className="form">
          <div className="review-head">
            <label className="field">
              <span className="label">Start from</span>
              <select className="input" name="base-view" value={baseId || ''} onChange={(e) => setBaseId(e.target.value)}>
                {info.suggestions.map((s, i) => (
                  <option key={s.layout_id} value={s.layout_id}>
                    {s.name} ({s.has_photo ? pct(s.score) : 'no photo'}){i === 0 && s.has_photo ? ', suggested' : ''}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span className="label">New view name</span>
              <input className="input" name="new-view-name" autoComplete="off" value={newName} onChange={(e) => setNewName(e.target.value)} />
            </label>
          </div>
          {suggestion && (
            <p className="hint">
              {suggestion.has_photo ? (
                <>
                  <Badge tone={suggestion.score >= 0.7 ? 'green' : suggestion.score >= 0.4 ? 'amber' : 'red'}>{pct(suggestion.score)}</Badge>{' '}
                  Its regions are drawn on this video's first frame ({info.width}×{info.height}).
                </>
              ) : (
                'This view has no photo to compare with, so the match is unknown.'
              )}
              {!suggestion.same_aspect &&
                ` The view was annotated at ${suggestion.frame_width}×${suggestion.frame_height}, a different shape, so check every region lines up.`}
            </p>
          )}
          {draft ? (
            <div className="card">
              <RegionEditor
                draft={draft}
                onRegions={onRegions}
                imageUrl={`/api/recordings/${encodeURIComponent(recording.name)}/frame`}
                editor={editor}
                inlinePanel
                fitHeight="(100dvh - 470px)"
              />
            </div>
          ) : (
            <p className="hint">Loading {baseId}…</p>
          )}
          <p className="hint">
            Replace updates {baseName} for every recording that uses it, with this video's frame as its photo. Save as new
            view keeps {baseName} and adds this angle. Either way the regions match this video exactly.
          </p>
          {unassigned && <p className="form-error">Assign a medication to every shelf before saving.</p>}
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
        </div>
      )}
    </Dialog>
  );
}
