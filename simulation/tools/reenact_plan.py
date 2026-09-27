"""Turn a dashboard re-enactment timeline (timeline/1) into a Unity plan (stdlib only).

The plan is everything Unity needs, already in Unity world coordinates:
- the rebuilt room's visual boxes, which of them collide, and the medication labels (M7)
- the registered camera and the room points used for the alignment check
- one body sample per video frame: tracked floor position and facing, held while the
  technician is out of view, blended or cut on reappearing, pushed out of solids, with
  a short step toward reachable contacts (M8)
- the bottles before the recording and, per action, what the hand and bottle should do

Room frame (backend): meters, right-handed, Y up. Unity = (x, y, -z) of the room frame.
Nothing here comes from the simulator's scripted outcomes or evaluator-only files; the
timeline itself carries only dashboard evidence.
"""
import json
import math
from pathlib import Path

SCHEMA = "reenactment-plan/1"
BODY_RADIUS_M = 0.26 + 0.02  # CollisionWorld.BodyRadius plus a small margin
BLEND_LIMIT_M = 1.0  # reappearing closer than this blends, farther cuts
BLEND_S = 0.5
REACH_LEAD_S = 0.6  # the hand starts moving this long before contact
RETRACT_S = 0.5
STEP_MAX_M = 0.3  # lean or step at most this far toward a contact
LEAN_M = 0.12  # remaining offset the reach itself absorbs after stepping
REACH_HEIGHT_M = (0.75, 1.75)  # wrist heights the rig can reach standing
# The demo's stand offset: contact 0.32 m to the right and 0.49 m ahead of the body root.
STAND_RIGHT_M, STAND_AHEAD_M = 0.32, 0.49
STAND_BEARING_DEG = math.degrees(math.atan2(STAND_RIGHT_M, STAND_AHEAD_M))
GRIP_DROP_M = 0.065  # bottle center below the wrist (as in the demo)
BOTTLE_HALF_HEIGHT_M = 0.10
BOTTLE_SPACING_M = 0.13
SURFACE_PARTS = ("board", "plinth", "bottom")
LOOK_BY_OUTCOME = {"returned": "normal", "in_hand": "normal", "misplaced": "misplaced",
                   "counter": "counter", "disposed": "disposed", "pending": "pending"}


class PlanError(ValueError):
    pass


# ---------------------------------------------------------------- geometry

def to_unity(p):
    return [float(p[0]), float(p[1]), -float(p[2])]


def box_to_unity(box):
    """A room box {center, size, yaw_deg} as the same box in Unity (its room +Z becomes local -Z)."""
    return {"center": to_unity(box["center"]), "size": [float(v) for v in box["size"]],
            "yaw_deg": -float(box.get("yaw_deg", 0.0))}


def box_axes(yaw_deg):
    """Room-frame local X and Z axes of a box (x, z components)."""
    a = math.radians(yaw_deg)
    c, s = math.cos(a), math.sin(a)
    return (c, -s), (s, c)


def box_corners(box):
    ax, az = box_axes(box.get("yaw_deg", 0.0))
    cx, cy, cz = box["center"]
    out = []
    for i in range(8):
        hx, hy, hz = [(1 if i >> k & 1 else -1) * box["size"][k] / 2 for k in range(3)]
        out.append([cx + ax[0] * hx + az[0] * hz, cy + hy, cz + ax[1] * hx + az[1] * hz])
    return out


def local_point(box, lx, y, lz):
    """A point in a room box's local X/Z (meters from its center) at height y."""
    ax, az = box_axes(box.get("yaw_deg", 0.0))
    return [box["center"][0] + ax[0] * lx + az[0] * lz, y, box["center"][2] + ax[1] * lx + az[1] * lz]


