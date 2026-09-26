# Detailed scene validation — 2026-09-26

The detailed visual release adds character normal maps, staff ID, pocket and pens;
shelf slots, metal lips, cabinet doors and handles; cap grips and barcode markings;
wood/tile/wall surface variation; keyboard, receipt printer, clock, protocol sign,
and disposal-bin fittings. A separate editorial portrait shows the character detail.
The fixed room camera, calibration pharmacy-v3, 106-second schedule, and solid
collision geometry are preserved. Small surface decorations are non-colliding.

Unity rendered 3,180 frames at 1920 × 1080 / 30 FPS. The final scene passed
6,362 simulation-frame checks across ordinary and optional-occluder variants,
including blocked routes, ownership, restart, deterministic playback, turns,
foot planting, limb guards, and disposal. The Python suite passed 24 tests.

Actual YOLO11n-pose inference on the final rendered video (CPU, size 960,
confidence threshold 0.5) found a person in 2,636 of 3,180 frames. All 10
scripted action-region samples were correct, with zero abstentions and zero
wrong regions. Inference took 192.03 seconds (16.56 FPS) while presentation
encoding ran concurrently. This small scripted check does not establish
real-pharmacy accuracy or implement inventory event fusion.

All eight delivered MP4s decoded to 3,180 frames at 30 FPS; all 15 mock
sensor events matched the CV observation stream in order and on the video clock.

The through-wall view remains explicitly labeled simulation rig truth, separate
from actual YOLO observations. It retains 16 joints per frame, including 450
frames with every joint occluded by solid room geometry and 594 frames with
some occlusion. Character motion is procedural; the scene remains stylized.

Detailed metrics: `validation/detail-cv.json`, `detail-presentation.json`,
`detail-xray.json`, and `detail-checks.json`.

---

## Previous animation release (historical measurements)

# Animation and simulation X-ray validation — 2026-09-26

## Delivered behavior

The room-camera workflow now runs for **106 seconds at 1920 × 1080, 30 FPS
(3,180 frames)**: a 40-second aisle walkthrough and 66-second handling sequence.
The handling schedule is slowed by 1.5×; sensor and prescription fixture timestamps
use the same scale. The room, three shelf banks, and eight configured regions remain
at calibration version pharmacy-v3.

A new, explicitly labeled **simulation X-ray** uses Unity rig state to keep the
complete skeleton visible through walls and shelving. Unity's Game view includes
an enabled-by-default toggle. Raw exports remain clean camera footage.
`simulation_view.py` exports X-ray, complete-skeleton, four-panel comparison, and
magnified motion-detail videos. Simulation panels are labeled Unity rig / not CV.
The actual YOLO output remains separate and retains missed/uncertain observations.

The exporter records 16 named joints on every frame under `evaluator_only/`.
All 3,180 frames contain all 16 joints, including **450 frames where every joint's
ray to the camera intersects scene geometry**. There are 594 frames with at least
one such occluded joint. Visibility flags describe room geometry, not skin/self-
occlusion; they color joints but never remove them from the X-ray presentation.
These rig files are absent from the runtime manifest and cannot be used as stock
or CV observations.

## Motion improvements and physical checks

- Planted world-space stance feet, predictive swing-foot landing, and foot IK.
- Hip height adapts to both leg reaches, avoiding the earlier crouched appearance.
- Rounded navigation corners, bounded yaw, and eased walking starts/stops.
- Relaxed empty hands, arm swing coupled to leg separation, light torso/gaze motion.
- Gradual reach lean, smooth wrist arcs, deliberate handling timing, and finger curl.
- Leg-segment and foot-sweep guards added to body/arm/carried-bottle checks.

**6,362 fixed-frame samples** passed across ordinary and optional-occluder variants,
including all ownership, blocked-route, disposal, restart, and deterministic-seek
checks. Foot-target error stayed within the 1 cm assertion and support-foot drift
within the 3 mm assertion (both measured near floating-point precision). Ankle
joints stayed above the floor; this is not a mesh-level contact-force test. Turns
were bounded to 180 degrees/second. Exact measurements are recorded in
`validation/animation-checks.json`.

