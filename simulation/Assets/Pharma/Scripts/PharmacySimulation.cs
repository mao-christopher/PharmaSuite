using System;
using System.Collections.Generic;
using System.Linq;
using UnityEngine;

namespace Pharma.Simulation
{
    /// <summary>Deterministic scene animation. Scene knowledge is exporter/evaluator-only.
    /// Composes the room description, a motion source for the body, and the action schedule.</summary>
    public partial class PharmacySimulation : MonoBehaviour
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
        public const float WorkflowTimeScale = 1.5f;
        public const float Duration = WorkflowOffset + 44*WorkflowTimeScale;
        public const int SimulationFps = 30;
        public float MediaTime { get; private set; }
        public bool Playing { get; private set; }
        public Transform RightWrist { get; private set; }
        public Transform LeftWrist { get; private set; }
        Dictionary<Transform, Quaternion> bindRotations;
        Dictionary<Transform, Vector3> bindPositions;
        FootPlantGait footGait;
        Vector3 animationVelocity;
        float animationTime, traveledDistance;
        public int SwingFoot => footGait == null ? -1 : footGait.SwingFoot;
        public Vector3 RightFootTarget => footGait.Right;
        public Vector3 LeftFootTarget => footGait.Left;
        public Transform RightFoot => rightFoot;
        public Transform LeftFoot => leftFoot;
        public bool showSimulationSkeleton = true;
        Dictionary<string, Transform> bones;
        Dictionary<string, Transform> bottleObjects;
        Transform rightUpper, rightLower, leftUpper, leftLower;
        Transform rightThigh, rightCalf, rightFoot, leftThigh, leftCalf, leftFoot;
        public enum TaskState { Idle, Retracting, Walking, Carrying, ReachingToPickUp, ReachingToPlace, AtCounter, PickingUp, Placing, Disposing, Complete, Blocked }
        public enum BottleState { OnShelf, Held, AtCounter, Misplaced, Disposed }
        [Serializable] public class SensorAction { public float time; public string type; }
        public TaskState State { get; private set; }
        public string BlockedReason { get; private set; }
        public RoomDescription Room { get; private set; }
        public IMotionSource Motion { get; private set; }
        public ActionSchedule Actions { get; private set; }
        public List<ActionCue> Cues => Actions?.Cues;
        public string HeldBottle => reenactment != null ? reenactment.HeldBottle : Actions?.HeldBottle;
        public int CompletedActions => Actions == null ? 0 : Actions.CompletedActions;
        public List<SensorAction> SensorEvents => Actions?.SensorEvents;
        public CollisionWorld World { get; private set; }
        int currentFrame = -1;
        public BottleState GetBottleState(string id) => Actions.GetBottleState(id);

        public void Initialize()
        {
            if (bindRotations != null) return;
            if (!technician || !roomCamera || !bottleOne || !bottleTwo)
                throw new InvalidOperationException("Scene has not been built. Use Pharma > Build pharmacy scene.");
            InitializeRig();
            Room = RoomDescription.FromScene(regions, technician, bottleOne, bottleTwo);
            bottleObjects = new Dictionary<string, Transform> { ["bottle-a1"] = bottleOne, ["bottle-a2"] = bottleTwo };
            Actions = new ActionSchedule(Room, ActionSchedule.Demo(Room), WorkflowOffset);
            World = GetComponent<CollisionWorld>();
            if (!World) World = gameObject.AddComponent<CollisionWorld>();
            World.Build(Room);
            Motion = new PlannedWalk(World, Actions, Room.floorY);
            ResetState();
        }

