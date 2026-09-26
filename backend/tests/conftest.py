"""Point the app at a throwaway copy of the data directory so tests never touch live state.

Layouts come from tests/fixtures, not data/layouts, because the Setup page edits the
live layout and tests must not depend on whatever was last annotated.
"""

import os
import shutil
import tempfile
from pathlib import Path

_DATA = Path(__file__).resolve().parents[1] / "data"
FIXTURE_LAYOUTS = Path(__file__).resolve().parent / "fixtures" / "layouts"
_TMP = Path(tempfile.mkdtemp(prefix="pharma-test-data-"))
_SKIP = shutil.ignore_patterns("upload-*", "video.*", "poses.json", "thumb.jpg")
shutil.copytree(_DATA / "scenarios", _TMP / "scenarios", ignore=_SKIP)
shutil.copytree(FIXTURE_LAYOUTS, _TMP / "layouts")
shutil.copy(FIXTURE_LAYOUTS.parent / "catalog.json", _TMP / "catalog.json")
os.environ["DATA_DIR"] = str(_TMP)  # read by pharma.config.Settings at import time


import hashlib
import uuid
import pytest


@pytest.fixture(autouse=True)
def mongo_store(monkeypatch):
    """Never use the developer's live inventory. Opt into real Mongo with TEST_MONGO_URI."""
    from pharma.db.repository import MongoStateRepository
    from pharma.services.store import PharmacyStore
    uri = os.getenv("TEST_MONGO_URI")
    if uri:
        from pymongo import MongoClient
        client = MongoClient(uri, serverSelectionTimeoutMS=3000)
        client.admin.command("ping")
    else:
        import mongomock
        client = mongomock.MongoClient()
    db_name = "pharma_test_" + uuid.uuid4().hex
    database = client[db_name]
    original = PharmacyStore.open.__func__

    def isolated(cls, path, catalog, repository=None):
        if repository is None:
            identity = hashlib.sha256(str(path).encode()).hexdigest()
            repository = MongoStateRepository(database.pharmacy_state, identity)
        return original(cls, path, catalog, repository=repository)

    monkeypatch.setattr(PharmacyStore, "open", classmethod(isolated))
    yield database
    client.drop_database(db_name)
    client.close()
