import os
from typing import Optional, Any

try:
    from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
except ImportError:
    AsyncIOMotorClient = Any  # type: ignore
    AsyncIOMotorDatabase = Any  # type: ignore


_client: Optional[Any] = None
_db: Optional[Any] = None

DEFAULT_MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DEFAULT_DB_NAME = os.getenv("MONGO_DB_NAME", "pharma")


def get_client(uri: str = DEFAULT_MONGO_URI) -> AsyncIOMotorClient:
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(uri)
    return _client


def get_database(uri: str = DEFAULT_MONGO_URI, db_name: str = DEFAULT_DB_NAME) -> AsyncIOMotorDatabase:
    global _db
    if _db is None:
        client = get_client(uri)
        _db = client[db_name]
    return _db


def get_collection(name: str, uri: str = DEFAULT_MONGO_URI, db_name: str = DEFAULT_DB_NAME):
    db = get_database(uri, db_name)
    return db[name]


async def init_indexes(db: AsyncIOMotorDatabase = None):
    """Create unique indexes across collections to enforce idempotency and fast lookups."""
    database = db if db is not None else get_database()

    await database.medications.create_index("medication_key", unique=True)
    await database.regions.create_index("region_id", unique=True)
    await database.receipts.create_index("receipt_id", unique=True)
    await database.inventory.create_index("medication_key", unique=True)
    await database.events.create_index("event_id", unique=True)
    await database.movement_sessions.create_index("session_id", unique=True)
    await database.transactions.create_index("transaction_id", unique=True)
    await database.disposals.create_index("disposal_id", unique=True)
    await database.alerts.create_index("alert_id", unique=True)


async def close_connection():
    global _client, _db
    if _client:
        _client.close()
        _client = None
        _db = None
