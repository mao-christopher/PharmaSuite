using System;
using System.Collections.Generic;
using UnityEngine;
using TaskState = Pharma.Simulation.PharmacySimulation.TaskState;
using BottleState = Pharma.Simulation.PharmacySimulation.BottleState;
using SensorAction = Pharma.Simulation.PharmacySimulation.SensorAction;

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

    /// <summary>Where one tick falls in the schedule: the hand retracts after the previous
    /// action, the body travels until walkEnd, then the hand reaches for cue `index`.</summary>
    public struct ActionLeg
    {
        public int index;
        public float walkStart, walkEnd;
        public bool started, retracting, complete;
    }

    /// <summary>Scheduled pickups/releases with guarded bottle ownership. Only accepted actions emit events.</summary>
    public class ActionSchedule
    {
        public List<ActionCue> Cues { get; }
        public List<Vector3> Stands { get; } = new List<Vector3>();
        public float StartTime { get; }
        public string HeldBottle { get; private set; }
        public int CompletedActions { get; private set; }
        public List<SensorAction> SensorEvents { get; } = new List<SensorAction>();
        readonly RoomDescription room;
        readonly Dictionary<string, BottleState> bottleStates = new Dictionary<string, BottleState>();
        readonly Dictionary<string, Vector3> bottlePositions = new Dictionary<string, Vector3>();
        readonly Dictionary<string, float> disposalTimes = new Dictionary<string, float>();
        bool movementEmitted;
        public BottleState GetBottleState(string id) => bottleStates[id];

        public ActionSchedule(RoomDescription room, List<ActionCue> cues, float startTime)
        {
            this.room = room; Cues = cues; StartTime = startTime;
            foreach (var cue in Cues) Stands.Add(room.StandFor(cue.contact, cue.region));
            Reset();
        }

        /// <summary>The fixed demo workflow on the front bank, scaled onto the media clock.</summary>
        public static List<ActionCue> Demo(RoomDescription room)
        {
            Vector3 a = room.Bottle("bottle-a1").origin + Vector3.up * .065f;
            Vector3 a2 = room.Bottle("bottle-a2").origin + Vector3.up * .065f;
            Vector3 counter = room.counterContact;
            Vector3 wrong = new Vector3(room.Region("shelf-b").center.x, a.y, a.z);
            Vector3 trash = room.disposalContact;
            var cues = new List<ActionCue> {
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
            foreach(var cue in cues) cue.time = PharmacySimulation.WorkflowOffset+cue.time*PharmacySimulation.WorkflowTimeScale;
            return cues;
        }

        public void Reset()
        {
            CompletedActions = 0; HeldBottle = null; movementEmitted = false;
            SensorEvents.Clear(); disposalTimes.Clear();
            foreach (var bottle in room.bottles)
            { bottleStates[bottle.id] = BottleState.OnShelf; bottlePositions[bottle.id] = bottle.origin; }
        }

        public ActionLeg Leg(float t)
        {
            if (t < StartTime) return new ActionLeg();
            int index = CompletedActions;
            var previous = index == 0 ? null : Cues[index-1];
            var next = index < Cues.Count ? Cues[index] : null;
            float previousTime = previous == null ? StartTime : previous.time;
            return new ActionLeg {
                index = index, started = true, complete = next == null,
                retracting = previous != null && t < previous.time + .5f,
                walkStart = previousTime + .5f, walkEnd = next == null ? 0 : next.time - .9f };
        }

        /// <summary>Hand target for this tick: retract from the last contact, carry, wait, or reach.</summary>
        public TaskState Hand(float t, ActionLeg leg, MotionSample body, Vector3 rest, out Vector3? target)
        {
            target = null;
            if (leg.retracting)
            {
                var previous = Cues[leg.index-1];
                target = Vector3.Lerp(previous.contact, body.position + rest, Ease((t-previous.time)/.5f));
                return TaskState.Retracting;
            }
            if (leg.complete) return TaskState.Complete;
            if (body.walking) return HeldBottle == null ? TaskState.Walking : TaskState.Carrying;
            if (t < leg.walkEnd)
                return leg.index > 0 && Cues[leg.index-1].region == "counter" ? TaskState.AtCounter : TaskState.Idle;
            var next = Cues[leg.index];
            float reach=Ease((t-leg.walkEnd)/.9f);
            target = Vector3.Lerp(body.position + rest,next.contact,reach)+Vector3.up*(.045f*Mathf.Sin(Mathf.PI*reach));
            return next.type == "pickup" ? TaskState.ReachingToPickUp : TaskState.ReachingToPlace;
        }

        /// <summary>The first wrist motion while holding a bottle emits one abstract movement event.</summary>
        public void NoteWristMotion(float t, Vector3 oldWrist, Vector3 wrist)
        {
            if (HeldBottle != null && !movementEmitted && Vector3.Distance(oldWrist,wrist)>.002f)
            { SensorEvents.Add(new SensorAction{time=t,type="movement"}); movementEmitted=true; }
        }

        public ActionCue Due(float t, ActionLeg leg) =>
            leg.started && !leg.complete && t + .0001f >= Cues[leg.index].time ? Cues[leg.index] : null;

        public bool TryCommit(ActionCue cue,float time,Vector3 body,Vector3 wrist,out TaskState state,out string reason)
        {
            reason=""; state=TaskState.Blocked;
            if (Vector3.Distance(body,Stands[CompletedActions])>.025f ||
                Vector3.Distance(wrist,cue.contact)>.08f)
            { reason="Cannot execute action before arrival and reachable contact: "+cue.scenario; return false; }
            if (cue.type=="pickup")
            {
                if (HeldBottle!=null || bottleStates[cue.bottle]==BottleState.Disposed ||
                    Vector3.Distance(bottlePositions[cue.bottle]+Vector3.up*.065f,cue.contact)>.025f)
                { reason="Pickup violates bottle ownership/location state"; return false; }
                HeldBottle=cue.bottle; bottleStates[cue.bottle]=BottleState.Held;
                movementEmitted=false; state=TaskState.PickingUp;
            }
            else
            {
                if (HeldBottle!=cue.bottle) { reason="Cannot release a bottle that is not held"; return false; }
                Vector3 center=cue.contact-Vector3.up*.065f;
                if(cue.region!="disposal" && (!Physics.Raycast(center,Vector3.down,out var support,.13f,CollisionWorld.SolidMask) || support.distance<.085f))
                { reason="Placement has no valid supporting surface"; return false; }
                bottlePositions[cue.bottle]=center;
                bottleStates[cue.bottle]=cue.region=="disposal"?BottleState.Disposed:cue.region=="counter"?BottleState.AtCounter:cue.region==room.Bottle(cue.bottle).home?BottleState.OnShelf:BottleState.Misplaced;
                if(cue.region=="disposal") disposalTimes[cue.bottle]=time;
                HeldBottle=null; state=cue.region=="disposal"?TaskState.Disposing:TaskState.Placing;
            }
            SensorEvents.Add(new SensorAction{time=time,type=cue.type}); CompletedActions++;
            return true;
        }

        /// <summary>Held bottles follow the wrist; disposed bottles fall inside the hollow bin.</summary>
        public Vector3 BottlePosition(BottlePlacement bottle,float time,Vector3 wrist)
        {
            string id=bottle.id;
            Vector3 position=bottleStates[id]==BottleState.Held?wrist-Vector3.up*.065f:bottlePositions[id];
            if(bottleStates[id]==BottleState.Disposed)
            {
                float elapsed=time-disposalTimes[id];
                // Gravity inside a genuinely hollow bin, stopping on its bottom.
                position.y=Mathf.Max(room.binBottomY,position.y-4.905f*elapsed*elapsed);
                position.x+=bottle.binOffsetX;
            }
            return position;
        }

        static float Ease(float u) { u=Mathf.Clamp01(u); return u*u*u*(u*(u*6-15)+10); }
    }
}
