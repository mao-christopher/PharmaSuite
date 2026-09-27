# PharmaSuite design and status

This document records the agreed product behavior, the architecture, what is built,
and what is still open. [AGENTS.md](AGENTS.md) lists the non-negotiable rules in
short form; this document explains them. Measured simulation results are in
[simulation/VALIDATION.md](simulation/VALIDATION.md).

## Status

**Built**

- **Unity simulation** (`simulation/`): a pharmacy room with a rigged technician who
  handles bottles through explicit task and ownership states, collision-aware paths
  and swept collision guards. It renders offline footage with synchronized mock IMU
  events, calibration, synthetic fixtures and evaluator-only ground truth.
- **Backend** (`backend/`): a FastAPI service that replays recordings on a
  server-side media clock, extracts YOLO skeletons, associates each pickup/put-down
  signal with a region, and applies the inventory rules below exactly once per
  signal. State lives in MongoDB ([backend/MONGODB.md](backend/MONGODB.md)).
- **Dashboard** (`backend/dashboard/`): React UI with the player, signal log,
  notifications and confirmations, recordings library, inventory, prescriptions,
  shipments, room setup and live capture.
- **Multi-camera recordings** with automatic switching to the camera that sees the
  arm ([backend/MULTICAMERA.md](backend/MULTICAMERA.md)).
- **Room scan, camera registration and floor track**: import a LiDAR scan, tag
  shelves in 3D, register cameras, and place the technician on a floor plan.
- **Unity re-enactment** of any recording from the dashboard's decisions, shown as
  a labeled "Simulation" or side-by-side player source.
- **Live wristband capture**: a BLE wristband ([wristband/](wristband/)) and a
  browser camera, keeping only a short clip around each event.
- **Shipment intake and stocking** for eight synthetic supplier formats
  ([backend/SHIPMENTS.md](backend/SHIPMENTS.md)).
- **Stock suggestions**: running low, last bottle, run-out forecast and expiring
  batches.

**Not built or not validated**

- Region association (distance threshold, joint fallbacks, camera switching) has
  not been scored against ground truth beyond one scripted clip.
- No accuracy has been measured on real pharmacy footage. The live flow has been
  tried by the team but not measured.
- Per-action clip export (AGENTS.md rule 13) is not done.
- Live mode uses one camera; live multi-camera handoff is not built.
- Only the most confident person per frame is tracked.
- The mixed real/simulated presentation edit is separate work.

## Scope of the demo

| Area | Decision |
| --- | --- |
| People and handling | One active technician; one bottle handled at a time |
| Prescription | One active prescription, one medication + strength |
| Cameras | Fixed, calibrated cameras; with several, switch to the one that sees the arm |
| Shelf map | Shelf boxes tagged on a 3D room scan, projected into each registered camera |
| Medication identity | Inferred from the pickup region, not label OCR |
| Sensor input | Pickup/put-down events from the wristband, a timestamps file, or the simulator |
| Tablet stock | Pooled across all bottles of each medication + strength |
| Bottle stock | Shelf counts and total undisposed bottles tracked separately |
| Dispensing | Deduct once, at confirmed fill or finalized payment/receipt |
| Disposal | One bottle per disposal; employee identifies the stock and quantity |
| Ambiguity | Dashboard confirmation; location stays uncertain until resolved |
| Expiry | Receiving records carry expiry; alerts tell employees where to look |

Out of scope: automatic shelf segmentation, several technicians or prescriptions at
once, and physical pill counting.

## Architecture and information boundaries

```text
Camera footage (real, live, or Unity render)
  |-- YOLO pose observations (per camera, with calibration version) ---+
Pickup/put-down signals (wristband, file, or mock IMU) ----------------+--> region association
Camera regions (projected from 3D shelf tags) -------------------------+          |
Receiving / shipments / prescriptions ---------------------------------> inventory engine
Employee confirmations and corrections --------------------------------> inventory engine
                                                                                  |
                                                                         MongoDB + dashboard
Unity ground truth --> offline evaluator only
```

The runtime sees only video, signals, configured regions and business
transactions. Signals may identify their source device but never carry a shelf,
medication or correct outcome. Unity transforms, object IDs, rig joints and scripted
outcomes are for animation and offline evaluation only.

