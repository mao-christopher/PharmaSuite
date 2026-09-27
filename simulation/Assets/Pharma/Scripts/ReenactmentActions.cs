using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Rendering;
using TaskState = Pharma.Simulation.PharmacySimulation.TaskState;

namespace Pharma.Simulation
{
    /// <summary>M8 action driver: reaches, bottle hand-offs, looks and region highlights for the
    /// timeline's pickups and put-downs. A pickup or put-down is shown only when the rig's
    /// wrist actually reaches the contact; otherwise the region is only highlighted.
    /// Re-enactment emits no sensor events.</summary>
    public class ReenactmentActions
    {
        public const float ContactTolerance = .08f;
        const float GripDrop = .065f;
        readonly ReenactmentPlan plan;
        readonly BottleView[] bottles;
        readonly Dictionary<string, Highlight> highlights = new Dictionary<string, Highlight>();
        readonly Camera camera;
        readonly bool[] done;
        readonly Vector3[] retractFrom;
        int held = -1;
        public string HeldBottle => held < 0 ? null : bottles[held].Id;
        /// <summary>Why the guard refused this frame's reach pose (null if it wasn't refused).</summary>
        public string ReachRefusal;
        public int Shown, HighlightOnly, Followed, Pending, Downgraded, GuardHolds;
        public readonly List<string> Notes = new List<string>();

        public ReenactmentActions(ReenactmentPlan plan, Transform[] bottleObjects, Dictionary<string, GameObject> regionHighlights, Camera camera)
        {
            this.plan = plan; this.camera = camera;
            bottles = new BottleView[plan.bottles.Length];
            for (int i = 0; i < bottles.Length; i++) bottles[i] = new BottleView(bottleObjects[i], plan.bottles[i]);
            foreach (var pair in regionHighlights) highlights[pair.Key] = new Highlight(pair.Value);
            done = new bool[plan.actions.Length];
            retractFrom = new Vector3[plan.actions.Length];
            Reset();
        }

        public void Reset()
        {
            held = -1; Shown = HighlightOnly = Followed = Pending = Downgraded = GuardHolds = 0; Notes.Clear();
            for (int i = 0; i < done.Length; i++) done[i] = false;
            foreach (var b in bottles) b.Reset();
            foreach (var h in highlights.Values) h.Show(false, null);
        }

        /// <summary>The reach this frame belongs to, if any: nearest contact among overlapping windows.</summary>
        int ActiveReach(int f)
        {
            int best = -1;
            for (int i = 0; i < plan.actions.Length; i++)
            {
                var a = plan.actions[i];
                if (a.mode != "reach" || f < a.reach_start_frame || f > a.retract_end_frame) continue;
                if (best < 0 || Mathf.Abs(f - a.contact_frame) < Mathf.Abs(f - plan.actions[best].contact_frame)) best = i;
            }
            return best;
        }

        /// <summary>Wrist target: reach from rest to the contact, then retract. Null means rest.</summary>
        public Vector3? HandTarget(int f, Vector3 rest, out TaskState state)
        {
            state = TaskState.Idle;
            int i = ActiveReach(f);
            if (i < 0) return null;
            var a = plan.actions[i];
            Vector3 contact = ReenactmentPlan.V(a.contact);
            if (f <= a.contact_frame)
            {
                state = a.type == "pickup" ? TaskState.ReachingToPickUp : TaskState.ReachingToPlace;
                float reach = Ease((float)(f - a.reach_start_frame) / Mathf.Max(1, a.contact_frame - a.reach_start_frame));
                return Vector3.Lerp(rest, contact, reach) + Vector3.up * (.045f * Mathf.Sin(Mathf.PI * reach));
            }
            state = TaskState.Retracting;
            Vector3 from = done[i] ? retractFrom[i] : contact;
            return Vector3.Lerp(from, rest, Ease((float)(f - a.contact_frame) / Mathf.Max(1, a.retract_end_frame - a.contact_frame)));
        }

        /// <summary>Applies every action whose contact frame has arrived, then moves bottles.</summary>
        public void AfterPose(int f, Vector3 wrist, bool handPosed)
        {
            for (int i = 0; i < plan.actions.Length; i++)
            {
                if (done[i] || plan.actions[i].contact_frame > f) continue;
                done[i] = true; retractFrom[i] = wrist;
                Apply(plan.actions[i], wrist, handPosed);
            }
            UpdateHighlights(f);
            foreach (var b in bottles) b.Update(f, wrist - Vector3.up * GripDrop, plan.fps, camera);
        }

