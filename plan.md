# Pharmacy inventory demo plan

## Linux demo deployment — 2026-09-27

The demo server now has a guided `/demo` page for the supplied September 27 GLB
and IMG_3537 recording. Python-assisted frame review annotated six stock-bottle
actions, with approximate ±250 ms timing; provenance is retained on the events.
These annotations are not wristband measurements or a validated recognition model.
The server completed pose extraction. Shelf regions and camera registration remain
user setup tasks; no invented shelf identities were applied to inventory.

The HTTP deployment exposed a startup failure from secure-context-only
`crypto.randomUUID`. Event IDs now fall back to Web Crypto random bytes while
preserving UUID v4 format. API regression/setup tests: 28 passed; UUID fallback
test and production frontend build passed. Linux Unity 6000.6.3f1 was installed,
but its launch returned exit 198 (no valid Editor license). Existing simulation
playback works; fresh server rendering is not claimed complete until activation.

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

## Room scan, camera registration and floor track (implemented M1–M4) — 2026-09-26

Plan A from the real-footage discussion: scan the room once with a LiDAR iPhone, tag
shelves on the scan, register one fixed camera by clicking matching points, then
place the technician on the floor from the skeleton alone. IMU events still give
pickup and put-down timing. Unity playback of this track (M5 onward) is not built.
Everything here is presentation data: inventory, events and alerts never read rooms,
3D regions, registrations or floor tracks.

Decisions agreed with the user:

- One camera. If the technician isn't seen, nothing is inferred: the map holds the
  last position greyed out while bottles and highlights keep following whatever the
  dashboard decided.
- On reappearing: a quick blend if they moved a little, otherwise a cut. This is for
  Unity playback, so it isn't built yet.
- The lens is solved from the clicked points. Film a calibration board only if the
  overlay shows that isn't accurate enough.

What is built:

- **M1 scan import:**
  - `POST /api/rooms` takes an uncompressed GLB (Polycam or 3D Scanner App export)
    and stores `data/rooms/<id>/` (git-ignored): `room.json`, `mesh.glb`, `plan.png`
    and `obstacles.png`.
  - The scan is leveled: the floor is fitted, tilt of 10° or less is corrected, walls
    are turned onto X/Z, and the floor is set to y = 0. It is never rescaled.
  - Draco/meshopt-compressed files and scans without a floor are rejected with a
    message.
- **M2 3D tagging (Room page, Shelves & regions):**
  - Two clicks on the scan make a box. A vertical face gives a box 35 cm deep into
    the shelf, turned to face out; a horizontal top gives a box rising 35 cm.
  - Numeric fields adjust center, size and turn.
  - Rules match the 2D layout: shelf IDs are `shelf_<key>`, one shelf per
    medication, and only configured medications.
  - Saves are versioned; a stale save gets 409.
- **M3 camera registration (Room page, Cameras):**
  - Pairs are clicked in the view's photo and the scan, in either order.
  - With 6 or more pairs the solve runs automatically: a focal-length search with the
    principal point at the image center, square pixels and no distortion, then
    SQPnP and LM refinement.
  - It reports the average and worst reprojection error for each pair, and draws the
    reprojected points, region edges, a 50 cm floor grid and a see-through scan over
    the photo.
  - A registration belongs to one room. Saving bumps its revision, and the page
    warns when the view's photo changed after registration.
- **M4 floor track and minimap (Dashboard, Floor map):**
  - `GET /api/recordings/{name}/floor-track` runs on the recording's cached
    skeletons. The result is cached in `floor_track.json` and keyed to the poses
    file, room version and registration revision.
  - **Position:** the ankle-midpoint ray meets the plane 8 cm above the floor. When
    the feet are hidden, the hip ray meets the measured hip height.
  - **Facing:** the best of 72 yaws at matching the projected shoulders and hips,
    with a face-visibility term. Travel direction blends in while walking.
  - **Clean-up:** jumps are rejected, gaps of up to 10 frames are bridged,
    smoothing is applied, and positions are pushed off furniture onto free floor.
  - The map shows the plan, region footprints, the camera and its field of view, the
    technician's dot and facing arrow, a 2 s trail, and a greyed-out "not in view,
    holding" state.