class Rect:
    """A solid's floor footprint (room x, z)."""

    def __init__(self, name, box):
        self.name = name
        self.cx, self.cz = box["center"][0], box["center"][2]
        self.hx, self.hz = box["size"][0] / 2, box["size"][2] / 2
        self.ax, self.az = box_axes(box.get("yaw_deg", 0.0))

    def local(self, x, z):
        dx, dz = x - self.cx, z - self.cz
        return dx * self.ax[0] + dz * self.ax[1], dx * self.az[0] + dz * self.az[1]

    def world(self, lx, lz):
        return (self.cx + self.ax[0] * lx + self.az[0] * lz, self.cz + self.ax[1] * lx + self.az[1] * lz)

    def distance(self, x, z):
        lx, lz = self.local(x, z)
        qx, qz = abs(lx) - self.hx, abs(lz) - self.hz
        if qx <= 0 and qz <= 0:
            return max(qx, qz)
        return math.hypot(max(qx, 0.0), max(qz, 0.0))

    def push(self, x, z, r):
        """The nearest point at least r from this footprint."""
        lx, lz = self.local(x, z)
        qx, qz = abs(lx) - self.hx, abs(lz) - self.hz
        sx, sz = (1 if lx >= 0 else -1), (1 if lz >= 0 else -1)
        if qx <= 0 and qz <= 0:
            if qx > qz:
                lx = sx * (self.hx + r)
            else:
                lz = sz * (self.hz + r)
        else:
            ox, oz = max(qx, 0.0), max(qz, 0.0)
            d = math.hypot(ox, oz)
            lx, lz = lx + sx * ox / d * (r - d), lz + sz * oz / d * (r - d)
        return self.world(lx, lz)


def solid_rects(rebuilt):
    """Walking obstacles: wall spans (openings stay open) and every other solid envelope."""
    rects = []
    parts = {}
    for b in rebuilt.get("boxes", []):
        parts.setdefault(b["object_id"], []).append(b)
    for obj in rebuilt.get("objects", []):
        if not obj.get("solid"):
            continue
        if obj["kind"] == "wall":
            spans = [b for b in parts.get(obj["id"], []) if b["part"].startswith("span")]
            for b in spans or [dict(obj["box"], part="span")]:
                rects.append(Rect(f"{obj['id']}/{b['part']}", b))
        else:
            rects.append(Rect(obj["id"], obj["box"]))
    return rects


def push_out(x, z, rects, r=BODY_RADIUS_M):
    """(x, z, moved) outside every footprint, or the input if it can't be freed."""
    moved = False
    for _ in range(8):
        hits = [rect for rect in rects if rect.distance(x, z) < r - 1e-6]
        if not hits:
            return x, z, moved
        worst = min(hits, key=lambda rect: rect.distance(x, z))
        x, z = worst.push(x, z, r)
        moved = True
    return x, z, moved


def blocked(a, b, rects, r=BODY_RADIUS_M, step=0.04):
    """True if a body moving straight from a to b (room x, z) would pass through a solid."""
    n = max(1, int(math.hypot(b[0] - a[0], b[1] - a[1]) / step))
    for i in range(1, n + 1):
        u = i / n
        x, z = a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u
        if any(rect.distance(x, z) < r - 1e-3 for rect in rects):
            return True
    return False


def yaw_delta(a, b):
    return (b - a + 180) % 360 - 180


def ease(u):
    u = min(max(u, 0.0), 1.0)
    return u * u * u * (u * (u * 6 - 15) + 10)


# ---------------------------------------------------------------- camera

def quaternion_from_matrix(m):
    """xyzw of a proper rotation matrix (rows)."""
    t = m[0][0] + m[1][1] + m[2][2]
    if t > 0:
        s = math.sqrt(t + 1) * 2
        return [(m[2][1] - m[1][2]) / s, (m[0][2] - m[2][0]) / s, (m[1][0] - m[0][1]) / s, s / 4]
    if m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = math.sqrt(1 + m[0][0] - m[1][1] - m[2][2]) * 2
        return [s / 4, (m[0][1] + m[1][0]) / s, (m[0][2] + m[2][0]) / s, (m[2][1] - m[1][2]) / s]
    if m[1][1] > m[2][2]:
        s = math.sqrt(1 + m[1][1] - m[0][0] - m[2][2]) * 2
        return [(m[0][1] + m[1][0]) / s, s / 4, (m[1][2] + m[2][1]) / s, (m[0][2] - m[2][0]) / s]
    s = math.sqrt(1 + m[2][2] - m[0][0] - m[1][1]) * 2
    return [(m[0][2] + m[2][0]) / s, (m[1][2] + m[2][1]) / s, s / 4, (m[1][0] - m[0][1]) / s]


