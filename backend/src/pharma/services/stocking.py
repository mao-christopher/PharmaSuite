"""One shipment/technician/bottle at a time, using the existing CV movement engine.

The employee identifies incoming stock. Sensor packets never contain medication or
shelf answers. Placements and corrections use normal region evidence/confirmation.
"""

from pharma.db.models import MovementSession, shelf_region_id


def active_shipment(engine):
    return next((s for s in engine.shipments.values() if s["status"] == "stocking"), None)


def get_shipment(engine, key):
    if key not in engine.shipments:
        raise ValueError("Shipment not found.")
    return engine.shipments[key]


def resolved_disposal(engine, session):
    record = engine.disposals.get(f"disp_{session.session_id}")
    return session.state == "DISPOSED" and record is not None and record.status == "resolved"


def unfinished(engine, shipment):
    if not shipment.get("stocking"):
        return []
    return [
        engine.sessions[sid]
        for line in shipment["stocking"]["lines"].values()
        for sid in line["movements"]
        if engine.sessions[sid].state != "ON_DESIGNATED_SHELF"
        and not resolved_disposal(engine, engine.sessions[sid])
    ]


def report(engine, shipment):
    if shipment.get("completion"):
        return shipment["completion"]
    stock = shipment.get("stocking")
    rows = []
    for line in shipment["lines"]:
        tracked = stock["lines"][line["line_id"]] if stock else {"movements": [], "short": 0}
        movements = [engine.sessions[sid] for sid in tracked["movements"]]
        correct = sum(s.state == "ON_DESIGNATED_SHELF" for s in movements)
        disposed = sum(resolved_disposal(engine, s) for s in movements)
        rows.append(
            {
                **line,
                "correct": correct,
                "short": tracked["short"],
                "staged": line["accepted"] - len(movements) - tracked["short"],
                "disposed": disposed,
                "unresolved": len(movements) - correct - disposed,
                "movements": [s.model_dump() for s in movements],
            }
        )
    return {
        "lines": rows,
        "can_finish": bool(stock) and all(r["staged"] == 0 and r["unresolved"] == 0 for r in rows),
    }


def start(engine, key, recording, regions, at):
    shipment = get_shipment(engine, key)
    if shipment["status"] == "stocking" and shipment["stocking"]["recording"] == recording:
        return shipment  # Retry must not receive the shipment again.
    if shipment["status"] != "imported":
        raise ValueError("This shipment has already been started or reconciled.")
    if active_shipment(engine):
        raise ValueError("Finish the active shipment before starting another.")
    if any(
        s.state in ("HELD", "AT_COUNTER", "MISPLACED", "NEEDS_CONFIRMATION")
        for s in engine.sessions.values()
    ):
        raise ValueError("Resolve existing bottle movements before stocking.")
    shelves = {r.medication_key for r in regions if r.region_type == "designated_shelf"}
    missing = {
        r["medication_key"]
        for r in shipment["lines"]
        if r["accepted"]
        and (r["medication_key"] not in engine.inventory or r["medication_key"] not in shelves)
    }
    if missing:
        raise ValueError(
            "Configure medications and their shelf regions first: " + ", ".join(sorted(missing))
        )
    tracked = {}
    for row in shipment["lines"]:
        key_med = row["medication_key"]
        receipt_id = None
        if row["accepted"]:
            receipt = engine.receive_stock(
                key_med,
                row["accepted"],
                row["units_per_bottle"],
                row["expiry"],
                shipment["received_at"],
                row["lot"],
                today_iso=at[:10],
            )
            receipt_id = receipt.receipt_id
            inv = engine.inventory[key_med]
            inv.shelf_counts[shelf_region_id(key_med)] -= row["accepted"]
            inv.staged_bottles += row["accepted"]
        tracked[row["line_id"]] = {
            "receipt_id": receipt_id,
            "movements": [],
            "short": 0,
            "exceptions": [],
        }
    shipment["status"] = "stocking"
    shipment["stocking"] = {
        "recording": recording,
        "started_at": at,
        "lines": tracked,
        "armed": None,
    }
    return shipment


def arm(engine, key, line_id, event_id):
    shipment = get_shipment(engine, key)
    if shipment["status"] != "stocking":
        raise ValueError("Start the shipment first.")
    stock = shipment["stocking"]
    if unfinished(engine, shipment):
        raise ValueError("Return or resolve the current bottle before selecting another.")
    row = next((r for r in report(engine, shipment)["lines"] if r["line_id"] == line_id), None)
    if row is None or row["staged"] < 1:
        raise ValueError("No unstocked bottles remain on that line.")
    stock["armed"] = {"line_id": line_id, "event_id": event_id}