Assumptions and constants (proposed, **not validated on real footage**):

- Ankle keypoint 0.08 m above the floor.
- Default hip height 0.95 m (measured per recording when the feet are seen).
- Shoulder half-width 0.18 m and hip half-width 0.13 m.
- Face confidence: 0.5 or above counts as seen, 0.2 or below as hidden.
- Bridge gaps of up to 10 frames. Reject jumps over 0.4 m against a 7-frame median.
- Smoothing σ: 0.10 s for position and 0.15 s for yaw.
- Obstacles are anything 0.15–1.8 m high, on a 2 cm plan grid.
- Registration quality: RMS of 0.4% of the image diagonal or less is "good", 1% or
  less is "check", anything above is "poor".

Measured so far (synthetic only; a perfect skeleton that matches the model's own
assumptions, so these are **not accuracy claims**):

- Unit tests recover the true focal length and pose, and put people within a few
  centimetres and a few degrees at every yaw, including with the feet hidden.
- A browser check through a separate, isolated server:
  - Setup: a synthetic scan, a rendered 1280×720 "photo", and 17 pairs clicked with
    1.5 px of simulated noise.
  - Registration came out "good": 2.2 px RMS and 5.4 px worst, with a 71° field of
    view against the true 70.8° and a camera height of 2.40 m.
  - A 12 s synthetic walk was placed in 96% of frames. The gap of 15 hidden frames
    stayed unplaced (held in grey on the map) rather than being guessed.
- Real accuracy is unknown until a real scan and recording are registered. Check the
  overlay first: if shelf edges and the floor grid visibly miss the photo, add pairs
  farther apart, and film a calibration board only if that fails.

Scan-capture checklist:

- Use LiDAR mode and walk slowly. Keep the floor in frame, and cover the shelf fronts,
  the counter and everything the camera sees.
- Export GLB **without** Draco/mesh compression.
- For registration, pick points spread across the image and at different heights:
  floor tape marks and corners, shelf corners, and counter-top corners. Two or three
  above the floor help fix the lens.

Not built yet: Unity playback of the floor track (M5 onward), multi-camera floor
tracks, and lens distortion. Distortion is to be added only if the overlay shows it
matters.

## Next steps after M1–M4 — 2026-09-26

### Status (updated 2026-09-26)

- **N0:** the user tested the real-footage flow and reported it "works well". That is
  the user's judgement, not a measurement: the proposed 15 cm / 0.5 s / 90% bar below
  has not been measured on real footage.
- **N1 built:** Setup is removed (`/setup` redirects to `/room`). The Cameras tab
  stacks the photo over the 3D scan. Views are created, renamed and deleted there;
  photos are uploaded or taken from a recording frame. After a new photo the old
  registration's overlay is drawn on it and "Keep registration" re-saves the same
  pairs, allowed only when the aspect ratio is unchanged (verified: identical pose,
  no calibration bump). Medications and opening stock moved to an Inventory section.
  The two-camera upload was checked in a browser against an isolated server: camera 1
  used the generated regions and camera 2 became a new view with no regions.
- **N2 built:**
  - 2D regions are generated from the 3D boxes (`box-visibility/1`). Hand-drawn
    edits to a generated view are refused.
  - The simulator's side: `backend/src/pharma/services/sim_scene.py` and
    `backend/scripts/import_sim_room.py` turn a render's `scene_geometry.json`
    (format in the module docstring) into a room. The solid boxes become the mesh;
    the regions become 3D boxes with their fronts facing the camera; the Unity
    camera becomes a registration from exact projected corners.
  - A round-trip test through the API recovers the Unity projection. Registration
    RMS is under 0.05 px, and region corners reproject within 0.5 px.
  - Sim medication IDs are matched to catalog keys by letters and digits. Regions
    whose medication isn't in the catalog are skipped and reported.
  - The Unity exporter that writes `scene_geometry.json` is delegated with M7/M8.
