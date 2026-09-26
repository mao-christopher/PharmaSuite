import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowCounterClockwiseIcon, FilmStripIcon, PauseIcon, PlayIcon, UploadSimpleIcon } from '@phosphor-icons/react';
import { useLive } from '../lib/live';
import { useDialogs } from '../lib/dialogs';
import { appliedState, formatMs, plural } from '../lib/format';
import { Badge, Card, EmptyState } from './ui';

export default function CameraFeed() {
  const { state, control, applyRecording } = useLive();
  const { openUpload } = useDialogs();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const rec = state.recording;

  if (!rec) {
    return (
      <Card title="Player" className="area-player">
        <EmptyState
          icon={FilmStripIcon}
          title="No recording in the player"
          actions={
            <>
              <button type="button" className="btn btn-primary" onClick={openUpload}>
                <UploadSimpleIcon size={14} aria-hidden="true" /> Upload recording
              </button>
              <Link className="btn" to="/recordings">
                Browse recordings
              </Link>
            </>
          }
        >
          Upload a clip with its pickup and put-down timestamps, or open a stored one from Recordings.
        </EmptyState>
      </Card>
    );
  }

  const { media_time_ms: t = 0, duration_ms: duration = 1, is_playing: playing } = state;
  const [fw, fh] = state.frame_size || [16, 9];
  const applied = appliedState(rec);
  const remaining = rec.events_total - rec.events_applied;

  const applyNow = async () => {
    setBusy(true);
    setError(null);
    try {
      await applyRecording(rec.name);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card
      title={
        <>
          <span className="truncate">{rec.label}</span>
          <Badge tone={applied.tone}>{applied.label}</Badge>
        </>
      }
      subtitle={state.has_video ? 'Video with pose skeleton' : 'Scripted wrist path, no video'}
      className="area-player"
      actions={
        <>
          <button
            type="button"
            className="icon-btn bordered"
            onClick={() => control('restart')}
            aria-label="Play from the beginning"
            title="Play from the beginning"
          >
            <ArrowCounterClockwiseIcon aria-hidden="true" />
          </button>
          <button type="button" className="btn btn-primary" onClick={() => control(playing ? 'pause' : 'play')}>
            {playing ? <PauseIcon size={14} aria-hidden="true" /> : <PlayIcon size={14} aria-hidden="true" />}
            {playing ? 'Pause' : 'Play'}
          </button>
        </>
      }
      flush
    >
      <div className="feed" style={{ aspectRatio: `${fw} / ${fh}`, width: `min(100%, calc((100dvh - 320px) * ${fw / fh}))` }}>
        <img
          key={state.scenario}
          src={`/api/video/feed?s=${encodeURIComponent(state.scenario || '')}`}
          alt={`Camera view of ${rec.label} with regions${state.has_video ? ' and the pose skeleton' : ''} drawn on it`}
          width={fw}
          height={fh}
        />
      </div>
      <div className="timeline">
        <div className="timeline-track timeline-events" role="progressbar" aria-label="Playback position" aria-valuemin={0} aria-valuemax={duration} aria-valuenow={t}>
          <div className="timeline-fill" style={{ width: `${Math.min(100, (t / duration) * 100)}%` }} />
          {state.events.map((e) => (
            <span
              key={e.event_id}
              className={`marker marker-${e.event_type} ${e.processed ? 'done' : ''}`}
              style={{ left: `${Math.min(100, (e.media_time_ms / duration) * 100)}%` }}
              title={`${e.event_type === 'pickup' ? 'Pickup' : 'Put-down'} at ${formatMs(e.media_time_ms)}${e.processed ? ', applied' : ''}`}
            />
          ))}
        </div>
        <span className="timeline-time">
          {formatMs(t)} / {formatMs(duration)}
        </span>
        <span className="legend" aria-hidden="true">
          <span className="legend-item">
            <span className="legend-dot legend-pickup" /> Pickup
          </span>
          <span className="legend-item">
            <span className="legend-dot legend-release" /> Put-down
          </span>
        </span>
      </div>
      <div className="player-note" aria-live="polite">
        {remaining === 0 ? (
          <span>Its signals are already in inventory. Replaying only shows the video.</span>
        ) : (
          <>
            <span>{plural(remaining, 'signal')} will update inventory as the playhead reaches them.</span>
            <button type="button" className="link-btn push" onClick={applyNow} disabled={busy}>
              {busy ? 'Applying…' : 'Apply without playing'}
            </button>
          </>
        )}
        {error && <span className="form-error">{error}</span>}
      </div>
    </Card>
  );
}