        void Apply(PlanAction a, Vector3 wrist, bool handPosed)
        {
            var b = a.bottle >= 0 ? bottles[a.bottle] : null;
            if (a.mode == "none") return;
            if (a.pending)
            {
                Pending++;
                if (b == null) return;
                if (a.type == "pickup") { b.SetLook("pending"); return; }
                if (held == a.bottle) held = -1;
                if (ReenactmentPlan.Has(a.to)) b.Place(ReenactmentPlan.V(a.to), "pending", false, 0);
                else b.Hide();
                return;
            }
            if (a.mode == "follow")
            {
                Followed++;
                Follow(a, b);
                return;
            }
            // Highlight-only: no reach is animated, but the bottle still ends up where the
            // dashboard has it (off the shelf after a pickup, at its destination after a put-down).
            if (a.mode == "highlight") { HighlightOnly++; Follow(a, b); return; }
            bool reached = handPosed && Vector3.Distance(wrist, ReenactmentPlan.V(a.contact)) <= ContactTolerance;
            if (a.type == "pickup")
            {
                if (reached && b != null && b.OnDisplay && held < 0) { held = a.bottle; b.Hold(); Shown++; return; }
                Downgraded++; HighlightOnly++;
                Notes.Add($"{a.action_id}: pickup not shown (wrist {Vector3.Distance(wrist, ReenactmentPlan.V(a.contact)):0.00} m from contact" +
                          (string.IsNullOrEmpty(ReachRefusal) ? ")" : $"; reach refused: {ReachRefusal})"));
                Follow(a, b);
                return;
            }
            if (b == null || held != a.bottle)
            {
                // The bottle isn't visibly in the hand (for example its pickup was out of view).
                Followed++; Follow(a, b); return;
            }
            if (!reached)
            {
                Downgraded++; HighlightOnly++;
                Notes.Add($"{a.action_id}: put-down not shown (wrist {Vector3.Distance(wrist, ReenactmentPlan.V(a.contact)):0.00} m from contact" +
                          (string.IsNullOrEmpty(ReachRefusal) ? ")" : $"; reach refused: {ReachRefusal})"));
                Follow(a, b);
                return;
            }
            held = -1; Shown++;
            b.Place(ReenactmentPlan.V(a.to), a.look, a.drop, a.contact_frame, wrist - Vector3.up * GripDrop);
        }

        void Follow(PlanAction a, BottleView b)
        {
            if (b == null) return;
            if (a.type == "pickup") { if (held == a.bottle) held = -1; b.Hide(); return; }
            if (held == a.bottle) held = -1;
            if (ReenactmentPlan.Has(a.to)) b.Place(ReenactmentPlan.V(a.to), a.look, false, 0);
        }

        void UpdateHighlights(int f)
        {
            var active = new Dictionary<string, string>();
            int hold = Mathf.RoundToInt(plan.fps);
            foreach (var a in plan.actions)
                if (a.mode != "none" && !string.IsNullOrEmpty(a.region_id) && f >= a.reach_start_frame && f <= a.contact_frame + hold)
                    active[a.region_id] = a.pending ? "pending" : a.look;
            foreach (var pair in highlights)
                pair.Value.Show(active.TryGetValue(pair.Key, out var look), look);
        }

        static float Ease(float u) { u=Mathf.Clamp01(u); return u*u*u*(u*(u*6-15)+10); }

        public static Color LookColor(string look)
        {
            switch (look)
            {
                case "misplaced": return new Color(.80f, .10f, .08f);
                case "counter": return new Color(1f, .68f, .10f);
                case "disposed": return new Color(.45f, .45f, .45f);
                default: return new Color(.52f, .24f, .06f);
            }
        }

        public static void SetFade(Material m, float alpha)
        {
            m.SetFloat("_Mode", 2);
            m.SetInt("_SrcBlend", (int)BlendMode.SrcAlpha); m.SetInt("_DstBlend", (int)BlendMode.OneMinusSrcAlpha);
            m.SetInt("_ZWrite", 0); m.DisableKeyword("_ALPHATEST_ON"); m.EnableKeyword("_ALPHABLEND_ON");
            m.DisableKeyword("_ALPHAPREMULTIPLY_ON"); m.renderQueue = 3000;
            var c = m.color; c.a = alpha; m.color = c;
        }

