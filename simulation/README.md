# Unity pharmacy simulation

A fixed room-camera scene with six medication shelf regions, a dispensing counter,
central terminal prop, disposal bin, and one textured, rigged medical character.
The deterministic 38-second sequence handles one bottle at a time. It exports
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

- `--preview`: render only the start and ten action frames; no MP4 is packaged.
- `--ambiguous`: a temporary panel obscures the correct return at 18 seconds.
- `--rebuild`: regenerate the scene from its editor builder before exporting.

The editor menu **Pharma > 2. Export recording** writes a timestamped frame export
under `Exports/`. To encode that export and add business fixtures, run:

```sh
python simulation/tools/package_recording.py simulation/Exports/RECORDING_NAME
```

The CLI wrapper performs that packaging step automatically. Playback UI and pose
annotations are not burned into the camera recording. CPU-baked skinned meshes
ensure offline frames reflect their exact sampled animation pose.

## Replay bundle

| File | Intended consumer |
| --- | --- |
| `camera.mp4` | CV input: 1280 × 720, 30 FPS, 1,140 frames |
| `imu_events.jsonl` | Mock pickup, movement, and release events; no object/location answers |
| `calibration.json` | Eight fixed region rectangles, normalized from top-left |
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
| 2 s | Pick up vitamin D, 50,000 IU, from shelf A |
| 6 s | Release at dispensing counter; keep the movement session active |
| 7 s | Prescription confirmed filled: 30 tablets |
| 8 s | Pick up the same bottle from the counter |
| 9 s | Payment/receipt for the same transaction; must not deduct twice |
| 12 s | Release at wrong shelf B |
| 14 s | Pick up misplaced bottle to correct it |
| 18 s | Return to designated shelf A |
| 21 s | Pick up the expired bottle from shelf A |
| 26 s | Release into disposal; employee should identify the receipt and enter 70 tablets |
| 29 s | Pick up the final vitamin D bottle |
| 35 s | Dispose of it; absent quantity uses the last-bottle balance rule |

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

**Pharma > 3. Verify deterministic simulation** checks the Unity scene, event order,
contact reachability, seek/restart determinism, counter placement, disposal, and the
occlusion variant. It also runs in batch mode through
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
The existing helper retains results in memory, so allow several GB for a full clip.

## Extending the scene

- `PharmacySceneBuilder.cs`: room geometry, materials, camera, labeled regions,
  imported character, and the generated scene.
- `PharmacySimulation.cs`: deterministic choreography, skeletal reach/step motion,
  bottle locations, and playback controls. `Evaluate(t)` is independent of history.
- `SimulationExporter.cs`: fixed-clock frame sampling, abstract sensor events,
  calibration, and separated truth export.
- `tools/`: pinned asset retrieval, batch rendering, video packaging, validation,
  and evaluation through the existing backend.

Rebuild the scene after changing shelf/camera geometry and export new calibration.
Animation is procedural and repeatable, not motion-capture quality. This initial
recording uses the lower shelf row; the upper row is stocked and calibrated but not
an evaluated reach scenario. No real IMU code, real prescription integration, patient
information, inventory database, or working cashier dashboard is included.

See `VALIDATION.md` for measured results and limitations, and
`Assets/ThirdParty/Rocketbox/NOTICE.md` for asset provenance and licensing.