        /// <summary>Binds the Rocketbox skeleton; shared by the demo and the re-enactment.</summary>
        void InitializeRig()
        {
            var animator = technician.GetComponent<Animator>();
            if (animator) animator.enabled = false;
            bones = new Dictionary<string, Transform>();
            bindRotations = new Dictionary<Transform, Quaternion>();
            bindPositions = new Dictionary<Transform, Vector3>();
            foreach (var bone in technician.GetComponentsInChildren<Transform>(true))
            { bones[bone.name.Replace("Bip02", "Bip01")] = bone; bindRotations[bone] = bone.localRotation; bindPositions[bone]=bone.localPosition; }
            rightUpper = Bone("R UpperArm"); rightLower = Bone("R Forearm"); RightWrist = Bone("R Hand");
            leftUpper = Bone("L UpperArm"); leftLower = Bone("L Forearm"); LeftWrist = Bone("L Hand");
            rightThigh = Bone("R Thigh"); rightCalf = Bone("R Calf"); rightFoot = Bone("R Foot");
            leftThigh = Bone("L Thigh"); leftCalf = Bone("L Calf"); leftFoot = Bone("L Foot");
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
            Evaluate(Mathf.Min(MediaTime + Time.deltaTime * playbackSpeed, EndTime));
            if (MediaTime >= EndTime || State==TaskState.Blocked) Playing=false;
        }

        void ResetState()
        {
            if (reenactment != null) { ResetReenactment(); return; }
            currentFrame = -1; Actions.Reset();
            footGait=null; animationTime=0; traveledDistance=0; animationVelocity=Vector3.zero;
            State = TaskState.Idle; BlockedReason = "";
            var start = Motion.Start;
            technician.position = start.position; technician.rotation = start.facing;
            foreach (var bottle in bottleObjects.Values) bottle.gameObject.SetActive(true);
            Pose(start.position, start.facing, null, 0);
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
            int targetFrame = Mathf.FloorToInt(Mathf.Clamp(time, 0, EndTime) * TickRate + .0001f);
            if (targetFrame < currentFrame) ResetState();
            // Replay fixed simulation ticks even after a large seek or slow UI frame.
            // Every tick checks collision and action preconditions before accepting events.
            for (int frame=currentFrame+1; frame<=targetFrame; frame++)
            {
                if (State == TaskState.Blocked) break;
                float t = (float)frame / TickRate;
                Step(t); currentFrame = frame;
            }
            MediaTime = (float)Mathf.Max(0,currentFrame) / TickRate;
        }

        void Step(float t)
        {
            if (reenactment != null) { StepReenactment(t); return; }
            animationTime=t;
            var leg = Actions.Leg(t);
            // The optional panel hides the correct return; it is never shown during the walkthrough.
            bool screenActive = leg.started && ambiguousReturn && t>=WorkflowOffset+18.5f*WorkflowTimeScale && t<=WorkflowOffset+21.5f*WorkflowTimeScale;
            if (occluder) occluder.gameObject.SetActive(screenActive);
            Physics.SyncTransforms();
            var body = Motion.Sample(t, leg);
            if (!leg.started)
            {
                State = body.walking ? TaskState.Walking : TaskState.Idle;
                ApplyGuardedPose(body.position,body.facing,null,body.gait);
                return;
            }
            State = Actions.Hand(t, leg, body, RestHand(), out var target);
            Vector3 oldWrist = RightWrist.position;
            if(!ApplyGuardedPose(body.position,body.facing,target,body.gait)) return;
            Actions.NoteWristMotion(t, oldWrist, RightWrist.position);
            var due = Actions.Due(t, leg);
            if (due != null)
            {
                if (!Actions.TryCommit(due,t,technician.position,RightWrist.position,out var committed,out string reason)) { Block(reason); return; }
                State = committed;
            }
            PositionBottles(t);
        }

        bool ApplyGuardedPose(Vector3 body,Quaternion rotation,Vector3? target,float gait)
        {
            if (TryPose(body,rotation,target,gait,false,out string obstacle)) return true;
            Block(obstacle); return false;
        }