def main_camera(timeline):
    layout = (timeline.get("track", {}).get("camera") or {}).get("layout_id")
    for cam in timeline.get("cameras", []):
        if cam.get("layout_id") == layout:
            if not cam.get("registered"):
                raise PlanError(f"Camera view {layout} is not registered; register it on the Room page.")
            return cam
    raise PlanError(f"The timeline has no camera for the tracked view {layout!r}.")


def intrinsics_at(cam, frame_size):
    """fx, fy, cx, cy scaled from the registration's frame to the render frame."""
    k = cam["intrinsics"]
    rw, rh = cam.get("frame_size") or frame_size
    sx, sy = frame_size[0] / rw, frame_size[1] / rh
    return k["fx"] * sx, k["fy"] * sy, k["cx"] * sx, k["cy"] * sy


def unity_camera(cam, frame_size):
    """Unity camera pose from an OpenCV registration: R_unity = F R^T D, position = F C."""
    R = cam["rotation"]
    t = cam["translation"]
    # Camera center in the room frame: C = -R^T t.
    center = [-sum(R[r][c] * t[r] for r in range(3)) for c in range(3)]
    D = [1, -1, 1]
    F = [1, 1, -1]
    rot = [[F[i] * R[j][i] * D[j] for j in range(3)] for i in range(3)]
    fx, fy, cx, cy = intrinsics_at(cam, frame_size)
    w, h = frame_size
    return {"position": to_unity(center), "rotation_xyzw": quaternion_from_matrix(rot),
            "vertical_fov_deg": math.degrees(2 * math.atan((h / 2) / fy)),
            "lens_shift": [-(cx - w / 2) / w, (cy - h / 2) / h], "fx_over_fy": fx / fy,
            "width": int(w), "height": int(h), "layout_id": cam.get("layout_id")}


def project_registration(cam, frame_size, point):
    """A room point in top-left pixels through the registration (plain pinhole math)."""
    R, t = cam["rotation"], cam["translation"]
    x = [sum(R[r][c] * point[c] for c in range(3)) + t[r] for r in range(3)]
    fx, fy, cx, cy = intrinsics_at(cam, frame_size)
    if x[2] <= 1e-6:
        return None
    return [fx * x[0] / x[2] + cx, fy * x[1] / x[2] + cy, x[2]]


# ---------------------------------------------------------------- room

def cutaway(box, camera_position, inside):
    """Is the camera on the outer side of this wall box? Then the footage shows the room past
    it, so the wall isn't drawn (a dollhouse cutaway); it still blocks walking."""
    a = math.radians(box["yaw_deg"])
    nx, nz = math.sin(a), math.cos(a)  # the wall's thin axis (box +Z) in the room frame
    side = lambda x, z: (x - box["center"][0]) * nx + (z - box["center"][2]) * nz
    cam, room = side(camera_position[0], camera_position[2]), side(inside[0], inside[1])
    return cam * room < 0 and abs(cam) > box["size"][2] / 2


