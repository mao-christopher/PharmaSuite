using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.AI;

namespace Pharma.Simulation
{
    /// <summary>Static navigation plus live swept-volume guards for fixed-clock offline simulation.</summary>
    public sealed class CollisionWorld : MonoBehaviour
    {
        public const int SolidLayer = 8;
        public const int FloorLayer = 9;
        public const int SolidMask = 1 << SolidLayer;
        public const float BodyRadius = .26f;
        public const float BodyHeight = 1.8f;
        NavMeshData data;
        NavMeshDataInstance instance;

        public void Build()
        {
            if (instance.valid) instance.Remove();
            if (data) DestroyImmediate(data);
            Physics.SyncTransforms();
            var sources = new List<NavMeshBuildSource>();
            foreach (var box in FindObjectsByType<BoxCollider>(FindObjectsSortMode.None))
            {
                if (!box.enabled || box.isTrigger || !box.gameObject.activeInHierarchy) continue;
                int layer = box.gameObject.layer;
                if (layer != SolidLayer && layer != FloorLayer) continue;
                sources.Add(new NavMeshBuildSource {
                    shape = NavMeshBuildSourceShape.Box,
                    transform = box.transform.localToWorldMatrix * Matrix4x4.Translate(box.center),
                    size = box.size, area = layer == FloorLayer ? 0 : 1
                });
            }
            var settings = NavMesh.GetSettingsByIndex(0);
            // Carrying arms need more corridor clearance than the central body capsule.
            settings.agentRadius = BodyRadius + .20f;
            settings.agentHeight = BodyHeight;
            settings.agentClimb = .08f;
            settings.agentSlope = 30;
            settings.overrideVoxelSize = true;
            settings.voxelSize = .035f;
            data = NavMeshBuilder.BuildNavMeshData(settings, sources,
                new Bounds(new Vector3(0, 1, 2), new Vector3(10, 5, 12)), Vector3.zero, Quaternion.identity);
            if (!data) throw new InvalidOperationException("Could not build pharmacy navigation surface");
            instance = NavMesh.AddNavMeshData(data);
        }

        public Vector3[] Route(Vector3 from, Vector3 to)
        {
            // Waiting at an interaction point must not make a trip to the nearest
            // NavMesh point and back merely because clearance erodes the surface.
            if (Vector3.Distance(from,to)<.0001f)
            {
                if (!CanStand(from,out string obstacle))
                    throw new InvalidOperationException("Stationary interaction is blocked by "+obstacle);
                return new[]{from,to};
            }
            if (!NavMesh.SamplePosition(from, out var start, .30f, NavMesh.AllAreas) ||
                !NavMesh.SamplePosition(to, out var end, .30f, NavMesh.AllAreas))
                throw new InvalidOperationException("Interaction stand point is outside walkable space: " + to);
            var path = new NavMeshPath();
            if (!NavMesh.CalculatePath(start.position, end.position, NavMesh.AllAreas, path) ||
                path.status != NavMeshPathStatus.PathComplete)
                throw new InvalidOperationException("No complete route to interaction stand point: " + to);
            var points = new List<Vector3> { from };
            foreach (var p in path.corners) points.Add(new Vector3(p.x, from.y, p.z));
            points.Add(to);
            for (int i = 1; i < points.Count; i++)
                if (!CanMove(points[i-1], points[i], out string obstacle))
                    throw new InvalidOperationException("Navigation route intersects " + obstacle);
            return points.ToArray();
        }

        public bool CanStand(Vector3 position, out string obstacle)
        {
            var hits = Physics.OverlapCapsule(position + Vector3.up * (BodyRadius + .03f),
                position + Vector3.up * (BodyHeight - BodyRadius), BodyRadius,
                SolidMask, QueryTriggerInteraction.Ignore);
            obstacle = hits.Length == 0 ? "" : hits[0].name;
            return hits.Length == 0;
        }

        public bool CanMove(Vector3 from, Vector3 to, out string obstacle)
        {
            if (!CanStand(from, out obstacle) || !CanStand(to, out obstacle)) return false;
            Vector3 delta = to - from;
            if (delta.magnitude < .00001f) return true;
            if (Physics.CapsuleCast(from + Vector3.up * (BodyRadius + .03f),
                from + Vector3.up * (BodyHeight - BodyRadius), BodyRadius,
                delta.normalized, out var hit, delta.magnitude, SolidMask, QueryTriggerInteraction.Ignore))
            { obstacle = hit.collider.name; return false; }
            return true;
        }

        public bool ClearSegment(Vector3 from, Vector3 to, float radius, out string obstacle)
        {
            var hits = Physics.OverlapCapsule(from, to, radius, SolidMask, QueryTriggerInteraction.Ignore);
            obstacle = hits.Length == 0 ? "" : hits[0].name;
            return hits.Length == 0;
        }

        public static float Length(Vector3[] points)
        {
            float length = 0;
            for (int i = 1; i < points.Length; i++) length += Vector3.Distance(points[i-1], points[i]);
            return length;
        }

        public static Vector3 Sample(Vector3[] points, float fraction, out Vector3 direction)
        {
            float distance = Length(points) * Mathf.Clamp01(fraction);
            direction = Vector3.forward;
            for (int i = 1; i < points.Length; i++)
            {
                Vector3 edge = points[i] - points[i-1];
                float length = edge.magnitude;
                if (length < .00001f) continue;
                direction = edge / length;
                if (distance <= length) return points[i-1] + direction * distance;
                distance -= length;
            }
            return points[points.Length-1];
        }

        void OnDestroy()
        {
            if (instance.valid) instance.Remove();
            if (data) DestroyImmediate(data);
        }
    }
}
