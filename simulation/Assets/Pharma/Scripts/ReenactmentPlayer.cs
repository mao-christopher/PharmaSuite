using UnityEngine;

namespace Pharma.Simulation
{
    /// <summary>M8 motion source: the recording's floor track, one sample per video frame.
    /// Holding out of view, blends, cuts, push-outs and reach steps are already in the plan.</summary>
    public class ReenactmentPlayer : IMotionSource
    {
        readonly ReenactmentPlan plan;
        readonly float floorY;
        public int FrameCount => plan.frame_count;

        public ReenactmentPlayer(ReenactmentPlan plan, float floorY)
        {
            if (plan.frames_flat == null || plan.frames_flat.Length != plan.frame_count * 4)
                throw new System.InvalidOperationException("Re-enactment plan frames are incomplete");
            this.plan = plan; this.floorY = floorY;
        }

        public int Frame(float t) => Mathf.Clamp(Mathf.FloorToInt(t * plan.fps + .0001f), 0, plan.frame_count - 1);
        public int Flags(int frame) => (int)plan.frames_flat[frame * 4 + 3];

        public MotionSample Start => At(0, false);

        public MotionSample Sample(float time, ActionLeg leg)
        {
            int f = Frame(time);
            var sample = At(f, true);
            if (f > 0 && (Flags(f) & ReenactmentPlan.Cut) == 0)
            {
                var previous = At(f - 1, false);
                sample.walking = Vector3.Distance(previous.position, sample.position) * plan.fps > .12f;
            }
            return sample;
        }

        MotionSample At(int f, bool withFlags)
        {
            int i = f * 4, flags = Flags(f);
            return new MotionSample {
                position = new Vector3(plan.frames_flat[i], floorY, plan.frames_flat[i + 1]),
                facing = Quaternion.Euler(0, plan.frames_flat[i + 2], 0),
                cut = withFlags && (flags & ReenactmentPlan.Cut) != 0,
                hidden = (flags & ReenactmentPlan.Visible) == 0 };
        }
    }
}
