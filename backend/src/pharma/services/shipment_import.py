"""Strict adapters for the documented synthetic supplier formats, not arbitrary EDI/PDF."""

import csv
import hashlib
import io
import json
import re
from datetime import date, datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from pydantic import BaseModel, Field, StrictInt, model_validator
from pharma.db.models import medication_key_for

MAX_BYTES = 2_000_000


class ShipmentLine(BaseModel):
    line_id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    strength: str = Field(min_length=1, max_length=60)
    medication_key: str
    unit: str
    ndc: str
    manufacturer: str
    lot: str = Field(min_length=1, max_length=120)
    expiry: date
    units_per_bottle: StrictInt = Field(gt=0)
    ordered: StrictInt = Field(ge=0)
    shipped: StrictInt = Field(ge=0)
    damaged: StrictInt = Field(ge=0)
    accepted: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def consistent(self):
        if self.damaged > self.shipped or self.accepted != self.shipped - self.damaged:
            raise ValueError("Accepted bottles must equal shipped minus damaged.")
        if self.medication_key != medication_key_for(self.name, self.strength):
            raise ValueError("Medication key does not match name and strength.")
        return self


class Shipment(BaseModel):
    supplier: str = Field(min_length=1, max_length=80)
    invoice: str = Field(min_length=1, max_length=120)
    reference: str
    received_at: str
    lines: list[ShipmentLine] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def consistent(self):
        datetime.fromisoformat(self.received_at)
        if len({line.line_id for line in self.lines}) != len(self.lines):
            raise ValueError("Duplicate shipment line IDs.")
        return self


def integer(value):
    # Supplier text fields may be integer strings; never truncate decimals or accept booleans.
    if type(value) is int:
        return value
    if isinstance(value, str) and re.fullmatch(r"\d+", value.strip()):
        return int(value)
    raise ValueError(f"Expected an integer quantity, got {value!r}.")


def line(
    line_id,
    name,
    strength,
    unit,
    ndc,
    manufacturer,
    lot,
    expiry,
    pack,
    ordered,
    shipped,
    damaged=0,
    accepted=None,
    total=None,
):
    shipped, damaged, pack = integer(shipped), integer(damaged), integer(pack)
    if total is not None and integer(total) != shipped * pack:
        raise ValueError(
            f"Line {line_id}: shipped unit total disagrees with bottle count and pack size."
        )
    return ShipmentLine(
        line_id=str(line_id),
        name=name.strip().upper(),
        strength=strength.strip().upper(),
        medication_key=medication_key_for(name, strength),
        unit=unit.strip().lower(),
        ndc=re.sub(r"[^0-9]", "", ndc),
        manufacturer=manufacturer.strip().upper(),
        lot=lot.strip().upper(),
        expiry=expiry,
        units_per_bottle=pack,
        ordered=integer(ordered),
        shipped=shipped,
        damaged=damaged,
        accepted=shipped - damaged if accepted is None else integer(accepted),
    )


def canonical(item):
    lines = []
    for row in item["lines"]:
        parsed = line(
            row["line"],
            row["drug_name"],
            row["strength"],
            row["dosage_form"],
            row["ndc"],
            row["manufacturer"],
            row["lot_number"],
            row["expiry_date"],
            row["units_per_bottle"],
            row["bottles_ordered"],
            row["bottles_shipped"],
            row["bottles_damaged"],
            row["bottles_accepted"],
            row["total_units_shipped"],
        )
        if (
            row["medication_key"] != parsed.medication_key
            or integer(row["total_units_accepted"]) != parsed.accepted * parsed.units_per_bottle
        ):
            raise ValueError(
                f"Line {parsed.line_id}: inconsistent medication key or accepted unit total."
            )
        lines.append(parsed)
    return Shipment(
        supplier=item["distributor"]["code"],
        invoice=item["invoice_number"],
        reference=item["shipment_id"],
        received_at=item["arrival_time"],
        lines=lines,
    )