YOLO pose gives body keypoints, not proof that a bottle was grasped or released;
the signals supply the action timing. Because one bottle is handled at a time, an
accepted action changes a count by exactly one. This is not visual counting of
arbitrary bottle piles.

## Stock and workflow rules

### Receiving

A receipt records ID, medication + strength, bottle count, tablets per bottle,
expiry, lot and received date. Receipts are kept for expiry and disposal matching;
per-bottle tablet counts are never invented.

Tablet balance = received tablets − completed prescription quantities − recorded
discards + audited corrections. Expiry raises an alert; it never subtracts stock.

### Bottle movement

```text
ON_DESIGNATED_SHELF --pickup--> HELD --release at counter--> AT_COUNTER
AT_COUNTER --pickup--> HELD
HELD --release at designated shelf--> ON_DESIGNATED_SHELF
HELD --release at other shelf--> MISPLACED (alert)
MISPLACED --pickup for correction--> HELD
HELD --release in disposal region--> DISPOSED (open form)
Any ambiguous transition --> NEEDS_CONFIRMATION
```

- A movement session links the bottle's medication and home shelf to every later
  event, including across a counter stop.
- Pickup lowers the source shelf count; total bottles are unchanged while held or at
  the counter.
- A wrong-shelf release raises the destination's physical count and records the
  bottle as misplaced. It never adds to the destination medication's stock, and the
  bottle keeps its original identity.
- A correct return restores the home shelf count. Disposal lowers total bottles once.
- A hand passing through a region without a release does nothing.
- When a shelf holds more than one kind of bottle (its own stock plus a misplaced
  bottle, or several misplaced bottles), a pickup there asks the employee which
  bottle it was (`which_bottle`). Stock waits for the answer. A shelf holding only a
  misplaced bottle is picked up without asking.

### Prescriptions

A transaction has an ID, medication + strength, quantity and status. Tablets are
deducted on the first confirmed fill or payment/receipt for that ID and never again
for the same ID. Arrival, cancellation before completion, and bottle movement do
not deduct. Corrections after completion are explicit adjustments. Prescriptions can
be added by form or imported from CSV/JSON (all rows validated first).

### Disposal and expiry

A release in the disposal region removes one bottle and opens a form listing the
receiving records for that medication (strength, expiry, lot, receipt). CV does not
know which physical bottle it was; the employee identifies it.

- An entered quantity is deducted once.
- With several bottles before disposal, a blank quantity means an empty bottle
  (zero). The assumption stays visible and can be corrected.
- With exactly one bottle before disposal, a blank quantity discards the whole
  pooled balance.
- A later entry applies only the difference from what was already deducted.
- Negative quantities are rejected. Entries above recorded stock, or tablets left
  after the last bottle is gone, are kept as reconciliation issues.
- Zero total bottles raises out-of-stock. Zero on the shelf with a bottle at the
  counter is a different, non-alerting state.
- Expiry alerts name the medication, strength and expiry. An alert clears only when
  disposals are matched to the expired receipt; other expired bottles keep it open.
- Employees can also dispose of bottles directly from a batch (for example expired
  stock they found), with the same quantity defaults.

## Signals, recordings and region association

### Inputs

Every input carries `schema_version`, `event_id`, `session_id` and a timestamp.
Signals add `event_type` (pickup/release) and `media_time_ms`. CV observations
carry frame time, keypoints and confidences, camera ID and calibration version.
Association uses media time, never wall-clock processing speed, so playback speed
cannot change results.

A recording is one or more videos plus a timestamps file (CSV, JSON or JSONL;
`time_s` or `media_time_ms`; `pickup`/`grab` and `release`/`drop`). Simulator
`movement` samples are skipped. On upload, YOLO11n-pose runs over every frame in the
background at 960 px on the long side (`POSE_IMGSZ`) and stores the most confident
person's 17 keypoints per frame. The stored video is never resized.

### Where the hand is at a signal

Each step runs only if the previous one found nothing:

1. The selected camera's complete arm (shoulder, elbow and wrist at confidence ≥ 0.5).
2. Any confident wrist, then any confident elbow (≥ 0.35), in the signal's frame or
   up to 5 frames either side, in every camera (selected camera first).
3. The nearest confident wrist within 1 s before or after the signal.
4. Nothing: the employee confirms the location.

