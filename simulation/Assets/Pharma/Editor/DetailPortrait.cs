using System;
using System.IO;
using UnityEditor.SceneManagement;
using UnityEngine;
namespace Pharma.Simulation.Editor
{
    // Editorial still only: does not change the recording camera or calibration.
    public static class DetailPortrait
    {
        public static void BuildAndExport() { PharmacySceneBuilder.Build(); Export(); }
        public static void Export()
        {
            EditorSceneManager.OpenScene(PharmacySceneBuilder.ScenePath);
            var sim=UnityEngine.Object.FindFirstObjectByType<PharmacySimulation>(); sim.Initialize(); sim.Evaluate(0);
            foreach(var skin in sim.technician.GetComponentsInChildren<SkinnedMeshRenderer>())
            {
                var p=new GameObject("Portrait skin");p.transform.SetParent(skin.transform,false);
                var mesh=new Mesh();skin.BakeMesh(mesh);p.AddComponent<MeshFilter>().sharedMesh=mesh;p.AddComponent<MeshRenderer>().sharedMaterials=skin.sharedMaterials;skin.enabled=false;
            }
            var camera=sim.roomCamera;var actor=sim.technician;
            camera.transform.position=actor.position+actor.forward*2.5f+actor.right*.65f+Vector3.up*1.5f;
            camera.transform.LookAt(actor.position+Vector3.up*1.27f);camera.fieldOfView=29;camera.aspect=1;
            var rt=new RenderTexture(1400,1400,24);rt.antiAliasing=4;rt.Create();camera.targetTexture=rt;camera.Render();RenderTexture.active=rt;
            var texture=new Texture2D(1400,1400,TextureFormat.RGB24,false);texture.ReadPixels(new Rect(0,0,1400,1400),0,0);texture.Apply();
            var args=Environment.GetCommandLineArgs();int i=Array.IndexOf(args,"-pharmaPortrait");if(i<0)throw new ArgumentException("Provide -pharmaPortrait output.png");
            File.WriteAllBytes(args[i+1],texture.EncodeToPNG());Debug.Log("PHARMA_PORTRAIT_READY");
        }
    }
}