- **N3 evaluated on rendered footage:** `simulation/tools/evaluate_floor_track.py`
  (evaluator-only) scores a floor track against `simulation_states.jsonl` (body
  position) and the rig's shoulders and hips, unprojected with the exact camera. It
  reports cm/degree medians, p90 and max per source (ankles, hips, bridged), placed
  fraction, and facing flips over 90°. A synthetic test checks the axis conversion.
  A fresh local replay placed 83.21% of 3,180 frames. Position error was
  4.75 cm median / 9.53 cm p90; facing error was 6.3 degrees median /
  42.23 degrees p90, with 41 frames over 90 degrees. Facing remains a limitation.
  See the scan-to-simulation validation section for provenance and limits.
- **N4 built:** `rebuild/2` (`services/room_rebuild.py`, `GET /api/rooms/{id}/rebuilt`,
  and the Scan/Rebuilt toggle). Fixture measurements only: walls fall 0–11 mm from
  the scanned inner faces, the 0.9 m doorway is found, board heights are within 1 cm
  and the colours match within 12/255. Nothing has been measured on a real scan.
- **M5 built:** `GET /api/recordings/{name}/timeline` writes the recording's
  `timeline.json` (`timeline/1`, `services/timeline.py`). It holds:
  - the floor track, the room's regions, medications, every camera's registration
    and the rebuilt room
  - one action per wearable signal
  - bottle counts before the first signal

  Details:
  - Actions carry the signal's event ID, contact time, bottle (movement session),
    status (`decided`, `confirmed`, `pending` or `not_applied`), region, and outcome
    (`in_hand`, `returned`, `misplaced`, `counter`, `disposed` or `pending`).
  - A pending action has no region, only `suggested_region_id`.
  - Starting counts come from a snapshot taken at the recording's first applied
    signal. Recordings applied before this change fall back to current counts and
    say so (`current_after_recording`).
  - The file is byte-identical for the same inputs. `inputs_key` is a cheap hash of
    the same inputs, used to key renders.
- **M6 built:** `PharmacySimulation.cs` is split into `RoomDescription.cs` (room
  data), `MotionSource.cs` (`IMotionSource`; `PlannedWalk` is the existing route) and
  `ActionSchedule.cs` (cues, ownership, commits). Pose validation stays in the host.
  - Prior-session baseline: the demo re-rendered with byte-identical rig skeleton, per-frame states,
    calibration and fixtures. IMU events and ground truth are identical once the
    per-run IDs are removed. SimulationChecks passes with the same metrics.
  - Frames are not byte-identical, even between two renders of the unchanged code:
    font rasterization varies from run to run. The measured noise is a mean pixel
    difference of 0.0015 (about 120–150 of 2.07 M pixels per frame, on sign text).
    The refactor stayed within that noise. A fresh local post-host-edit comparison
    of all 3,180 raw frames measured 0.000802 mean pixel difference, with identical
    states and sensor schedule; collision checks passed again.
- **M7/M8 implemented and preview-verified:** static scene export/import, registered
  camera, rebuilt-room scene, floor-track player, and `render.py --timeline`.
  The 65-frame action preview shows 7 reaches, 2 counter highlight fallbacks and
  1 state-follow fallback, with zero guard holds or refused reaches.
  Cutaway walls retain colliders; thin shelf tags extend over furniture; bottle
  selection preserves medication identity; tagged rows reuse nearby scanned boards.
  The full 3,180-frame product render passed with the same results.
  Full product-render results are recorded in `simulation/VALIDATION.md`.
- **M9 backend, UI and Unity renderer implemented:**
  - Queue (`services/render_jobs.py`): one render at a time, queued and cancellable,
    keyed on `inputs_key` so the same inputs reuse the finished render. Verified
    through the live API and player; an edited region marks the result stale. Each queued
    job owns an immutable timeline snapshot; later exports cannot change its inputs.
    POSIX cancellation terminates the wrapper and its Unity/encoder process group.
  - Output per render: `sim.mp4`, `side_by_side.mp4` and `manifest.json` (input and
    output hashes, requested vs rendered fps/frames, the renderer's report).
  - Staleness: after a correction or new signals the render is marked stale and the
    button reads "Re-render".
  - The button is on Recordings rows and in the player header, beside a Real /
    Simulation / Side by side switch. That switch is server-side, on the shared
    clock, and every rendered frame is labelled "Simulation re-enactment (not camera
    footage)". Render jobs also appear in the top-bar job indicator.
  - Configuration: `UNITY_PATH` (and optionally `SIMULATION_DIR`). Without it the
    button is disabled and says why.
  - Checks: tests use a fake renderer, and a browser check used a stand-in render
    script. The real Unity `render.py --timeline` mode is part of the delegated
    M7/M8 work.

