using System;
using System.Collections.Generic;
using UnityEngine;

namespace Pharma.Simulation
{
    /// <summary>M8: the same rig and collision guards, driven by a recording's floor track and
    /// the dashboard's pickup/put-down decisions instead of the scripted demo.</summary>
    public partial class PharmacySimulation
    {
        ReenactmentActions reenactment;
        ReenactmentPlayer player;
        public ReenactmentActions Reenactment => reenactment;
        public bool TechnicianVisible { get; private set; } = true;
        float TickRate => reenactment == null ? SimulationFps : reenactmentFps;
        float EndTime => reenactment == null ? Duration : (player.FrameCount - 1) / reenactmentFps;
        float reenactmentFps;

        /// <summary>Switch this simulation to re-enact a plan in a scene built from its room.</summary>
        public void InitializeReenactment(ReenactmentPlan plan, RoomDescription room, Transform[] bottles,
                                          Dictionary<string, GameObject> highlights)
        {
            if (!technician || !roomCamera) throw new InvalidOperationException("Re-enactment scene has no technician or camera");
            if (plan.fps <= 0 || plan.frame_count <= 0) throw new InvalidOperationException("Re-enactment plan has no frames");
            InitializeRig();
            Room = room; Actions = null;
            World = GetComponent<CollisionWorld>();
            if (!World) World = gameObject.AddComponent<CollisionWorld>();
            try { World.Build(room); }
            catch (InvalidOperationException e) { Debug.LogWarning("PHARMA_REENACT_NAVIGATION " + e.Message); }
            reenactmentFps = plan.fps;
            player = new ReenactmentPlayer(plan, room.floorY);
            Motion = player;
            reenactment = new ReenactmentActions(plan, bottles, highlights, roomCamera);
            ResetReenactment();
        }

        void ResetReenactment()
        {
            currentFrame = -1;
            footGait=null; animationTime=0; traveledDistance=0; animationVelocity=Vector3.zero;
            State = TaskState.Idle; BlockedReason = "";
            reenactment.Reset();
            var start = Motion.Start;
            technician.position = start.position; technician.rotation = start.facing;
            TechnicianVisible = !start.hidden;
            Pose(start.position, start.facing, null, 0);
        }

        void StepReenactment(float t)
        {
            animationTime=t;
            Physics.SyncTransforms();
            int f = player.Frame(t);
            var body = Motion.Sample(t, default);
            bool posed = false, handPosed = false;
            Vector3? target = null;
            if (body.hidden) TechnicianVisible = false;
            else
            {
                bool first = !TechnicianVisible;
                TechnicianVisible = true;
                var rest = body.position + body.facing * RestHand();
                target = reenactment.HandTarget(f, rest, out var reach);
                bool cut = body.cut || first;
                // The guard, not the plan, has the last word: try the planned pose, then a cut
                // (never a sweep through furniture), then the body without the reach.
                string obstacle;
                posed = TryPose(body.position, body.facing, target, 0, cut, out obstacle) ||
                        (!cut && TryPose(body.position, body.facing, target, 0, true, out obstacle));
                handPosed = posed && target.HasValue;
                reenactment.ReachRefusal = target.HasValue && !handPosed ? obstacle : null;
                if (!posed && target.HasValue)
                    posed = TryPose(body.position, body.facing, null, 0, cut, out obstacle) ||
                            TryPose(body.position, body.facing, null, 0, true, out obstacle);
                if (!posed) { reenactment.GuardHolds++; if (reenactment.Notes.Count < 50) reenactment.Notes.Add($"frame {f}: {obstacle}"); }
                State = reach != TaskState.Idle && handPosed ? reach : body.walking ? (HeldBottle == null ? TaskState.Walking : TaskState.Carrying) : TaskState.Idle;
            }
            reenactment.AfterPose(f, RightWrist.position, handPosed);
        }
    }
}