def scene_boxes(rebuilt, camera_position=None):
    """Visual boxes for Unity. `collider`: solid | floor | none. Labels carry their text.
    Walls between the camera and the room are marked `cutaway` (not drawn)."""
    solid_ids = {o["id"] for o in rebuilt.get("objects", []) if o.get("solid")}
    kinds = {o["id"]: o["kind"] for o in rebuilt.get("objects", [])}
    outline = rebuilt.get("floor_polygon") or []
    inside = (sum(p[0] for p in outline) / len(outline), sum(p[1] for p in outline) / len(outline)) if outline else None
    out = []
    for b in rebuilt.get("boxes", []):
        kind = kinds.get(b["object_id"])
        is_label = b["part"].startswith("label") or "label" in b
        if kind == "floor":
            collider = "floor"
        elif b["object_id"] in solid_ids and not is_label:
            collider = "solid"
        else:
            collider = "none"
        entry = dict(box_to_unity(b), name=b["id"], color=b.get("color", "#cccccc"), collider=collider)
        entry["cutaway"] = bool(kind == "wall" and camera_position is not None and inside is not None
                                and cutaway(b, camera_position, inside))
        if b.get("label"):
            entry["label"] = b["label"]
        out.append(entry)
    return out


def region_surfaces(rebuilt, region):
    """Top heights of horizontal parts (boards, plinths, bin bottoms, counter tops) inside a region."""
    lo = region["box"]["center"][1] - region["box"]["size"][1] / 2
    hi = region["box"]["center"][1] + region["box"]["size"][1] / 2
    owners = {o["id"] for o in rebuilt.get("objects", []) if region["region_id"] in o.get("region_ids", [])}
    parts = SURFACE_PARTS + (("top",) if region["region_type"] == "dispensing_counter" else ())
    counter = region["region_type"] == "dispensing_counter"
    tops = []
    for b in rebuilt.get("boxes", []):
        if b["object_id"] in owners and b["part"].split("_")[0] in parts:
            top = b["center"][1] + b["size"][1] / 2
            # A counter's region usually ends at its top; shelves need room above a board.
            if lo - 0.05 <= top <= hi + (0.05 if counter else -0.05):
                tops.append(top)
    return sorted(tops, reverse=counter)


def region_slots(rebuilt, region, count=24):
    """Bottle centers (room frame) on the region's lowest surface (a counter's top), front row first."""
    box = region["box"]
    tops = region_surfaces(rebuilt, region)
    kind = region["region_type"]
    floor = tops[0] if tops else box["center"][1] - box["size"][1] / 2
    y = floor + BOTTLE_HALF_HEIGHT_M
    w, d = box["size"][0], box["size"][2]
    if kind == "dispensing_counter" and d > w:
        # Counters: slots along the longer side, down the middle of the top.
        along, across, swap = d, w, True
    else:
        along, across, swap = w, d, False
    per_row = max(1, int((along - 0.1) / BOTTLE_SPACING_M))
    rows = max(1, int((across - 0.08) / BOTTLE_SPACING_M)) if kind != "disposal" else 1
    # Columns fill from the middle outward, so a few bottles sit centered in their region.
    order = sorted(range(per_row), key=lambda c: (abs(c - (per_row - 1) / 2), c))
    slots = []
    for n in range(count):
        row, k = divmod(n, per_row)
        if row >= rows:
            row = rows - 1
        offset = (order[k] - (per_row - 1) / 2) * BOTTLE_SPACING_M
        if kind == "designated_shelf":
            depth = d / 2 - 0.09 - row * BOTTLE_SPACING_M  # front row near the open (+Z) face
        else:
            depth = (row - (rows - 1) / 2) * BOTTLE_SPACING_M
        lx, lz = (depth, offset) if swap else (offset, depth)
        slots.append(local_point(box, lx, y, lz))
    return slots


# ---------------------------------------------------------------- body

FLAG_VISIBLE, FLAG_IN_VIEW, FLAG_CUT, FLAG_PUSHED, FLAG_STEP = 1, 2, 4, 8, 16


