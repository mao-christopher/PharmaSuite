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

If no view qualifies, keep the current POV with an uncertainty label. For a sensor
event at that moment, fall back in order (added 2026-09-26, unvalidated):

1. any confident wrist, then any confident elbow (>= 0.35), in the signal's frame or
   the nearest frame within 5 before or after it, in every camera (selected first,
   then by arm score);
2. the nearest confident wrist within 1 s before or after the signal;
3. no location, so the event goes through employee confirmation.

The hand is matched against the regions of the camera it was found in. Camera
selection never uses future frames; the event fallback may, since recordings are
processed before playback. Nothing uses Unity rig truth. Confidence remains an imperfect visibility proxy.
This demo assumes one technician, cameras that start together, and prerecorded
videos. Unsynchronized live streams and identity tracking across multiple people
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
calibration. For this `shared_zero_origin` clock, loading rejects mismatched clocks or
pose sizes, or a changed calibration version, so rebuild or review the group after
changing calibration. The dashboard selects the camera automatically for a loaded
group and shows its name and arm visibility above the player.

### Uploaded groups

Uploading several videos in the dashboard's upload window writes the same file with
`"clock": "media_time"` and `"source": "upload"`. Its cameras only share a zero time
origin, so their frame rates and lengths may differ; the first camera's clock drives
the player. Its `calibration_version` is `null`, meaning it follows the view's current
calibration, so editing a view in Setup doesn't break the recording. Each camera also
records its `label` (the file name), `width`, `height`, `fps` and `view_confirmed`.
The upload window's second step shows each camera's own frame and view before the
recording is created, so every camera's view is confirmed on upload.

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
