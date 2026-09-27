import React, { useEffect, useRef, useState } from 'react';
import { useLive } from '../lib/live';
import { errorMessage } from '../lib/api';
import { useDialogs } from '../lib/dialogs';
import { listPending, removePending, savePending } from '../lib/pendingEvents';
import { BandLink } from '../lib/bandLink';

const SERVICE = 'c47c5b10-9c49-4b73-9af1-3c0bcabdf001';
const EVENT = 'c47c5b11-9c49-4b73-9af1-3c0bcabdf001';
const decoder = new TextDecoder();
const LEASE_KEY = 'pharma-live-camera-owner';

export function parseBandPacket(value, expectedId) {
  const packet = JSON.parse(decoder.decode(value));
  if (packet.id !== expectedId || !['P', 'D'].includes(packet.e)) throw new Error('Unexpected wristband event');
  return packet;
}

export default function LiveCamera() {
  const { state, refresh } = useLive();
  const { openConfirm } = useDialogs();
  const video = useRef(null);
  const canvas = useRef(null);
  const stream = useRef(null);
  const cameraStarting = useRef(false);
  const cameraTrackListeners = useRef([]);
  const bandLink = useRef(null);
  const timer = useRef(null);
  const leaseTimer = useRef(null);
  const reconnectTimer = useRef(null);
  const mounted = useRef(true);
  const frames = useRef([]);
  const captureBusy = useRef(false);
  const uploadQueue = useRef(Promise.resolve());
  const captureId = useRef(crypto.randomUUID());
  const activeLayout = useRef(null);
  const wristRef = useRef('right');
  const [layoutId, setLayoutId] = useState('');
  const [wrist, setWrist] = useState('right');
  const [camera, setCamera] = useState(false);
  const [band, setBand] = useState('Disconnected');
  const [message, setMessage] = useState('Start the camera, then connect a wristband.');
  const [lastClip, setLastClip] = useState(null);
  const [pendingCount, setPendingCount] = useState(0);
  const views = state?.views || {};
  const liveActions = [...(state?.live_activity || [])].reverse();
  const chosenId = layoutId || state?.layout?.layout_id || '';
  activeLayout.current = views[chosenId];
  wristRef.current = wrist;

  const leaseRequest = (method, keepalive = false) => fetch('/api/live/lease', {
    method, keepalive, headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ capture_id: captureId.current }),
  });

  async function stopCamera() {
    clearInterval(timer.current);
    timer.current = null;
    cameraTrackListeners.current.forEach(([track, lost]) => {
      track.removeEventListener('mute', lost);
      track.removeEventListener('ended', lost);
    });
    cameraTrackListeners.current = [];
    const media = stream.current;
    stream.current = null;
    frames.current = [];
    media?.getTracks().forEach((track) => track.stop());
    clearInterval(leaseTimer.current);
    const lease = JSON.parse(localStorage.getItem(LEASE_KEY) || 'null');
    if (lease?.id === captureId.current) localStorage.removeItem(LEASE_KEY);
    if (video.current) video.current.srcObject = null;
    setCamera(false);
    if (media) await leaseRequest('DELETE').catch(() => {});
  }

  async function startCamera() {
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      setMessage('Camera access requires Chrome on localhost or HTTPS.');
      return;
    }
    if (cameraStarting.current) return;
    cameraStarting.current = true;
    try {
      const lease = JSON.parse(localStorage.getItem(LEASE_KEY) || 'null');
      if (lease && lease.id !== captureId.current && Date.now() - lease.at < 5000) {
        setMessage('Another Pharma tab is using the live camera. Stop it there first.');
        return;
      }
      await stopCamera();
      const media = await navigator.mediaDevices.getUserMedia({ audio: false, video: { width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 10 } } });
      stream.current = media;
      const lost = () => {
        if (stream.current !== media) return;
        frames.current = [];
        setMessage('Camera feed lost. Wristband events will need location confirmation until the camera restarts.');
        stopCamera().catch(() => {});
      };
      cameraTrackListeners.current = media.getVideoTracks().map((track) => {
        track.addEventListener('mute', lost);
        track.addEventListener('ended', lost);
        return [track, lost];
      });
      if (!cameraTrackListeners.current.length || media.getVideoTracks().some((track) => track.readyState !== 'live' || track.muted)) {
        throw new Error('Camera feed stopped');
      }
      video.current.srcObject = media;
      await video.current.play();
      if (stream.current !== media) return;
      const leaseResponse = await leaseRequest('POST');
      if (stream.current !== media) {
        await leaseRequest('DELETE').catch(() => {});
        return;
      }
      if (!leaseResponse.ok) throw new Error(await errorMessage(leaseResponse));
      setCamera(true);
      const renew = () => localStorage.setItem(LEASE_KEY, JSON.stringify({ id: captureId.current, at: Date.now() }));
      renew();
      leaseTimer.current = setInterval(() => {
        renew();
        leaseRequest('POST').then((response) => {
          if (!response.ok) setMessage('Live session lost its server lease. Stop and restart the camera.');
        }).catch(() => setMessage('Server connection lost; event uploads will remain pending.'));
      }, 3000);
      setMessage('Camera buffering. Wait five seconds before handling a bottle.');
      timer.current = setInterval(() => {
        if (captureBusy.current || !video.current?.videoWidth) return;
        captureBusy.current = true;
        const w = Math.min(video.current.videoWidth, 1280);
        const h = Math.round(w * video.current.videoHeight / video.current.videoWidth);
        const ctx = canvas.current.getContext('2d');
        canvas.current.width = w;
        canvas.current.height = h;
        ctx.drawImage(video.current, 0, 0, w, h);
        const at = performance.now();
        canvas.current.toBlob((blob) => {
          if (blob) {
            frames.current.push({ at, blob });
            frames.current = frames.current.filter((frame) => frame.at >= at - 5300);
          }
          captureBusy.current = false;
        }, 'image/jpeg', 0.65);
      }, 100);
    } catch (err) {
      await stopCamera();
      setMessage(`Camera unavailable: ${err.message}`);
    } finally {
      cameraStarting.current = false;
    }
  }

  async function uploadEvent(pending) {
    const form = new FormData();
    form.set('metadata', JSON.stringify(pending.metadata));
    pending.snapshot.forEach((frame, index) => form.append('frames', frame.blob, `frame-${index}.jpg`));
    const response = await fetch('/api/live/events', { method: 'POST', body: form });
    if (!response.ok) throw new Error(await errorMessage(response));
    const result = await response.json();
    await removePending(pending.event_id);
    setPendingCount((count) => Math.max(0, count - 1));
    setLastClip(result.clip_url);
    setMessage(`${pending.metadata.code === 'P' ? 'Pickup' : 'Put-down'}: ${result.status === 'duplicate' ? 'already saved' : result.status === 'reconciliation' ? 'sequence needs reconciliation' : result.region_id || 'location needs confirmation'}.`);
    await refresh();
  }

  async function retryPending() {
    const pending = await listPending();
    setPendingCount(pending.length);
    for (const item of pending) {
      uploadQueue.current = uploadQueue.current.catch(() => {}).then(() => uploadEvent(item));
      try { await uploadQueue.current; } catch (err) { setMessage(`Event ${item.event_id} still pending: ${err.message}`); }
    }
  }

  function onNotification(event, expectedId) {
    const notificationMs = performance.now();
    const notificationEpochMs = Date.now();
    try {
      const packet = parseBandPacket(event.target.value, expectedId);
      if (!activeLayout.current) throw new Error('Select a calibrated camera view first');
      const snapshot = frames.current.filter((frame) => frame.at >= notificationMs - 5000 && frame.at <= notificationMs);
      const eventId = crypto.randomUUID();
      const layout = activeLayout.current;
      const pending = { event_id: eventId, snapshot, metadata: {
        event_id: eventId, capture_id: captureId.current, code: packet.e, band_id: packet.id,
        wrist: wristRef.current, layout_id: layout.layout_id, calibration_version: layout.calibration_version,
        notification_ms: notificationMs, notification_epoch_ms: notificationEpochMs,
        frame_times_ms: snapshot.map((frame) => frame.at),
      } };
      savePending(pending).then(() => {
        setPendingCount((count) => count + 1);
        uploadQueue.current = uploadQueue.current.catch(() => {}).then(() => uploadEvent(pending));
        uploadQueue.current.catch((err) => setMessage(`Event ${eventId} is pending: ${err.message}`));
      }).catch((err) => setMessage(`Event ${eventId} could not be queued: ${err.message}`));
      setMessage(`${packet.e === 'P' ? 'Pickup' : 'Put-down'} received; analyzing the previous five seconds…`);
    } catch (err) {
      setMessage(`Wristband notification ignored: ${err.message}`);
    }
  }

  async function connect(selected) {
    if (!mounted.current || !bandLink.current) return;
    clearTimeout(reconnectTimer.current);
    if (await bandLink.current.connect(selected) && mounted.current &&
        bandLink.current?.selected === selected && selected.gatt.connected) {
      setBand(`Connected: ${selected.name}`);
    }
  }

  function scheduleReconnect(selected) {
    if (!mounted.current || bandLink.current?.selected !== selected) return;
    clearTimeout(reconnectTimer.current);
    reconnectTimer.current = setTimeout(() => {
      if (!mounted.current || bandLink.current?.selected !== selected) return;
      connect(selected).catch((err) => {
        setBand(`Waiting for ${selected.name}`);
        setMessage(`Reconnect pending: ${err.message}`);
        scheduleReconnect(selected);
      });
    }, 2000);
  }

  async function chooseBand() {
    if (!navigator.bluetooth) { setMessage('Web Bluetooth is unavailable. Use Chrome on localhost or HTTPS.'); return; }
    let selected;
    try {
      selected = await navigator.bluetooth.requestDevice({ filters: [{ namePrefix: 'Wristband-' }], optionalServices: [SERVICE] });
      await connect(selected);
    } catch (err) {
      setMessage(`Wristband connection failed: ${err.message}`);
      if (selected) scheduleReconnect(selected);
    }
  }

  useEffect(() => {
    mounted.current = true;
    bandLink.current = new BandLink({
      service: SERVICE, characteristic: EVENT, onNotification,
      onDisconnected: (selected) => {
        if (!mounted.current) return;
        setBand('Disconnected; reconnecting…');
        scheduleReconnect(selected);
      },
    });
    retryPending().catch((err) => setMessage(`Could not restore pending events: ${err.message}`));
    if (navigator.bluetooth?.getDevices) navigator.bluetooth.getDevices().then((devices) => {
      const previous = devices.find((item) => /^Wristband-[A-Za-z0-9]{2}$/.test(item.name || ''));
      if (previous) connect(previous).catch(() => {
        setBand('Previously authorized band is offline');
        scheduleReconnect(previous);
      });
    }).catch(() => {});
    return () => {
      mounted.current = false;
      clearInterval(timer.current);
      clearInterval(leaseTimer.current);
      clearTimeout(reconnectTimer.current);
      cameraTrackListeners.current.forEach(([track, lost]) => {
        track.removeEventListener('mute', lost);
        track.removeEventListener('ended', lost);
      });
      cameraTrackListeners.current = [];
      stream.current?.getTracks().forEach((track) => track.stop());
      if (stream.current) leaseRequest('DELETE', true).catch(() => {});
      const lease = JSON.parse(localStorage.getItem(LEASE_KEY) || 'null');
      if (lease?.id === captureId.current) localStorage.removeItem(LEASE_KEY);
      bandLink.current?.close();
      bandLink.current = null;
    };
  }, []);

  return <section className="card live-camera-page">
    <h1>Live camera and wristband</h1>
    <p className="muted">One technician and one bottle at a time. Select the camera view calibrated for this exact camera position.</p>
    <div className="live-controls">
      <label>Camera view <select className="input" value={chosenId} onChange={(event) => setLayoutId(event.target.value)}>{Object.values(views).map((view) => <option key={view.layout_id} value={view.layout_id}>{view.name}</option>)}</select></label>
      <label>Wrist wearing band <select className="input" value={wrist} onChange={(event) => setWrist(event.target.value)}><option value="right">Right</option><option value="left">Left</option></select></label>
      <button type="button" className="btn btn-primary" onClick={startCamera}>{camera ? 'Restart camera' : 'Start camera'}</button>
      <button type="button" className="btn" onClick={chooseBand}>Connect wristband</button>
      <button type="button" className="btn" onClick={stopCamera} disabled={!camera}>Stop camera</button>
      {pendingCount > 0 && <button type="button" className="btn" onClick={() => retryPending().catch((err) => setMessage(err.message))}>Retry {pendingCount} pending event(s)</button>}
    </div>
    <p role="status">{band}. {message}</p>
    <video ref={video} muted playsInline className="live-preview" aria-label="Live camera preview" />
    <canvas ref={canvas} hidden />
    {lastClip && <div><h2>Last action clip</h2><video src={lastClip} controls className="live-preview" /></div>}
    <h2>Captured actions</h2>
    {liveActions.length === 0 ? <p className="muted">No wristband events captured yet.</p> : <div className="table-wrap"><table className="table"><thead><tr><th>Action</th><th>Location</th><th>Result</th><th>Evidence</th></tr></thead><tbody>
      {liveActions.map((action) => {
        const detail = action.raw_event?.details || {};
        const alert = Object.values(state.alerts).find((item) => item.alert_type === 'uncertainty' && item.status === 'open' && item.metadata.session_id === action.session_id && item.metadata.phase === action.event_type);
        return <tr key={action.event_id}>
          <td>{action.event_type === 'pickup' ? 'Pickup' : 'Put-down'}</td>
          <td>{action.confirmed_region_id || action.nearest_region_id || 'Uncertain'}</td>
          <td>{action.state} {alert && <button type="button" className="btn btn-sm btn-primary" onClick={() => openConfirm(alert.alert_id)}>Confirm location</button>}</td>
          <td>{detail.clip ? <a href={`/api/live/clips/${detail.capture_id}/${action.event_id}`} target="_blank" rel="noreferrer"><img className="live-thumb" src={`/api/live/clips/${detail.capture_id}/${action.event_id}/thumbnail`} alt="Action camera snapshot" />View five-second clip</a> : 'No camera frames'}</td>
        </tr>;
      })}
    </tbody></table></div>}
  </section>;
}
