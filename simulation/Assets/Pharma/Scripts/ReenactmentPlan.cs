using System;
using UnityEngine;

namespace Pharma.Simulation
{
    /// <summary>Unity side of `tools/reenact_plan.py`: a timeline already converted to Unity
    /// world coordinates. Dashboard evidence only; no simulator truth or scripted outcomes.</summary>
    [Serializable] public class ReenactmentPlan
    {
        public string schema, recording;
        public float fps, height_m;
        public int width, height, frame_count;
        public PlanCamera camera;
        public PlanBox[] boxes;
        public PlanRegion[] regions;
        public PlanBottle[] bottles;
        public float[] frames_flat;  // x, z, yaw (Unity), flags per frame
        public PlanAction[] actions;
        public PlanPoint[] alignment_points;

        public const int Visible = 1, InView = 2, Cut = 4, Pushed = 8, Step = 16;
        public static Vector3 V(float[] a) => new Vector3(a[0], a[1], a[2]);
        public static bool Has(float[] a) => a != null && a.Length == 3;
    }

    [Serializable] public class PlanCamera
    {
        public float[] position, rotation_xyzw, lens_shift;
        public float vertical_fov_deg;
        public int width, height;
        public string layout_id;
    }

    [Serializable] public class PlanBox
    {
        public string name, color, collider, label;
        public float[] center, size;
        public float yaw_deg;
        public bool cutaway;
    }

    [Serializable] public class PlanRegion
    {
        public string region_id, region_type, medication_key;
        public float[] center, size;
        public float yaw_deg;
    }

    [Serializable] public class PlanBottle
    {
        public int index;
        public string medication_key, label, look;
        public float[] position;
        public bool hidden;
    }

    [Serializable] public class PlanAction
    {
        public string action_id, type, status, outcome, bottle_id, medication_key, region_id, mode, look;
        public int contact_frame, reach_start_frame, retract_end_frame, bottle;
        public float[] contact, to, stand;
        public bool drop, pending;
    }

    [Serializable] public class PlanPoint
    {
        public string region_id;
        public float[] unity, pixel;
    }
}
