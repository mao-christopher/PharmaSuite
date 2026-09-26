# Stateful simulation validation — 2026-09-26

## Implementation verified

The character follows guarded task states and tracks each bottle's ownership and
location. Unity navigation paths route around furniture. Each 30 Hz tick checks
body capsule movement, arm/hand sweeps, and carried-bottle clearance against the
scene's solid collision geometry. Actions commit only after arrival, reachable
contact, valid ownership, and a supporting surface when applicable.

A newly blocked route stops the actor at its last safe pose and emits no false
completion event. The hollow disposal bin accepts dropped bottles, which remain
in a disposed state and stop at its bottom. Seeking replays intermediate ticks.
Stationary tasks rest in place rather than making a spurious navigation loop.

## Environment and measured video results

- Unity **6000.6.3f1**, built-in rendering and navigation, graphics-enabled batch export.
- Apple M5, ARM64, 16 GB RAM; pose inference explicitly used CPU.
- Python 3.12.14, Ultralytics 8.4.163, `yolo11n-pose.pt`.
- Both final recordings: **1280 × 720, 30 FPS, 44 seconds, 1,320 frames**.
- The existing backend pose helper processed each complete MP4. Default model input
  was 640 (384 × 640 letterboxed tensor). Ten action timestamps were scored using
  the right wrist, threshold 0.5, and exactly one matching region rectangle.

| Measurement | Clean workflow | Occluded-return variant |
| --- | ---: | ---: |
| Decoded/inferred frames | 1320 | 1320 |
| Frames with a detected person | 1320 | 1229 |
| Action timestamps scored | 10 | 10 |
| Correct region identifications | 10 | 9 |
| Abstentions | 0 | 1 |
| Confident wrong regions | 0 | 0 |
| End-to-end inference time | 26.38 s | 27.18 s |
| Measured processing throughput | 50.03 FPS | 48.56 FPS |

The deliberately occluded action is the shelf return at **20 seconds**. Its panel
sits between the camera and technician, outside the technician's physical route.
Clean action-frame wrist confidences ranged from 0.860 to 0.953.
Timings are individual local runs, not hardware guarantees. These scores evaluate
scripted synthetic footage, not real-camera reliability or inventory correctness.

## Automated and visual checks

- Unity imported/compiled the project and rendered both full recordings successfully.
- **2,642 simulation-frame checks** passed across both variants, including endpoints:
  collision-free body paths, arm/bottle guards, bounded walking speed, and ten
  completed legal actions per run.
- Negative tests passed: inserting an aisle blocker after path planning stops the
  actor without an impossible release; releasing without ownership, picking up a
  second bottle while holding one, and picking up a disposed bottle are rejected.
- Counter rest, misplacement state, first/last disposal, bin-bottom landing,
  deterministic seeking, repeated restart, and an unreachable wall target passed.
- **13 Python recording-contract tests passed.** The unchanged backend helper also
  processed both complete recordings; its two existing smoke tests passed in the
  preceding implementation pass.
- Both replay bundles validated their frame clock, 15 actual mock sensor events,
  eight calibrated regions, receiving fixtures, input boundaries, and video hash.
- Rendered frames and YOLO overlays were visually inspected. The technician now
  walks around the counter with a carried bottle rather than cutting through it.

## Evidence and limitations

Per-event metrics are committed in `validation/clean.json` and
`validation/occluded.json`. Generated bundles include those metrics, pose overlays,
separate ground truth, and `evaluator_only/simulation_states.jsonl` containing the
agent/bottle state at every rendered frame. Runtime manifests never reference this
internal state. Reproduce using the render and evaluate tools in the simulator README.

This is a deterministic kinematic demo, not motion-capture animation or a complete
contact-force physics simulator. Collisions use approximate body/limb/bottle volumes
and the **PharmaSolid (layer 8)** geometry. Bottle-to-bottle contact forces, crowds,
and continuous replanning around moving obstacles are not implemented. Add room
obstacles on the solid layer and rebuild paths; unexpected blockers stop the actor.

An earlier smaller occluder could cause YOLO to guess a hidden wrist confidently.
The final variant's abstention does not make confidence alone a reliable occlusion
test. Production temporal event fusion and employee confirmation remain necessary.
Only the lower shelf row is exercised; upper shelves are stocked and calibrated.
Mock sensor events represent accepted actions, not real IMU classification accuracy.
The inventory service, MongoDB, and working dashboard remain future work. The
existing video pose helper retains results in memory, so larger datasets need a
streaming evaluation path.
