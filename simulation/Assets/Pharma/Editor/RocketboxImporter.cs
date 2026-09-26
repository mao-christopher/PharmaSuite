using UnityEditor;
using UnityEngine;

namespace Pharma.Simulation.Editor
{
    public class RocketboxImporter : AssetPostprocessor
    {
        bool IsCharacter => assetPath.StartsWith("Assets/ThirdParty/Rocketbox/");
        void OnPreprocessModel()
        {
            if (!IsCharacter) return;
            var importer=(ModelImporter)assetImporter;
            importer.animationType=ModelImporterAnimationType.Generic;
            importer.importAnimation=false;
            importer.materialImportMode=ModelImporterMaterialImportMode.ImportStandard;
        }
        void OnPreprocessTexture()
        {
            if(!IsCharacter) return;
            var importer=(TextureImporter)assetImporter;
            importer.maxTextureSize=2048;
            if(assetPath.Contains("normal")) importer.textureType=TextureImporterType.NormalMap;
        }
        void OnPostprocessMaterial(Material material)
        {
            if(!IsCharacter) return;
            material.color=Color.white;
            material.SetFloat("_Glossiness",.12f);
        }
        void OnPostprocessModel(GameObject root)
        {
            if(!IsCharacter) return;
            foreach(var node in root.GetComponentsInChildren<Transform>(true))
            {
                string n=node.name.ToLowerInvariant();
                if(n.Contains("poly") && !n.Contains("hipoly")) node.gameObject.SetActive(false);
            }
        }
    }
}
