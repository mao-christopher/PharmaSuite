"""Install photo-annotated IMG_3537 regions via the inventory API, retaining a rollback copy."""
import json, os, shutil
from pathlib import Path
from datetime import datetime, timezone
import httpx
from pharma.db.repository import MongoStateRepository

assert os.environ.get('PHARMACY_ID') == 'img-3537-demo', 'Only run against the supplied demo pharmacy'
root=Path('/data/workspace')
setup=json.loads((root/'demo/setup.json').read_text())
view=setup['camera_layout_id']
backup=Path('/data/archive')/('before-wristband-notices-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S'))
backup.mkdir(parents=True)
regions=json.loads(Path('/tmp/img3537-camera-regions.json').read_text())
with httpx.Client(base_url='http://127.0.0.1:8000', timeout=120) as api:
    api.post('/api/replay/control',json={'action':'pause'}).raise_for_status()
    before=api.get('/api/inventory').raise_for_status().json()
    assert before['recording']['name']==setup['recording']
    (backup/'ui-state.json').write_text(json.dumps(before,indent=2))
    repo=MongoStateRepository.configured()
    (backup/'inventory.json').write_text(json.dumps(repo.load(),default=str,indent=2));repo.close()
    layout=api.get('/api/layouts/'+view).raise_for_status().json()
    (backup/'layout.json').write_text(json.dumps(layout,indent=2))
    if layout['regions_source'] is None:
        layout['regions']=regions
    else:
        setup['room_id']=layout['regions_source']['room_id']
        (root/'demo/setup.json').write_text(json.dumps(setup,indent=2))
    if layout['regions_source'] is None:
        result=api.put('/api/layouts/'+view,params={'reset_inventory':'true'},json=layout).raise_for_status().json()
    else:
        api.post('/api/inventory/reset').raise_for_status()
        result={'layout':layout}
    api.post('/api/replay/control',json={'action':'seek','media_time_ms':0}).raise_for_status()
    after=api.get('/api/inventory').raise_for_status().json()
    assert after['recording']['events_applied']==0 and len(after['layout']['regions'])>=5
    print(json.dumps({'backup':str(backup),'regions':5,'events_applied':0,'calibration_version':result['layout']['calibration_version']}))
