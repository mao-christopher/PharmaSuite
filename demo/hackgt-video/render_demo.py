from pathlib import Path
import os
import cv2, numpy as np, subprocess, json, math, textwrap
from PIL import Image, ImageDraw, ImageFont

ROOT=Path(os.environ['PHARMA_VIDEO_WORKSPACE'])
OUT=ROOT/'outputs/hackgt-demo-v4'
V3=ROOT/'outputs/hackgt-demo-v3'
OLD=ROOT/'outputs/hackgt-demo-v2'
SRC=ROOT/'work/video-source/video'
W,H,FPS=1920,1080,30
DURATION=163.0
FONT='/System/Library/Fonts/SFNS.ttf'
FONT_B='/System/Library/Fonts/SFNS.ttf'
TEAL=(34,211,180)
NAVY=(12,18,28)
WHITE=(245,248,250)
MUTED=(177,190,204)
YELLOW=(52,211,250)
RED=(77,79,244)

P={
 **{k:V3/(k+'.webm') for k in ['alerts','batches','library']},
 'ship_review':OLD/'shipment-reviewed.webm',
 'lab':Path(os.environ['PHARMA_LABCOAT_VIDEO']),
 **{k:OLD/(k+'.webm') for k in ['overview','inventory','shipments','recordings','room']},
 'scan_real': ROOT/'work/local-pharma/data/scenarios/upload-img-3515-20260926-215800/video.mov',
 'phone_wide': SRC/'IMG_9010.MOV',
 'phone_close': SRC/'IMG_9011.MOV',
 'dataset': SRC/'2026-09-27 02-54-59.mp4',
 'dsp': SRC/'2026-09-27 03-18-09.mp4',
 'sim_clean': ROOT/'outputs/pharmacy-three-camera-demo/automatic-pov.mp4',
 'sim_arm': ROOT/'outputs/pharmacy-three-camera-demo/arm-overlay.mp4',
 'scan_sim': ROOT/'outputs/scan-to-simulation/simulation-reenactment.mp4',
 'ship': ROOT/'outputs/shipments-dashboard.png',
 'dashboard': ROOT/'outputs/scan-to-simulation/dashboard-side-by-side.png',
 'multidash': ROOT/'outputs/pharmacy-multicamera-dashboard.png',
 'regions': ROOT/'outputs/pharmacy-three-camera-demo/all-regions.png',
}

SCENES=[
 (0,8,'video','sim_clean',26,1,'title'),
 (8,17.6,'video','scan_real',0,1,'problem'),
 (17.6,26,'video','room',1,0.8,'digital_twin'),
 (26,35.755,'video','library',1,0.65,'intro_dash'),
 (35.755,41.5,'video','ship_review',3,1,'shipment'),
 (41.5,47,'video','inventory',1,1,'stock'),
 (47,62.5,'video','lab',0,1,'lab_actions'),
 (62.5,70,'video','alerts',1,0.8,'alerts_new'),
 (70,94.5,'video','lab',15.5,1,'lab_actions'),
 (94.5,107.435,'bridge','lab',40,1,'bridge'),
 (107.435,115.3,'portrait','phone_close',0,1,'privacy'),
 (115.3,125.3,'portrait','phone_wide',0,1,'imu'),
 (125.3,131.3,'video','dataset',0,1,'dataset'),
 (131.3,136.635,'video','dsp',0,1,'accuracy'),
 (136.635,150.7,'video','sim_arm',30,1,'transaction_pose'),
 (150.7,155.5,'video','batches',1,1,'batch_new'),
 (155.5,158,'video','lab',42,1,'lab_actions'),
 (158,163,'video','sim_clean',152,1,'end'),
]

# Exact speech segments derived from the supplied narration, in final order.
raw=json.loads((ROOT/'work/video-review/transcript.json').read_text())
by={x['file']:x for x in raw}
order=['New Recording 203.m4a','New Recording 201.m4a','New Recording 202.m4a','New Recording 205.m4a']
offsets={order[0]:0, order[1]:35.7546666667, order[2]:91.7333333334, order[3]:107.4346666667}
repl={
 'Pharmysweet':'PharmaSuite','Farmasweet':'PharmaSuite','farmasweet':'PharmaSuite','pharmacy suite':'PharmaSuite',
 'covered into':'converted into','reduce air':'reduce error','So the next to our project':'For the next part of our project',
 "After the transaction, it obviously registers":"After the transaction, it automatically registers",
 'pharmacist can make':'PharmaSuite can make','pharmacist can':'PharmaSuite can'
}
CAP=[]
for name in order:
    for s in by[name]['segments']:
        txt=s['text']
        for a,b in repl.items(): txt=txt.replace(a,b)
        CAP.append((offsets[name]+s['start'],offsets[name]+s['end'],txt))

