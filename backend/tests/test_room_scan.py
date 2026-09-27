"""Room scan import: leveling, wall alignment, plan images, and 3D region rules."""

import json

import numpy as np
import pytest

from pharma.db.models import Catalog, Medication, Region3D, Room
from pharma.services.room import (
    ScanError, apply_transform, build_plan, import_scan, level_scan, list_rooms, load_obstacles, read_scan,
    registration_for_view, validate_regions,
)
from tests.room_fixtures import COUNTER, SHELF, scan_transform, write_scan


def _composite(tmp_path, transform):
    path = tmp_path / "scan.glb"
    write_scan(path, transform)
    vertices, faces = read_scan(path)
    to_room, fit = level_scan(vertices, faces)
    return vertices, faces, to_room, fit


@pytest.mark.parametrize("tilt,yaw", [(0.0, 0.0), (2.0, 17.0), (-4.0, -52.0), (6.0, 131.0)])
def test_leveling_recovers_the_room_up_to_a_quarter_turn(tmp_path, tilt, yaw):
    transform = scan_transform(tilt, yaw, (1.3, 0.4, -2.0))
    vertices, faces, to_room, fit = _composite(tmp_path, transform)
    rotation = (to_room @ transform)[:3, :3]
    # Up stays up, and the walls land on X/Z: the rotation is a multiple of 90° about Y.
    assert np.allclose(rotation[:, 1], [0, 1, 0], atol=1e-6)
    assert np.allclose(np.abs(rotation), np.round(np.abs(rotation)), atol=1e-6)
    room = apply_transform(vertices, to_room)
    assert abs(room[:, 1].min() - (-0.1)) < 1e-3  # the floor slab's underside; its top is y = 0
    assert fit["tilt_deg"] == pytest.approx(abs(tilt), abs=0.05)
    assert fit["tilt_corrected"]


def test_leveling_never_rescales(tmp_path):
    vertices, _faces, to_room, _ = _composite(tmp_path, scan_transform())
    room = apply_transform(vertices, to_room)
    assert np.allclose(np.linalg.norm(room[1:] - room[0], axis=1), np.linalg.norm(vertices[1:] - vertices[0], axis=1))


def test_plan_marks_furniture_but_not_open_floor(tmp_path):
    vertices, faces, to_room, _ = _composite(tmp_path, scan_transform(0, 0, (0, 0, 0)))
    room = apply_transform(vertices, to_room)
    plan, obstacles, origin, res = build_plan(room, faces)
    # The fixture is symmetric enough in X/Z that centering keeps furniture where it was
    # (up to the small centering shift, which the transform tells us).
    shift = to_room[:3, 3]

    def cell(x, z):
        return int((z + shift[2] - origin[1]) / res), int((x + shift[0] - origin[0]) / res)

    shelf_center, counter_center = SHELF[0], COUNTER[0]
    assert obstacles[cell(shelf_center[0], shelf_center[2])] == 255
    assert obstacles[cell(counter_center[0], counter_center[2])] == 255
    assert obstacles[cell(0.8, 1.0)] == 0  # open floor
    assert plan[cell(0.8, 1.0)][3] == 255  # floor is drawn
    # Walls are surfaces one cell thick from above; they must survive noise filtering.
    assert obstacles[cell(3.0, 0.0)[0], cell(2.96, 0.0)[1]:cell(3.12, 0.0)[1]].max() == 255


def test_import_writes_room_files(tmp_path):
    rooms = tmp_path / "rooms"
    upload = tmp_path / "upload.glb"
    write_scan(upload)
    room = import_scan(rooms, upload, "Back room", source_name="scan.glb")
    assert room.room_id == "back-room"
    assert not upload.exists()  # moved into the room folder, not copied
    folder = rooms / "back-room"
    assert {p.name for p in folder.iterdir()} == {"room.json", "mesh.glb", "plan.png", "obstacles.png"}
    assert json.loads((folder / "room.json").read_text())["mesh"]["source_name"] == "scan.glb"
    assert load_obstacles(rooms, room).dtype == bool
    assert [r.room_id for r in list_rooms(rooms)] == ["back-room"]
    again = tmp_path / "again.glb"
    write_scan(again)
    assert import_scan(rooms, again, "Back room").room_id == "back-room-2"


def test_rejects_files_that_are_not_scans(tmp_path):
    bad = tmp_path / "bad.glb"
    bad.write_bytes(b"PK\x03\x04 this is a zip")
    with pytest.raises(ScanError, match="Not a GLB"):
        read_scan(bad)