Rendered walking/reach frames were visually inspected, including a magnified
sequence used to identify and fix excessive knee bend in an intermediate pass.
The delivered animation is procedural, not motion capture or full grasp physics.
Unexpected blockers still stop the actor; dynamic replanning is not implemented.

## Actual CV on the new rendered footage

Unity 6000.6.3f1; Apple M5 ARM64, 16 GB; Python 3.12.14;
Ultralytics 8.4.163, YOLO11n-pose, CPU, requested inference size 960,
right-wrist confidence threshold 0.5.

| Measurement | Final recording |
| --- | ---: |
| Frames inferred | 3,180 |
| Frames with a detected person | 2,636 |
| Bottle-action timestamps scored | 10 |
| Correct action-region assignments | 10 |
| Action abstentions / confident wrong regions | 0 / 0 |
| Full-video evaluation time | 158.43 s |
| Evaluation throughput | 20.07 FPS |

The evaluation ran while presentation export was also active. Timings are local
measurements, not isolated benchmarks or real-time guarantees. Ten front-bank,
counter, and disposal actions do not establish rear-aisle or real-camera accuracy.
YOLO can miss or guess hidden joints. Per-event evidence is in
`validation/animation-cv.json`.

## Software and replay validation

24 Python tests passed, including source/clock validation, drawing fully occluded
rig joints, recording contracts, CV confidence/overlap logic, streaming options,
and backend smoke tests. The recording manifest validates 15 accepted mock sensor
events, eight calibrated regions, receiving fixtures, clock alignment, and MP4 hash.
All eight presentation/source MP4s decoded fully to 3,180 frames at 30 FPS. The
per-frame CV clock and all 15 synchronized event IDs matched the source recording.

## Scope and previous evidence

Rear shelf banks remain collidable scenery; pickup mappings cover the front bank.
The walkthrough is authored navigation, not autonomous semantic search. The X-ray
is a simulator visualization, not evidence that a real camera sees through walls.
Inventory event fusion, MongoDB, and the working dashboard remain future work.
Individual-action clips remain required when the simulation is finalized.

The previous [84-second report](validation/layered-report.md) and
[single-bank report](validation/single-bank-report.md) are historical references.
The optional-panel variant passed current motion checks but was not separately
rendered/evaluated for this release; the delivered clip demonstrates natural shelf
occlusion and the always-visible simulation rig.

## Integrated MongoDB and multi-camera validation — 2026-09-26

The combined dashboard/simulation branch now persists live pharmacy state in MongoDB.
129 tests passed with isolated databases on a real MongoDB 7.0.14 server, including
legacy import, stale-writer rejection, failed/uncertain write recovery, prescription
idempotency, restart, and camera-switch event deduplication. The dashboard production
build passed. Browser inspection verified the MongoDB-backed dashboard showing the
side camera; API-served assets and direct SPA page routes have regression coverage.

The side camera rendered the same 3,180-frame / 106-second scenario. Actual YOLO
inference was performed separately for each camera. A causal selector switched
front→side at 6.033 s and side→front at 33.833 s when the active arm evidence was lost.
There were 75 frames without a reliable arm in the selected view; these retain
uncertainty. The selector uses no Unity skeleton/truth input.

All 10 bottle-action regions were correct in the integrated MongoDB inventory replay,
with no action abstentions or wrong regions. Replaying and reopening the controller
left inventory and applied-event counts unchanged. The two new presentation videos
both decoded all 3,180 frames at 30 FPS, 1920 × 1080. Metrics are in
`validation/multicamera-handoff.json`, `multicamera-inventory.json`, and
`multicamera-videos.json`. These measurements cover one synthetic technician/workflow;
confidence is not proof of visibility and real-camera reliability is unmeasured.
