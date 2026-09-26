using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace Pharma.Simulation.Editor
{
    public static class SimulationExporter
    {
        [Serializable] class SensorEvent
        {
            public int schema_version=1;
            public string event_id, session_id, event_type, sensor_id="mock-imu-01";
            public double media_time_ms;
        }
        [Serializable] class TruthEvent
        {
            public string event_id, region_id, bottle_id, scenario;
            public double media_time_ms;
            public Vector3 right_wrist_pixels;
            public bool intentionally_occluded;
        }
        [Serializable] class StateFrame
        {
            public int frame, completed_actions;
            public double media_time_ms;
            public string technician_state, held_bottle, bottle_one_state, bottle_two_state;
            public Vector3 body_position;
        }
        [Serializable] class RegionData
        {
            public string region_id, kind, medication_id;
            public float x_min,y_min,x_max,y_max;
        }
        [Serializable] class Calibration
        {
            public int schema_version=1, width,height;
            public string camera_id="room-camera-01", calibration_version="pharmacy-v3", coordinates="normalized_top_left";
            public RegionData[] regions;
        }
        [Serializable] class Capture
        {
            public int schema_version=1, width,height,fps,frame_count;
            public float workflow_offset_ms=PharmacySimulation.WorkflowOffset*1000, workflow_time_scale=PharmacySimulation.WorkflowTimeScale;
            public string session_id, camera_id="room-camera-01", calibration_version="pharmacy-v3";
            public string frames="frames/%06d.png", imu_events="imu_events.jsonl", calibration="calibration.json";
            public string initial_inventory="initial_inventory.json", business_events="business_events.jsonl";
            public string coordinate_system="top_left_pixels";
        }
        [MenuItem("Pharma/2. Export recording (106 seconds, 30 FPS)")]
        public static void ExportFromMenu() { Export(); }
        public static void Export()
        {
            if(!File.Exists(PharmacySceneBuilder.ScenePath)) PharmacySceneBuilder.Build();
            EditorSceneManager.OpenScene(PharmacySceneBuilder.ScenePath);
            var sim=UnityEngine.Object.FindFirstObjectByType<PharmacySimulation>();
            sim.ambiguousReturn=Has("-pharmaAmbiguous"); sim.Initialize();
            string output=Argument("-pharmaOutput",Path.GetFullPath("Exports/"+DateTime.UtcNow.ToString("yyyyMMdd-HHmmss")));
            bool preview=Has("-pharmaPreview");
            int fps=int.Parse(Argument("-pharmaFps","30"));
            int width=int.Parse(Argument("-pharmaWidth","1920"));
            int height=int.Parse(Argument("-pharmaHeight","1080"));
            if(fps!=PharmacySimulation.SimulationFps || width<64 || height<64) throw new ArgumentException("Invalid capture settings");
            if(Directory.Exists(output) && Directory.EnumerateFileSystemEntries(output).Any())
                throw new IOException("Output directory must be new or empty: "+output);
            Directory.CreateDirectory(output); Directory.CreateDirectory(Path.Combine(output,"frames"));
            Directory.CreateDirectory(Path.Combine(output,"evaluator_only"));
            string session=Path.GetFileName(output)+"-"+Guid.NewGuid().ToString("N").Substring(0,12);
            var camera=sim.roomCamera;
            var rt=new RenderTexture(width,height,24,RenderTextureFormat.ARGB32);
            rt.antiAliasing=4; rt.Create(); camera.targetTexture=rt; camera.aspect=(float)width/height;
            var texture=new Texture2D(width,height,TextureFormat.RGB24,false);
            // Explicit CPU skinning ensures offline Camera.Render sees this sample's bone pose,
            // without relying on an Editor/player-loop skinning update between samples.
            var skins=sim.technician.GetComponentsInChildren<SkinnedMeshRenderer>();
            var bakedMeshes=new List<Mesh>();
            var proxies=new List<GameObject>();
            foreach(var skin in skins)
            {
                var proxy=new GameObject("Offline skinned mesh");
                proxy.transform.SetParent(skin.transform,false);
                var mesh=new Mesh(); proxy.AddComponent<MeshFilter>().sharedMesh=mesh;
                proxy.AddComponent<MeshRenderer>().sharedMaterials=skin.sharedMaterials;
                bakedMeshes.Add(mesh); proxies.Add(proxy); skin.enabled=false;
            }
            Action bake=()=> { for(int k=0;k<skins.Length;k++) skins[k].BakeMesh(bakedMeshes[k]); };
            try
            {
                // Warm up the skinning/render pipeline before the first saved frame.
                sim.Evaluate(0); bake(); camera.Render();
                float[] times=preview?new[]{0f,8f,16f,24f,32f}.Concat(sim.Cues.Select(c=>c.time)).ToArray():Enumerable.Range(0,(int)(PharmacySimulation.Duration*fps)).Select(i=>(float)i/fps).ToArray();
                File.WriteAllText(Path.Combine(output,"evaluator_only","rig_definition.json"),JsonUtility.ToJson(
                    new SimulationSkeleton.Definition{width=width,height=height,fps=fps,frame_count=times.Length},true));
                using var rigLog=new StreamWriter(Path.Combine(output,"evaluator_only","rig_skeleton.jsonl"));
                using var stateLog = new StreamWriter(Path.Combine(output,"evaluator_only","simulation_states.jsonl"));
                for(int i=0;i<times.Length;i++)
                {
                    sim.Evaluate(times[i]);
                    if(sim.State==PharmacySimulation.TaskState.Blocked)
                        throw new InvalidOperationException("Export stopped at "+sim.MediaTime+"s: "+sim.BlockedReason);
                    stateLog.WriteLine(JsonUtility.ToJson(new StateFrame{
                        frame=(int)Math.Round(times[i]*fps), media_time_ms=times[i]*1000,
                        technician_state=sim.State.ToString(), held_bottle=sim.HeldBottle,
                        completed_actions=sim.CompletedActions, body_position=sim.technician.position,
                        bottle_one_state=sim.GetBottleState("bottle-a1").ToString(),
                        bottle_two_state=sim.GetBottleState("bottle-a2").ToString()}));
                    rigLog.WriteLine(JsonUtility.ToJson(SimulationSkeleton.Capture(sim,(int)Math.Round(times[i]*fps),width,height)));
                    bake(); camera.Render();
                    RenderTexture.active=rt;
                    texture.ReadPixels(new Rect(0,0,width,height),0,0); texture.Apply();
                    File.WriteAllBytes(Path.Combine(output,"frames",i.ToString("D6")+".png"),texture.EncodeToPNG());
                    if(i%120==0) Debug.Log($"PHARMA_RENDER {i}/{times.Length}");
                }
                if(preview)
                {
                    File.WriteAllText(Path.Combine(output,"preview_times.json"),"["+string.Join(",",times.Select(t=>t.ToString(System.Globalization.CultureInfo.InvariantCulture)))+"]");
                }
                else
                {
                    var capture=new Capture{session_id=session,width=width,height=height,fps=fps,frame_count=times.Length};
                    File.WriteAllText(Path.Combine(output,"capture.json"),JsonUtility.ToJson(capture,true));
                }
                WriteCalibration(sim,width,height,output);
                if(sim.CompletedActions != sim.Cues.Count)
                    throw new InvalidOperationException("Export did not complete every action");
                var actualEvents=sim.SensorEvents.ToArray();
                var sensors=new List<string>(); var truth=new List<string>();
                int eventIndex=0;
                foreach(var action in actualEvents)
                {
                    string id=session+"-"+(eventIndex++).ToString("D4");
                    sensors.Add(JsonUtility.ToJson(new SensorEvent{event_id=id,session_id=session,event_type=action.type,media_time_ms=action.time*1000}));
                    if(action.type=="movement") continue;
                    var cue=sim.Cues.First(c=>Mathf.Abs(c.time-action.time)<.01f);
                    sim.Evaluate(action.time); Vector3 wrist=camera.WorldToViewportPoint(sim.RightWrist.position);
                    truth.Add(JsonUtility.ToJson(new TruthEvent{event_id=id,region_id=cue.region,bottle_id=cue.bottle,scenario=cue.scenario,media_time_ms=action.time*1000,right_wrist_pixels=new Vector3(wrist.x*width,(1-wrist.y)*height,wrist.z),intentionally_occluded=sim.ambiguousReturn && cue.time==PharmacySimulation.WorkflowOffset+20*PharmacySimulation.WorkflowTimeScale}));
                }
                File.WriteAllLines(Path.Combine(output,"imu_events.jsonl"),sensors);
                File.WriteAllLines(Path.Combine(output,"evaluator_only","ground_truth.jsonl"),truth);
                Debug.Log("PHARMA_EXPORT_READY "+output);
            }
            finally
            {
                camera.targetTexture=null; RenderTexture.active=null; rt.Release();
                UnityEngine.Object.DestroyImmediate(rt); UnityEngine.Object.DestroyImmediate(texture);
                for(int k=0;k<skins.Length;k++)
                { skins[k].enabled=true; UnityEngine.Object.DestroyImmediate(proxies[k]); UnityEngine.Object.DestroyImmediate(bakedMeshes[k]); }
                sim.Evaluate(0);
            }
        }
        static void WriteCalibration(PharmacySimulation sim,int width,int height,string output)
        {
            var data=new List<RegionData>();
            foreach(var region in sim.regions)
            {
                float xmin=1,ymin=1,xmax=0,ymax=0;
                for(int i=0;i<8;i++)
                {
                    Vector3 offset=Vector3.Scale(region.size*.5f,new Vector3((i&1)==0?-1:1,(i&2)==0?-1:1,(i&4)==0?-1:1));
                    Vector3 p=sim.roomCamera.WorldToViewportPoint(region.center+offset);
                    if(p.z<=0) throw new InvalidOperationException("Region behind camera");
                    xmin=Mathf.Min(xmin,p.x); xmax=Mathf.Max(xmax,p.x);
                    ymin=Mathf.Min(ymin,1-p.y); ymax=Mathf.Max(ymax,1-p.y);
                }
                data.Add(new RegionData{region_id=region.id,kind=region.kind,medication_id=region.medication,x_min=xmin,y_min=ymin,x_max=xmax,y_max=ymax});
            }
            File.WriteAllText(Path.Combine(output,"calibration.json"),JsonUtility.ToJson(new Calibration{width=width,height=height,regions=data.ToArray()},true));
        }
        static bool Has(string key) => Environment.GetCommandLineArgs().Contains(key);
        static string Argument(string key,string fallback)
        {
            string[] args=Environment.GetCommandLineArgs(); int index=Array.IndexOf(args,key);
            return index>=0 && index+1<args.Length?args[index+1]:fallback;
        }
    }
}