The hand is matched against the regions of the camera it was found in. The nearest
region wins: distance 0 inside a polygon, otherwise distance to its edge as a
fraction of the frame diagonal. Pickups consider shelves and counters; releases
also consider disposal. Beyond `MAX_REGION_DISTANCE = 0.06`, or on a tie between
overlapping regions, the employee confirms. A release arriving while its pickup is
unconfirmed waits and is applied after confirmation. All of these thresholds are
unvalidated demo defaults.

### Recordings and live state

- Inventory is one live state that carries across recordings. A recording's signals
  apply the first time the playhead passes them, or all at once with **Apply**.
  Applied event IDs are recorded, so replays, seeking, restarts and repeated Apply
  never apply a signal twice or undo one.
- Every upload is a new set of real events, even identical footage uploaded twice.
  Upload order is taken as the order they happened; Apply offers to catch up earlier
  unapplied uploads first. Mistaken uploads are fixed by correction or reset.
- Deleting a recording removes its files but keeps the stock changes it made.
- **Reset to opening stock** restores the catalog's opening batches and clears
  alerts, disposals, deductions and applied markers.
- An append-only history records signals, shipments, disposals, confirmations,
  prescription changes, uploads, deletions, setup saves and resets.

### Uploads

The upload window handles everything before a recording exists: choose videos (they
upload to a draft immediately), mark pickup/put-down times on the preview or load a
file, then review each camera's first frame with its most similar saved view. Edited
cameras are saved as new or updated views. Finishing validates everything first, so
a rejected request keeps the draft. Abandoned drafts are deleted after 24 h. View
suggestion (thumbnail correlation plus colour histogram) is an unvalidated
heuristic; the employee always confirms.

## Cameras, rooms and the floor track

- **Catalog and views.** Medications and opening stock live in `data/catalog.json`.
  Each `data/layouts/<view>/layout.json` holds one camera view's frame size, photo
  and regions. A shelf's ID is `shelf_<medication key>` in every view. Each
  medication has exactly one shelf; any number of counter and disposal regions are
  allowed.
- **Room scan.** `POST /api/rooms` imports an uncompressed GLB from a LiDAR scan
  app. The scan is levelled (tilt ≤ 10° corrected, floor at y = 0), never rescaled.
- **3D tags.** Shelf, counter and disposal boxes are tagged on the scan. These are
  the only hand-made regions.
- **Registration.** Clicked point pairs (≥ 6) between a camera photo and the scan
  solve the camera (focal-length search, SQPnP, LM refinement). The page reports
  reprojection error and draws the overlay. No lens distortion is modelled.
- **Generated regions.** Each registered camera's 2D polygons are projected from the
  3D tags; hand edits to generated views are refused. A camera with no registration
  has no regions, so its events need confirmation. The view's calibration version
  rises whenever its polygons change.
- **Floor track.** The technician's floor position comes from the ankle midpoint
  (or hips when feet are hidden) through the registration; facing is fitted from
  shoulders and hips. Jumps are rejected, short gaps bridged, and positions pushed
  off furniture. When the technician is out of view, nothing is inferred; the map
  greys out the last position.

Rooms, tags, registrations and floor tracks are presentation data. Inventory reads
only the generated 2D regions.

## Unity re-enactment

`GET /api/recordings/{name}/timeline` bundles a recording's floor track, decisions
(decided/confirmed/pending/not applied), starting bottle counts, the rebuilt room
and the cameras. The **Render simulation** button queues a Unity render from it:

- The room is rebuilt from the scan and tags (walls, shelving with boards, counter,
  bin and blocks), never from the raw scan mesh.
- The technician follows the floor track. Each reach starts 0.6 s before its signal
  and may step at most 0.3 m; if the contact is still out of reach, the region is
  only highlighted and no grasp is animated.
- Bottles keep their medication. Misplaced bottles are red, counter placements
  amber, disposed bottles grey, and pending decisions translucent with a "?".
- Output is `sim.mp4`, `side_by_side.mp4` and a manifest. Renders are keyed on their
  inputs, reused when unchanged, and marked stale after corrections. Every frame is
  labeled "Simulation re-enactment (not camera footage)".
- Rendering needs a licensed Unity editor configured with `UNITY_PATH`.

The re-enactment uses dashboard evidence only and emits no sensor events.

