using System.Linq;
using UnityEditor;
using UnityEngine;

namespace Pharma.Simulation.Editor
{
    // Surface details share the existing furniture envelopes and never add navigation obstacles.
    public static class PharmacyDetailPass
    {
        static Material paint, steel, ink, paper, blue;
        static Material Mat(string name, Color color, float smooth=.3f, float metallic=0)
        {
            string path="Assets/Pharma/Generated/Detail_"+name+".mat";
            var m=AssetDatabase.LoadAssetAtPath<Material>(path);
            if(!m) { m=new Material(Shader.Find("Standard")); AssetDatabase.CreateAsset(m,path); }
            m.color=color; m.SetFloat("_Glossiness",smooth); m.SetFloat("_Metallic",metallic); return m;
        }
        static GameObject Shape(string name, Vector3 p, Vector3 size, Material m, PrimitiveType type=PrimitiveType.Cube)
        {
            var o=GameObject.CreatePrimitive(type); o.name=name; o.transform.position=p; o.transform.localScale=size;
            Object.DestroyImmediate(o.GetComponent<Collider>()); o.GetComponent<Renderer>().sharedMaterial=m; return o;
        }
        static GameObject Text(string name, Vector3 p, float size, Color color)
        {
            var o=new GameObject(name); o.transform.position=p;
            var t=o.AddComponent<TextMesh>(); t.text=name; t.fontSize=64; t.characterSize=size*.28f; t.anchor=TextAnchor.MiddleCenter; t.alignment=TextAlignment.Center; t.color=color;
            o.AddComponent<WorldText>().labelShader=Shader.Find("Pharma/WorldText"); return o;
        }
        public static void Room()
        {
            paint=Mat("Cabinet enamel",new Color(.09f,.35f,.36f));
            steel=Mat("Satin aluminum",new Color(.63f,.67f,.69f),.55f,.7f);
            ink=Mat("Soft black",new Color(.035f,.05f,.06f)); paper=Mat("Warm paper",new Color(.95f,.94f,.89f),.12f);
            blue=Mat("Clinical blue",new Color(.10f,.37f,.58f));
            // Fine surface variation is deterministic and saved with the scene's materials.
            foreach(string name in new[]{"Oak","Floor","Wall"})
            {
                var mat=AssetDatabase.LoadAssetAtPath<Material>("Assets/Pharma/Generated/"+name+".mat");
                string path="Assets/Pharma/Generated/Detail_"+name+"Surface.asset";
                var tex=AssetDatabase.LoadAssetAtPath<Texture2D>(path);
                if(!tex) { tex=new Texture2D(128,128,TextureFormat.RGB24,true); AssetDatabase.CreateAsset(tex,path); }
                for(int y=0;y<128;y++) for(int x=0;x<128;x++)
                {
                    float n=Mathf.PerlinNoise(x*.19f,y*.19f);
                    float v=name=="Oak"?.82f+.16f*Mathf.Sin(x*.46f+Mathf.PerlinNoise(x*.025f,y*.045f)*5):.94f+.06f*n;
                    tex.SetPixel(x,y,new Color(v,v,v));
                }
                tex.wrapMode=TextureWrapMode.Repeat; tex.Apply(); EditorUtility.SetDirty(tex);
                mat.mainTexture=tex; mat.mainTextureScale=name=="Oak"?new Vector2(2,2):new Vector2(8,8); EditorUtility.SetDirty(mat);
            }
            for(int bank=0;bank<3;bank++) for(int col=0;col<3;col++)
            {
                float x=(col-1)*1.65f,z=1.38f+bank*2.55f;
                foreach(float side in new[]{-.39f,.39f})
                {
                    Shape("Inset cabinet door",new Vector3(x+side,.22f,z-.334f),new Vector3(.75f,.32f,.018f),paint);
                    Shape("Cabinet pull",new Vector3(x+side,.28f,z-.355f),new Vector3(.19f,.017f,.026f),steel);
                }
                foreach(float side in new[]{-.75f,.75f}) for(int h=0;h<12;h++)
                    Shape("Shelf adjustment slot",new Vector3(x+side,.52f+h*.13f,z-.316f),new Vector3(.013f,.027f,.004f),ink);
                foreach(float y in new[]{.38f,.84f,1.56f,2.16f})
                    Shape("Rolled shelf lip",new Vector3(x,y-.013f,z-.327f),new Vector3(1.57f,.021f,.014f),steel);
            }
            foreach(float y in new[]{.18f,.40f})
            {
                Shape("Dispensing drawer",new Vector3(-2.55f,y,-1.283f),new Vector3(1.15f,.19f,.022f),paint);
                Shape("Drawer handle",new Vector3(-2.55f,y+.035f,-1.309f),new Vector3(.26f,.018f,.034f),steel);
            }
            Shape("Terminal display glass",new Vector3(-2.97f,1.37f,-.594f),new Vector3(.38f,.25f,.004f),blue);
            for(int i=0;i<3;i++) Shape("Display status line",new Vector3(-2.97f,1.345f+i*.031f,-.598f),new Vector3(.27f,.008f,.002f),paper);
            Shape("Keyboard",new Vector3(-2.99f,1.028f,-1.075f),new Vector3(.35f,.025f,.13f),ink);
            for(int i=0;i<10;i++) for(int j=0;j<3;j++) Shape("Key",new Vector3(-3.14f+i*.033f,1.044f,-1.12f+j*.035f),new Vector3(.026f,.009f,.026f),steel);
            Shape("Receipt printer",new Vector3(-3.02f,1.075f,-.83f),new Vector3(.22f,.13f,.19f),paper);
            Shape("Receipt slot",new Vector3(-3.02f,1.143f,-.83f),new Vector3(.15f,.006f,.018f),ink);
            Shape("Printed receipt",new Vector3(-3.02f,1.148f,-.87f),new Vector3(.13f,.002f,.09f),paper);
            for(int i=0;i<5;i++) Shape("Receipt print",new Vector3(-3.02f,1.150f,-.9f+i*.011f),new Vector3(.09f,.001f,.002f),ink);
            // Flush wall fittings and framed printed instructions, clear of walking routes.
            Shape("Back baseboard cap",new Vector3(0,.25f,7.322f),new Vector3(8,.025f,.02f),steel);
            Shape("Left skirting",new Vector3(-4.128f,.12f,2.15f),new Vector3(.02f,.24f,10.5f),paint);
            Shape("Wall clock frame",new Vector3(3.3f,2.69f,7.32f),new Vector3(.48f,.48f,.05f),steel);
            Shape("Wall clock dial",new Vector3(3.3f,2.69f,7.29f),new Vector3(.43f,.43f,.012f),paper);
            Text("12\n\n6",new Vector3(3.3f,2.69f,7.28f),.027f,Color.black);
            Shape("Clock minute hand",new Vector3(3.3f,2.76f,7.27f),new Vector3(.012f,.16f,.008f),ink);
            var hand=Shape("Clock hour hand",new Vector3(3.35f,2.69f,7.26f),new Vector3(.11f,.015f,.009f),ink); hand.transform.Rotate(0,0,-25);
            Shape("Protocol frame",new Vector3(-3.35f,2.15f,7.32f),new Vector3(.65f,.9f,.04f),steel);
            Shape("Protocol sheet",new Vector3(-3.35f,2.15f,7.294f),new Vector3(.59f,.84f,.008f),paper);
            Text("STOCK CARE\n\nCHECK EXPIRY\nVERIFY LABEL\nLOG DISPOSAL",new Vector3(-3.35f,2.15f,7.284f),.028f,new Color(.05f,.25f,.27f));
            Shape("Disposal front panel",new Vector3(2.5f,.44f,-.901f),new Vector3(.57f,.48f,.004f),paint);
            Shape("Disposal foot pedal",new Vector3(2.5f,.13f,-.94f),new Vector3(.29f,.04f,.11f),steel);
            for(int i=0;i<5;i++) Shape("Pedal tread",new Vector3(2.5f,.154f,-.979f+i*.019f),new Vector3(.25f,.004f,.006f),ink);
            foreach(float x in new[]{2.23f,2.77f}) foreach(float y in new[]{.22f,.67f})
                Shape("Bin rivet",new Vector3(x,y,-.909f),Vector3.one*.016f,steel,PrimitiveType.Sphere);
        }
        public static void Bottle(Transform root)
        {
            // Preserve the original bottle silhouette and support/contact envelope.
            for(int i=0;i<16;i++)
            {
                float a=i*Mathf.PI/8;
                var rib=Shape("Cap grip rib",root.position+new Vector3(Mathf.Sin(a)*.059f,.105f,Mathf.Cos(a)*.059f),new Vector3(.004f,.041f,.004f),paper);
                rib.transform.SetParent(root,true);
            }
            var band=Shape("Label color band",root.position+new Vector3(0,-.027f,-.054f),new Vector3(.074f,.009f,.002f),blue); band.transform.SetParent(root,true);
            for(int i=0;i<13;i++)
            {
                var bar=Shape("Bottle barcode",root.position+new Vector3(-.03f+i*.0047f,-.011f,-.055f),new Vector3(i%3==0?.0027f:.0013f,.015f,.002f),ink); bar.transform.SetParent(root,true);
            }
        }
        public static void Character(GameObject actor)
        {
            foreach(var r in actor.GetComponentsInChildren<SkinnedMeshRenderer>(true)) foreach(var m in r.sharedMaterials)
            {
                string n=m.name.ToLowerInvariant(),part=n.Contains("head")?"head":n.Contains("steto")?"stetoskop":"body";
                var normal=AssetDatabase.LoadAssetAtPath<Texture2D>("Assets/ThirdParty/Rocketbox/m153_"+part+"_normal.tga");
                if(normal) { m.SetTexture("_BumpMap",normal); m.SetFloat("_BumpScale",.65f); m.EnableKeyword("_NORMALMAP"); }
                m.SetFloat("_Glossiness",part=="head"?.23f:part=="body"?.12f:.45f); EditorUtility.SetDirty(m);
            }
            var chest=actor.GetComponentsInChildren<Transform>(true).First(t=>t.name=="Bip01 Spine2");
            // Place in the normalized actor coordinate frame, then parent to the chest bone.
            Vector3 origin=actor.transform.position;
            var cloth=Mat("Coat pocket",new Color(.94f,.95f,.95f),.10f);
            void Attach(GameObject o) { o.transform.SetParent(chest,true); }
            Attach(Shape("ID badge holder",origin+new Vector3(-.105f,1.28f,.151f),new Vector3(.09f,.115f,.008f),blue));
            Attach(Shape("ID badge paper",origin+new Vector3(-.105f,1.28f,.157f),new Vector3(.079f,.095f,.003f),paper));
            Attach(Shape("Badge photo",origin+new Vector3(-.124f,1.294f,.160f),new Vector3(.026f,.032f,.002f),blue));
            Attach(Shape("Badge clip",origin+new Vector3(-.105f,1.345f,.152f),new Vector3(.025f,.025f,.009f),steel));
            var text=Text("PHARMA\nTECHNICIAN",origin+new Vector3(-.102f,1.263f,.161f),.006f,new Color(.03f,.2f,.25f)); text.transform.rotation=Quaternion.Euler(0,180,0); Attach(text);
            Attach(Shape("Coat pen pocket",origin+new Vector3(.105f,1.334f,.167f),new Vector3(.073f,.043f,.006f),cloth));
            Attach(Shape("Pocket stitched edge",origin+new Vector3(.105f,1.355f,.175f),new Vector3(.076f,.002f,.002f),paper));
            foreach(float x in new[]{.086f,.105f,.124f})
            {
                Attach(Shape("Pocket pen",origin+new Vector3(x,1.36f,.157f),new Vector3(.008f,.0375f,.008f),x==.105f?blue:ink,PrimitiveType.Cylinder));
                Attach(Shape("Pen clip",origin+new Vector3(x+.004f,1.378f,.164f),new Vector3(.003f,.034f,.003f),steel));
            }
        }
    }
}
