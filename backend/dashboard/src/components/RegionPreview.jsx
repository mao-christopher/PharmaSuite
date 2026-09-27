import React from 'react';
import { REGION_TYPES, regionLabel } from '../lib/format';

/**
 * A camera image with a view's 2D regions drawn over it, read-only. The regions are
 * the ones the inventory matches hands against; they come from the room's 3D boxes.
 */
export default function RegionPreview({ imageUrl, width, height, layout, regions = layout?.regions || [], fitHeight, alt, children }) {
  const size = fitHeight ? { width: `min(100%, calc(${fitHeight} * ${width / height}))` } : {};
  const fontSize = Math.max(width, height) / 70;
  return (
    <div className="canvas region-preview" style={{ aspectRatio: `${width} / ${height}`, ...size }}>
      {imageUrl && <img src={imageUrl} alt={alt || ''} draggable={false} />}
      <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-hidden="true">
        {regions.map((r) => {
          const pts = r.polygon.map(([u, v]) => [u * width, v * height]);
          const color = REGION_TYPES[r.region_type]?.color || '#888';
          const [x, y] = pts.reduce(([ax, ay], [px, py]) => [Math.min(ax, px), Math.min(ay, py)], [Infinity, Infinity]);
          return (
            <g key={r.region_id} className="preview-region" style={{ '--region': color }}>
              <polygon points={pts.map((p) => p.join(',')).join(' ')} />
              <text x={x + fontSize * 0.4} y={y + fontSize * 1.2} style={{ fontSize }}>
                {layout ? regionLabel(layout, r.region_id) : r.region_id}
              </text>
            </g>
          );
        })}
        {children}
      </svg>
    </div>
  );
}
