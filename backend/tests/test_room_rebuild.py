"""The clean room rebuild: walls with doorways, shelf units with boards, furniture blocks, colors."""

import json

import numpy as np
import pytest

from pharma.db.models import Catalog, Medication, Region3D
from pharma.services.room import apply_transform, import_scan, read_scan, save_room
from pharma.services.room_rebuild import REBUILT_FILE, load_or_rebuild, rebuild_room
from tests.room_fixtures import scan_transform

FLOOR_RGB = (150, 120, 90)
WALL_RGB = (222, 216, 200)
SHELF_RGB = (200, 60, 60)
COUNTER_RGB = (60, 90, 160)
CABINET_RGB = (90, 160, 90)
BOARDS = (0.4, 0.9, 1.4)
DOOR = (1.0, 1.9)  # x range of the doorway in the front wall
CABINET = ((2.2, 0.5, 1.2), (0.8, 1.0, 0.5))
CATALOG = Catalog(medications=[Medication(medication_key="AMOXICILLIN_500MG", name="Amoxicillin", strength="500mg"),
                               Medication(medication_key="IBUPROFEN_200MG", name="Ibuprofen", strength="200mg")])


def _scene():
    """A 6 x 5 m room (fixture coordinates) with a doorway, an open shelf unit, a counter,
    a bin and an untagged cabinet, each in its own color."""
    import trimesh

    def box(center, size, rgb):
        mesh = trimesh.creation.box(extents=size)
        mesh.apply_translation(center)
        mesh.visual.vertex_colors = np.tile([*rgb, 255], (len(mesh.vertices), 1))
        return mesh

    parts = [
        box((0, -0.05, 0), (6, 0.1, 5), FLOOR_RGB),
        box((0, 1.25, -2.55), (6, 2.5, 0.1), WALL_RGB),
        box((-3.05, 1.25, 0), (0.1, 2.5, 5), WALL_RGB),
        box((3.05, 1.25, 0), (0.1, 2.5, 5), WALL_RGB),
        # Front wall with a doorway at x = 1.0 .. 1.9 and a lintel over it.
        box(((-3.0 + DOOR[0]) / 2, 1.25, 2.55), (DOOR[0] + 3.0, 2.5, 0.1), WALL_RGB),
        box(((DOOR[1] + 3.0) / 2, 1.25, 2.55), (3.0 - DOOR[1], 2.5, 0.1), WALL_RGB),
        box((sum(DOOR) / 2, 2.275, 2.55), (DOOR[1] - DOOR[0], 0.45, 0.1), WALL_RGB),
        # Open shelf unit against the back wall: sides, back, boards and a top.
        box((-0.79, 0.9, -2.3), (0.02, 1.8, 0.4), SHELF_RGB),
        box((0.79, 0.9, -2.3), (0.02, 1.8, 0.4), SHELF_RGB),
        box((0, 0.9, -2.49), (1.56, 1.8, 0.02), SHELF_RGB),
        box((0, 1.79, -2.3), (1.6, 0.02, 0.4), SHELF_RGB),
        *[box((0, h - 0.0125, -2.3), (1.56, 0.025, 0.38), SHELF_RGB) for h in BOARDS],
        box((-1.8, 0.475, 0.5), (0.6, 0.95, 1.2), COUNTER_RGB),
        box((2.3, 0.35, -1.5), (0.4, 0.7, 0.4), (60, 60, 60)),
        box(*CABINET, CABINET_RGB),
    ]
    return parts


