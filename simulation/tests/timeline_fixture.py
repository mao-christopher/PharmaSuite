"""A synthetic timeline/1 fixture for the re-enactment planner (room frame, meters)."""
import math

FPS = 30.0
CAMERA = {
    "camera_id": None, "layout_id": "default", "registered": True, "frame_size": [1280, 720],
    "intrinsics": {"fx": 896.6648471772694, "fy": 896.6648471772694, "cx": 640.0, "cy": 360.0},
    "rotation": [[0.7801260677765898, 0.0006050707847486125, -0.6256220522526216],
                 [0.25460723744698666, -0.9137497828088211, 0.31660146723672355],
                 [-0.5714704480680085, -0.40627696009763814, -0.7129940803953229]],
    "translation": [-0.4225472272438352, 0.9449280617715963, 3.781950838013016],
}


def box(center, size, yaw=0.0):
    return {"center": list(center), "size": list(size), "yaw_deg": yaw}


def rebuilt_room():
    """Four walls, a two-row shelf unit with boards, a counter and a hollow disposal bin."""
    parts, objects = [], []

    def part(obj, name, center, size, color="#d9d5cc", **extra):
        parts.append(dict(box(center, size), id=f"{obj}/{name}", object_id=obj, part=name, color=color, **extra))

    objects.append({"id": "floor", "kind": "floor", "solid": False, "box": box((0, -.025, 0), (6.4, .05, 5.4))})
    part("floor", "slab", (0, -.025, 0), (6.4, .05, 5.4), "#c9c4ba")
    for n, (c, s, yaw) in enumerate([((0, 1.25, -2.6), (6.6, 2.5, .1), 0), ((3.1, 1.25, 0), (5.6, 2.5, .1), -90),
                                     ((0, 1.25, 2.5), (6.6, 2.5, .1), 180), ((-3.0, 1.25, 0), (5.6, 2.5, .1), 90)], 1):
        objects.append({"id": f"wall_0{n}", "kind": "wall", "solid": True, "box": box(c, s, yaw), "openings": []})
        part(f"wall_0{n}", "span_1", c, s, "#e6e3dc", yaw_deg=yaw)
    objects.append({"id": "shelf_unit_01", "kind": "shelf_unit", "solid": True, "box": box((.06, .9, -2.35), (1.56, 1.8, .36)),
                    "region_ids": ["shelf_amoxicillin_500mg", "shelf_ibuprofen_200mg"]})
    part("shelf_unit_01", "back", (.06, .9, -2.52), (1.52, 1.8, .02))
    part("shelf_unit_01", "plinth", (.06, .04, -2.35), (1.52, .08, .32))
    for n, y in enumerate((.3775, .8775, 1.3775), 1):
        part("shelf_unit_01", f"board_{n}", (.06, y, -2.34), (1.52, .025, .34))
    part("shelf_unit_01", "label_shelf_ibuprofen_200mg", (.06, .8725, -2.168), (.18, .045, .004), "#fbfaf6",
         label="Ibuprofen 200mg", region_id="shelf_ibuprofen_200mg")
    objects.append({"id": "counter_01", "kind": "counter", "solid": True, "box": box((-1.74, .475, .45), (.6, .95, 1.2)),
                    "region_ids": ["counter_01"]})
    part("counter_01", "base", (-1.74, .455, .45), (.54, .91, 1.14), "#a88f73")
    part("counter_01", "top", (-1.74, .93, .45), (.6, .04, 1.2), "#a88f73")
    objects.append({"id": "disposal_01", "kind": "disposal", "solid": True, "box": box((2.36, .35, -1.55), (.4, .7, .4)),
                    "region_ids": ["disposal_01"]})
    part("disposal_01", "bottom", (2.36, .01, -1.55), (.4, .02, .4), "#3f5a78")
    regions = [
        {"region_id": "shelf_amoxicillin_500mg", "region_type": "designated_shelf", "medication_key": "AMOXICILLIN_500MG",
         "box": box((.06, .65, -2.35), (1.56, .5, .36))},
        {"region_id": "shelf_ibuprofen_200mg", "region_type": "designated_shelf", "medication_key": "IBUPROFEN_200MG",
         "box": box((.06, 1.15, -2.35), (1.56, .5, .36))},
        {"region_id": "counter_01", "region_type": "dispensing_counter", "medication_key": None,
         "box": box((-1.74, .475, .45), (.6, .95, 1.2))},
        {"region_id": "disposal_01", "region_type": "disposal", "medication_key": None,
         "box": box((2.36, .35, -1.55), (.4, .7, .4))},
    ]
    return {"schema_version": 1, "height_m": 2.5, "objects": objects, "boxes": parts, "regions": regions,
            "palette": {}, "floor_polygon": []}


