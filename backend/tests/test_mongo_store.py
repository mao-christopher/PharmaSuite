"""Persistence invariants; run against real MongoDB with TEST_MONGO_URI."""
import copy
import json
import pytest
from pymongo.errors import AutoReconnect
from pharma.db.repository import MongoStateRepository, StateConflict, StorageUnavailable
from pharma.services.store import PharmacyStore
from pharma.services.layout import load_catalog
from tests.conftest import FIXTURE_LAYOUTS


def open_store(tmp_path, db, name="pharmacy"):
    return PharmacyStore.open(tmp_path / "pharmacy.json", load_catalog(FIXTURE_LAYOUTS),
                              MongoStateRepository(db.pharmacy_state, name))


def test_migration_is_once_and_preserves_source(tmp_path, mongo_store):
    original = open_store(tmp_path, mongo_store, "legacy")
    original.engine.inventory["AMOXICILLIN_500MG"].pooled_tablets = 321
    original.recordings = {"clip": {"applied_event_ids": ["e1"], "activity": []}}
    original.record("correction", "Employee corrected stock")
    original.save()
    payload = copy.deepcopy(original._durable)
    payload.pop("_id"); payload.pop("revision")
    source = tmp_path / "pharmacy.json"
    source.write_text(json.dumps(payload))
    untouched = source.read_bytes()
    store = open_store(tmp_path, mongo_store)
    assert store.engine.inventory["AMOXICILLIN_500MG"].pooled_tablets == 321
    assert store.applied_event_ids("clip") == {"e1"}
    assert store.migration["sha256"]
    store.engine.inventory["AMOXICILLIN_500MG"].pooled_tablets = 300
    store.save()
    assert open_store(tmp_path, mongo_store).engine.inventory["AMOXICILLIN_500MG"].pooled_tablets == 300
    assert source.read_bytes() == untouched
    assert len([h for h in store.history if h["kind"] == "migration"]) == 1


def test_concurrent_writer_cannot_overwrite_counts(tmp_path, mongo_store):
    first = open_store(tmp_path, mongo_store)
    second = open_store(tmp_path, mongo_store)
    first.engine.inventory["AMOXICILLIN_500MG"].pooled_tablets = 470
    first.save()
    second.engine.inventory["AMOXICILLIN_500MG"].pooled_tablets = 450
    with pytest.raises(StateConflict): second.save()
    second.refresh()
    assert second.engine.inventory["AMOXICILLIN_500MG"].pooled_tablets == 470


@pytest.mark.parametrize("committed", [False, True])
def test_uncertain_write_recovers_authoritative_state(tmp_path, mongo_store, monkeypatch, committed):
    store = open_store(tmp_path, mongo_store)
    collection = store.repository.collection
    replace = collection.replace_one
    def failure(*args, **kwargs):
        if committed: replace(*args, **kwargs)
        raise AutoReconnect("simulated lost acknowledgement")
    monkeypatch.setattr(collection, "replace_one", failure)
    store.engine.inventory["AMOXICILLIN_500MG"].pooled_tablets = 470
    store.recordings = {"clip": {"applied_event_ids": ["e1"], "activity": []}}
    with pytest.raises(StorageUnavailable): store.save()
    assert store.engine.inventory["AMOXICILLIN_500MG"].pooled_tablets == 500
    monkeypatch.setattr(collection, "replace_one", replace)
    store.refresh()
    assert store.engine.inventory["AMOXICILLIN_500MG"].pooled_tablets == (470 if committed else 500)
    assert store.applied_event_ids("clip") == ({"e1"} if committed else set())
    assert not (tmp_path / "pharmacy.json").exists()


def test_missing_database_is_not_reseeded(tmp_path, mongo_store):
    store = open_store(tmp_path, mongo_store)
    mongo_store.pharmacy_state.delete_many({})
    with pytest.raises(StorageUnavailable, match="missing"): store.refresh()


def test_invalid_legacy_file_does_not_create_inventory(tmp_path, mongo_store):
    (tmp_path / "pharmacy.json").write_text('{"version": 99}')
    with pytest.raises(ValueError, match="schema"): open_store(tmp_path, mongo_store)
    assert mongo_store.pharmacy_state.count_documents({}) == 0


def test_api_restart_retains_prescription_deduction(tmp_path, mongo_store):
    from tests.test_api import make_controller
    first = make_controller(tmp_path)
    first.load_scenario("demo_scenario_01")
    assert first.engine.process_prescription_deduction("TX_RX_1001", "confirmed_fill")
    first.store.save()
    restarted = make_controller(tmp_path)
    restarted.restore_player()
    assert restarted.engine.inventory["AMOXICILLIN_500MG"].pooled_tablets == 470
    assert not restarted.engine.process_prescription_deduction("TX_RX_1001", "paid")
    assert restarted.current_scenario_name == "demo_scenario_01"
