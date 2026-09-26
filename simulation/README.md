# Unity pharmacy simulation

A fixed room-camera scene with three physical shelf banks and walkable aisles, six
configured medication regions on the front bank, a dispensing counter,
central terminal prop, disposal bin, and one textured, rigged medical character.
The deterministic 106-second sequence begins with a 40-second aisle survey, then
handles one bottle at a time in the 66-second handling workflow (the original schedule slowed by 1.5×). It exports
prerecorded footage and synchronized mock IMU events for the existing Python CV
pipeline. It does not implement the inventory service, MongoDB, or dashboard.

## Open and play

1. Install **Unity 6000.6.3f1** (the version used for validation) through Unity Hub.
2. From the repository root, run `python3 simulation/tools/fetch_character.py`.
   This downloads one pinned MIT-licensed Microsoft Rocketbox character and its
   textures, verifies their hashes, and leaves the large files untracked.
3. In Unity Hub, add the **`simulation/` directory** as a project and open it.
4. Open `Assets/Pharma/Generated/Pharmacy.unity`. If the generated scene is missing
   or you changed the builder, use **Pharma > 1. Build pharmacy scene**.
5. Press **Play**. The Game view shows the camera feed and controls for play/pause,
   restart, seeking, and the occluded-return variant. Use a 16:9 Game view.

The downloadable project archive includes the character assets, so step 2 is only
necessary for a Git checkout. The generated scene and material files are committed;
Unity's Library cache, rendered recordings, and large character files are not.

## Record footage and mock signals

Create a Python environment and install `simulation/requirements.txt`. Rendering
needs a licensed Unity editor and a functioning graphics device. Do **not** add
`-nographics`: the exporter needs Unity to render the camera image.

From the repository root on macOS:

```sh
python simulation/tools/render.py \
  --unity '/Applications/Unity/Hub/Editor/6000.6.3f1/Unity.app/Contents/MacOS/Unity' \
  --output simulation/Exports/demo-001
```

Use the corresponding Unity executable path on Windows/Linux. Close this project
in the editor before a batch run; Unity does not allow two instances to open it.
The output directory must be new or empty. Repeat runs use separate directories and unique session IDs.

Options:

- `--preview`: render only the five aisle samples and ten action frames; no MP4 is packaged.
- `--ambiguous`: a temporary panel obscures the correct return at 70 seconds.
- `--rebuild`: regenerate the scene from its editor builder before exporting.

The editor menu **Pharma > 2. Export recording** writes a timestamped frame export
under `Exports/`. To encode that export and add business fixtures, run:

```sh
python simulation/tools/package_recording.py simulation/Exports/RECORDING_NAME
```

The CLI wrapper performs that packaging step automatically. Playback UI and pose
annotations are not burned into the camera recording. CPU-baked skinned meshes
ensure offline frames reflect their exact sampled animation pose.

## Character state and physical constraints

The technician follows a scheduled task plan through explicit states: idle, walking,
carrying, reaching, pickup, placement, counter rest, disposal, complete, or blocked.
Each bottle has its own location/ownership state: on shelf, held, at counter,
misplaced, or disposed. The deliberate wrong return is an allowed scenario action;
it does not erase the bottle's identity or imply the placement is correct.

Unity builds navigation paths around walls and furniture. The walkable surface
includes extra clearance for carrying arms. A fixed 30 Hz simulation checks swept
body capsules, arm/hand motion, and the carried bottle against solid geometry.
Pickup requires arrival, reachable contact, an available bottle at that location,
and an empty hand. Placement requires ownership and a supporting surface; disposal
uses a hollow bin and a gravity drop that stops on its bottom. Bottles do not move
just because a scheduled timestamp has arrived.

If a live obstacle blocks a planned path or reach, the agent enters **Blocked** and
keeps its last safe pose. It does not emit the failed action's IMU event. Export
fails rather than packaging a completed-looking invalid demonstration. Clear the
obstacle and restart, or rebuild the scene/path plan for a changed room layout.
This prototype stops on unexpected obstacles; it does not continuously replan around
moving people. Navigation and collision are deterministic kinematic constraints,
not a full contact-force or grasp-physics simulation.

The 66-second handling schedule, after a 40-second aisle walkthrough, provides time to route around the counter at bounded
walking speed. Seeking replays all intermediate simulation ticks; it cannot skip
collision checks or inventory-object transitions. The technician's internal state
is exported only to `evaluator_only/simulation_states.jsonl`, never to the CV input.

