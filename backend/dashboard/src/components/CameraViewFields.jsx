import React from 'react';
import { Link } from 'react-router-dom';
import RegionPreview from './RegionPreview';
import { Badge } from './ui';

const pct = (score) => `${Math.round(score * 100)}% match`;

/** Best-matching view, preferring one no other camera of the same recording already uses. */
export function pickView(suggestions, taken = new Set()) {
  return (suggestions.find((s) => s.has_photo && !taken.has(s.layout_id)) || suggestions[0])?.layout_id;
}

export const VIEW_ACTIONS = [
  ['use', 'Use as is'],
  ['replace', 'Update its photo'],
  ['new', 'New camera view'],
];

/** Where a view's regions come from, in one sentence for the employee. */
export function regionStatus(base, action) {
  if (action === 'new') {
    return {
      tone: 'amber',
      text: 'A new view starts without shelf regions. Register its camera on the Room page so its signals can be placed; until then each one asks for confirmation.',
    };
  }
  if (!base) return null;
  if (base.regions_source) {
    return { tone: 'muted', text: `Regions come from the 3D tags in room ${base.regions_source.room_id}.` };
  }
  if (base.regions.length) {
    return {
      tone: 'amber',
      text: 'These regions were drawn by hand in an older version. Register this camera on the Room page to use the 3D tags instead.',
    };
  }
  return { tone: 'amber', text: 'This view has no shelf regions yet. Register its camera on the Room page, or its signals ask for confirmation.' };
}

/**
 * Pick a saved camera view for one camera and decide what the upload does with it. The
 * view's regions are shown read-only over this camera's frame: regions are made from the
 * room's 3D tags on the Room page, never drawn here. `info` is the view-suggestion
 * response (suggestions, width, height).
 */
export default function CameraViewFields({
  info,
  baseId,
  onBaseId,
  base,
  action,
  onAction,
  imageUrl,
  multi,
  sharedWith = [],
  newName,
  onNewName,
  fitHeight,
  children,
}) {
  const suggestion = info.suggestions.find((s) => s.layout_id === baseId);
  const baseName = base?.name || baseId;
  const status = regionStatus(base, action);
  const source = multi ? "camera's" : "video's";

  return (
    <div className="form">
      <div className="review-head">
        <label className="field">
          <span className="label">Camera view</span>
          <select className="input" name="base-view" value={baseId || ''} onChange={(e) => onBaseId(e.target.value)}>
            {info.suggestions.map((s, i) => (
              <option key={s.layout_id} value={s.layout_id}>
                {s.name} ({s.has_photo ? pct(s.score) : 'no photo'}){i === 0 && s.has_photo ? ', suggested' : ''}
              </option>
            ))}
          </select>
        </label>
        <div className="field">
          <span className="label">This {multi ? 'camera' : 'video'}</span>
          <div className="segmented" role="group" aria-label="What to do with the camera view">
            {VIEW_ACTIONS.map(([id, label]) => (
              <button key={id} type="button" className={action === id ? 'active' : ''} aria-pressed={action === id} onClick={() => onAction(id)}>
                {label}
              </button>
            ))}
          </div>
        </div>
        {action === 'new' && (
          <label className="field">
            <span className="label">New view name</span>
            <input className="input" name="new-view-name" autoComplete="off" value={newName} onChange={(e) => onNewName(e.target.value)} />
          </label>
        )}
      </div>
      {suggestion && action !== 'new' && (
        <p className="hint">
          {suggestion.has_photo ? (
            <>
              <Badge tone={suggestion.score >= 0.7 ? 'green' : suggestion.score >= 0.4 ? 'amber' : 'red'}>{pct(suggestion.score)}</Badge>{' '}
              {baseName}'s regions are drawn on this {source} first frame ({info.width}×{info.height}).
            </>
          ) : (
            'This view has no photo to compare with, so the match is unknown.'
          )}
          {!suggestion.same_aspect &&
            ` The view was set up at ${suggestion.frame_width}×${suggestion.frame_height}, a different shape, so the regions won't line up.`}
        </p>
      )}
      {action === 'replace' && (
        <p className="hint text-amber">
          {baseName} gets this {source} frame as its photo, for every recording that uses it. Its regions and camera registration stay; if
          the camera moved, register it again on the <Link to={`/room?tab=cameras&view=${encodeURIComponent(baseId)}`}>Room page</Link>.
        </p>
      )}
      {sharedWith.length > 0 && action !== 'new' && (
        <p className="hint text-amber">
          {sharedWith.join(', ')} already uses {baseName}. Each camera sees the room from its own angle, so make this one a new camera view.
        </p>
      )}
      {base || action === 'new' ? (
        <div className="card">
          <RegionPreview
            imageUrl={imageUrl}
            width={info.width}
            height={info.height}
            layout={action === 'new' ? null : base}
            regions={action === 'new' ? [] : base.regions}
            fitHeight={fitHeight}
            alt={`First frame of this ${multi ? 'camera' : 'video'}`}
          />
        </div>
      ) : (
        <p className="hint">Loading {baseId}…</p>
      )}
      {status && <p className={`hint ${status.tone === 'amber' ? 'text-amber' : ''}`}>{status.text}</p>}
      {children}
    </div>
  );
}
