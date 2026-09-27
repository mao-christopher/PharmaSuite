import React from 'react';
import { VideoCameraIcon } from '@phosphor-icons/react';
import { useLiveCapture } from '../lib/liveCapture';

/** Top-bar entry point: "Go live" when the camera is off, the live status pill when it's on. */
export default function LiveButton() {
  const capture = useLiveCapture();
  if (capture.camera === 'off') {
    return (
      <button type="button" className="btn btn-primary btn-sm" onClick={capture.openSetup}>
        <VideoCameraIcon size={14} aria-hidden="true" /> Go live
      </button>
    );
  }
  const band = capture.band.status === 'connected' ? capture.band.name?.replace('Wristband-', 'Band ') : 'No band';
  const detail = capture.analyzing > 0 ? 'Analyzing…' : capture.camera === 'starting' ? 'Starting…' : band;
  return (
    <button
      type="button"
      className={`live-pill ${capture.band.status === 'connected' || capture.devMode ? '' : 'is-warn'}`}
      onClick={capture.openSetup}
      title="Live capture settings"
    >
      <span className="live-dot" aria-hidden="true" />
      Live
      <span className="live-pill-detail">{detail}</span>
    </button>
  );
}
