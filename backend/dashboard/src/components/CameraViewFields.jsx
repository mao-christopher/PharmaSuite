import React from 'react';
import RegionEditor from './RegionEditor';
import { Badge } from './ui';

const pct = (score) => `${Math.round(score * 100)}% match`;

/** Best-matching view, preferring one no other camera of the same recording already uses. */
export function pickView(suggestions, taken = new Set()) {
  return (suggestions.find((s) => s.has_photo && !taken.has(s.layout_id)) || suggestions[0])?.layout_id;
}

export const isEdited = (base, regions) => Boolean(base && regions && JSON.stringify(regions) !== JSON.stringify(base.regions));

export const hasUnassignedShelf = (regions) =>
  Boolean(regions?.some((r) => r.region_type === 'designated_shelf' && !r.medication_key));

/**
 * Pick a saved camera view for one camera and adjust its regions on that camera's own
 * frame. `info` is the view-suggestion response (suggestions, width, height). `decision`
 * sits above the editor (what happens to edits); `children` below it.
 */
export default function CameraViewFields({
  info,
  baseId,
  onBaseId,
  base,
  regions,
  onRegions,
  editor,
  imageUrl,
  multi,
  sharedWith = [],
  newName,
  onNewName,
  fitHeight,
  decision,
  children,
}) {
  const suggestion = info.suggestions.find((s) => s.layout_id === baseId);
  const baseName = base?.name || baseId;
  const draft = base && regions && { ...base, regions, frame_width: info.width, frame_height: info.height };

  return (
    <div className="form">
      <div className="review-head">
        <label className="field">
          <span className="label">Start from</span>
          <select className="input" name="base-view" value={baseId || ''} onChange={(e) => onBaseId(e.target.value)}>
            {info.suggestions.map((s, i) => (
              <option key={s.layout_id} value={s.layout_id}>
                {s.name} ({s.has_photo ? pct(s.score) : 'no photo'}){i === 0 && s.has_photo ? ', suggested' : ''}
              </option>
            ))}
          </select>
        </label>
        {onNewName && (
          <label className="field">
            <span className="label">New view name</span>
            <input className="input" name="new-view-name" autoComplete="off" value={newName} onChange={(e) => onNewName(e.target.value)} />
          </label>
        )}
      </div>
      {suggestion && (
        <p className="hint">
          {suggestion.has_photo ? (
            <>
              <Badge tone={suggestion.score >= 0.7 ? 'green' : suggestion.score >= 0.4 ? 'amber' : 'red'}>{pct(suggestion.score)}</Badge>{' '}
              Its regions are drawn on this {multi ? "camera's" : "video's"} first frame ({info.width}×{info.height}).
            </>
          ) : (
            'This view has no photo to compare with, so the match is unknown.'
          )}
          {!suggestion.same_aspect &&
            ` The view was annotated at ${suggestion.frame_width}×${suggestion.frame_height}, a different shape, so check every region lines up.`}
        </p>
      )}
      {decision}
      {sharedWith.length > 0 && (
        <p className="hint text-amber">
          {sharedWith.join(', ')} already uses {baseName}. Each camera sees the room from its own angle, so save this one as a
          new view.
        </p>
      )}
      {draft ? (
        <div className="card">
          <RegionEditor draft={draft} onRegions={onRegions} imageUrl={imageUrl} editor={editor} inlinePanel fitHeight={fitHeight} />
        </div>
      ) : (
        <p className="hint">Loading {baseId}…</p>
      )}
      {children}
      {hasUnassignedShelf(regions) && <p className="form-error">Assign a medication to every shelf before saving.</p>}
    </div>
  );
}