        /// <summary>Poses the rig if the body, limbs and carried bottle stay clear; otherwise
        /// restores the previous pose. A cut places the body without sweeping from the last pose.</summary>
        bool TryPose(Vector3 body,Quaternion rotation,Vector3? target,float gait,bool cut,out string obstacle)
        {
            if (cut ? !World.CanStand(body,out obstacle) : !World.CanMove(technician.position,body,out obstacle))
            { obstacle = "Body route blocked by " + obstacle; return false; }
            Vector3 oldPosition = technician.position;
            var oldRotations = bindRotations.Keys.ToDictionary(b=>b,b=>b.localRotation);
            var oldPositions = bindPositions.Keys.ToDictionary(b=>b,b=>b.localPosition);
            Vector3 oldWrist = RightWrist.position;
            var armJoints = new[]{rightLower,RightWrist,leftLower,LeftWrist,rightFoot,leftFoot};
            var oldJointPositions = armJoints.Select(j=>j.position).ToArray();
            if (cut) { footGait=null; animationVelocity=Vector3.zero; }
            else
            {
                rotation=Quaternion.RotateTowards(technician.rotation,rotation,reenactment==null ? 180f/SimulationFps : 180f/TickRate);
                animationVelocity=(body-oldPosition)*TickRate;
                traveledDistance+=Vector3.Distance(body,oldPosition);
            }
            Pose(body,rotation,target,gait);
            bool clear = ValidatePose(cut ? RightWrist.position : oldWrist,out obstacle);
            for(int joint=0; clear && !cut && joint<armJoints.Length; joint++)
                clear = World.ClearSegment(oldJointPositions[joint],armJoints[joint].position,.035f,out obstacle);
            if (!clear)
            {
                technician.position = oldPosition;
                foreach (var entry in oldRotations) entry.Key.localRotation = entry.Value;
                foreach (var entry in oldPositions) entry.Key.localPosition = entry.Value;
                return false;
            }
            return true;
        }

        Vector3 RestHand() => HeldBottle==null ? new Vector3(.20f,.94f,.08f) : new Vector3(.20f,1.17f,.16f);

        void Pose(Vector3 body,Quaternion rotation,Vector3? target,float gait)
        {
            foreach (var item in bindRotations) item.Key.localRotation=item.Value;
            foreach (var item in bindPositions) item.Key.localPosition=item.Value;
            technician.position=body; technician.rotation=rotation;
            float speed=animationVelocity.magnitude;
            float walking=Mathf.Clamp01(speed/.55f);
            float cycle=traveledDistance*2*Mathf.PI/1.05f;
            Vector3 neutralRight=rightFoot.position, neutralLeft=leftFoot.position;
            Quaternion neutralRightRotation=rightFoot.rotation,neutralLeftRotation=leftFoot.rotation;
            if(footGait==null) footGait=new FootPlantGait(neutralRight,neutralLeft,neutralRightRotation,neutralLeftRotation);
            footGait.Tick(neutralRight,neutralLeft,neutralRightRotation,neutralLeftRotation,animationVelocity,reenactment==null ? 1f/SimulationFps : 1f/TickRate);
            float armSwing=Mathf.Clamp(Vector3.Dot(footGait.Left-footGait.Right,technician.forward)*.35f,-.14f,.14f);
            var pelvis=Bone("Pelvis");
            pelvis.position+=technician.right*(.005f*Mathf.Sin(cycle)*walking);
            // Raise the pelvis as far as BOTH legs allow; the stance leg stays nearly straight.
            float rightHeight=HipHeightForFoot(rightThigh,rightCalf,rightFoot,footGait.Right);
            float leftHeight=HipHeightForFoot(leftThigh,leftCalf,leftFoot,footGait.Left);
            pelvis.position+=Vector3.up*(Mathf.Min(rightHeight-rightThigh.position.y,leftHeight-leftThigh.position.y)-.001f);
            Vector3 resting=body+rotation*RestHand();
            Vector3 wrist=target ?? resting;
            float reach=target.HasValue?Mathf.Clamp01(Vector3.Distance(wrist,resting)/.45f):0;
            var spine=Bone("Spine1");
            float lean=Mathf.Lerp(8,32,Mathf.InverseLerp(1.5f,.94f,wrist.y))*reach;
            spine.rotation=Quaternion.AngleAxis(lean,technician.right)*spine.rotation;
            spine.rotation=Quaternion.AngleAxis(Mathf.Sin(cycle)*3*walking*(1-reach),Vector3.up)*spine.rotation;
            // Small breathing and gaze movement; no sudden full-torso lean at reach onset.
            var chest=Bone("Spine2");
            chest.rotation=Quaternion.AngleAxis(.45f*Mathf.Sin(animationTime*1.6f)*(1-reach),technician.right)*chest.rotation;
            var head=Bone("Head");
            head.rotation=Quaternion.AngleAxis((reenactment==null && animationTime<WorkflowOffset?9*Mathf.Sin(animationTime*.8f):0)*(1-reach),Vector3.up)*head.rotation;
            Solve(rightThigh,rightCalf,rightFoot,footGait.Right,technician.forward);
            Solve(leftThigh,leftCalf,leftFoot,footGait.Left,technician.forward);
            rightFoot.rotation=footGait.RightRotation; leftFoot.rotation=footGait.LeftRotation;
            if(!target.HasValue && HeldBottle==null)
                wrist+=rotation*new Vector3(0,-.03f*walking,armSwing*walking);
            Vector3 leftWrist=leftUpper.position+rotation*new Vector3(-.08f,-.59f,.08f-armSwing*walking);
            Solve(rightUpper,rightLower,RightWrist,wrist,rotation*new Vector3(.7f,-.6f,-.2f));
            Solve(leftUpper,leftLower,LeftWrist,leftWrist,rotation*new Vector3(-.5f,-.5f,.1f));
            float grip=HeldBottle!=null?1:reach*.8f;
            foreach(var pair in bones)
                if(pair.Key.Contains("Finger") && !pair.Key.EndsWith("Nub"))
                    pair.Value.localRotation*=Quaternion.Euler(0,0,pair.Key.Contains("R Finger")?Mathf.Lerp(8,30,grip):10);
        }

