# Shipment import and stocking

Open **Shipments** in the dashboard. This workflow uses the existing MongoDB
inventory aggregate, camera calibration, pose-based region association, and replay
signals. It does not require a Unity run or a new model.

## Import

1. Choose a file and **Review file**. Check the medication, strength, lot, expiry,
   ordered/shipped/damaged/accepted bottles. Review does not write inventory.
2. **Import reviewed shipments** stores the validated delivery in MongoDB. Import
   alone does not receive or shelve its stock.
3. Supported adapters are the repository's canonical shipment manifest (or a single
   canonical shipment), Northwind CSV, Cascade XML, Summit JSON webhook, and Meridian
   synthetic X12-856-style EDI. Files are UTF-8, at most 2 MB, and contain up to 100
   shipments. PDF packing slips remain reference documents. Generic supplier formats,
   OCR, and arbitrary production EDI require additional adapters.

Supplier code + invoice identifies a delivery. Reimporting another representation
of the same delivery is a no-op. Changed identity/lot/expiry/quantity fields under
that invoice cause a conflict instead of adding stock again. A multi-shipment file
is validated before any records are written. Imports retain the normalized lines,
source filename, and source SHA-256; raw supplier files remain with the operator.
The EDI example has no timezone; its delivery time remains explicitly naive rather
than inventing a timezone. Arrival/reference differences between document formats
are not treated as new deliveries.

## Stock one shipment

1. Configure all accepted medications and shelf destinations in Room. Load a ready
   stocking recording with synchronized pickup/release events. Pause the player.
2. **Start stocking** binds the shipment to this recording. Each accepted line
   creates a lot/expiry receipt, adds total bottles and pooled tablets, and places
   its bottles in `staged_bottles`, not on the shelf. Unknown medications or missing
   destinations block the entire start before stock changes. Backordered bottles
   and damaged bottles excluded from acceptance never enter stock.
3. Before each incoming pickup, select its shipment line/lot. This identifies the
   bottle from receiving; pose cannot identify an unstocked bottle's medication.
   Selection binds to the next unapplied pickup event, not any arbitrary later event.
4. Play. The pickup transfers one staged bottle to held. The release uses the normal
   CV association and existing uncertainty confirmation, counter placement,
   misplacement and disposal workflows. No shelf ID is added to sensor signals.
   Corrective pickups preserve the same bottle/lot, including after a counter stop.
5. Playback pauses at an unassigned pickup. Select the next line after the current
   bottle is correctly shelved or its disposal is resolved. Correct a wrong shelf
   by replaying the same bottle's corrective pickup and release. Low-confidence
   locations require dashboard confirmation; do not silently guess a shelf.
6. For accepted bottles that did not actually arrive, **Document shortage** requires
   a count and reason. It subtracts only still-staged bottles and their starting
   units, updates the lot count, and preserves the discrepancy. Retry IDs prevent a
   duplicate subtraction. Use the disposal region/form for physical discards; the
   selected disposal receipt must match the identified stocking lot.
7. **Finish reconciliation** requires every accepted bottle to be correctly placed,
   explicitly short, or disposed with its form resolved. It freezes the result,
   including discrepancies, so later dispensing does not rewrite receiving history.
   Finishing does not add inventory again.

One shipment/technician/bottle is active at a time. Setup/calibration edits, changing
its recording, inventory reset, and manual batch disposal are blocked while it is
active. Existing shelf and dispensing workflows continue after reconciliation.
Session selection, movements, receipts, exceptions, and applied event IDs survive
restart in the same atomic MongoDB document. No custom Atlas deployment is required;
use the application's configured MongoDB URI.

## Shelf layout from shipments

The **Shelf layout from shipments** card on the Shipments page proposes a slot for
every medication shelved in a scanned room and every accepted line of a shipment that
has not been stocked yet. It fills the shelving already tagged on the Room page: each
unit's rows are split into slots at least 25 cm wide.

