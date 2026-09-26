using System;
using System.Collections.Generic;
using System.Linq;
using UnityEngine;

namespace Pharma.Simulation
{
    [Serializable] public class ActionCue
    {
        public float time;
        public string type, region, bottle, scenario;
        public Vector3 contact;
        public ActionCue(float t, string action, string location, string id, Vector3 p, string label)
        { time=t; type=action; region=location; bottle=id; contact=p; scenario=label; }
    }

    [Serializable] public class ShelfRegion
    {
        public string id, kind, medication;
        public Vector3 center, size;
    }

    /// <summary>Deterministic scene animation. Scene knowledge is exporter/evaluator-only.</summary>
    public class PharmacySimulation : MonoBehaviour
    {
        public Camera roomCamera;
        public Transform technician;
        public Transform bottleOne, bottleTwo;
        public Transform occluder;
        public ShelfRegion[] regions;
        public bool ambiguousReturn;
        public bool playOnStart = true;
        public float playbackSpeed = 1;
        public const float Duration = 38;
        public float MediaTime { get; private set; }
        public bool Playing { get; private set; }
        public List<ActionCue> Cues { get; private set; }
        public Transform RightWrist { get; private set; }
        public Transform LeftWrist { get; private set; }
        Dictionary<Transform, Quaternion> bindRotations;
        Dictionary<string, Transform> bones;
        Vector3 originOne, originTwo;
        Transform rightUpper, rightLower, leftUpper, leftLower;
        Transform rightThigh, rightCalf, rightFoot, leftThigh, leftCalf, leftFoot;
        float rootY;

        public void Initialize()
        {
            if (bindRotations != null) return;
            if (!technician || !roomCamera || !bottleOne || !bottleTwo)
                throw new InvalidOperationException("Scene has not been built. Use Pharma > Build pharmacy scene.");
            var animator = technician.GetComponent<Animator>();
            if (animator) animator.enabled = false;
            bones = new Dictionary<string, Transform>();
            bindRotations = new Dictionary<Transform, Quaternion>();
            foreach (var bone in technician.GetComponentsInChildren<Transform>(true))
            { bones[bone.name.Replace("Bip02", "Bip01")] = bone; bindRotations[bone] = bone.localRotation; }
            rightUpper = Bone("R UpperArm"); rightLower = Bone("R Forearm"); RightWrist = Bone("R Hand");
            leftUpper = Bone("L UpperArm"); leftLower = Bone("L Forearm"); LeftWrist = Bone("L Hand");
            rightThigh = Bone("R Thigh"); rightCalf = Bone("R Calf"); rightFoot = Bone("R Foot");
            leftThigh = Bone("L Thigh"); leftCalf = Bone("L Calf"); leftFoot = Bone("L Foot");
            rootY = technician.position.y;
            originOne = bottleOne.position; originTwo = bottleTwo.position;
            Vector3 a = originOne + Vector3.up * .065f;
            Vector3 a2 = originTwo + Vector3.up * .065f;
            Vector3 counter = new Vector3(-2.45f, 1.13f, -.8f);
            Vector3 wrong = new Vector3(0f, a.y, a.z);
            Vector3 trash = new Vector3(2.5f, .94f, -.55f);
            Cues = new List<ActionCue> {
                new ActionCue(2,"pickup","shelf-a","bottle-a1",a,"dispense"),
                new ActionCue(6,"release","counter","bottle-a1",counter,"counter rest"),
                new ActionCue(8,"pickup","counter","bottle-a1",counter,"resume"),
                new ActionCue(12,"release","shelf-b","bottle-a1",wrong,"wrong return"),
                new ActionCue(14,"pickup","shelf-b","bottle-a1",wrong,"correct misplacement"),
                new ActionCue(18,"release","shelf-a","bottle-a1",a,"correct return"),
                new ActionCue(21,"pickup","shelf-a","bottle-a1",a,"expired bottle"),
                new ActionCue(26,"release","disposal","bottle-a1",trash,"dispose expired bottle"),
                new ActionCue(29,"pickup","shelf-a","bottle-a2",a2,"last bottle"),
                new ActionCue(35,"release","disposal","bottle-a2",trash,"dispose last bottle")
            };
        }

        Transform Bone(string name)
        {
            if (bones.TryGetValue("Bip01 " + name, out var bone)) return bone;
            throw new InvalidOperationException("Missing Rocketbox bone: " + name);
        }

        void Start() { Initialize(); Playing=playOnStart; Evaluate(0); }
        void Update()
        {
            if (!Playing) return;
            Evaluate(Mathf.Min(MediaTime + Time.deltaTime * playbackSpeed, Duration));
            if (MediaTime >= Duration) Playing=false;
        }