def body_frames(timeline, rects):
    """Per-frame room-frame body samples [x, z, yaw_deg, flags].

    Out-of-view frames hold the last shown pose (hidden before the first sighting). On
    reappearing the body blends over BLEND_S if it moved less than BLEND_LIMIT_M along a
    free straight line, otherwise it cuts. A tracked jump through a solid also cuts.
    """
    fps = float(timeline["fps"])
    fields = timeline["track"]["fields"]
    ix, iz, iyaw = fields.index("x"), fields.index("z"), fields.index("yaw_deg")
    blend_n = max(1, int(round(BLEND_S * fps)))
    out, stats = [], {"pushed_frames": 0, "out_of_view_frames": 0, "cuts": 0, "blends": 0}
    last, gap, blend = None, False, None
    for f, row in enumerate(timeline["track"]["frames"]):
        if row is None:
            stats["out_of_view_frames"] += 1
            out.append([0.0, 0.0, 0.0, 0] if last is None else [last[0], last[1], last[2], FLAG_VISIBLE])
            gap, blend = True, None
            continue
        x, z, yaw = float(row[ix]), float(row[iz]), float(row[iyaw])
        x, z, pushed = push_out(x, z, rects)
        stats["pushed_frames"] += pushed
        flags = FLAG_VISIBLE | FLAG_IN_VIEW | (FLAG_PUSHED if pushed else 0)
        if last is None:
            flags |= FLAG_CUT
        elif gap:
            if math.hypot(x - last[0], z - last[1]) < BLEND_LIMIT_M and not blocked(last[:2], (x, z), rects):
                blend = (last, f)
                stats["blends"] += 1
            else:
                flags |= FLAG_CUT
        elif blocked(last[:2], (x, z), rects):
            flags |= FLAG_CUT
            blend = None
        gap = False
        if blend is not None:
            start, first = blend
            u = ease((f - first + 1) / blend_n)
            x, z = start[0] + (x - start[0]) * u, start[1] + (z - start[1]) * u
            yaw = start[2] + yaw_delta(start[2], yaw) * u
            if f - first + 1 >= blend_n:
                blend = None
        stats["cuts"] += bool(flags & FLAG_CUT)
        last = (x, z, yaw)
        out.append([x, z, yaw, flags])
    stats["never_seen"] = last is None
    return out, stats


def reach_pose(body, contact, rects):
    """(mode, stand x, z, yaw) for reaching `contact` from the tracked body pose.

    `reach`: stand within STEP_MAX_M (+ a lean) of the demo's stand offset;
    `highlight`: the contact is too far, too low/high, or the step would enter a solid.
    """
    x, z, yaw = body
    if not REACH_HEIGHT_M[0] <= contact[1] <= REACH_HEIGHT_M[1]:
        return "highlight", x, z, yaw
    bearing = math.degrees(math.atan2(contact[0] - x, contact[2] - z))
    face = bearing + STAND_BEARING_DEG
    a = math.radians(face)
    fwd, right = (math.sin(a), math.cos(a)), (-math.cos(a), math.sin(a))
    ideal = (contact[0] - fwd[0] * STAND_AHEAD_M - right[0] * STAND_RIGHT_M,
             contact[2] - fwd[1] * STAND_AHEAD_M - right[1] * STAND_RIGHT_M)
    dx, dz = ideal[0] - x, ideal[1] - z
    d = math.hypot(dx, dz)
    if d > STEP_MAX_M + LEAN_M:
        return "highlight", x, z, yaw
    k = min(1.0, STEP_MAX_M / d) if d > 1e-9 else 0.0
    sx, sz = x + dx * k, z + dz * k
    px, pz, pushed = push_out(sx, sz, rects)
    if pushed and math.hypot(px - ideal[0], pz - ideal[1]) > LEAN_M:
        return "highlight", x, z, yaw
    if blocked((x, z), (px, pz), rects):
        return "highlight", x, z, yaw
    return "reach", px, pz, face


# ---------------------------------------------------------------- actions and bottles

