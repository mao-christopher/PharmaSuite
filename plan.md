# Pharmacy inventory demo plan

## Status and objective

The agreed product requirements below remain the implementation baseline.
Status as of 2026-09-26, after PRs #2 (Unity simulation) and #3 (MongoDB, camera
handoff, and the dashboard branch) merged into `main`:

**Implemented**

- `simulation/`: a Unity room with a textured, rigged technician. It has stateful
  bottle handling with navigation and collision guards, and exports offline video.
  Mock sensor events, calibration, synthetic receiving/prescription fixtures, and
  evaluator-only ground truth are exported alongside. Measured results are in
  [simulation/VALIDATION.md](simulation/VALIDATION.md).
- `backend/`: a FastAPI service that replays uploaded recordings on a server-side
  media clock and extracts YOLO skeletons in the background. At each signal it
  matches the hand to a region and applies the inventory rules below once per signal.
  State lives in MongoDB (see `backend/MONGODB.md`).
- `backend/dashboard/`: a React dashboard with the player, signal log,
  notifications, recordings library, inventory, and camera-view setup.
- Multi-camera recordings. They come from the simulator's bundle format or from
  uploading several videos. The player switches to whichever camera sees the arm
  (see `backend/MULTICAMERA.md`).
- A single upload window: videos, pickup and put-down times, then each camera's shelf
  boxes. Upload closes the window and extracts skeletons in the background.
- Proactive stock suggestions: low stock, last bottle, run-out forecast, and
  batches expiring soon.

**Not implemented or not validated**

- Region association has not been scored against ground truth. That covers the
  distance threshold, the joint fallbacks, and camera switching. The 10/10 result in
  `simulation/VALIDATION.md` is a feasibility check on one scripted clip.
- No real pharmacy footage has been processed.
- Per-action clips, the real IMU adapter, and real-to-simulation re-enactment are
  not implemented. The same goes for the mixed real and simulated presentation.
- The terminal in the Unity scene is a visual prop.

Demonstrate medication pickup, valid temporary counter placement, correct/incorrect
return, disposal, expiry notification, and transaction-based tablet inventory using
actual CV on prerecorded synthetic room-camera footage plus synchronized mock IMU
events. The centralized cashier dashboard displays the pharmacy's full configured
inventory and prompts employees when input is needed. MongoDB stores state/history.

## Agreed first-demo constraints

| Area | Decision |
| --- | --- |
| People and handling | One active technician; one bottle handled at a time |
| Prescription | One active prescription, one medication + strength |
| Camera | Fixed, calibrated cameras; with several, the player switches to the one that sees the arm |
| Shelf map | Employee-drawn polygons on the dashboard Setup page (see "Shared camera layout") |
| Medication identity | Infer from the configured pickup region, not label OCR |
| Rendering | Moderately realistic human and motions; Unity 6000.6.3f1 |
| Execution | Offline-rendered footage replayed through actual YOLO pose inference |
| Sensor input | Synchronized mock pickup/movement/release events |
| Tablet stock | Pool across all bottles of each medication + strength |
| Bottle stock | Track shelf counts and total undisposed bottles separately |
| Dispensing | Deduct at confirmed fill or finalized payment/receipt, once |
| Disposal | Detect one-bottle disposal; employee identifies candidate stock and quantity |
| Ambiguity | Dashboard confirmation; keep location uncertain until resolved |
| Expiry | Receiving records include expiry; notify employees to inspect that section |

## Architecture and information boundaries

```text
Unity scene + animation
  |-- camera recording ----------------------> YOLO pose observations --+
  |-- mock sensor events (same media clock) ----------------------------+--> event association
  |-- ground truth --> offline evaluator only                           |          |
Preconfigured camera regions ------------------------------------------+          v
Mock receiving/prescription transaction feed --------------------------> inventory service
Employee confirmations and corrections -------------------------------> inventory service
                                                                                |
                                                                         MongoDB + dashboard
```

The inference pipeline and event/inventory service are Python (FastAPI); the
dashboard is React (Vite). Recordings are replayable files; live streaming is future work.

The simulator may know exact bottle and joint transforms for animation and scoring.
The runtime receives only rendered video, legitimate mock sensor events, configured
regions, and business transactions. A sensor ID may identify a source but must not
secretly encode the medication, shelf, or correct placement outcome.