## Replay bundle

| File | Intended consumer |
| --- | --- |
| `camera.mp4` | CV input: 1920 × 1080, 30 FPS, 3,180 frames |
| `imu_events.jsonl` | Mock pickup, movement, and release events; no object/location answers |
| `calibration.json` | Eight front-workflow region rectangles, normalized from top-left |
| `initial_inventory.json` | Synthetic receiving records, expiry, and pooled stock |
| `business_events.jsonl` | One prescription; arrival, confirmed fill, and payment receipt |
| `manifest.json` | Relative input paths, dimensions, frame rate, camera/calibration IDs, video SHA-256 |
| `frames/` | Lossless source frames; optional after packaging |
| `evaluator_only/` | Ground truth, expected manual actions, and optional pose evaluation |

Sensor `media_time_ms` and video frame `i / fps` use the same clock. Slower rendering
or faster/slower replay must not change event association. Sensor IDs represent a
mock event source; they do not identify a medication or provide raw real-IMU data.
These are ideal action events, not a validated physical pickup/release classifier.

The runtime manifest never references `evaluator_only/`. The simulator's transforms,
object identities, and outcome labels are only for animation and offline evaluation.
Static calibration is intentional setup information, not a detected answer.

## Choreography and fixtures

| Time | Action |
| --- | --- |
| 0–40 s | Walk around shelf ends and through both rear aisles; no bottle-handling events |
| 43 s | Pick up vitamin D, 50,000 IU, from shelf A |
| 50.5 s | Release at dispensing counter; keep the movement session active |
| 52 s | Prescription confirmed filled: 30 tablets |
| 53.5 s | Pick up the same bottle from the counter |
| 55 s | Payment/receipt for the same transaction; must not deduct twice |
| 61 s | Release at wrong shelf B |
| 64 s | Pick up misplaced bottle to correct it |
| 70 s | Return to designated shelf A |
| 74.5 s | Pick up the expired bottle from shelf A |
| 83.5 s | Release into disposal; employee should identify the receipt and enter 70 tablets |
| 92.5 s | Pick up the final vitamin D bottle |
| 101.5 s | Dispose of it; absent quantity uses the last-bottle balance rule |

There are two vitamin D bottles and four bottles of each other configured medication.
Each received bottle starts with 100 tablets. One vitamin D receipt is expired
relative to the explicit fixture clock. Initial pooled vitamin D stock is 200; the
prescription leaves 170, the employee's 70-tablet disposal leaves 100, and last-bottle
disposal leaves zero. These are expected downstream outcomes, not implemented
inventory mutations. Employee answers are evaluator instructions, not runtime events.

## Verify and evaluate

```sh
python -m pytest simulation/tests -q
python simulation/tools/validate_recording.py simulation/Exports/demo-001
```

**Pharma > 3. Verify state, collision, and deterministic playback** checks every
frame of both variants, path speed, seek/restart determinism, counter and disposal
states, invalid ownership transitions, and a newly inserted aisle blocker. It also runs in batch mode through
`-executeMethod Pharma.Simulation.Editor.SimulationChecks.Run`.

Install the backend dependencies to use the existing pose helper on the recording:

```sh
python backend/scripts/pose.py simulation/Exports/demo-001/camera.mp4 --no-save
python simulation/tools/evaluate_pose.py simulation/Exports/demo-001 \
  --weights models/yolo11n-pose.pt
```

The evaluator calls `backend/src/pharma/pose.py`, processes the full video, and
scores the ten action frames against evaluator-only ground truth. It writes pose
images and metrics under `evaluator_only/`. Its simple right-wrist/rectangle check
is a feasibility measurement, not the future temporal event-association service.
Both evaluation and presentation use the backend streaming option, retaining only
needed frames rather than the complete video in memory.

## Camera, CV, and skeleton presentation

```sh
python simulation/tools/pose_videos.py simulation/Exports/demo-001 \
  --weights models/yolo11n-pose.pt --output simulation/Exports/demo-001-pose
```

This runs actual YOLO inference on every rendered frame (default `--image-size 960`
for smaller people in the wider room view) and exports synchronized:

- `pose-overlay.mp4`: camera footage with detected skeletons, arm highlights, and configured regions.
- `skeleton-only.mp4`: the same estimated joints on a dark background, with arms highlighted.
- `comparison.mp4`: raw camera, overlay, and skeleton side by side with media clock,
  right-wrist confidence, region candidate, and latest mock IMU event.
