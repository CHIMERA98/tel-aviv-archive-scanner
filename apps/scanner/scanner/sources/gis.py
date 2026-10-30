"""Tel Aviv GIS (ArcGIS IView2): addresses (527), buildings (513) and zoning plans (528)."""

import difflib
import re
from dataclasses import dataclass
from datetime import date

from shapely.geometry import Point, Polygon

from scanner import config
from scanner.geo import esri_date, to_lonlat, to_mercator
from scanner.http import Fetcher
from scanner.models import AddressMatch, Building, Plan

CITY_WORDS = re.compile(
    r"תל[\s\-־]*אביב(?:[\s\-־]*יפו)?|\bיפו\b|ישראל|tel[\s\-]*aviv(?:[\s\-]*(?:yafo|jaffa))?|israel",
    re.IGNORECASE,
)
QUOTES = re.compile(r"[\"'״׳`’‘”“]")
STREET_PREFIXES = re.compile(r"^(?:רחוב|רח\b|street|st\b)\s*", re.IGNORECASE)
# House number, optionally followed by a single entrance letter (15א, 15a) that is not the start of a word.
HOUSE = re.compile(r"(\d{1,4})(?:\s?([א-ת]|[a-zA-Z])(?![א-תa-zA-Z]))?(?!\d)")


@dataclass(frozen=True)
class Street:
    code: int
    name_he: str
    name_en: str | None


@dataclass(frozen=True)
class ParsedAddress:
    street: str
    number: int
    entrance: str


def normalize_street(name: str) -> str:
    name = QUOTES.sub("", name)
    name = re.sub(r"[\-־,.]", " ", name)
    name = re.sub(r"\bשד\b", "שדרות", name)
    name = STREET_PREFIXES.sub("", name.strip())
    return re.sub(r"\s+", " ", name).strip().lower()


def parse_address(text: str) -> ParsedAddress | None:
    """Split free text like 'הגר"א 27, תל אביב' or 'Levontin 15a' into street, number, entrance."""
    cleaned = CITY_WORDS.sub(" ", text).replace(",", " ")
    match = HOUSE.search(cleaned)
    if not match:
        return None
    street = (cleaned[: match.start()] + " " + cleaned[match.end() :]).strip()
    if not street:
        return None
    entrance = (match.group(2) or "").strip()
    return ParsedAddress(street=street, number=int(match.group(1)), entrance=entrance)


async def _query(fetcher: Fetcher, layer: int, **params: str | int) -> list[dict]:
    data = await fetcher.get_json(f"{config.GIS_MAPSERVER}/{layer}/query", params={"f": "json", **params})
    if "error" in data:
        raise RuntimeError(f"GIS layer {layer} query failed: {data['error']}")
    return data.get("features", [])


async def load_streets(fetcher: Fetcher) -> list[Street]:
    """Every street that has at least one address, with its Hebrew and English names."""
    streets: dict[int, Street] = {}
    offset = 0
    while True:
        features = await _query(
            fetcher,
            config.LAYER_ADDRESSES,
            where="1=1",
            outFields="k_rechov,t_rechov,t_rechov_eng",
            returnDistinctValues="true",
            returnGeometry="false",
            orderByFields="k_rechov",
            resultOffset=offset,
            resultRecordCount=2000,
        )
        for feature in features:
            a = feature["attributes"]
            if a.get("k_rechov") is not None and a.get("t_rechov"):
                streets[a["k_rechov"]] = Street(a["k_rechov"], a["t_rechov"].strip(), a.get("t_rechov_eng"))
        if len(features) < 2000:
            return list(streets.values())
        offset += len(features)


def match_streets(query: str, streets: list[Street], limit: int = 5) -> list[Street]:
    """Rank streets by how well their Hebrew or English name matches the query."""
    q = normalize_street(query)
    if not q:
        return []
    scored: list[tuple[float, Street]] = []
    for street in streets:
        best = 0.0
        for name in (street.name_he, street.name_en or ""):
            n = normalize_street(name)
            if not n:
                continue
            if n == q:
                score = 1.0
            elif n.startswith(q) or q.startswith(n):
                score = 0.9 - abs(len(n) - len(q)) * 0.01
            elif q in n.split() or q in n:
                score = 0.75
            else:
                score = difflib.SequenceMatcher(None, q, n).ratio() * 0.8
            best = max(best, score)
        if best >= 0.6:
            scored.append((best, street))
    scored.sort(key=lambda item: -item[0])
    return [street for _, street in scored[:limit]]