Decisions from review:

- **The 3D room tags are the only tags.** Nobody draws 2D polygons any more.
- **The Setup page goes away.** Everything it does moves to the Room page, except
  medications and opening stock (see N1).
- **Unity uses a clean room rebuilt from the plan and the region boxes**, not the raw
  scan mesh. It must carry enough real detail to be recognizably the same room,
  reskinned.
- **The Unity character keeps its default size.** No height scaling.
- **A "Render simulation" button** starts the Unity render from the dashboard.
- **On the Room page Cameras tab:**
  - The photo and the 3D scan are stacked vertically, each full width, instead of
    side by side.
  - A new camera photo can be uploaded there directly.

### N0 — Real-footage check (in progress, user testing)

- Run the full flow on the real room:
  - scan with tape crosses on the floor
  - register the camera
  - a 20 s standing test on the crosses, facing marked directions
  - one normal workflow recording
- **Proposed bar for moving to the Unity work** (awaiting confirmation; not validated):
  - about 15 cm median position error
  - no facing flips longer than 0.5 s while standing at a shelf
  - technician placed in 90% or more of frames
- **Likely first fixes:**
  - a lighter browser copy of large scans (clicking lags on million-triangle meshes)
  - lens distortion, if shelf edges drift toward the image edges
  - following the same person across frames, so a passer-by can't take over the track
  - warning when a recording's frames no longer match the registered view

### N1 — Room page becomes the only setup page; Setup is removed

**Cameras tab:**

- **Layout:** the photo on top and the 3D scan below, both full width. The pair list
  and solve results stay in the side panel. Clicking a pair highlights it in both views.
- **Camera views are managed here:** create, rename and delete, moved from Setup.
- **Photo sources:**
  - "Replace photo" (upload a PNG or JPEG)
  - "Use a recording frame…", which takes a frame from any recording of this camera
  - The size-mismatch warning moves here too: a recording whose frame size differs
    from the photo gets a warning and a "Use its frame" action.
- **Replacing the photo of a registered camera:**
  1. Draw the existing registration's overlay on the new photo.
  2. If the shelf edges and floor grid still line up, the employee keeps the
     registration and nothing is re-solved.
  3. If they don't (the camera moved), clear the pairs and re-register.
  - This way a new photo never silently invalidates a registration, and never
    silently keeps a wrong one.

**Medications & opening stock** (catalog, receiving records, expiry and lot numbers):

- **Default home:** a new section of the Inventory page, since this is stock data, not
  room layout.
- The Room page's shelf medication picker links there.

**Removing Setup:**

- Remove the page and its nav link. `/setup` redirects to `/room`.
- Update every link that points to Setup, for example the ones on the Room page and in
  the upload dialog.
- Delete `RegionEditor` only after N2 has landed and nothing uses it.
- **Test:** the upload flow still works end to end with no Setup page, including new
  camera views created during a multi-camera upload.

### N2 — 3D tags become the only tags (2D regions are derived)

The inventory pipeline still associates wrists with image regions, so its logic stays
the same. The difference is where the polygons come from:

- For each registered camera, they are generated from the room's 3D boxes and saved
  into that view's `layout.json` as they are today.
- Each generated region records where it came from: `room_id`, `room_version` and the
  registration revision.
- The view's `calibration_version` goes up whenever the generated polygons change.
- Existing observations keep identifying their camera and calibration version
  (AGENTS rule 15).

**How the polygons are made:**

- Project each box and clip it to the camera's view and the frame. Planned default:
  the outline of the box's front face plus its top face.