class Stock:
    """Dashboard bookkeeping of bottle objects: where each one is after every action."""

    def __init__(self, rebuilt, regions):
        self.regions = {r["region_id"]: r for r in regions}
        self.slots = {rid: region_slots(rebuilt, r) for rid, r in self.regions.items()}
        self.used = {rid: [None] * len(s) for rid, s in self.slots.items()}
        self.bottles = []
        self.notes = {"bottles_without_region": 0, "spawned_bottles": 0, "full_regions": 0}

    def add(self, medication, region_id, look="normal", hidden=False, bottle_id=None, near=None):
        index = len(self.bottles)
        entry = {"index": index, "medication_key": medication, "bottle_id": bottle_id, "region_id": None,
                 "position": None, "look": look, "hidden": hidden, "held": False}
        self.bottles.append(entry)
        if region_id is not None:
            self.place(entry, region_id, near)
        return entry

    def place(self, bottle, region_id, near=None):
        """Put a bottle in the region's first free slot, or the free slot nearest `near` (x, z)."""
        self.release(bottle)
        used = self.used[region_id]
        free = [i for i, v in enumerate(used) if v is None]
        if not free:
            self.notes["full_regions"] += 1
            slot = len(used) - 1
        elif near is None:
            slot = free[0]
        else:
            slots = self.slots[region_id]
            slot = min(free, key=lambda i: math.hypot(slots[i][0] - near[0], slots[i][2] - near[1]))
        used[slot] = bottle["index"]
        bottle.update(region_id=region_id, position=self.slots[region_id][slot], slot=slot, held=False)
        return bottle["position"]

    def release(self, bottle):
        if bottle.get("region_id") is not None and bottle.get("slot") is not None:
            if self.used[bottle["region_id"]][bottle["slot"]] == bottle["index"]:
                self.used[bottle["region_id"]][bottle["slot"]] = None
        bottle.update(region_id=None, slot=None)

    def find(self, region_id, medication, bottle_id=None, near=None):
        """The bottle this action most likely handled: by ID, then medication (a misplaced
        bottle keeps its identity whichever session moved it last), then any; within a tier,
        the one nearest the technician (x, z) if given."""
        here = [b for b in self.bottles if b["region_id"] == region_id and not b["held"]]
        if near is not None:
            here.sort(key=lambda b: math.hypot(b["position"][0] - near[0], b["position"][2] - near[1]))
        for test in (lambda b: bottle_id and b["bottle_id"] == bottle_id,
                     lambda b: b["medication_key"] == medication,
                     lambda b: b["bottle_id"] is None, lambda b: True):
            for b in here:
                if test(b):
                    return b
        return None


