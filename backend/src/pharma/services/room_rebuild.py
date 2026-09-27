"""A clean rebuild of a scanned room from plain colored boxes (the Unity "reskin").

The scan mesh is noisy and heavy; the simulation and the Room page preview instead use
this rebuild, made only from what was measured:

- **Walls** follow the outline of the floor plan, as tall as the scan. Stretches of the
  outline with nothing tall standing on them are kept open (doorways), with a lintel.
- **Shelf units** stand where shelves are tagged. Tagged rows whose footprints overlap
  are one unit. Its boards are at the heights of the scan's horizontal surfaces inside
  the unit (plus the bottom of every tagged row), and each row gets a medication label.
- **Counter and disposal** are built from their tagged boxes.
- **Untagged furniture** in the plan's obstacle mask becomes blocks with their measured
  height and footprint, so nothing that blocks walking goes missing.
- **Colors** are the median scan-texture color of the floor, walls, shelves, counter
  and each block. No photo textures.
- **Cameras** are copied from their registrations, so the rebuilt view frames like the photo.

Everything is in room coordinates (meters, Y up, floor at y = 0). `boxes` are visual
parts; `objects` carry the solid envelope each one occupies (for collisions). The
result is deterministic for a given room version and scan.
"""

import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from pharma.db.models import Catalog, Room
from pharma.services.room import PLAN_MAX_HEIGHT_M, _face_geometry, apply_transform, room_dir

ALGORITHM = "rebuild/2"  # 2: a tagged row reuses a scanned board within BOARD_SEPARATION_M
SCHEMA_VERSION = 1
REBUILT_FILE = "rebuilt.json"

WALL_THICKNESS_M = 0.10
WALL_SIMPLIFY_M = 0.06  # outline simplification tolerance
MIN_WALL_M = 0.3
CORNER_OVERLAP_M = 0.3  # walls run this far past their corner (hidden inside the neighbour)
WALL_BAND_Y = (1.0, 1.9)  # scanned surface at these heights on the outline counts as wall (above: lintels)
MIN_OPENING_M = 0.6  # narrower gaps in the wall trace are scan holes, not doorways
MAX_OPENING_M = 2.0  # wider ones are more likely wall the scan missed
DOOR_HEIGHT_M = 2.05
WALL_BAND_M = 0.15  # obstacle cells this close to the outline belong to the wall
MIN_HEIGHT_M, MAX_HEIGHT_M = 2.2, 3.5
PANEL_M = 0.02
BOARD_M = 0.025
PLINTH_M = 0.08
BOARD_BIN_M = 0.02
BOARD_MIN_SHARE = 0.08  # of the unit's footprint area, for a horizontal surface to be a board
BOARD_SEPARATION_M = 0.12
BLOCK_MIN_M2 = 0.04
BLOCK_CLEARANCE_M = 0.10  # around tagged objects, so their scan surfaces don't become blocks
SAMPLES_PER_M2 = 4000
MAX_SAMPLES = 1_500_000
MAX_COLOR_FACES = 300_000
MIN_COLOR_AREA_M2 = 0.02

DEFAULT_PALETTE = {
    "floor": "#c9c4ba",
    "wall": "#e6e3dc",
    "shelf": "#d9d5cc",
    "counter": "#a88f73",
    "disposal": "#3f5a78",
    "furniture": "#9a948a",
    "label": "#fbfaf6",
}


# ---------------------------------------------------------------- geometry helpers


def _rot(yaw_deg: float) -> np.ndarray:
    a = np.radians(yaw_deg)
    return np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]])


def _to_local(points: np.ndarray, center: Sequence[float], yaw_deg: float) -> np.ndarray:
    return (np.atleast_2d(points) - np.asarray(center, float)) @ _rot(yaw_deg)


def _to_world(local: Sequence[float], center: Sequence[float], yaw_deg: float) -> np.ndarray:
    return _rot(yaw_deg) @ np.asarray(local, float) + np.asarray(center, float)


def _round(values, digits: int = 4) -> List[float]:
    return [round(float(v), digits) + 0.0 for v in values]