- **Overlapping shelves** (a near shelf covering a far one in the image) need a
  decided rule:
  - Option A: the nearer region wins the overlap.
  - Option B: keep both, and let the existing ambiguity path ask for confirmation.
  - This is decided by the regression comparison below, not assumed.
- **A camera with no registration has no regions.** Its events can't be placed, so
  they stay uncertain and ask for confirmation (AGENTS rule 6). The upload dialog tells
  the employee to register the new camera on the Room page.
- **Legacy views** keep their hand-drawn polygons, read-only, until registered. The
  first registration replaces them after showing a preview.

**Validation:**

- **Regression comparison.** Replay the existing recordings twice, once with the
  hand-drawn polygons and once with the generated ones. Compare region decisions and
  confidence, and report changed decisions separately from new abstentions.
- **Tests:**
  - projection and clipping
  - a box behind the camera or partly out of frame
  - overlap handling
  - `calibration_version` bumps
  - idempotent regeneration: the same inputs give the same polygons and no bump
  - no regions for an unregistered camera
- **Simulated footage needs a room too.** The Unity exporter writes `room.json`
  (exact shelf boxes) and a registration built from the exact Unity camera, so
  rendered recordings follow the same 3D-only path. This is shared with N3.

### N3 — Floor-track accuracy on the existing simulation (small)

- Run the floor track on the existing Unity render, using the exact room and camera
  from N2's exporter.
- Compare it with the rig truth, which is used for evaluation only and never enters
  the track.
- Report position error (cm) and facing error (degrees), split by ankle and hip
  source.
- This also exercises the Unity-to-room axis conversion that M7 needs in reverse.

### N4 — Clean room rebuild ("reskin")

Built in Python, as a data file. Unity (M7) only turns it into objects, and the Room
page previews it with a **Scan / Rebuilt** toggle, so problems show up before any
Unity work.

**Detail that makes it recognizably the same room:**

- **Walls:** traced from the plan outline, with the real height from the scan bounds.
  Gaps in the trace are kept as openings (doorways).
- **Shelf units:** each tagged shelf box becomes a shelving unit. The number and heights
  of its boards come from the scan's horizontal surfaces inside the box. It gets a
  medication label on its front.
- **Counter and disposal:** built from their boxes (top, base, bin).
- **Untagged furniture:** the plan's other obstacle shapes become simple blocks with
  their measured height, so nothing is missing.
- **Colors:** sampled from the scan texture (the average color of floor, walls, shelves
  and counter), so the palette matches the real room. A plain floor and the measured
  materials, no photo textures.
- **The camera** is placed from its registration, so the rebuilt view matches the
  photo's framing.

**Checks:**

- The rebuilt objects stay within the scan: walls within a few cm of the scanned wall
  surfaces, shelves inside their tagged boxes.
- The side-by-side preview against the photo is judged by eye.

**Walking area:** the obstacle grid plus the rebuilt objects. The noisy scan mesh
never becomes a collider.

### M5–M9 — Unity re-enactment (updated)

- **M5 Timeline export:**
  - Contents: floor track, confirmed actions (pending ones stay pending), starting
    bottle counts per shelf, and the rebuilt room and camera.
  - Nothing evaluator-only goes in.
  - Idempotent, with stable event IDs.
- **M6 Unity refactor, no behavior change:**
  - Split `PharmacySimulation.cs` into three parts: the room description, where the
    motion comes from (its own planned walk, or a floor track), and the pickup and
    put-down actions.
  - The existing demo must re-render identically.
- **M7 Scene from the rebuilt room:**
  - Unity builds the scene from N4's primitives. Importing the scan mesh (and the
    glTF package that would need) is no longer required.
  - Includes collision layers, regions, bottles and the registered camera.
  - Alignment check: project the registration points in Unity; they must match the
    photo within a few px.