- `observations.jsonl`: per-frame pixel keypoints, confidences, region candidates,
  and synchronized sensor event IDs. `summary.json` records source hash and coverage.

The CV tool reads no Unity joint positions or evaluator truth. An edge needs both joints to
exceed the configured threshold (default 0.5); missing people/joints remain missing.
YOLO may nevertheless guess occluded joints confidently. A 2D wrist inside a shelf
rectangle does not resolve depth or prove physical contact. The comparison presents
observations, not inventory deductions or a completed event-fusion dashboard.

The rear two shelf banks are stocked, collidable scenery for the aisle-visibility
experiment. They do not yet have medication mappings or pickup tasks. The walkthrough
is an authored route, not semantic search or an autonomous task planner. Expect long
occlusions behind shelf backs in the actual CV output. A separate, explicitly labeled
simulation view can show the rig through those occlusions, as described below.
Per-action clips remain a separate required deliverable at simulation finalization.

## Always-visible simulation skeleton and improved motion

The Unity Game view now offers **Simulation X-ray skeleton (Unity rig, not CV)**,
which draws over scene geometry. Raw camera exports remain unannotated.

```sh
python simulation/tools/simulation_view.py simulation/Exports/demo-001 \
  --cv-presentation simulation/Exports/demo-001-pose \
  --output simulation/Exports/demo-001-simulation-view
```

Run `pose_videos.py` first on the same recording. This presentation tool exports:

- `simulation-xray.mp4`: the rendered room with the rig visible through walls/shelves.
- `simulation-skeleton.mp4`: the complete rig on a dark background on every frame.
- `comparison.mp4`: raw camera, simulation X-ray, actual YOLO output, and complete rig.
- `motion-detail.mp4`: a magnified view of the rendered animation; its crop follows the rig.

Every simulation panel is labeled **Unity rig ground truth / not CV**. Cyan joints
have a clear ray through scene geometry; amber joints are occluded by geometry.
Both remain drawn. This visibility flag does not model body self-occlusion.
The tool verifies source video hashes, frame clocks, full joint counts, and provenance.

`evaluator_only/rig_definition.json` defines 16 rig joints and their edges;
`evaluator_only/rig_skeleton.jsonl` records projected positions on every frame.
Neither file is referenced by the runtime replay manifest. Simulator joints must
never replace missing YOLO observations in inventory decisions.

Animation now uses world-space planted stance feet, predictive swing-foot landing,
foot IK, rounded path corners, bounded body rotation, acceleration/deceleration,
contralateral arm swing, gentle body/gaze motion, gradually blended torso lean,
eased wrist arcs, and state-dependent finger curl. The handling schedule is slowed
by 1.5× for more deliberate movements, while the aisle walkthrough remains 40 s.
All motion is deterministic procedural animation, not motion capture or full grasp
physics. Collision guards now also check leg segments and foot sweeps.

## Extending the scene

- `PharmacySceneBuilder.cs`: room geometry, materials, camera, labeled regions,
  imported character, and the generated scene.
- `FootPlantGait.cs`: planted stance, predictive swing-foot steps and landing targets.
- `SimulationSkeleton.cs`: labeled live X-ray and evaluator-only rig export.
- `CollisionWorld.cs`: Unity navigation mesh, complete paths, capsule/swept collision guards.
- `PharmacySimulation.cs`: guarded task/bottle states, skeletal reach/step motion,
  bottle locations, and playback controls. `Evaluate(t)` is independent of history.
- `SimulationExporter.cs`: fixed-clock frame sampling, abstract sensor events,
  calibration, and separated truth export.
- `tools/`: pinned asset retrieval, batch rendering, video packaging, validation,
  and evaluation through the existing backend.

New walls/furniture must have colliders on **PharmaSolid (layer 8)**; floor geometry
uses **PharmaWalkable (layer 9)**. Rebuild the scene/path plan after changing geometry
and export new calibration. Decorative props and bottle geometry are not navigation
obstacles; this prototype does not model bottle-to-bottle contact forces.
Animation is procedural and repeatable, not motion-capture quality. This initial
recording uses the lower shelf row; the upper row is stocked and calibrated but not
an evaluated reach scenario. No real IMU code, real prescription integration, patient
information, inventory database, or working cashier dashboard is included.

See `VALIDATION.md` for measured results and limitations, and
`Assets/ThirdParty/Rocketbox/NOTICE.md` for asset provenance and licensing.