YOLO pose provides body keypoints, including wrists; it does not by itself prove
that a bottle was grasped, released, or counted. This demo assumes the mock IMU
adapter supplies those action events. Real IMU action recognition is unvalidated
separate work. The one-bottle constraint allows an accepted action to change the
count by one; do not describe this as visual counting of arbitrary bottle piles.

## Simulation agent behavior

The Unity character has explicit idle/walk/carry/reach/pick/place/counter/dispose/
blocked states and tracks whether a bottle is held, resting, misplaced, or disposed.
Navigation paths respect the room and furniture; swept body, arm, and carried-bottle
checks guard each fixed simulation tick. Tasks commit only after arrival, reachable
contact, valid ownership, and (for placement) a supporting surface. A blocked action
stops the actor and emits no completion signal. Export rejects an incomplete run.
The scenario currently takes 106 seconds: a 40-second collision-checked walkthrough
through two aisles, then 66 seconds of bottle handling. Three physical shelf banks
provide realistic depth and occlusion; only the front bank has mapped medication
regions and handling tasks. Rear banks are currently reserve-stock scenery.
Separate raw, CV-overlay, skeleton-only, and comparison videos demonstrate the
render-to-inference path, with per-frame keypoint/confidence data. CV skeleton videos
use YOLO estimates from pixels. The user also requires a separate always-visible
simulation X-ray skeleton from Unity rig truth, explicitly labeled and kept out of
CV/inventory inputs. Its projected joints are exported only under evaluator_only.
Planted-foot IK, predictive steps, smoother turns, arm swing, and eased reaches
improve the procedural animation; a 1.5× slower handling schedule allows natural pacing. This is scripted traversal,
not autonomous semantic search. A single 2D camera cannot reliably distinguish
front/rear depth or recover hidden hands from pose alone.
Agent/world state belongs only to the simulator and evaluator; the production CV
pipeline still receives rendered camera footage and abstract mock IMU events.

## Stock and workflow rules

### Receiving and initialization

Ingest a receipt ID, medication/strength key, bottle count, starting tablet quantity,
expiry date, and available lot/manufacturer information. Preserve bottle-level
receiving references or batch counts for expiry and disposal selection without
inventing individual remaining tablet counts. Seed initial per-region bottle counts;
the demo updates these from events rather than independently recounting every frame.

Tablet balance = received tablets - completed prescription quantities - recorded
discard quantities + explicit audited corrections. Expiration is an alert, not an
automatic subtraction. Physical stock and expired stock should be distinguishable.

### Bottle movement

Suggested state machine:

```text
ON_DESIGNATED_SHELF --pickup--> HELD --release at counter--> AT_COUNTER
AT_COUNTER --pickup--> HELD
HELD --release at designated shelf--> ON_DESIGNATED_SHELF
HELD --release at other shelf--> MISPLACED (alert)
MISPLACED --pickup for correction--> HELD
HELD --release in disposal region--> DISPOSED (open form)
Any ambiguous transition --> NEEDS_CONFIRMATION
```

Keep a movement-session record linking the known source medication to later events.
Counter placement retains that session. For the first demo, do not start handling a
second bottle while the first is parked at the counter. Wrong placement retains
the original identity and opens a correction workflow before normal picking resumes.

Pickup reduces the physical source shelf count by one. Counter/held state leaves
total bottle inventory unchanged. A confidently wrong return increases the actual
destination's physical count while recording that its content is misplaced; it must
not increase the destination medication's stock. Correct return restores its shelf
count. Disposal reduces total bottles once; do not subtract the shelf count again
if it was already reduced at pickup.

At a pickup or release timestamp, use a short surrounding window of visible wrist
positions and keypoint confidence to associate a region. Tune the window and
thresholds on recorded clips. Use normalized coordinates or explicit image-size
transforms so resizing cannot move region boundaries. A wrist is only a proxy for
bottle location: test shelf depth, wrist/bottle offsets, neighboring boxes, occlusion,
and both hands. Ambiguous source, destination, or timing requires confirmation.
Do not choose a nearest shelf unconditionally. Unsupported or missing event
sequences remain pending for reconciliation rather than causing a guessed decrement.

### Prescription deductions

Mock incoming data supplies transaction ID, medication/strength, quantity, and status.
Pickup can warn if its source differs from the active prescription. Deduct only on
the first qualifying finalized event for that ID, whether confirmed-fill or payment/
receipt. A later equivalent completion event does not deduct again. Transaction
arrival, cancellation before completion, and movement do not deduct tablets.
Post-completion corrections use explicit adjustments; never silently rewrite history.