async def search_addresses(fetcher: Fetcher, text: str, streets: list[Street], limit: int = 8) -> list[AddressMatch]:
    parsed = parse_address(text)
    if parsed is None:
        return []
    candidates = match_streets(parsed.street, streets)
    if not candidates:
        return []
    codes = ",".join(str(s.code) for s in candidates)
    features = await _query(
        fetcher,
        config.LAYER_ADDRESSES,
        where=f"k_rechov IN ({codes}) AND ms_bayit={parsed.number}",
        outFields="k_rechov,t_rechov,t_rechov_eng,ms_bayit,knisa,t_ktovet_melea,ms_gush,ms_chelka,lat,lon",
        returnGeometry="false",
    )
    rank = {s.code: i for i, s in enumerate(candidates)}
    matches: list[AddressMatch] = []
    for feature in features:
        a = feature["attributes"]
        if a.get("lat") is None or a.get("lon") is None:
            continue
        x, y = to_mercator(a["lon"], a["lat"])
        matches.append(
            AddressMatch(
                street_code=a["k_rechov"],
                street_he=(a.get("t_rechov") or "").strip(),
                street_en=a.get("t_rechov_eng"),
                house_number=a["ms_bayit"],
                entrance=(a.get("knisa") or "").strip(),
                label_he=(a.get("t_ktovet_melea") or "").strip(),
                lat=a["lat"],
                lon=a["lon"],
                x=x,
                y=y,
                gush=a.get("ms_gush"),
                helka=a.get("ms_chelka"),
            )
        )

    def order(m: AddressMatch) -> tuple[int, int, str]:
        wanted_entrance = 0 if m.entrance == parsed.entrance else 1
        return (rank.get(m.street_code, 99), wanted_entrance, m.entrance)

    matches.sort(key=order)
    return matches[:limit]


def _polygon(geometry: dict | None) -> Polygon | None:
    rings = (geometry or {}).get("rings") or []
    if not rings or len(rings[0]) < 3:
        return None
    return Polygon(rings[0], rings[1:])


async def building_at(fetcher: Fetcher, x: float, y: float, search_radius_m: int = 40) -> Building | None:
    """The building footprint containing (x, y) in EPSG:3857, or the nearest one within the radius."""
    features = await _query(
        fetcher,
        config.LAYER_BUILDINGS,
        geometry=f"{x},{y}",
        geometryType="esriGeometryPoint",
        inSR=3857,
        outSR=3857,
        spatialRel="esriSpatialRelIntersects",
        distance=search_radius_m,
        units="esriSRUnit_Meter",
        outFields="id_binyan,year,ms_komot,t_sug_mivne,shem_mivne",
        returnGeometry="true",
    )
    point = Point(x, y)
    best: tuple[float, dict, Polygon] | None = None
    for feature in features:
        polygon = _polygon(feature.get("geometry"))
        if polygon is None or not feature["attributes"].get("id_binyan"):
            continue
        distance = 0.0 if polygon.covers(point) else polygon.distance(point)
        if best is None or distance < best[0]:
            best = (distance, feature, polygon)
    if best is None:
        return None
    _, feature, polygon = best
    a = feature["attributes"]
    inside = polygon.representative_point()
    lon, lat = to_lonlat(inside.x, inside.y)
    outline = [[to_lonlat(px, py) for px, py in ring.coords] for ring in [polygon.exterior, *polygon.interiors]]
    year = a.get("year")
    return Building(
        building_id=int(a["id_binyan"]),
        x=inside.x,
        y=inside.y,
        lat=lat,
        lon=lon,
        outline=outline,
        year_built=int(year) if year and int(year) > 1800 else None,
        floors=a.get("ms_komot") or None,
        building_type=(a.get("t_sug_mivne") or None),
        name=(a.get("shem_mivne") or None),
    )


async def plans_at(fetcher: Fetcher, building: Building) -> list[Plan]:
    """Zoning plans (תב"ע) covering the building, excluding city-wide ones."""
    features = await _query(
        fetcher,
        config.LAYER_PLANS,
        geometry=f"{building.x},{building.y}",
        geometryType="esriGeometryPoint",
        inSR=3857,
        spatialRel="esriSpatialRelIntersects",
        where=f"Shape_Area < {config.MAX_PLAN_AREA_M2}",
        outFields="id_taba,taba,shem_taba,t_status,tr_matan_tokef,tr_hafkada,sug_nose,url_documents,url_iplan,Shape_Area",
        returnGeometry="false",
    )
    plans: list[Plan] = []
    seen: set[str] = set()
    for feature in features:
        a = feature["attributes"]
        kind = QUOTES.sub("", a.get("sug_nose") or "")
        plan_no = (a.get("taba") or "").strip()
        if "תבע" not in kind or not plan_no or plan_no in seen:
            continue
        seen.add(plan_no)
        plans.append(
            Plan(
                plan_no=plan_no,
                id_taba=a.get("id_taba"),
                name=(a.get("shem_taba") or "").strip() or None,
                status=(a.get("t_status") or "").strip() or None,
                in_force_date=esri_date(a.get("tr_matan_tokef")),
                deposit_date=esri_date(a.get("tr_hafkada")),
                area_m2=a.get("Shape_Area"),
                documents_url=a.get("url_documents") or None,
                iplan_url=a.get("url_iplan") or None,
            )
        )

    def order(p: Plan) -> tuple[bool, date, str]:
        when = p.in_force_date or p.deposit_date
        return (when is None, when or date.max, p.plan_no)

    plans.sort(key=order)
    return plans