def test_rejects_compressed_meshes(tmp_path):
    path = tmp_path / "draco.glb"
    write_scan(path)
    data = bytearray(path.read_bytes())
    length = int.from_bytes(data[12:16], "little")
    gltf = json.loads(data[20:20 + length])
    gltf["extensionsRequired"] = ["KHR_draco_mesh_compression"]
    chunk = json.dumps(gltf).encode()
    chunk += b" " * (-len(chunk) % 4)
    rebuilt = data[:12] + len(chunk).to_bytes(4, "little") + data[16:20] + chunk + data[20 + length:]
    rebuilt[8:12] = len(rebuilt).to_bytes(4, "little")
    path.write_bytes(bytes(rebuilt))
    with pytest.raises(ScanError, match="without mesh compression"):
        read_scan(path)


def test_rejects_a_scan_without_a_floor(tmp_path):
    import trimesh

    path = tmp_path / "wall.glb"
    wall = trimesh.creation.box(extents=(3, 2.5, 0.1))
    path.write_bytes(trimesh.Scene([wall]).export(file_type="glb"))
    vertices, faces = read_scan(path)
    with pytest.raises(ScanError, match="no floor"):
        level_scan(vertices, faces)


def _room(**changes):
    base = {
        "room_id": "r", "created_at": "2026-09-26T00:00:00+00:00", "updated_at": "2026-09-26T00:00:00+00:00",
        "mesh": {"file": "mesh.glb", "sha256": "0" * 64, "bytes": 1, "triangles": 1},
        "mesh_to_room": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
        "bounds_min": [-1, 0, -1], "bounds_max": [1, 2, 1],
        "plan": {"image": "plan.png", "obstacles": "obstacles.png", "origin": [0, 0], "resolution_m": 0.02,
                 "width": 1, "height": 1},
    }
    return Room.model_validate({**base, **changes})


def _box():
    return {"center": [0, 1, 0], "size": [1, 0.5, 0.4], "yaw_deg": 0}


def test_shelves_take_their_medication_id():
    room = _room(regions=[{"region_id": "anything", "region_type": "designated_shelf",
                           "medication_key": "AMOXICILLIN_500MG", "box": _box()}])
    assert room.regions[0].region_id == "shelf_amoxicillin_500mg"


@pytest.mark.parametrize("regions,message", [
    ([{"region_id": "a", "region_type": "designated_shelf", "medication_key": "X_1MG", "box": _box()},
      {"region_id": "b", "region_type": "designated_shelf", "medication_key": "X_1MG", "box": _box()}],
     "more than one shelf"),
    ([{"region_id": "c", "region_type": "dispensing_counter", "medication_key": "X_1MG", "box": _box()}],
     "cannot hold a medication"),
    ([{"region_id": "s", "region_type": "designated_shelf", "medication_key": None, "box": _box()}],
     "no medication"),
    ([{"region_id": "c", "region_type": "dispensing_counter", "box": _box()},
      {"region_id": "c", "region_type": "disposal", "box": _box()}], "Duplicate region ID"),
])
def test_room_region_rules(regions, message):
    with pytest.raises(ValueError, match=message):
        _room(regions=regions)


def test_box_sizes_must_be_positive():
    with pytest.raises(ValueError, match="between 0 and 20"):
        _room(regions=[{"region_id": "c", "region_type": "disposal",
                        "box": {"center": [0, 0, 0], "size": [1, 0, 1]}}])


def test_shelves_must_name_configured_medications():
    catalog = Catalog(medications=[Medication(medication_key="AMOXICILLIN_500MG", name="Amoxicillin", strength="500mg")])
    ok = Region3D(region_id="s", region_type="designated_shelf", medication_key="AMOXICILLIN_500MG", box=_box())
    validate_regions([ok], catalog)
    unknown = Region3D(region_id="s", region_type="designated_shelf", medication_key="ASPIRIN_81MG", box=_box())
    with pytest.raises(ValueError, match="ASPIRIN_81MG"):
        validate_regions([unknown], catalog)


def test_registration_lookup_by_view(tmp_path):
    rooms = tmp_path / "rooms"
    upload = tmp_path / "u.glb"
    write_scan(upload)
    import_scan(rooms, upload, "Front")
    assert registration_for_view(rooms, "default") is None
