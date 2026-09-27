# Scan-to-simulation handoff

Handoff for the agent finishing the **room scan → dashboard → Unity re-enactment** work
(plan items N1–N4 and M5–M9, `plan.md` › "Next steps after M1–M4 — 2026-09-26").
Written 2026-09-26 on branch `feature/room-scan-floor-track`.

Read first: `AGENTS.md` (rules, especially 3, 6, 12–15), then `plan.md` ›
"Next steps after M1–M4" and its "Status (updated 2026-09-26)" block, then
`simulation/README.md` › "Scene geometry and dashboard re-enactment", and
`simulation/VALIDATION.md` (you'll add a section to it).

## What exists (all implemented and unit-tested)

The pipeline, end to end:

1. **Room scan → clean room.** `services/room.py` imports a scan. `services/room_rebuild.py`
   (`rebuild/2`) rebuilds walls, shelf units (with boards and labels), counter, bin and blocks
   from the scan plus the 3D tags. 3D tags (`Region3D` boxes) are the only hand-made regions.
2. **Camera registration.** `services/camera_geometry.py` solves a camera from point pairs.
   `services/region_projection.py` projects the 3D tags into each view's 2D polygons, which
   CV/inventory uses unchanged.
3. **Floor track (N3).** `services/floor_track.py` places the technician on the floor from
   YOLO ankles/hips, using the registration.
4. **Timeline (M9 input).** `services/timeline.py` bundles `timeline/1`: the track, the dashboard's
   decisions (decided / confirmed / pending / not_applied), starting counts, the rebuilt room and
   the cameras.
   - It is served at `GET /api/recordings/{name}/timeline`.
   - `inputs_key` keys renders and marks them stale.
5. **Planner (M7/M8).** `simulation/tools/reenact_plan.py` turns a timeline into
   `reenactment-plan/1`, covering:
   - Unity camera, bottles on shelves, reaches with a ≤0.3 m step and a 0.6 s lead.
   - Blend or cut after out-of-view gaps.
   - Push-out from furniture.
   - Highlight / follow fallbacks.
   - Wall cutaway (new; see below).
6. **Unity re-enactment.** Editor code: `Editor/ReenactmentScene.cs`,
   `Editor/ReenactmentExporter.cs`. Runtime: `Scripts/ReenactmentPlan|Player|Actions.cs`,
   `PharmacySimulation.Reenactment.cs`.
   - It drives the same rig and collision guards. Pickups and put-downs are shown only when
     the wrist actually reaches the contact.
   - No sensor events are emitted.
   - CLI: `simulation/tools/render.py --timeline T --output O`. Preview flags:
     `--frames N --at-ms a,b,c --keep-frames`.
7. **Render queue and player (M9).**
   - `services/render_jobs.py` (`RenderQueue`) runs one render at a time and reuses renders
     by `inputs_key`.
   - Routes in `api/room_routes.py`: `/renders`, `/recordings/{name}/render[/{file}]`,
     `POST /player/source`.
   - The player (`replay_stream.py`) serves real, sim or side-by-side video. Sim frames carry
     the banner "Simulation re-enactment (not camera footage)".
   - Dashboard: `RenderButton.jsx`, the source switch in `CameraFeed.jsx`, render toasts in
     `lib/jobs.jsx`.
8. **Simulator → dashboard import (N2).**
   - Unity writes `scene_geometry.json` (`Editor/SceneGeometry.cs`).
   - `backend/scripts/import_sim_room.py` / `services/sim_scene.py` creates the room, tags,
     layout, photo and registration from exact point pairs.
9. **Evaluator (N3), evaluator-only.** `simulation/tools/evaluate_floor_track.py` compares a
   floor track with Unity rig truth. It must never feed CV, inventory or the re-enactment.

## What happened in the last session (the reason for this handoff)

The first **preview** of the real Unity demo's re-enactment (`upload-unity-demo-20260926-202249`)
looked wrong even though the numbers passed. Four root causes were found and fixed:

| Problem seen in the preview | Cause | Fix (file) | Verified? |
|---|---|---|---|
| Camera outside the room; near walls hid counter, bin, technician | Rebuild draws a wall on every outline edge; the demo is a two-wall dollhouse with the camera past the open corner | Walls the camera is outside of are `cutaway`: not drawn, still colliders (`reenact_plan.cutaway`, `PlanBox.cutaway`, `ReenactmentScene.cs`) | Yes, in preview (2 walls cut, scene matches the footage) |
| Paper-thin shelf units; 0 of 10 actions shown, 5 guard holds | Unity draws shelf regions as 0.02 m planes; the importer passed that through | `sim_scene.deepen_region` extends thin tags back over the furniture behind (0.65 m boards) | Yes: 6 of 10 shown, 0 guard holds. CV re-checked on a fresh upload: all 10 decisions identical |
| Misplaced vitamin D bottle stayed on the amoxicillin shelf; a different bottle was picked up | `Stock.find` preferred an untouched bottle over a matching medication | Medication match now outranks untouched (`reenact_plan.Stock.find`) | Yes: one bottle carried shelf → counter → misplaced → back → bin |
| `evt_006` (return to the vitamin D shelf) not shown, wrist 0.40 m away | The rebuild found two boards 5 cm apart (0.82 from the tag bottom plus 0.87 scanned). The bottle slot sat on the lower one, inside the upper one, so the guard refused: "Carried bottle intersects shelf_unit_01/board_3" | A tagged row reuses a scanned board within `BOARD_SEPARATION_M` (0.12 m); `ALGORITHM` bumped to `rebuild/2` so cached rebuilds refresh | **Not yet re-rendered.** Only the unit test is done |

Diagnostics added: when the guard refuses a reach pose, the render report's note now says why
(`ReenactmentActions.ReachRefusal`, "…; reach refused: <obstacle>").

The other two actions not shown are `evt_002`/`evt_003` (counter put-down and pickup). They are
`highlight` because the tracked body is too far from the counter for a ≤0.3 m step. This is
allowed behavior, not a bug; look at it only if time allows.

## Measured results so far (record these in `plan.md` / `VALIDATION.md`)

- **N2 import** (room `simulated-pharmacy`, view `simulation-room-camera-01`):
  - Registration RMS 0.00057 px, max 0.0010 px.
  - All 8 regions imported.
  - After deepening, the regenerated shelf polygons are 38–73% larger (calibration v5 → v6).
    Counter and bin polygons are unchanged.
- **CV with the deepened tags** (`upload-unity-demo-deep-tags-20260926-203454`):
  - All 10 decisions match the first run and the scripted demo: counter, misplaced, returned,
    2× disposed.
  - It raised 2 `reconciliation_issue` alerts ("pickup from a shelf whose recorded count is
    already zero"). This is expected: it replayed on the same test inventory after the first
    run had emptied the vitamin D shelf. It is **not** a CV difference.
- **N3 floor track vs rig truth** on the demo (3180 frames). Report `scratchpad/m7/floor_track_eval.json`:
  - Placed fraction: 0.832.
  - Position: median 4.8 cm, p90 9.7 cm, max 26.9 cm.
  - Facing: median 6.4°, p90 40.6°, max 176.9°. There are **41 front/back flips over 90°**
    (bridged frames: median 115°).
  - Facing is the weak point. Report it as measured, not as validated accuracy.
- **Re-enactment preview** (65 frames, about 11 s wall time):
  - Region alignment max 0.0001 px.
  - 534 out-of-view frames (= the unplaced track frames), 2 cuts, 0 blends.
  - 7 bottles have no region: medications in the test DB's starting counts with no tag in
    this layout. Expected.
- **Tests:** backend 209 passed; simulation 29 passed; `git diff --check` clean.
  `npm run build` last passed before this session's final edits; no dashboard files changed
  since.
- **Earlier (M6):** simulation output is byte-identical (or identical once IDs are removed);
  SimulationChecks passed (`maxFootError=6.69e-07`); the frame noise floor is mean pixel
  difference ≈0.0015, max 109, from TextMesh glyph edges. That full-demo comparison ran
  *before* the later Unity host edits, so it must be redone (step 5).

## Environment right now

- **Do not touch port 8000.** It is the user's own dev server (`--reload`).
- **Isolated test server on port 8011** (pid 65583 at handoff). It was started before the
  `room_rebuild.py` change and has no `--reload`, so it runs **old** rebuild code and must be
  restarted.
  - `DATA_DIR=$S/e2e/data`, `MONGO_DB_NAME=pharma_room_e2e`.
  - Recordings: `upload-unity-demo-20260926-202249` (the one to render),
    `upload-unity-demo-deep-tags-20260926-203454`, `upload-synthetic-walk`,
    `upload-cam-a-20260926-190523`, `demo_scenario_01`.
- `S` = `/private/tmp/claude-501/-Users-christophermao-Documents-GitHub-Pharma/7c97b3a6-33e2-45bf-b1b0-eac80ed7d750/scratchpad`
  (the previous session's scratchpad; it may be cleaned up by the OS). Its contents:
  - `m7/n2-demo/`: the full Unity demo export (camera.mp4, imu_events.jsonl,
    scene_geometry.json, calibration, rig truth for the evaluator).
  - `m7/demo_timeline2.json`: the timeline with deepened tags, but still the old double boards.
  - `m7/demo_preview3/`: the latest preview (frames plus reports).
  - `m6/baseline/` + `m6/compare.py`: the M6 baseline demo render and the frame comparison
    script.
  - `e2e/data/`: the 8011 server's data dir.
  - If `S` is gone, regenerate:
    1. Render the demo with `simulation/tools/render.py --unity U --output <dir>`.
    2. Import it with `import_sim_room.py <dir> --api http://127.0.0.1:8011`.
    3. Upload `camera.mp4` + `imu_events.jsonl` with `layout_id`, wait for
       `status=ready`, then `POST /api/recordings/{name}/apply`.
    4. The backend needs the simulator's medications (Vitamin D 50000IU, Atorvastatin 20mg,
       Amoxicillin 500mg, Lisinopril 10mg, Metformin 500mg, Omeprazole 20mg) in the test
       catalog.
- Unity: `U=/Applications/Unity/Hub/Editor/6000.6.3f1/Unity.app/Contents/MacOS/Unity`. The
  `Licensing::Module … Access token is unavailable` log line is harmless noise.
- Python: always `backend/.venv/bin/python`.

## Remaining work, in order

The user's rule: **iterate on previews** (the first ~15 frames plus frames around action times
via `--at-ms`). Do **one** full-length render per deliverable, only at the very end.

1. **Restart 8011 with the real Unity**, after checking nothing is processing, since a restart
   kills extraction threads. From `backend/`:
   `DATA_DIR=$S/e2e/data MONGO_DB_NAME=pharma_room_e2e UNITY_PATH=$U .venv/bin/python -m uvicorn pharma.api.main:app --host 127.0.0.1 --port 8011`
   Leave `SIMULATION_DIR` unset (it defaults to `../simulation`).
2. **Verify the board fix in a preview.**
   1. Re-fetch `GET /api/recordings/upload-unity-demo-20260926-202249/timeline` and check
      that `rebuilt.algorithm == "rebuild/2"` and each shelf unit reports 3 boards (≈0.41,
      0.87, 1.59 m).
   2. Preview it:
      `render.py --unity $U --timeline <tl> --output <dir> --frames 15 --at-ms 43000,50500,53500,61000,64000,70000,74500,83500,92500,101500 --keep-frames`
   3. Expect `evt_006` shown, 0 guard holds and no "reach refused" notes.
   4. Look at the frames, not only the report. Check:
      - Bottles standing on boards, not inside them.
      - The red misplaced bottle on the amoxicillin shelf until `evt_005` lifts it.
      - The amber bottle on the counter.
      - The drop into the bin.
3. **One full re-enactment render through the product path.** Use the dashboard's
   "Render simulation" button, or `POST /api/recordings/{name}/render`. Then check:
   - The `renders/<key>/` output: `sim.mp4`, `side_by_side.mp4`, `manifest.json`.
   - The player's Sim and Side-by-side sources.
   - Re-applying or changing a decision marks the render stale.
4. **Re-run N3** if the floor track changed (it shouldn't have). Otherwise keep the numbers
   above. Command:
   `evaluate_floor_track.py $S/m7/n2-demo --track <scenario>/floor_track.json --room <rooms>/simulated-pharmacy/room.json`
5. **One full demo render plus regression checks**, required by AGENTS.md 12 because Unity
   movement and host code changed:
   1. Render the scripted demo, not `--timeline`.
   2. Compare it with `$S/m6/baseline` using `$S/m6/compare.py`. Expect it within the noise
      floor; only `capture.json`/`manifest.json` IDs and the `scene_geometry` entry may differ.
   3. Run SimulationChecks:
      `$U -batchmode -projectPath simulation -quit -executeMethod Pharma.Simulation.Editor.SimulationChecks.Run -logFile <log>`
   4. Run `simulation/tools/validate_recording.py` on the output.
6. **Docs.**
   - Add a "Scene-to-simulation re-enactment — 2026-09-26" section to
     `simulation/VALIDATION.md` with the measurements (above and from steps 2–5).
   - Update the M7/M8/N2/N3 status in `plan.md`. Record the four fixes, and record the facing
     flips and the counter highlight-only as known limitations. Never state thresholds as
     validated accuracy.
7. **Final checks:**
   - `backend/.venv/bin/python -m pytest -q backend/tests`
   - `… -m pytest -q simulation/tests`
   - `cd backend/dashboard && npm run build`
   - `git diff --check`
8. **Cleanup**, only when done:
   - Stop the 8011 server (not 8000).
   - Drop the Mongo DB `pharma_room_e2e`.
   - Delete bulky scratch exports: `$S/m6` (≈2.7 GB) after step 5, and `$S/m7` previews
     (≈2.9 GB).

## Rules to keep in mind

- Commit only when the user asks.
- Never commit:
  - `backend/data/layouts/default/layout.json` (left modified on purpose)
  - `.env`, weights, videos, datasets or scans
  - `bus.jpg` at the repo root (not ours; leave it)
- Rig truth and `evaluator_only/` stay out of CV, inventory and the planner. The planner reads
  only the timeline.
- A pickup or put-down that isn't physically reached must not look completed. The
  highlight/follow fallbacks exist for that; don't bypass the guard to make actions "shown".
- Re-enactment emits no IMU events. Mock IMU events never carry shelf IDs.
- Out of scope here: automatic camera handoff (a requirement, not implemented) and
  real-world/sim presentation transitions.