def plan_actions(timeline, frames, rects, stock):
    fps = float(timeline["fps"])
    n = len(frames)
    held = {}  # bottle_id -> bottle entry (dashboard: with the technician)
    plans, counts = [], {"reach": 0, "highlight": 0, "follow": 0, "pending": 0, "not_applied": 0,
                         "pending_unlocated": 0}
    for action in timeline.get("actions", []):
        contact_f = min(n - 1, max(0, int(round(action["contact_ms"] * fps / 1000))))
        entry = {"action_id": action["action_id"], "type": action["type"], "status": action["status"],
                 "outcome": action.get("outcome"), "bottle_id": action.get("bottle_id"),
                 "medication_key": action.get("medication_key"), "contact_frame": contact_f,
                 "reach_start_frame": max(0, contact_f - int(round(REACH_LEAD_S * fps))),
                 "retract_end_frame": min(n - 1, contact_f + int(round(RETRACT_S * fps))),
                 "region_id": None, "mode": "none", "bottle": -1, "contact": None, "to": None,
                 "look": None, "drop": False}
        plans.append(entry)
        status = action["status"]
        if status == "not_applied":
            counts["not_applied"] += 1
            continue
        region_id = action.get("region_id") if status != "pending" else action.get("suggested_region_id")
        if region_id is not None and region_id not in stock.regions:
            region_id = None
        entry["region_id"] = region_id
        entry["look"] = LOOK_BY_OUTCOME.get(action.get("outcome") or "", "normal")
        med = action.get("medication_key")
        bid = action.get("bottle_id")
        bottle = None
        # The technician handles the bottle (or free slot) nearest where the track has them.
        body = frames[contact_f]
        near = (body[0], body[1]) if body[3] & FLAG_VISIBLE else None
        if action["type"] == "pickup":
            if region_id is not None:
                bottle = stock.find(region_id, med, bid, near)
                if bottle is None and status != "pending":
                    bottle = stock.add(med, region_id, near=near)
                    stock.notes["spawned_bottles"] += 1
            if bottle is not None:
                entry["contact"] = [bottle["position"][0], bottle["position"][1] + GRIP_DROP_M, bottle["position"][2]]
                if status != "pending":
                    bottle["bottle_id"] = bid
                    stock.release(bottle)
                    bottle["held"] = True
                    held[bid] = bottle
        else:
            bottle = held.pop(bid, None)
            if bottle is None and status != "pending":
                bottle = stock.add(med, None, hidden=True, bottle_id=bid)
                bottle["held"] = True
                stock.notes["spawned_bottles"] += 1
            if region_id is not None:
                if bottle is None:  # a pending put-down of a bottle nobody saw picked up
                    bottle = stock.add(med, None, hidden=True, bottle_id=bid)
                to = stock.place(bottle, region_id, near)
                entry["to"] = list(to)
                entry["drop"] = stock.regions[region_id]["region_type"] == "disposal"
                if entry["drop"]:
                    top = stock.regions[region_id]["box"]["center"][1] + stock.regions[region_id]["box"]["size"][1] / 2
                    entry["contact"] = [to[0], top + 0.12, to[2]]
                else:
                    entry["contact"] = [to[0], to[1] + GRIP_DROP_M, to[2]]
                bottle["held"] = False
        entry["bottle"] = -1 if bottle is None else bottle["index"]
        if status == "pending" and region_id is None:
            counts["pending_unlocated"] += 1
        # Presentation mode: out of view follows the dashboard; in view reaches or highlights.
        if not body[3] & FLAG_IN_VIEW:
            entry["mode"] = "follow"
        elif entry["contact"] is None:
            entry["mode"] = "highlight"
        else:
            mode, sx, sz, face = reach_pose(body[:3], entry["contact"], rects)
            entry["mode"] = mode
            if mode == "reach":
                entry["stand"] = [sx, sz, face]
        counts[entry["mode"] if status != "pending" else "pending"] += 1
        entry["pending"] = status == "pending"
    return plans, counts


def apply_steps(frames, plans):
    """Blend the body toward each reach's stand point over the reach, and back after it."""
    for p in plans:
        if p["mode"] != "reach":
            continue
        start, contact, end = p["reach_start_frame"], p["contact_frame"], p["retract_end_frame"]
        sx, sz, face = p["stand"]
        for f in range(start, end + 1):
            w = ease((f - start) / max(1, contact - start)) if f <= contact else ease((end - f) / max(1, end - contact))
            x, z, yaw, flags = frames[f]
            if not flags & FLAG_IN_VIEW:
                continue
            frames[f] = [x + (sx - x) * w, z + (sz - z) * w, yaw + yaw_delta(yaw, face) * w, flags | FLAG_STEP]


# ---------------------------------------------------------------- plan