def from_json(text):
    data = json.loads(text)
    if "shipments" in data:
        return [canonical(s) for s in data["shipments"]]
    if "lines" in data:
        return [canonical(data)]
    if data.get("event") != "shipment.delivered" or data["supplier"]["code"] != "SGDX":
        raise ValueError("Unsupported JSON shipment schema.")
    s = data["shipment"]
    lines = [
        line(
            r["line"],
            r["product"]["name"],
            r["product"]["strength"],
            r["product"]["form"],
            r["ndc"],
            r["product"]["manufacturer"],
            r["lot"]["number"],
            r["lot"]["expiration"],
            r["units_per_bottle"],
            r["bottles"]["ordered"],
            r["bottles"]["shipped"],
            r["bottles"]["damaged"],
            total=r["total_units"],
        )
        for r in data["items"]
    ]
    result = Shipment(
        supplier="SGDX",
        invoice=s["invoice_number"],
        reference=s["shipment_id"],
        received_at=s["arrival_time"],
        lines=lines,
    )
    totals = data["totals"]
    if integer(totals["bottles_accepted"]) != sum(r.accepted for r in lines) or integer(
        totals["units_accepted"]
    ) != sum(r.accepted * r.units_per_bottle for r in lines):
        raise ValueError("Shipment summary does not match accepted lines.")
    return [result]


def from_csv(text):
    groups = {}
    for r in csv.DictReader(io.StringIO(text)):
        invoice = r["Invoice No"]
        if not invoice.startswith("NWPD-"):
            raise ValueError("Unsupported CSV supplier.")
        group = groups.setdefault(invoice, {"received_at": r["Delivered At"], "lines": []})
        if group["received_at"] != r["Delivered At"]:
            raise ValueError("Conflicting delivery times within one invoice.")
        expiry = datetime.strptime(r["Exp Date (MM/DD/YYYY)"], "%m/%d/%Y").date().isoformat()
        group["lines"].append(
            line(
                r["Line"],
                r["Item Description"],
                r["Strength"],
                r["Form"],
                r["NDC"],
                r["Manufacturer"],
                r["Lot"],
                expiry,
                r["Pack Size"],
                r["Qty Ordered (Btl)"],
                r["Qty Shipped (Btl)"],
                r["Qty Damaged (Btl)"],
                total=r["Total Units"],
            )
        )
    return [Shipment(supplier="NWPD", invoice=k, reference=k, **v) for k, v in groups.items()]


def from_xml(text):
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("XML document types and entities are not supported.")
    root = ET.fromstring(text)
    if root.tag != "{urn:synthetic:cascade-rx:asn:1.2}AdvanceShipNotice":
        raise ValueError("Unsupported XML shipment schema.")
    for node in root.iter():
        node.tag = node.tag.split("}")[-1]
    h = root.find("Header")
    lines = []
    for r in root.findall("Items/Item"):
        lines.append(
            line(
                r.attrib["line"],
                r.findtext("Product/Name"),
                r.findtext("Product/Strength"),
                r.find("Product").attrib["form"],
                r.findtext("NDC"),
                r.findtext("Product/Manufacturer"),
                r.findtext("Lot"),
                r.find("Lot").attrib["expires"],
                r.findtext("Quantity/UnitsPerBottle"),
                r.findtext("Quantity/BottlesOrdered"),
                r.findtext("Quantity/BottlesShipped"),
                r.findtext("Quantity/BottlesDamaged"),
                total=r.findtext("Quantity/TotalUnits"),
            )
        )
    if integer(root.findtext("Summary/BottlesAccepted")) != sum(
        r.accepted for r in lines
    ) or integer(root.findtext("Summary/UnitsAccepted")) != sum(
        r.accepted * r.units_per_bottle for r in lines
    ):
        raise ValueError("XML summary does not match accepted lines.")
    return [
        Shipment(
            supplier="CSRX",
            invoice=h.findtext("InvoiceNumber"),
            reference=h.findtext("ASNNumber"),
            received_at=h.findtext("DeliveredDateTime"),
            lines=lines,
        )
    ]


