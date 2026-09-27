import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { errorMessage } from './api';
import { useLive } from './live';
import { BandLink } from './bandLink';
import { listPending, removePending, savePending } from './pendingEvents';
import { LIVE_REASONS, regionLabel } from './format';
import LiveSetupDialog from '../components/LiveSetupDialog';

/*
 * Live capture lives at the app level so it keeps running while the employee moves
 * between pages. The camera feed is only ever held in memory: a rolling buffer just
 * long enough to cut the clip around a band notification. Only those clips are uploaded.
 */

const SERVICE = 'c47c5b10-9c49-4b73-9af1-3c0bcabdf001';
const EVENT = 'c47c5b11-9c49-4b73-9af1-3c0bcabdf001';
// Must match backend/src/pharma/services/live_capture.py.
export const PRE_ROLL_MS = 4000;
export const POST_ROLL_MS = 1000;
const FRAME_INTERVAL_MS = 100;
const BUFFER_MS = PRE_ROLL_MS + POST_ROLL_MS + 500;
const MAX_FRAME_WIDTH = 1280;
const LEASE_KEY = 'pharma-live-camera-owner';
const PREFS_KEY = 'pharma-live-prefs';
const decoder = new TextDecoder();

export function parseBandPacket(value, expectedId) {
  const packet = JSON.parse(decoder.decode(value));
  if (packet.id !== expectedId || !['P', 'D'].includes(packet.e)) throw new Error('Unexpected wristband event');
  return packet;
}

function readJson(storage, key, fallback) {
  try {
    return JSON.parse(storage.getItem(key)) ?? fallback;
  } catch {
    return fallback;
  }
}

function writeJson(storage, key, value) {
  try {
    storage.setItem(key, JSON.stringify(value));
  } catch {
    // Private windows can refuse storage; preferences then last for this page only.
  }
}

/** ?dev=1 turns on the keyboard stand-in for the band for this browser tab (?dev=0 turns it off). */
function readDevMode() {
  const flag = new URLSearchParams(window.location.search).get('dev');
  if (flag !== null) writeJson(sessionStorage, 'pharma-dev', flag !== '0');
  return readJson(sessionStorage, 'pharma-dev', flag !== null && flag !== '0') === true;
}

const verbFor = (code) => (code === 'P' ? 'Pickup' : 'Put-down');
const IGNORED = {
  pickup_while_holding: 'a bottle is already in hand',
  release_without_pickup: 'no bottle is in hand',
};

const LiveCaptureContext = createContext(null);

