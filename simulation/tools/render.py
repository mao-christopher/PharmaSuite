"""Run Unity offline export; preserves real rendering (do not add -nographics).

Demo mode renders the scripted pharmacy recording. `--timeline` renders a dashboard
recording's re-enactment (M7 scene from the rebuilt room, M8 floor-track player) and
writes camera.mp4 and render_report.json, printing `PROGRESS <0..1>` lines as it goes.
"""
import argparse
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time

TOOLS = Path(__file__).resolve().parent
PROJECT = TOOLS.parents[0]


def progress(value):
    print(f"PROGRESS {min(max(value, 0.0), 1.0):.3f}", flush=True)


def fail(message):
    print(f"ERROR {message}", file=sys.stderr, flush=True)
    raise SystemExit(message)


def run_unity(command, log_path, on_line):
    """Run Unity to completion, calling on_line for each new log line as it's written."""
    proc = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    position = 0
    tail = []

    def read():
        nonlocal position
        if not log_path.exists():
            return
        with log_path.open("r", encoding="utf-8", errors="replace") as log:
            log.seek(position)
            chunk = log.read()
            position = log.tell()
        for line in chunk.splitlines():
            tail.append(line)
            del tail[:-40]
            on_line(line)

    try:
        while proc.poll() is None:
            read()
            time.sleep(0.5)
    except BaseException:
        proc.terminate()
        raise
    read()
    return proc.returncode, tail


def encode(frames_dir, fps, output):
    import imageio_ffmpeg

    rate = str(int(fps)) if float(fps).is_integer() else repr(float(fps))
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-framerate", rate,
                    "-i", str(frames_dir / "%06d.png"), "-c:v", "libx264", "-crf", "18",
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output)], check=True)
    return imageio_ffmpeg.count_frames_and_secs(str(output))[0]


def alignment_stats(points):
    """Pixel distance between the Unity camera and the registration's own projection."""
    dist = [((p["unity_pixel"][0] - p["registration_pixel"][0]) ** 2 +
             (p["unity_pixel"][1] - p["registration_pixel"][1]) ** 2) ** 0.5
            for p in points if p.get("depth", 0) > 0]
    if not dist:
        return {"points": 0, "median_px": None, "max_px": None}
    return {"points": len(dist), "median_px": round(statistics.median(dist), 4), "max_px": round(max(dist), 4)}


def preview_frames(args, plan):
    """Frame indices a preview renders (None renders everything): the first --frames frames,
    plus a few around each --at-ms time (reach start, contact, retract)."""
    if not args.frames and not args.at_ms:
        return None
    fps, n = plan["fps"], plan["frame_count"]
    wanted = set(range(min(n, args.frames or 0)))
    for ms in args.at_ms or []:
        for offset in (-0.6, -0.3, 0.0, 0.3, 0.8):
            wanted.add(min(n - 1, max(0, round((ms / 1000 + offset) * fps))))
    return sorted(wanted)