## Live wristband capture

- **Sources.** One camera at a time, chosen in Chrome (webcam or an iPhone via
  Continuity Camera), plus a BLE wristband through Web Bluetooth.
- **Privacy.** Frames live only in a rolling ~10.5 s in-browser buffer. Only the
  window from 9 s before to 1 s after each band notification is uploaded and kept
  (the band notifies roughly 4–5 s after the action). Nothing is recorded between
  events, and no pose runs on the continuous feed.
- **Analysis.** Each clip becomes a recording (`live-<event_id>`) with its own poses
  and one signal, applied once on arrival. Replaying a clip is review only.
- **Continuity.** A pickup opens a movement; the next put-down closes it, so the
  bottle's identity carries across clips. Out-of-sequence events (pickup while
  holding, put-down with nothing held) are recorded as ignored with no clip.
- **Uncertainty.** Several people, missing frames, a frame shape that doesn't match
  the view, or no confident wrist raise the normal uncertainty alert with the clip.
- **Latency.** About 10 s from notification to dashboard update on a laptop CPU.
- **Dev mode.** `?dev=1` lets Space send pickups and put-downs without a band.

Not validated: real notification-to-contact timing, end-to-end latency on other
hardware, and automatic accuracy on live footage.

## Shipments

Supplier documents (canonical JSON, CSV, XML, a synthetic X12-856-style EDI profile
and a JSON webhook) are reviewed and imported on the Shipments page. Starting a
shipment creates lot/expiry receipts and staged, off-shelf stock. The employee picks
the line for each incoming pickup; CV decides where it was placed. Finishing requires
every bottle to be shelved, documented short or disposed. See
[backend/SHIPMENTS.md](backend/SHIPMENTS.md).

## Stock suggestions

Derived from live stock on every read, never stored:

- **Running low:** tablets at or below the reorder point (default 20% of opening stock).
- **Last bottle:** one bottle left.
- **Forecast to run out:** fewer than 7 days of stock at the last 14 days' average.
- **Expiring soon:** a batch with bottles left expires within 30 days (urgent within 7).

Dismissed suggestions return when they change or escalate.

## Acceptance scenarios

| Scenario | Expected outcome |
| --- | --- |
| Pickup, counter rest, pickup, return | Shelf count falls then restores; total unchanged; no alert |
| Wrong medication pickup | Warn against the active prescription |
| Wrong-shelf release then correction | Alert; identity preserved; counts corrected; alert resolved |
| Hand passes a wrong shelf without release | Nothing |
| Release near a boundary or occluded | Uncertain; employee confirms; no guess |
| Empty disposal, other bottles remain | One bottle removed; blank quantity deducts zero |
| Partial or expired disposal | One bottle removed; quantity deducted once; expiry record matched |
| Last bottle disposed, no quantity | Bottles and tablets reach zero; out-of-stock alert |
| Late quantity or duplicate disposal event | Correct net deduction; no repeat bottle decrement |
| Confirmed fill then payment | One deduction |
| Expiry without disposal | Alert; quantities unchanged |
| Missing/out-of-order signal, restart, replay | Pending or recoverable; no duplicate mutation |

The deterministic business-rule scenarios are covered by the test suite. On CV
clips, report correct assignments, confident errors and abstentions separately, plus
throughput. Passing simulated clips does not establish real-camera accuracy.

## Risks and open work

- The wrist is a proxy for the bottle. Shelf depth, which hand holds it, and
  occlusion are not modelled; a nearest wrist may be up to 1 s from the signal.
- A single 2D camera can see overlapping shelf regions or lose the hands.
- Missed events leave counts wrong until corrected; unresolved counts stay visible.
- Tablet balances are estimates; unrecorded waste creates discrepancies CV cannot fix.
- A second person in view can take over the track.
- Floor-track facing flips front/back at times (see VALIDATION.md).
- The live region rule scans the whole 10 s clip, so a sustained wrist in a second
  region makes an event uncertain; narrowing it needs measured band latency.
- A missed put-down leaves the bottle held; there is no timeout or manual put-down.
- The single MongoDB document must be split into collections for long-running use.
- Next steps: per-action clip export, live multi-camera handoff, person tracking
  across frames, lens distortion if overlays drift, stocking through live events,
  and measurement on real footage.