# SRT sidecar
with (OUT/'captions.srt').open('w') as f:
    def ts(x):
        ms=round(x*1000); h=ms//3600000; ms%=3600000; m=ms//60000; ms%=60000; sec=ms//1000; ms%=1000
        return f'{h:02d}:{m:02d}:{sec:02d},{ms:03d}'
    for i,(a,b,txt) in enumerate(CAP,1): f.write(f'{i}\n{ts(a)} --> {ts(b)}\n{txt}\n\n')

font_cache={}
def font(size,bold=False):
    k=(size,bold)
    if k not in font_cache: font_cache[k]=ImageFont.truetype(FONT_B if bold else FONT,size)
    return font_cache[k]

def rgba_text_box(lines, width=900, title_size=48, body_size=31, accent=TEAL, danger=False, align='left'):
    lines=lines[:2]
    lines=[(txt.replace(' • ', ', '),sz,col,b) for txt,sz,col,b in lines]
    # Two clean text lines per callout
    heights=[]
    for txt,sz,col,b in lines:
        bb=font(sz,b).getbbox(txt); heights.append(bb[3]-bb[1])
    hh=48+sum(heights)+18*(len(lines)-1)+48
    im=Image.new('RGBA',(width,hh),(0,0,0,0)); d=ImageDraw.Draw(im)
    d.rounded_rectangle((0,0,width-1,hh-1),radius=24,fill=(9,15,24,228),outline=(accent[2],accent[1],accent[0],220),width=3)
    d.rounded_rectangle((0,0,12,hh-1),radius=6,fill=(accent[2],accent[1],accent[0],255))
    y=40
    for (txt,sz,col,b),ht in zip(lines,heights):
        fill=(col[2],col[1],col[0],255)
        x=42
        if align=='center':
            bb=d.textbbox((0,0),txt,font=font(sz,b)); x=(width-(bb[2]-bb[0]))//2
        d.text((x,y),txt,font=font(sz,b),fill=fill)
        y+=ht+18
    return np.array(im)[:,:, [2,1,0,3]]

def overlay_rgba(base,ov,x,y,opacity=1.0):
    h,w=ov.shape[:2]
    if y>650 and x!=160: y=min(y,880-h)
    x0,y0=max(0,x),max(0,y); x1,y1=min(base.shape[1],x+w),min(base.shape[0],y+h)
    if x1<=x0 or y1<=y0:return
    q=ov[y0-y:y1-y,x0-x:x1-x]; a=(q[:,:,3:4].astype(np.float32)/255.0)*opacity
    base[y0:y1,x0:x1]=(q[:,:,:3]*a+base[y0:y1,x0:x1]*(1-a)).astype(np.uint8)

def cover(img,w,h):
    ih,iw=img.shape[:2]; s=max(w/iw,h/ih); r=cv2.resize(img,(round(iw*s),round(ih*s)),interpolation=cv2.INTER_AREA)
    y=(r.shape[0]-h)//2; x=(r.shape[1]-w)//2; return r[y:y+h,x:x+w]

def contain(img,w,h,bg=(10,16,24)):
    out=np.full((h,w,3),bg,np.uint8); ih,iw=img.shape[:2]; s=min(w/iw,h/ih); r=cv2.resize(img,(round(iw*s),round(ih*s)),interpolation=cv2.INTER_AREA)
    y=(h-r.shape[0])//2; x=(w-r.shape[1])//2; out[y:y+r.shape[0],x:x+r.shape[1]]=r; return out

def portrait(img):
    bg=cover(img,W,H); bg=cv2.resize(cv2.GaussianBlur(cv2.resize(bg,(480,270)),(0,0),7),(W,H)); bg=(bg*0.42).astype(np.uint8)
    ih,iw=img.shape[:2]; s=min(860/iw,1010/ih); r=cv2.resize(img,(round(iw*s),round(ih*s)),interpolation=cv2.INTER_AREA)
    x=110; y=(H-r.shape[0])//2
    bg[y:y+r.shape[0],x:x+r.shape[1]]=r
    cv2.rectangle(bg,(x-3,y-3),(x+r.shape[1]+3,y+r.shape[0]+3),(55,220,192),3)
    return bg