### Disposal and expiration

An accepted release in the trash region removes one bottle and opens a form. Show
candidate receiving records for the active medication, with strength, expiry, lot,
and receipt reference where available; do not claim CV knows the expiry of the
handled bottle. Employee selection resolves which received bottle/batch was removed.
The detected count is prefilled as one, with correction available for erroneous events.

- Employee-entered quantity is deducted once and recorded with the disposal event.
- With multiple bottles present before disposal, absent quantity defaults to zero
  (assumed empty). Show that default and keep identification pending until answered.
- With exactly one bottle present before disposal, absent quantity removes the
  entire recorded pooled balance. Snapshot the pre-disposal count for this rule.
- Workers must document nonempty disposal. The zero default is a fallback, not
  evidence that the bottle was actually empty.
- If an entry arrives after a default, apply only the difference from the previous
  deduction. Do not subtract the full entry a second time.
- Reject negative quantities and flag entries exceeding recorded stock. If the
  last bottle is gone but an explicit smaller disposal quantity leaves tablets,
  retain the discrepancy for reconciliation rather than silently zeroing it.
- Trigger an out-of-stock alert when total bottle count reaches zero. A bottle
  temporarily off shelf must not create that alert. Separately flag zero tablets
  or inconsistent tablet/bottle balances.
- Generate expiry alerts against received stock using a configured pharmacy clock.
  Employee identification on disposal decrements the matching expired batch count;
  keep the alert open if other expired bottles remain. No selection means the expiry
  association is unresolved, even though a physical disposal has been detected.

Exact expiry-date boundary/timezone and form dismissal behavior must be explicit
configuration/UX decisions before implementation. The default quantity may be applied
provisionally while the dashboard keeps the identification task visible.

## Proposed event and storage contracts

Every input carries `schema_version`, `event_id`, `session_id`, and a timestamp.
Mock sensor records add `event_type` (pickup/movement/release), `sensor_id`, and
`media_time_ms`; do not include destination or drug answers. CV observations carry
frame/media time, keypoints/confidences, camera ID, and calibration version.

Replay manifest: video path/hash, dimensions, FPS/timebase, sensor-event file,
calibration file/version, initial inventory fixture, and business-event fixture.
For constant-FPS video, derive media time from frame index/FPS; preserve presentation
timestamps if using variable-FPS input. Associate by media time, never processing
wall-clock speed. Store receipt/expiry dates separately from replay-relative time.

Proposed MongoDB collections:

| Collection | Responsibility |
| --- | --- |
| medications | Stable medication + strength key and display fields |
| regions | Camera polygons, region type, designated medication, calibration version |
| receipts | Received bottles/batches, initial quantities, expiry, lot/reference |
| inventory | Pooled tablet totals, total bottles, per-location counts, uncertainty |
| events | Raw inputs, derived actions, evidence, deduplication IDs |
| movement_sessions | Source medication, current state/location, pending confirmations |
| transactions | Prescription status and whether its stock deduction was applied |
| disposals | Candidate/selected receipts, quantity/default, deductions and corrections |
| alerts | Misplacement, uncertainty, expiry, out-of-stock, and reconciliation status |

### Shared camera layout (implemented)

Decided 2026-09-26. The camera is fixed, so one layout is annotated once and shared
by every recording. `data/layouts/<layout_id>/layout.json` holds frame dimensions,
`calibration_version`, medications, region polygons (normalized 0-1 vertices), and
preset received batches (bottle count, units per bottle, expiry, lot, received date).
Each recording folder in `data/scenarios/` holds its own video/IMU/transaction files
plus `scenario.json` naming its `layout_id`.

- A medication is one drug + strength (`AMOXICILLIN_500MG`); each has exactly one
  shelf and each shelf holds exactly one medication. Any number of counter and
  disposal regions are allowed. The API rejects layouts that break these rules.
- The preset batches are the opening stock: pooled tablets = sum of bottles x units
  per bottle, and all bottles start on the shelf. They seed live inventory the first
  time and on an explicit reset.
- Saving from the Setup page bumps `calibration_version`. By default live inventory
  is kept: new medications start with their opening stock, and bottles counted on a
  deleted or reassigned shelf (not explained by a misplaced bottle) move to their
  medication's current shelf. The save dialog can instead reset inventory.
- The Setup page imports a photo taken from the camera angle as the annotation
  background (`background_image`); the layout takes the photo's frame size.

### Recordings and hand-to-region association (implemented)