        static float HipHeightForFoot(Transform thigh,Transform calf,Transform foot,Vector3 target)
        {
            float length=Vector3.Distance(thigh.position,calf.position)+Vector3.Distance(calf.position,foot.position)-.009f;
            Vector3 delta=target-thigh.position;delta.y=0;
            return target.y+Mathf.Sqrt(Mathf.Max(.01f,length*length-delta.sqrMagnitude));
        }

        public Transform[] SkeletonJoints() => new[]{Bone("Head"),Bone("Neck"),Bone("Spine2"),Bone("Pelvis"),
            rightUpper,rightLower,RightWrist,leftUpper,leftLower,LeftWrist,rightThigh,rightCalf,rightFoot,leftThigh,leftCalf,leftFoot};

        public bool ValidatePose(Vector3 previousWrist,out string obstacle)
        {
            if (!World.CanStand(technician.position,out obstacle)) return false;
            foreach (var pair in new[]{new[]{rightUpper,rightLower},new[]{rightLower,RightWrist},new[]{leftUpper,leftLower},new[]{leftLower,LeftWrist},new[]{rightThigh,rightCalf},new[]{rightCalf,rightFoot},new[]{leftThigh,leftCalf},new[]{leftCalf,leftFoot}})
                if (!World.ClearSegment(pair[0].position,pair[1].position,.035f,out obstacle))
                { obstacle="Limb intersects " + obstacle; return false; }
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

        void PositionBottles(float time)
        {
            foreach(var placement in Room.bottles)
            {
                var bottle=bottleObjects[placement.id];
                bottle.rotation=Quaternion.identity;
                bottle.position=Actions.BottlePosition(placement,time,RightWrist.position);
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
            if(showSimulationSkeleton && bindRotations!=null) SimulationSkeleton.Draw(this);
            GUILayout.BeginArea(new Rect(16,16,570,155),GUI.skin.box);
            GUILayout.Label("PHARMA / ROOM CAMERA — synthetic footage");
            GUILayout.Label($"{MediaTime:0.00}s / {EndTime:0}s | {State} | holding: {HeldBottle ?? "nothing"}");
            if(State==TaskState.Blocked) GUILayout.Label(BlockedReason);
            GUILayout.BeginHorizontal();
            if(GUILayout.Button(Playing?"Pause":"Play")) Playing=!Playing;
            if(GUILayout.Button("Restart")) Restart();
            ambiguousReturn=GUILayout.Toggle(ambiguousReturn,"Occluded return");
            GUILayout.EndHorizontal();
            showSimulationSkeleton=GUILayout.Toggle(showSimulationSkeleton,"Simulation X-ray skeleton (Unity rig, not CV)");
            float seek=GUILayout.HorizontalSlider(MediaTime,0,EndTime);
            if(Mathf.Abs(seek-MediaTime)>.02f) { Playing=false; Evaluate(seek); }
            GUILayout.EndArea();
        }
    }
}