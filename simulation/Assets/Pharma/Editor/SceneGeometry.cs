using System;
using System.Collections.Generic;
using System.IO;
using UnityEngine;

namespace Pharma.Simulation.Editor
{
    /// <summary>Static setup export (N2): the camera, configured region volumes and solid
    /// collider envelopes in Unity world coordinates. No bottles, rig, states or outcomes.</summary>
    public static class SceneGeometry
    {
        public const string FileName = "scene_geometry.json";
        [Serializable] class CameraData
        {
            public string camera_id, calibration_version;
            public float[] position, rotation_xyzw;
            public float vertical_fov_deg;
            public int width, height;
        }
        [Serializable] class RegionData
        {
            public string region_id, kind, medication_id;
            public float[] center, size, front;
            public float yaw_deg;
        }
        [Serializable] class SolidData
        {
            public string name, note;
            public float[] center, size;
            public float yaw_deg;
        }
        [Serializable] class Scene
        {
            public string schema = "scene-geometry/1", coordinates = "unity_world";
            public float floor_y;
            public CameraData camera;
            public RegionData[] regions;
            public SolidData[] solids;
        }

        static float[] A(Vector3 v) => new[]{v.x, v.y, v.z};

        public static void Write(PharmacySimulation sim, Camera camera, int width, int height, string output, string cameraId, string calibrationVersion)
        {
            var room = sim.Room;
            var q = camera.transform.rotation;
            var scene = new Scene {
                floor_y = 0,
                camera = new CameraData { camera_id = cameraId, calibration_version = calibrationVersion,
                    position = A(camera.transform.position), rotation_xyzw = new[]{q.x, q.y, q.z, q.w},
                    vertical_fov_deg = camera.fieldOfView, width = width, height = height },
                regions = Array.ConvertAll(room.regions, r => new RegionData {
                    region_id = r.id, kind = r.kind, medication_id = r.medication, center = A(r.center), size = A(r.size),
                    // Configured regions open toward the room camera: their local -Z.
                    yaw_deg = r.yaw, front = A(Quaternion.Euler(0, r.yaw, 0) * Vector3.back) }),
            };
            var solids = new List<SolidData>();
            foreach (var box in room.collisionBoxes)
            {
                if (box.walkable) continue;
                var m = box.transform;
                Vector3 euler = m.rotation.eulerAngles;
                var solid = new SolidData { name = box.name, center = A((Vector3)m.GetColumn(3)),
                    size = A(Vector3.Scale(box.size, m.lossyScale)), yaw_deg = euler.y };
                if (Mathf.Abs(Mathf.DeltaAngle(euler.x, 0)) > .01f || Mathf.Abs(Mathf.DeltaAngle(euler.z, 0)) > .01f)
                {
                    var bounds = GeometryUtility.CalculateBounds(Corners(box.size), m);
                    solid.center = A(bounds.center); solid.size = A(bounds.size); solid.yaw_deg = 0;
                    solid.note = "tilted box: world bounds";
                }
                solids.Add(solid);
            }
            // Non-box solid colliders are not navigation boxes; export their world bounds.
            foreach (var collider in UnityEngine.Object.FindObjectsByType<Collider>(FindObjectsSortMode.None))
            {
                if (collider is BoxCollider || !collider.enabled || collider.isTrigger || !collider.gameObject.activeInHierarchy) continue;
                if (collider.gameObject.layer != CollisionWorld.SolidLayer) continue;
                solids.Add(new SolidData { name = collider.name, center = A(collider.bounds.center),
                    size = A(collider.bounds.size), note = "non-box collider: world bounds" });
            }
            scene.solids = solids.ToArray();
            File.WriteAllText(Path.Combine(output, FileName), JsonUtility.ToJson(scene, true));
        }

        static Vector3[] Corners(Vector3 size)
        {
            var corners = new Vector3[8];
            for (int i = 0; i < 8; i++)
                corners[i] = Vector3.Scale(size * .5f, new Vector3((i&1)==0?-1:1, (i&2)==0?-1:1, (i&4)==0?-1:1));
            return corners;
        }
    }
}
