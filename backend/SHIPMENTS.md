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

## API

- `POST /api/shipments/import?preview=true`: multipart `file`, validated preview.
- `POST /api/shipments/import`: multipart `file`, atomic import/deduplication.
- `GET /api/shipments`: deliveries and reconciliation reports.
- `POST /api/shipments/{id}/start`: bind to the current paused recording.
- `POST /api/shipments/{id}/select`: `{line_id, event_id}` for next pickup.
- `POST /api/shipments/{id}/shortage`: `{line_id, quantity, reason, operation_id}`.
- `POST /api/shipments/{id}/finish`: reconcile/freeze completion.

The inventory snapshot now includes `shipments` and per-medication
`staged_bottles`. Old MongoDB documents load with empty shipments and zero staging.
The explicit whole-inventory reset clears shipments along with the rest of stock.

## Validation and boundaries

The tests compare all eight supplier documents against the canonical manifest and
exercise duplicate/conflicting imports, invalid input, staged stock, replay gates,
wrong shelves, employee confirmation, counter continuity, disposal, shortages,
restart, and Mongo write rollback. They do not establish pose or wearable accuracy.

Stocking works with prerecorded recordings. Live wristband events do not yet go
through these stocking checks. Shelf assignment is employee-configured; automatic
shelf optimization is not implemented.
