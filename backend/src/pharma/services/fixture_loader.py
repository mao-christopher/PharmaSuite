"""Scenario fixture loading service for initializing MongoDB state before replay runs."""

import json
from pathlib import Path
from typing import Dict, Any, List
from motor.motor_asyncio import AsyncIOMotorDatabase
from pharma.db.connection import get_database, init_indexes
from pharma.services.layout import build_initial_state, load_layout


def load_json(path: Path) -> Any:
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


async def load_scenario_into_db(scenario_dir: Path, layouts_dir: Path, db: AsyncIOMotorDatabase = None):
    """Reset database collections and populate from the scenario and its shared layout."""
    database = db if db is not None else get_database()
    layout = load_layout(layouts_dir, load_json(scenario_dir / "scenario.json")["layout_id"])
    regions, inventory, receipts = build_initial_state(layout)

    # Clear existing collections
    collections = [
        "medications", "regions", "receipts", "inventory",
        "events", "movement_sessions", "transactions", "disposals", "alerts"
    ]
    for coll_name in collections:
        await database[coll_name].delete_many({})

    await init_indexes(database)

    calibration = {"layout_id": layout.layout_id, "calibration_version": layout.calibration_version}
    regions_data = [{**r.model_dump(mode="json"), **calibration} for r in regions]
    if regions_data:
        await database.regions.insert_many(regions_data)

    medications_data = [m.model_dump() for m in layout.medications]
    if medications_data:
        await database.medications.insert_many(medications_data)

    inventory_data = [i.model_dump() for i in inventory.values()]
    if inventory_data:
        await database.inventory.insert_many(inventory_data)

    receipts_data = [r.model_dump() for r in receipts]
    if receipts_data:
        await database.receipts.insert_many(receipts_data)

    # 5. Transactions
    transactions_data = load_json(scenario_dir / "transactions.json")
    if transactions_data:
        await database.transactions.insert_many(transactions_data)

    # 6. IMU Events
    events_data = load_jsonl(scenario_dir / "imu_events.jsonl")
    if events_data:
        await database.events.insert_many(events_data)

    return {
        "regions_count": len(regions_data),
        "medications_count": len(medications_data),
        "inventory_count": len(inventory_data),
        "receipts_count": len(receipts_data),
        "transactions_count": len(transactions_data),
        "events_count": len(events_data),
    }
