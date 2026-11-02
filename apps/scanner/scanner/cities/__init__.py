"""Address lookup per city. Neither city's lookup touches Complot or the archives; scans do that."""

import re
import time

from scanner.http import Fetcher
from scanner.models import AddressMatch, Building, City
from scanner.sources import gis, haifa_gis

CITIES: tuple[City, ...] = ("tel-aviv", "haifa")
HAIFA_WORDS = re.compile(r"חיפה|haifa", re.IGNORECASE)
STREETS_TTL_S = 24 * 3600

_streets: dict[str, tuple[float, list[gis.Street]]] = {}


def detect_city(text: str, default: City = "tel-aviv") -> City:
    return "haifa" if HAIFA_WORDS.search(text) else default


def parse_city(value: str | None) -> City | None:
    return value if value in CITIES else None  # type: ignore[return-value]


async def streets(fetcher: Fetcher, city: City) -> list[gis.Street]:
    loaded_at, cached = _streets.get(city, (0.0, []))
    if not cached or time.time() - loaded_at > STREETS_TTL_S:
        cached = await (haifa_gis.load_streets(fetcher) if city == "haifa" else gis.load_streets(fetcher))
        _streets[city] = (time.time(), cached)
    return cached


async def lookup(fetcher: Fetcher, city: City, text: str, limit: int = 5) -> list[tuple[AddressMatch, Building | None]]:
    known = await streets(fetcher, city)
    if city == "haifa":
        matches = await haifa_gis.search_addresses(fetcher, text, known, limit=limit)
        return [(m, await haifa_gis.building_at(fetcher, m)) for m in matches]
    matches = await gis.search_addresses(fetcher, text, known, limit=limit)
    return [(m, await gis.building_at(fetcher, m.x, m.y)) for m in matches]
