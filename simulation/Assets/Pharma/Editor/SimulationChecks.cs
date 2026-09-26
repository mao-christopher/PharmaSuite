using System;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace Pharma.Simulation.Editor
{
    public static class SimulationChecks
    {
        [MenuItem("Pharma/3. Verify deterministic simulation")]
        public static void Run()
        {
            EditorSceneManager.OpenScene(PharmacySceneBuilder.ScenePath);
            var sim=UnityEngine.Object.FindFirstObjectByType<PharmacySimulation>();
            sim.Initialize();
            Require(sim.Cues.Count==10,"Expected ten pickup/release actions");
            Require(sim.regions.Length==8,"Expected six shelves, one counter, one disposal region");
            Require(sim.regions.Select(r=>r.id).Distinct().Count()==8,"Region IDs must be unique");
            bool held=false;
            foreach(var cue in sim.Cues)
            {
                Require(cue.time*30==Mathf.Round(cue.time*30),"Cue must align with media frame");
                Require(cue.type=="pickup"?!held:held,"Single-bottle pickup/release order");
                held=cue.type=="pickup";
                sim.Evaluate(cue.time);
                Require(Vector3.Distance(sim.RightWrist.position,cue.contact)<.08f,"Reach cannot reach contact: "+cue.scenario+" distance="+Vector3.Distance(sim.RightWrist.position,cue.contact)+" wrist="+sim.RightWrist.position+" contact="+cue.contact);
            }
            sim.Evaluate(12); Vector3 position=sim.bottleOne.position, wrist=sim.RightWrist.position;
            sim.Evaluate(35); sim.Evaluate(2); sim.Evaluate(12);
            Require(Vector3.Distance(position,sim.bottleOne.position)<.0001f,"Seek changed bottle position");
            Require(Vector3.Distance(wrist,sim.RightWrist.position)<.0001f,"Seek changed skeleton pose");
            sim.Evaluate(6.5f);
            Require(Vector3.Distance(sim.bottleOne.position,sim.Cues[1].contact-Vector3.up*.065f)<.0001f,"Counter must retain bottle");
            sim.Evaluate(27);
            Require(!sim.bottleOne.gameObject.activeSelf && sim.bottleTwo.gameObject.activeSelf,"First disposal must remove only one bottle");
            sim.Evaluate(36);
            Require(!sim.bottleOne.gameObject.activeSelf && !sim.bottleTwo.gameObject.activeSelf,"Last disposal must remove remaining bottle");
            sim.Evaluate(0);
            Require(sim.bottleOne.gameObject.activeSelf && sim.bottleTwo.gameObject.activeSelf,"Restart must restore both bottles");
            sim.ambiguousReturn=true; sim.Evaluate(18);
            Require(sim.occluder.gameObject.activeSelf,"Ambiguous variant must activate occluder");
            sim.Evaluate(20);
            Require(!sim.occluder.gameObject.activeSelf,"Occluder must leave after return");
            Debug.Log("PHARMA_SIMULATION_CHECKS_PASSED");
        }
        static void Require(bool condition,string message)
        { if(!condition) throw new InvalidOperationException(message); }
    }
}
