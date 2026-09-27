"""A registered camera's 2D regions, generated from the room's 3D region boxes.

The 3D boxes are the only hand-made regions. Each registered camera sees them through
its registration, and the inventory pipeline keeps matching hands against the image
polygons that come out, exactly as it did with hand-drawn ones.

Each pixel goes to the nearest box surface that the camera sees there, the way a
person outlining shelves on the photo would. Neighbouring shelves therefore never
overlap (a lower shelf's top can't claim pixels where the shelf above hides it). A
region whose box is out of view, or hidden behind other boxes, gets no polygon.
Scan geometry is not used as an occluder: a counter in front of a shelf still lets the
shelf keep its outline, since a hand reaching in shows above it.
"""

from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from pharma.db.models import CameraRegistration, Region, Room
from pharma.services.camera_geometry import Camera, box_corners

ALGORITHM = "box-visibility/1"
RASTER_WIDTH = 640
NEAR_M = 0.05
# A region smaller than this share of the image is treated as not visible (unvalidated).
MIN_AREA_SHARE = 0.0005
SIMPLIFY_PX = 1.0

# box_corners order: index bits are (x, y, z) sign flags, see camera_geometry.box_corners.
_FACES = [
    (0, 1, 2),  # corner, then the two neighbours spanning the face
    (4, 6, 5),
    (0, 2, 4),
    (1, 5, 3),
    (0, 4, 1),
    (2, 3, 6),
]


def _faces(center, size, yaw_deg) -> List[Tuple[np.ndarray, np.ndarray, np.ndarray]]:
    corners = box_corners(center, size, yaw_deg)
    return [(corners[a], corners[b] - corners[a], corners[c] - corners[a]) for a, b, c in _FACES]


def _raster(cam: Camera, width: int, height: int) -> Tuple[np.ndarray, np.ndarray]:
    u = (np.arange(width) + 0.5) / width
    v = (np.arange(height) + 0.5) / height
    uu, vv = np.meshgrid(u, v)
    rays = cam.rays(np.column_stack([uu.ravel(), vv.ravel()]))
    return cam.center, rays


def visibility_labels(room: Room, cam: Camera, width: int = RASTER_WIDTH) -> Tuple[np.ndarray, int, int]:
    """Per-pixel index of the nearest visible region box (-1 where none), at a small raster."""
    height = max(1, int(round(width * cam.height / cam.width)))
    origin, rays = _raster(cam, width, height)
    depth = np.full(len(rays), np.inf)
    label = np.full(len(rays), -1, dtype=np.int32)
    for index, region in enumerate(room.regions):
        box = region.box
        for p0, e1, e2 in _faces(box.center, box.size, box.yaw_deg):
            normal = np.cross(e1, e2)
            denom = rays @ normal
            with np.errstate(divide="ignore", invalid="ignore"):
                t = ((p0 - origin) @ normal) / denom
            hit = origin + rays * t[:, None]
            rel = hit - p0
            a = rel @ e1 / (e1 @ e1)
            b = rel @ e2 / (e2 @ e2)
            inside = (t > NEAR_M) & (a >= 0) & (a <= 1) & (b >= 0) & (b <= 1) & (t < depth)
            depth[inside] = t[inside]
            label[inside] = index
    return label.reshape(height, width), width, height


def _polygon(mask: np.ndarray) -> Tuple[Optional[List[Tuple[float, float]]], int]:
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None, 0
    largest = max(contours, key=cv2.contourArea)
    approx = cv2.approxPolyDP(largest, SIMPLIFY_PX, True).reshape(-1, 2)
    if len(approx) < 3:
        return None, len(contours)
    h, w = mask.shape
    # Contours run through pixel centers; push them out to the pixel edges on the frame border.
    pts = [(min(1.0, max(0.0, (x + 0.5) / w)), min(1.0, max(0.0, (y + 0.5) / h))) for x, y in approx]
    pts = [(0.0 if x <= 0.5 / w + 1e-9 else 1.0 if x >= 1 - 0.5 / w - 1e-9 else x,
            0.0 if y <= 0.5 / h + 1e-9 else 1.0 if y >= 1 - 0.5 / h - 1e-9 else y) for x, y in pts]
    return [(round(x, 4), round(y, 4)) for x, y in pts], len(contours)


def generate_regions(room: Room, reg: CameraRegistration,
                     width: int = RASTER_WIDTH) -> Tuple[List[Region], List[Dict[str, Any]]]:
    """2D regions (normalized polygons) for one registered camera, plus a per-region report."""
    return regions_for_camera(room, Camera.from_registration(reg), width)


def regions_for_camera(room: Room, cam: Camera,
                       width: int = RASTER_WIDTH) -> Tuple[List[Region], List[Dict[str, Any]]]:
    labels, w, h = visibility_labels(room, cam, width)
    regions: List[Region] = []
    report: List[Dict[str, Any]] = []
    for index, region in enumerate(room.regions):
        mask = labels == index
        share = float(mask.mean())
        entry: Dict[str, Any] = {"region_id": region.region_id, "region_type": region.region_type,
                                 "image_share": round(share, 4), "visible": False}
        if share >= MIN_AREA_SHARE:
            polygon, pieces = _polygon(mask)
            if polygon:
                entry.update(visible=True, pieces=pieces,
                             touches_edge=bool(mask[0].any() or mask[-1].any() or mask[:, 0].any() or mask[:, -1].any()))
                regions.append(Region(region_id=region.region_id, region_type=region.region_type,
                                      medication_key=region.medication_key, polygon=polygon))
        report.append(entry)
    return regions, report