@pytest.fixture
def room(tmp_path):
    import trimesh

    to_scan = scan_transform()
    scene = trimesh.Scene()
    for n, mesh in enumerate(_scene()):
        mesh = mesh.copy()
        mesh.apply_transform(to_scan)
        scene.add_geometry(mesh, node_name=f"part{n}")
    upload = tmp_path / "scan.glb"
    upload.write_bytes(scene.export(file_type="glb"))
    rooms_dir = tmp_path / "rooms"
    room = import_scan(rooms_dir, upload, "Detailed")
    fixture_to_room = np.array(room.mesh_to_room) @ to_scan
    turn = float(np.degrees(np.arctan2(fixture_to_room[0, 2], fixture_to_room[2, 2])))

    def place(center):
        return apply_transform(np.array([center], float), fixture_to_room)[0].round(4).tolist()

    def tag(region_id, kind, center, size, med=None):
        return Region3D(region_id=region_id, region_type=kind, medication_key=med,
                        box={"center": place(center), "size": size, "yaw_deg": round(turn, 3)})

    regions = [
        tag("shelf_amoxicillin_500mg", "designated_shelf", (0, 0.65, -2.3), [1.56, 0.5, 0.36], "AMOXICILLIN_500MG"),
        tag("shelf_ibuprofen_200mg", "designated_shelf", (0, 1.15, -2.3), [1.56, 0.5, 0.36], "IBUPROFEN_200MG"),
        tag("counter_01", "dispensing_counter", (-1.8, 0.475, 0.5), [0.6, 0.95, 1.2]),
        tag("disposal_01", "disposal", (2.3, 0.35, -1.5), [0.4, 0.7, 0.4]),
    ]
    room = save_room(rooms_dir, room.model_copy(update={"regions": regions}))
    return rooms_dir, room, place


def _rgb(hex_color):
    return np.array([int(hex_color[i:i + 2], 16) for i in (1, 3, 5)])


def test_walls_follow_the_scanned_surfaces_and_keep_the_doorway(room):
    rooms_dir, room, place = room
    rebuilt = rebuild_room(room, rooms_dir / room.room_id / room.mesh.file, CATALOG)
    walls = rebuilt["report"]["walls"]
    assert len(walls) == 4
    assert all(w["scan_offset_m"] is not None and w["scan_offset_m"] <= 0.03 for w in walls)
    assert rebuilt["height_m"] == pytest.approx(2.5, abs=0.02)
    door_walls = [o for o in rebuilt["objects"] if o["kind"] == "wall" and o["openings"]]
    assert len(door_walls) == 1 and len(door_walls[0]["openings"]) == 1
    start, end = door_walls[0]["openings"][0]
    assert end - start == pytest.approx(DOOR[1] - DOOR[0], abs=0.08)
    # The opening sits where the fixture's doorway is.
    wall = door_walls[0]["box"]
    a = np.radians(wall["yaw_deg"])
    along = np.array([np.cos(a), -np.sin(a)])  # the wall's own X, in (x, z)
    first = np.array(wall["center"])[[0, 2]] - along * wall["size"][0] / 2
    middle = first + along * (0.3 + (start + end) / 2)  # the envelope runs 0.3 m past the corner
    assert np.linalg.norm(middle - np.array(place((sum(DOOR) / 2, 0, 2.5)))[[0, 2]]) < 0.1
    assert any(b["part"].startswith("lintel") and b["object_id"] == door_walls[0]["id"] for b in rebuilt["boxes"])


def test_stacked_rows_make_one_shelf_unit_with_its_boards_and_labels(room):
    rooms_dir, room, _ = room
    rebuilt = rebuild_room(room, rooms_dir / room.room_id / room.mesh.file, CATALOG)
    units = rebuilt["report"]["shelf_units"]
    assert len(units) == 1 and sorted(units[0]["regions"]) == ["shelf_amoxicillin_500mg", "shelf_ibuprofen_200mg"]
    assert units[0]["board_heights_m"] == pytest.approx(list(BOARDS), abs=0.03)
    assert units[0]["height_m"] == pytest.approx(1.8, abs=0.03)
    labels = sorted(b["label"] for b in rebuilt["boxes"] if "label" in b)
    assert labels == ["Amoxicillin 500mg", "Ibuprofen 200mg"]
    assert rebuilt["report"]["parts_outside_envelope"] == []