Decided 2026-09-26. Demo input is an uploaded video plus a timestamps file (CSV,
JSON, or JSONL; `time_s` or `media_time_ms`; `pickup`/`grab` and `release`/`drop`),
standing in for wearable IMU signals. Each pickup opens a movement session and the
next release closes it. On upload, YOLO11n-pose runs over every frame in the
background and stores the most confident person's 17 keypoints in `poses.json`.
Inference runs at 960 px on the long side (`POSE_IMGSZ`, decided 2026-09-26; was the
640 default), which finds distant people more often at roughly twice the processing
time. The stored video is never resized, and `poses.json` records the size used.

- At each signal, both wrists are taken from the frame at the signal's media time,
  or the nearest frame within 5 before or after it, when confident (conf >= 0.35).
  Fallbacks when no wrist qualifies are under "Multi-camera uploads, hand fallback,
  upload order, and stock suggestions" below.
- The region nearest either wrist wins: distance 0 inside a polygon, otherwise
  distance to its edge as a fraction of the frame diagonal. Pickups consider shelves
  and counters; releases also consider disposal regions.
- `MAX_REGION_DISTANCE = 0.06` is an arbitrary, unvalidated constant. Farther than
  that, no confident wrist, or a tie between overlapping regions requires employee
  confirmation, which then applies the chosen region. A release that arrives while
  its pickup is unconfirmed is held and applied after confirmation.
- Release on the home shelf: no alert. Other shelf: misplacement alert. Counter:
  valid parking. Disposal: bottle removed and disposal form opened.
- Per user direction, a pickup from a shelf holding a misplaced bottle is assumed to
  be that bottle (the correction). It keeps its original medication, and putting it
  down anywhere closes the earlier misplacement alert.
- Receiving stock adds a batch to live counts (bottles go straight onto the shelf)
  without resetting.
- The replay clock runs server-side on real elapsed time, independent of viewers,
  and stops at the end of the clip. State changes are pushed over the WebSocket, and
  the dashboard shows a per-signal log of the decision.
- Measured on this dev Mac (CPU): YOLO11n-pose extraction ran at about 17 frames/s
  including model load. This is processing speed only, not pose accuracy.

### Recording library and persistent inventory (implemented)

Decided 2026-09-26 (defaults chosen without a review round; revisit if wrong).
Uploaded recordings are kept in `data/scenarios/upload-*` with their metadata
(label, original file names, upload time, duration, frame size, calibration version)
and listed on a Recordings page. Inventory is one live state that carries across
recordings instead of resetting per recording.

- Live state was first saved to `data/state/pharmacy.json`. It now lives in one
  revision-checked MongoDB document per pharmacy; the JSON file is imported once
  and kept as a backup (see "MongoDB workflow and camera handoff" and
  `backend/MONGODB.md`).
- A recording's signals change inventory the first time the playhead passes them, or
  all at once with "Apply". Applied event IDs are recorded per recording, so replays,
  seeks backward, restarts, and repeated Apply calls never apply a signal twice and
  never undo one. Movement sessions are namespaced `<recording>:<session>` because
  every upload numbers its sessions from `sess_001`.
- Superseded 2026-09-26: uploads are assumed to have happened in upload order (see
  below). Playing or applying still works in any order; Apply offers to catch up earlier
  uploads first. A partially played recording leaves its bottle in hand until the rest
  is applied.
- Deleting an uploaded recording removes its files; inventory changes it made stay.
  Bundled fixtures cannot be deleted.
- "Reset to opening stock" restores the layout's preset batches, clears alerts,
  disposals and prescription deductions, and marks every recording unapplied.
- An append-only history records signals, shipments, disposals, confirmations,
  prescription changes, uploads, deletions, layout saves, and resets.

Use unique IDs and atomic/idempotent processing so replay, retries, and restart do
not repeat mutations. Keep event acceptance and its stock update consistent across
crashes. The MongoDB store writes the whole pharmacy state (counts, applied event
IDs, history) in one revision-checked document replace, so an event and its stock
update land together. A long-running service would need to split that ledger before
MongoDB's document size limit (see `backend/MONGODB.md`).

## Implementation milestones

1. **Pose feasibility gate.** Build a minimal Unity scene with one textured, clothed,
   rigged human, shelves, counter, trash region, and fixed camera. Render a reach,
   counter placement, return, and disposal. Run `backend/scripts/pose.py` on the clip and
   inspect wrist overlays at action times. Adjust camera/animation/assets before
   building the full scene. Record hardware, model, resolution, and processing speed.
