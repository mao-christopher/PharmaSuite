using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
namespace Pharma.Simulation.Editor {
public static class DemoBridgeExporter {
 public static void Export() {
  EditorSceneManager.OpenScene(PharmacySceneBuilder.ScenePath);
  var sim=UnityEngine.Object.FindFirstObjectByType<PharmacySimulation>();sim.Initialize();sim.showSimulationSkeleton=false;
  string output=Environment.GetEnvironmentVariable("PHARMA_BRIDGE_OUT");Directory.CreateDirectory(output);
  var cams=new List<Camera>{sim.roomCamera};
  for(int i=0;i<2;i++){var c=UnityEngine.Object.Instantiate(sim.roomCamera);c.transform.position=new Vector3(0,2.95f,i==0?2.12f:4.67f);c.transform.LookAt(new Vector3(0,1.2f,i==0?3.65f:6.2f));c.fieldOfView=74;cams.Add(c);}
  var rt=new RenderTexture(1920,1080,24);rt.antiAliasing=2;rt.Create();var tex=new Texture2D(1920,1080,TextureFormat.RGB24,false);
  var skins=sim.technician.GetComponentsInChildren<SkinnedMeshRenderer>();var meshes=new List<Mesh>();
  foreach(var skin in skins){var g=new GameObject("Bridge skin");g.transform.SetParent(skin.transform,false);var m=new Mesh();g.AddComponent<MeshFilter>().sharedMesh=m;g.AddComponent<MeshRenderer>().sharedMaterials=skin.sharedMaterials;meshes.Add(m);skin.enabled=false;}
  foreach(var c in cams){c.targetTexture=rt;c.aspect=16f/9f;}
  for(int c=0;c<3;c++)Directory.CreateDirectory(Path.Combine(output,"cam"+c));
  for(int i=0;i<330;i++){
   float time=1f+i/30f*2.45f;sim.Evaluate(time);
   if(sim.State==PharmacySimulation.TaskState.Blocked)throw new Exception(sim.BlockedReason);
   for(int k=0;k<skins.Length;k++)skins[k].BakeMesh(meshes[k]);
   for(int j=0;j<3;j++){cams[j].Render();RenderTexture.active=rt;tex.ReadPixels(new Rect(0,0,1920,1080),0,0);tex.Apply();File.WriteAllBytes(Path.Combine(output,"cam"+j,i.ToString("D6")+".jpg"),tex.EncodeToJPG(93));}
   if(i%30==0)Debug.Log("BRIDGE_FRAME "+i);
  }
  File.WriteAllText(Path.Combine(output,"complete.txt"),"330 frames per camera; collision-aware existing walk; 30fps; simulation times 1..27.87; editorial camera cuts");
 }
}}
