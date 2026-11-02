"""Historic maps of mandate Palestine from Palestine Open Maps (palopenmaps.org). Rendered with mapjunction.render.

The site states no license, so each map carries its exact source and a note that publishing rights are unknown.
"""

from scanner import config
from scanner.models import HistoricMap


def maps_at(lat: float, lon: float) -> list[HistoricMap]:
    """Every configured layer: they cover all of mandate Palestine, and render() drops one with nothing at the point."""
    del lat, lon
    return [
        HistoricMap(
            map_id=key,
            title=title,
            year=year,
            folder="palopenmaps",
            tiles=tiles,
            zoom_min=0,
            zoom_max=zoom,
            credit=f"Source: {source} {config.PALOPENMAPS_RIGHTS}",
        )
        for key, title, year, tiles, zoom, source in config.PALOPENMAPS_LAYERS
    ]


def link(historic: HistoricMap) -> str:
    del historic
    return config.PALOPENMAPS_SITE