2. **Fixtures and replay contracts.** Define schemas, initial stock, rectangles,
   mocked receiving/prescription data, and synchronized mock IMU JSONL. Export ground
   truth separately. Implement deterministic replay, pause/resume, and run isolation.
3. **Region association and movement state.** Add confidence-aware wrist-to-region
   mapping, media-time event matching, counter continuity, wrong-return detection,
   and uncertain-event confirmation. Test event sequences without requiring a model.
4. **Inventory and MongoDB.** Implement receiving, pooled tablets, physical bottle
   locations, idempotent prescription deductions, disposal defaults/corrections,
   expiry records, alert lifecycle, and restart recovery with audited adjustments.
5. **Centralized dashboard.** Show medication/strength, tablet stock, bottles on
   shelves/off shelves/total, expiry, misplaced stock, and pending confirmations.
   Add active prescription, notifications, candidate disposal selection, and quantity
   entry. Proposed demo view includes synchronized video with pose/region overlays.
6. **Integrated demonstration.** Replay the scenario suite below from clean fixtures,
   compare system outputs against evaluator-only ground truth, fix failures, and
   document measured outcomes and remaining limitations.
7. **Later work.** Employee-drawn calibration UI, real IMU adapter, real-camera
   validation, richer receiving/transaction integrations, multiple workers, and
   broader prescription workflows. Do not implement these as first-demo prerequisites.

## Task flexibility and final action recordings

The current simulator is a scripted, stateful demo, not a general-purpose agent
that interprets arbitrary instructions. Its action schedule, two handled bottles,
contact positions, timing, and animation assumptions are defined in code. Navigation
and action guards are reusable, but new workflows require explicit scenario changes
and collision/CV validation. New skills (for example opening containers or counting
pills) require additional behavior and animation implementation. A configurable
scenario format and reusable action library would be the next step toward supporting
user-specified task sequences; these are not implemented yet.

**Required when the simulation is finalized:** record every individual action
separately, in addition to the complete workflow recordings. Produce one labeled
room-camera MP4 per action occurrence in the finalized scenario suite, including
repeated actions in different contexts (shelf pickup, counter placement/pickup,
correct and incorrect return, correction, and disposal). Include enough lead-in and
follow-through to observe the action, preserve its valid starting state, and provide
synchronized mock IMU events with clip-relative timestamps and camera calibration.
Keep an index mapping each clip to its scenario/action and source time range; keep
expected outcomes and simulator state evaluator-only. Run the CV pipeline on each
clip and report results. This is a pending finalization deliverable, not a claim that
individual-action videos have already been exported.

## Acceptance scenarios and proposed evaluation gates

| Scenario | Expected outcome |
| --- | --- |
| Correct pickup, counter rest, pickup, return | Shelf count falls/restores; total bottles unchanged; no wrong-placement alert |
| Wrong medication pickup | Compare source region with active prescription; warn |
| Wrong-shelf release then correction | Alert; preserve original drug identity; correct physical counts and resolve alert |
| Hand passes through wrong shelf without release | No return or misplacement event |
| Release near boundary or while occluded | Uncertain state and employee confirmation; no confident guess |
| Empty disposal with other bottles remaining | One bottle removed; absent quantity deducts zero; form shows candidates |
| Partial or expired bottle disposal | One bottle removed; employee quantity deducted once; selected expiry record updated |
| Last bottle disposal without quantity | Total bottles and pooled tablets reach zero; out-of-stock notification |
| Late quantity entry or duplicate disposal event | Correct net deduction; no repeat bottle decrement |
| Confirmed fill followed by payment/receipt | One tablet deduction for the transaction |
| Expiry without disposal | Expiry alert; physical quantities unchanged |
| Missing/out-of-order sensor event, restart, repeat run | Pending/recoverable state; no duplicate stock mutation |

All deterministic business-rule scenarios must pass. On recorded CV clips, report
correct region assignments, confident errors, abstentions, missed/duplicate actions,
inventory discrepancies, and processing throughput. Provisional gate: no silent
wrong inventory mutations in the scripted suite; clean visible cases must resolve
automatically and deliberately ambiguous cases must request confirmation. Publish
sample counts and coverage rather than claim a general accuracy percentage from a
few scripted clips. Passing simulated clips does not establish real-camera accuracy.

## Remaining technical decisions and risks