        class Highlight
        {
            readonly GameObject box;
            readonly Material material;
            public Highlight(GameObject box) { this.box = box; material = box.GetComponent<Renderer>().sharedMaterial; }
            public void Show(bool on, string look)
            {
                box.SetActive(on);
                if (!on) return;
                var c = look == "pending" ? new Color(.9f, .92f, .95f) : look == "normal" || look == null ? new Color(.15f, .75f, .8f) : LookColor(look);
                c.a = .22f; material.color = c;
            }
        }

        /// <summary>One bottle object. It keeps its medication identity whatever happens to it.</summary>
        class BottleView
        {
            readonly Transform root;
            readonly PlanBottle data;
            readonly Renderer[] renderers;
            readonly Material[][] opaque, faded;
            readonly Renderer body;
            readonly GameObject marker;
            public string Id => "bottle-" + data.index;
            enum Where { Placed, Held, Hidden, Dropping }
            Where where;
            Vector3 position, dropFrom;
            int dropFrame;

            public BottleView(Transform root, PlanBottle data)
            {
                this.root = root; this.data = data;
                var list = new List<Renderer>();
                foreach (var r in root.GetComponentsInChildren<Renderer>(true))
                    if (r.GetComponent<TextMesh>() == null) list.Add(r);
                renderers = list.ToArray();
                opaque = new Material[renderers.Length][]; faded = new Material[renderers.Length][];
                for (int i = 0; i < renderers.Length; i++)
                {
                    opaque[i] = renderers[i].sharedMaterials;
                    faded[i] = new Material[opaque[i].Length];
                    for (int k = 0; k < opaque[i].Length; k++) { faded[i][k] = new Material(opaque[i][k]); SetFade(faded[i][k], .35f); }
                    if (renderers[i].name == "Amber container") body = renderers[i];
                }
                if (body != null)
                {
                    int i = System.Array.IndexOf(renderers, body);
                    opaque[i] = new[]{ new Material(opaque[i][0]) };
                }
                var q = root.Find("Pending marker");
                marker = q ? q.gameObject : null;
            }

            public bool OnDisplay => where == Where.Placed && root.gameObject.activeSelf;

            public void Reset()
            {
                if (ReenactmentPlan.Has(data.position)) { position = ReenactmentPlan.V(data.position); where = data.hidden ? Where.Hidden : Where.Placed; }
                else where = Where.Hidden;
                SetLook(data.look);
                root.gameObject.SetActive(where != Where.Hidden);
                root.position = position; root.rotation = Quaternion.identity;
            }

            public void SetLook(string look)
            {
                bool pending = look == "pending";
                for (int i = 0; i < renderers.Length; i++) renderers[i].sharedMaterials = pending ? faded[i] : opaque[i];
                if (body != null && !pending) body.sharedMaterials[0].color = LookColor(look);
                if (marker) marker.SetActive(pending);
            }

            public void Hold() { where = Where.Held; root.gameObject.SetActive(true); }
            public void Hide() { where = Where.Hidden; root.gameObject.SetActive(false); }

            public void Place(Vector3 to, string look, bool drop, int frame, Vector3? from = null)
            {
                position = to; SetLook(look); root.gameObject.SetActive(true);
                if (drop && from.HasValue) { where = Where.Dropping; dropFrom = from.Value; dropFrame = frame; }
                else where = Where.Placed;
            }

            public void Update(int f, Vector3 hand, float fps, Camera camera)
            {
                if (where == Where.Held) root.position = hand;
                else if (where == Where.Dropping)
                {
                    float elapsed = (f - dropFrame) / fps;
                    // Gravity inside the hollow bin, stopping on its bottom.
                    float y = Mathf.Max(position.y, dropFrom.y - 4.905f * elapsed * elapsed);
                    root.position = new Vector3(position.x, y, position.z);
                    if (y <= position.y) where = Where.Placed;
                }
                else root.position = position;
                root.rotation = Quaternion.identity;
                if (marker && marker.activeSelf && camera)
                    marker.transform.rotation = Quaternion.LookRotation(marker.transform.position - camera.transform.position);
            }
        }
    }
}
