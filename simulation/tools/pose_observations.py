"""Dependency-free confidence gating for pixel-based pose observations."""
EDGES = [(5, 6), (5, 7), (7, 9), (6, 8), (8, 10), (5, 11), (6, 12),
         (11, 12), (11, 13), (13, 15), (12, 14), (14, 16), (0, 1), (0, 2), (1, 3), (2, 4)]


def region_observation(points, confidence, regions, width, height, threshold):
    if len(points) != 1:
        return {"status": "uncertain", "reason": "expected_one_person", "region": None, "candidates": []}
    x, y = points[0][10]
    conf = float(confidence[0][10])
    candidates = [r['region_id'] for r in regions if r['x_min'] <= x / width <= r['x_max']
                  and r['y_min'] <= y / height <= r['y_max']]
    region = candidates[0] if conf >= threshold and len(candidates) == 1 else None
    return {"status": "candidate" if region else "uncertain", "region": region,
            "candidates": candidates, "right_wrist_confidence": conf,
            "reason": "unique_2d_region" if region else "low_confidence_or_ambiguous_region"}


def visible_edges(confidence, threshold):
    return [(a, b) for a, b in EDGES if confidence[a] >= threshold and confidence[b] >= threshold]