def build_plan(timeline):
    if timeline.get("schema") != "timeline/1":
        raise PlanError(f"Expected a timeline/1 file, got {timeline.get('schema')!r}")
    rebuilt = timeline.get("rebuilt") or {}
    if not rebuilt.get("boxes"):
        raise PlanError("The timeline has no rebuilt room; rebuild the room on the Room page first.")
    frame_size = [int(v) for v in timeline["frame_size"]]
    frames_in = timeline["track"]["frames"]
    if not frames_in:
        raise PlanError("The timeline's floor track has no frames.")
    cam = main_camera(timeline)
    rects = solid_rects(rebuilt)
    regions = (timeline.get("room") or {}).get("regions") or rebuilt.get("regions") or []
    frames, body_stats = body_frames(timeline, rects)
    stock = Stock(rebuilt, regions)
    for region_id, meds in sorted(((timeline.get("start_counts") or {}).get("shelves") or {}).items()):
        for med, count in sorted(meds.items()):
            for _ in range(int(count)):
                if region_id in stock.regions:
                    stock.add(med, region_id)
                else:
                    stock.notes["bottles_without_region"] += 1
    counters = [r["region_id"] for r in regions if r["region_type"] == "dispensing_counter"]
    for med, count in sorted(((timeline.get("start_counts") or {}).get("counter") or {}).items()):
        for _ in range(int(count)):
            if counters:
                stock.add(med, counters[0], look="counter")
            else:
                stock.notes["bottles_without_region"] += 1
    initial = [dict(b, position=list(b["position"]) if b["position"] else None) for b in stock.bottles]
    plans, action_counts = plan_actions(timeline, frames, rects, stock)
    apply_steps(frames, plans)
    # Bottles created while planning (spawned for unseen pickups) start hidden.
    bottles = initial + [dict(b, hidden=True, position=None) for b in stock.bottles[len(initial):]]
    camera = unity_camera(cam, frame_size)
    R, t = cam["rotation"], cam["translation"]
    boxes = scene_boxes(rebuilt, [-sum(R[r][c] * t[r] for r in range(3)) for c in range(3)])
    alignment = []
    for r in regions:
        for corner in box_corners(r["box"]):
            px = project_registration(cam, frame_size, corner)
            if px is not None:
                alignment.append({"region_id": r["region_id"], "unity": to_unity(corner), "pixel": px[:2]})
    return {
        "schema": SCHEMA,
        "recording": timeline.get("recording"),
        "inputs_sha256": timeline.get("inputs_sha256"),
        "fps": float(timeline["fps"]),
        "width": frame_size[0], "height": frame_size[1],
        "frame_count": len(frames),
        "camera": camera,
        "height_m": float(rebuilt.get("height_m", 2.5)),
        "boxes": boxes,
        "regions": [dict(box_to_unity(r["box"]), region_id=r["region_id"], region_type=r["region_type"],
                         medication_key=r.get("medication_key")) for r in regions],
        "bottles": [{"index": b["index"], "medication_key": b["medication_key"],
                     "label": med_label(timeline, b["medication_key"]),
                     "position": to_unity(b["position"]) if b["position"] else None,
                     "look": b["look"], "hidden": b["hidden"]} for b in bottles],
        # Unity yaw = 180 - room yaw: room forward (sin, cos) becomes Unity (sin, -cos).
        "frames": [[round(f[0], 5), round(-f[1], 5), round((180.0 - f[2]) % 360, 3), f[3]] for f in frames],
        "actions": [dict(p, contact=to_unity(p["contact"]) if p["contact"] else None,
                         to=to_unity(p["to"]) if p["to"] else None,
                         stand=None if "stand" not in p else [p["stand"][0], -p["stand"][1], 180.0 - p["stand"][2]])
                    for p in plans],
        "alignment_points": alignment,
        "report": dict(body_stats, **action_counts, **stock.notes, cutaway_walls=sum(b["cutaway"] for b in boxes)),
    }


def unity_json(plan):
    """The plan in a form Unity's JsonUtility reads: flat frame floats, no nulls."""
    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items() if v is not None}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value
    body = {k: v for k, v in plan.items() if k not in ("frames", "report")}
    body["frames_flat"] = [float(v) for f in plan["frames"] for v in f]
    return json.dumps(clean(body))


def med_label(timeline, key):
    med = (timeline.get("medications") or {}).get(key or "")
    if not med:
        return ""
    return f"{med['name']} {med['strength']}".strip()


def load_timeline(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data["timeline"] if "timeline" in data and "schema" not in data else data


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("timeline", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    plan = build_plan(load_timeline(args.timeline))
    args.output.write_text(json.dumps(plan), encoding="utf-8")
    print(json.dumps(plan["report"], indent=2))
