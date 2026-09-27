using System.IO;
using UnityEditor;
using UnityEngine;

namespace Pharma.Simulation.Editor
{
    public static class Demo3537Scene
    {
        [MenuItem("Pharma/IMG_3537/Build reviewed demo")]
        public static void Build()
        {
            var plan = JsonUtility.FromJson<ReenactmentPlan>(File.ReadAllText("Assets/Pharma/Demo3537/plan.json"));
            var built = ReenactmentScene.Build(plan);
            built.sim.Evaluate(14.6f);
            Selection.activeGameObject = built.sim.roomCamera.gameObject;
            Debug.Log("IMG_3537 presentation scene ready. This authored reconstruction is not CV evidence.");
        }
    }
}
