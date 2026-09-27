"""Background Unity renders of recordings' re-enactments (M9), one at a time.

The Unity project allows one editor instance, so renders run in a single worker thread
and the rest wait in a queue. Each render is keyed on the timeline's `inputs_key`:
rendering the same inputs again reuses the finished result, and a render whose key no
longer matches the recording (for example after an employee correction) is stale.

Output, per recording and key, in `<recording>/renders/<key[:16]>/`:
- sim.mp4: the Unity re-enactment at the recording's fps, frame size and duration
- side_by_side.mp4: the real video (left) and the render (right), to check alignment
- manifest.json: input hashes, output hashes and the renderer's own report

The renderer is `simulation/tools/render.py --timeline`. It prints `PROGRESS <0..1>` lines
and writes camera.mp4 and render_report.json to its output folder.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional

import cv2
import numpy as np

RENDERS_DIR = "renders"
MANIFEST = "manifest.json"
SIM_VIDEO = "sim.mp4"
SIDE_VIDEO = "side_by_side.mp4"
SCHEMA = "render/1"
KEY_CHARS = 16

Runner = Callable[[Path, Path, Callable[[float], None], threading.Event], Path]


def _public(job: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in job.items() if not k.startswith("_")}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def render_dir(scenario_dir: Path, key: str) -> Path:
    return scenario_dir / RENDERS_DIR / key[:KEY_CHARS]


def finished(scenario_dir: Path, key: str) -> Optional[Dict[str, Any]]:
    """The finished render for these inputs, if there is one."""
    try:
        manifest = json.loads((render_dir(scenario_dir, key) / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return manifest if manifest.get("inputs_key") == key else None


def latest(scenario_dir: Path) -> Optional[Dict[str, Any]]:
    """The most recently finished render of a recording, whatever its inputs."""
    best = None
    for manifest_path in (scenario_dir / RENDERS_DIR).glob(f"*/{MANIFEST}"):
        if manifest_path.parent.name.endswith(".partial"):
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if best is None or manifest.get("rendered_at", "") > best.get("rendered_at", ""):
            best = manifest
    return best


def unity_runner(unity_path: str, simulation_dir: Path) -> Runner:
    """Run the simulation's render CLI in re-enactment mode."""

    def run(timeline_path: Path, out_dir: Path, progress: Callable[[float], None], cancel: threading.Event) -> Path:
        cmd = [sys.executable, str(simulation_dir / "tools" / "render.py"), "--unity", unity_path,
               "--timeline", str(timeline_path), "--output", str(out_dir)]
        log_path = out_dir.parent / f"{out_dir.name}.log"
        with log_path.open("w", encoding="utf-8") as log:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

            def watch():
                while proc.poll() is None:
                    if cancel.wait(0.5):
                        proc.terminate()
                        return

            threading.Thread(target=watch, daemon=True).start()
            tail: Deque[str] = deque(maxlen=20)
            for line in proc.stdout:
                log.write(line)
                tail.append(line.rstrip())
                if line.startswith("PROGRESS "):
                    try:
                        progress(float(line.split()[1]))
                    except (IndexError, ValueError):
                        pass
            code = proc.wait()
        if code != 0:
            raise RuntimeError(f"Unity render failed (exit {code}): " + " | ".join(tail)[-600:])
        video = out_dir / "camera.mp4"
        if not video.exists():
            raise RuntimeError("The renderer finished without writing camera.mp4.")
        return video

    return run


def side_by_side(real: Path, sim: Path, out: Path) -> int:
    """Real video on the left, render on the right, at the render's height. Returns frames."""
    a, b = cv2.VideoCapture(str(real)), cv2.VideoCapture(str(sim))
    fps = b.get(cv2.CAP_PROP_FPS) or 30
    writer = None
    frames = 0
    try:
        while True:
            ok_b, sim_frame = b.read()
            if not ok_b:
                break
            ok_a, real_frame = a.read()
            h, w = sim_frame.shape[:2]
            if ok_a:
                scale = h / real_frame.shape[0]
                real_frame = cv2.resize(real_frame, (max(1, round(real_frame.shape[1] * scale)), h))
            else:
                real_frame = np.zeros_like(sim_frame)
            frame = np.hstack([real_frame, sim_frame])
            if writer is None:
                writer = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"), fps, (frame.shape[1], h))
            writer.write(frame)
            frames += 1
    finally:
        a.release()
        b.release()
        if writer is not None:
            writer.release()
    return frames