def _hex(rgb: Optional[np.ndarray], fallback: str) -> str:
    if rgb is None:
        return fallback
    r, g, b = (int(np.clip(round(float(c)), 0, 255)) for c in rgb[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


# ---------------------------------------------------------------- scan reading


def read_colored_scan(path: Path) -> Tuple[np.ndarray, np.ndarray, Optional[np.ndarray]]:
    """Vertices, faces and per-face RGB (0-255) of a GLB; colors are None without any."""
    import trimesh

    scene = trimesh.load(str(path), file_type="glb", force="scene", process=False)
    vertices, faces, colors, offset, any_color = [], [], [], 0, False
    for node in scene.graph.nodes_geometry:
        transform, name = scene.graph[node]
        geometry = scene.geometry.get(name)
        if not isinstance(geometry, trimesh.Trimesh) or len(geometry.faces) == 0:
            continue
        v = np.asarray(geometry.vertices, dtype=np.float64) @ transform[:3, :3].T + transform[:3, 3]
        f = np.asarray(geometry.faces, dtype=np.int64)
        rgb = None
        try:
            visual = geometry.visual
            if visual.kind == "texture":
                visual = visual.to_color()  # samples the texture at each vertex's UV
            if visual.kind in ("vertex", "face"):
                if visual.kind == "vertex":
                    rgb = np.asarray(visual.vertex_colors, float)[f][:, :, :3].mean(axis=1)
                else:
                    rgb = np.asarray(visual.face_colors, float)[:, :3]
        except Exception:  # an unreadable texture only costs the palette
            rgb = None
        any_color |= rgb is not None
        colors.append(rgb if rgb is not None else np.full((len(f), 3), np.nan))
        vertices.append(v)
        faces.append(f + offset)
        offset += len(v)
    return (np.concatenate(vertices), np.concatenate(faces),
            np.concatenate(colors) if any_color else None)


# ---------------------------------------------------------------- plan grids


class Grid:
    """The room plan's grid: column -> +X, row -> +Z from `origin`."""

    def __init__(self, room: Room):
        self.origin = np.array(room.plan.origin, float)
        self.res = float(room.plan.resolution_m)
        self.width, self.height = room.plan.width, room.plan.height

    def cell(self, xz: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        col = np.floor((xz[:, 0] - self.origin[0]) / self.res).astype(int)
        row = np.floor((xz[:, 1] - self.origin[1]) / self.res).astype(int)
        return row, col

    def inside(self, row, col) -> np.ndarray:
        return (row >= 0) & (row < self.height) & (col >= 0) & (col < self.width)

    def world(self, col: np.ndarray, row: np.ndarray) -> np.ndarray:
        """Cell centers (x, z) for pixel coordinates (col, row); accepts fractions."""
        return np.column_stack([self.origin[0] + (np.asarray(col) + 0.5) * self.res,
                                self.origin[1] + (np.asarray(row) + 0.5) * self.res])


def _heightmap(grid: Grid, vertices: np.ndarray, faces: np.ndarray, seed: int = 0) -> Tuple[np.ndarray, np.ndarray]:
    """Per plan cell: the tallest scanned surface below the ceiling cut (-inf where none),
    and whether any surface is at wall height (between a door's handle and its top)."""
    import trimesh

    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    count = int(np.clip(mesh.area * SAMPLES_PER_M2, 50_000, MAX_SAMPLES))
    samples, _ = trimesh.sample.sample_surface(mesh, count, seed=seed)
    points = np.concatenate([samples, vertices])
    points = points[points[:, 1] <= PLAN_MAX_HEIGHT_M]
    row, col = grid.cell(points[:, [0, 2]])
    ok = grid.inside(row, col)
    top = np.full((grid.height, grid.width), -np.inf)
    np.maximum.at(top, (row[ok], col[ok]), points[ok, 1])
    mid = np.zeros((grid.height, grid.width), bool)
    band = ok & (points[:, 1] >= WALL_BAND_Y[0]) & (points[:, 1] <= WALL_BAND_Y[1])
    mid[row[band], col[band]] = True
    return top, mid


def _footprint(plan_rgba: np.ndarray) -> np.ndarray:
    """Everything the scan covers from above (floor or furniture), holes filled."""
    covered = (plan_rgba[:, :, 3] > 0).astype(np.uint8)
    covered = cv2.morphologyEx(covered, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(covered, connectivity=8)
    if count <= 1:
        return covered.astype(bool)
    largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    main = (labels == largest).astype(np.uint8)
    contours, _ = cv2.findContours(main, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    filled = np.zeros_like(main)
    cv2.drawContours(filled, contours, -1, 1, thickness=cv2.FILLED)
    return filled.astype(bool)


# ---------------------------------------------------------------- builder


class _Builder:
    def __init__(self):
        self.boxes: List[Dict[str, Any]] = []
        self.objects: List[Dict[str, Any]] = []

    def box(self, object_id: str, part: str, center, size, yaw_deg: float, color: str, **extra) -> None:
        self.boxes.append({"id": f"{object_id}/{part}", "object_id": object_id, "part": part,
                           "center": _round(center), "size": _round(size), "yaw_deg": round(float(yaw_deg), 3) + 0.0,
                           "color": color, **extra})

    def local_box(self, object_id, part, frame_center, frame_yaw, lo, hi, color, **extra) -> None:
        """A box given by its min/max corner in an object's own frame."""
        lo, hi = np.asarray(lo, float), np.asarray(hi, float)
        self.box(object_id, part, _to_world((lo + hi) / 2, frame_center, frame_yaw), hi - lo, frame_yaw, color, **extra)

    def obj(self, object_id: str, kind: str, center, size, yaw_deg: float, **extra) -> None:
        self.objects.append({"id": object_id, "kind": kind, "solid": True,
                             "box": {"center": _round(center), "size": _round(size), "yaw_deg": round(float(yaw_deg), 3) + 0.0},
                             **extra})


def _outline(footprint: np.ndarray, grid: Grid) -> np.ndarray:
    """The footprint's outer boundary as a counterclockwise-in-(x, z) polygon, simplified."""
    contours, _ = cv2.findContours(footprint.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    contour = max(contours, key=cv2.contourArea)
    approx = cv2.approxPolyDP(contour, WALL_SIMPLIFY_M / grid.res, True).reshape(-1, 2).astype(float)
    # Contours run through boundary cell centers; the wall's inner face is the cell's outer edge.
    center = approx.mean(axis=0)
    approx += np.sign(approx - center) * 0.5
    pts = grid.world(approx[:, 0], approx[:, 1])
    area = 0.5 * np.sum(pts[:, 0] * np.roll(pts[:, 1], -1) - np.roll(pts[:, 0], -1) * pts[:, 1])
    return pts if area > 0 else pts[::-1]


def _wall_spans(p0, p1, mid: np.ndarray, grid: Grid, inward: np.ndarray) -> Tuple[List[Tuple[float, float]], List[Tuple[float, float]]]:
    """(solid spans, openings) along one outline edge, as distances from p0."""
    length = float(np.linalg.norm(p1 - p0))
    direction = (p1 - p0) / length
    steps = max(2, int(np.ceil(length / grid.res)))
    s = (np.arange(steps) + 0.5) * length / steps
    tall = np.zeros(steps, bool)
    # Look on both sides of the face: its samples may fall in either neighbouring cell.
    for depth in np.arange(-2.5, WALL_BAND_M / grid.res + 1) * grid.res:
        xz = p0 + direction * s[:, None] + inward * depth
        row, col = grid.cell(xz)
        ok = grid.inside(row, col)
        tall[ok] |= mid[row[ok], col[ok]]
    openings, start = [], None
    for i, present in enumerate(np.append(tall, True)):
        if not present and start is None:
            start = i
        elif present and start is not None:
            a, b = start * length / steps, i * length / steps
            if MIN_OPENING_M <= b - a <= MAX_OPENING_M and a > 0.05 and b < length - 0.05:
                openings.append((a, b))
            start = None
    solid, cursor = [], 0.0
    for a, b in openings:
        if a - cursor >= 0.02:
            solid.append((cursor, a))
        cursor = b
    if length - cursor >= 0.02:
        solid.append((cursor, length))
    return solid, openings


def _board_heights(local_pts: np.ndarray, areas: np.ndarray, half: np.ndarray, height: float,
                   rows: List[Tuple[float, float]]) -> List[float]:
    """Heights of horizontal scan surfaces inside a unit's footprint, plus each row's floor."""
    inside = (np.abs(local_pts[:, 0]) <= half[0]) & (np.abs(local_pts[:, 2]) <= half[1])
    y = local_pts[inside, 1]
    w = areas[inside]
    keep = (y > PLINTH_M + 0.05) & (y < height - 0.05)
    found: List[float] = []
    if keep.any():
        edges = np.arange(PLINTH_M, height + BOARD_BIN_M, BOARD_BIN_M)
        hist, edges = np.histogram(y[keep], bins=edges, weights=w[keep])
        need = BOARD_MIN_SHARE * (4 * half[0] * half[1])
        for i in np.argsort(-hist):
            if hist[i] < need:
                break
            h = float((edges[i] + edges[i + 1]) / 2)
            if all(abs(h - f) >= BOARD_SEPARATION_M for f in found):
                found.append(h)
    for bottom, _top in rows:  # a tagged row's bottles stand on a board
        # A scanned board this close is that row's board (tags are often drawn a little low).
        if bottom > PLINTH_M + 0.05 and all(abs(bottom - f) >= BOARD_SEPARATION_M for f in found):
            found.append(bottom)
    return sorted(found)


def _footprint_corners(region, frame_center, frame_yaw) -> np.ndarray:
    """A tagged box's 8 corners in another object's frame."""
    w, h, d = region.box.size
    local = np.array([[x, y, z] for x in (-w / 2, w / 2) for y in (-h / 2, h / 2) for z in (-d / 2, d / 2)])
    return _to_local(local @ _rot(region.box.yaw_deg).T + np.array(region.box.center), frame_center, frame_yaw)


def _group_shelves(shelves) -> List[List[Any]]:
    """Tagged shelf rows whose footprints overlap (stacked rows of one unit), grouped."""
    groups: List[List[Any]] = []
    for r in shelves:
        home = None
        for g in groups:
            center, yaw = g[0].box.center, g[0].box.yaw_deg
            if abs(((yaw - r.box.yaw_deg) + 180) % 360 - 180) >= 5:
                continue
            members = np.concatenate([_footprint_corners(q, center, yaw) for q in g])
            mine = _footprint_corners(r, center, yaw)
            overlap_x = min(members[:, 0].max(), mine[:, 0].max()) - max(members[:, 0].min(), mine[:, 0].min())
            overlap_z = min(members[:, 2].max(), mine[:, 2].max()) - max(members[:, 2].min(), mine[:, 2].min())
            if overlap_x > 0.05 and overlap_z > 0.02:
                home = g
                break
        if home is None:
            groups.append([r])
        else:
            home.append(r)
    return groups


def rebuild_room(room: Room, scan_path: Path, catalog: Optional[Catalog] = None,
                 plan_rgba: Optional[np.ndarray] = None, obstacles: Optional[np.ndarray] = None) -> Dict[str, Any]:
    """The rebuilt room (see the module docstring) and a report of how well it fits the scan."""
    folder = scan_path.parent
    if plan_rgba is None:
        plan_rgba = cv2.cvtColor(cv2.imread(str(folder / room.plan.image), cv2.IMREAD_UNCHANGED), cv2.COLOR_BGRA2RGBA)
    if obstacles is None:
        obstacles = cv2.imread(str(folder / room.plan.obstacles), cv2.IMREAD_GRAYSCALE) > 127

    raw_vertices, faces, face_rgb = read_colored_scan(scan_path)
    vertices = apply_transform(raw_vertices, np.array(room.mesh_to_room))
    centroids, normals, areas = _face_geometry(vertices, faces)
    grid = Grid(room)
    top, wall_high = _heightmap(grid, vertices, faces)
    footprint = _footprint(plan_rgba)
    height = float(np.clip(room.bounds_max[1], MIN_HEIGHT_M, MAX_HEIGHT_M))
    meds = {m.medication_key: m for m in (catalog.medications if catalog else [])}

    # Colors: sample a fixed subset of faces for speed.
    rng = np.random.default_rng(0)
    pick = np.arange(len(faces)) if len(faces) <= MAX_COLOR_FACES else np.sort(rng.choice(len(faces), MAX_COLOR_FACES, replace=False))

    def median_color(mask: np.ndarray) -> Optional[np.ndarray]:
        """Area-weighted median color of the selected faces, or None with too little surface."""
        if face_rgb is None:
            return None
        sel = pick[mask[pick]]
        sel = sel[~np.isnan(face_rgb[sel]).any(axis=1)]
        weight = areas[sel]
        if weight.sum() < MIN_COLOR_AREA_M2:
            return None
        out = []
        for channel in range(3):
            values = face_rgb[sel, channel]
            order = np.argsort(values)
            cumulative = np.cumsum(weight[order])
            out.append(values[order][np.searchsorted(cumulative, cumulative[-1] / 2)])
        return np.array(out)

    row, col = grid.cell(centroids[:, [0, 2]])
    on_grid = grid.inside(row, col)
    band_px = max(1, int(round(WALL_BAND_M / grid.res)))
    interior = cv2.erode(footprint.astype(np.uint8), np.ones((2 * band_px + 1,) * 2, np.uint8)).astype(bool)
    in_interior = np.zeros(len(faces), bool)
    in_interior[on_grid] = interior[row[on_grid], col[on_grid]]
    up = normals[:, 1] > 0.85
    vertical = np.abs(normals[:, 1]) < 0.3

    b = _Builder()
    palette = dict(DEFAULT_PALETTE)
    palette["floor"] = _hex(median_color(up & (np.abs(centroids[:, 1]) < 0.05) & in_interior), palette["floor"])
    palette["wall"] = _hex(median_color(vertical & ~in_interior & on_grid & (centroids[:, 1] > 0.3)), palette["wall"])

    # ---- floor
    outline = _outline(footprint, grid)
    lo, hi = outline.min(axis=0) - WALL_THICKNESS_M, outline.max(axis=0) + WALL_THICKNESS_M
    b.obj("floor", "floor", [(lo[0] + hi[0]) / 2, -0.025, (lo[1] + hi[1]) / 2], [hi[0] - lo[0], 0.05, hi[1] - lo[1]], 0, solid=False)
    b.box("floor", "slab", [(lo[0] + hi[0]) / 2, -0.025, (lo[1] + hi[1]) / 2], [hi[0] - lo[0], 0.05, hi[1] - lo[1]], 0, palette["floor"])

    # ---- walls
    walls_report = []
    wall_n = 0
    for i in range(len(outline)):
        p0, p1 = outline[i], outline[(i + 1) % len(outline)]
        length = float(np.linalg.norm(p1 - p0))
        if length < MIN_WALL_M:
            continue  # a corner chamfer of the trace; the neighbours are extended over it
        d = (p1 - p0) / length
        inward = np.array([-d[1], d[0]])  # counterclockwise outline: the room is on the left
        yaw = float(np.degrees(np.arctan2(-d[1], d[0])))
        # Put the inner face on the scanned wall surface that faces into the room.
        rel = centroids[:, [0, 2]] - p0
        along, across = rel @ d, rel @ inward
        facing = (normals[:, [0, 2]] @ inward) > 0.7
        near = facing & vertical & (along > 0) & (along < length) & (across > -0.1) & (across < 0.4) & (centroids[:, 1] > 0.3)
        shift, residual = 0.0, None
        if areas[near].sum() >= 0.05:
            order = np.argsort(across[near])
            weights = np.cumsum(areas[near][order])
            shift = float(across[near][order][np.searchsorted(weights, weights[-1] / 2)])
            residual = float(np.median(np.abs(across[near] - shift)))
        solid, openings = _wall_spans(p0 + inward * shift, p1 + inward * shift, wall_high, grid, inward)
        wall_n += 1
        wall_id = f"wall_{wall_n:02d}"
        face = inward * (shift - WALL_THICKNESS_M / 2)  # from the outline to the wall's center line
        center = (p0 + p1) / 2 + face
        b.obj(wall_id, "wall", [center[0], height / 2, center[1]], [length + 2 * CORNER_OVERLAP_M, height, WALL_THICKNESS_M], yaw,
              openings=[_round(o, 3) for o in openings])
        for k, (s0, s1) in enumerate(solid):
            # Spans that reach a corner run on past it, so neighbouring walls always meet.
            s0 = s0 - CORNER_OVERLAP_M if s0 <= 0.02 else s0
            s1 = s1 + CORNER_OVERLAP_M if s1 >= length - 0.02 else s1
            mid = p0 + d * (s0 + s1) / 2 + face
            b.box(wall_id, f"span_{k + 1}", [mid[0], height / 2, mid[1]], [s1 - s0, height, WALL_THICKNESS_M], yaw, palette["wall"])
        for k, (s0, s1) in enumerate(openings):
            if height - DOOR_HEIGHT_M > 0.05:
                mid = p0 + d * (s0 + s1) / 2 + face
                b.box(wall_id, f"lintel_{k + 1}", [mid[0], (DOOR_HEIGHT_M + height) / 2, mid[1]],
                      [s1 - s0, height - DOOR_HEIGHT_M, WALL_THICKNESS_M], yaw, palette["wall"])
        walls_report.append({"id": wall_id, "length_m": round(length, 3), "openings": len(openings),
                             "shift_from_trace_m": round(shift, 3),
                             # Median distance of the scanned wall surface from the rebuilt inner face.
                             "scan_offset_m": None if residual is None else round(residual, 3)})

    # ---- shelf units
    tagged_mask = np.zeros_like(footprint, dtype=np.uint8)
    shelves_report = []
    shelves = [r for r in room.regions if r.region_type == "designated_shelf"]
    for n, group in enumerate(_group_shelves(shelves), start=1):
        frame_c, frame_yaw = np.array(group[0].box.center, float), group[0].box.yaw_deg
        corners = np.concatenate([_footprint_corners(r, frame_c, frame_yaw) for r in group])
        rows = [(float(r.box.center[1] - r.box.size[1] / 2), float(r.box.center[1] + r.box.size[1] / 2)) for r in group]
        x0, x1 = corners[:, 0].min(), corners[:, 0].max()
        z0, z1 = corners[:, 2].min(), corners[:, 2].max()
        unit_c = _to_world([(x0 + x1) / 2, 0, (z0 + z1) / 2], frame_c, frame_yaw)
        unit_c[1] = 0.0
        half = np.array([(x1 - x0) / 2, (z1 - z0) / 2])
        local_all = _to_local(centroids, unit_c, frame_yaw)
        in_foot = (np.abs(local_all[:, 0]) <= half[0]) & (np.abs(local_all[:, 2]) <= half[1])
        scan_top = local_all[in_foot & (centroids[:, 1] <= PLAN_MAX_HEIGHT_M), 1]
        unit_h = max(max(t for _b, t in rows), float(np.percentile(scan_top, 99)) if len(scan_top) > 20 else 0.0)
        unit_h = float(min(unit_h, PLAN_MAX_HEIGHT_M))
        color = _hex(median_color(in_foot & (centroids[:, 1] > PLINTH_M)), palette["shelf"])
        uid = f"shelf_unit_{n:02d}"
        b.obj(uid, "shelf_unit", [unit_c[0], unit_h / 2, unit_c[2]], [2 * half[0], unit_h, 2 * half[1]], frame_yaw,
              region_ids=[r.region_id for r in group])
        W, D = half
        b.local_box(uid, "side_left", unit_c, frame_yaw, [-W, 0, -D], [-W + PANEL_M, unit_h, D], color)
        b.local_box(uid, "side_right", unit_c, frame_yaw, [W - PANEL_M, 0, -D], [W, unit_h, D], color)
        b.local_box(uid, "back", unit_c, frame_yaw, [-W + PANEL_M, 0, -D], [W - PANEL_M, unit_h, -D + PANEL_M], color)
        b.local_box(uid, "plinth", unit_c, frame_yaw, [-W + PANEL_M, 0, -D + PANEL_M], [W - PANEL_M, PLINTH_M, D - 0.02], color)
        b.local_box(uid, "top", unit_c, frame_yaw, [-W, unit_h - BOARD_M, -D], [W, unit_h, D], color)
        boards = _board_heights(local_all[up], areas[up], half, unit_h, rows)
        for k, h in enumerate(boards):
            b.local_box(uid, f"board_{k + 1}", unit_c, frame_yaw, [-W + PANEL_M, h - BOARD_M, -D + PANEL_M], [W - PANEL_M, h, D], color)
        for r, (bottom, _t) in zip(group, rows):
            med = meds.get(r.medication_key)
            text = f"{med.name} {med.strength}" if med else (r.medication_key or r.region_id)
            front = _to_local(np.array([r.box.center]) + _rot(r.box.yaw_deg) @ [0, 0, r.box.size[2] / 2], unit_c, frame_yaw)[0]
            lh = 0.045
            base = max(bottom - lh - 0.005, PLINTH_M) if bottom > PLINTH_M + lh else bottom + 0.01
            b.local_box(uid, f"label_{r.region_id}", unit_c, frame_yaw, [front[0] - 0.09, base, D], [front[0] + 0.09, base + lh, D + 0.004],
                        palette["label"], label=text, region_id=r.region_id)
        cv2.fillPoly(tagged_mask, [_cells(grid, unit_c, frame_yaw, half + BLOCK_CLEARANCE_M)], 1)
        shelves_report.append({"id": uid, "regions": [r.region_id for r in group], "boards": len(boards),
                               "board_heights_m": _round(boards, 3), "height_m": round(unit_h, 3)})

    # ---- counter and disposal
    for r in room.regions:
        if r.region_type == "designated_shelf":
            continue
        c, yaw = np.array(r.box.center, float), r.box.yaw_deg
        w, h, dd = r.box.size
        base_c = np.array([c[0], 0.0, c[2]])
        top_y = float(c[1] + h / 2)
        local_all = _to_local(centroids, base_c, yaw)
        in_foot = (np.abs(local_all[:, 0]) <= w / 2) & (np.abs(local_all[:, 2]) <= dd / 2) & (centroids[:, 1] > 0.05)
        oid = r.region_id
        if r.region_type == "dispensing_counter":
            color = _hex(median_color(in_foot), palette["counter"])
            palette["counter"] = color
            b.obj(oid, "counter", [c[0], top_y / 2, c[2]], [w, top_y, dd], yaw, region_ids=[oid])
            b.local_box(oid, "base", base_c, yaw, [-w / 2 + 0.03, 0, -dd / 2 + 0.03], [w / 2 - 0.03, top_y - 0.04, dd / 2 - 0.03], color)
            b.local_box(oid, "top", base_c, yaw, [-w / 2, top_y - 0.04, -dd / 2], [w / 2, top_y, dd / 2], color)
        else:
            color = _hex(median_color(in_foot), palette["disposal"])
            palette["disposal"] = color
            b.obj(oid, "disposal", [c[0], top_y / 2, c[2]], [w, top_y, dd], yaw, region_ids=[oid])
            t = 0.02
            b.local_box(oid, "bottom", base_c, yaw, [-w / 2, 0, -dd / 2], [w / 2, t, dd / 2], color)
            b.local_box(oid, "wall_front", base_c, yaw, [-w / 2, 0, dd / 2 - t], [w / 2, top_y, dd / 2], color)
            b.local_box(oid, "wall_back", base_c, yaw, [-w / 2, 0, -dd / 2], [w / 2, top_y, -dd / 2 + t], color)
            b.local_box(oid, "wall_left", base_c, yaw, [-w / 2, 0, -dd / 2 + t], [-w / 2 + t, top_y, dd / 2 - t], color)
            b.local_box(oid, "wall_right", base_c, yaw, [w / 2 - t, 0, -dd / 2 + t], [w / 2, top_y, dd / 2 - t], color)
        cv2.fillPoly(tagged_mask, [_cells(grid, base_c, yaw, np.array([w / 2, dd / 2]) + BLOCK_CLEARANCE_M)], 1)

    # ---- untagged furniture
    free = obstacles & interior & ~tagged_mask.astype(bool)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(free.astype(np.uint8), connectivity=8)
    blocks_report = []
    order = sorted(range(1, count), key=lambda k: (stats[k, cv2.CC_STAT_TOP], stats[k, cv2.CC_STAT_LEFT]))
    for k in order:
        if stats[k, cv2.CC_STAT_AREA] * grid.res ** 2 < BLOCK_MIN_M2:
            continue
        rr, cc = np.nonzero(labels == k)
        xz = grid.world(cc, rr).astype(np.float32)
        (cx, cz), (sx, sz), angle = cv2.minAreaRect(xz)
        tops = top[rr, cc]
        tops = tops[np.isfinite(tops)]
        h = float(np.clip(np.percentile(tops, 95), 0.2, PLAN_MAX_HEIGHT_M)) if len(tops) else 0.8
        yaw = -float(angle)  # minAreaRect turns from +X toward +Z; room yaw turns the other way
        sx, sz = max(sx + grid.res, 0.05), max(sz + grid.res, 0.05)
        local = _to_local(centroids, [cx, 0, cz], yaw)
        in_foot = (np.abs(local[:, 0]) <= sx / 2) & (np.abs(local[:, 2]) <= sz / 2) & (centroids[:, 1] > 0.05)
        color = _hex(median_color(in_foot), palette["furniture"])
        bid = f"block_{len(blocks_report) + 1:02d}"
        b.obj(bid, "block", [cx, h / 2, cz], [sx, h, sz], yaw)
        b.box(bid, "body", [cx, h / 2, cz], [sx, h, sz], yaw, color)
        blocks_report.append({"id": bid, "area_m2": round(float(sx * sz), 3), "height_m": round(h, 3)})

    offsets = [w["scan_offset_m"] for w in walls_report if w["scan_offset_m"] is not None]
    outside = _parts_outside(b)
    return {
        "schema_version": SCHEMA_VERSION,
        "algorithm": ALGORITHM,
        "room_id": room.room_id,
        "room_version": room.room_version,
        "mesh_sha256": room.mesh.sha256,
        "height_m": round(height, 3),
        "floor_polygon": [_round(p, 3) for p in outline],
        "palette": palette,
        "has_scan_colors": face_rgb is not None,
        "objects": b.objects,
        "boxes": b.boxes,
        "regions": [r.model_dump(mode="json") for r in room.regions],
        "cameras": [{k: v for k, v in c.model_dump(mode="json").items() if k != "correspondences"} for c in room.cameras],
        "report": {
            "walls": walls_report,
            "max_wall_offset_m": round(max(offsets), 3) if offsets else None,
            "shelf_units": shelves_report,
            "blocks": blocks_report,
            "box_count": len(b.boxes),
            # Parts (labels aside) that stick out of their object's envelope: should be none.
            "parts_outside_envelope": outside,
        },
    }


def _parts_outside(b: _Builder, tolerance_m: float = 0.002) -> List[str]:
    envelopes = {o["id"]: o["box"] for o in b.objects}
    outside = []
    for part in b.boxes:
        env = envelopes[part["object_id"]]
        if "label" in part:
            continue
        w, h, d = part["size"]
        corners = np.array([[x, y, z] for x in (-w / 2, w / 2) for y in (-h / 2, h / 2) for z in (-d / 2, d / 2)])
        world = corners @ _rot(part["yaw_deg"]).T + np.array(part["center"])
        local = _to_local(world, env["center"], env["yaw_deg"])
        if (np.abs(local) > np.array(env["size"]) / 2 + tolerance_m).any():
            outside.append(part["id"])
    return outside


def _cells(grid: Grid, center, yaw_deg: float, half: np.ndarray) -> np.ndarray:
    """An object's footprint rectangle as plan pixel coordinates (for cv2.fillPoly)."""
    corners = np.array([[sx * half[0], 0, sz * half[1]] for sx, sz in ((-1, -1), (1, -1), (1, 1), (-1, 1))])
    world = corners @ _rot(yaw_deg).T + np.asarray(center, float)
    col = (world[:, 0] - grid.origin[0]) / grid.res - 0.5
    row = (world[:, 2] - grid.origin[1]) / grid.res - 0.5
    return np.round(np.column_stack([col, row])).astype(np.int32)


def load_or_rebuild(rooms_dir: Path, room: Room, catalog: Optional[Catalog] = None) -> Dict[str, Any]:
    """The cached rebuild for this room version, or a fresh one (then cached)."""
    folder = room_dir(rooms_dir, room.room_id)
    path = folder / REBUILT_FILE
    if path.exists():
        try:
            cached = json.loads(path.read_text())
            if (cached.get("algorithm") == ALGORITHM and cached.get("room_version") == room.room_version
                    and cached.get("mesh_sha256") == room.mesh.sha256):
                return cached
        except ValueError:
            pass
    rebuilt = rebuild_room(room, folder / room.mesh.file, catalog)
    tmp = path.with_suffix(f".{uuid.uuid4().hex[:8]}.tmp")  # two readers may build at once
    tmp.write_text(json.dumps(rebuilt, indent=1))
    tmp.replace(path)
    return rebuilt
