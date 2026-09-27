"""Rename only the supplied demo fixtures, preserve inventory quantities and audit history."""
import json,shutil
from pathlib import Path
from datetime import datetime,timezone
from pharma.db.models import Catalog,Room,Layout
from pharma.db.repository import MongoStateRepository
root=Path('/data/workspace');setup=json.loads((root/'demo/setup.json').read_text());rid=setup['recording'];roomid=setup['room_id'];viewid=setup['camera_layout_id']
archive=Path('/data/archive')/('before-ui-restore-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S'));archive.mkdir(parents=True)
names=[('Metformin','500 mg'),('Atorvastatin','20 mg'),('Ibuprofen','200 mg'),('Amoxicillin','500 mg')]
mapping={};details={}
for i,(name,strength) in enumerate(names):
 old=f'DEMO_{chr(65+i)}_{(i+1)*10}MG';new=name.upper()+'_'+strength.replace(' ','').upper();mapping[old]=new;mapping['shelf_'+old.lower()]='shelf_'+new.lower();mapping[f'Demo {chr(65+i)} {(i+1)*10} mg']=name+' '+strength;details[new]=(name,strength)
def rewrite(v):
 if isinstance(v,str):return mapping.get(v,v)
 if isinstance(v,list):return [rewrite(x) for x in v]
 if isinstance(v,dict):
  d={mapping.get(k,k):rewrite(x) for k,x in v.items()}
  if d.get('medication_key') in details:
   n,s=details[d['medication_key']]
   if 'name' in d:d['name']=n
   if 'strength' in d:d['strength']=s
  return d
 return v
for f,model in [('catalog.json',Catalog),(f'rooms/{roomid}/room.json',Room),(f'layouts/{viewid}/layout.json',Layout),('demo/setup.json',None)]:
 path=root/f;target=archive/f;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target);data=rewrite(json.loads(path.read_text()))
 if model:data=model.model_validate(data).model_dump(mode='json')
 path.write_text(json.dumps(data,indent=2))
setup=json.loads((root/'demo/setup.json').read_text());setup.pop('single_demo',None);(root/'demo/setup.json').write_text(json.dumps(setup,indent=2))
path=root/f'scenarios/{rid}/scenario.json';shutil.copy2(path,archive/'scenario.json');meta=json.loads(path.read_text());meta['privacy_windows']=True;meta['presentation_only']=True
meta['presentation_regions']=[dict(region_id=('shelf_'+name+'_'+strength.replace(' ','')).lower(),region_type='designated_shelf',medication_key=name.upper()+'_'+strength.replace(' ','').upper(),polygon=r['polygon']) for (name,strength),r in zip(names,setup['presentation_regions'])]
path.write_text(json.dumps(meta,indent=2));shutil.copy2(root/'demo/simulation.mp4',path.parent/'presentation.mp4')
repo=MongoStateRepository.configured();data=repo.load()
if data is not None:
 (archive/'inventory.json').write_text(json.dumps(data,default=str,indent=2));repo.save(rewrite(data),data['revision'])
repo.close()
print('Restored UI fixture migration complete; prior files and inventory archived at',archive)