def test_a_row_tagged_a_little_low_uses_its_scanned_board(room):
    # Tags whose bottoms sit 8 cm under the scanned boards don't add a second board each.
    rooms_dir, room, _ = room
    lowered = [r.model_copy(update={"box": r.box.model_copy(update={"center": [r.box.center[0], r.box.center[1] - 0.08,
                                                                              r.box.center[2]]})})
               if r.region_type == "designated_shelf" else r for r in room.regions]
    rebuilt = rebuild_room(room.model_copy(update={"regions": lowered}), rooms_dir / room.room_id / room.mesh.file, CATALOG)
    assert rebuilt["report"]["shelf_units"][0]["board_heights_m"] == pytest.approx(list(BOARDS), abs=0.03)


def test_untagged_furniture_becomes_a_measured_block(room):
    rooms_dir, room, place = room
    rebuilt = rebuild_room(room, rooms_dir / room.room_id / room.mesh.file, CATALOG)
    blocks = [o for o in rebuilt["objects"] if o["kind"] == "block"]
    assert len(blocks) == 1  # the tagged counter, bin and shelf are not blocks
    box = blocks[0]["box"]
    assert box["size"][1] == pytest.approx(CABINET[1][1], abs=0.05)
    assert sorted(box["size"][::2]) == pytest.approx(sorted(CABINET[1][::2]), abs=0.08)
    assert np.linalg.norm(np.array(box["center"])[[0, 2]] - np.array(place(CABINET[0]))[[0, 2]]) < 0.05
    body = next(b for b in rebuilt["boxes"] if b["object_id"] == blocks[0]["id"])
    assert np.abs(_rgb(body["color"]) - CABINET_RGB).max() <= 12


def test_colors_come_from_the_scan(room):
    rooms_dir, room, _ = room
    rebuilt = rebuild_room(room, rooms_dir / room.room_id / room.mesh.file, CATALOG)
    assert rebuilt["has_scan_colors"]
    assert np.abs(_rgb(rebuilt["palette"]["floor"]) - FLOOR_RGB).max() <= 12
    assert np.abs(_rgb(rebuilt["palette"]["wall"]) - WALL_RGB).max() <= 12
    assert np.abs(_rgb(rebuilt["palette"]["counter"]) - COUNTER_RGB).max() <= 12
    shelf = next(b for b in rebuilt["boxes"] if b["part"] == "side_left")
    assert np.abs(_rgb(shelf["color"]) - SHELF_RGB).max() <= 12


def test_rebuild_is_deterministic_and_cached_per_room_version(room):
    rooms_dir, room, _ = room
    first = load_or_rebuild(rooms_dir, room, CATALOG)
    assert (rooms_dir / room.room_id / REBUILT_FILE).exists()
    assert load_or_rebuild(rooms_dir, room, CATALOG) == first
    again = rebuild_room(room, rooms_dir / room.room_id / room.mesh.file, CATALOG)
    assert json.dumps(again, sort_keys=True) == json.dumps(first, sort_keys=True)
    moved = save_room(rooms_dir, room.model_copy(update={"regions": room.regions[:1]}))
    assert moved.room_version == room.room_version + 1
    assert load_or_rebuild(rooms_dir, moved, CATALOG)["room_version"] == moved.room_version


def test_a_scan_without_colors_uses_the_default_palette(tmp_path):
    from tests.room_fixtures import write_scan

    upload = tmp_path / "plain.glb"
    write_scan(upload)
    vertices, _ = read_scan(upload)
    room = import_scan(tmp_path / "rooms", upload, "Plain")
    rebuilt = rebuild_room(room, tmp_path / "rooms" / room.room_id / room.mesh.file)
    assert len(rebuilt["report"]["walls"]) == 4 and rebuilt["palette"]["floor"].startswith("#")
