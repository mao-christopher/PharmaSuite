using System;
using System.Collections.Generic;
using UnityEngine;

namespace Pharma.Simulation
{
    /// <summary>The technician's root on the floor at one tick. Hands and bottles come from actions.</summary>
    public struct MotionSample
    {
        public Vector3 position;
        public Quaternion facing;
        public float gait;
        public bool walking;
        public bool cut;  // place without sweeping from the last pose (reappearing far away)
        public bool hidden;  // not seen yet: nothing to show
    }

    /// <summary>Where the body stands and faces over time: its own planned walk, or later a floor track.</summary>
    public interface IMotionSource
    {
        MotionSample Start { get; }
        MotionSample Sample(float time, ActionLeg leg);
    }

    /// <summary>The authored aisle survey, then collision-aware routes between action stand points.</summary>
    public class PlannedWalk : IMotionSource
    {
        readonly List<Vector3[]> routes = new List<Vector3[]>();
        readonly List<Vector3> stands;
        readonly Vector3[] surveyRoute;
        readonly float surveyEnd;

        public PlannedWalk(CollisionWorld world, ActionSchedule actions, float floorY)
        {
            var cues = actions.Cues; stands = actions.Stands; surveyEnd = actions.StartTime;
            for (int i=0; i<cues.Count; i++)
            {
                var route = world.Route(i==0 ? stands[0] : stands[i-1], stands[i]);
                float seconds = cues[i].time - (i==0 ? surveyEnd : cues[i-1].time) - 1.4f;
                if (CollisionWorld.Length(route) * 1.5f / seconds > 1.8f)
                    throw new InvalidOperationException("Schedule requires unsafe walking speed: " + cues[i].scenario);
                routes.Add(world.RoundRoute(route));
            }
            var surveyPoints = new[]{
                new Vector3(3.25f,floorY,.5f), new Vector3(3.25f,floorY,2.65f),
                new Vector3(-3.25f,floorY,2.65f), new Vector3(-3.25f,floorY,5.2f),
                new Vector3(3.25f,floorY,5.2f), new Vector3(3.25f,floorY,.5f), stands[0]};
            var survey = new List<Vector3>();
            for(int i=1;i<surveyPoints.Length;i++) survey.AddRange(world.Route(surveyPoints[i-1],surveyPoints[i]));
            surveyRoute=world.RoundRoute(survey.ToArray());
            if(CollisionWorld.Length(surveyRoute)/(surveyEnd-2)>1.8f)
                throw new InvalidOperationException("Survey route exceeds walking speed");
        }

        public MotionSample Start => Standing(surveyRoute[0]);

        public MotionSample Sample(float t, ActionLeg leg)
        {
            if (!leg.started)
            {
                float u=TravelFraction(t-1,surveyEnd-2,.8f);
                Vector3 position=CollisionWorld.Sample(surveyRoute,u,out var direction);
                bool walking=!(t<1 || t>surveyEnd-1);
                Quaternion facing=Quaternion.LookRotation(direction);
                if(t>surveyEnd-1) facing=Quaternion.Slerp(facing,Quaternion.identity,t-(surveyEnd-1));
                float surveyGait=walking?Mathf.Sin(CollisionWorld.Length(surveyRoute)*u*14)*.09f:0;
                return new MotionSample{position=position,facing=facing,gait=surveyGait,walking=walking};
            }
            if (leg.retracting) return Standing(stands[leg.index-1]);
            if (leg.complete) return Standing(stands[stands.Count-1]);
            float length = CollisionWorld.Length(routes[leg.index]);
            if (length > .01f && t < leg.walkEnd)
            {
                float u = Mathf.Clamp01((t-leg.walkStart)/(leg.walkEnd-leg.walkStart));
                float eased = Mathf.SmoothStep(0,1,u);
                Vector3 body = CollisionWorld.Sample(routes[leg.index],eased,out var direction);
                float turn = Mathf.SmoothStep(0,1,Mathf.Min(u/.18f,(1-u)/.18f));
                Quaternion rotation = Quaternion.Slerp(Quaternion.identity,Quaternion.LookRotation(direction),turn);
                float gait = Mathf.Sin(length * eased * 14) * .11f * Mathf.Sin(u*Mathf.PI);
                return new MotionSample{position=body,facing=rotation,gait=gait,walking=true};
            }
            return Standing(stands[leg.index]);
        }

        static MotionSample Standing(Vector3 position) => new MotionSample{position=position,facing=Quaternion.identity};

        static float TravelFraction(float time,float duration,float ramp)
        {
            float t=Mathf.Clamp(time,0,duration);
            float distance=t<ramp?.5f*t*t/ramp:t>duration-ramp?duration-ramp-.5f*(duration-t)*(duration-t)/ramp:t-.5f*ramp;
            return distance/(duration-ramp);
        }
    }
}