def blocked_reason(engine, recording, event):
    shipment = active_shipment(engine)
    if not shipment:
        return None
    stock = shipment["stocking"]
    if stock["recording"] != recording:
        return "Finish the stocking session before processing another recording."
    if event["event_type"] != "pickup":
        return None
    open_moves = unfinished(engine, shipment)
    if open_moves:
        if all(s.state in ("MISPLACED", "AT_COUNTER") for s in open_moves):
            return None  # Correction of the same bottle goes through CV, not a new receipt.
        return "Resolve the bottle currently held or awaiting confirmation first."
    if stock["armed"] is None or stock["armed"]["event_id"] != event["event_id"]:
        return "Select the incoming shipment line for the next pickup on the Shipments page."
    return None


def pickup(engine, recording, event, sid, hands):
    """Returns a movement for a staged pickup, or None for an ordinary correction."""
    shipment = active_shipment(engine)
    if not shipment or shipment["stocking"]["recording"] != recording:
        return None
    problem = blocked_reason(engine, recording, event)
    if problem:
        raise ValueError(problem)
    stock = shipment["stocking"]
    open_moves = unfinished(engine, shipment)
    if open_moves:
        # Keep identity when fixing a misplaced/parked shipment bottle. An uncertain
        # correction must not let the ordinary source-shelf path invent a second bottle.
        session = open_moves[0]
        region, evidence = engine._locate(hands, ("designated_shelf", "dispensing_counter"))
        if region is None or region.region_id != session.current_location_id:
            engine.sessions[sid] = session
            engine._uncertain(
                session,
                "pickup",
                {
                    **evidence,
                    "reason": "stocking_correction_source_unconfirmed",
                    "stocking_source": session.current_location_id,
                    "stocking_previous_state": session.state,
                },
            )
            return session
        return engine._apply_pickup(sid, region, evidence)
    selected = stock["armed"]
    row = next(r for r in shipment["lines"] if r["line_id"] == selected["line_id"])
    inv = engine.inventory[row["medication_key"]]
    if inv.staged_bottles < 1:
        raise ValueError("No staged stock remains.")
    inv.staged_bottles -= 1
    inv.held_bottles += 1
    session = MovementSession(
        session_id=sid,
        medication_key=row["medication_key"],
        original_shelf_id=shelf_region_id(row["medication_key"]),
        state="HELD",
        current_location_id="receiving",
        evidence={
            "source": "employee_selected_shipment_line",
            "shipment_id": shipment["id"],
            "line_id": row["line_id"],
        },
    )
    engine.sessions[sid] = session
    stock["lines"][row["line_id"]]["movements"].append(sid)
    stock["armed"] = None
    return session


def shortage(engine, key, line_id, quantity, reason, operation_id, at):
    shipment = get_shipment(engine, key)
    if shipment["status"] != "stocking":
        raise ValueError("Shipment is not active.")
    row = next((r for r in report(engine, shipment)["lines"] if r["line_id"] == line_id), None)
    if row is None:
        raise ValueError("Unknown shipment line.")
    tracked = shipment["stocking"]["lines"][line_id]
    existing = next((x for x in tracked["exceptions"] if x["operation_id"] == operation_id), None)
    if existing:
        if existing["quantity"] != quantity or existing["reason"] != reason:
            raise ValueError("This correction ID already records different information.")
        return
    if not reason.strip() or quantity < 1 or quantity > row["staged"]:
        raise ValueError(
            "Document a reason and a shortage no larger than the remaining staged bottles."
        )
    inv = engine.inventory[row["medication_key"]]
    units = quantity * row["units_per_bottle"]
    if inv.staged_bottles < quantity or inv.total_bottles < quantity or inv.pooled_tablets < units:
        raise ValueError("Stock balance conflicts with this correction; reconcile inventory first.")
    inv.staged_bottles -= quantity
    inv.total_bottles -= quantity
    inv.pooled_tablets -= units
    receipt = engine.receipts[tracked["receipt_id"]]
    receipt.remaining_bottles -= quantity
    if receipt.remaining_bottles == 0:
        engine._resolve_alerts("expiry", receipt_id=receipt.receipt_id)
    if inv.total_bottles == 0:
        engine._add_alert(
            "out_of_stock",
            "error",
            row["medication_key"],
            "No bottles remain after shipment reconciliation.",
        )
    tracked["short"] += quantity
    tracked["exceptions"].append(
        {"quantity": quantity, "reason": reason, "operation_id": operation_id, "at": at}
    )
    if shipment["stocking"]["armed"] and shipment["stocking"]["armed"]["line_id"] == line_id:
        shipment["stocking"]["armed"] = None


def finish(engine, key, at):
    shipment = get_shipment(engine, key)
    if shipment["status"] in ("completed", "completed_with_discrepancies"):
        return shipment
    summary = report(engine, shipment)
    if not summary["can_finish"]:
        raise ValueError(
            "Stock all bottles, correct wrong placements and resolve uncertainty before finishing; document unreceived shortages explicitly."
        )
    shipment["status"] = (
        "completed_with_discrepancies"
        if any(r["short"] or r["disposed"] for r in summary["lines"])
        else "completed"
    )
    shipment["completed_at"] = at
    shipment["completion"] = summary
    shipment["stocking"]["armed"] = None
    return shipment