def darken(img,v=.18): return (img.astype(np.float32)*(1-v)).astype(np.uint8)

class Vid:
    def __init__(self,path,start=0,speed=1):
        self.path=str(path); self.cap=cv2.VideoCapture(self.path); self.fps=self.cap.get(cv2.CAP_PROP_FPS) or 30
        self.frames=int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT)); self.duration=self.frames/self.fps
        self.start=start; self.speed=speed; self.last=-1; self.frame=None
    def at(self,t):
        tt=self.start+t*self.speed
        if self.duration>0: tt=min(tt,self.duration-1/self.fps)
        target=min(self.frames-1,max(0,int(tt*self.fps)))
        if target<self.last or target-self.last>12:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES,target); self.last=target-1
        while self.last<target:
            ok,fr=self.cap.read()
            if not ok:
                self.cap.set(cv2.CAP_PROP_POS_FRAMES,0); ok,fr=self.cap.read(); self.last=0
            else:self.last+=1
            self.frame=fr
        return self.frame.copy() if self.frame is not None else np.zeros((H,W,3),np.uint8)
    def close(self): self.cap.release()

images={k:cv2.imread(str(v)) for k,v in P.items() if str(v).lower().endswith(('.png','.jpg','.jpeg'))}

# cached graphics
G={}
def panel(key,lines,width=780,accent=TEAL):
    if key not in G:G[key]=rgba_text_box(lines,width=width,accent=accent)
    return G[key]

