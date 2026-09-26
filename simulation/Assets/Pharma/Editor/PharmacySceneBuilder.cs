using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;

namespace Pharma.Simulation.Editor
{
    public static class PharmacySceneBuilder
    {
        public const string ScenePath="Assets/Pharma/Generated/Pharmacy.unity";
        static readonly Color Teal=new Color(.055f,.28f,.3f);
        static readonly Color Pale=new Color(.83f,.89f,.88f);
        static Material white, teal, wood, metal, dark, amber;
        static Camera camera;

        [MenuItem("Pharma/1. Build pharmacy scene")]
        public static void Build()
        {
            string characterPath="Assets/ThirdParty/Rocketbox/Medical_Male_03.fbx";
            var prefab=AssetDatabase.LoadAssetAtPath<GameObject>(characterPath);
            if(!prefab) throw new InvalidOperationException("Run python3 simulation/tools/fetch_character.py first.");
            Directory.CreateDirectory("Assets/Pharma/Generated");
            AssetDatabase.Refresh();
            EditorSceneManager.NewScene(NewSceneSetup.EmptyScene,NewSceneMode.Single);
            white=Material("Porcelain",new Color(.9f,.93f,.92f));
            teal=Material("Deep teal",Teal); wood=Material("Oak",new Color(.64f,.47f,.3f));
            metal=Material("Brushed metal",new Color(.47f,.53f,.55f),.3f);
            dark=Material("Graphite",new Color(.045f,.065f,.075f));
            amber=Material("Amber bottle",new Color(.52f,.24f,.06f));
            RenderSettings.ambientMode=AmbientMode.Trilight;
            RenderSettings.ambientSkyColor=new Color(.42f,.46f,.5f);
            RenderSettings.ambientEquatorColor=new Color(.30f,.34f,.34f);
            RenderSettings.ambientGroundColor=new Color(.16f,.18f,.2f);
            QualitySettings.shadows=ShadowQuality.All;
            QualitySettings.shadowResolution=ShadowResolution.High;
            QualitySettings.antiAliasing=4;
            QualitySettings.shadowDistance=20;
            var sun=new GameObject("Soft daylight").AddComponent<Light>();
            sun.type=LightType.Directional; sun.intensity=.75f; sun.color=new Color(1,.95f,.88f);
            sun.transform.rotation=Quaternion.Euler(40,-25,0); sun.shadows=LightShadows.Soft;
            var fill=new GameObject("Ceiling fill").AddComponent<Light>();
            fill.type=LightType.Point; fill.range=12; fill.intensity=.65f; fill.transform.position=new Vector3(-2,3,-1);
            Box("Floor",new Vector3(0,-.06f,0),new Vector3(9,.12f,7),Material("Floor",new Color(.64f,.69f,.69f)));
            var grout=Material("Tile grout",new Color(.47f,.52f,.52f));
            for(int i=-4;i<=4;i++) Box("Floor seam",new Vector3(i,.001f,0),new Vector3(.012f,.002f,7),grout);
            for(int i=-3;i<=3;i++) Box("Floor seam",new Vector3(0,.002f,i),new Vector3(9,.002f,.012f),grout);
            Box("Back wall",new Vector3(0,1.65f,1.9f),new Vector3(8,3.3f,.16f),Material("Wall",Pale));
            Box("Wall skirting",new Vector3(0,.12f,1.78f),new Vector3(8,.24f,.08f),teal);
            Box("Header",new Vector3(0,2.82f,1.75f),new Vector3(6.2f,.48f,.08f),teal);
            Label("PHARMA  /  DISPENSARY",new Vector3(0,2.82f,1.68f),.13f,Color.white);
            Label("RESEARCH DEMO  /  SYNTHETIC STOCK",new Vector3(0,2.48f,1.68f),.052f,Teal);
            var regionList=new List<ShelfRegion>();
            string[] keys={"vitamin-d-50000-iu","amoxicillin-500-mg","metformin-500-mg","atorvastatin-20-mg","lisinopril-10-mg","omeprazole-20-mg"};
            string[] titles={"VITAMIN D | 50,000 IU","AMOXICILLIN | 500 mg","METFORMIN | 500 mg","ATORVASTATIN | 20 mg","LISINOPRIL | 10 mg","OMEPRAZOLE | 20 mg"};
            for(int col=0;col<3;col++)
            {
                float x=(col-1)*1.65f;
                Box("Shelf backing",new Vector3(x,1.27f,1.67f),new Vector3(1.54f,1.78f,.1f),white);
                foreach(float side in new[]{-.79f,.79f}) Box("Shelf upright",new Vector3(x+side,1.27f,1.38f),new Vector3(.055f,1.83f,.62f),white);
                foreach(float y in new[]{.38f,.84f,1.56f,2.16f}) Box("Shelf board",new Vector3(x,y,1.38f),new Vector3(1.6f,.055f,.65f),white);
                Box("Base cabinet",new Vector3(x,.2f,1.38f),new Vector3(1.59f,.4f,.65f),teal);
                for(int row=0;row<2;row++)
                {
                    int index=row*3+col; float y=row==0?.975f:1.695f;
                    string id="shelf-"+(char)('a'+index);
                    regionList.Add(new ShelfRegion{id=id,kind="shelf",medication=keys[index],center=new Vector3(x,y+.13f,1.08f),size=new Vector3(1.47f,.57f,.02f)});
                    Box("Shelf label strip",new Vector3(x,y-.13f,1.027f),new Vector3(1.44f,.095f,.018f),teal);
                    Label(titles[index],new Vector3(x,y-.13f,1.011f),.039f,Color.white);
                    for(int j=0;j<(index==0?2:4);j++)
                    {
                        float bx=x+(index==0?j*.32f:(j-1.5f)*.3f);
                        var bottle=Bottle("Stock "+id+"-"+j,new Vector3(bx,y,1.18f),titles[index]);
                        if(index==0) bottle.name=j==0?"Tracked bottle A1":"Tracked bottle A2";
                    }
                }
            }
            // Low side counter leaves the active technician visible to the room camera.
            Box("Dispensing cabinet",new Vector3(-2.55f,.46f,-.8f),new Vector3(1.25f,.92f,.95f),teal);
            Box("Countertop",new Vector3(-2.55f,.97f,-.8f),new Vector3(1.38f,.08f,1.05f),wood);
            Box("Counting mat",new Vector3(-2.43f,1.018f,-.8f),new Vector3(.55f,.015f,.4f),white);
            Label("DISPENSING",new Vector3(-2.55f,.68f,-1.285f),.055f,Color.white);
            Box("Terminal stand",new Vector3(-2.97f,1.16f,-.56f),new Vector3(.1f,.3f,.12f),dark);
            Box("Cashier terminal",new Vector3(-2.97f,1.37f,-.56f),new Vector3(.42f,.29f,.06f),dark);
            Label("INVENTORY",new Vector3(-2.97f,1.39f,-.6f),.026f,Color.white);
            Label("DEMO",new Vector3(-2.97f,1.31f,-.6f),.023f,Color.cyan);
            regionList.Add(new ShelfRegion{id="counter",kind="counter",medication="",center=new Vector3(-2.5f,1.15f,-.8f),size=new Vector3(1.15f,.45f,.7f)});
            Box("Disposal bin",new Vector3(2.5f,.37f,-.55f),new Vector3(.7f,.74f,.7f),Material("Waste blue",new Color(.12f,.31f,.45f)));
            Box("Bin opening",new Vector3(2.5f,.75f,-.55f),new Vector3(.59f,.02f,.58f),dark);
            foreach(float x in new[]{2.15f,2.85f}) Box("Bin rim",new Vector3(x,.8f,-.55f),new Vector3(.055f,.11f,.77f),metal);
            Label("DISPOSAL",new Vector3(2.5f,.48f,-.908f),.05f,Color.white);
            Label("LOG CONTENTS",new Vector3(2.5f,.34f,-.909f),.028f,Color.white);
            regionList.Add(new ShelfRegion{id="disposal",kind="disposal",medication="",center=new Vector3(2.5f,.95f,-.55f),size=new Vector3(.85f,.46f,.7f)});
            camera=new GameObject("Room Camera").AddComponent<Camera>();
            camera.tag="MainCamera"; camera.transform.position=new Vector3(4.7f,3.25f,-6.8f);
            camera.transform.LookAt(new Vector3(-.1f,1.18f,.45f));
            camera.fieldOfView=46; camera.nearClipPlane=.1f; camera.farClipPlane=30;
            camera.backgroundColor=new Color(.78f,.84f,.85f); camera.clearFlags=CameraClearFlags.SolidColor;
            camera.allowHDR=false; camera.aspect=16f/9f;
            var actor=(GameObject)PrefabUtility.InstantiatePrefab(prefab);
            actor.name="Pharmacy technician (Rocketbox)";
            // Explicitly bind the imported color maps to avoid FBX exporter path differences.
            foreach(var renderer in actor.GetComponentsInChildren<SkinnedMeshRenderer>(true))
            {
                var mats=renderer.sharedMaterials;
                for(int i=0;i<mats.Length;i++)
                {
                    string name=mats[i].name.ToLowerInvariant();
                    string part=name.Contains("head")?"head":name.Contains("steto")?"stetoskop":"body";
                    var mat=Material("Character_"+mats[i].name,Color.white);
                    mat.mainTexture=AssetDatabase.LoadAssetAtPath<Texture2D>("Assets/ThirdParty/Rocketbox/m153_"+part+"_color.tga");
                    mats[i]=mat;
                }
                renderer.sharedMaterials=mats; renderer.updateWhenOffscreen=true;
            }
            var renderers=actor.GetComponentsInChildren<Renderer>();
            if(renderers.Length==0) throw new InvalidOperationException("Character contains no active renderers");
            Bounds bounds=renderers[0].bounds; foreach(var renderer in renderers) bounds.Encapsulate(renderer.bounds);
            actor.transform.localScale*=1.8f/bounds.size.y;
            bounds=renderers[0].bounds; foreach(var renderer in renderers) bounds.Encapsulate(renderer.bounds);
            actor.transform.position=new Vector3(-1.97f,-bounds.min.y,.69f);
            var simulation=new GameObject("Pharmacy Simulation").AddComponent<PharmacySimulation>();
            simulation.roomCamera=camera; simulation.technician=actor.transform;
            simulation.bottleOne=GameObject.Find("Tracked bottle A1").transform;
            simulation.bottleTwo=GameObject.Find("Tracked bottle A2").transform;
            simulation.regions=regionList.ToArray();
            var screen=Box("Optional shelf occluder",new Vector3(-1.55f,1.25f,.5f),new Vector3(1.6f,1.05f,.07f),white);
            simulation.occluder=screen.transform; screen.SetActive(false);
            AssetDatabase.SaveAssets();
            EditorSceneManager.SaveScene(EditorSceneManager.GetActiveScene(),ScenePath);
            EditorBuildSettings.scenes=new[]{new EditorBuildSettingsScene(ScenePath,true)};
            Debug.Log("PHARMA_SCENE_READY "+ScenePath);
        }

