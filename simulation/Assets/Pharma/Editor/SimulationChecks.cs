using System;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace Pharma.Simulation.Editor
{
    public static class SimulationChecks
    {
        [MenuItem("Pharma/3. Verify state, collision, and deterministic playback")]
        public static void Run()
        {
            EditorSceneManager.OpenScene(PharmacySceneBuilder.ScenePath);
            var sim=UnityEngine.Object.FindFirstObjectByType<PharmacySimulation>();
            sim.Initialize();
            float offset=PharmacySimulation.WorkflowOffset;
            Require(sim.Cues.Count==10,"Expected ten pickup/release actions");
            Require(sim.regions.Length==8 && sim.regions.Select(r=>r.id).Distinct().Count()==8,"Eight unique regions required");
            foreach(bool occluded in new[]{false,true})
            {
                sim.Evaluate(0); sim.ambiguousReturn=occluded;
                Vector3 last=sim.technician.position;
                for(int frame=0;frame<=PharmacySimulation.Duration*30;frame++)
                {
                    sim.Evaluate(frame/30f);
                    Require(sim.State!=PharmacySimulation.TaskState.Blocked,$"Blocked at {frame/30f}: {sim.BlockedReason}");
                    Require(sim.World.CanMove(last,sim.technician.position,out var obstacle),"Body tunnels through "+obstacle);
                    Require(Vector3.Distance(last,sim.technician.position)*30<1.81f,"Walking speed exceeds limit");
                    last=sim.technician.position;
                }
                Require(sim.State==PharmacySimulation.TaskState.Complete && sim.CompletedActions==10,"Sequence did not complete");
                Require(sim.SensorEvents.Count(e=>e.type=="pickup")==5 && sim.SensorEvents.Count(e=>e.type=="release")==5,"Accepted event count mismatch");
                Require(sim.GetBottleState("bottle-a1")==PharmacySimulation.BottleState.Disposed && sim.GetBottleState("bottle-a2")==PharmacySimulation.BottleState.Disposed,"Both bottles must be disposed");
                Require(Mathf.Abs(sim.bottleOne.position.y-.21f)<.001f,"Bottle must stop at bin bottom");
            }
            sim.ambiguousReturn=false;
            sim.Evaluate(offset+14); Vector3 bottle=sim.bottleOne.position,wrist=sim.RightWrist.position;
            Require(sim.GetBottleState("bottle-a1")==PharmacySimulation.BottleState.Misplaced,"Wrong shelf must remain a distinct state");
            sim.Evaluate(offset+41); sim.Evaluate(offset+2); sim.Evaluate(offset+14);
            Require(Vector3.Distance(bottle,sim.bottleOne.position)<.0001f && Vector3.Distance(wrist,sim.RightWrist.position)<.0001f,"Seek must reproduce identical state and pose");
            sim.Evaluate(offset+7.6f);
            Require(sim.State==PharmacySimulation.TaskState.AtCounter,"Counter rest must not create a spurious walking loop");
            Require(sim.HeldBottle==null && sim.GetBottleState("bottle-a1")==PharmacySimulation.BottleState.AtCounter,"Counter must retain ownership/location without a held bottle");
            sim.Evaluate(offset+30);
            Require(sim.GetBottleState("bottle-a1")==PharmacySimulation.BottleState.Disposed && sim.GetBottleState("bottle-a2")==PharmacySimulation.BottleState.OnShelf,"First disposal must preserve second bottle");

            // Change the world AFTER path planning. Swept guards must still stop safely.
            sim.Evaluate(offset+11); Vector3 obstruction=sim.technician.position+Vector3.up;
            sim.Evaluate(0);
            var wall=GameObject.CreatePrimitive(PrimitiveType.Cube);
            wall.name="Injected aisle blocker"; wall.layer=CollisionWorld.SolidLayer;
            wall.transform.position=obstruction; wall.transform.localScale=new Vector3(.7f,2,.7f);
            Physics.SyncTransforms(); sim.Evaluate(offset+15);
            Require(sim.State==PharmacySimulation.TaskState.Blocked,"Dynamic obstacle must block action");
            Require(!sim.SensorEvents.Any(e=>e.type=="release" && e.time>=offset+14),"Blocked character emitted an impossible release");
            Require(sim.World.CanStand(sim.technician.position,out _),"Blocked character must remain outside obstacle");
            UnityEngine.Object.DestroyImmediate(wall); Physics.SyncTransforms();

            sim.Evaluate(0);
            string original=sim.Cues[0].type; sim.Cues[0].type="release";
            sim.Evaluate(offset+2);
            Require(sim.State==PharmacySimulation.TaskState.Blocked && sim.SensorEvents.Count==0,"Cannot release without owning a bottle");
            sim.Cues[0].type=original; sim.Evaluate(0);
            original=sim.Cues[1].type; sim.Cues[1].type="pickup"; sim.Evaluate(offset+7);
            Require(sim.State==PharmacySimulation.TaskState.Blocked && sim.CompletedActions==1,"Cannot pick up a second bottle while holding one");
            sim.Cues[1].type=original; sim.Evaluate(0);
            original=sim.Cues[8].bottle; sim.Cues[8].bottle="bottle-a1"; sim.Evaluate(offset+35);
            Require(sim.State==PharmacySimulation.TaskState.Blocked && sim.CompletedActions==8,"Cannot pick up a disposed bottle");
            sim.Cues[8].bottle=original; sim.Evaluate(0); sim.Evaluate(offset+44);
            Require(sim.CompletedActions==10,"Restart must recover a valid scenario");
            bool unreachable=false;
            try { sim.World.Route(sim.technician.position,new Vector3(0,0,7.45f)); }
            catch(InvalidOperationException) { unreachable=true; }
            Require(unreachable,"Cannot route into a wall");
            sim.Restart();
            Require(sim.CompletedActions==0 && sim.SensorEvents.Count==0 && sim.HeldBottle==null,"Restart clears accepted events and ownership");
            sim.Restart();
            Require(sim.State==PharmacySimulation.TaskState.Idle,"Restart works even at time zero");
            Debug.Log("PHARMA_STATE_COLLISION_CHECKS_PASSED: 5042 frame samples, blocked route, invalid ownership, and deterministic replay");
        }
        static void Require(bool condition,string message)
        { if(!condition) throw new InvalidOperationException(message); }
    }
}