class RenderQueue:
    """One render at a time; the rest wait in order. Jobs are keyed by recording name."""

    def __init__(self, unity_path: Optional[str] = None, simulation_dir: Optional[Path] = None,
                 runner: Optional[Runner] = None):
        self.unity_path = unity_path if unity_path is not None else os.getenv("UNITY_PATH")
        self.simulation_dir = simulation_dir or Path(
            os.getenv("SIMULATION_DIR") or Path(__file__).resolve().parents[4] / "simulation")
        self._runner = runner
        self.jobs: Dict[str, Dict[str, Any]] = {}
        self._queue: Deque[str] = deque()
        self._cancel: Dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self._worker: Optional[threading.Thread] = None

    # ---------------------------------------------------------------- availability

    def unavailable_reason(self) -> Optional[str]:
        if self._runner is not None:
            return None
        if not self.unity_path:
            return "Set UNITY_PATH on the server to a licensed Unity 6000.6.3f1 editor to render simulations."
        if not Path(self.unity_path).exists():
            return f"UNITY_PATH points to {self.unity_path}, which doesn't exist on the server."
        if not (self.simulation_dir / "tools" / "render.py").exists():
            return f"The simulation project isn't at {self.simulation_dir}."
        return None

    def runner(self) -> Runner:
        return self._runner or unity_runner(self.unity_path, self.simulation_dir)

    # ---------------------------------------------------------------- jobs

    def status(self, name: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self.jobs.get(name)
            if job is None:
                return None
            out = _public(job)
            if job["state"] == "queued":
                out["queue_position"] = list(self._queue).index(name) + 1 if name in self._queue else None
            return out

    def active(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [_public(j) for j in self.jobs.values() if j["state"] in ("queued", "running")]

    def enqueue(self, name: str, label: str, scenario_dir: Path, timeline: Dict[str, Any],
                real_video: Optional[Path]) -> Dict[str, Any]:
        """Queue a render of this timeline. Reuses a finished render of the same inputs."""
        key = timeline["inputs_key"]
        done = finished(scenario_dir, key)
        if done is not None:
            return {"state": "done", "reused": True, "manifest": done}
        reason = self.unavailable_reason()
        if reason:
            raise RuntimeError(reason)
        with self._lock:
            job = self.jobs.get(name)
            if job and job["state"] in ("queued", "running"):
                if job["inputs_key"] == key:
                    return _public(job)
                raise RuntimeError("A render of this recording is already running. Cancel it first.")
            self.jobs[name] = {
                "name": name, "label": label, "inputs_key": key, "state": "queued", "step": "Waiting",
                "progress": 0.0, "error": None, "queued_at": _now(), "_dir": str(scenario_dir),
                "_timeline": str(Path(scenario_dir) / "timeline.json"), "_real": str(real_video) if real_video else None,
                "_meta": {k: timeline[k] for k in ("fps", "frame_size", "duration_ms", "inputs_sha256")},
            }
            self._cancel[name] = threading.Event()
            self._queue.append(name)
            self._ensure_worker()
            self._wake.notify()
            return _public(self.jobs[name])

    def cancel(self, name: str) -> bool:
        with self._lock:
            job = self.jobs.get(name)
            if not job or job["state"] not in ("queued", "running"):
                return False
            if name in self._queue:
                self._queue.remove(name)
                job.update(state="cancelled", step="Cancelled")
            self._cancel[name].set()
            return True

    def wait_idle(self, timeout: float = 10.0) -> bool:
        """For tests: wait until nothing is queued or running."""
        import time

        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if not self.active():
                return True
            time.sleep(0.02)
        return False

    # ---------------------------------------------------------------- worker

    def _ensure_worker(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._loop, name="unity-render", daemon=True)
            self._worker.start()

    def _loop(self) -> None:
        while True:
            with self._lock:
                while not self._queue:
                    if not self._wake.wait(timeout=30):
                        self._worker = None
                        return
                name = self._queue.popleft()
                job = self.jobs[name]
                job.update(state="running", step="Rendering in Unity", started_at=_now())
                cancel = self._cancel[name]
            try:
                self._render(job, cancel)
            except Exception as e:  # surfaced through status
                with self._lock:
                    if cancel.is_set():
                        job.update(state="cancelled", step="Cancelled", error=None)
                    else:
                        job.update(state="failed", step="Failed", error=str(e))

    def _render(self, job: Dict[str, Any], cancel: threading.Event) -> None:
        scenario_dir = Path(job["_dir"])
        key = job["inputs_key"]
        final = render_dir(scenario_dir, key)
        work = final.with_name(final.name + ".partial")
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)

        def progress(p: float) -> None:
            job["progress"] = round(min(max(p, 0.0), 1.0) * 0.9, 3)

        video = self.runner()(Path(job["_timeline"]), work / "unity", progress, cancel)
        if cancel.is_set():
            raise RuntimeError("cancelled")
        job.update(step="Packaging", progress=0.9)
        shutil.move(str(video), work / SIM_VIDEO)
        report_path = video.parent / "render_report.json"
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else None
        cap = cv2.VideoCapture(str(work / SIM_VIDEO))
        sim_info = {"fps": cap.get(cv2.CAP_PROP_FPS), "frames": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
                    "frame_size": [int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))]}
        cap.release()
        files = {SIM_VIDEO: _sha256(work / SIM_VIDEO)}
        if job["_real"] and Path(job["_real"]).exists():
            side_by_side(Path(job["_real"]), work / SIM_VIDEO, work / SIDE_VIDEO)
            files[SIDE_VIDEO] = _sha256(work / SIDE_VIDEO)
        shutil.rmtree(work / "unity", ignore_errors=True)
        timeline_sha = _sha256(Path(job["_timeline"]))
        manifest = {
            "schema": SCHEMA, "recording": job["name"], "inputs_key": key,
            "timeline_sha256": timeline_sha, "timeline_inputs_sha256": job["_meta"]["inputs_sha256"],
            "rendered_at": _now(), "requested": {k: job["_meta"][k] for k in ("fps", "frame_size", "duration_ms")},
            "rendered": sim_info, "files": files, "render_report": report,
            "renderer": "custom" if self._runner else f"unity:{self.unity_path}",
        }
        (work / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        shutil.rmtree(final, ignore_errors=True)
        work.replace(final)
        with self._lock:
            job.update(state="done", step="Done", progress=1.0, finished_at=manifest["rendered_at"])
