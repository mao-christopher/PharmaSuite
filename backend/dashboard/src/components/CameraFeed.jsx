import React, { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  ArrowCounterClockwiseIcon, ArrowsInIcon, ArrowsOutIcon, FilmStripIcon, PauseIcon, PlayIcon, UploadSimpleIcon,
} from '@phosphor-icons/react';
import { useLive } from '../lib/live';
import { useDialogs } from '../lib/dialogs';
import { appliedState, formatMs, plural } from '../lib/format';
import { Badge, Card, EmptyState } from './ui';

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const SNAP = 0.015; // snap to a signal marker within 1.5% of the timeline

function Scrubber({ t, duration, events, onSeek }) {
  const ref = useRef(null);
  const [drag, setDrag] = useState(null);
  const [hover, setHover] = useState(null);

  const timeAt = (e) => {
    const r = ref.current.getBoundingClientRect();
    return clamp((e.clientX - r.left) / r.width, 0, 1) * duration;
  };
  const snap = (ms) => {
    const near = events.find((ev) => Math.abs(ev.media_time_ms - ms) <= SNAP * duration);
    return near ? near.media_time_ms : ms;
  };
  const markerNear = (ms) => events.find((ev) => Math.abs(ev.media_time_ms - ms) <= SNAP * duration);

  const onKeyDown = (e) => {
    const step = e.shiftKey ? 5000 : 1000;
    const moves = { ArrowLeft: -step, ArrowRight: step, PageDown: -10000, PageUp: 10000 };
    if (e.key in moves) onSeek(clamp(t + moves[e.key], 0, duration));
    else if (e.key === 'Home') onSeek(0);
    else if (e.key === 'End') onSeek(duration);
    else return;
    e.preventDefault();
    e.stopPropagation();
  };

  const shown = drag ?? t;
  const hoverMarker = hover != null ? markerNear(hover) : null;

  return (
    <div
      ref={ref}
      className={`scrubber ${drag != null ? 'dragging' : ''}`}
      role="slider"
      tabIndex={0}
      aria-label="Seek"
      aria-valuemin={0}
      aria-valuemax={duration}
      aria-valuenow={Math.round(shown)}
      aria-valuetext={`${formatMs(shown)} of ${formatMs(duration)}`}
      onKeyDown={onKeyDown}
      onPointerDown={(e) => {
        e.currentTarget.setPointerCapture(e.pointerId);
        setDrag(snap(timeAt(e)));
      }}
      onPointerMove={(e) => {
        const ms = timeAt(e);
        setHover(ms);
        if (drag != null) setDrag(snap(ms));
      }}
      onPointerUp={() => {
        if (drag != null) onSeek(drag);
        setDrag(null);
      }}
      onPointerLeave={() => setHover(null)}
    >
      <div className="scrubber-track">
        <div className="scrubber-fill" style={{ width: `${(shown / duration) * 100}%` }} />
      </div>
      {events.map((ev) => (
        <span
          key={ev.event_id}
          className={`marker marker-${ev.event_type} ${ev.processed ? 'done' : ''}`}
          style={{ left: `${(ev.media_time_ms / duration) * 100}%` }}
          aria-hidden="true"
        />
      ))}
      <span className="scrubber-thumb" style={{ left: `${(shown / duration) * 100}%` }} aria-hidden="true" />
      {hover != null && (
        <span className="scrubber-tip" style={{ left: `${(hover / duration) * 100}%` }} aria-hidden="true">
          {hoverMarker
            ? `${hoverMarker.event_type === 'pickup' ? 'Pickup' : 'Put-down'} ${formatMs(hoverMarker.media_time_ms)}${hoverMarker.processed ? ', applied' : ''}`
            : formatMs(hover)}
        </span>
      )}
    </div>
  );
}