- **M8 Re-enactment player:**
  - The character follows the track and turns with the tracked facing.
  - The reach starts about 0.6 s before each wearable contact time.
  - If the target is just out of reach, the character leans or steps up to 0.3 m.
    Beyond that it only highlights the region, never showing a completed put-down.
  - Bottles keep their original medication identity. A misplaced bottle is shown in
    red, a counter placement in amber, disposal in gray, and a pending decision as
    translucent with a "?".
  - While the technician is out of view, the body holds its last position and
    bottles and highlights follow the dashboard. On reappearing it blends if they
    moved under 1 m, otherwise it cuts.
- **M9 Render and the "Render simulation" button:**
  - **Where:** a button on each recording, on the Recordings page and in the
    dashboard player.
  - **Job:**
    - It runs in the background through the existing job indicator: export the
      timeline, build the scene, render at the recording's exact fps, frame size and
      duration, then package.
    - One render at a time, because the Unity project allows only one editor
      instance. Other renders wait in a queue, and a render can be cancelled.
  - **Needs:** a licensed Unity editor on the server machine, configured through an
    environment variable (`UNITY_PATH`). Without it, the button is disabled and says
    why.
  - **Output:**
    - `sim.mp4`, plus a real-vs-sim side-by-side for checking alignment, and a render
      manifest with the input hashes.
    - The player gets a Real / Simulation / Side-by-side switch. The cross-fade edit
      stays separate (AGENTS rule 16).
  - **Stale renders:** if the decisions, room or registration change after a render
    (for example an employee correction), the render is marked stale and the button
    becomes "Re-render". Rendering the same inputs again reuses the result.

### Order and rough size

| Step | Depends on | Size |
|---|---|---|
| N0 real-footage check | — | user time, plus small fixes |
| N1 Room page as the only setup (UI half) | — | M |
| N2 3D-only tags, generated 2D regions, regression comparison | N1 | M |
| N1 finish: remove Setup | N2 | S |
| N3 simulation accuracy check | N2 exporter | S |
| N4 clean room rebuild and preview | N0 | M |
| M5 timeline export | N2, N4 | S |
| M6 Unity refactor | — (can start any time) | M |
| M7 Unity scene from rebuilt room | N4, M6 | S–M |
| M8 re-enactment player | M5–M7 | L |
| M9 render job and button | M8 | M |

## Live wristband capture in the dashboard — 2026-09-27

Replaces PR 7's separate Live camera page (branch `feature/live-wristband-dashboard`,
built on PR 7's browser capture and band connection). The live camera is now the main
dashboard tile rather than a separate page, and every band event updates the whole
dashboard like a played recording.

Agreed behavior:

- **Sources.** One camera at a time, chosen in the browser: the Mac webcam or an iPhone
  through Continuity Camera (it appears as an ordinary camera in Chrome). Capture stays
  in Chrome (`getUserMedia` + Web Bluetooth); the band firmware is unchanged.
- **Privacy.** The feed lives only in browser memory (a rolling ~10.5 s JPEG buffer at
  10 fps). Only the window from 9 s before to 1 s after each band notification is
  uploaded and kept. The window was first 4 s + 1 s, but the band's notification arrives
  roughly 4–5 s after the physical action (user report, 2026-09-27, not measured), so the
  old window started around the action itself and missed the reach. The thumbnail uses
  the frame 4.5 s before the notification; the region decision still scans the whole clip. Nothing is recorded between events, and no skeleton runs on the
  continuous feed. Clips are kept until someone deletes them.
- **Analysis.** Each event clip becomes a first-class recording (`scenarios/live-<event_id>`)
  with an H.264 MP4, a thumbnail, YOLO poses from its pixels, one IMU event and its
  scenario metadata. The existing three-consecutive-frame wrist rule decides the region,
  and the inventory engine applies the event once, when it arrives. Replaying a clip is
  review only, even after a reset, and review works while live capture keeps running.
  The Unity re-render stays behind its button. OpenCV's `avc1` writer produces
  browser-playable H.264 (`mp4v` fallback), so ffmpeg is not required.
- **Tracking across clips.** All live events share one session scope (`live`). A pickup
  opens a movement whose ID is its event ID; the next put-down joins that movement, so
  the bottle's medication and original shelf carry across the two clips. The engine's
  existing counter parking also applies here: a later pickup at the counter continues
  the parked bottle.
