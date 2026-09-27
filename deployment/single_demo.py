"""Switch this deployment to one demo, archiving replaced fixtures outside the served workspace."""
import json, shutil
from pathlib import Path
from datetime import datetime, timezone
from pharma.db.models import Catalog, Layout, Room
root=Path('/data/workspace'); setup=json.loads((root/'demo/setup.json').read_text());rid=setup['recording'];roomid=setup['room_id'];viewid='img-3537-camera'
archive=Path('/data/archive')/datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S');archive.mkdir(parents=True)
for folder,keep in [('scenarios',rid),('rooms',roomid),('layouts',viewid)]:
 (archive/folder).mkdir()
 for path in (root/folder).iterdir():
  if path.is_dir() and not path.name.startswith('.') and path.name!=keep:shutil.move(str(path),str(archive/folder/path.name))
for f in ['catalog.json','demo/setup.json',f'rooms/{roomid}/room.json',f'layouts/{viewid}/layout.json',f'scenarios/{rid}/scenario.json']:
 dest=archive/f;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(root/f,dest)
colors=['#60c5ed','#d7a1ff','#63dfb4','#f4bd64']; meds=[];regions=[];now=datetime.now(timezone.utc).isoformat()
polygons=[[[.49,.25],[.66,.32],[.66,.47],[.49,.38]],[[.67,.32],[.84,.39],[.84,.59],[.67,.47]],[[.49,.46],[.67,.56],[.67,.76],[.49,.64]],[[.68,.56],[.85,.68],[.85,.87],[.68,.76]]]
plan=json.loads(Path('/app/simulation/Assets/Pharma/Demo3537/plan.json').read_text())
for i in range(4):
 name,strength=[('Metformin','500 mg'),('Atorvastatin','20 mg'),('Ibuprofen','200 mg'),('Amoxicillin','500 mg')][i];key=name.upper()+'_'+strength.replace(' ','').upper()
 meds.append(dict(medication_key=key,name=name,strength=strength))
 regions.append(dict(label=f'{name} {strength}',color=colors[i],polygon=polygons[i]))
catalog=Catalog(medications=meds,receipts=[dict(receipt_id=f'demo-{i}',medication_key=m['medication_key'],bottle_count=1,tablets_per_bottle=30,expiry_date='2028-12-31',lot_number='SYNTHETIC',received_at=now) for i,m in enumerate(meds)])
(root/'catalog.json').write_text(catalog.model_dump_json(indent=2))
room=json.loads((root/f'rooms/{roomid}/room.json').read_text());room['regions']=[]
for r in plan['regions']:
 room['regions'].append(dict(region_id=r['region_id'],region_type=r['region_type'],medication_key=r.get('medication_key'),box=dict(center=[r['center'][0],r['center'][1],-r['center'][2]],size=r['size'],yaw_deg=0)))
room['room_version']+=1;room['updated_at']=now
(root/f'rooms/{roomid}/room.json').write_text(Room.model_validate(room).model_dump_json(indent=2))
view=json.loads((root/f'layouts/{viewid}/layout.json').read_text());view.update(name='Pharmacy security camera',regions=[],regions_source=None,medications=[],receipts=[])
# Camera rectangles are presentation-only, never fabricated calibrated inventory evidence.
(root/f'layouts/{viewid}/layout.json').write_text(Layout.model_validate(view).model_dump_json(indent=2))
meta_path=root/f'scenarios/{rid}/scenario.json';meta=json.loads(meta_path.read_text());meta.update(privacy_windows=True,label='Pharmacy camera');meta_path.write_text(json.dumps(meta,indent=2))
setup.update(camera_layout_id=viewid,presentation_regions=regions,reconstruction_source='Authored from video review; approximate room boxes, no camera calibration')
(root/'demo/setup.json').write_text(json.dumps(setup,indent=2))
print('Archived previous demos at',archive,'; one recording, one camera, four medication shelves and a counter remain')
