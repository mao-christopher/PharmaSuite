using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEngine;

namespace Pharma.Simulation.Editor
{
    /// <summary>Batch entry for `render.py --timeline`: builds the M7 scene, plays M8 and writes
    /// one PNG per timeline frame plus unity_report.json. Frames carry no labels.</summary>
    public static class ReenactmentExporter
    {
        [Serializable] class AlignmentPixel { public string region_id; public float[] unity_pixel, registration_pixel; public float depth; }
        [Serializable] class Report
        {
            public string unity_version, recording;
            public int[] preview_frames;
            public int frames, technician_hidden_frames, colliders, labels, bottles;
            public int shown, highlight_only, followed, pending, downgraded, guard_holds;
            public AlignmentPixel[] alignment;
            public string[] notes;
        }

        public static void Export()
        {
            string planPath = Argument("-pharmaPlan"), output = Argument("-pharmaOutput");
            if (planPath == null || output == null) throw new ArgumentException("Provide -pharmaPlan plan.json and -pharmaOutput dir");
            var plan = JsonUtility.FromJson<ReenactmentPlan>(File.ReadAllText(planPath));
            if (plan.schema != "reenactment-plan/1") throw new InvalidOperationException("Unexpected plan schema " + plan.schema);
            if (plan.width < 64 || plan.height < 64) throw new ArgumentException("Invalid frame size");
            Directory.CreateDirectory(Path.Combine(output, "frames"));
            // Preview: every frame is still simulated, but only these are rendered and saved.
            string only = Argument("-pharmaFrames");
            var wanted = only == null ? null : new HashSet<int>(only.Split(',').Select(int.Parse));
            var built = ReenactmentScene.Build(plan);
            var sim = built.sim; var camera = sim.roomCamera;
            int width = plan.width, height = plan.height;
            var rt = new RenderTexture(width, height, 24, RenderTextureFormat.ARGB32);
            rt.antiAliasing = 4; rt.Create(); camera.targetTexture = rt;
            var texture = new Texture2D(width, height, TextureFormat.RGB24, false);
            var report = new Report { unity_version = Application.unityVersion, recording = plan.recording,
                colliders = built.colliders, labels = built.labels, bottles = built.bottles.Length };
            // Alignment check: the Unity camera's projection of the registration's own points.
            report.alignment = (plan.alignment_points ?? new PlanPoint[0]).Select(p => {
                Vector3 s = camera.WorldToScreenPoint(ReenactmentPlan.V(p.unity));
                return new AlignmentPixel { region_id = p.region_id, unity_pixel = new[]{ s.x, height - s.y },
                    registration_pixel = p.pixel, depth = s.z };
            }).ToArray();
            // Offline CPU skinning, as in the demo exporter, so each frame shows its sampled pose.
            var skins = sim.technician.GetComponentsInChildren<SkinnedMeshRenderer>();
            var meshes = new List<Mesh>(); var proxies = new List<GameObject>();
            foreach (var skin in skins)
            {
                var proxy = new GameObject("Offline skinned mesh");
                proxy.transform.SetParent(skin.transform, false);
                var mesh = new Mesh(); proxy.AddComponent<MeshFilter>().sharedMesh = mesh;
                proxy.AddComponent<MeshRenderer>().sharedMaterials = skin.sharedMaterials;
                meshes.Add(mesh); proxies.Add(proxy); skin.enabled = false;
            }
            var visuals = sim.technician.GetComponentsInChildren<Renderer>(true).Where(r => !(r is SkinnedMeshRenderer)).ToArray();
            try
            {
                sim.Evaluate(0); Bake(skins, meshes); camera.Render();
                for (int i = 0; i < plan.frame_count; i++)
                {
                    sim.Evaluate(i / plan.fps);
                    if (!sim.TechnicianVisible) report.technician_hidden_frames++;
                    if (wanted != null && !wanted.Contains(i)) continue;
                    foreach (var r in visuals) r.enabled = sim.TechnicianVisible;
                    Bake(skins, meshes); camera.Render();
                    RenderTexture.active = rt;
                    texture.ReadPixels(new Rect(0, 0, width, height), 0, 0); texture.Apply();
                    File.WriteAllBytes(Path.Combine(output, "frames", i.ToString("D6") + ".png"), texture.EncodeToPNG());
                    if (wanted != null || i % 10 == 0 || i == plan.frame_count - 1) Debug.Log($"PHARMA_PROGRESS {i + 1} {plan.frame_count}");
                }
                var a = sim.Reenactment;
                report.frames = plan.frame_count;
                report.preview_frames = wanted == null ? new int[0] : wanted.OrderBy(v => v).ToArray();
                report.shown = a.Shown; report.highlight_only = a.HighlightOnly; report.followed = a.Followed;
                report.pending = a.Pending; report.downgraded = a.Downgraded; report.guard_holds = a.GuardHolds;
                report.notes = a.Notes.ToArray();
                File.WriteAllText(Path.Combine(output, "unity_report.json"), JsonUtility.ToJson(report, true));
                Debug.Log("PHARMA_REENACT_READY " + output);
            }
            finally
            {
                camera.targetTexture = null; RenderTexture.active = null; rt.Release();
                UnityEngine.Object.DestroyImmediate(rt); UnityEngine.Object.DestroyImmediate(texture);
                for (int k = 0; k < skins.Length; k++) { skins[k].enabled = true; UnityEngine.Object.DestroyImmediate(proxies[k]); UnityEngine.Object.DestroyImmediate(meshes[k]); }
            }
        }

        static void Bake(SkinnedMeshRenderer[] skins, List<Mesh> meshes)
        { for (int k = 0; k < skins.Length; k++) skins[k].BakeMesh(meshes[k]); }

        static string Argument(string key)
        {
            string[] args = Environment.GetCommandLineArgs(); int index = Array.IndexOf(args, key);
            return index >= 0 && index + 1 < args.Length ? args[index + 1] : null;
        }
    }
}
