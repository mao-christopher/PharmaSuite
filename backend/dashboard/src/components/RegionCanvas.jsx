import React, { useCallback, useEffect, useRef, useState } from 'react';
import { REGION_TYPES } from '../lib/format';

const clamp01 = (v) => Math.min(1, Math.max(0, v));
const round = (v) => Math.round(v * 10000) / 10000;
const isTyping = (e) => ['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target.tagName);

export default function RegionCanvas({
  frameWidth: W,
  frameHeight: H,
  regions,
  labelFor,
  selectedId,
  onSelect,
  tool,
  onCreate,
  onCancelTool,
  onChangePolygon,
  showGrid,
  imageUrl,
  fitHeight = '(100dvh - 260px)',
}) {
  const svgRef = useRef(null);
  const drag = useRef(null);
  const [unit, setUnit] = useState(1);
  const [draft, setDraft] = useState([]);
  const [hover, setHover] = useState(null);
  const drawing = tool !== 'select';

  useEffect(() => {
    const el = svgRef.current;
    const ro = new ResizeObserver(() => {
      const width = el.getBoundingClientRect().width;
      if (width) setUnit(W / width);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [W]);

  useEffect(() => setDraft([]), [tool]);

  const toNorm = (e) => {
    const r = svgRef.current.getBoundingClientRect();
    return [round(clamp01((e.clientX - r.left) / r.width)), round(clamp01((e.clientY - r.top) / r.height))];
  };
  const nearFirst = (pt) =>
    pt && draft.length >= 3 && Math.hypot((pt[0] - draft[0][0]) * W, (pt[1] - draft[0][1]) * H) < 10 * unit;

  const finish = useCallback(() => {
    if (draft.length >= 3) onCreate(draft);
    setDraft([]);
  }, [draft, onCreate]);

  useEffect(() => {
    if (!drawing) return undefined;
    const onKey = (e) => {
      if (isTyping(e)) return;
      if (e.key === 'Enter') finish();
      else if (e.key === 'Escape') (draft.length ? setDraft([]) : onCancelTool());
      else if (e.key === 'Backspace') {
        e.preventDefault();
        setDraft((d) => d.slice(0, -1));
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [drawing, draft.length, finish, onCancelTool]);

  const selected = regions.find((r) => r.region_id === selectedId);

  const onPointerDown = (e) => {
    if (e.button !== 0) return;
    const pt = toNorm(e);
    if (drawing) {
      if (nearFirst(pt)) finish();
      else setDraft((d) => [...d, pt]);
      return;
    }
    const data = e.target.dataset;
    if (data.vertex !== undefined && selected) {
      const index = Number(data.vertex);
      if (e.shiftKey || e.altKey) {
        if (selected.polygon.length > 3) onChangePolygon(selected.region_id, selected.polygon.filter((_, i) => i !== index));
        return;
      }
      drag.current = { kind: 'vertex', id: selected.region_id, index };
    } else if (data.mid !== undefined && selected) {
      const index = Number(data.mid) + 1;
      const poly = [...selected.polygon];
      poly.splice(index, 0, pt);
      onChangePolygon(selected.region_id, poly);
      drag.current = { kind: 'vertex', id: selected.region_id, index };
    } else if (data.region) {
      const region = regions.find((r) => r.region_id === data.region);
      onSelect(region.region_id);
      drag.current = { kind: 'move', id: region.region_id, start: pt, orig: region.polygon };
    } else {
      onSelect(null);
      return;
    }
    svgRef.current.setPointerCapture(e.pointerId);
  };

  const onPointerMove = (e) => {
    const pt = toNorm(e);
    if (drawing) {
      setHover(pt);
      return;
    }
    const d = drag.current;
    if (!d) return;
    const region = regions.find((r) => r.region_id === d.id);
    if (!region) return;
    if (d.kind === 'vertex') {
      onChangePolygon(d.id, region.polygon.map((p, i) => (i === d.index ? pt : p)));
      return;
    }
    const xs = d.orig.map((p) => p[0]);
    const ys = d.orig.map((p) => p[1]);
    const dx = Math.min(1 - Math.max(...xs), Math.max(-Math.min(...xs), pt[0] - d.start[0]));
    const dy = Math.min(1 - Math.max(...ys), Math.max(-Math.min(...ys), pt[1] - d.start[1]));
    if (dx === 0 && dy === 0) return;
    onChangePolygon(d.id, d.orig.map(([x, y]) => [round(x + dx), round(y + dy)]));
  };

  const toPx = ([x, y]) => `${x * W},${y * H}`;
  const drawColor = drawing ? REGION_TYPES[tool].color : null;
  const preview = hover && !nearFirst(hover) ? [...draft, hover] : draft;

  return (
    <div className="canvas" style={{ aspectRatio: `${W} / ${H}`, width: `min(100%, calc(${fitHeight} * ${W / H}))` }}>
      <img src={imageUrl} alt="Fixed camera view used for annotation" width={W} height={H} draggable={false} />
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        className={drawing ? 'drawing' : ''}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={() => (drag.current = null)}
        onPointerLeave={() => setHover(null)}
      >
        {showGrid &&
          Array.from({ length: 9 }, (_, i) => (i + 1) / 10).map((f) => (
            <g key={f} className="grid-line">
              <line x1={f * W} y1={0} x2={f * W} y2={H} />
              <line x1={0} y1={f * H} x2={W} y2={f * H} />
            </g>
          ))}

        {regions.map((r) => {
          const { color } = REGION_TYPES[r.region_type];
          const isSel = r.region_id === selectedId;
          const minX = Math.min(...r.polygon.map((p) => p[0])) * W;
          const minY = Math.min(...r.polygon.map((p) => p[1])) * H;
          return (
            <g key={r.region_id}>
              <polygon
                data-region={r.region_id}
                points={r.polygon.map(toPx).join(' ')}
                fill={color}
                fillOpacity={isSel ? 0.24 : 0.12}
                stroke={color}
                strokeWidth={isSel ? 2.5 : 1.75}
                strokeDasharray={r.region_type === 'designated_shelf' && !r.medication_key ? '6 4' : undefined}
                vectorEffect="non-scaling-stroke"
                style={{ pointerEvents: drawing ? 'none' : 'all', cursor: 'move' }}
              />
              <text
                x={minX + 6 * unit}
                y={minY + 16 * unit}
                fontSize={12 * unit}
                fill={color}
                stroke="#fff"
                strokeWidth={3 * unit}
                paintOrder="stroke"
                fontWeight="600"
                pointerEvents="none"
              >
                {labelFor(r)}
              </text>
            </g>
          );
        })}

        {!drawing && selected && (
          <g>
            {selected.polygon.map((p, i) => {
              const q = selected.polygon[(i + 1) % selected.polygon.length];
              return (
                <circle
                  key={`m${i}`}
                  data-mid={i}
                  className="handle-mid"
                  cx={((p[0] + q[0]) / 2) * W}
                  cy={((p[1] + q[1]) / 2) * H}
                  r={4 * unit}
                />
              );
            })}
            {selected.polygon.map((p, i) => (
              <circle
                key={`v${i}`}
                data-vertex={i}
                className="handle"
                cx={p[0] * W}
                cy={p[1] * H}
                r={6 * unit}
                stroke={REGION_TYPES[selected.region_type].color}
              />
            ))}
          </g>
        )}

        {drawing && draft.length > 0 && (
          <g pointerEvents="none">
            <polyline
              points={preview.map(toPx).join(' ')}
              fill={drawColor}
              fillOpacity={0.08}
              stroke={drawColor}
              strokeWidth={2}
              strokeDasharray="6 4"
              vectorEffect="non-scaling-stroke"
            />
            {draft.map((p, i) => (
              <circle
                key={i}
                cx={p[0] * W}
                cy={p[1] * H}
                r={(i === 0 && nearFirst(hover) ? 9 : 5) * unit}
                fill={i === 0 ? drawColor : '#fff'}
                stroke={drawColor}
                strokeWidth={2}
                vectorEffect="non-scaling-stroke"
              />
            ))}
          </g>
        )}
      </svg>
    </div>
  );
}
