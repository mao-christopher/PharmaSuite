import React, { useEffect, useRef, useState } from 'react';
import { Dialog } from './ui';

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

/** Largest crop of an image with the target aspect, as fractions of the image. */
function maxCrop(imgW, imgH, aspect) {
  return aspect < imgW / imgH ? { w: (aspect * imgH) / imgW, h: 1 } : { w: 1, h: imgW / (aspect * imgH) };
}

/**
 * Crop a photo to the aspect ratio of the view's recordings so normalized regions land
 * on the same spots in the photo and in the video. Resolves with a File (or the original).
 */
export default function CropDialog({ file, target, onCancel, onDone }) {
  const [url] = useState(() => URL.createObjectURL(file));
  const [img, setImg] = useState(null);
  const [scale, setScale] = useState(1);
  const [pos, setPos] = useState(null);
  const [busy, setBusy] = useState(false);
  const frameRef = useRef(null);
  const drag = useRef(null);
  const aspect = target.width / target.height;

  useEffect(() => () => URL.revokeObjectURL(url), [url]);

  const base = img ? maxCrop(img.naturalWidth, img.naturalHeight, aspect) : { w: 1, h: 1 };
  const crop = { w: base.w * scale, h: base.h * scale };
  const at = pos || { x: (1 - crop.w) / 2, y: (1 - crop.h) / 2 };
  const box = { x: clamp(at.x, 0, 1 - crop.w), y: clamp(at.y, 0, 1 - crop.h), ...crop };

  const onPointerDown = (e) => {
    const r = frameRef.current.getBoundingClientRect();
    drag.current = { sx: e.clientX, sy: e.clientY, x: box.x, y: box.y, w: r.width, h: r.height };
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e) => {
    const d = drag.current;
    if (!d) return;
    setPos({ x: d.x + (e.clientX - d.sx) / d.w, y: d.y + (e.clientY - d.sy) / d.h });
  };
  const nudge = (e) => {
    const step = e.shiftKey ? 0.05 : 0.01;
    const moves = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
    if (!moves[e.key]) return;
    e.preventDefault();
    setPos({ x: box.x + moves[e.key][0], y: box.y + moves[e.key][1] });
  };

  const apply = async () => {
    setBusy(true);
    const sw = Math.round(box.w * img.naturalWidth);
    const sh = Math.round(box.h * img.naturalHeight);
    const canvas = document.createElement('canvas');
    canvas.width = sw;
    canvas.height = sh;
    canvas.getContext('2d').drawImage(img, Math.round(box.x * img.naturalWidth), Math.round(box.y * img.naturalHeight), sw, sh, 0, 0, sw, sh);
    const blob = await new Promise((res) => canvas.toBlob(res, 'image/jpeg', 0.92));
    onDone(new File([blob], file.name.replace(/\.\w+$/, '') + '-cropped.jpg', { type: 'image/jpeg' }));
  };

  return (
    <Dialog
      title="Crop photo to match the video"
      onClose={onCancel}
      width={640}
      footer={
        <>
          <button type="button" className="btn btn-ghost" onClick={() => onDone(file)}>
            Keep full photo
          </button>
          <button type="button" className="btn btn-primary" onClick={apply} disabled={!img || busy}>
            {busy ? 'Cropping…' : 'Crop and import'}
          </button>
        </>
      }
    >
      <div className="form">
        <p className="lead">
          Recordings in this view are {target.width}×{target.height}
          {target.label ? ` (${target.label})` : ''}. Frame the part of the photo the video shows, so regions stay in place
          between Setup and playback.
        </p>
        <div className="crop-frame" ref={frameRef}>
          <img src={url} alt="Photo to crop" onLoad={(e) => setImg(e.currentTarget)} draggable={false} />
          {img && (
            <div
              className="crop-box"
              role="slider"
              tabIndex={0}
              aria-label="Crop position. Arrow keys move it."
              aria-valuetext={`${Math.round(box.x * 100)}% from left, ${Math.round(box.y * 100)}% from top`}
              style={{ left: `${box.x * 100}%`, top: `${box.y * 100}%`, width: `${box.w * 100}%`, height: `${box.h * 100}%` }}
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={() => (drag.current = null)}
              onKeyDown={nudge}
            />
          )}
        </div>
        <label className="field">
          <span className="label">Crop size {Math.round(scale * 100)}%</span>
          <input
            type="range"
            name="crop-size"
            min="0.3"
            max="1"
            step="0.01"
            value={scale}
            onChange={(e) => setScale(Number(e.target.value))}
          />
        </label>
      </div>
    </Dialog>
  );
}
