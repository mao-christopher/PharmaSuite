import React from 'react';
import { Link } from 'react-router-dom';
import { ArrowClockwiseIcon, BluetoothIcon, ShieldCheckIcon, VideoCameraIcon } from '@phosphor-icons/react';
import { POST_ROLL_MS, PRE_ROLL_MS, useLiveCapture } from '../lib/liveCapture';
import { useLive } from '../lib/live';
import { Badge, Dialog } from './ui';

const BAND_STATUS = {
  connected: ['Connected', 'green'],
  connecting: ['Connecting…', 'blue'],
  reconnecting: ['Reconnecting…', 'amber'],
  disconnected: ['Not connected', 'gray'],
  unsupported: ['Needs Chrome', 'red'],
};

/** Camera device, its registered view, wearing wrist and wristband, all in one place. */
export default function LiveSetupDialog({ onClose }) {
  const { state } = useLive();
  const capture = useLiveCapture();
  const views = Object.values(state.views || {});
  const on = capture.camera === 'on';
  const [bandLabel, bandTone] = BAND_STATUS[capture.band.status] || BAND_STATUS.disconnected;
  const unlabeled = capture.devices.length > 0 && capture.devices.every((d) => /^Camera \d+$/.test(d.label));
  const hasRegions = (capture.view?.regions || []).length > 0;

  return (
    <Dialog
      title="Live capture"
      onClose={onClose}
      width={520}
      footer={
        <>
          {on ? (
            <button type="button" className="btn btn-ghost" onClick={() => capture.stopCamera()}>
              Stop camera
            </button>
          ) : (
            <button type="button" className="btn btn-ghost" onClick={onClose}>
              Close
            </button>
          )}
          {on ? (
            <button type="button" className="btn btn-primary" onClick={onClose}>
              Done
            </button>
          ) : (
            <button
              type="button"
              className="btn btn-primary"
              disabled={!capture.supported.camera || !capture.view || capture.camera === 'starting'}
              onClick={async () => {
                if (await capture.startCamera()) onClose();
              }}
            >
              <VideoCameraIcon size={14} aria-hidden="true" /> {capture.camera === 'starting' ? 'Starting…' : 'Start camera'}
            </button>
          )}
        </>
      }
    >
      <div className="form">
        {!capture.supported.camera && (
          <p className="banner banner-red">Camera access needs Chrome on localhost or an HTTPS address.</p>
        )}
        {capture.status?.tone === 'red' && (
          <p className="banner banner-red" role="alert">
            {capture.status.text}
          </p>
        )}
        <div className="field">
          <span className="label" id="live-camera-label">Camera</span>
          <div className="inline-controls">
            <select
              className="input"
              aria-labelledby="live-camera-label"
              value={capture.deviceId}
              onChange={(e) => capture.setDeviceId(e.target.value)}
            >
              {!capture.deviceId && <option value="">Default camera</option>}
              {capture.devices.map((d) => (
                <option key={d.deviceId} value={d.deviceId}>
                  {d.label}
                </option>
              ))}
            </select>
            <button type="button" className="icon-btn bordered" onClick={() => capture.refreshDevices()} aria-label="Look for cameras again" title="Look for cameras again">
              <ArrowClockwiseIcon aria-hidden="true" />
            </button>
          </div>
          <span className="hint">
            {unlabeled
              ? 'Camera names appear after you allow camera access. Start the camera once, then pick it here.'
              : 'To use your iPhone, bring it near this Mac with Continuity Camera on; it appears in this list.'}
          </span>
        </div>

        <label className="field">
          <span className="label">Camera view</span>
          <select className="input" value={capture.layoutId} onChange={(e) => capture.setLayoutId(e.target.value)}>
            {views.map((v) => (
              <option key={v.layout_id} value={v.layout_id}>
                {v.name} ({v.regions.length} regions, calibration v{v.calibration_version})
              </option>
            ))}
          </select>
          <span className="hint">
            Remembered for this camera. Register the camera's position on the <Link className="link" to="/room" onClick={onClose}>Room page</Link> so its regions match.
          </span>
          {capture.view && !hasRegions && <span className="form-error">This view has no regions yet, so every event will need confirmation.</span>}
        </label>

        <div className="field">
          <span className="label" id="live-wrist-label">Wrist wearing the band</span>
          <div className="segmented" role="group" aria-labelledby="live-wrist-label">
            {['left', 'right'].map((side) => (
              <button key={side} type="button" className={capture.wrist === side ? 'active' : ''} aria-pressed={capture.wrist === side} onClick={() => capture.setWrist(side)}>
                {side === 'left' ? 'Left' : 'Right'}
              </button>
            ))}
          </div>
        </div>

        <div className="field">
          <span className="label">Wristband</span>
          <div className="setup-row">
            <span className="setup-row-icon" aria-hidden="true">
              <BluetoothIcon />
            </span>
            <div className="choice-main">
              <div className="row-title">{capture.band.name || 'Wristband-XX'}</div>
              <div className="row-sub">Chrome remembers the band after the first connection and reconnects automatically.</div>
            </div>
            <Badge tone={bandTone}>{bandLabel}</Badge>
            {capture.band.status !== 'connected' && capture.supported.bluetooth && (
              <button type="button" className="btn btn-sm" onClick={() => capture.connectBand()}>
                Connect
              </button>
            )}
          </div>
        </div>

        <p className="setup-note">
          <ShieldCheckIcon size={16} aria-hidden="true" />
          <span>
            The feed stays in this browser's memory. Only the {(PRE_ROLL_MS + POST_ROLL_MS) / 1000} seconds around each band event
            are saved and analyzed.
          </span>
        </p>
        {capture.devMode && (
          <p className="hint">
            Developer mode: while the camera runs, <kbd>Space</kbd> sends a pickup, then a put-down, through the same path as the band.
          </p>
        )}
      </div>
    </Dialog>
  );
}
