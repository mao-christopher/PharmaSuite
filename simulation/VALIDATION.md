# Simulation validation

Measured results for the Unity pharmacy simulation and the pipeline that consumes
its footage. All numbers come from **synthetic, scripted footage** of one
technician. They show the pipeline works end to end on rendered pixels; they do
not establish accuracy on real pharmacy cameras. Raw metrics are in
[`validation/`](validation/).

Measurements were taken on 2026-09-26 with Unity 6000.6.3f1 on an Apple M5
(ARM64, 16 GB), Python 3.12, Ultralytics 8.4.163 and YOLO11n-pose on CPU.

## Scene, motion and collision checks

The scripted recording is 106 s at 1920 × 1080 / 30 FPS (3,180 frames): a 40 s
aisle walkthrough followed by a 66 s bottle-handling sequence. The fixed camera
and eight configured regions use calibration `pharmacy-v3`.

- **6,362 fixed-frame samples** passed across the ordinary and optional-occluder
  variants: blocked routes, bottle ownership, disposal, restart, deterministic
  seek, turns, foot planting and limb/bottle collision guards.
- Foot IK error stayed within the 1 cm assertion and stance-foot drift within the
  3 mm assertion (both measured near floating-point precision). Turns are bounded
  to 180 °/s. This is procedural animation, not motion capture or contact-force
  physics. Metrics: [`animation-checks.json`](validation/animation-checks.json),
  [`detail-checks.json`](validation/detail-checks.json).
- All presentation and source MP4s decoded to 3,180 frames at 30 FPS, and all 15
  mock sensor events matched the CV observation stream in order and on the video
  clock.

## YOLO pose on the rendered footage

Inference size 960, right-wrist confidence threshold 0.5, point-in-region check at
each scripted action time.

| Measurement | Result |
| --- | ---: |
| Frames inferred | 3,180 |
| Frames with a detected person | 2,636 |
| Bottle-action timestamps scored | 10 |
| Correct action-region assignments | 10 |
| Abstentions / confident wrong regions | 0 / 0 |
| Inference time (with presentation encoding running concurrently) | 192.03 s (16.56 FPS) |

Ten front-bank, counter and disposal actions are a feasibility check, not a
general accuracy figure. YOLO can miss hidden joints or guess them confidently.
Per-event evidence: [`detail-cv.json`](validation/detail-cv.json),
[`detail-presentation.json`](validation/detail-presentation.json).

## Simulation X-ray skeleton

The X-ray view draws Unity rig truth through walls and is labeled as such. It is
evaluator/presentation-only and never feeds CV or inventory. All 3,180 frames
contain all 16 rig joints, including 450 frames where every joint is hidden behind
room geometry and 594 frames with partial occlusion. Visibility flags describe room
geometry only, not self-occlusion. Metrics: [`detail-xray.json`](validation/detail-xray.json).

## MongoDB inventory and multi-camera handoff

A second (side) camera rendered the same scenario, and YOLO ran separately on each
camera. The causal arm-confidence selector switched front → side at 6.033 s and
side → front at 33.833 s. 75 frames had no reliable arm in the selected view and
stay marked uncertain. The selector uses no Unity truth.

All 10 bottle-action regions were correct in the MongoDB-backed inventory replay,
with no abstentions or wrong regions. Replaying and restarting the controller left
inventory and applied-event counts unchanged. Metrics:
[`multicamera-handoff.json`](validation/multicamera-handoff.json),
[`multicamera-inventory.json`](validation/multicamera-inventory.json),
[`multicamera-videos.json`](validation/multicamera-videos.json).

Confidence is a visibility proxy, not proof. Real-camera handoff is unmeasured.

## Scene import, floor track and re-enactment

The scripted demo's static scene geometry was imported through the API as a room,
its footage processed like a real upload, and the result re-enacted in Unity from
the dashboard's decisions. No simulator truth reached inference, inventory, the
floor track or the re-enactment planner; rig truth was used only to score the
floor track.

| Check | Result |
| --- | --- |
| Imported/generated regions | 8 / 8, none skipped |
| Camera registration | RMS 0.00057 px; max 0.0010 px |
| Hand-drawn vs generated region decisions | 10 unchanged; no new abstentions |
| Shelf rebuild | `rebuild/2`; 3 boards per unit at 0.41, 0.87, 1.59 m |
| Floor track placed | 2,646 / 3,180 frames (83.21%) |
| Position error | median 4.75 cm; p90 9.53 cm; max 27.57 cm |
| Facing error | median 6.3°; p90 42.23°; max 178.2° |
| Facing errors over 90° | 41 frames |
| Re-enactment camera alignment | 64 corners; max 0.0002 px |
| Re-enactment actions | 7 shown; 2 highlight-only; 1 state-follow fallback |
| Collision holds / refused reaches | 0 / 0 |
| Out-of-view track / cuts / blends | 534 frames / 2 / 0 |
| Raw image regression vs baseline | mean abs. difference 0.000802 / 255 |

The two counter events are shown as highlights because the permitted 0.3 m step
does not reach them; the re-enactment never animates a grasp it cannot reach.
Facing is the weak point of the floor track. A full product render through
`POST /api/recordings/{name}/render` decoded all 3,180 frames for both `sim.mp4`
and `side_by_side.mp4`; re-rendering the same inputs reused the result, and a
region edit marked it stale. Metrics: [`scan-to-simulation.json`](validation/scan-to-simulation.json).

## Limits

- One synthetic technician and one scripted workflow. Rear shelf banks are
  collidable scenery without pickup tasks.
- The walkthrough is authored navigation, not autonomous search. Unexpected
  blockers stop the actor; there is no dynamic replanning.
- The optional-occluder variant passed the motion checks but was not separately
  rendered and scored through CV.
- Per-action clips (AGENTS.md rule 13) are not yet exported.
- Nothing here has been measured on real pharmacy footage.
