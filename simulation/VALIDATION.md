# Layered-shelf simulation validation — 2026-09-26

## Current scene and outputs

The scene now has three physical shelf banks, two aisles, stocked containers,
collidable supports and backs, a dispensing counter, and a hollow disposal bin.
One technician follows a collision-checked 40-second aisle walkthrough and then the
44-second bottle workflow. The fixed room camera exports **1920 × 1080, 30 FPS,
84 seconds, 2,520 frames**, calibration version **pharmacy-v3**.

The front bank retains six mapped medication regions; the two rear banks are
reserve-stock scenery for the visibility experiment. No rear pickup/medication
mapping is claimed. The walkthrough is authored navigation, not semantic search.

`pose_videos.py` processes the camera MP4 through the existing backend helper with
streaming inference, producing a full-resolution pose overlay and skeleton-only
video, a 1920 × 720 three-view comparison, and timestamped keypoint/confidence JSONL.
Arms are highlighted. Skeleton edges require both endpoints above confidence 0.5.
These outputs do not read Unity bones, simulation state, or evaluator ground truth.
The comparison synchronizes mock IMU events and labels wrist-region candidates as
observations, not confirmed inventory changes.

## Measured inference results

Environment: Unity 6000.6.3f1; Apple M5 ARM64, 16 GB; Python 3.12.14;
Ultralytics 8.4.163 with `yolo11n-pose.pt`, CPU inference.

| Measurement | Default-size baseline | Final wide-room inference |
| --- | ---: | ---: |
| Requested inference size | 640 | 960 |
| Video frames processed | 2,520 | 2,520 |
| Frames with a detected person | 1,583 | 1,969 |
| Bottle-action timestamps scored | 10 | 10 |
| Correct region assignments | 6 | 10 |
| Abstentions | 4 | 0 |
| Confident wrong action regions | 0 | 0 |

A preview comparison also tried size 1280; 960 recovered the ten visible action
poses and was retained for the full run. Framewise missing-person detections remain
in the exported presentation. A detected person or confident joint does not prove
visibility: YOLO can guess hidden anatomy. The ten action scores measure only the
scripted front-bank/counter/disposal events, not correctness throughout rear aisles.

The final full-video evaluation took 120.84 seconds (20.85 FPS) on this local run,
while the presentation export was also active. This is not a real-time guarantee
or an isolated benchmark. The design uses prerecorded playback. Per-event evidence
is in `validation/layered.json`; the original 640-size result is retained in
`validation/layered-baseline-640.json`.

## Verification

- Unity built and exported the complete final recording successfully.
- 5,042 simulation-frame samples passed across ordinary and optional-panel variants,
  including body/arm/carried-bottle guards, bounded walking speed, ownership,
  deterministic seek/restart, disposal landing, invalid actions, and injected blockers.
- 20 Python tests passed: recording contracts, confidence/overlap handling, streaming
  inference options, and existing backend smoke tests.
- The replay validates its frame clock, 15 mock sensor events, eight configured
  regions, receiving totals, and source video SHA-256.
- All four delivered MP4s decoded to exactly 2,520 frames at 30 FPS. The observation
  clock and all 15 synchronized sensor IDs matched the source recording.
- Rendered frames and three-view comparisons were inspected at walkthrough and
  bottle-action moments. Person loss behind shelves is preserved in the presentation.

## Limits and historical evidence

The camera cannot see through shelf backs. A 2D wrist overlapping a front-region
rectangle does not establish depth or rear-shelf identity. Occlusion-aware event
fusion, additional views/depth, or employee confirmation need separate development
and validation before rear-aisle inventory decisions. Do not use Unity-known joints
to conceal CV failures.

Navigation remains deterministic and kinematic with approximate body/limb volumes;
unexpected obstacles stop the actor rather than trigger dynamic replanning. Full
contact-force physics, generic task planning, MongoDB, inventory mutation services,
and the working dashboard remain unimplemented. Per-action clips remain a required
future deliverable when the simulation is finalized.

The previous single-bank 44-second results are retained in
[the historical report](validation/single-bank-report.md), with `clean.json` and
`occluded.json`. They do not describe this new camera/scene. The optional-panel
variant passed current movement checks but was not separately rendered/evaluated
for this layered-scene release; the delivered video demonstrates natural shelving
occlusion.
