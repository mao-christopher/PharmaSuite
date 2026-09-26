"""Atomic, revision-checked persistence for the demo's complete inventory aggregate.

Counts, deduction flags, applied event IDs and audit history share one MongoDB
write, so a crash cannot save a decrement without its deduplication marker.
"""
import copy
import os
from bson import BSON
from pymongo.errors import PyMongoError, DuplicateKeyError
from pharma.db.connection import create_client, get_collection, get_database


class StorageUnavailable(RuntimeError):
    pass


class StateConflict(RuntimeError):
    pass


class StateTooLarge(RuntimeError):
    pass


class MongoStateRepository:
    def __init__(self, collection, pharmacy_id="default", client=None):
        self.collection = collection
        self.pharmacy_id = pharmacy_id
        self.client = client

    @classmethod
    def configured(cls):
        client = create_client()
        return cls(get_collection("pharmacy_state", get_database(client)),
                   os.getenv("PHARMACY_ID", "default"), client)

    def load(self):
        try:
            return self.collection.find_one({"_id": self.pharmacy_id})
        except PyMongoError as exc:
            raise StorageUnavailable("MongoDB is unavailable; inventory changes are paused.") from exc

    def save(self, payload, expected_revision):
        revision = (expected_revision or 0) + 1
        document = {**copy.deepcopy(payload), "_id": self.pharmacy_id, "revision": revision}
        # Fail before MongoDB's 16 MiB limit; never trim audit/deduplication history.
        if len(BSON.encode(document)) > 14 * 1024 * 1024:
            raise StateTooLarge("Inventory history reached the demo storage limit; archive/migrate it before continuing.")
        try:
            if expected_revision is None:
                self.collection.insert_one(document)
            else:
                result = self.collection.replace_one(
                    {"_id": self.pharmacy_id, "revision": expected_revision}, document)
                if result.matched_count != 1:
                    raise StateConflict("Inventory changed in another process. Refresh and retry the action.")
        except DuplicateKeyError as exc:
            raise StateConflict("Another process initialized this pharmacy. Refresh and retry.") from exc
        except PyMongoError as exc:
            raise StorageUnavailable("MongoDB could not confirm this write. Refresh before retrying.") from exc
        return document

    def close(self):
        if self.client is not None:
            self.client.close()