def pill(draw,xy,text,fill,fg=(255,255,255),fs=26):
    x,y=xy; ft=font(fs,True); bb=draw.textbbox((0,0),text,font=ft); ww=bb[2]-bb[0]+40; hh=bb[3]-bb[1]+24
    draw.rounded_rectangle((x,y,x+ww,y+hh),radius=hh//2,fill=fill); draw.text((x+20,y+10),text,font=ft,fill=fg); return ww

def title_layer(frame,local):
    p=Image.fromarray(cv2.cvtColor(darken(frame,.42),cv2.COLOR_BGR2RGB)); d=ImageDraw.Draw(p,'RGBA')
    d.rounded_rectangle((90,90,330,146),radius=28,fill=(28,211,179,230)); d.text((120,103),'HACKGT 2026',font=font(27,True),fill=(4,17,24,255))
    d.text((90,220),'PharmaSuite',font=font(104,True),fill=(248,250,252,255))
    d.text((96,345),'AI support for safer medication handling.',font=font(43),fill=(198,211,223,255))
    d.line((96,430,660,430),fill=(31,211,180,255),width=8)
    return cv2.cvtColor(np.array(p),cv2.COLOR_RGB2BGR)

def special_overlay(frame,key,t,local):
    if key in ('clean_handoff','alerts_new','batch_new'):
        lab={'clean_handoff':('Follow the transaction','Switch cameras as the shelf blocks the view'),'alerts_new':('Employee alerts','Review expired stock and resolve exceptions'),'batch_new':('Batch-level inventory','Check lot numbers and expiration dates')}
        head,body=lab[key];ov=panel(key,[(head,38,WHITE,True),(body,28,MUTED,False)],850,TEAL);overlay_rgba(frame,ov,50,110);return frame
    if key=='transaction_pose':
        ov=panel('txnpose',[('TRANSACTION ACTIVE',38,WHITE,True),('YOLO arm → configured shelf / counter region',26,MUTED,False),('Pose overlay shown only for this handling session',22,MUTED,False)],910,TEAL)
        overlay_rgba(frame,ov,55,700);return frame
    if key=='lab_actions':
        # Editorial illustrations: no synchronized sensor log was supplied with this clip.
        steps=[
         (0,'PRESCRIPTION WORKFLOW','One technician • one bottle at a time'),
         (6.5,'Prescription entry','Set the expected medication and quantity'),
         (13,'PICKUP','Shelf bottles −1 • total tablets unchanged'),
         (17,'COUNTER PLACEMENT','Bottle remains assigned to its home region'),
         (22,'Confirm prescription','Confirmation deducts the prescribed quantity'),
         (28.5,'RETURN IN PROGRESS','IMU release + CV location check'),
         (31.5,'Shelf return','A correct return restores the shelf count'),
         (35,'NEXT TRANSACTION','Keep a separate transaction ID'),
         (38,'PICKUP','Retain action-linked replay evidence'),
         (41.5,'PLACEMENT CHECK','Compare release region with designated shelf')]
        _,head,body=max((x for x in steps if x[0]<=local),key=lambda x:x[0])
        ov=panel('lab'+head,[(head,36,WHITE,True),(body,24,MUTED,False),('Workflow',20,MUTED,False)],850,TEAL)
        overlay_rgba(frame,ov,45,110)
        return frame
    if key in ('stock','intro_dash','separate'):
        labels={'stock':('INVENTORY + EXPIRY','Bottles, pooled tablets and received batches'),'intro_dash':('THE CENTRAL DASHBOARD','Inventory • alerts • prescriptions • replay'),'separate':('SEPARATE SHELF REGIONS','Each medication + strength has a designated location')}
        head,body=labels[key];ov=panel(key,[(head,38,WHITE,True),(body,27,MUTED,False)],900,TEAL);overlay_rgba(frame,ov,65,700);return frame
    if key=='title': return title_layer(frame,local)
    if key=='problem':
        ov=panel('problem',[("2–4 mistakes",62,WHITE,True),("in a typical pharmacy each day",32,MUTED,False)],760,RED); overlay_rgba(frame,ov,105,110)
    elif key=='edge_intro':
        ov=panel('edge_intro',[("Wearable edge AI",52,WHITE,True),("detects handling — without filming all day",29,MUTED,False)],790,TEAL); overlay_rgba(frame,ov,1010,125)
    elif key=='trust':
        ov=panel('trust',[("REAL ACTION  →  DIGITAL TWIN",38,WHITE,True),("One workflow. Verifiable evidence.",29,MUTED,False)],820,TEAL); overlay_rgba(frame,ov,550,105)
    elif key=='shipment':
        ov=panel('shipment',[("Shipment review",48,WHITE,True),("Quantity • expiry • lot • destination",29,MUTED,False)],760,TEAL); overlay_rgba(frame,ov,1060,755)
    elif key=='locations':
        ov=panel('locations',[("Configured medication regions",47,WHITE,True),("Similar names are separated before stocking",28,MUTED,False)],780,TEAL); overlay_rgba(frame,ov,90,815)
    elif key=='database':
        ov=panel('database',[("PICKUP DETECTED",47,WHITE,True),("MongoDB inventory event queued",29,MUTED,False)],760,TEAL); overlay_rgba(frame,ov,1050,130)
    elif key=='blocked':
        ov=panel('blocked',[("WRONG REGION",51,WHITE,True),("Return blocked • employee alerted",29,MUTED,False)],720,RED); overlay_rgba(frame,ov,1080,760)
    elif key=='replay':
        ov=panel('replay',[("10 s replay retained",45,WHITE,True),("Action-linked evidence, not continuous recording",28,MUTED,False)],800,TEAL); overlay_rgba(frame,ov,1000,780)
    elif key=='digital_twin':
        ov=panel('digital',[("SCAN  →  SIMULATION",44,WHITE,True),("The room becomes a reusable test environment",28,MUTED,False)],810,TEAL); overlay_rgba(frame,ov,555,105)
    elif key=='fullstack':
        ov=panel('full',[("ONE CONTINUOUS SYSTEM",46,WHITE,True),("Shipment → shelf → transaction → reorder",29,MUTED,False)],820,TEAL); overlay_rgba(frame,ov,90,770)
    elif key=='meta':
        ov=panel('meta',[("META API ECOSYSTEM",42,WHITE,True),("Shipment data → structured stock",29,MUTED,False)],720,TEAL); overlay_rgba(frame,ov,1100,770)
    elif key=='layout':
        ov=panel('layout',[("AI-ASSISTED PLACEMENT",42,WHITE,True),("Quantity • expiry • name similarity",30,MUTED,False)],760,TEAL); overlay_rgba(frame,ov,90,815)
    elif key=='privacy':
        if local<3.4:
            ov=panel('idle',[("IDLE",49,WHITE,True),("Camera buffer only • nothing saved",29,MUTED,False)],700,MUTED)
        else:
            ov=panel('pickup',[("PICKUP DETECTED",48,WHITE,True),("IMU trigger → retain last 10 seconds",29,MUTED,False)],760,TEAL)
        overlay_rgba(frame,ov,1060,120)
    elif key=='imu':
        label='PICK UP' if local<4.4 else 'PUT DOWN'
        ov=panel('imu'+label,[("WRIST IMU",38,MUTED,True),(label,62,WHITE,True),("Edge inference on ESP32",28,MUTED,False)],660,TEAL); overlay_rgba(frame,ov,1110,135)
    elif key=='dataset':
        ov=panel('data',[("5,000 HAND-COLLECTED SAMPLES",39,WHITE,True),("idle • random • pick up • put down",29,MUTED,False)],800,TEAL); overlay_rgba(frame,ov,1050,760)
    elif key=='accuracy':
        ov=panel('acc',[("98.21%",72,WHITE,True),("reported four-class gesture accuracy",31,MUTED,False)],650,TEAL); overlay_rgba(frame,ov,1110,145)
    elif key=='handoff':
        # Show current simulated camera based on source time and known handoffs.
        src_t=3+local*1.65
        cam='CAMERA 01'
        if src_t>=6.1: cam='CAMERA 02'
        if src_t>=18.13: cam='CAMERA 03'
        if src_t>=23.87: cam='CAMERA 01'
        ov=panel('handoff'+cam,[("VISIBILITY-DRIVEN CAMERA SELECTION",31,MUTED,True),(f"ACTIVE VIEW  →  {cam}",42,WHITE,True),("YOLO selects the clearest right-arm view",27,MUTED,False)],790,TEAL); overlay_rgba(frame,ov,90,790)
    elif key=='return':
        ov=panel('return',[("RETURN VERIFIED",45,WHITE,True),("Shelf region matched • inventory committed",29,MUTED,False)],800,TEAL); overlay_rgba(frame,ov,1015,760)
    elif key=='end':
        ov=panel('end',[("PharmaSuite",62,WHITE,True),("Safer handling. Stronger trust.",33,MUTED,False)],760,TEAL); overlay_rgba(frame,ov,580,120)
    return frame

def caption_for(t):
    for a,b,txt in CAP:
        if a<=t<=b:return txt
    return ''

def caption_overlay(frame,text):
    if not text:return
    key='cap:'+text
    if key not in G:
        # Wrap using actual rendered width so long narration stays in the safe area.
        def wrap_px(txt,ft,maxw=1460):
            words=txt.split(); lines=[]; cur=''
            probe=ImageDraw.Draw(Image.new('RGB',(4,4)))
            for word in words:
                cand=(cur+' '+word).strip()
                if cur and probe.textbbox((0,0),cand,font=ft)[2]>maxw:
                    lines.append(cur); cur=word
                else:
                    cur=cand
            if cur: lines.append(cur)
            return lines
        fs=34; lines=wrap_px(text,font(fs,True))
        if len(lines)>2:
            fs=29; lines=wrap_px(text,font(fs,True))
        ph=120 if len(lines)>=3 else 100
        im=Image.new('RGBA',(1600,ph),(0,0,0,0)); d=ImageDraw.Draw(im)
        d.rounded_rectangle((0,0,1599,ph-1),radius=22,fill=(3,8,14,204))
        ft=font(fs,True); step=38 if fs<34 else 42; total=len(lines)*step; y=(ph-total)//2
        for line in lines:
            bb=d.textbbox((0,0),line,font=ft); x=(1600-(bb[2]-bb[0]))//2; d.text((x,y),line,font=ft,fill=(248,250,252,255)); y+=step
        G[key]=np.array(im)[:,:, [2,1,0,3]]
    overlay_rgba(frame,G[key],160,1080-G[key].shape[0])

def make_scene(scene):
    a,b,typ,key,start,speed,label=scene
    vids=[]
    if typ=='split':
        vids=[Vid(P[key[0]],start[0],speed),Vid(P[key[1]],start[1],speed)]
    elif typ in ('video','portrait','bridge'):
        vids=[Vid(P[key],start,speed)]
    return vids

def frame_scene(scene,vids,local):
    a,b,typ,key,start,speed,label=scene
    if typ=='bridge':
        # New synchronized Unity render; editorial cuts preserve its frame clock.
        bt=min(10.96,local*0.85);idx=min(329,round(bt*30))
        cam=0 if bt<2.7 else (1 if bt<6.9 else (2 if bt<9.4 else 0))
        sim=cv2.imread(str(OUT/'bridge'/('cam'+str(cam))/(f'{idx:06d}.jpg')))
        if local<2.0:
            frame=contain(vids[0].at(local),W,H)
            progress=max(0,min(1,(local-1.0)/1.0));progress=progress*progress*(3-2*progress)
            sw=int(680+(W-680)*progress);sh=int(382+(H-382)*progress)
            x=int((W-720)*(1-progress));y=int(140*(1-progress))
            tile=np.array(Image.fromarray(sim).resize((sw,sh),Image.Resampling.BILINEAR));frame[y:y+sh,x:x+sw]=tile
            cv2.rectangle(frame,(x,y),(x+sw-1,y+sh-1),(175,211,34),3)
        else:frame=sim
        ov=panel('bridge'+str(cam),[(f'Camera {cam+1:02d}',36,WHITE,True),('Continue behind the shelf',26,MUTED,False)],650,TEAL)
        if local>=2:overlay_rgba(frame,ov,50,110)
        return frame
    if typ=='video': frame=contain(vids[0].at(local),W,H)
    elif typ=='portrait': frame=portrait(vids[0].at(local))
    elif typ=='split':
        l=cover(vids[0].at(local),W//2,H); r=cover(vids[1].at(local),W//2,H); frame=np.concatenate([l,r],axis=1)
        cv2.line(frame,(W//2,0),(W//2,H),(45,220,190),5)
        cv2.rectangle(frame,(36,35),(300,94),(8,15,24),-1); cv2.putText(frame,'REAL WORLD',(58,77),cv2.FONT_HERSHEY_SIMPLEX,1.0,(240,247,250),2,cv2.LINE_AA)
        cv2.rectangle(frame,(W//2+36,35),(W//2+355,94),(8,15,24),-1); cv2.putText(frame,'UNITY DIGITAL TWIN',(W//2+58,77),cv2.FONT_HERSHEY_SIMPLEX,0.9,(240,247,250),2,cv2.LINE_AA)
    elif typ=='image':
        img=images[key]; zoom=1+0.04*local/(b-a); ih,iw=img.shape[:2]; r=cv2.resize(img,(round(iw*zoom),round(ih*zoom)),interpolation=cv2.INTER_CUBIC); frame=cover(r,W,H)
    elif typ=='regions':
        img=images[key]; thirds=[img[:785],img[785:1570],img[1570:]]; idx=min(2,int(local/(b-a)*3)); frame=cover(thirds[idx],W,H)
    else: frame=np.zeros((H,W,3),np.uint8)
    return special_overlay(frame,label,a+local,local+start if label=='lab_actions' else local)

ffmpeg=str(next((ROOT/'work/sim-venv/lib/python3.12/site-packages/imageio_ffmpeg/binaries').glob('ffmpeg-*')))
video_silent=OUT/'visuals-silent.mp4'
cmd=[ffmpeg,'-y','-f','rawvideo','-pix_fmt','bgr24','-s',f'{W}x{H}','-r',str(FPS),'-i','-','-an','-c:v','libx264','-preset','veryfast','-crf','18','-pix_fmt','yuv420p','-movflags','+faststart',str(video_silent)]
proc=subprocess.Popen(cmd,stdin=subprocess.PIPE,stderr=subprocess.DEVNULL)
scene_i=0; scene=SCENES[0]; vids=make_scene(scene); prev_last=None
n=int(DURATION*FPS)
for i in range(n):
    t=i/FPS
    while t>=scene[1]-1e-6 and scene_i<len(SCENES)-1:
        for v in vids:v.close()
        prev_last=last.copy()
        scene_i+=1; scene=SCENES[scene_i]; vids=make_scene(scene)
    local=t-scene[0]
    frame=frame_scene(scene,vids,local)
    # short freeze-frame dissolve makes source changes feel intentional and smooth
    if prev_last is not None and local<0.32:
        alpha=local/0.32; frame=cv2.addWeighted(prev_last,1-alpha,frame,alpha,0)
    # persistent chapter + progress rail
    chapter='THE PROBLEM' if t<35.755 else ('WORKING PRODUCT' if t<91.733 else ('AI PLACEMENT' if t<107.435 else 'IMU + COMPUTER VISION'))
    cv2.rectangle(frame,(0,0),(W,7),(20,31,42),-1); cv2.rectangle(frame,(0,0),(int(W*t/DURATION),7),(41,211,180),-1)
    cv2.rectangle(frame,(1510,28),(1885,82),(7,13,22),-1); cv2.putText(frame,chapter,(1535,64),cv2.FONT_HERSHEY_SIMPLEX,0.67,(235,242,246),2,cv2.LINE_AA)
    caption_overlay(frame,caption_for(t))
    proc.stdin.write(frame.tobytes()); last=frame
    if i%900==0: print(f'Rendered {t:.0f}s',flush=True)
for v in vids:v.close()
proc.stdin.close(); rc=proc.wait()
if rc: raise SystemExit(rc)
print(video_silent)
