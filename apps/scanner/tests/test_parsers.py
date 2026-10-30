import datetime as dt

import pytest

from scanner.geo import esri_date, tile_float, to_lonlat, to_mercator
from scanner.sources import gis, handasa, mapjunction, taba

STREETS = [
    gis.Street(384, 'הגר"א', "HAGHARA"),
    gis.Street(45, "לבונטין", "LEVONTIN"),
    gis.Street(496, "אבן גבירול", "IBN GVIROL"),
    gis.Street(30, "שיבת ציון", "SHIVAT TSIYON"),
    gis.Street(41, "שדרות ירושלים", "SDEROT YERUSHALAYIM"),
]


@pytest.mark.parametrize(
    ("text", "street", "number", "entrance"),
    [
        ('הגר"א 27', 'הגר"א', 27, ""),
        ("הגרא 27, תל אביב-יפו", "הגרא", 27, ""),
        ("לבונטין 15א", "לבונטין", 15, "א"),
        ("Levontin 15, Tel Aviv", "Levontin", 15, ""),
        ("27 הגרא", "הגרא", 27, ""),
    ],
)
def test_parse_address(text, street, number, entrance):
    parsed = gis.parse_address(text)
    assert parsed is not None
    assert parsed.street == street
    assert parsed.number == number
    assert parsed.entrance == entrance


def test_parse_address_needs_number_and_street():
    assert gis.parse_address("הגרא") is None
    assert gis.parse_address("27") is None


@pytest.mark.parametrize(
    ("query", "code"),
    [("הגרא", 384), ('הגר"א', 384), ("רח' לבונטין", 45), ("levontin", 45), ("אבן גבירול", 496), ("שד ירושלים", 41)],
)
def test_match_streets(query, code):
    assert gis.match_streets(query, STREETS)[0].code == code


def test_mercator_round_trip():
    x, y = to_mercator(34.775618, 32.062037)
    lon, lat = to_lonlat(x, y)
    assert lon == pytest.approx(34.775618)
    assert lat == pytest.approx(32.062037)


def test_tile_float_matches_known_tile():
    tx, ty = tile_float(34.766, 32.0615, 18)
    assert (int(tx), int(ty)) == (156387, 106402)


def test_esri_date_uses_israeli_calendar_day():
    # 2018-12-01 22:00 UTC is already 2 December in Israel.
    assert esri_date(1543701600000) == dt.date(2018, 12, 2)
    assert esri_date(None) is None


def _row(**overrides):
    row = {
        "UniqueID": "{328A6B4A-2633-429F-A178-6B6B4C1D1942}",
        "FileExtension": "pdf",
        "TlvMPEngFolderId": "03840270",
        "TlvMPEngFolderStreetCodeHouseNum": "384_27",
        "TlvMPEngFolderBlocksParcels": "6977_22",
        "TlvMPEngDocumentType": "בקשה להתקנת ביתן אשפה",
        "TlvMPEngDocDate": "/Date(1543701600000)/",
        "TlvMPEngRequestNum": None,
        "TlvMPEngPermitNum": "12-345",
        "TlvMPEngOnlineReqNum": None,
    }
    row.update(overrides)
    return row


def test_parse_archive_results():
    payload = [
        {"SchemaVersion": "15.0.0.0", "ErrorInfo": None},
        {
            "ResultTables": [
                {
                    "TotalRows": 3,
                    "ResultRows": [
                        _row(),
                        _row(UniqueID="{abc}", TlvMPEngDocDate="/Date(2208981600000)/"),
                        _row(UniqueID="{def}", TlvMPEngDocDate="/Date(-2208996000000)/"),
                    ],
                }
            ]
        },
    ]
    records, total = handasa.parse_results(payload)
    assert total == 3
    first, second, third = records
    assert first.guid == "328A6B4A-2633-429F-A178-6B6B4C1D1942"
    assert first.date == dt.date(2018, 12, 2)
    assert first.permit_no == "12-345"
    assert first.request_no is None
    assert first.street_house == "384_27"
    assert first.download_url.endswith("DocViewer.aspx?id=328A6B4A-2633-429F-A178-6B6B4C1D1942")
    assert second.guid == "ABC"
    assert second.date is None  # 1.1.2040 means "no date"
    assert third.date is None  # so does 1.1.1900


def test_parse_archive_error_raises():
    with pytest.raises(RuntimeError, match="UseRemoteAPIs"):
        handasa.parse_results([{"ErrorInfo": {"ErrorMessage": "needs UseRemoteAPIs"}}])


def test_archive_request_body_carries_safe_template_and_building():
    body = handasa._request_body(2287, 500)
    assert "SafeQueryPropertiesTemplateUrl" in body
    assert "buildingId=2287" in body
    assert '<Parameter Type="Number">500</Parameter>' in body
    assert "&lt;&gt;" in body  # the <> operators must be XML-escaped


def test_taba_extract_prefers_pdf_over_jpg():
    html = """
      var a = "/taba_raster/000000064101102.pdf"; SRC=" + ls_fullpath + "
      '/taba_raster/000000064102103.jpg' and '/taba_raster/000000064102103.pdf'
      <a href="/taba_raster/000000064101102.pdf">again</a>
    """
    assert taba.extract_files(html) == [
        "https://gisn.tel-aviv.gov.il/taba_raster/000000064101102.pdf",
        "https://gisn.tel-aviv.gov.il/taba_raster/000000064102103.pdf",
    ]


def test_select_maps_drops_world_layers_and_far_maps():
    world = {"southwest": {"lat": -85, "lng": -180}, "northeast": {"lat": 85, "lng": 180}}
    local = {"southwest": {"lat": 32.054, "lng": 34.757}, "northeast": {"lat": 32.069, "lng": 34.775}}
    far = {"southwest": {"lat": 32.10, "lng": 34.78}, "northeast": {"lat": 32.12, "lng": 34.80}}
    raw = [
        {"title": "Google Hybrid", "bbox": world, "tiles": "https://t/{z}/{x}/{y}", "meta": {"map_id": "g"}},
        {
            "title": "Survey 1936",
            "year": 1936,
            "bbox": local,
            "tiles": "https://t/a/{z}/{x}/{y}",
            "meta": {"map_id": "a"},
        },
        {"title": "Untitled plan", "bbox": local, "tiles": "https://t/b/{z}/{x}/{y}", "meta": {"map_id": "b"}},
        {
            "title": "Survey 1925",
            "year": 1925,
            "bbox": local,
            "tiles": "https://t/c/{z}/{x}/{y}",
            "meta": {"map_id": "c"},
        },
        {"title": "Elsewhere", "year": 1930, "bbox": far, "tiles": "https://t/d/{z}/{x}/{y}", "meta": {"map_id": "d"}},
    ]
    maps = mapjunction.select_maps(raw, 32.0615, 34.766)
    assert [m.map_id for m in maps] == ["c", "a", "b"]
