import json,subprocess,argparse
from pathlib import Path
import cv2,imageio_ffmpeg
parser=argparse.ArgumentParser();parser.add_argument('frames_directory',type=Path);parser.add_argument('output',type=Path);args=parser.parse_args();root=args.frames_directory;out=args.output
rig={r['frame']:r for r in map(json.loads,(root/'evaluator_only/privacy_rig.jsonl').read_text().splitlines())}
edges=[0,1,1,2,2,3,2,4,4,5,5,6,2,7,7,8,8,9,3,10,10,11,11,12,3,13,13,14,14,15]
cmd=[imageio_ffmpeg.get_ffmpeg_exe(),'-y','-v','error','-f','rawvideo','-pix_fmt','bgr24','-s','960x540','-r','15','-i','-','-an','-c:v','libx264','-crf','20','-pix_fmt','yuv420p','-movflags','+faststart',str(out)]
p=subprocess.Popen(cmd,stdin=subprocess.PIPE)
for i,f in enumerate(sorted((root/'frames').glob('*.png'))):
 frame=cv2.imread(str(f))
 if i in rig:
  joints=rig[i]['joints_pixels']
  for a,b in zip(edges[::2],edges[1::2]):
   A,B=joints[a],joints[b]
   if A['z']>0 and B['z']>0:cv2.line(frame,(round(A['x']),round(A['y'])),(round(B['x']),round(B['y'])),(195,255,70),2,cv2.LINE_AA)
 cv2.rectangle(frame,(0,0),(960,34),(35,27,20),-1)
 cv2.putText(frame,'IMG_3537 | UNITY RECONSTRUCTION | SYNTHETIC MEDICATIONS',(14,23),cv2.FONT_HERSHEY_SIMPLEX,.53,(255,255,255),1,cv2.LINE_AA)
 if i in rig:cv2.putText(frame,'Action window: Unity rig overlay (not camera CV)',(14,520),cv2.FONT_HERSHEY_SIMPLEX,.5,(195,255,70),1,cv2.LINE_AA)
 p.stdin.write(frame.tobytes())
p.stdin.close();assert p.wait()==0
cap=cv2.VideoCapture(str(out));assert int(cap.get(cv2.CAP_PROP_FRAME_COUNT))==667;cap.release()
print(out,out.stat().st_size,'bytes;',len(rig),'pre-action skeleton frames')
