"""Authored presentation plan from reviewed IMG_3537 actions, never inventory evidence."""
import json, math
from pathlib import Path
import numpy as np

out=Path(__file__).resolve().parents[1] / 'Assets/Pharma/Demo3537';out.mkdir(exist_ok=True)
fps=15; duration=44.48
p=dict(schema='reenactment-plan/1',recording='IMG_3537 illustrative reconstruction',fps=fps,width=960,height=540,frame_count=round(duration*fps),height_m=2.5,boxes=[],regions=[],bottles=[],actions=[],alignment_points=[])
def box(name,c,s,color='#dddddd',collider='solid',label=None):
 p['boxes'].append(dict(name=name,center=c,size=s,color=color,collider=collider,yaw_deg=0,label=label))
box('Room floor',[0,-.045,0],[1.85,.09,8.08],'#6b7278','floor')
box('Back wall',[.92,1.25,0],[.06,2.5,8.08],'#d3d9d9')
box('End wall',[0,1.25,4],[1.85,2.5,.06],'#d3d9d9')
colors=['#60c5ed','#d7a1ff','#63dfb4','#f4bd64'];names=['Metformin 500 mg','Atorvastatin 20 mg','Ibuprofen 200 mg','Amoxicillin 500 mg'];medkeys=['METFORMIN_500MG','ATORVASTATIN_20MG','IBUPROFEN_200MG','AMOXICILLIN_500MG']
for row,y in enumerate([1.45,.8]):
 box('Shelf row '+str(row),[.56,y-.035,.6],[.62,.07,2.15],'#343d43')
 for col,z in enumerate([0,1.15]):
  i=row*2+col;key=medkeys[i];rid='shelf_'+key.lower()
  p['regions'].append(dict(region_id=rid,region_type='designated_shelf',medication_key=key,center=[.54,y+.15,z],size=[.58,.3,1.0],yaw_deg=0))
  box(names[i]+' label',[.23,y-.06,z],[.85,.1,.025],colors[i],'none',names[i]);p['boxes'][-1]['yaw_deg']=90
  p['bottles'].append(dict(index=i,medication_key=key,label=names[i],look='normal',position=[.31,y+.1,z],hidden=False))
for z in [-.49,1.69]:box('Shelf upright',[.83,.8,z],[.08,1.6,.06],'#a5adb1')
box('Work counter',[-.38,.72,-1.6],[.95,.08,.65],'#aa8960')
for x in [-.78,.02]:box('Counter leg',[x,.35,-1.6],[.06,.7,.5],'#a5adb1')
p['regions'].append(dict(region_id='counter',region_type='dispensing_counter',center=[-.38,.93,-1.6],size=[.95,.34,.65],yaw_deg=0))
# Start on lower left shelf; match the reviewed shelf/counter/wrong-shelf/correction sequence.
contacts=[[.25,.965,0],[-.5,.925,-1.34],[-.5,.925,-1.34],[.25,.965,1.15],[.25,.965,1.15],[.25,.965,0]]
stands=[[-.30,.32,90],[-.18,-.85,180],[-.18,-.85,180],[-.30,1.47,90],[-.30,1.47,90],[-.30,.32,90]]
times=[14.6,16.75,28.7,31.65,37.3,40.35]
regions=[p['regions'][2]['region_id'],'counter','counter',p['regions'][3]['region_id'],p['regions'][3]['region_id'],p['regions'][2]['region_id']]
for i,(t,c,st,r) in enumerate(zip(times,contacts,stands,regions)):
 p['actions'].append(dict(action_id=f'review-{i}',type='pickup' if i%2==0 else 'release',status='illustrative',outcome='in_hand' if i%2==0 else 'counter' if i==1 else 'misplaced' if i==3 else 'returned',bottle_id='demo-c',medication_key='IBUPROFEN_200MG',region_id=r,mode='reach',look='misplaced' if i==3 else 'normal',contact_frame=round(t*fps),reach_start_frame=round((t-.6)*fps),retract_end_frame=round((t+.35)*fps),bottle=2,contact=c,to=[c[0],c[1]-.065,c[2]],stand=[st[0],0,st[1]],drop=False,pending=False))
# Smooth manually reviewed floor route. Pauses mirror shelf approach / counter work / return.
keys=[(0,[-.3,-.9,0]),(5,[-.3,-.65,0]),(11,stands[0]),(14.95,stands[0]),(16.15,stands[1]),(29.05,stands[2]),(31.05,stands[3]),(37.65,stands[4]),(39.75,stands[5]),(41,stands[5]),(44.48,[-.3,-.9,180])]
p['privacy_lead_s']=1.0
p['frames_flat']=[]
for f in range(p['frame_count']):
 t=f/fps
 for (a,A),(b,B) in zip(keys,keys[1:]):
  if a<=t<=b:
   u=(t-a)/(b-a);u=u*u*(3-2*u);v=[A[j]+u*(B[j]-A[j]) for j in range(3)];break
 p['frames_flat']+=v+[3]
pos=np.array([-2.8,2.9,-3.5]);forward=np.array([0,1,.4])-pos;forward/=np.linalg.norm(forward);right=np.cross([0,1,0],forward);right/=np.linalg.norm(right);up=np.cross(forward,right)
m=np.column_stack([right,up,forward]); qw=math.sqrt(1+np.trace(m))/2; quat=[(m[2,1]-m[1,2])/(4*qw),(m[0,2]-m[2,0])/(4*qw),(m[1,0]-m[0,1])/(4*qw),qw]
p['camera']=dict(position=pos.tolist(),rotation_xyzw=quat,lens_shift=[0,0],vertical_fov_deg=52,width=960,height=540,layout_id='img-3537-camera')
(out/'plan.json').write_text(json.dumps(p,separators=(',',':')))
print('Wrote',p['frame_count'],'frames',len(p['regions']),'regions')