def render_timeline(args, base):
    sys.path.insert(0, str(TOOLS))
    from reenact_plan import PlanError, build_plan, load_timeline, unity_json

    output = args.output.resolve()
    try:
        timeline = load_timeline(args.timeline)
        plan = build_plan(timeline)
    except (OSError, ValueError, KeyError, PlanError) as e:
        fail(f"Can't re-enact {args.timeline}: {e}")
    output.mkdir(parents=True, exist_ok=True)
    plan_path = output / "reenactment_plan.json"
    plan_path.write_text(unity_json(plan), encoding="utf-8")
    progress(0.02)
    log_path = output / "unity.log"
    command = base + ["-executeMethod", "Pharma.Simulation.Editor.ReenactmentExporter.Export",
                      "-pharmaPlan", str(plan_path), "-pharmaOutput", str(output), "-logFile", str(log_path)]

    def on_line(line):
        if line.startswith("PHARMA_PROGRESS"):
            done, total = (int(v) for v in line.split()[1:3])
            progress(0.05 + 0.85 * done / total)

    preview = preview_frames(args, plan)
    if preview is not None:
        command += ["-pharmaFrames", ",".join(str(f) for f in preview)]
    code, tail = run_unity(command, log_path, on_line)
    report_path = output / "unity_report.json"
    if code != 0 or not report_path.exists():
        errors = [line for line in tail if "Exception" in line or "error" in line.lower() or "another Unity" in line]
        fail(f"Unity re-enactment failed (exit {code}). See {log_path}. " + " | ".join((errors or tail)[-4:]))
    unity = json.loads(report_path.read_text(encoding="utf-8"))
    frames_dir = output / "frames"
    progress(0.92)
    if preview is None:
        encoded = encode(frames_dir, plan["fps"], output / "camera.mp4")
        if encoded != plan["frame_count"]:
            fail(f"camera.mp4 has {encoded} frames; the timeline has {plan['frame_count']}.")
    else:
        encoded = len(list(frames_dir.glob("*.png")))
    pr = plan["report"]
    report = {
        "schema": "render-report/1",
        "recording": plan.get("recording"),
        "timeline_inputs_sha256": plan.get("inputs_sha256"),
        "unity_version": unity["unity_version"],
        "fps": plan["fps"], "frame_size": [plan["width"], plan["height"]], "frames": encoded,
        "preview_frames": preview,
        "camera_layout_id": plan["camera"]["layout_id"],
        "alignment": alignment_stats(unity.get("alignment", [])),
        "out_of_view_frames": pr["out_of_view_frames"],
        "technician_hidden_frames": unity["technician_hidden_frames"],
        "pushed_out_of_solid_frames": pr["pushed_frames"],
        "cuts": pr["cuts"], "blends": pr["blends"],
        "guard_holds": unity["guard_holds"],
        "actions": {
            "total": len(plan["actions"]),
            "shown": unity["shown"],
            "highlight_only": unity["highlight_only"],
            "followed_out_of_view": unity["followed"],
            "pending": unity["pending"],
            "not_applied": pr["not_applied"],
            "reach_not_completed": unity["downgraded"],
            "pending_without_region": pr["pending_unlocated"],
        },
        "bottles": {"placed_at_start": sum(1 for b in plan["bottles"] if b["position"] and not b["hidden"]),
                    "without_region": pr["bottles_without_region"], "spawned": pr["spawned_bottles"]},
        "scene": {"boxes": len(plan["boxes"]), "colliders": unity["colliders"], "labels": unity["labels"]},
        "notes": unity.get("notes", []),
    }
    (output / "render_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if not args.keep_frames and preview is None:
        for frame in frames_dir.glob("*.png"):
            frame.unlink()
        frames_dir.rmdir()
    progress(1.0)
    result = output / ("render_report.json" if preview is not None else "camera.mp4")
    print(f"Re-enactment {'preview' if preview is not None else 'video'} ready: {result}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--unity", required=True, type=Path, help="Unity editor executable")
    parser.add_argument("--output", required=True, type=Path, help="New recording directory")
    parser.add_argument("--preview", action="store_true", help="Action sample frames instead of full recording")
    parser.add_argument("--ambiguous", action="store_true", help="Occlude the correct return during the workflow")
    parser.add_argument("--rebuild", action="store_true", help="Regenerate the scene first")
    parser.add_argument("--camera", choices=["front", "side"], default="front", help="Calibrated camera preset")
    parser.add_argument("--timeline", type=Path, help="Render this dashboard timeline.json's re-enactment instead")
    parser.add_argument("--keep-frames", action="store_true", help="Keep the re-enactment's PNG frames")
    parser.add_argument("--frames", type=int, default=0, help="Timeline preview: render only the first N frames")
    parser.add_argument("--at-ms", type=lambda v: [float(x) for x in v.split(",")], default=None,
                        help="Timeline preview: also render frames around these media times (comma-separated ms)")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("Output directory must be new or empty")
    if not args.unity.exists():
        fail(f"Unity editor not found at {args.unity}")
    output.parent.mkdir(parents=True, exist_ok=True)
    base = [str(args.unity), "-batchmode", "-projectPath", str(PROJECT), "-quit"]
    if args.rebuild or not (PROJECT / "Assets/Pharma/Generated/Pharmacy.unity").exists():
        subprocess.run(base + ["-executeMethod", "Pharma.Simulation.Editor.PharmacySceneBuilder.Build",
                               "-logFile", str(output.parent / (output.name + "-build.log"))], check=True)
    if args.timeline:
        render_timeline(args, base)
        return
    command = base + ["-executeMethod", "Pharma.Simulation.Editor.SimulationExporter.Export",
                      "-pharmaOutput", str(output), "-pharmaCamera", args.camera,
                      "-logFile", str(output.parent / (output.name + "-render.log"))]
    if args.preview:
        command.append("-pharmaPreview")
    if args.ambiguous:
        command.append("-pharmaAmbiguous")
    subprocess.run(command, check=True)
    if not args.preview:
        subprocess.run([sys.executable, str(TOOLS / "package_recording.py"), str(output)], check=True)


if __name__ == "__main__":
    main()
