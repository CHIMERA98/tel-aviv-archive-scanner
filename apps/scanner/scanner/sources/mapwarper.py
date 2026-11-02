"""Historic Haifa maps warped on mapwarper.net (MapJunction has none for Haifa). Rendered with mapjunction.render."""

from scanner import config
from scanner.models import HistoricMap


def maps_at(lat: float, lon: float) -> list[HistoricMap]:
    """The configured maps whose extent covers the point, oldest first."""
    found = []
    for map_id, title, year, (west, south, east, north) in config.HAIFA_MAPS:
        if west <= lon <= east and south <= lat <= north:
            found.append(
                HistoricMap(
                    map_id=str(map_id),
                    title=title,
                    year=year,
                    folder="mapwarper",
                    tiles=config.MAPWARPER_TILES.format(id=map_id),
                    zoom_min=0,
                    zoom_max=20,
                )
            )
    return sorted(found, key=lambda m: (m.year or 0, m.title))


def link(historic: HistoricMap) -> str:
    return f"https://mapwarper.net/maps/{historic.map_id}"
