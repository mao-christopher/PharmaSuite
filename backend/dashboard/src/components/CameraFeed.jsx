import React from 'react';
import { Pause, Play, RotateCcw } from 'lucide-react';
import { useLive } from '../lib/live';
import { formatMs } from '../lib/format';
import { Card } from './ui';

export default function CameraFeed() {
  const { state, control } = useLive();
  const { media_time_ms: t = 0, duration_ms: duration = 1, is_playing: playing } = state;
  const [fw, fh] = state.frame_size || [16, 9];
  const aspect = `${fw} / ${fh}`;

  return (
    <Card
      title="Camera"
      subtitle={`${state.has_video ? 'Video with pose skeleton' : 'Demo: scripted wrist, no video'} · layout ${state.layout?.layout_id} v${state.layout?.calibration_version}`}
      actions={
        <>
          <button className="btn" onClick={() => control(playing ? 'pause' : 'play')}>
            {playing ? <Pause size={15} /> : <Play size={15} />}
            {playing ? 'Pause' : 'Play'}
          </button>
          <button className="btn btn-ghost" onClick={() => control('restart')} aria-label="Restart">
            <RotateCcw size={15} />
          </button>
        </>
      }
      flush
    >
      <div className="feed" style={{ aspectRatio: aspect, width: `min(100%, calc((100vh - 280px) * ${fw / fh}))` }}>
        <img key={state.scenario} src={`/api/video/feed?s=${encodeURIComponent(state.scenario || '')}`} alt="Annotated camera feed" />
      </div>
      <div className="timeline">
        <div className="timeline-track timeline-events">
          <div className="timeline-fill" style={{ width: `${Math.min(100, (t / duration) * 100)}%` }} />
          {state.events.map((e) => (
            <span
              key={e.event_id}
              className={`marker marker-${e.event_type} ${e.processed ? 'done' : ''}`}
              style={{ left: `${Math.min(100, (e.media_time_ms / duration) * 100)}%` }}
              title={`${e.event_type === 'pickup' ? 'Pick up' : 'Put down'} at ${formatMs(e.media_time_ms)}`}
            />
          ))}
        </div>
        <span className="mono">
          {formatMs(t)} / {formatMs(duration)}
        </span>
      </div>
    </Card>
  );
}
