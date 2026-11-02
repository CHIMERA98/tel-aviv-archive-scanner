"""Haifa GIS (ArcGIS PublicSite): addresses, footprints, conservation, current parcels and zoning plans.

Geometry comes back in WGS84 (outSR=4326) except plans, which are fetched in EPSG:2039 so their area is in m².
None of this touches Complot, so lookups are free to run often.
"""

import re

from shapely.geometry import Point, Polygon

from scanner import config
from scanner.geo import to_lonlat, to_mercator
from scanner.http import Fetcher
from scanner.models import AddressMatch, Building, Conservation, Plan
from scanner.sources import gis

CITY_WORDS = re.compile(r"חיפה|ישראל|haifa|israel", re.IGNORECASE)
# Haifa's register names most streets without their type ("בן גוריון"), but people type it ("שדרות בן גוריון").
STREET_TYPES = re.compile(r"^(?:שדרות|שד'?|רחוב|רח'?|דרך|כיכר)\s+")
PAGE = 2000


async def _query(fetcher: Fetcher, layer_url: str, **params: str | int) -> list[dict]:
    data = await fetcher.get_json(f"{layer_url}/query", params={"f": "json", **params})
    if "error" in data:
        raise RuntimeError(f"Haifa GIS query failed ({layer_url.rsplit('/', 3)[-3]}): {data['error']}")
    return data.get("features", [])


def _at_point(lat: float, lon: float, distance_m: int | None = None) -> dict[str, str | int]:
    params: dict[str, str | int] = {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
    }
    if distance_m:
        params |= {"distance": distance_m, "units": "esriSRUnit_Meter"}
    return params


def _text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


async def load_streets(fetcher: Fetcher) -> list[gis.Street]:
    """Every street with at least one address. Codes are the ones Complot uses."""
    streets: dict[int, gis.Street] = {}
    offset = 0
    while True:
        features = await _query(
            fetcher,
            config.HAIFA_ADDRESSES,
            where="1=1",
            outFields="STREET_COD,STREET_NAM",
            returnDistinctValues="true",
            returnGeometry="false",
            orderByFields="STREET_COD",
            resultOffset=offset,
            resultRecordCount=PAGE,
        )
        for feature in features:
            a = feature["attributes"]
            name = _text(a.get("STREET_NAM"))
            if a.get("STREET_COD") is not None and name:
                streets[int(a["STREET_COD"])] = gis.Street(int(a["STREET_COD"]), name, None)
        if len(features) < PAGE:
            return list(streets.values())
        offset += len(features)


def match_streets(query: str, streets: list[gis.Street], limit: int = 5) -> list[gis.Street]:
    ranked = gis.match_streets(query, streets, limit)
    bare = STREET_TYPES.sub("", query.strip())
    if bare != query.strip():
        ranked += [s for s in gis.match_streets(bare, streets, limit) if s not in ranked]
    return ranked[:limit]


async def search_addresses(
    fetcher: Fetcher, text: str, streets: list[gis.Street], limit: int = 8
) -> list[AddressMatch]:
    parsed = gis.parse_address(text, CITY_WORDS)
    if parsed is None:
        return []
    candidates = match_streets(parsed.street, streets)
    if not candidates:
        return []
    codes = ",".join(str(s.code) for s in candidates)
    features = await _query(
        fetcher,
        config.HAIFA_ADDRESSES,
        where=f"STREET_COD IN ({codes}) AND BLDG_NUM={parsed.number}",
        outFields="STREET_COD,STREET_NAM,BLDG_NUM,BLDG_LETTE,FullAddress,neighborhood",
        returnGeometry="true",
        outSR=4326,
    )
    rank = {s.code: i for i, s in enumerate(candidates)}
    seen: set[tuple[int, int, str]] = set()
    matches: list[AddressMatch] = []
    for feature in features:
        a, point = feature["attributes"], feature.get("geometry") or {}
        if point.get("x") is None or point.get("y") is None:
            continue
        entrance = (a.get("BLDG_LETTE") or "").strip()
        ident = (int(a["STREET_COD"]), int(a["BLDG_NUM"]), entrance)
        if ident in seen:  # the layer repeats many points
            continue
        seen.add(ident)
        lon, lat = float(point["x"]), float(point["y"])
        x, y = to_mercator(lon, lat)
        street = (a.get("STREET_NAM") or "").strip()
        matches.append(
            AddressMatch(
                city="haifa",
                street_code=ident[0],
                street_he=street,
                house_number=ident[1],
                entrance=entrance,
                label_he=f"{_text(a.get('FullAddress')) or f'{street} {ident[1]}{entrance}'}, חיפה",
                lat=lat,
                lon=lon,
                x=x,
                y=y,
                neighborhood=_text(a.get("neighborhood")),
            )
        )

    def order(m: AddressMatch) -> tuple[int, int, str]:
        return (rank.get(m.street_code, 99), 0 if m.entrance == parsed.entrance else 1, m.entrance)

    matches.sort(key=order)
    return matches[:limit]


def _ring_polygon(rings: list[list[list[float]]]) -> Polygon | None:
    if not rings or len(rings[0]) < 3:
        return None
    return Polygon(rings[0], rings[1:])


