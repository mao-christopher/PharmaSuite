using UnityEngine;

namespace Pharma.Simulation
{
    /// <summary>Fixed-tick planted stance and alternating swing-foot targets. No root motion teleporting.</summary>
    public sealed class FootPlantGait
    {
        public Vector3 Right { get; private set; }
        public Vector3 Left { get; private set; }
        public Quaternion RightRotation { get; private set; }
        public Quaternion LeftRotation { get; private set; }
        public int SwingFoot { get; private set; } = -1;
        public float SwingProgress { get; private set; }
        Vector3 start, end;
        Quaternion startRotation, endRotation;
        float elapsed, duration;
        int nextFoot;
        public FootPlantGait(Vector3 right, Vector3 left, Quaternion rightRotation, Quaternion leftRotation)
        { Right=right; Left=left; RightRotation=rightRotation; LeftRotation=leftRotation; }

        public void Tick(Vector3 neutralRight,Vector3 neutralLeft,Quaternion rightRotation,
            Quaternion leftRotation,Vector3 velocity,float dt)
        {
            float speed=velocity.magnitude;
            if(SwingFoot<0)
            {
                float rd=Vector3.Distance(Right,neutralRight), ld=Vector3.Distance(Left,neutralLeft);
                float ra=Quaternion.Angle(RightRotation,rightRotation), la=Quaternion.Angle(LeftRotation,leftRotation);
                float threshold=speed>.08f?.09f:.045f;
                if(Mathf.Max(rd,ld)>threshold || Mathf.Max(ra,la)>24)
                {
                    SwingFoot=Mathf.Abs(rd-ld)>.015f ? (rd>ld?0:1) : nextFoot;
                    nextFoot=1-SwingFoot;
                    start=SwingFoot==0?Right:Left;
                    startRotation=SwingFoot==0?RightRotation:LeftRotation;
                    end=SwingFoot==0?neutralRight:neutralLeft;
                    // Anticipation lands ahead of the translating pelvis; stance stays in world space.
                    end+=Vector3.ClampMagnitude(velocity*.45f,.60f);
                    end.y=(SwingFoot==0?neutralRight:neutralLeft).y;
                    endRotation=SwingFoot==0?rightRotation:leftRotation;
                    elapsed=0; duration=Mathf.Lerp(.36f,.27f,Mathf.Clamp01(speed/1.6f));
                }
            }
            if(SwingFoot<0) { SwingProgress=0; return; }
            // Predict the landing from the current root velocity, not the liftoff pose.
            // Landing slightly ahead avoids overstretching the supporting leg next step.
            Vector3 neutral=SwingFoot==0?neutralRight:neutralLeft;
            end=neutral+Vector3.ClampMagnitude(velocity*(Mathf.Max(0,duration-elapsed)+.16f),.60f);
            end.y=neutral.y;
            endRotation=SwingFoot==0?rightRotation:leftRotation;
            elapsed+=dt; float u=Mathf.Clamp01(elapsed/duration); SwingProgress=u;
            float eased=u*u*u*(u*(u*6-15)+10);
            Vector3 p=Vector3.Lerp(start,end,eased);
            p.y+=Mathf.Sin(Mathf.PI*u)*Mathf.Lerp(.045f,.095f,Mathf.Clamp01(speed/1.5f));
            Quaternion rotation=Quaternion.Slerp(startRotation,endRotation,eased);
            if(SwingFoot==0) { Right=p; RightRotation=rotation; }
            else { Left=p; LeftRotation=rotation; }
            if(u>=1) SwingFoot=-1;
        }
    }
}
