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
        public const float WorkflowOffset = 40;
        public const float Duration = WorkflowOffset + 44;
        public const int SimulationFps = 30;
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
        public enum TaskState { Idle, Retracting, Walking, Carrying, ReachingToPickUp, ReachingToPlace, AtCounter, PickingUp, Placing, Disposing, Complete, Blocked }
        public enum BottleState { OnShelf, Held, AtCounter, Misplaced, Disposed }
        [Serializable] public class SensorAction { public float time; public string type; }
        public TaskState State { get; private set; }
        public string BlockedReason { get; private set; }
        public string HeldBottle { get; private set; }
        public int CompletedActions { get; private set; }
        public CollisionWorld World { get; private set; }
        public List<SensorAction> SensorEvents { get; private set; } = new List<SensorAction>();
        readonly Dictionary<string, BottleState> bottleStates = new Dictionary<string, BottleState>();
        readonly Dictionary<string, Vector3> bottlePositions = new Dictionary<string, Vector3>();
        readonly List<Vector3[]> routes = new List<Vector3[]>();
        readonly List<Vector3> stands = new List<Vector3>();
        Vector3[] surveyRoute;
        int currentFrame = -1;
        bool movementEmitted;
        float disposalOne = -1, disposalTwo = -1;
        public BottleState GetBottleState(string id) => bottleStates[id];

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
            Vector3 counter = new Vector3(-2.45f, 1.177f, -1.08f);
            Vector3 wrong = new Vector3(0f, a.y, a.z);
            Vector3 trash = new Vector3(2.5f, 1.02f, -.75f);
            Cues = new List<ActionCue> {
                new ActionCue(2,"pickup","shelf-a","bottle-a1",a,"dispense"),
                new ActionCue(7,"release","counter","bottle-a1",counter,"counter rest"),
                new ActionCue(9,"pickup","counter","bottle-a1",counter,"resume"),
                new ActionCue(14,"release","shelf-b","bottle-a1",wrong,"wrong return"),
                new ActionCue(16,"pickup","shelf-b","bottle-a1",wrong,"correct misplacement"),
                new ActionCue(20,"release","shelf-a","bottle-a1",a,"correct return"),
                new ActionCue(23,"pickup","shelf-a","bottle-a1",a,"expired bottle"),
                new ActionCue(29,"release","disposal","bottle-a1",trash,"dispose expired bottle"),
                new ActionCue(35,"pickup","shelf-a","bottle-a2",a2,"last bottle"),
                new ActionCue(41,"release","disposal","bottle-a2",trash,"dispose last bottle")
            };
            foreach(var cue in Cues) cue.time += WorkflowOffset;
            World = GetComponent<CollisionWorld>();
            if (!World) World = gameObject.AddComponent<CollisionWorld>();
            World.Build();
            foreach (var cue in Cues)
            {
                float zOffset = cue.region == "counter" ? .63f : cue.region == "disposal" ? .54f : .49f;
                stands.Add(new Vector3(cue.contact.x - .32f, rootY, cue.contact.z - zOffset));
            }
            for (int i=0; i<Cues.Count; i++)
            {
                var route = World.Route(i==0 ? stands[0] : stands[i-1], stands[i]);
                float seconds = Cues[i].time - (i==0 ? WorkflowOffset : Cues[i-1].time) - 1.4f;
                if (CollisionWorld.Length(route) * 1.5f / seconds > 1.8f)
                    throw new InvalidOperationException("Schedule requires unsafe walking speed: " + Cues[i].scenario);
                routes.Add(route);
            }
            var surveyPoints = new[]{
                new Vector3(3.25f,rootY,.5f), new Vector3(3.25f,rootY,2.65f),
                new Vector3(-3.25f,rootY,2.65f), new Vector3(-3.25f,rootY,5.2f),
                new Vector3(3.25f,rootY,5.2f), new Vector3(3.25f,rootY,.5f), stands[0]};
            var survey = new List<Vector3>();
            for(int i=1;i<surveyPoints.Length;i++) survey.AddRange(World.Route(surveyPoints[i-1],surveyPoints[i]));
            surveyRoute=survey.ToArray();
            if(CollisionWorld.Length(surveyRoute)/(WorkflowOffset-2)>1.8f)
                throw new InvalidOperationException("Survey route exceeds walking speed");
            ResetState();
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
            if (MediaTime >= Duration || State==TaskState.Blocked) Playing=false;
        }

        void ResetState()
        {
            currentFrame = -1; CompletedActions = 0; HeldBottle = null;
            State = TaskState.Idle; BlockedReason = ""; movementEmitted = false;
            SensorEvents.Clear(); disposalOne = disposalTwo = -1;
            bottleStates["bottle-a1"] = bottleStates["bottle-a2"] = BottleState.OnShelf;
            bottlePositions["bottle-a1"] = originOne; bottlePositions["bottle-a2"] = originTwo;
            technician.position = surveyRoute[0]; technician.rotation = Quaternion.identity;
            bottleOne.gameObject.SetActive(true); bottleTwo.gameObject.SetActive(true);
            Pose(surveyRoute[0], Quaternion.identity, null, 0);
            PositionBottles(0);
        }

        public void Restart()
        {
            Initialize(); ResetState(); Evaluate(0);
            Playing = State != TaskState.Blocked;
        }

        public void Evaluate(float time)
        {
            Initialize();
            int targetFrame = Mathf.FloorToInt(Mathf.Clamp(time, 0, Duration) * SimulationFps + .0001f);
            if (targetFrame < currentFrame) ResetState();
            // Replay fixed simulation ticks even after a large seek or slow UI frame.
            // Every tick checks collision and action preconditions before accepting events.
            for (int frame=currentFrame+1; frame<=targetFrame; frame++)
            {
                if (State == TaskState.Blocked) break;
                float t = (float)frame / SimulationFps;
                Step(t); currentFrame = frame;
            }
            MediaTime = (float)Mathf.Max(0,currentFrame) / SimulationFps;
        }

        void Step(float t)
        {
            if(t < WorkflowOffset)
            {
                if(occluder) occluder.gameObject.SetActive(false);
                Physics.SyncTransforms();
                float u=Mathf.Clamp01((t-1)/(WorkflowOffset-2));
                Vector3 position=CollisionWorld.Sample(surveyRoute,u,out var direction);
                State=t<1 || t>WorkflowOffset-1 ? TaskState.Idle : TaskState.Walking;
                Quaternion facing=Quaternion.LookRotation(direction);
                if(t>WorkflowOffset-1) facing=Quaternion.Slerp(facing,Quaternion.identity,t-(WorkflowOffset-1));
                float surveyGait=State==TaskState.Walking?Mathf.Sin(CollisionWorld.Length(surveyRoute)*u*14)*.09f:0;
                ApplyGuardedPose(position,facing,null,surveyGait);
                return;
            }
            bool screenActive = ambiguousReturn && t>=WorkflowOffset+18.5f && t<=WorkflowOffset+21.5f;
            if (occluder) occluder.gameObject.SetActive(screenActive);
            Physics.SyncTransforms();
            int index = CompletedActions;
            var previous = index == 0 ? null : Cues[index-1];
            var next = index < Cues.Count ? Cues[index] : null;
            Vector3 body = technician.position;
            Quaternion rotation = Quaternion.identity;
            Vector3? target = null;
            float gait = 0;
            float previousTime = previous == null ? WorkflowOffset : previous.time;
            Vector3 rest = new Vector3(.20f, 1.20f, .10f);
            if (previous != null && t < previous.time + .5f)
            {
                State = TaskState.Retracting;
                body = stands[index-1];
                target = Vector3.Lerp(previous.contact, body + rest,
                    Mathf.SmoothStep(0,1,(t-previous.time)/.5f));
            }
            else if (next == null)
            { State = TaskState.Complete; body = stands[stands.Count-1]; }
            else
            {
                float walkStart = previousTime + .5f;
                float walkEnd = next.time - .9f;
                float length = CollisionWorld.Length(routes[index]);
                if (length > .01f && t < walkEnd)
                {
                    State = HeldBottle == null ? TaskState.Walking : TaskState.Carrying;
                    float u = Mathf.Clamp01((t-walkStart)/(walkEnd-walkStart));
                    float eased = Mathf.SmoothStep(0,1,u);
                    body = CollisionWorld.Sample(routes[index],eased,out var direction);
                    float turn = Mathf.SmoothStep(0,1,Mathf.Min(u/.18f,(1-u)/.18f));
                    rotation = Quaternion.Slerp(Quaternion.identity,Quaternion.LookRotation(direction),turn);
                    gait = Mathf.Sin(length * eased * 14) * .11f * Mathf.Sin(u*Mathf.PI);
                }
                else if (t < walkEnd)
                {
                    State = previous != null && previous.region == "counter" ? TaskState.AtCounter : TaskState.Idle;
                    body = stands[index];
                }
                else
                {
                    State = next.type == "pickup" ? TaskState.ReachingToPickUp : TaskState.ReachingToPlace;
                    body = stands[index];
                    target = Vector3.Lerp(body + rest,next.contact,
                        Mathf.SmoothStep(0,1,(t-walkEnd)/.9f));
                }
            }
            Vector3 oldWrist = RightWrist.position;
            if(!ApplyGuardedPose(body,rotation,target,gait)) return;
            if (HeldBottle != null && !movementEmitted && Vector3.Distance(oldWrist,RightWrist.position)>.002f)
            { SensorEvents.Add(new SensorAction{time=t,type="movement"}); movementEmitted=true; }
            if (next != null && t + .0001f >= next.time)
            {
                if (!TryCommit(next,t,out string reason)) { Block(reason); return; }
            }
            PositionBottles(t);
        }

        bool ApplyGuardedPose(Vector3 body,Quaternion rotation,Vector3? target,float gait)
        {
            if (!World.CanMove(technician.position,body,out string obstacle))
            { Block("Body route blocked by " + obstacle); return false; }
            Vector3 oldPosition = technician.position;
            var oldRotations = bindRotations.Keys.ToDictionary(b=>b,b=>b.localRotation);
            Vector3 oldWrist = RightWrist.position;
            var armJoints = new[]{rightLower,RightWrist,leftLower,LeftWrist};
            var oldJointPositions = armJoints.Select(j=>j.position).ToArray();
            Pose(body,rotation,target,gait);
            bool clear = ValidatePose(oldWrist,out obstacle);
            for(int joint=0; clear && joint<armJoints.Length; joint++)
                clear = World.ClearSegment(oldJointPositions[joint],armJoints[joint].position,.035f,out obstacle);
            if (!clear)
            {
                technician.position = oldPosition;
                foreach (var entry in oldRotations) entry.Key.localRotation = entry.Value;
                Block(obstacle); return false;
            }
            return true;
        }

        void Pose(Vector3 body,Quaternion rotation,Vector3? target,float gait)
        {
            foreach (var item in bindRotations) item.Key.localRotation=item.Value;
            technician.position=body; technician.rotation=rotation;
            Vector3 wrist = target ?? (body + rotation * new Vector3(.20f,1.20f,.10f));
            if (target.HasValue)
            {
                var spine=Bone("Spine1");
                float lean=Mathf.Lerp(8,32,Mathf.InverseLerp(1.5f,.94f,wrist.y));
                spine.rotation=Quaternion.AngleAxis(lean,technician.right)*spine.rotation;
            }
            Vector3 rf=rightFoot.position,lf=leftFoot.position;
            Solve(rightThigh,rightCalf,rightFoot,rf+technician.forward*gait+Vector3.up*Mathf.Max(0,gait)*.5f,technician.forward);
            Solve(leftThigh,leftCalf,leftFoot,lf-technician.forward*gait+Vector3.up*Mathf.Max(0,-gait)*.5f,technician.forward);
            Solve(rightUpper,rightLower,RightWrist,wrist,rotation*new Vector3(.7f,-.6f,-.2f));
            Solve(leftUpper,leftLower,LeftWrist,leftUpper.position+rotation*new Vector3(-.08f,-.52f,.08f),rotation*new Vector3(-.5f,-.5f,.1f));
            foreach(var pair in bones)
                if(pair.Key.Contains("Finger") && !pair.Key.EndsWith("Nub"))
                    pair.Value.localRotation*=Quaternion.Euler(0,0,12);
        }

        public bool ValidatePose(Vector3 previousWrist,out string obstacle)
        {
            if (!World.CanStand(technician.position,out obstacle)) return false;
            foreach (var pair in new[]{new[]{rightUpper,rightLower},new[]{rightLower,RightWrist},new[]{leftUpper,leftLower},new[]{leftLower,LeftWrist}})
                if (!World.ClearSegment(pair[0].position,pair[1].position,.035f,out obstacle))
                { obstacle="Arm intersects " + obstacle; return false; }
            if (HeldBottle != null)
            {
                Vector3 center=RightWrist.position-Vector3.up*.065f;
                if (!World.ClearSegment(center-Vector3.up*.038f,center+Vector3.up*.065f,.056f,out obstacle))
                { obstacle="Carried bottle intersects " + obstacle; return false; }
                // Continuous swept check prevents tunnelling across thin walls between ticks.
                if (!World.ClearSegment(previousWrist-Vector3.up*.065f,center,.056f,out obstacle))
                { obstacle="Carried bottle sweep intersects " + obstacle; return false; }
            }
            return true;
        }

        bool TryCommit(ActionCue cue,float time,out string reason)
        {
            reason="";
            if (Vector3.Distance(technician.position,stands[CompletedActions])>.025f ||
                Vector3.Distance(RightWrist.position,cue.contact)>.08f)
            { reason="Cannot execute action before arrival and reachable contact: "+cue.scenario; return false; }
            if (cue.type=="pickup")
            {
                if (HeldBottle!=null || bottleStates[cue.bottle]==BottleState.Disposed ||
                    Vector3.Distance(bottlePositions[cue.bottle]+Vector3.up*.065f,cue.contact)>.025f)
                { reason="Pickup violates bottle ownership/location state"; return false; }
                HeldBottle=cue.bottle; bottleStates[cue.bottle]=BottleState.Held;
                movementEmitted=false; State=TaskState.PickingUp;
            }
            else
            {
                if (HeldBottle!=cue.bottle) { reason="Cannot release a bottle that is not held"; return false; }
                Vector3 center=cue.contact-Vector3.up*.065f;
                if(cue.region!="disposal" && (!Physics.Raycast(center,Vector3.down,out var support,.13f,CollisionWorld.SolidMask) || support.distance<.085f))
                { reason="Placement has no valid supporting surface"; return false; }
                bottlePositions[cue.bottle]=center;
                bottleStates[cue.bottle]=cue.region=="disposal"?BottleState.Disposed:cue.region=="counter"?BottleState.AtCounter:cue.region=="shelf-a"?BottleState.OnShelf:BottleState.Misplaced;
                if(cue.region=="disposal") { if(cue.bottle=="bottle-a1") disposalOne=time; else disposalTwo=time; }
                HeldBottle=null; State=cue.region=="disposal"?TaskState.Disposing:TaskState.Placing;
            }
            SensorEvents.Add(new SensorAction{time=time,type=cue.type}); CompletedActions++;
            return true;
        }

        void PositionBottles(float time)
        {
            foreach(var id in new[]{"bottle-a1","bottle-a2"})
            {
                var bottle=id=="bottle-a1"?bottleOne:bottleTwo;
                bottle.rotation=Quaternion.identity;
                Vector3 position=bottleStates[id]==BottleState.Held?RightWrist.position-Vector3.up*.065f:bottlePositions[id];
                if(bottleStates[id]==BottleState.Disposed)
                {
                    float elapsed=time-(id=="bottle-a1"?disposalOne:disposalTwo);
                    // Gravity inside a genuinely hollow bin, stopping on its bottom.
                    position.y=Mathf.Max(.21f,position.y-4.905f*elapsed*elapsed);
                    position.x+=id=="bottle-a1"?-.065f:.065f;
                }
                bottle.position=position;
            }
        }

        void Block(string reason)
        { State=TaskState.Blocked; BlockedReason=reason; Playing=false; }

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
            GUILayout.BeginArea(new Rect(16,16,540,130),GUI.skin.box);
            GUILayout.Label("PHARMA / ROOM CAMERA — synthetic footage");
            GUILayout.Label($"{MediaTime:0.00}s / {Duration:0}s | {State} | holding: {HeldBottle ?? "nothing"}");
            if(State==TaskState.Blocked) GUILayout.Label(BlockedReason);
            GUILayout.BeginHorizontal();
            if(GUILayout.Button(Playing?"Pause":"Play")) Playing=!Playing;
            if(GUILayout.Button("Restart")) Restart();
            ambiguousReturn=GUILayout.Toggle(ambiguousReturn,"Occluded return");
            GUILayout.EndHorizontal();
            float seek=GUILayout.HorizontalSlider(MediaTime,0,Duration);
            if(Mathf.Abs(seek-MediaTime)>.02f) { Playing=false; Evaluate(seek); }
            GUILayout.EndArea();
        }
    }
}