export default function CameraFeed() {
  const { state, control, seek, applyRecording } = useLive();
  const { openUpload } = useDialogs();
  const playerRef = useRef(null);
  const [fullscreen, setFullscreen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const rec = state.recording;

  useEffect(() => {
    const onChange = () => setFullscreen(document.fullscreenElement === playerRef.current);
    document.addEventListener('fullscreenchange', onChange);
    return () => document.removeEventListener('fullscreenchange', onChange);
  }, []);

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
  const toggle = () => control(playing ? 'pause' : 'play');
  const skip = (ms) => seek(clamp(t + ms, 0, duration));
  const toggleFullscreen = () =>
    document.fullscreenElement ? document.exitFullscreen() : playerRef.current?.requestFullscreen?.();

  const onKeyDown = (e) => {
    if (e.target.closest('.scrubber') && e.key.startsWith('Arrow')) return;
    const key = e.key.toLowerCase();
    if (key === ' ' || key === 'k') toggle();
    else if (key === 'arrowleft') skip(-5000);
    else if (key === 'arrowright') skip(5000);
    else if (key === 'j') skip(-10000);
    else if (key === 'l') skip(10000);
    else if (key === 'f') toggleFullscreen();
    else if (key === '0' || key === 'home') seek(0);
    else return;
    e.preventDefault();
  };

  const applyNow = async () => {
    setBusy(true);
    setError(null);
    try {
      await applyRecording(rec.name);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const feedStyle = fullscreen
    ? { width: '100%', height: '100%' }
    : { aspectRatio: `${fw} / ${fh}`, width: `min(100%, calc((100dvh - 330px) * ${fw / fh}))` };

  return (
    <Card
      title={
        <>
          <span className="truncate">{rec.label}</span>
          <Badge tone={applied.tone}>{applied.label}</Badge>
        </>
      }
      subtitle={
        state.camera_selection
          ? `Switching between ${state.camera_selection.cameras} cameras. Showing ${state.camera_selection.label || state.camera_selection.camera_id} (${
              state.camera_selection.reliable_arm ? 'arm visible' : 'arm not clearly visible'
            }), view ${state.layout?.name || state.layout?.layout_id}`
          : `${state.has_video ? 'Video with pose skeleton' : 'Scripted wrist path, no video'}, view ${state.layout?.name || state.layout?.layout_id}`
      }
      className="area-player"
      flush
    >
      <div
        ref={playerRef}
        className={`player ${fullscreen ? 'is-fullscreen' : ''} ${playing ? 'is-playing' : ''}`}
        tabIndex={0}
        aria-label="Video player. Space or K plays and pauses, arrow keys skip 5 seconds, F toggles full screen."
        onKeyDown={onKeyDown}
      >
        <div className="player-stage" onClick={toggle}>
          <div className="feed" style={feedStyle}>
            <img
              key={state.scenario}
              src={`/api/video/feed?s=${encodeURIComponent(state.scenario || '')}`}
              alt={`Camera view of ${rec.label} with regions${state.has_video ? ' and the pose skeleton' : ''} drawn on it`}
              width={fw}
              height={fh}
              draggable={false}
            />
          </div>
          {!playing && (
            <button
              type="button"
              className="big-play"
              aria-label={t >= duration ? 'Replay' : 'Play'}
              onClick={(e) => {
                e.stopPropagation();
                toggle();
              }}
            >
              {t >= duration ? <ArrowCounterClockwiseIcon size={30} /> : <PlayIcon size={30} weight="fill" />}
            </button>
          )}
        </div>
        <div className="player-controls">
          <button type="button" className="ctl-btn" onClick={toggle} aria-label={playing ? 'Pause' : 'Play'} title={playing ? 'Pause (K)' : 'Play (K)'}>
            {playing ? <PauseIcon size={18} weight="fill" /> : <PlayIcon size={18} weight="fill" />}
          </button>
          <button type="button" className="ctl-btn" onClick={() => control('restart')} aria-label="Play from the beginning" title="Play from the beginning (0)">
            <ArrowCounterClockwiseIcon size={17} />
          </button>
          <span className="timeline-time">
            {formatMs(t)} / {formatMs(duration)}
          </span>
          <Scrubber t={t} duration={duration} events={state.events} onSeek={seek} />
          <span className="legend" aria-hidden="true">
            <span className="legend-item">
              <span className="legend-dot legend-pickup" /> Pickup
            </span>
            <span className="legend-item">
              <span className="legend-dot legend-release" /> Put-down
            </span>
          </span>
          <button
            type="button"
            className="ctl-btn"
            onClick={toggleFullscreen}
            aria-label={fullscreen ? 'Exit full screen' : 'Full screen'}
            title={fullscreen ? 'Exit full screen (F)' : 'Full screen (F)'}
          >
            {fullscreen ? <ArrowsInIcon size={17} /> : <ArrowsOutIcon size={17} />}
          </button>
        </div>
      </div>
      <div className="player-note" aria-live="polite">
        {remaining === 0 ? (
          <span>Its signals are already in inventory. Replaying or skipping only moves the video.</span>
        ) : (
          <>
            <span>
              {plural(remaining, 'signal')} will update inventory as the playhead reaches them, including when you skip past them.
            </span>
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
