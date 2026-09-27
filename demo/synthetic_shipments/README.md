# Synthetic pharmacy shipments

Fictitious wholesaler deliveries to one pharmacy (`Riverside Community Pharmacy`,
store `RCP-0142`) during September 2026. Every company, NDC, DEA number, lot,
tracking number, and price is made up. Nothing here describes a real product or shipment.

Each shipment arrives as a PDF packing slip/invoice plus one electronic
document. The distributor determines the electronic format, which shows that intake
has to accept more than one format:

| Shipment | Distributor | Arrived (local) | Electronic format | Scenario |
| --- | --- | --- | --- | --- |
| SHP-2026-0001 | Northwind Pharmaceutical Distribution (NWPD) | 2026-09-02 08:14 | `csv/` portal export | Normal; includes the demo's Amoxicillin 500mg and Ibuprofen 200mg |
| SHP-2026-0002 | Cascade Rx Wholesale (CSRX) | 2026-09-05 10:42 | `xml/` advance ship notice | Short-dated lot (expires 2026-10-31) |
| SHP-2026-0003 | Meridian Health Supply (MHSC) | 2026-09-09 07:55 | `edi/` X12 856-style ASN | Partial fill and a backordered line |
| SHP-2026-0004 | Summit Generics Direct (SGDX) | 2026-09-12 13:20 | `json/` delivery webhook | One product split across two lots/expiries |
| SHP-2026-0005 | Northwind (NWPD) | 2026-09-16 08:03 | `csv/` | One bottle damaged in transit and not accepted |
| SHP-2026-0006 | Cascade Rx (CSRX) | 2026-09-19 11:30 | `xml/` | Normal |
| SHP-2026-0007 | Meridian (MHSC) | 2026-09-23 07:48 | `edi/` | Short-dated lot |
| SHP-2026-0008 | Summit (SGDX) | 2026-09-25 15:05 | `json/` | Normal |

## Bottles and units are separate quantities

Every line records bottle counts (ordered, shipped, damaged, and accepted) separately
from units per bottle and total units (tablets or capsules). Bottle counts drive the
physical shelf stock. Units form the pooled balance for each medication and strength.

## Files

- `pdf/`: printable packing slips/invoices with the supplier, ship-to, invoice, PO, ASN,
  ship and arrival times, carrier, tracking, carton count, temperature log, lines
  (NDC, product, strength, form, manufacturer, lot, expiry, bottles, units, price,
  status), totals, and the receiver.
- `csv/`, `xml/`, `edi/`, `json/`: the same shipment in each distributor's own
  electronic schema and field names.
- `shipments_manifest.json`: canonical normalized record of all eight shipments.
- `receiving_ledger.csv`: one row per accepted lot (`receipt_id`, medication key,
  lot, expiry, `bottles_received`, `units_received`). Backordered lines with zero
  bottles are left out.
- `pooled_stock_summary.csv`: totals per medication and strength
  (`total_bottles`, `total_units`, lot count, and earliest expiry).

Medication keys follow the backend's `medication_key_for` convention
(for example, `AMOXICILLIN_500MG`). No parser for these files exists yet.
