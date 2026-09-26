using System;
using UnityEngine;

namespace Pharma.Simulation
{
    /// <summary>Explicit simulator-only X-ray visualization. Never a CV/runtime observation.</summary>
    public static class SimulationSkeleton
    {
        public static readonly string[] Names={"head","neck","chest","pelvis","right_shoulder","right_elbow","right_wrist",
            "left_shoulder","left_elbow","left_wrist","right_hip","right_knee","right_ankle","left_hip","left_knee","left_ankle"};
        public static readonly int[] Edges={0,1,1,2,2,3,2,4,4,5,5,6,2,7,7,8,8,9,3,10,10,11,11,12,3,13,13,14,14,15};
        [Serializable] public class Definition
        {
            public int schema_version=1,width,height,fps,frame_count;
            public string source="unity_rig_ground_truth",coordinates="top_left_pixels",usage="simulation_visualization_and_evaluation_only";
            public string[] joint_names=Names;
            public int[] edge_indices=Edges;
        }
        [Serializable] public class Frame
        {
            public int frame;
            public double media_time_ms;
            public string source="unity_rig_ground_truth";
            public Vector3[] joints_pixels;
            public bool[] visible_to_camera;
        }
        public static Frame Capture(PharmacySimulation sim,int frame,int width,int height)
        {
            var joints=sim.SkeletonJoints();
            var data=new Frame{frame=frame,media_time_ms=(double)frame*1000/PharmacySimulation.SimulationFps,
                joints_pixels=new Vector3[joints.Length],visible_to_camera=new bool[joints.Length]};
            for(int i=0;i<joints.Length;i++)
            {
                var p=sim.roomCamera.WorldToViewportPoint(joints[i].position);
                data.joints_pixels[i]=new Vector3(p.x*width,(1-p.y)*height,p.z);
                Vector3 ray=joints[i].position-sim.roomCamera.transform.position;
                data.visible_to_camera[i]=p.z>0 && !Physics.Raycast(sim.roomCamera.transform.position,ray.normalized,
                    Mathf.Max(0,ray.magnitude-.02f),CollisionWorld.SolidMask,QueryTriggerInteraction.Ignore);
            }
            return data;
        }
        public static void Draw(PharmacySimulation sim)
        {
            var data=Capture(sim,0,Screen.width,Screen.height);
            var previous=GUI.color;var matrix=GUI.matrix;
            GUI.color=new Color(.20f,.93f,1f,1);
            for(int e=0;e<Edges.Length;e+=2)
            {
                Vector3 a=data.joints_pixels[Edges[e]],b=data.joints_pixels[Edges[e+1]];
                if(a.z<=0 || b.z<=0) continue;
                Vector2 delta=(Vector2)(b-a);
                GUIUtility.RotateAroundPivot(Mathf.Atan2(delta.y,delta.x)*Mathf.Rad2Deg,a);
                GUI.DrawTexture(new Rect(a.x,a.y-2,delta.magnitude,4),Texture2D.whiteTexture);
                GUI.matrix=matrix;
            }
            GUI.color=previous;
        }
    }
}
