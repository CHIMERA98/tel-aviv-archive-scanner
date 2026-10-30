"""Web Mercator (EPSG:3857) <-> WGS84 conversions and slippy-map tile maths."""

import math
from datetime import date, datetime

from scanner.config import ISRAEL_TZ

EARTH_RADIUS = 6378137.0


def to_mercator(lon: float, lat: float) -> tuple[float, float]:
    x = math.radians(lon) * EARTH_RADIUS
    y = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * EARTH_RADIUS
    return x, y


def to_lonlat(x: float, y: float) -> tuple[float, float]:
    lon = math.degrees(x / EARTH_RADIUS)
    lat = math.degrees(2 * math.atan(math.exp(y / EARTH_RADIUS)) - math.pi / 2)
    return lon, lat


def tile_float(lon: float, lat: float, zoom: int) -> tuple[float, float]:
    """Fractional XYZ tile coordinates of a point."""
    n = 2**zoom
    tx = (lon + 180.0) / 360.0 * n
    ty = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
    return tx, ty


def esri_date(value: int | float | None) -> date | None:
    """ArcGIS date fields are epoch milliseconds (UTC); the municipality means Israeli calendar days."""
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, tz=ISRAEL_TZ).date()