def from_edi(text):
    segments = [s.strip().split("*") for s in text.split("~") if s.strip()]
    if sum(s[0] == "ST" for s in segments) != 1 or not any(
        s[:2] == ["ST", "856"] for s in segments
    ):
        raise ValueError("Expected one synthetic MHSC 856 transaction.")
    if not any(s[:3] == ["GS", "SH", "MHSC"] for s in segments):
        raise ValueError("Only the documented synthetic MHSC EDI format is supported.")
    invoice = received = reference = None
    rows = []
    row = None
    count = None
    for s in segments:
        if s[:2] == ["REF", "IV"]:
            invoice = s[2]
        elif s[0] == "BSN":
            reference = s[2]
        elif s[:2] == ["DTM", "017"]:
            received = datetime.strptime(s[2] + s[3], "%Y%m%d%H%M").isoformat()
        elif s[0] == "LIN":
            row = {"line_id": s[1], "ndc": s[3], "lot": s[5], "damaged": 0}
            rows.append(row)
        elif s[0] == "CTT":
            count = integer(s[1])
        elif row is not None:
            if s[0] == "SN1":
                row.update(shipped=s[2], ordered=s[5])
            elif s[0] == "PO4":
                row["pack"] = s[1]
            elif s[:2] == ["DTM", "036"]:
                row["expiry"] = datetime.strptime(s[2], "%Y%m%d").date().isoformat()
            elif s[0] == "PID" and s[2] == "MF":
                row["manufacturer"] = s[5]
            elif s[0] == "PID":
                match = re.fullmatch(r"(.+) (\d+(?:\.\d+)?(?:MCG|MG|IU)) (TABLETS|CAPSULES)", s[5])
                if not match:
                    raise ValueError(
                        "Unsupported EDI product description; cannot safely infer strength."
                    )
                row.update(zip(("name", "strength", "unit"), match.groups()))
    if count != len(rows):
        raise ValueError("EDI line count mismatch.")
    return [
        Shipment(
            supplier="MHSC",
            invoice=invoice,
            reference=reference,
            received_at=received,
            lines=[line(**r) for r in rows],
        )
    ]


def parse_shipments(filename, content):
    if len(content) > MAX_BYTES:
        raise ValueError("Shipment files must be at most 2 MB.")
    adapters = {".json": from_json, ".csv": from_csv, ".xml": from_xml, ".edi": from_edi}
    adapter = adapters.get(Path(filename).suffix.lower())
    if adapter is None:
        raise ValueError(
            "Upload a supported JSON, CSV, XML or synthetic EDI file. PDF slips are reference documents only."
        )
    try:
        results = adapter(content.decode("utf-8-sig"))
        if not results or len(results) > 100:
            raise ValueError("Expected 1–100 shipments.")
        ids = [shipment_id(s) for s in results]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate supplier invoices in file.")
        return results
    except (KeyError, TypeError, AttributeError, IndexError, UnicodeError, ET.ParseError) as exc:
        raise ValueError("Malformed or unsupported shipment fields.") from exc


def shipment_id(shipment):
    return hashlib.sha256(
        f"{shipment.supplier.upper()}:{shipment.invoice.upper()}".encode()
    ).hexdigest()[:24]


def content_key(shipment):
    # Format-specific references, name capitalization and missing EDI timezone do not
    # create a second delivery. All quantity/identity/lot/expiry differences conflict.
    return json.dumps(
        sorted([r.model_dump(mode="json") for r in shipment.lines], key=lambda r: r["line_id"]),
        sort_keys=True,
    )


def import_shipments(engine, shipments, filename, content, at):
    decisions = []
    for s in shipments:
        key = shipment_id(s)
        existing = engine.shipments.get(key)
        if existing and existing["content_key"] != content_key(s):
            raise ValueError(
                f"{s.invoice} already exists with different lines; reconcile instead of importing twice."
            )
        decisions.append((key, s, existing))
    # Validate the whole file before mutation; the API persists all records in one Mongo write.
    results = []
    for key, s, existing in decisions:
        if not existing:
            engine.shipments[key] = {
                **s.model_dump(mode="json"),
                "id": key,
                "content_key": content_key(s),
                "status": "imported",
                "imported_at": at,
                "source": {
                    "filename": Path(filename).name,
                    "sha256": hashlib.sha256(content).hexdigest(),
                },
                "stocking": None,
            }
        results.append({"id": key, "duplicate": bool(existing)})
    return results
