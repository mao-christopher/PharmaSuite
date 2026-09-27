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

## Scene-to-simulation re-enactment — 2026-09-26

Completed local verification on `feature/room-scan-floor-track`, using Unity
6000.6.3f1, actual YOLO11n-pose inference at size 960, and isolated MongoDB 7.0.14.
The other machine's scratch files were unavailable. A new scripted demo export was
rendered once; an available original render from this Mac supplied the uploaded CV
recording and regression baseline. All 3,180 raw baseline/candidate frames were
compared; simulator states and sensor timing were identical. No simulator truth
was supplied to inference, inventory, the floor track, or the re-enactment planner.
Static scene geometry was imported as a synthetic box-mesh scan through the API.

| Check | Result |
| --- | --- |
| Imported/generated regions | 8 / 8, none skipped |
| Camera registration | RMS 0.00057 px; maximum 0.0010 px |
| Hand-drawn vs generated region decisions | 10 unchanged; no new abstentions |
| Shelf rebuild | `rebuild/2`; 3 boards/unit at 0.41, 0.87, 1.59 m |
| Floor track placed | 2,646 / 3,180 frames (83.21%) |
| Position error | median 4.75 cm; p90 9.53 cm; max 27.57 cm |
| Facing error | median 6.3 degrees; p90 42.23 degrees; max 178.2 degrees |
| Facing errors over 90 degrees | 41 frames |
| Re-enactment camera alignment | 64 corners; maximum 0.0002 px |
| Re-enactment actions | 7 shown; 2 highlight-only; 1 state-follow fallback |
| Collision holds / refused reaches | 0 / 0 |
| Out-of-view track / cuts / blends | 534 frames / 2 / 0 |
| Initial bottles | 22; none missing a region or spawned |
| Unity scripted collision checks | 6,362 frame samples passed |
| Scripted states / sensor schedule | Identical to local baseline |
| Raw image regression | mean absolute difference 0.000802 / 255; max 108 |
| Raw pixels changed per frame | average 74 of 2,073,600 |
| Python tests / dashboard build | 240 passed / passed |

The sparse preview captured 65 frames (first 15 plus five around each action),
while simulating the whole timeline. Visual inspection checked supported bottles,
misplacement, return and disposal. `evt_006`, previously refused because a tagged
board duplicated a scanned board, now reaches its contact. Re-enactment fixes also
include camera-side wall cutaways with retained colliders, thin tags extended over
the actual shelf depth, and medication-preserving bottle selection. Counter events
`evt_002`/`evt_003` remain highlight fallbacks because the permitted 0.3 m step does
not reach them; this is intentional, not an animated successful grasp.

One full product render was then requested through `POST /api/recordings/{name}/render`.
Both `sim.mp4` (1920x1080) and `side_by_side.mp4` (3840x1080) decoded all 3,180 frames
at 30 FPS. The three player sources were checked through the MJPEG API, and the
browser showed the labeled side-by-side view at the 70-second return. Repeating
the render reused its manifest without another Unity run. Reapplying all signals
applied zero new events and left inventory unchanged. A 1 cm test-region edit
marked the render stale; restoring the region advanced the room version, as expected.
The test did not rerender merely to clear this intentional stale state.

Queue jobs now own immutable timeline snapshots, preventing a later export from
changing a queued render's inputs. POSIX cancellation terminates the private wrapper,
Unity and encoder process group. Two regression tests exercise snapshot isolation
and cancellation of a real child process without starting Unity. Completed renders
retain their timeline snapshot for reproducibility.

Facing is not validated accuracy: bridged-frame median facing error was 120.7 degrees.
The room was a synthetic box mesh, not a noisy real scan. Untagged rear furniture
appears as coarse blocks, not detailed shelving. The 534 missing track frames retain
last-position/cut behavior; they do not create invented inventory observations.
Unresolved disposal forms and expiry alerts were deliberately left as employee work.
No new full multi-camera or real/simulation transition demonstration was performed.

Metrics and output hashes: [scan-to-simulation.json](validation/scan-to-simulation.json).
The encoded baseline comparison had mean difference 0.09285 because encoding spreads
small raster differences; the full raw-frame comparison is the regression authority.
The current raw difference is within the earlier measured text-raster noise, rather
than a claim of byte-identical video. Only the isolated 8011 test server/database
were used; port 8000 and user inventory were not modified.
