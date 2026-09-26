# Simulation validation — 2026-09-26

## Environment and scope

- Unity **6000.6.3f1**, built-in rendering, graphics-enabled batch capture.
- Apple M5, ARM64, 16 GB RAM; pose inference explicitly used **CPU**.
- Python 3.12.14, Ultralytics 8.4.163, `yolo11n-pose.pt`.
- Each final recording: **1280 × 720, 30 FPS, 38 seconds, 1,140 frames**.
- The evaluator called the existing `backend/src/pharma/pose.py` helper on each
  complete MP4. Default inference resolution was 640 (384 × 640 letterboxed tensor).
- Region scoring used the right wrist at ten scripted pickup/release timestamps,
  a provisional confidence threshold of 0.5, and exactly one matching rectangle.
  This is a feasibility check, not production event fusion or inventory validation.

## Final rendered-video results

| Measurement | Clean workflow | Occluded-return variant |
| --- | ---: | ---: |
| Decoded/inferred frames | 1,140 | 1,140 |
| Frames with a detected person | 1,110 | 1,019 |
| Action timestamps scored | 10 | 10 |
| Correct region identifications | 10 | 9 |
| Abstentions | 0 | 1 |
| Confident wrong regions | 0 | 0 |
| End-to-end inference time | 22.36 s | 23.88 s |
| Measured processing throughput | 50.99 FPS | 47.74 FPS |

The occluded action is the designated-shelf return at **18 seconds**. In the final
variant no person pose was available at that frame, so the evaluator abstained.
The clean action-frame wrist confidences ranged from approximately 0.868 to 0.952.
These timings are one local run each, not a stable hardware performance guarantee.

The recorded clips still contain missed detections between action timestamps.
Do not interpret ten successful action frames as continuous tracking accuracy.
An earlier, smaller occluder produced a guessed wrist with confidence approximately
0.564 even though the hand was hidden. A confidence threshold alone therefore does
not guarantee safe handling of occlusion. Temporal association and confirmation
behavior belong in the future runtime implementation.

## Checks performed

- Unity imported and compiled the project, built the generated scene, rendered both
  full recordings, and exited successfully.
- Unity `SimulationChecks.Run` passed: distinct regions, action ordering, frame-clock
  alignment, contact reach within 8 cm, history-independent seeking, counter rest,
  first/last disposal, restart restoration, and occluder activation/deactivation.
- **13 Python contract tests passed**, including duplicate sensor events, malformed
  clocks, second pickup, truth leakage, video hash mismatch, and receiving totals.
- **2 existing backend tests passed**, including YOLO inference on its sample image.
- Both replay bundles passed validation: 15 mock sensor events, eight regions,
  matching image dimensions, stock fixtures, video hash, and relative runtime paths.
- Actual rendered frames and YOLO overlays were visually inspected. Corrected
  excessive lighting, label scale, stale offline skinning, unreachable disposal
  reach, and physical labels drawing through scene geometry before final export.

## Evidence and reproducibility

The generated replay bundles contain `manifest.json`, sensor/calibration/business
inputs, and `evaluator_only/pose_metrics.json` with per-event results. Pose overlays
are under `evaluator_only/pose/`. The final metrics are also committed in `validation/clean.json` and
`validation/occluded.json`. Generated media are excluded from Git.
Reproduce using `simulation/tools/render.py` and `simulation/tools/evaluate_pose.py`
as described in the simulator README. The character source revision and file
hashes are committed under `Assets/ThirdParty/Rocketbox/`.

## Remaining limits

- Procedural body/step motion is basic; it is not motion-capture quality.
- Only one technician, one handled bottle, and one single-medication prescription.
- Only the lower shelf row is exercised by this script; upper shelves are visual
  stock and calibrated regions, not validated reach scenarios.
- Sensor events are ideal mock actions, not physical IMU classification results.
- The camera view is fixed. No real pharmacy footage was evaluated.
- Expiry/disposal/prescription outcomes are fixtures and expected downstream actions.
  No MongoDB service, event-fusion service, alert workflow, or working dashboard was
  implemented. The visible terminal is a prop.
- The existing backend helper retains video results in RAM. Larger datasets should
  use a future streaming inference path rather than scale this evaluator unchanged.
