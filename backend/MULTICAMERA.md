# Synchronized camera handoff

The dashboard can play a synchronized multi-camera recording while applying one
shared IMU stream to the MongoDB pharmacy. Camera changes never create sensor events.
It uses the same movement-session and event IDs across views and retains the original
medication identity while a bottle is held. Camera and calibration provenance are
saved with the applied event and uncertainty alert.

## Selection rule

At least one complete arm (shoulder, elbow, wrist on the same side) must be inside
the image with all three YOLO confidences >= 0.5. Keep the current camera while it
meets that condition. After 0.2 seconds without a usable arm, switch to an alternate
with at least 0.1 seconds of continuous evidence. Decisions use only frames up to
the current timestamp and remain identical after seeking/restarting.

If no view qualifies, retain the current POV with an uncertainty label and provide
no hand location to inventory. A sensor event then goes through employee confirmation.
The multi-camera path does not use elbow/shoulder-only hand fallback, future frames,
or Unity rig truth. Confidence remains an imperfect visibility proxy. This demo
assumes one technician, aligned clocks, identical FPS/frame count, and prerecorded
videos; unsynchronized live streams and identity tracking across multiple people
are not implemented.

## Recording format

Keep normal `scenario.json`, primary `video.mp4`, `poses.json`, transaction fixtures,
and a single `imu_events.jsonl`. Add `multicam.json`:

```json
{
  "schema_version": 1,
  "clock": "shared_zero_origin",
  "cameras": [
    {"camera_id":"room-camera-01","layout_id":"default","calibration_version":1,
     "video":"video.mp4","poses":"poses.json"},
    {"camera_id":"room-camera-02","layout_id":"side","calibration_version":1,
     "video":"side.mp4","poses":"side-poses.json"}
  ]
}
```

Paths must stay within the recording directory. Each view must have its own saved
calibration. Loading rejects mismatched clocks/pose sizes or changed calibration
versions. Rebuild/review a group after changing calibration. The current upload form
still creates single-camera recordings; use the bundle generator for synchronized
groups. The dashboard automatically selects POV for a loaded group and shows its
camera ID and arm visibility status above the player.

## Reproduce the rendered demonstration

1. Export the same Unity scenario twice with `simulation/tools/render.py`, using
   `--camera front` and `--camera side`, into separate directories.
2. Run `simulation/tools/pose_videos.py` on each recording using the same weights.
3. Run `simulation/tools/multicamera_demo.py --front FRONT --side SIDE
   --front-cv FRONT_CV --side-cv SIDE_CV --output NEW_OUTPUT`.
4. Set `DATA_DIR` to the output's absolute `dashboard-data` path, choose a separate
   `PHARMACY_ID=multicamera-demo`, and run the API with MongoDB configured.
5. Open Recordings, load "Synchronized pharmacy cameras", and play or seek.

The bundle includes clean source views, normalized YOLO tracks, shared events,
calibrations, opening inventory, an automatic POV movie, a two-view comparison,
and a per-frame selection log. It contains no evaluator truth in runtime inputs.
Real/simulated footage transitions remain future presentation work.
