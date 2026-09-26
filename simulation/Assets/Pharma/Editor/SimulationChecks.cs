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
            float offset=PharmacySimulation.WorkflowOffset,scale=PharmacySimulation.WorkflowTimeScale;
            Require(sim.Cues.Count==10,"Expected ten pickup/release actions");
            Require(sim.regions.Length==8 && sim.regions.Select(r=>r.id).Distinct().Count()==8,"Eight unique regions required");
            float maxFootError=0,maxStanceDrift=0,minFootY=10,worstFootTime=0; int hiddenFrames=0;
            foreach(bool occluded in new[]{false,true})
            {
                sim.Evaluate(0); sim.ambiguousReturn=occluded;
                Vector3 last=sim.technician.position;
                Vector3 lastRight=sim.RightFoot.position,lastLeft=sim.LeftFoot.position;
                int lastSwing=sim.SwingFoot; Quaternion lastRotation=sim.technician.rotation;
                for(int frame=0;frame<=PharmacySimulation.Duration*30;frame++)
                {
                    sim.Evaluate(frame/30f);
                    Require(sim.State!=PharmacySimulation.TaskState.Blocked,$"Blocked at {frame/30f}: {sim.BlockedReason}");
                    Require(sim.World.CanMove(last,sim.technician.position,out var obstacle),"Body tunnels through "+obstacle);
                    Require(Vector3.Distance(last,sim.technician.position)*30<1.81f,"Walking speed exceeds limit");
                    float error=Mathf.Max(Vector3.Distance(sim.RightFoot.position,sim.RightFootTarget),Vector3.Distance(sim.LeftFoot.position,sim.LeftFootTarget));
                    if(error>maxFootError) { maxFootError=error;worstFootTime=frame/30f; }
                    minFootY=Mathf.Min(minFootY,sim.RightFoot.position.y,sim.LeftFoot.position.y);
                    if(lastSwing==sim.SwingFoot && lastSwing>=0)
                        maxStanceDrift=Mathf.Max(maxStanceDrift,lastSwing==0?Vector3.Distance(lastLeft,sim.LeftFoot.position):Vector3.Distance(lastRight,sim.RightFoot.position));
                    Require(Quaternion.Angle(lastRotation,sim.technician.rotation)<=6.01f,"Abrupt turn exceeds 180 degrees/second");
                    var rig=SimulationSkeleton.Capture(sim,frame,1920,1080);
                    Require(rig.joints_pixels.Length==16 && rig.joints_pixels.All(p=>p.z>0),"Simulation skeleton must retain every joint");
                    if(rig.visible_to_camera.Any(v=>!v)) hiddenFrames++;
                    last=sim.technician.position; lastRight=sim.RightFoot.position;lastLeft=sim.LeftFoot.position;
                    lastSwing=sim.SwingFoot;lastRotation=sim.technician.rotation;
                }
                Require(sim.State==PharmacySimulation.TaskState.Complete && sim.CompletedActions==10,"Sequence did not complete");
                Require(sim.SensorEvents.Count(e=>e.type=="pickup")==5 && sim.SensorEvents.Count(e=>e.type=="release")==5,"Accepted event count mismatch");
                Require(sim.GetBottleState("bottle-a1")==PharmacySimulation.BottleState.Disposed && sim.GetBottleState("bottle-a2")==PharmacySimulation.BottleState.Disposed,"Both bottles must be disposed");
                Require(Mathf.Abs(sim.bottleOne.position.y-.21f)<.001f,"Bottle must stop at bin bottom");
            }
            sim.ambiguousReturn=false;
            sim.Evaluate(offset+14*scale); Vector3 bottle=sim.bottleOne.position,wrist=sim.RightWrist.position;
            Require(sim.GetBottleState("bottle-a1")==PharmacySimulation.BottleState.Misplaced,"Wrong shelf must remain a distinct state");
            sim.Evaluate(offset+41*scale); sim.Evaluate(offset+2*scale); sim.Evaluate(offset+14*scale);
            Require(Vector3.Distance(bottle,sim.bottleOne.position)<.0001f && Vector3.Distance(wrist,sim.RightWrist.position)<.0001f,"Seek must reproduce identical state and pose");
            sim.Evaluate(offset+7.6f*scale);
            Require(sim.State==PharmacySimulation.TaskState.AtCounter,"Counter rest must not create a spurious walking loop");
            Require(sim.HeldBottle==null && sim.GetBottleState("bottle-a1")==PharmacySimulation.BottleState.AtCounter,"Counter must retain ownership/location without a held bottle");
            sim.Evaluate(offset+30*scale);
            Require(sim.GetBottleState("bottle-a1")==PharmacySimulation.BottleState.Disposed && sim.GetBottleState("bottle-a2")==PharmacySimulation.BottleState.OnShelf,"First disposal must preserve second bottle");

            // Change the world AFTER path planning. Swept guards must still stop safely.
            sim.Evaluate(offset+11*scale); Vector3 obstruction=sim.technician.position+Vector3.up;
            sim.Evaluate(0);
            var wall=GameObject.CreatePrimitive(PrimitiveType.Cube);
            wall.name="Injected aisle blocker"; wall.layer=CollisionWorld.SolidLayer;
            wall.transform.position=obstruction; wall.transform.localScale=new Vector3(.7f,2,.7f);
            Physics.SyncTransforms(); sim.Evaluate(offset+15*scale);
            Require(sim.State==PharmacySimulation.TaskState.Blocked,"Dynamic obstacle must block action");
            Require(!sim.SensorEvents.Any(e=>e.type=="release" && e.time>=offset+14*scale),"Blocked character emitted an impossible release");
            Require(sim.World.CanStand(sim.technician.position,out _),"Blocked character must remain outside obstacle");
            UnityEngine.Object.DestroyImmediate(wall); Physics.SyncTransforms();

            sim.Evaluate(0);
            string original=sim.Cues[0].type; sim.Cues[0].type="release";
            sim.Evaluate(offset+2*scale);
            Require(sim.State==PharmacySimulation.TaskState.Blocked && sim.SensorEvents.Count==0,"Cannot release without owning a bottle");
            sim.Cues[0].type=original; sim.Evaluate(0);
            original=sim.Cues[1].type; sim.Cues[1].type="pickup"; sim.Evaluate(offset+7*scale);
            Require(sim.State==PharmacySimulation.TaskState.Blocked && sim.CompletedActions==1,"Cannot pick up a second bottle while holding one");
            sim.Cues[1].type=original; sim.Evaluate(0);
            original=sim.Cues[8].bottle; sim.Cues[8].bottle="bottle-a1"; sim.Evaluate(offset+35*scale);
            Require(sim.State==PharmacySimulation.TaskState.Blocked && sim.CompletedActions==8,"Cannot pick up a disposed bottle");
            sim.Cues[8].bottle=original; sim.Evaluate(0); sim.Evaluate(offset+44*scale);
            Require(sim.CompletedActions==10,"Restart must recover a valid scenario");
            bool unreachable=false;
            try { sim.World.Route(sim.technician.position,new Vector3(0,0,7.45f)); }
            catch(InvalidOperationException) { unreachable=true; }
            Require(unreachable,"Cannot route into a wall");
            sim.Restart();
            Require(sim.CompletedActions==0 && sim.SensorEvents.Count==0 && sim.HeldBottle==null,"Restart clears accepted events and ownership");
            sim.Restart();
            Require(sim.State==PharmacySimulation.TaskState.Idle,"Restart works even at time zero");
            Debug.Log($"PHARMA_ANIMATION_METRICS maxFootError={maxFootError} maxStanceDrift={maxStanceDrift} minFootY={minFootY} hiddenFrames={hiddenFrames} worstFootTime={worstFootTime}");
            Require(maxFootError<.01f,"Foot IK must reach the planted/swing target within 1 cm");
            Require(maxStanceDrift<.003f,"Planted support foot must not slide");
            Require(minFootY>.09f,"Ankle must remain above floor");
            Require(hiddenFrames>0,"X-ray test must exercise occluded joints");

            Debug.Log("PHARMA_STATE_COLLISION_CHECKS_PASSED: 6362 frame samples, blocked route, invalid ownership, and deterministic replay");
        }
        static void Require(bool condition,string message)
        { if(!condition) throw new InvalidOperationException(message); }
    }
}