        public void Evaluate(float time)
        {
            Initialize(); MediaTime = Mathf.Clamp(time, 0, Duration);
            foreach (var item in bindRotations) item.Key.localRotation = item.Value;
            ActionCue previous = Cues[0], next = Cues[0];
            foreach (var cue in Cues)
            {
                if (cue.time <= MediaTime) previous=cue;
                if (cue.time >= MediaTime) { next=cue; break; }
                next=cue;
            }
            float span=next.time-previous.time;
            float u=span > .001f ? Mathf.Clamp01((MediaTime-previous.time)/span) : 0;
            float moveU=Mathf.SmoothStep(0, 1, Mathf.Clamp01((u-.12f)/.76f));
            Vector3 p=Vector3.Lerp(previous.contact,next.contact,moveU);
            // Stand to the left of the contact to keep the working arm visible.
            technician.position=new Vector3(p.x-.32f,rootY,p.z-.49f);
            technician.rotation=Quaternion.Euler(0,0,0);
            // Lean the upper torso toward low work surfaces so the grasp stays reachable.
            // Spine1 is above the leg branches in the Rocketbox hierarchy.
            var spine = Bone("Spine1");
            float lean = Mathf.Lerp(8, 32, Mathf.InverseLerp(1.5f, .94f, p.y));
            spine.rotation = Quaternion.AngleAxis(lean, technician.right) * spine.rotation;
            float travel=Vector3.Distance(previous.contact,next.contact);
            float gait = travel > .2f && u > .15f && u < .85f ? Mathf.Sin(MediaTime*8)*.10f : 0;
            Vector3 rf=rightFoot.position, lf=leftFoot.position;
            Solve(rightThigh,rightCalf,rightFoot,rf+new Vector3(0,Mathf.Max(0,gait)*.5f,gait),Vector3.forward);
            Solve(leftThigh,leftCalf,leftFoot,lf+new Vector3(0,Mathf.Max(0,-gait)*.5f,-gait),Vector3.forward);
            Vector3 wrist=p+Vector3.up*(Mathf.Sin(moveU*Mathf.PI)*.14f);
            // A small approach/retraction keeps the arm from freezing in a reach at the endpoints.
            if (MediaTime<1.4f) wrist=Vector3.Lerp(rightUpper.position+new Vector3(.08f,-.45f,.08f),Cues[0].contact,Mathf.SmoothStep(0,1,MediaTime/1.4f));
            if (MediaTime>35.4f) wrist=Vector3.Lerp(Cues[Cues.Count-1].contact,rightUpper.position+new Vector3(.08f,-.45f,.08f),Mathf.SmoothStep(0,1,(MediaTime-35.4f)/1.1f));
            Solve(rightUpper,rightLower,RightWrist,wrist,new Vector3(.7f,-.6f,-.2f));
            Solve(leftUpper,leftLower,LeftWrist,leftUpper.position+new Vector3(-.08f,-.52f,.08f),new Vector3(-.5f,-.5f,.1f));
            // Relax fingers rather than rendering the original open reference pose.
            foreach(var pair in bones)
                if(pair.Key.Contains("Finger") && !pair.Key.EndsWith("Nub"))
                    pair.Value.localRotation *= Quaternion.Euler(0,0,12);
            PlaceBottle(bottleOne,"bottle-a1",originOne);
            PlaceBottle(bottleTwo,"bottle-a2",originTwo);
            if (occluder) occluder.gameObject.SetActive(ambiguousReturn && MediaTime>=16.5f && MediaTime<=19.5f);
        }

        void PlaceBottle(Transform bottle,string id,Vector3 origin)
        {
            ActionCue last=null;
            foreach(var cue in Cues) if(cue.bottle==id && cue.time<=MediaTime) last=cue;
            bottle.gameObject.SetActive(last==null || last.region!="disposal" || last.type!="release" || MediaTime-last.time<.55f);
            bottle.rotation=Quaternion.identity;
            if(last==null) bottle.position=origin;
            else if(last.type=="pickup") bottle.position=RightWrist.position-Vector3.up*.065f;
            else if(last.region=="disposal") bottle.position=last.contact-Vector3.up*(.065f+Mathf.Min(.7f,4*(MediaTime-last.time)*(MediaTime-last.time)));
            else bottle.position=last.contact-Vector3.up*.065f;
        }

        static void Solve(Transform upper,Transform lower,Transform tip,Vector3 target,Vector3 pole)
        {
            Vector3 start=upper.position;
            float a=Vector3.Distance(start,lower.position), b=Vector3.Distance(lower.position,tip.position);
            Vector3 delta=target-start;
            float d=Mathf.Clamp(delta.magnitude,Mathf.Abs(a-b)+.001f,a+b-.001f);
            Vector3 axis=delta.normalized;
            Vector3 bend=Vector3.ProjectOnPlane(pole,axis).normalized;
            float x=(a*a-b*b+d*d)/(2*d), y=Mathf.Sqrt(Mathf.Max(0,a*a-x*x));
            Vector3 elbow=start+axis*x+bend*y;
            upper.rotation=Quaternion.FromToRotation(lower.position-start,elbow-start)*upper.rotation;
            lower.rotation=Quaternion.FromToRotation(tip.position-lower.position,target-lower.position)*lower.rotation;
        }

        void OnGUI()
        {
            GUILayout.BeginArea(new Rect(16,16,350,105),GUI.skin.box);
            GUILayout.Label("PHARMA / ROOM CAMERA — synthetic footage");
            GUILayout.Label($"{MediaTime:0.00}s / {Duration:0}s    One technician · one bottle");
            GUILayout.BeginHorizontal();
            if(GUILayout.Button(Playing?"Pause":"Play")) Playing=!Playing;
            if(GUILayout.Button("Restart")) { Evaluate(0); Playing=true; }
            ambiguousReturn=GUILayout.Toggle(ambiguousReturn,"Occluded return");
            GUILayout.EndHorizontal();
            float seek=GUILayout.HorizontalSlider(MediaTime,0,Duration);
            if(Mathf.Abs(seek-MediaTime)>.02f) { Playing=false; Evaluate(seek); }
            GUILayout.EndArea();
        }
    }
}
