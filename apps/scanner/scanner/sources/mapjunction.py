"""Historic maps from MapJunction, cropped around the building by stitching XYZ tiles."""

import asyncio
import io
import math

from PIL import Image, ImageDraw

from scanner import config
from scanner.geo import tile_float
from scanner.http import Fetcher
from scanner.models import HistoricMap

TILE = 256
SEARCH_DELTA_DEG = 0.003  # ~300 m: maps must cover the building itself
MAX_SPAN_DEG = 1.5  # anything wider is a world/base layer, not a historic map


def _covers(bbox: dict, lat: float, lon: float) -> bool:
    sw, ne = bbox.get("southwest") or {}, bbox.get("northeast") or {}
    try:
        return sw["lat"] <= lat <= ne["lat"] and sw["lng"] <= lon <= ne["lng"]
    except KeyError:
        return False


def _span(bbox: dict) -> float:
    sw, ne = bbox.get("southwest") or {}, bbox.get("northeast") or {}
    try:
        return max(ne["lat"] - sw["lat"], ne["lng"] - sw["lng"])
    except KeyError:
        return 360.0


def select_maps(raw_maps: list[dict], lat: float, lon: float) -> list[HistoricMap]:
    """Keep local historic maps that cover the point; drop world base layers. Oldest first."""
    maps: list[HistoricMap] = []
    seen: set[str] = set()
    for raw in raw_maps:
        bbox = raw.get("bbox") or {}
        tiles = raw.get("tiles")
        meta = raw.get("meta") or {}
        map_id = meta.get("map_id") or raw.get("id")
        if not tiles or not map_id or map_id in seen:
            continue
        if _span(bbox) > MAX_SPAN_DEG or not _covers(bbox, lat, lon):
            continue
        seen.add(map_id)
        zoom = raw.get("zoom") or {}
        year = raw.get("year") or meta.get("year")
        maps.append(
            HistoricMap(
                map_id=map_id,
                title=(raw.get("title") or "Untitled").strip(),
                year=int(year) if year else None,
                folder=meta.get("folder"),
                tiles=tiles if tiles.startswith("http") else f"https:{tiles}",
                zoom_min=int(zoom.get("min") or 0),
                zoom_max=int(zoom.get("max") or 20),
                thumbnail=raw.get("warped_thumbnail"),
            )
        )
    maps.sort(key=lambda m: (m.year is None, m.year or 0, m.title))
    return maps


async def find_maps(fetcher: Fetcher, lat: float, lon: float) -> list[HistoricMap]:
    d = SEARCH_DELTA_DEG
    bbox = f"{lat - d:.5f},{lon - d:.5f},{lat + d:.5f},{lon + d:.5f}"
    data = await fetcher.get_json(config.MAPJUNCTION_FILTER, params={"bbox": bbox})
    return select_maps((data.get("data") or {}).get("maps") or [], lat, lon)


async def render(fetcher: Fetcher, historic: HistoricMap, lat: float, lon: float) -> bytes | None:
    """A JPEG of the map around the building with a marker on it, or None if the map has no tiles here."""
    top = max(historic.zoom_min, min(config.MAP_ZOOM, historic.zoom_max))
    # mapwarper often has tiles only down to zoom 15-17 (deeper ones come back transparent): step out until it shows.
    zooms = range(top, max(historic.zoom_min, top - 3) - 1, -1) if historic.folder == "mapwarper" else [top]
    for zoom in zooms:
        image = await _render_at(fetcher, historic, lat, lon, zoom)
        if image is not None:
            return image
    return None


async def _render_at(fetcher: Fetcher, historic: HistoricMap, lat: float, lon: float, zoom: int) -> bytes | None:
    grid = config.MAP_GRID
    fx, fy = tile_float(lon, lat, zoom)
    x0, y0 = math.floor(fx) - grid // 2, math.floor(fy) - grid // 2

    async def fetch(tx: int, ty: int) -> tuple[int, int, Image.Image | None]:
        url = historic.tiles.replace("{z}", str(zoom)).replace("{x}", str(tx)).replace("{y}", str(ty))
        try:
            response = await fetcher.get(url)
        except Exception:
            return tx, ty, None
        if response.status_code != 200 or not response.headers.get("content-type", "").startswith("image"):
            return tx, ty, None  # missing tiles come back as 403/404 from the tile bucket
        image = Image.open(io.BytesIO(response.content)).convert("RGBA")
        # mapwarper answers outside a map's mask with fully transparent tiles: treat them as missing.
        return tx, ty, image if image.getchannel("A").getbbox() else None

    tiles = await asyncio.gather(*(fetch(x0 + i, y0 + j) for i in range(grid) for j in range(grid)))
    present = [img for _, _, img in tiles if img is not None]
    if len(present) < grid:  # mostly outside this map
        return None
    # The building itself must be on the map, not just inside its bounding box (rotated scans leave empty corners).
    home = next((img for tx, ty, img in tiles if (tx, ty) == (math.floor(fx), math.floor(fy))), None)
    if home is None:
        return None
    hx, hy = int((fx % 1) * home.width), int((fy % 1) * home.height)
    if home.getchannel("A").getpixel((min(hx, home.width - 1), min(hy, home.height - 1))) == 0:
        return None

    # MapJunction serves 512 px "retina" tiles; use whatever size the map actually has.
    size = present[0].width or TILE
    canvas = Image.new("RGB", (grid * size, grid * size), "white")
    for tx, ty, img in tiles:
        if img is None:
            continue
        if img.size != (size, size):
            img = img.resize((size, size), Image.Resampling.LANCZOS)
        canvas.paste(img, ((tx - x0) * size, (ty - y0) * size), img)

    px, py = (fx - x0) * size, (fy - y0) * size
    draw = ImageDraw.Draw(canvas)
    for radius, width in ((16, 5), (4, 4)):
        draw.ellipse((px - radius, py - radius, px + radius, py + radius), outline=(220, 30, 30), width=width)

    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=82)
    return out.getvalue()