export function LiveCaptureProvider({ children }) {
  const { state, refresh } = useLive();
  const [prefs, setPrefs] = useState(() => readJson(localStorage, PREFS_KEY, {}));
  const [devices, setDevices] = useState([]);
  const [activeDeviceId, setActiveDeviceId] = useState(null);
  const [camera, setCamera] = useState('off'); // off | starting | on
  const [stream, setStream] = useState(null);
  const [videoSize, setVideoSize] = useState(null);
  const [bufferReady, setBufferReady] = useState(false);
  const [band, setBand] = useState(() => ({ status: navigator.bluetooth ? 'disconnected' : 'unsupported', name: null }));
  const [status, setStatus] = useState(null); // { text, tone }
  const [analyzing, setAnalyzing] = useState(0);
  const [pendingCount, setPendingCount] = useState(0);
  const [lastEventAt, setLastEventAt] = useState(0);
  const [panel, setPanel] = useState('live'); // what the dashboard's main tile shows while live
  const [setupOpen, setSetupOpen] = useState(false);
  const devMode = useMemo(readDevMode, []);

  const stateRef = useRef(state);
  const streamRef = useRef(null);
  const captureVideo = useRef(null);
  const canvas = useRef(null);
  const frames = useRef([]);
  const timers = useRef({});
  const trackListeners = useRef([]);
  const captureBusy = useRef(false);
  const uploadQueue = useRef(Promise.resolve());
  const queued = useRef(new Set());
  const inflight = useRef(0);
  const lastDevCode = useRef(null);
  const captureId = useRef(crypto.randomUUID());
  const liveSessionId = useRef(null);
  const bandLink = useRef(null);
  const mounted = useRef(true);
  const selectionRef = useRef({});
  stateRef.current = state;

  const views = state?.views || {};
  const deviceKey = activeDeviceId || prefs.deviceId || 'default';
  const boundView = prefs.views?.[deviceKey];
  const layoutId = (boundView && views[boundView] ? boundView : null) || state?.layout?.layout_id || Object.keys(views)[0] || '';
  const view = views[layoutId] || null;
  const wrist = prefs.wrist === 'left' ? 'left' : 'right';
  selectionRef.current = { view, wrist };

  const updatePrefs = useCallback((patch) => {
    setPrefs((prev) => {
      const next = { ...prev, ...patch };
      writeJson(localStorage, PREFS_KEY, next);
      return next;
    });
  }, []);

  const setLayoutId = (id) => updatePrefs({ views: { ...(prefs.views || {}), [deviceKey]: id } });
  const setWrist = (value) => updatePrefs({ wrist: value });

  const refreshDevices = useCallback(async () => {
    if (!navigator.mediaDevices?.enumerateDevices) return;
    const all = await navigator.mediaDevices.enumerateDevices();
    const cams = all.filter((d) => d.kind === 'videoinput');
    // Labels stay empty until camera permission is granted.
    setDevices(cams.map((d, i) => ({ deviceId: d.deviceId, label: d.label || `Camera ${i + 1}` })));
  }, []);

  const leaseRequest = (method, keepalive = false) =>
    fetch('/api/live/lease', {
      method,
      keepalive,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ capture_id: captureId.current }),
    });

  const say = (text, tone = 'gray') => mounted.current && setStatus({ text, tone, at: Date.now() });

  // ------------------------------------------------------------------ camera

  const stopCamera = useCallback(async () => {
    Object.values(timers.current).forEach((t) => {
      clearInterval(t);
      clearTimeout(t);
    });
    timers.current = {};
    trackListeners.current.forEach(([track, lost]) => {
      track.removeEventListener('mute', lost);
      track.removeEventListener('ended', lost);
    });
    trackListeners.current = [];
    const media = streamRef.current;
    streamRef.current = null;
    frames.current = [];
    media?.getTracks().forEach((track) => track.stop());
    if (captureVideo.current) captureVideo.current.srcObject = null;
    const lease = readJson(localStorage, LEASE_KEY, null);
    if (lease?.id === captureId.current) localStorage.removeItem(LEASE_KEY);
    if (mounted.current) {
      setCamera('off');
      setStream(null);
      setBufferReady(false);
    }
    if (media) await leaseRequest('DELETE').catch(() => {});
  }, []);

  const startCamera = useCallback(async (deviceId = prefs.deviceId) => {
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      say('Camera access needs Chrome on localhost or HTTPS.', 'red');
      return false;
    }
    const lease = readJson(localStorage, LEASE_KEY, null);
    if (lease && lease.id !== captureId.current && Date.now() - lease.at < 5000) {
      say('Another Pharma tab is running the live camera. Stop it there first.', 'red');
      return false;
    }
    await stopCamera();
    setCamera('starting');
    try {
      const constraints = { width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 15 } };
      const media = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: deviceId ? { ...constraints, deviceId: { exact: deviceId } } : constraints,
      });
      streamRef.current = media;
      const lost = () => {
        if (streamRef.current !== media) return;
        say('Camera feed lost. Band events need location confirmation until the camera restarts.', 'red');
        stopCamera();
      };
      trackListeners.current = media.getVideoTracks().map((track) => {
        track.addEventListener('mute', lost);
        track.addEventListener('ended', lost);
        return [track, lost];
      });
      const [track] = media.getVideoTracks();
      if (!track || track.readyState !== 'live' || track.muted) throw new Error('Camera feed stopped');

      // A detached element feeds the buffer, so capture continues whatever page is open.
      const video = captureVideo.current || document.createElement('video');
      captureVideo.current = video;
      video.muted = true;
      video.playsInline = true;
      video.srcObject = media;
      await video.play();
      if (streamRef.current !== media) return false;

      const leaseResponse = await leaseRequest('POST');
      if (!leaseResponse.ok) throw new Error(await errorMessage(leaseResponse));
      if (streamRef.current !== media) {
        await leaseRequest('DELETE').catch(() => {});
        return false;
      }

      const used = track.getSettings().deviceId || deviceId || null;
      setActiveDeviceId(used);
      if (used) updatePrefs({ deviceId: used });
      refreshDevices().catch(() => {});
      liveSessionId.current = crypto.randomUUID();
      setVideoSize([video.videoWidth, video.videoHeight]);
      setStream(media);
      setCamera('on');
      setPanel('live');
      setBufferReady(false);
      say(`Buffering. Handle bottles after ${PRE_ROLL_MS / 1000} seconds.`);

      const renew = () => writeJson(localStorage, LEASE_KEY, { id: captureId.current, at: Date.now() });
      renew();
      timers.current.ready = setTimeout(() => {
        setBufferReady(true);
        say('Ready. Each band event saves the clip around it.', 'green');
      }, PRE_ROLL_MS);
      timers.current.lease = setInterval(() => {
        renew();
        leaseRequest('POST')
          .then((r) => !r.ok && say('Live session lost its server lease. Stop and restart the camera.', 'red'))
          .catch(() => say('Server connection lost; band events stay queued in this browser.', 'amber'));
      }, 3000);
      canvas.current = canvas.current || document.createElement('canvas');
      timers.current.capture = setInterval(() => {
        if (captureBusy.current || !video.videoWidth) return;
        captureBusy.current = true;
        const w = Math.min(video.videoWidth, MAX_FRAME_WIDTH);
        const h = Math.round((w * video.videoHeight) / video.videoWidth);
        const c = canvas.current;
        c.width = w;
        c.height = h;
        c.getContext('2d').drawImage(video, 0, 0, w, h);
        const at = performance.now();
        c.toBlob(
          (blob) => {
            if (blob) {
              frames.current.push({ at, blob });
              frames.current = frames.current.filter((f) => f.at >= at - BUFFER_MS);
            }
            captureBusy.current = false;
          },
          'image/jpeg',
          0.7,
        );
      }, FRAME_INTERVAL_MS);
      return true;
    } catch (err) {
      await stopCamera();
      say(`Camera unavailable: ${err.message}`, 'red');
      return false;
    }
  }, [prefs.deviceId, stopCamera, refreshDevices, updatePrefs]);

  const setDeviceId = (deviceId) => {
    updatePrefs({ deviceId });
    setActiveDeviceId(deviceId);
    if (streamRef.current) startCamera(deviceId);
  };

  // ------------------------------------------------------------------ band events

  const describe = (result, code) => {
    const verb = verbFor(code);
    const current = stateRef.current;
    const layout = current ? { ...(current.views?.[current.layout?.layout_id] || current.layout), medications: current.layout?.medications } : null;
    switch (result.status) {
      case 'applied':
        return [`${verb} at ${regionLabel(layout, result.region_id)}.`, 'green'];
      case 'needs_confirmation':
        return [`${verb} saved. ${LIVE_REASONS[result.evidence?.reason] || 'Location uncertain'}; confirm where it happened.`, 'amber'];
      case 'ignored':
        return [`${verb} ignored: ${IGNORED[result.reason] || result.reason} (treated as a false detection).`, 'gray'];
      default:
        return [`${verb} was already saved.`, 'gray'];
    }
  };

  const upload = async (pending) => {
    const form = new FormData();
    form.set('metadata', JSON.stringify(pending.metadata));
    pending.snapshot.forEach((frame, i) => form.append('frames', frame.blob, `frame-${i}.jpg`));
    const response = await fetch('/api/live/events', { method: 'POST', body: form });
    if (!response.ok) throw new Error(await errorMessage(response));
    const result = await response.json();
    await removePending(pending.event_id);
    queued.current.delete(pending.event_id);
    if (mounted.current) setPendingCount((n) => Math.max(0, n - 1));
    const [text, tone] = describe(result, pending.metadata.code);
    say(text, tone);
    await refresh();
    return result;
  };

  const enqueue = (pending, fresh = false) => {
    if (queued.current.has(pending.event_id)) return;
    queued.current.add(pending.event_id);
    if (!fresh) inflight.current += 1;
    setAnalyzing(inflight.current);
    const task = uploadQueue.current.catch(() => {}).then(() => upload(pending));
    uploadQueue.current = task;
    task
      .catch((err) => {
        queued.current.delete(pending.event_id);
        say(`${verbFor(pending.metadata.code)} is queued in this browser: ${err.message}`, 'amber');
      })
      .finally(() => {
        inflight.current = Math.max(0, inflight.current - 1);
        if (mounted.current) setAnalyzing(inflight.current);
      });
  };

  const retryPending = useCallback(async () => {
    const pending = await listPending();
    if (mounted.current) setPendingCount(pending.length);
    pending.forEach((item) => enqueue(item));
  }, []);

  /** One band notification (or its keyboard stand-in): cut the clip around it and queue the upload. */
  const handleEvent = (code, source, bandId) => {
    const notificationMs = performance.now();
    const notificationEpochMs = Date.now();
    const { view: layout, wrist: side } = selectionRef.current;
    if (!layout) {
      say('Choose a camera view before handling bottles.', 'red');
      return;
    }
    const eventId = crypto.randomUUID();
    inflight.current += 1;
    setAnalyzing(inflight.current);
    setLastEventAt(Date.now());
    const camOn = Boolean(streamRef.current);
    say(camOn ? `${verbFor(code)} detected. Saving the clip…` : `${verbFor(code)} detected with the camera off; it will need confirmation.`, camOn ? 'blue' : 'amber');

    const cut = () => {
      const snapshot = frames.current.filter((f) => f.at >= notificationMs - PRE_ROLL_MS && f.at <= notificationMs + POST_ROLL_MS);
      const pending = {
        event_id: eventId,
        snapshot,
        metadata: {
          event_id: eventId,
          capture_id: captureId.current,
          live_session_id: liveSessionId.current || captureId.current,
          code,
          band_id: bandId,
          source,
          wrist: side,
          layout_id: layout.layout_id,
          calibration_version: layout.calibration_version,
          notification_ms: notificationMs,
          notification_epoch_ms: notificationEpochMs,
          frame_times_ms: snapshot.map((f) => f.at),
        },
      };
      savePending(pending)
        .then(() => {
          setPendingCount((n) => n + 1);
          enqueue(pending, true);
          if (camOn) say(`${verbFor(code)} detected. Analyzing the clip…`, 'blue');
        })
        .catch((err) => {
          inflight.current = Math.max(0, inflight.current - 1);
          setAnalyzing(inflight.current);
          say(`${verbFor(code)} could not be queued: ${err.message}`, 'red');
        });
    };
    // Keep recording for the post-roll before cutting the clip.
    if (camOn) setTimeout(cut, POST_ROLL_MS + 150);
    else cut();
  };
  const handleEventRef = useRef(handleEvent);
  handleEventRef.current = handleEvent;

  // ------------------------------------------------------------------ wristband

  const connect = async (selected) => {
    if (!mounted.current || !bandLink.current) return;
    clearTimeout(timers.current.reconnect);
    setBand({ status: 'connecting', name: selected.name });
    if ((await bandLink.current.connect(selected)) && mounted.current && bandLink.current?.selected === selected && selected.gatt.connected) {
      setBand({ status: 'connected', name: selected.name });
    }
  };

  const scheduleReconnect = (selected) => {
    if (!mounted.current || bandLink.current?.selected !== selected) return;
    clearTimeout(timers.current.reconnect);
    setBand({ status: 'reconnecting', name: selected.name });
    timers.current.reconnect = setTimeout(() => {
      if (!mounted.current || bandLink.current?.selected !== selected) return;
      connect(selected).catch(() => scheduleReconnect(selected));
    }, 2000);
  };

  const connectBand = async () => {
    if (!navigator.bluetooth) {
      say('Web Bluetooth is unavailable. Use Chrome on localhost or HTTPS.', 'red');
      return;
    }
    let selected;
    try {
      selected = await navigator.bluetooth.requestDevice({ filters: [{ namePrefix: 'Wristband-' }], optionalServices: [SERVICE] });
      await connect(selected);
    } catch (err) {
      if (err.name !== 'NotFoundError') say(`Wristband connection failed: ${err.message}`, 'red');
      if (selected) scheduleReconnect(selected);
      else setBand((b) => (b.status === 'connecting' ? { status: 'disconnected', name: null } : b));
    }
  };

  useEffect(() => {
    mounted.current = true;
    bandLink.current = new BandLink({
      service: SERVICE,
      characteristic: EVENT,
      onNotification: (event, expectedId) => {
        try {
          const packet = parseBandPacket(event.target.value, expectedId);
          handleEventRef.current(packet.e, 'band', packet.id);
        } catch (err) {
          say(`Wristband notification ignored: ${err.message}`, 'amber');
        }
      },
      onDisconnected: (selected) => mounted.current && scheduleReconnect(selected),
    });
    retryPending().catch((err) => say(`Could not restore queued events: ${err.message}`, 'red'));
    refreshDevices().catch(() => {});
    const onDeviceChange = () => refreshDevices().catch(() => {});
    navigator.mediaDevices?.addEventListener?.('devicechange', onDeviceChange);
    // Reconnect to a band this browser already authorized; the first time needs the picker.
    navigator.bluetooth?.getDevices?.()
      .then((found) => {
        const previous = found.find((d) => /^Wristband-[A-Za-z0-9]{2}$/.test(d.name || ''));
        if (previous) connect(previous).catch(() => scheduleReconnect(previous));
      })
      .catch(() => {});
    const release = () => streamRef.current && leaseRequest('DELETE', true).catch(() => {});
    window.addEventListener('pagehide', release);
    return () => {
      mounted.current = false;
      window.removeEventListener('pagehide', release);
      navigator.mediaDevices?.removeEventListener?.('devicechange', onDeviceChange);
      release();
      stopCamera();
      clearTimeout(timers.current.reconnect);
      bandLink.current?.close();
      bandLink.current = null;
    };
  }, []);

  // ------------------------------------------------------------------ dev keyboard stand-in

  useEffect(() => {
    if (!devMode || camera !== 'on') return undefined;
    const onKey = (e) => {
      if (e.code !== 'Space' || e.repeat || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.target.closest?.('input, textarea, select, [contenteditable="true"], .player, .dialog')) return;
      e.preventDefault();
      if (document.activeElement?.tagName === 'BUTTON') document.activeElement.blur();
      // While earlier events are still being analyzed, alternate from the last key press.
      const held = Boolean(stateRef.current?.live?.held_movement_id);
      const code = inflight.current > 0 ? (lastDevCode.current === 'P' ? 'D' : 'P') : held ? 'D' : 'P';
      lastDevCode.current = code;
      handleEventRef.current(code, 'dev', 'dev');
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [devMode, camera]);

  const value = {
    devices,
    deviceId: activeDeviceId || prefs.deviceId || '',
    setDeviceId,
    refreshDevices,
    layoutId,
    view,
    setLayoutId,
    wrist,
    setWrist,
    camera,
    stream,
    videoSize,
    bufferReady,
    startCamera,
    stopCamera,
    band,
    connectBand,
    status,
    analyzing,
    pendingCount,
    retryPending,
    lastEventAt,
    panel,
    setPanel,
    devMode,
    openSetup: () => setSetupOpen(true),
    supported: {
      camera: Boolean(window.isSecureContext && navigator.mediaDevices?.getUserMedia),
      bluetooth: Boolean(navigator.bluetooth),
    },
  };

  return (
    <LiveCaptureContext.Provider value={value}>
      {children}
      {setupOpen && state && <LiveSetupDialog onClose={() => setSetupOpen(false)} />}
    </LiveCaptureContext.Provider>
  );
}

export function useLiveCapture() {
  return useContext(LiveCaptureContext);
}
