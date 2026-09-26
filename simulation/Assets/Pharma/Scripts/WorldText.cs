using UnityEngine;

namespace Pharma.Simulation
{
    /// <summary>Depth-tested physical labels; Unity's default text shader draws through people.</summary>
    [ExecuteAlways, RequireComponent(typeof(TextMesh))]
    public class WorldText : MonoBehaviour
    {
        public Shader labelShader;
        Material material;
        TextMesh text;
        void OnEnable()
        {
            text=GetComponent<TextMesh>();
            if(!text.font) text.font=Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
            text.font.RequestCharactersInTexture("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 /|,.-",64);
            if(!labelShader) labelShader=Shader.Find("Pharma/WorldText");
            material=new Material(labelShader){hideFlags=HideFlags.HideAndDontSave};
            GetComponent<MeshRenderer>().sharedMaterial=material;
            UpdateAtlas(text.font);
            Font.textureRebuilt+=UpdateAtlas;
        }
        void UpdateAtlas(Font font)
        { if(material && text && text.font==font) material.mainTexture=font.material.mainTexture; }
        void OnDisable()
        {
            Font.textureRebuilt-=UpdateAtlas;
            if(!material) return;
            if(Application.isPlaying) Destroy(material); else DestroyImmediate(material);
        }
    }
}