        static Material Material(string name,Color color,float metallic=0)
        {
            string path="Assets/Pharma/Generated/"+name.Replace("/","_")+".mat";
            var material=AssetDatabase.LoadAssetAtPath<Material>(path);
            if(!material) { material=new Material(Shader.Find("Standard")); AssetDatabase.CreateAsset(material,path); }
            material.color=color; material.SetFloat("_Metallic",metallic); material.SetFloat("_Glossiness",.18f);
            return material;
        }
        static GameObject Box(string name,Vector3 p,Vector3 scale,Material mat)
        {
            var obj=GameObject.CreatePrimitive(PrimitiveType.Cube); obj.name=name;
            obj.transform.position=p; obj.transform.localScale=scale;
            obj.GetComponent<Renderer>().sharedMaterial=mat; return obj;
        }
        static Transform Bottle(string name,Vector3 p,string label)
        {
            var root=new GameObject(name).transform; root.position=p;
            var body=GameObject.CreatePrimitive(PrimitiveType.Cylinder); body.name="Amber container";
            body.transform.SetParent(root,false); body.transform.localScale=new Vector3(.105f,.10f,.105f);
            body.GetComponent<Renderer>().sharedMaterial=amber;
            var cap=GameObject.CreatePrimitive(PrimitiveType.Cylinder); cap.name="Child-resistant cap";
            cap.transform.SetParent(root,false); cap.transform.localPosition=new Vector3(0,.105f,0); cap.transform.localScale=new Vector3(.12f,.025f,.12f);
            cap.GetComponent<Renderer>().sharedMaterial=white;
            var wrap=GameObject.CreatePrimitive(PrimitiveType.Cylinder); wrap.name="Paper label";
            wrap.transform.SetParent(root,false); wrap.transform.localScale=new Vector3(.107f,.048f,.107f); wrap.GetComponent<Renderer>().sharedMaterial=white;
            var text=Label(label.Split('|')[0].Trim(),p+new Vector3(0,0,-.056f),.012f,Teal);
            text.transform.SetParent(root,true); return root;
        }
        static GameObject Label(string text,Vector3 p,float size,Color color)
        {
            var obj=new GameObject(text); obj.transform.position=p;
            var mesh=obj.AddComponent<TextMesh>(); mesh.text=text; mesh.fontSize=64; mesh.characterSize=size*.28f;
            mesh.anchor=TextAnchor.MiddleCenter; mesh.alignment=TextAlignment.Center; mesh.color=color;
            obj.AddComponent<WorldText>().labelShader=Shader.Find("Pharma/WorldText");
            return obj;
        }
    }
}
