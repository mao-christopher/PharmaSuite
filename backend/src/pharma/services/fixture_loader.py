"""Scenario fixture loading service for initializing MongoDB state before replay runs."""

import json
from pathlib import Path
from typing import Dict, Any, List
from motor.motor_asyncio import AsyncIOMotorDatabase
from pharma.db.connection import get_database, init_indexes


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


async def load_scenario_into_db(scenario_dir: Path, db: AsyncIOMotorDatabase = None):
    """Reset database collections and populate from scenario fixture files."""
    database = db if db is not None else get_database()

    # Clear existing collections
    collections = [
        "medications", "regions", "receipts", "inventory",
        "events", "movement_sessions", "transactions", "disposals", "alerts"
    ]
    for coll_name in collections:
        await database[coll_name].delete_many({})

    await init_indexes(database)

    # 1. Regions
    regions_data = load_json(scenario_dir / "regions.json")
    if regions_data:
        await database.regions.insert_many(regions_data)

    # 2. Medications (derive from regions + inventory if not separate)
    medications_data = load_json(scenario_dir / "medications.json")
    if not medications_data and regions_data:
        keys = {r["medication_key"] for r in regions_data if r.get("medication_key")}
        medications_data = [
            {"medication_key": key, "name": key.split("_")[0].title(), "strength": "500mg", "unit": "tablets"}
            for key in keys
        ]
    if medications_data:
        await database.medications.insert_many(medications_data)

    # 3. Initial Inventory
    inventory_data = load_json(scenario_dir / "initial_inventory.json")
    if inventory_data:
        await database.inventory.insert_many(inventory_data)

    # 4. Receipts
    receipts_data = load_json(scenario_dir / "receipts.json")
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