# Stops (x, z, facing yaw) and the walk between them; yaw 0 faces room +Z.
STOPS = [(1.0, .8, 200), (.35, -1.62, 180), (-1.02, .3, 270), (-.3, -1.62, 180), (1.72, -1.5, 90), (.35, -1.62, 180)]
SCHEDULE = [(0, 0), (2.5, 1), (5.5, 1), (7.5, 2), (11.5, 2), (13.5, 3), (16.0, 3), (18.0, 4), (20.5, 4), (22.0, 5), (24.0, 5)]


def track(gap=(12.2, 13.0)):
    """One row per frame; None while the technician is out of view."""
    n = int(SCHEDULE[-1][0] * FPS)
    frames = []
    for f in range(n):
        t = f / FPS
        k = max(i for i, (s, _) in enumerate(SCHEDULE) if s <= t)
        k = min(k, len(SCHEDULE) - 2)
        (t0, a), (t1, b) = SCHEDULE[k], SCHEDULE[k + 1]
        u = (t - t0) / (t1 - t0)
        u = u * u * (3 - 2 * u)
        (x0, z0, y0), (x1, z1, _) = STOPS[a], STOPS[b]
        x, z = x0 + (x1 - x0) * u, z0 + (z1 - z0) * u
        yaw = y0 if a == b else math.degrees(math.atan2(x1 - x0, z1 - z0))
        if gap[0] <= t < gap[1]:
            frames.append(None)
        else:
            frames.append([round(x, 3), round(z, 3), round(yaw % 360, 1), "ankles", .9, False])
    return frames


def action(n, kind, ms, status="decided", region=None, outcome=None, med="IBUPROFEN_200MG", suggested=None,
           bottle="rec:sess_001", original="shelf_ibuprofen_200mg"):
    region_type = {"counter_01": "dispensing_counter", "disposal_01": "disposal"}.get(region, "designated_shelf" if region else None)
    return {"action_id": f"evt_{n:03d}", "type": kind, "contact_ms": ms, "bottle_id": bottle, "status": status,
            "region_id": region, "region_type": region_type, "suggested_region_id": suggested, "outcome": outcome,
            "medication_key": med, "original_shelf_id": original}


def timeline(rebuilt=None, camera=None):
    rebuilt = rebuilt or rebuilt_room()
    actions = [
        action(1, "pickup", 4000, region="shelf_ibuprofen_200mg", outcome="in_hand"),
        action(2, "putdown", 9500, region="counter_01", outcome="counter"),
        action(3, "pickup", 11000, region="counter_01", outcome="in_hand"),
        action(4, "putdown", 15000, region="shelf_amoxicillin_500mg", outcome="misplaced"),
        action(5, "putdown", 19500, region="disposal_01", outcome="disposed"),
        action(6, "pickup", 23000, status="pending", outcome="pending", suggested="shelf_ibuprofen_200mg",
               bottle="rec:sess_002"),
        action(7, "putdown", 23500, status="not_applied", bottle="rec:sess_003"),
    ]
    frames = track()
    return {
        "schema": "timeline/1", "recording": "synthetic-reenactment-fixture", "label": "fixture", "fps": FPS,
        "frame_size": [1280, 720], "duration_ms": int(len(frames) * 1000 / FPS),
        "room": {"room_id": "fixture", "room_version": 1, "regions": rebuilt["regions"]},
        "medications": {"AMOXICILLIN_500MG": {"name": "Amoxicillin", "strength": "500mg"},
                        "IBUPROFEN_200MG": {"name": "Ibuprofen", "strength": "200mg"}},
        "cameras": [camera or CAMERA],
        "track": {"schema": "floor-track/1", "fps": FPS, "frame_size": [1280, 720],
                  "fields": ["x", "z", "yaw_deg", "source", "conf", "bridged"], "frames": frames,
                  "hip_height_m": .95, "camera": {"layout_id": "default"}},
        "actions": actions,
        "start_counts": {"source": "before_first_signal",
                         "shelves": {"shelf_amoxicillin_500mg": {"AMOXICILLIN_500MG": 3},
                                     "shelf_ibuprofen_200mg": {"IBUPROFEN_200MG": 4}},
                         "counter": {}, "held": {}},
        "rebuilt": rebuilt,
        "inputs_sha256": "fixture",
    }