Mix-up risks come first. Name rules flag the same drug in another strength or release
form, pairs from published confused-name lists (a subset of lists such as ISMP's),
sound-alike and look-alike names, and class siblings that share a name stem (two
-sartans, two -statins). With `META_API_KEY` set in `backend/.env`, Meta's Llama API
also lists pairs the rules miss and proposes the placement. Every risk pair must land on
**different shelves** (another row or unit) and never in touching slots, including
directly above or below. Other units are preferred. After that, shelved medications
stay where they are, and the busiest stock goes at waist or eye height near the counter.

The model's answer is untrusted. It is used only if it names every medication once, uses
listed slots, and separates the risk pairs at least as well as the deterministic built-in
planner. Otherwise the built-in plan is shown with the reason, and it still separates any
pairs the model flagged. The same planner runs when no token is configured. The name
thresholds are unvalidated heuristics that deliberately cast a wide net. They are not a
clinical look-alike/sound-alike review.

A plan changes nothing until an employee applies it. The plan shows every risk pair and
where it landed. If the shelving can't separate a pair, applying needs an explicit
acknowledgement. Applying is blocked during stocking and while a bottle is held, at the
counter, misplaced or awaiting confirmation. It adds new medications to the catalog
with no opening stock, replaces the room's shelf boxes with one slot box per medication,
regenerates every registered camera's regions, and records a `shelf_layout` history
entry. Stock counts never change. The full-width rows are saved in
`rooms/<id>/shelving.json`, so empty slots remain available for the next plan.

`demo/synthetic_shipments/mixups/lookalike_delivery.json` is a synthetic delivery with
four risk pairs (hydroxyzine/hydralazine, metformin/metronidazole, tramadol/trazodone,
and two atorvastatin strengths) for trying the planner.

## API

- `POST /api/shipments/import?preview=true`: multipart `file`, validated preview.
- `POST /api/shipments/import`: multipart `file`, atomic import/deduplication.
- `GET /api/shipments`: deliveries and reconciliation reports.
- `POST /api/shipments/{id}/start`: bind to the current paused recording.
- `POST /api/shipments/{id}/select`: `{line_id, event_id}` for next pickup.
- `POST /api/shipments/{id}/shortage`: `{line_id, quantity, reason, operation_id}`.
- `POST /api/shipments/{id}/finish`: reconcile/freeze completion.
- `GET /api/shelf-layout/status`: whether a Meta API token is configured, and the rooms.
- `POST /api/shelf-layout/plan`: `{room_id, use_meta}`, a proposed layout with risk pairs.
  It runs outside the inventory lock, so a slow Llama call doesn't block other requests.
- `POST /api/shelf-layout/apply`: `{room_id, room_version, assignments, source,
  mixup_pairs, acknowledge_mixups}`; 409 if the room changed, or if pairs would stay together unacknowledged.

The inventory snapshot now includes `shipments` and per-medication
`staged_bottles`. Old MongoDB documents load with empty shipments and zero staging.
The explicit whole-inventory reset clears shipments along with the rest of stock.

## Validation and boundaries

The tests compare all eight supplier documents against the canonical manifest and
exercise duplicate/conflicting imports, invalid input, staged stock, replay gates,
wrong shelves, employee confirmation, counter continuity, disposal, shortages,
restart, and Mongo write rollback. They do not establish pose or wearable accuracy.
The unmerged live-browser/wristband PR is separate: this implementation connects to
the merged prerecorded CV pipeline. Live stocking needs to route its events through
these stocking checks when that adapter is integrated. Proposed shelf layouts are
reviewed and applied by an employee; they are never applied automatically.

Verified on 2026-09-27: 266 backend/simulation tests passed using a real local
MongoDB instance (22 shipment tests); the dashboard production build passed with
its existing large-chunk warning. An isolated running server imported all eight
shipments and displayed their reconciliations in the browser. No Unity rerender or
physical wristband validation was performed.