- The simulator uses Unity 6000.6.3f1, the MIT-licensed Microsoft Rocketbox Medical_Male_03
  character, procedural animation, and a fixed camera recorded at 1920 x 1080 / 30 FPS.
  The dashboard is React; production CV thresholds remain to be selected.
- Unity is implemented for the simulation. The measured prototype results are in
  the simulation validation report; they do not establish real-camera performance.
  Prerecorded playback separates rendering from inference.
- A single 2D camera can have overlapping projected shelf regions or hidden hands.
  Place the demo camera to reduce those failures; retain confirmation for the rest.
- Initial bottle counts come from receiving/setup. Event tracking cannot guarantee
  continuous physical truth after missed events; visibly track unresolved counts.
- Transaction deductions estimate tablet stock. Unrecorded waste and inaccurate
  source data can create discrepancies that CV cannot resolve.
- Real IMU timing, release detection reliability, and attachment/identity conventions
  require agreement with the separate hardware effort before real integration.
- The wrist is a proxy for the bottle. Shelf depth, which hand holds the bottle, and
  occlusion are not modeled. The nearest-region rule, its distance constant, and the
  elbow and nearest-wrist fallbacks have not been evaluated against ground truth on
  rendered footage. A nearest wrist can be up to 1 s before or after the signal; the
  hand may have moved in between.
- Only the most confident person per frame is tracked; a second person in view can
  be picked instead of the technician.
- Decided 2026-09-26: every upload is a new set of real events, even identical
  footage uploaded twice, and upload order is the order they happened. Uploading
  clips out of order, or uploading footage by mistake, therefore changes stock. The
  fix is a correction or a reset, not deduplication.

### Camera views, joint fallback, and manual stock actions (implemented)

Decided 2026-09-26.

- **Views and catalog.** Medications and opening stock moved to `data/catalog.json`,
  shared by every camera view; each `data/layouts/<id>/layout.json` now holds only a
  view's frame size, photo, and regions (older layouts seed the catalog once). A shelf
  always has the ID `shelf_<medication key>` in every view, so counts line up across
  angles. A view may omit shelves it cannot see. Each recording names its view, and its
  signals are associated with that view's regions.
- **View suggestion on upload.** The server ranks saved views by how much their photo
  looks like the video's first frame (thumbnail cross-correlation plus color histogram,
  penalized by aspect-ratio difference). This is an unvalidated heuristic; the employee
  always reviews it on the video's own frame and chooses: use the view unchanged,
  replace it, or save a new view. Saving uses the video frame as the view photo, so
  regions cannot drift between annotation and playback. Setup also warns when a view's
  photo and its recordings differ in shape, and can take a recording's frame as the
  photo or crop an imported photo to the recordings' aspect ratio.
- **Joint fallback.** Superseded 2026-09-26 by the chain under "Multi-camera uploads,
  hand fallback, upload order, and stock suggestions": shoulders are no longer used,
  and a recently seen wrist is tried last. Signals applied before the change keep the
  joint they were recorded with.
- **Confirmation.** The dialog lists regions nearest first with their distances, and
  settles a pickup and its held put-down in one step. Confirmed rows in the signal log
  show the employee's choice; the original evidence stays in the history.
- **Manual disposal.** Employees can dispose of bottles from a batch (e.g. expired
  stock they found). Bottles must be on the shelf; a blank tablet count follows the
  same default as camera-detected disposal. Emptying the expired batch clears its alert.
- **Prescriptions** can be added by form or imported from CSV/JSON (all rows validated
  before any is added). Filled or paid ones deduct once, immediately.
- **Player.** Skipping forward past a signal applies it, like playing through it;
  skipping back never undoes one. Uploads close the window while skeletons are
  extracted in the background, with a top-bar indicator and a notice when ready.

## Dashboard design (implemented)

Decided 2026-09-26. The UI follows the Vercel DESIGN.md from awesome-design-md
(Geist and Geist Mono, ink on near-white, hairline borders, 6px controls, 8px cards),
the taste-skill redesign and minimalist rules (one accent, pastel status tones only,
no em-dashes, Phosphor icons instead of Lucide), and was audited against the Vercel
web interface guidelines (focus-visible rings, labelled icon buttons, skip link,
`aria-live` for updates, `Intl` formatting, confirm dialogs for destructive actions).
Headings and buttons use sentence case (the guidelines prefer Title Case; the other
two sources and the existing copy use sentence case). A top-bar toggle switches light
and dark themes; it follows the OS setting until the user picks one, then remembers it
in the browser.

