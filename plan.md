# Pharmacy inventory demo plan

## Status and objective

Planning baseline from the product discussion. No milestones below are implemented
by this documentation change. Repository inspected at `59bd867`: Python YOLO11
pose/detection/training helpers, CLI scripts, Docker configuration, and detection
smoke tests exist. Inventory persistence, dashboard, replay fusion, and simulation
are new work.

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
| Camera | Fixed room-camera POV with visible shelf/counter/disposal regions |
| Shelf map | Employee-drawn polygons on the dashboard Setup page (see "Shared camera layout") |
| Medication identity | Infer from the configured pickup region, not label OCR |
| Rendering | Moderately realistic human and motions; Unity proposed default |
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

Use Python for the existing inference pipeline and proposed event/inventory service.
A small web dashboard is proposed; exact API/UI frameworks are implementation
choices, not user commitments. Start with replayable files before streaming.

The simulator may know exact bottle and joint transforms for animation and scoring.
The runtime receives only rendered video, legitimate mock sensor events, configured
regions, and business transactions. A sensor ID may identify a source but must not
secretly encode the medication, shelf, or correct placement outcome.

YOLO pose provides body keypoints, including wrists; it does not by itself prove
that a bottle was grasped, released, or counted. This demo assumes the mock IMU
adapter supplies those action events. Real IMU action recognition is unvalidated
separate work. The one-bottle constraint allows an accepted action to change the
count by one; do not describe this as visual counting of arbitrary bottle piles.

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

- At each signal, both wrists are taken from the frame at the signal's media time
  (or the nearest frame within 3 frames that shows a confident wrist, conf >= 0.35).
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

- Live state is saved to `data/state/pharmacy.json` (atomic write, gitignored) after
  every change and reloaded on server start. This JSON store stands in for MongoDB;
  moving it into the collections above remains open.
- A recording's signals change inventory the first time the playhead passes them, or
  all at once with "Apply". Applied event IDs are recorded per recording, so replays,
  seeks backward, restarts, and repeated Apply calls never apply a signal twice and
  never undo one. Movement sessions are namespaced `<recording>:<session>` because
  every upload numbers its sessions from `sess_001`.
- Recordings apply in whatever order they are played or applied, not by capture time.
  A partially played recording leaves its bottle in hand until the rest is applied.
- Deleting an uploaded recording removes its files; inventory changes it made stay.
  Bundled fixtures cannot be deleted.
- "Reset to opening stock" restores the layout's preset batches, clears alerts,
  disposals and prescription deductions, and marks every recording unapplied.
- An append-only history records signals, shipments, disposals, confirmations,
  prescription changes, uploads, deletions, layout saves, and resets.

Use unique IDs and atomic/idempotent processing so replay, retries, and restart do
not repeat mutations. Keep event acceptance and its stock update consistent across
crashes. Choose a MongoDB transaction-capable setup or a documented recoverable
event-ledger approach before implementing multi-document writes. The JSON store
writes the whole state in one atomic replace, so an event and its stock update land
together; a crash between applying and saving can lose the latest change but not
split it.

## Implementation milestones

1. **Pose feasibility gate.** Build a minimal Unity scene with one textured, clothed,
   rigged human, shelves, counter, trash region, and fixed camera. Render a reach,
   counter placement, return, and disposal. Run `scripts/pose.py` on the clip and
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

- Unity version, character/animation assets and their licenses, camera geometry,
  hardware, dashboard framework, and numerical CV thresholds remain to be selected.
- Unity is the default proposal, not a claim of measured performance. Godot is an
  alternative if the feasibility spike exposes setup/resource problems. Prerecorded
  playback removes the requirement to render and run inference simultaneously.
- A single 2D camera can have overlapping projected shelf regions or hidden hands.
  Place the demo camera to reduce those failures; retain confirmation for the rest.
- Initial bottle counts come from receiving/setup. Event tracking cannot guarantee
  continuous physical truth after missed events; visibly track unresolved counts.
- Transaction deductions estimate tablet stock. Unrecorded waste and inaccurate
  source data can create discrepancies that CV cannot resolve.
- Real IMU timing, release detection reliability, and attachment/identity conventions
  require agreement with the separate hardware effort before real integration.
- The wrist is a proxy for the bottle. Shelf depth, which hand holds the bottle, and
  occlusion are not modeled; the nearest-region rule and its distance constant have
  not been evaluated against ground truth on rendered footage.
- Only the most confident person per frame is tracked; a second person in view can
  be picked instead of the technician.
- Live inventory now depends on the order recordings are applied. Clips recorded out
  of order, or applied twice under different uploads of the same footage, will be
  counted as separate real events.

## Dashboard design (implemented)

Decided 2026-09-26. The UI follows the Vercel DESIGN.md from awesome-design-md
(Geist and Geist Mono, ink on near-white, hairline borders, 6px controls, 8px cards),
the taste-skill redesign and minimalist rules (one accent, pastel status tones only,
no em-dashes, Phosphor icons instead of Lucide), and was audited against the Vercel
web interface guidelines (focus-visible rings, labelled icon buttons, skip link,
`aria-live` for updates, `Intl` formatting, confirm dialogs for destructive actions).
Headings and buttons use sentence case (the guidelines prefer Title Case; the other
two sources and the existing copy use sentence case). Light mode only, per request.

## Renderer references

- [Unity humanoid animation retargeting](https://docs.unity3d.com/6000.0/Documentation/Manual/Retargeting.html)
- [Unity Recorder](https://docs.unity.com/en-us/engine/6000.3/manual/packages-list/packages-all/pack-safe/com-unity-recorder)
- [Godot animation](https://docs.godotengine.org/en/stable/tutorials/animation/animation_tree.html)
- [Godot offline movie capture](https://docs.godotengine.org/en/stable/tutorials/animation/creating_movies.html)
