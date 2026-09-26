# Working on Pharma

## Purpose and authority

Build a pharmacy inventory demo that combines YOLO pose estimation on prerecorded
room-camera footage with synchronized mock IMU events. Read `plan.md` before
implementation; it records agreed product behavior, milestones, and open decisions.
The plan distinguishes implemented simulation work from future inventory features.
Read `simulation/README.md` and `simulation/VALIDATION.md` for that project.

## Scope

- Fixed, calibrated cameras with visibility-driven handoff during multi-shelf workflows,
  one active technician, one bottle being handled at a time,
  and one active prescription containing one medication and strength.
- Preconfigured shelf rectangles, each assigned one medication + strength;
  separately configured dispensing-counter and disposal regions.
- Use an actual CV pipeline on rendered footage. The simulator uses Unity 6000.6.3f1.
  Render offline, then replay the video and mock sensor events together.
- Store inventory, receipts, events, alerts, and employee corrections in MongoDB.
- Physical IMU hardware, firmware, and real pickup/release recognition are separate
  work. Do not modify or develop that code as part of this demo. Define an input
  contract and mock adapter; real integration remains optional future work.
- Automatic shelf segmentation, multiple simultaneous technicians/prescriptions,
  and physical pill counting are outside the first demo.

## Non-negotiable behavior

1. Pool tablet balances by medication + strength. Do not infer per-bottle tablet
   balances. Keep receiving/expiry records so employees can identify disposed stock.
2. Distinguish bottles physically on shelves, bottles temporarily off shelves,
   and total undisposed bottles. Pickup changes shelf count, not total stock.
3. Derive location from CV. Mock IMU events may indicate pickup, movement, or
   release but must not carry shelf IDs, medication identities, or correct answers.
4. A release at the dispensing counter is valid temporary placement. Preserve the
   active bottle's medication and original designated shelf until return/disposal.
5. A release on the wrong shelf produces an alert. Merely reaching or passing a
   hand through a region does not. Keep the bottle's original medication identity
   after misplacement; do not relabel it from the destination shelf.
6. Uncertain location requires employee confirmation. Show pending/uncertain state;
   never silently turn low-confidence evidence into a definite inventory change.
7. Accepted disposal removes one bottle and opens a form listing candidate received
   bottles/batches with medication, strength, expiry, and available lot information.
   The employee identifies the disposed stock and enters discarded tablet quantity.
8. With multiple bottles, no quantity entry means assumed empty (zero tablets).
   Preserve this assumption visibly and allow later correction. With exactly one
   bottle remaining immediately before disposal, no entry means its entire pooled
   tablet balance is discarded. Explicit conflicting entries require reconciliation.
9. Expiry alerts identify medication/strength and expiry; workers find the bottle.
   Expiry alone does not subtract inventory. Clear the relevant alert only after
   disposal is matched to the expired receiving record; partial disposal may leave
   other expired bottles outstanding.
10. Subtract prescription tablets once at confirmed filling or finalized payment/
    receipt, using the same transaction ID. Arrival and bottle pickup do not deduct.
11. Zero total bottles triggers an out-of-stock alert; zero on-shelf bottles with a
    bottle at the counter is a different condition. Preserve conflicting pill/bottle
    balances as reconciliation issues rather than inventing missing quantities.

12. Simulation agents must obey explicit task and bottle-ownership states. Use
    collision-aware paths and swept body/arm/bottle checks; do not interpolate bodies
    through furniture. Blocked/unreachable actions must not emit completion events.
    Re-render footage and repeat collision/CV checks when movement logic changes.
13. When the simulation is finalized, export a separate room-camera video for every
    individual action as well as full workflows. Include synchronized clip-relative
    mock IMU events, calibration, and an action index; validate each clip through CV.
    See `plan.md` for the pending final action-recording deliverable.
14. CV-overlay videos must use pose estimates from rendered pixels and preserve
    uncertainty. The user also requires an always-visible, through-wall skeleton:
    provide this as a clearly labeled simulation X-ray view using Unity rig truth.
    Keep that data evaluator-only and separate from CV/runtime inventory inputs.
    A projected wrist/region match alone cannot establish depth or a stock mutation.

15. Multi-shelf workflows must support switching between calibrated cameras when
    the technician's arm skeleton is no longer observable in the active camera.
    The demo must demonstrate POV handoff to a camera that can observe the arm.
    Use actual CV visibility/confidence for the decision; the simulation X-ray is
    presentation-only and must not imply camera visibility through an obstacle.
    Preserve the shared clock, technician/bottle movement session, medication
    identity, inventory state, and event IDs across camera switches. A switch must
    not create another pickup/release or duplicate an inventory mutation. If no
    camera has adequate evidence, retain uncertainty and request confirmation.
    Each observation must identify its camera and calibration version.
16. The intended final presentation mixes real-world and simulated footage with
    seamless transitions. This is project context and future presentation work;
    implementing or editing those transitions is not part of the current MongoDB
    migration. Automatic camera handoff is a requirement, not an implemented claim.

## Repository conventions

- Existing code: `backend/src/pharma/{config,detect,pose,train}.py`; thin CLIs in `backend/scripts/`.
  `backend/scripts/pose.py` runs the existing pose helper with `yolo11n-pose.pt` by default.
- Unity code lives in `simulation/Assets/Pharma/`; the generated scene/materials
  and stable asset metadata are committed. Fetch the pinned character assets using
  `simulation/tools/fetch_character.py`; do not commit large source art or recordings.
- Keep CV, replay, event association, inventory rules, persistence, and dashboard
  interfaces separate. Extend this Python package rather than duplicate inference.
- Implement one milestone at a time. Record assumptions and measured results in
  `plan.md`; do not present proposed confidence thresholds as validated accuracy.
- Keep frame dimensions, camera/calibration version, video timestamps, and event
  timestamps explicit. Playback speed must not change event association.
- Preserve raw events and correction history. Use stable IDs and idempotent writes;
  duplicate delivery or restart must not repeat decrements or alerts.
- Use synthetic medication/prescription fixtures. Keep credentials in environment
  configuration. Do not commit `.env`, model weights, large videos, or datasets.
- Keep simulator ground truth in evaluator-only files. Inventory decisions must
  not read Unity object transforms, object IDs, or scripted outcome labels.

## Validation and completion

- For documentation-only changes, inspect the diff and run `git diff --check`.
  Do not download model weights or install packages solely to validate Markdown.
- For code changes, run relevant deterministic tests and the existing `pytest`
  suite as appropriate. Existing inference smoke tests can download weights and a
  remote sample; report unavailable dependencies/network instead of claiming a pass.
- Test duplicate events, restart/replay, uncertain locations, wrong returns,
  counter placement, disposal defaults and corrections, expiry matching, and
  prescription completion/payment deduplication.
- Evaluate CV on rendered footage with separately held ground truth. Report wrong
  confident assignments separately from abstentions and processing speed.
- Use a feature branch and a reviewable PR. Describe actual changes and checks;
  do not claim simulation success or real-pharmacy reliability without evidence.
