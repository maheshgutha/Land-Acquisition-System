"""Small GeoJSON helpers (no GIS dependency; equirectangular approximation, fine for parcel scale)."""
import math

M_PER_DEG_LAT = 110_540.0
M_PER_DEG_LON_AT_EQUATOR = 111_320.0


def ring_area_ha(ring: list[list[float]]) -> float:
    """Area of a lon/lat ring in hectares (shoelace on locally projected metres)."""
    if len(ring) < 4:
        return 0.0
    lat0 = sum(p[1] for p in ring) / len(ring)
    kx = M_PER_DEG_LON_AT_EQUATOR * math.cos(math.radians(lat0))
    pts = [(p[0] * kx, p[1] * M_PER_DEG_LAT) for p in ring]
    area = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0 / 10_000.0


def geometry_area_ha(geometry: dict | None) -> float | None:
    if not geometry or geometry.get("type") != "Polygon":
        return None
    return ring_area_ha(geometry["coordinates"][0])


def rectangle(lat: float, lon: float, area_ha: float, aspect: float = 1.5) -> dict:
    """A rectangular Polygon centred on (lat, lon) whose area is `area_ha`."""
    area_m2 = area_ha * 10_000.0
    w_m = math.sqrt(area_m2 * aspect)
    h_m = area_m2 / w_m
    dlon = (w_m / 2.0) / (M_PER_DEG_LON_AT_EQUATOR * math.cos(math.radians(lat)))
    dlat = (h_m / 2.0) / M_PER_DEG_LAT
    ring = [
        [lon - dlon, lat - dlat],
        [lon + dlon, lat - dlat],
        [lon + dlon, lat + dlat],
        [lon - dlon, lat + dlat],
        [lon - dlon, lat - dlat],
    ]
    return {"type": "Polygon", "coordinates": [ring]}


def centroid(geometry: dict | None) -> tuple[float, float] | None:
    """(lat, lon) mean of the outer ring's vertices."""
    if not geometry or geometry.get("type") != "Polygon":
        return None
    ring = geometry["coordinates"][0][:-1] or geometry["coordinates"][0]
    return (sum(p[1] for p in ring) / len(ring), sum(p[0] for p in ring) / len(ring))