async def _footprint(fetcher: Fetcher, address: AddressMatch) -> tuple[Polygon, dict] | None:
    """The footprint containing the address point, or the nearest one within 30 m, in EPSG:3857."""
    features = await _query(
        fetcher,
        config.HAIFA_BUILDINGS,
        **_at_point(address.lat, address.lon, 30),
        outFields="Elevation,GlobalID",
        returnGeometry="true",
        outSR=4326,
    )
    point = Point(address.x, address.y)
    best: tuple[float, Polygon, dict] | None = None
    for feature in features:
        rings = (feature.get("geometry") or {}).get("rings") or []
        projected = [[to_mercator(lon, lat) for lon, lat, *_ in ring] for ring in rings]
        polygon = _ring_polygon(projected)  # type: ignore[arg-type]
        if polygon is None or not polygon.is_valid:
            continue
        distance = 0.0 if polygon.covers(point) else polygon.distance(point)
        if best is None or distance < best[0]:
            best = (distance, polygon, feature["attributes"])
    return (best[1], best[2]) if best else None


async def conservation_at(fetcher: Fetcher, address: AddressMatch) -> Conservation | None:
    features = await _query(
        fetcher,
        config.HAIFA_CONSERVATION,
        **_at_point(address.lat, address.lon, 8),
        outFields="SiteName,status,shimur_value,complot,street,bldg_num,seker_date,sokrim_names",
        returnGeometry="false",
    )
    # Prefer the entry registered at this house number; the 8 m search can catch the neighbour.
    features.sort(key=lambda f: str(f["attributes"].get("bldg_num") or "").strip() != str(address.house_number))
    for feature in features:
        a = feature["attributes"]
        return Conservation(
            status=_text(a.get("status")),
            name=_text(a.get("SiteName")),
            grade=_text(a.get("shimur_value")),
            file_no=_text(a.get("complot")),
            survey=_text(a.get("seker_date")),
            surveyors=_text(a.get("sokrim_names")),
        )
    return None


async def parcel_at(fetcher: Fetcher, address: AddressMatch) -> tuple[int, int] | None:
    """Today's gush/helka at the point (Complot's own parcel numbers can be decades old)."""
    features = await _query(
        fetcher,
        config.HAIFA_PLAN_PARCELS,
        **_at_point(address.lat, address.lon),
        outFields="Gush,Helka",
        returnGeometry="false",
    )
    for feature in features:
        a = feature["attributes"]
        if a.get("Gush") and a.get("Helka") is not None:
            return int(a["Gush"]), int(a["Helka"])
    return None


async def building_at(fetcher: Fetcher, address: AddressMatch) -> Building:
    """Haifa's archive is keyed by address, so there is always a building, with or without a footprint."""
    found = await _footprint(fetcher, address)
    parcel = await parcel_at(fetcher, address)
    if parcel:
        address.gush, address.helka = parcel
    conservation = await conservation_at(fetcher, address)
    key = f"haifa:{address.street_code}:{address.house_number}"
    if found is None:
        return Building(
            city="haifa", key=key, x=address.x, y=address.y, lat=address.lat, lon=address.lon, conservation=conservation
        )
    polygon, attributes = found
    inside = polygon.representative_point()
    lon, lat = to_lonlat(inside.x, inside.y)
    outline = [[to_lonlat(px, py) for px, py in ring.coords] for ring in [polygon.exterior, *polygon.interiors]]
    elevation = attributes.get("Elevation")
    return Building(
        city="haifa",
        key=key,
        x=inside.x,
        y=inside.y,
        lat=lat,
        lon=lon,
        outline=outline,
        height_m=round(float(elevation), 1) if elevation else None,
        name=conservation.name if conservation else None,
        conservation=conservation,
    )


def ring_area(rings: list[list[list[float]]]) -> float:
    """Area of an Esri polygon in its own units. Outer rings and holes wind opposite ways, so they cancel correctly."""
    total = 0.0
    for ring in rings:
        total += sum(ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1] for i in range(len(ring) - 1)) / 2
    return abs(total)


async def plans_at(fetcher: Fetcher, building: Building) -> list[Plan]:
    """Approved and deposited plans covering the building, without the city-wide ones."""
    plans: list[Plan] = []
    seen: set[str] = set()
    for layer, default_status in ((config.HAIFA_PLANS_APPROVED, "בתוקף"), (config.HAIFA_PLANS_DEPOSITED, "בהפקדה")):
        features = await _query(
            fetcher,
            layer,
            **_at_point(building.lat, building.lon),
            outFields="taba_name,Taba_Num,Taba_Numer,Ofi",
            returnGeometry="true",
            outSR=config.HAIFA_SR,
            maxAllowableOffset=20,  # metres; plenty for an area estimate and keeps city-wide shapes small
        )
        for feature in features:
            a = feature["attributes"]
            plan_no = _text(a.get("Taba_Num"))
            area = ring_area((feature.get("geometry") or {}).get("rings") or [])
            if not plan_no or plan_no in seen or area > config.HAIFA_MAX_PLAN_AREA_M2:
                continue
            seen.add(plan_no)
            number = a.get("Taba_Numer")
            plans.append(
                Plan(
                    plan_no=plan_no,
                    id_taba=int(number) if number else None,
                    name=_text(a.get("taba_name")),
                    status=_text(a.get("Ofi")) or default_status,
                    area_m2=round(area),
                    iplan_url=f"https://mavat.iplan.gov.il/SV3?text={plan_no}",
                )
            )
    return plans
