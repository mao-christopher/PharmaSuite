import React, { useCallback, useEffect, useState } from 'react';
import { CursorIcon, GridFourIcon, TrashIcon } from '@phosphor-icons/react';
import { REGION_TYPES, regionLabel } from '../lib/format';
import RegionCanvas from './RegionCanvas';

export const TOOLS = [
  { id: 'select', label: 'Select', icon: CursorIcon },
  { id: 'designated_shelf', label: 'Shelf' },
  { id: 'dispensing_counter', label: 'Counter' },
  { id: 'disposal', label: 'Disposal' },
];

const isTyping = (e) => ['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target.tagName);

export function nextId(prefix, existing) {
  const taken = new Set(existing);
  let n = 1;
  while (taken.has(`${prefix}_${String(n).padStart(2, '0')}`)) n += 1;
  return `${prefix}_${String(n).padStart(2, '0')}`;
}

/** Shelves are named after their medication, so every view counts the same shelf. */
export const shelfIdFor = (medKey) => `shelf_${medKey.toLowerCase()}`;

export function useRegionEditor() {
  const [tool, setTool] = useState('select');
  const [pendingMed, setPendingMed] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [showGrid, setShowGrid] = useState(true);
  return { tool, setTool, pendingMed, setPendingMed, selectedId, setSelectedId, showGrid, setShowGrid };
}

export function labelForRegion(draft, r) {
  return r.region_type === 'designated_shelf' && !draft.medications.some((m) => m.medication_key === r.medication_key)
    ? 'Unassigned shelf'
    : regionLabel(draft, r.region_id);
}

/**
 * Toolbar + polygon canvas for one camera view. `draft` needs medications, regions,
 * frame_width and frame_height; changes go through `onRegions(updater)`.
 */
export default function RegionEditor({ draft, onRegions, imageUrl, editor, toolbarExtra, inlinePanel = false, fitHeight }) {
  const { tool, setTool, pendingMed, setPendingMed, selectedId, setSelectedId, showGrid, setShowGrid } = editor;

  const setPolygon = useCallback(
    (id, polygon) => onRegions((regions) => regions.map((r) => (r.region_id === id ? { ...r, polygon } : r))),
    [onRegions],
  );

  const createRegion = useCallback(
    (polygon) => {
      const ids = draft.regions.map((r) => r.region_id);
      const takenShelf = pendingMed && draft.regions.some((r) => r.medication_key === pendingMed);
      const medication_key = tool === 'designated_shelf' && !takenShelf ? pendingMed : null;
      const id = medication_key ? shelfIdFor(medication_key) : nextId(REGION_TYPES[tool].prefix, ids);
      onRegions((regions) => [...regions, { region_id: id, region_type: tool, medication_key, polygon }]);
      setSelectedId(id);
      setTool('select');
      setPendingMed(null);
    },
    [tool, pendingMed, draft, onRegions],
  );

  const deleteRegion = (id) => {
    onRegions((regions) => regions.filter((r) => r.region_id !== id));
    setSelectedId(null);
  };

  const assignShelf = (regionId, medKey) => {
    const { newId, regions } = reassignShelf(draft.regions, regionId, medKey);
    onRegions(() => regions);
    setSelectedId(newId);
  };

  useEffect(() => {
    if (tool !== 'select' || !selectedId) return undefined;
    const onKey = (e) => {
      if (isTyping(e)) return;
      if (e.key === 'Delete' || e.key === 'Backspace') {
        e.preventDefault();
        deleteRegion(selectedId);
      } else if (e.key === 'Escape') setSelectedId(null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [tool, selectedId]);

  const selected = draft.regions.find((r) => r.region_id === selectedId);
  const pendingMedLabel = draft.medications.find((m) => m.medication_key === pendingMed);
  const takenShelfMeds = new Set(draft.regions.filter((r) => r.region_id !== selectedId).map((r) => r.medication_key));
  const hint =
    tool === 'select'
      ? selected
        ? 'Drag the region or its corners. Click a midpoint to add a corner; Shift-click a corner to remove it. Delete removes the region.'
        : 'Click a region to edit it, or pick a tool to draw a new one.'
      : `Click to place corners${pendingMedLabel ? ` of the ${pendingMedLabel.name} ${pendingMedLabel.strength} shelf` : ''}. Click the first corner or press Enter to finish. Backspace undoes a corner; Esc cancels.`;

  return (
    <div className="region-editor">
      <div className="toolbar">
        <div className="segmented" role="toolbar" aria-label="Annotation tools">
          {TOOLS.map((t) => {
            const Icon = t.icon;
            return (
              <button
                key={t.id}
                type="button"
                className={tool === t.id ? 'active' : ''}
                aria-pressed={tool === t.id}
                onClick={() => {
                  setTool(t.id);
                  setPendingMed(null);
                }}
              >
                {Icon ? (
                  <Icon size={14} aria-hidden="true" />
                ) : (
                  <span className="dot" aria-hidden="true" style={{ background: REGION_TYPES[t.id].color }} />
                )}
                {t.label}
              </button>
            );
          })}
        </div>
        <div className="toolbar-right">
          {toolbarExtra}
          <button
            type="button"
            className={`btn btn-ghost btn-sm ${showGrid ? 'active' : ''}`}
            aria-pressed={showGrid}
            onClick={() => setShowGrid((v) => !v)}
          >
            <GridFourIcon size={14} aria-hidden="true" /> Grid
          </button>
        </div>
      </div>
      <RegionCanvas
        frameWidth={draft.frame_width}
        frameHeight={draft.frame_height}
        regions={draft.regions}
        labelFor={(r) => labelForRegion(draft, r)}
        selectedId={selectedId}
        onSelect={setSelectedId}
        tool={tool}
        onCreate={createRegion}
        onCancelTool={() => {
          setTool('select');
          setPendingMed(null);
        }}
        onChangePolygon={setPolygon}
        showGrid={showGrid}
        imageUrl={imageUrl}
        fitHeight={fitHeight}
      />
      <p className="hint canvas-hint" aria-live="polite">
        {hint}
      </p>
      {inlinePanel && selected && (
        <div className="selected-bar">
          <span className="dot" aria-hidden="true" style={{ background: REGION_TYPES[selected.region_type].color }} />
          <span className="strong">{labelForRegion(draft, selected)}</span>
          {selected.region_type === 'designated_shelf' && (
            <select
              className="input input-sm"
              name="shelf-medication"
              aria-label="Medication on this shelf"
              value={draft.medications.some((m) => m.medication_key === selected.medication_key) ? selected.medication_key : ''}
              onChange={(e) => assignShelf(selected.region_id, e.target.value)}
            >
              <option value="">Choose a medication…</option>
              {draft.medications.map((m) => (
                <option key={m.medication_key} value={m.medication_key} disabled={takenShelfMeds.has(m.medication_key)}>
                  {m.name} {m.strength}
                  {takenShelfMeds.has(m.medication_key) ? ' (has a shelf)' : ''}
                </option>
              ))}
            </select>
          )}
          <button type="button" className="btn btn-sm btn-danger push" onClick={() => deleteRegion(selected.region_id)}>
            <TrashIcon size={13} aria-hidden="true" /> Delete region
          </button>
        </div>
      )}
    </div>
  );
}

/** Assign (or clear) a shelf's medication; the shelf takes that medication's canonical ID. */
export function reassignShelf(regions, regionId, medKey) {
  const newId = medKey ? shelfIdFor(medKey) : nextId('shelf', regions.map((r) => r.region_id));
  return {
    newId,
    regions: regions.map((r) => (r.region_id === regionId ? { ...r, region_id: newId, medication_key: medKey || null } : r)),
  };
}
