using System;
using System.Collections.Generic;
using System.Linq;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace Pharma.Simulation.Editor
{
    /// <summary>M7: an in-memory scene built from the dashboard's rebuilt room boxes, with the
    /// registered camera. It reuses the demo scene's character, lights and bottle model; the demo
    /// room itself is removed and the scan mesh never becomes geometry or a collider.</summary>
    public static class ReenactmentScene
    {
        public class Built
        {
            public PharmacySimulation sim;
            public Transform[] bottles;
            public Dictionary<string, GameObject> highlights;
            public int colliders, labels, cutaway;
        }

        static readonly Dictionary<string, Material> materials = new Dictionary<string, Material>();

        public static Built Build(ReenactmentPlan plan)
        {
            EditorSceneManager.OpenScene(PharmacySceneBuilder.ScenePath);
            var sim = UnityEngine.Object.FindFirstObjectByType<PharmacySimulation>();
            if (!sim || !sim.technician || !sim.roomCamera || !sim.bottleOne)
                throw new InvalidOperationException("The demo scene is missing its character, camera or bottle model.");
            var template = UnityEngine.Object.Instantiate(sim.bottleOne.gameObject);
            template.name = "Bottle model"; template.SetActive(false);
            var keep = new HashSet<GameObject> { sim.gameObject, sim.technician.root.gameObject, sim.roomCamera.transform.root.gameObject, template };
            foreach (var root in EditorSceneManager.GetActiveScene().GetRootGameObjects())
                if (!keep.Contains(root) && !root.GetComponent<Light>()) UnityEngine.Object.DestroyImmediate(root);
            sim.bottleOne = sim.bottleTwo = null; sim.occluder = null;
            materials.Clear();
            var built = new Built { sim = sim, highlights = new Dictionary<string, GameObject>() };

            var floorBounds = new Bounds();
            foreach (var box in plan.boxes)
            {
                var obj = GameObject.CreatePrimitive(PrimitiveType.Cube);
                obj.name = box.name;
                obj.transform.position = ReenactmentPlan.V(box.center);
                obj.transform.rotation = Quaternion.Euler(0, box.yaw_deg, 0);
                obj.transform.localScale = ReenactmentPlan.V(box.size);
                obj.GetComponent<Renderer>().sharedMaterial = Flat(box.color);
                if (box.collider == "solid") { obj.layer = CollisionWorld.SolidLayer; built.colliders++; }
                else if (box.collider == "floor") { obj.layer = CollisionWorld.FloorLayer; floorBounds = obj.GetComponent<Renderer>().bounds; }
                else UnityEngine.Object.DestroyImmediate(obj.GetComponent<Collider>());
                if (!string.IsNullOrEmpty(box.label)) { Label(box); built.labels++; }
                // A wall between the camera and the room: not drawn, still a collider.
                if (box.cutaway) { obj.GetComponent<Renderer>().enabled = false; built.cutaway++; }
            }

            var regions = new List<ShelfRegion>();
            foreach (var r in plan.regions)
            {
                string kind = r.region_type == "dispensing_counter" ? "counter" : r.region_type == "disposal" ? "disposal" : "shelf";
                regions.Add(new ShelfRegion { id = r.region_id, kind = kind, medication = r.medication_key ?? "",
                    center = ReenactmentPlan.V(r.center), size = ReenactmentPlan.V(r.size), yaw = r.yaw_deg });
                var glow = GameObject.CreatePrimitive(PrimitiveType.Cube);
                glow.name = "Highlight " + r.region_id;
                UnityEngine.Object.DestroyImmediate(glow.GetComponent<Collider>());
                glow.transform.position = ReenactmentPlan.V(r.center);
                glow.transform.rotation = Quaternion.Euler(0, r.yaw_deg, 0);
                glow.transform.localScale = ReenactmentPlan.V(r.size) + Vector3.one * .02f;
                var m = new Material(Shader.Find("Standard"));
                ReenactmentActions.SetFade(m, .22f);
                var renderer = glow.GetComponent<Renderer>();
                renderer.sharedMaterial = m; renderer.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
                glow.SetActive(false);
                built.highlights[r.region_id] = glow;
            }

            built.bottles = plan.bottles.Select(b => Bottle(template, b)).ToArray();

            var camera = sim.roomCamera;
            camera.transform.position = ReenactmentPlan.V(plan.camera.position);
            var q = plan.camera.rotation_xyzw;
            camera.transform.rotation = new Quaternion(q[0], q[1], q[2], q[3]);
            camera.aspect = (float)plan.width / plan.height;
            var shift = plan.camera.lens_shift ?? new float[2];
            if (Mathf.Abs(shift[0]) > 1e-5f || Mathf.Abs(shift[1]) > 1e-5f)
            {
                // Principal point off center: a physical camera with the sensor at the frame's aspect.
                camera.usePhysicalProperties = true;
                camera.sensorSize = new Vector2(36, 36f * plan.height / plan.width);
                camera.gateFit = Camera.GateFitMode.Vertical;
                camera.lensShift = new Vector2(shift[0], shift[1]);
            }
            camera.fieldOfView = plan.camera.vertical_fov_deg;
            camera.nearClipPlane = .05f; camera.farClipPlane = 50;

            var fill = UnityEngine.Object.FindObjectsByType<Light>(FindObjectsSortMode.None).FirstOrDefault(l => l.type == LightType.Point);
            if (fill) fill.transform.position = new Vector3(floorBounds.center.x, Mathf.Max(1.8f, plan.height_m - .3f), floorBounds.center.z);

            sim.regions = regions.ToArray();
            var room = RoomDescription.Rebuilt(sim.regions, sim.technician.position.y);
            sim.InitializeReenactment(plan, room, built.bottles, built.highlights);
            return built;
        }

        static Material Flat(string hex)
        {
            if (materials.TryGetValue(hex ?? "", out var m)) return m;
            if (!ColorUtility.TryParseHtmlString(hex ?? "", out var color)) color = new Color(.8f, .8f, .8f);
            m = new Material(Shader.Find("Standard")) { color = color };
            m.SetFloat("_Glossiness", .18f);
            materials[hex ?? ""] = m;
            return m;
        }

        /// <summary>Label text on the plate's front face (room +Z, which is the plate's Unity -Z).</summary>
        static void Label(PlanBox box)
        {
            var rotation = Quaternion.Euler(0, box.yaw_deg, 0);
            Vector3 size = ReenactmentPlan.V(box.size);
            var obj = Text(box.label, ReenactmentPlan.V(box.center) + rotation * new Vector3(0, 0, -(size.z / 2 + .0015f)),
                           Mathf.Min(.6f * size.y, 1.8f * size.x / Mathf.Max(1, box.label.Length)), new Color(.1f, .12f, .14f));
            obj.transform.rotation = rotation;
        }

        static GameObject Text(string text, Vector3 position, float height, Color color)
        {
            var obj = new GameObject(text); obj.transform.position = position;
            var mesh = obj.AddComponent<TextMesh>(); mesh.text = text; mesh.fontSize = 64; mesh.characterSize = height / 6.4f;
            mesh.anchor = TextAnchor.MiddleCenter; mesh.alignment = TextAlignment.Center; mesh.color = color;
            obj.AddComponent<WorldText>().labelShader = Shader.Find("Pharma/WorldText");
            return obj;
        }

        static Transform Bottle(GameObject template, PlanBottle data)
        {
            var obj = UnityEngine.Object.Instantiate(template);
            obj.name = $"Bottle {data.index} {data.medication_key}";
            foreach (var c in obj.GetComponentsInChildren<Collider>(true)) UnityEngine.Object.DestroyImmediate(c);
            obj.transform.position = ReenactmentPlan.Has(data.position) ? ReenactmentPlan.V(data.position) : Vector3.zero;
            string name = string.IsNullOrEmpty(data.label) ? "?" : data.label.Split(' ')[0].ToUpperInvariant();
            foreach (var t in obj.GetComponentsInChildren<TextMesh>(true))
            {
                t.text = name;
                t.characterSize = Mathf.Min(t.characterSize, .085f / 6.4f * 1.8f / Mathf.Max(4, name.Length));
            }
            var marker = Text("?", obj.transform.position + Vector3.up * .2f, .12f, new Color(1f, .95f, .35f));
            marker.name = "Pending marker"; marker.transform.SetParent(obj.transform, true); marker.SetActive(false);
            obj.SetActive(true);
            return obj.transform;
        }
    }
}