The upload dialog accepts pickup/put-down times either as a file or through a manual
form. The form previews the chosen video so times can be marked at the playhead, and
it is sent to the server as the same CSV a file upload would be.

## Renderer references

- [Unity humanoid animation retargeting](https://docs.unity3d.com/6000.0/Documentation/Manual/Retargeting.html)
- [Unity Recorder](https://docs.unity.com/en-us/engine/6000.3/manual/packages-list/packages-all/pack-safe/com-unity-recorder)
- [Godot animation](https://docs.godotengine.org/en/stable/tutorials/animation/animation_tree.html)
- [Godot offline movie capture](https://docs.godotengine.org/en/stable/tutorials/animation/creating_movies.html)

## Visual detail pass — 2026-09-26

Added surface texture and construction details throughout the existing room,
normal-mapped character clothing/skin, bone-attached staff ID and pocket pens,
cap grips/barcodes, shelf/cabinet fittings, counter equipment, signage, and bin
hardware. This is a visual upgrade to the same deterministic scenario; it does
not implement new employee tasks or change the fixed camera calibration.
The simulation remains stylized and procedural, not photorealistic or mocap.
New rendered-footage checks are recorded in `simulation/VALIDATION.md`.

## MongoDB workflow and camera handoff — 2026-09-26

Live inventory is migrated to an atomic, revision-checked MongoDB pharmacy document.
Counts, prescription deduction flags, replay event IDs, receipts, alerts, disposals,
and corrections persist together. Existing JSON stock is imported once and retained
as a backup. MongoDB failure pauses updates; there is no local-file fallback.
`backend/MONGODB.md` documents setup, migration, recovery, tests, and the bounded-demo
aggregate size limit. Setup/calibration and video assets remain filesystem resources.

Multi-shelf operation must support visibility-driven camera handoff when the active
view loses the arm skeleton. Observations identify camera/calibration; a shared
clock and event identity preserve bottle ownership and prevent duplicate inventory
updates. If no view has reliable arm evidence, request confirmation. The eventual
presentation mixes real and simulated footage seamlessly; editing that mix is
future presentation work, not part of this storage migration.

### Implemented camera demonstration

Two synchronized Unity cameras now feed separate YOLO passes. The dashboard and
exported POV use a causal arm-confidence selector with loss/acquisition debounce.
The 106-second recording switches at 6.033 s and 33.833 s; 75 frames have no reliable
arm in the selected view and remain uncertain. All 10 scripted bottle-action regions
were correct in the integrated inventory replay, with no action abstentions/wrong
regions. MongoDB state and applied-event counts remained unchanged after replay and
controller restart. This does not validate real-camera performance. Multi-camera
uploads were added afterwards (see the next section); unsynchronized live capture
remains future work. Simulator groups use the bundle format in `backend/MULTICAMERA.md`.

## Multi-camera uploads, hand fallback, upload order, and stock suggestions (implemented)

Decided 2026-09-26. The thresholds below are unvalidated demo defaults.

- **Multi-camera uploads.** The upload window accepts several videos of the same moment
  plus one timestamps file. The first video is the main camera: its clock drives the
  player and the timestamps, and any other can be made main. The cameras are assumed
  to start together; they may differ in frame rate or length, and a camera is treated
  as having no view once its video ends. A difference over 1 s is shown as a warning.
  Each camera gets the most similar saved view, preferring one no other camera of the
  upload uses, and a camera's recordings follow its view's current calibration. Simulator bundles still pin a
  calibration version and require identical clocks.
- **One upload window** (decided 2026-09-26). Everything happens before the upload
  finishes, so a recording never needs revisiting to draw boxes:
  1. *Video and times.* Choosing videos starts uploading them to a draft
     (`POST /api/uploads`, kept under `scenarios/.drafts/`) while the employee marks
     pickup and put-down times on the main camera's preview, types them, or picks a
     timestamps file.
  2. *Shelf boxes.* Each camera's first frame, with its most similar saved view,
     preferring one no other camera uses. The employee can switch views or adjust
     boxes. Edited cameras are saved as a new view (default) or as an update to that
     view. Unchanged or unopened cameras use their view as is.

  Upload (`POST /api/uploads/{id}/finish`) checks the times and choices first, so a
  rejected request keeps the draft to fix. It then turns the draft into a recording
  with every camera's view confirmed, starts skeleton extraction, and closes the
  window. Progress shows in the top bar, then as a toast. Cancelling or closing the
  window deletes the draft. Drafts left by a closed browser tab are deleted after
  24 h. The one-request `POST /api/recordings` still works; its views stay
  unconfirmed and are reviewed from Recordings, where any recording's views can
  still be changed later.
- **Where the hand is at a signal.** Each step is tried only when the previous one
  finds nothing:
  1. Camera switching (`backend/MULTICAMERA.md`): the selected camera's complete
     arm, meaning shoulder, elbow and wrist at confidence >= 0.5.
  2. Any confident wrist, then any confident elbow (>= 0.35). Each is looked for in
     the signal's frame, then outwards up to 5 frames before or after it (earlier
     first on ties), in every camera: the selected camera first, then the others by
     arm score.
  3. The nearest confident wrist before or after the signal, within 1 s: its last
     or next known position.
  4. Nothing: the employee confirms the location. Anything needing more than 1 s
     either way lands here (decided 2026-09-26).

  A single camera runs the same chain without step 1. Steps 2 and 3 read frames after
  the signal, which is fine because recordings are processed before they play; camera
  selection itself still never looks ahead. Shoulders are not used.

  A person missing from the start of a clip, or from every camera, gives step 4 with
  no error. The signal log shows which camera was used and when the hand came from
  an elbow or a wrist seen earlier or later (with how far). The engine's distance
  threshold still decides whether the position is confident enough to act on.
  Activity rows store the offset as `joint_offset_ms` (negative means before);
  earlier rows stored `joint_age_ms`.
- **Upload order.** Every upload is a new set of events, even the same footage again.
  Uploads are numbered in upload order, which is taken as the order they happened.
  Applying a recording while earlier uploads still have unapplied signals asks
  whether to apply those first, oldest first; "Only this one" is still available.
- **Simulator event files.** Timestamp files may contain the simulator's `movement`
  samples. They are skipped, since they carry no location, and the original file
  is saved beside the recording as `events-source.*`.
- **Stock suggestions.** Derived from live stock on every read, not stored, so they
  never duplicate. They change nothing; alerts still come from the inventory rules.
  - *Running low:* tablets at or below the medication's reorder point. The default is
    20% of opening stock; it can be set per medication on Setup.
  - *Last bottle:* one bottle left, which replaces "Running low".
  - *Forecast to run out:* at the average of the last 14 days' prescription
    deductions, stock lasts fewer than 7 days. This needs the new `deducted_at` time
    on prescriptions, so deductions made before this change don't count.
  - *Expiring soon:* a batch with bottles left expires within 30 days, marked urgent
    within 7 days. If nothing else would be left, or only one bottle, it says to
    order more.

  "Dismiss" hides a suggestion until it changes. The expiry suggestion comes back
  when it escalates to the 7-day stage; the stock suggestions come back after a new
  batch is received. Dismissals are stored with the pharmacy state and cleared by a
  reset.

## Real footage and the simulation (direction)

Recorded 2026-09-26 as context; nothing in this section is implemented.

The intended flow starts with real footage plus a pickup and put-down signal. The
real footage gets the CV annotations: skeleton, regions, and the decided shelf. A
Unity re-enactment of the same actions highlights which shelf or bottle was picked up
and where it was put down. The final demo cross-fades between real and simulated
footage; that edit is separate work. For now the simulation is rendered first and
then repeated in real life. Either way the pipeline uses only what real film would
provide: video and signals. It never uses Unity calibration or rig truth.

Proposed path for real-to-Unity, simplest first:

1. **Event-driven re-enactment (recommended).**
   - The dashboard exports the confirmed action timeline of a recording: time, pickup
     or put-down, region and medication.
   - The Unity simulation plays those actions through its existing guarded action
     system, walking to the shelf, picking and placing, and highlights the shelf,
     bottle and destination.
   - This needs a timeline export endpoint and a table mapping each dashboard region
     to a Unity shelf or region. On the Unity side it needs the "configurable
     scenario format" from "Task flexibility" above, plus slack in the schedule for
     walking time.
   - Only the action moments line up with the real clip, not body motion.
   - Moderate effort, mostly on the Unity side.
2. **Motion reconstruction (not recommended for the demo).**
   - Lift the real 2D keypoints to 3D with a monocular human pose or mesh model, then
     retarget them onto the Rocketbox rig in a Unity room built to match the real one.
   - This needs real camera intrinsics and extrinsics, a matched room model, and
     foot-contact cleanup.
   - Research-grade effort, with visible artefacts likely.