- **Out-of-sequence events** (pickup while a bottle is held, put-down with nothing held)
  are treated as band false positives. Their raw notification is recorded in the store
  and history as ignored, so a retry stays ignored, but no clip is written and inventory
  is unchanged. Consequence: a missed put-down leaves the bottle held, and later pickups
  are ignored until a put-down arrives.
- **Uncertainty.** Multiple people, no frames, an incomplete window, a frame shape that
  doesn't match the view, a calibration change while queued, or no confident wrist all
  raise the normal uncertainty alert. The alert carries the live reason and its clip;
  Notifications shows the thumbnail, and Confirm location plays the clip.
- **Dashboard.** "Go live" replaces Upload on the top bar (Upload moved to Recordings).
  The setup dialog has the camera, view (remembered per camera), wrist, band and a
  privacy note. While live, the main tile switches between the live picture (with the
  view's regions drawn over it) and the player. "Live movements" pairs each pickup with
  its put-down, and Recordings groups clips by live session.
- **Latency.** The target is 5–10 s from notification to dashboard update: 1 s post-roll,
  upload, encoding (1.1 s measured) and pose on about 100 frames (6.8–7.7 s measured on
  this Mac at 960 px, 101 frames from real footage), then the store write. That puts the
  update at about 10 s after the notification, or 14–15 s after the physical action.
  `POSE_IMGSZ=640` or posing every other frame would be faster; neither is chosen yet. Pose runs outside the inventory
  lock so the replay clock doesn't stall.
- **Dev mode.** `?dev=1` (remembered for the tab) makes Space send a pickup, then a
  put-down, through the same upload path as the band, marked `source: dev`.

Checks run (local, not Docker): the backend and simulation suites passed 262 tests,
including 13 live API tests. Those cover:

- a movement across two clips;
- ignored out-of-sequence events without footage;
- replay and reset never re-applying;
- counter re-pickup keeping identity;
- an uncertain pickup then confirm;
- missing frames, multiple people, calibration change and frames outside the window;
- dev source;
- the live lease while a clip is reviewed;
- deleting a clip keeping stock.

The 5 Node band-link tests and the production dashboard build passed. The UI was checked in
headless Chrome with a fake camera (light and dark themes); no band events were sent
against the working database.

Not validated: a physical band with a real camera, the real notification-to-contact
timing (the firmware sends no timestamp or sequence number, so the window only brackets
the notification), the measured end-to-end latency, Continuity Camera specifically, and
automatic accuracy on live footage. The automatic rule remains provisional.

Deferred:

- Multi-camera live capture with visibility-driven handoff (AGENTS rule 15). Live mode
  uses one camera.
- Adapting PR 8's stocking flow to live events.
- A timeout or manual "put down" for a band that misses a put-down.
- Limiting the region decision to the frames around the expected action time. The rule
  scans the whole 10 s clip, so a sustained wrist in a second eligible region (for
  example the counter soon after a shelf pickup) makes the event uncertain rather than
  wrong. Narrowing it needs a measured band latency.

## Pickup from a shelf holding more than one kind of bottle — 2026-09-27

Previously, a pickup from a shelf that held a misplaced bottle was always assumed to be
that bottle (the correction). That guessed wrong when the technician took the shelf's own
stock. For example, a leftover misplaced Amoxicillin on the Ibuprofen shelf turned an
Ibuprofen pickup into Amoxicillin, and the later wrong return and its correction were then
reported backwards.

Rule now (user decision): when a shelf holds more than one kind of bottle (its own stock
plus a misplaced bottle, or several misplaced bottles), the pickup raises an uncertainty
alert with reason `which_bottle` and the candidate bottles. Stock doesn't change, and a
put-down that follows waits, until an employee chooses the bottle ("Which bottle?" in
Notifications). A shelf holding only a misplaced bottle is still picked up without
asking. The answer is recorded in the confirmation history. Live events report
`needs_confirmation` in this case even though the camera decided the region.

Checked: engine tests cover the recorded IMG_3537 sequence:

1. Ibuprofen → counter → Amoxicillin shelf raises a misplacement.
2. Picking up from the Amoxicillin shelf then asks which bottle.
3. Answering "the misplaced Ibuprofen" and returning it home resolves the alert.

They also cover choosing the shelf's own bottle, a lone misplaced bottle, invalid answers,
and a live API round trip. Full suite: 267 passed. After resetting the working inventory
and re-applying IMG_3537, the dashboard showed the misplacement and the bottle question.
The answer was left to the user.

## Shipment intake and stocking sessions — 2026-09-27

Implemented on `feature/shipment-stocking`; see [backend/SHIPMENTS.md](backend/SHIPMENTS.md).

- Dashboard review/import for the eight synthetic deliveries: canonical JSON,
  supplier JSON, CSV, XML, and the documented synthetic EDI profile. PDFs are reference
  documents, not automatically parsed. Supplier/invoice deduplication also works
  across document formats; conflicting contents require reconciliation.
- Starting a shipment creates lot/expiry receipts and staged off-shelf stock.
  Import alone changes no stock; starting and finishing cannot double-receive it.
- Employee-selected incoming line/lot binds to the next replay pickup. CV determines
  release location through existing confidence, correction, counter, and disposal
  rules. No medication/region answers are embedded in sensor signals.
- Persistent per-line stocking reconciliation, documented shortages, and immutable
  completion reports. Missing/unresolved placements block completion; short or
  disposed stock finishes with visible discrepancies. One active shipment and bottle.
- Mongo persistence includes shipments and staging, with revision checks and existing
  rollback/deduplication semantics. Setup/recording changes are blocked while stocking.
- Not included: automatic shelf optimization, arbitrary supplier/PDF intake, an Atlas
  deployment, or the unmerged live wristband adapter. Real-world CV accuracy is not
  established by these workflow tests.
- Validation: 266 backend/simulation tests pass with real local MongoDB, including
  22 shipment-specific cases; dashboard production build passes. All eight imported
  shipments were inspected in an isolated browser preview. No new Unity run needed.

## IMG_3537-only presentation — 2026-09-27

The deployment now has one recording and one camera named Pharmacy security camera.
Other room/view/recording fixtures are archived outside the served workspace. The
setup selects its camera at startup while preserving the original dashboard and navigation.
A separate pharmacy ID preserves the earlier demo's inventory history.

The supplied scan remains the room reference. Four illustrative shelf boxes (two
levels, two positions per level), four named medications with fictional demo stock, and a
counter are prepared. Camera polygons are presentation-only: the scan has not been
registered to the footage and no stock mutations are inferred from these drawings.

The new Unity presentation reuses Rocketbox and bottle assets and follows the six
visually reviewed actions at 14.60, 16.75, 28.70, 31.65, 37.30 and 40.35 seconds.
Body motion is authored from video review, not calibrated motion capture. It is
separate from the evidence-driven timeline and emits no inventory events.

Privacy presentation supersedes the earlier always-visible skeleton request for
this demo: actual CV skeletons and the separately labeled simulation rig appear
only in the one second preceding each contact. Raw footage and analysis are still
retained; this is display minimization, not anonymization or data deletion.

Validation: 35 targeted backend tests pass (one integration check skipped); dashboard production build passes.
Unity 6000.6.3f1 rendered 667 frames at 960x540 / 15 FPS. All six actions completed,
with zero downgraded reaches and zero collision guard holds; four labels/bottles.
The licensed Windows editor produced the render, then it was deployed as MP4.
Server-side fresh rendering still requires Linux Unity activation.


The user clarified that removing other demos must not remove the original interface.
Restored App.jsx from the original repository commit a14f5f2. IMG_3537 is the only
recording, auto-loaded into the original player, including Real / Simulation /
Side by side. The authored render is explicitly flagged presentation-only and
never masquerades as a render derived from confirmed inventory decisions. Named
medications are Metformin 500 mg, Atorvastatin 20 mg, Ibuprofen 200 mg, and
Amoxicillin 500 mg, with fictional opening stock. Existing quantities and audit
history are preserved during the medication rename. Privacy windows remain.
