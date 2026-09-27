import json
from pathlib import Path
import zipfile
import pytest
from pharma.services.workspace_bundle import export_bundle, restore_bundle


def bundle(tmp_path,mongo_store):
    root=tmp_path/'source';root.mkdir()
    (root/'catalog.json').write_text('{"medications":[],"receipts":[]}')
    folder=root/'scenarios/upload-demo';folder.mkdir(parents=True)
    (folder/'video.mp4').write_bytes(b'test-video')
    (folder/'poses.json').write_text('{"frames":[]}')
    (folder/'unity.log').write_text('must not ship host logs')
    (root/'.env').write_text('SECRET=excluded')
    mongo_store.pharmacy_state.insert_one({'_id':'source','revision':3,'version':1,
                                           'engine':{'inventory':{'med':{'total_bottles':2}}},'recordings':{'upload-demo':{'applied_event_ids':['evt1']}}})
    archive=tmp_path/'workspace.zip'
    manifest=export_bundle(root,archive,mongo_store.pharmacy_state,'source',
                           {'scenario':'upload-demo','media_time_ms':1234,'player_source':'side','is_playing':False})
    return root,archive,manifest


def test_bundle_preserves_media_inventory_player_and_cache_timestamps(tmp_path,mongo_store):
    root,archive,manifest=bundle(tmp_path,mongo_store)
    assert 'data/scenarios/upload-demo/unity.log' not in manifest['files']
    assert 'data/.env' not in manifest['files']
    dest=tmp_path/'restored'
    restore_bundle(archive,dest,mongo_store.pharmacy_state,'restored')
    assert (dest/'scenarios/upload-demo/video.mp4').read_bytes()==b'test-video'
    assert (dest/'scenarios/upload-demo/poses.json').stat().st_mtime_ns==(root/'scenarios/upload-demo/poses.json').stat().st_mtime_ns
    doc=mongo_store.pharmacy_state.find_one({'_id':'restored'})
    assert doc['player_state']=={'scenario':'upload-demo','media_time_ms':1234,'player_source':'side'}
    assert doc['recordings']['upload-demo']['applied_event_ids']==['evt1']
    assert doc['engine']['inventory']['med']['total_bottles']==2
    with pytest.raises(ValueError,match='already has inventory'):
        restore_bundle(archive,tmp_path/'other',mongo_store.pharmacy_state,'restored')
    with pytest.raises(ValueError,match='new DATA_DIR'):
        restore_bundle(archive,dest,mongo_store.pharmacy_state,'new')


@pytest.mark.parametrize('unsafe', [False,True])
def test_corrupt_or_unsafe_archive_changes_neither_files_nor_inventory(tmp_path,mongo_store,unsafe):
    _,archive,manifest=bundle(tmp_path,mongo_store)
    with zipfile.ZipFile(archive) as z:parts={n:z.read(n) for n in z.namelist()}
    if unsafe:
        meta=manifest['files'].pop('data/catalog.json');manifest['files']['data/../../outside.json']=meta
        parts['data/../../outside.json']=parts.pop('data/catalog.json')
    else:parts['data/catalog.json']=b'corrupted'
    parts['bundle.json']=json.dumps(manifest).encode()
    with zipfile.ZipFile(archive,'w') as z:
        for n,c in parts.items():z.writestr(n,c)
    with pytest.raises(ValueError):restore_bundle(archive,tmp_path/'restored',mongo_store.pharmacy_state,'restored')
    assert not (tmp_path/'restored').exists()
    assert mongo_store.pharmacy_state.find_one({'_id':'restored'}) is None
    assert not (tmp_path/'outside.json').exists()


def test_restore_bookmark_does_not_reapply_signals(tmp_path):
    from tests.test_api import make_controller
    ctrl=make_controller(tmp_path)
    ctrl.load_scenario('demo_scenario_01')
    bookmark={'scenario':'demo_scenario_01','media_time_ms':1500,'player_source':'real'}
    ctrl.store.player_state=bookmark;ctrl.store.save()
    before=ctrl.store.engine.to_dict()
    ctrl.restore_player()
    assert ctrl.current_media_time_ms==1500 and not ctrl.is_playing
    assert ctrl.store.engine.to_dict()==before
    assert ctrl.store.player_state==bookmark
