"""Haifa: Complot parsers on recorded pages, the paced client, priorities and outputs. No network."""

import datetime as dt
import json
import time
from pathlib import Path

import httpx
import pytest
import respx

from scanner import cities, config, haifa_scan, haifa_worker
from scanner.models import AddressMatch, Building, ScanManifest
from scanner.render import requests_md
from scanner.sources import complot, gis, haifa_gis, mapwarper
from scanner.sources.complot import ComplotBlockedError, ComplotClient, LocalCache

FIXTURES = Path(__file__).parent / "fixtures" / "haifa" / "complot"


def page(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8-sig")


# ---------- parsers ----------


def test_building_files_by_address():
    files = complot.parse_building_files(page("tikim_by_address_bengurion10.html"))
    assert [f.number for f in files] == ["334477", "345544", "482776"]
    assert files[0].address == "שד' בן גוריון 10 חיפה"
    assert (files[2].gush, files[2].helka) == ("10830", "6")


def test_no_results_page():
    assert complot.parse_building_files(page("no_results.html")) == []


def test_building_file_requests_merge_permit_rows():
    building_file = complot.parse_building_file(page("tik_file_20130857.html"), "20130857")
    assert len(building_file.requests) == 8
    latest = building_file.requests[-1]
    assert latest.request_no == "2013085707"
    assert latest.submitted == dt.date(2023, 9, 19)
    assert (latest.permit_no, latest.permit_date) == ("2013085707", dt.date(2025, 9, 15))
    assert building_file.requests[0].permit_no is None


def test_request_page():
    request, documents = complot.parse_request(page("request_2013085707.html"), "2013085707", "20130857")
    assert request.request_type == "בקשה להיתר - ללא הקלות"
    assert request.main_use == "מלונאות"
    assert request.online_request_no == "10000279680"
    assert request.essence[0].startswith("תכנית שינויים + מצב סופי למלון")
    assert ("עורך", "גיא ארבל") in request.parties
    assert len(request.events) == 39 and request.events[0].date == dt.date(2025, 12, 16)
    assert request.decisions[0] == "לאשר את הבקשה בתנאים הבאים:"
    assert len(documents) == 8
    assert documents[0].guid == "F04FD286-C3A8-4E75-9822-F69556D1F6C9"
    assert documents[0].name == "היתר בניה חתום" and documents[0].date == dt.date(2025, 9, 18)


def test_old_request_page():
    request, documents = complot.parse_request(page("request_33447701.html"), "33447701", "334477")
    assert request.submitted == dt.date(1988, 5, 2)
    assert request.essence == []  # "לא נמצאו נתונים"
    assert ("מבקש", "יבין יעקב") in request.parties
    assert len(request.events) == 29
    assert len(documents) == 2


def test_plan_page_and_file_choice():
    plan = complot.parse_plan(page("taba_583.html"))
    assert plan.status == "בתוקף"
    assert plan.status_date == dt.date(1966, 1, 20)
    assert plan.deposit_date == dt.date(1961, 3, 23)
    assert len(plan.files) == 4
    assert [f.name for f in complot.pick_plan_files(plan.files)] == ["תשריט", "תקנון"]
    assert complot.pick_plan_files(plan.files)[0].args == ("201", "583", "0", "1")


def test_download_links():
    assert complot.parse_download_link(page("document_ifn.html")).endswith(
        "/handasi2016/cache/HFA/IFN/1.F04FD286-C3A8-4E75-9822-F69556D1F6C9.pdf"
    )
    assert complot.parse_download_link(page("show_photo.html")) == (
        "https://archive.gis-net.co.il/Haifa/files/taba/901-1000/981/981-col.pdf"
    )
    with pytest.raises(complot.ComplotError):
        complot.parse_download_link(page("error_page.html"))


def test_page_url_matches_the_site():
    assert complot.page_url("GetTikimByAddress", c=4000, s=399, h=10, l="true") == (
        "https://handasi.complot.co.il/magicscripts/mgrqispi.dll?appname=cixpa&prgname=GetTikimByAddress"
        "&siteid=16&c=4000&s=399&h=10&l=true&arguments=siteid,c,s,h,l"
    )


# ---------- client ----------


@respx.mock
async def test_client_caches_pages(tmp_path):
    route = respx.get(url__startswith=config.COMPLOT_PAGES).mock(
        return_value=httpx.Response(200, text=page("tik_file_20130857.html"))
    )
    cache = LocalCache(tmp_path)
    async with ComplotClient(cache, interval=0) as client:
        await client.building_file("20130857")
    async with ComplotClient(cache, interval=0) as client:
        building_file = await client.building_file("20130857")
        assert client.calls == 0
    assert route.call_count == 1
    assert len(building_file.requests) == 8


@respx.mock
async def test_client_never_retries_a_block(tmp_path):
    route = respx.get(url__startswith=config.COMPLOT_PAGES).mock(return_value=httpx.Response(429))
    async with ComplotClient(LocalCache(tmp_path), interval=0) as client:
        with pytest.raises(ComplotBlockedError):
            await client.building_file("1")
    assert route.call_count == 1


@respx.mock
async def test_client_paces_page_calls(tmp_path):
    respx.get(url__startswith=config.COMPLOT_PAGES).mock(return_value=httpx.Response(200, text="<html></html>"))
    async with ComplotClient(LocalCache(tmp_path), interval=0.3) as client:
        started = time.monotonic()
        await client.page("A", x=1)
        await client.page("A", x=2)
        await client.page("A", x=3)
    assert time.monotonic() - started >= 0.6


@respx.mock
async def test_document_needs_ifn_and_rejects_error_pages(tmp_path):
    respx.get(url__startswith=config.COMPLOT_PAGES).mock(
        return_value=httpx.Response(200, text=page("document_ifn.html"))
    )
    pdf = respx.get(url__startswith=f"{config.COMPLOT_BASE}/handasi2016/cache/").mock(
        return_value=httpx.Response(200, content=b"%PDF-1.7 test")
    )
    async with ComplotClient(LocalCache(tmp_path / "cache"), interval=0) as client:
        path = await client.document("F04FD286-C3A8-4E75-9822-F69556D1F6C9", tmp_path / "out" / "A-001.pdf")
        assert path.read_bytes().startswith(b"%PDF")
        # Second time: from the cache, no page call.
        await client.document("F04FD286-C3A8-4E75-9822-F69556D1F6C9", tmp_path / "out" / "A-001.pdf")
        assert client.calls == 1 and pdf.call_count == 1

        pdf.mock(return_value=httpx.Response(200, content=page("error_page.html").encode("utf-8-sig")))
        with pytest.raises(complot.ComplotError):
            await client.document("00000000-0000-0000-0000-000000000000", tmp_path / "out" / "A-002.pdf")


NO_LINK = "<html><body><p>No link here</p></body></html>"  # what Complot answered during the 2026-11-02 silent block
REJECTED = (
    "<html><body>The requested URL was rejected. Please consult with your administrator.<br>"
    "Your support ID is: 1</body></html>"
)


@respx.mock
async def test_silent_block_on_document_links(tmp_path):
    ifn = respx.get(url__startswith=config.COMPLOT_PAGES).mock(return_value=httpx.Response(200, text=NO_LINK))
    respx.get(url__startswith=f"{config.COMPLOT_BASE}/handasi2016/cache/").mock(
        return_value=httpx.Response(200, content=b"%PDF-1.7 test")
    )
    cache = LocalCache(tmp_path / "cache")
    async with ComplotClient(cache, interval=0) as client:
        # One document without a link may just lack its file; a good answer after it clears the doubt.
        with pytest.raises(complot.ComplotSuspectError):
            await client.document("A", tmp_path / "A.pdf")
        ifn.mock(return_value=httpx.Response(200, text=page("document_ifn.html")))
        await client.document("B", tmp_path / "B.pdf")
        assert client.suspects == 0
        # Two in a row: a block, and no third call.
        ifn.mock(return_value=httpx.Response(200, text=NO_LINK))
        with pytest.raises(complot.ComplotSuspectError):
            await client.document("C", tmp_path / "C.pdf")
        with pytest.raises(ComplotBlockedError):
            await client.document("D", tmp_path / "D.pdf")
        assert client.calls == 4
    # The first suspect page is kept for diagnosis, once.
    assert [p.read_text(encoding="utf-8") for p in (tmp_path / "cache" / "debug").iterdir()] == [NO_LINK]


@respx.mock
async def test_suspect_page_is_retried_once_and_never_cached(tmp_path):
    route = respx.get(url__startswith=config.COMPLOT_PAGES).mock(
        side_effect=[httpx.Response(200, text=NO_LINK), httpx.Response(200, text=page("tik_file_20130857.html"))]
    )
    cache = LocalCache(tmp_path)
    async with ComplotClient(cache, interval=0) as client:
        assert len((await client.building_file("20130857")).requests) == 8
        assert route.call_count == 2 and client.suspects == 0

    route.side_effect = [httpx.Response(200, text=NO_LINK)] * 2
    async with ComplotClient(cache, interval=0) as client:
        with pytest.raises(ComplotBlockedError):
            await client.request("1", "2")
    assert not list((tmp_path / "pages").glob("GetBakashaFile/*"))


@respx.mock
async def test_rejected_page_is_a_block_at_once(tmp_path):
    route = respx.get(url__startswith=config.COMPLOT_PAGES).mock(return_value=httpx.Response(200, text=REJECTED))
    async with ComplotClient(LocalCache(tmp_path), interval=0) as client:
        with pytest.raises(ComplotBlockedError):
            await client.building_file("1")
    assert route.call_count == 1


@respx.mock
async def test_genuine_pages_are_not_suspect(tmp_path):
    for name in ("no_results.html", "error_page.html", "tikim_by_address_bengurion10.html"):
        respx.get(url__startswith=config.COMPLOT_PAGES).mock(return_value=httpx.Response(200, text=page(name)))
        async with ComplotClient(LocalCache(tmp_path / name), interval=0) as client:
            await client.building_files(399, 10)
            assert client.suspects == 0 and client.calls == 1


@respx.mock
async def test_document_without_link_gets_the_suspect_note(tmp_path):
    respx.get(url__startswith=config.COMPLOT_PAGES).mock(return_value=httpx.Response(200, text=NO_LINK))
    doc = haifa_scan.archive_documents(_entries()[1])[0]
    async with ComplotClient(LocalCache(tmp_path / "cache"), interval=0) as client:
        await haifa_scan.fetch_document(client, doc, tmp_path)
        assert (doc.status, doc.note) == ("failed", haifa_scan.SUSPECT_NOTE)
        with pytest.raises(ComplotBlockedError):
            await haifa_scan.fetch_document(client, haifa_scan.archive_documents(_entries()[1])[1], tmp_path)


@respx.mock
async def test_worker_puts_back_the_document_before_a_silent_block(tmp_path, monkeypatch):
    edge = "https://edge.test"
    monkeypatch.setenv("EDGE_URL", edge)
    monkeypatch.setenv("SCANNER_HMAC_KEY", "k")
    manifest = _manifest(tmp_path)
    first, second = manifest.documents[0].doc_id, manifest.documents[1].doc_id
    work = [{"kind": "document", "scan_id": "S", "doc_id": d, "interval_s": 0.001} for d in (first, second)]
    respx.post(f"{edge}/internal/haifa/next").mock(side_effect=[httpx.Response(200, json=w) for w in work])
    respx.post(f"{edge}/internal/haifa/heartbeat").mock(return_value=httpx.Response(200, json={}))
    respx.get(f"{edge}/internal/scans/S/files/manifest.json").mock(
        return_value=httpx.Response(200, content=manifest.model_dump_json().encode())
    )
    respx.put(url__startswith=f"{edge}/internal/").mock(return_value=httpx.Response(200, json={}))
    respx.get(url__startswith=f"{edge}/internal/haifa/cache/").mock(return_value=httpx.Response(404))
    reports = respx.post(url__startswith=f"{edge}/internal/haifa/scans/S/documents/").mock(
        return_value=httpx.Response(200, json={"pending": 1})
    )
    blocked = respx.post(f"{edge}/internal/haifa/blocked").mock(return_value=httpx.Response(200, json={}))
    complot_pages = respx.get(url__startswith=config.COMPLOT_PAGES).mock(return_value=httpx.Response(200, text=NO_LINK))

    assert await haifa_worker.run() == 2
    sent = [(r.request.url.path.rsplit("/", 1)[-1], json.loads(r.request.content)["status"]) for r in reports.calls]
    assert sent == [(first, "failed"), (first, "pending")]
    assert blocked.call_count == 1 and complot_pages.call_count == 2


# ---------- priorities and documents ----------


def test_document_priorities():
    assert haifa_scan.rank("היתר בניה חתום", "היתר בניה") == 0
    assert haifa_scan.rank("תכנית היתר חתומה", "שרטוטים ותרשימים") == 1
    assert haifa_scan.rank("מכתב החלטה", "כתובות החלטות ופרוטוקולים של מוסדות התכנון") == 2
    assert haifa_scan.rank("תכנית ראשית", "שרטוטים ותרשימים") == 3
    assert haifa_scan.rank("גרמושקה לחישוב אגרות", "שרטוטים ותרשימים") == 9


def _entries():
    new, new_docs = complot.parse_request(page("request_2013085707.html"), "2013085707", "20130857")
    old, old_docs = complot.parse_request(page("request_33447701.html"), "33447701", "334477")
    return [new, old], [(new, d) for d in new_docs] + [(old, d) for d in old_docs]


def test_archive_documents_and_first_batch():
    _, entries = _entries()
    docs = haifa_scan.archive_documents(entries)
    assert [d.doc_id for d in docs][:2] == ["A-001", "A-002"]
    assert all(d.status == "pending" and d.guid for d in docs)
    assert docs[0].date <= docs[-1].date  # ids follow dates
    batch = haifa_scan.first_batch(docs)
    titles = [d.title for d in batch]
    # Newest request's signed permit first, then the old permits, then signed plan and decision.
    assert titles[0] == "היתר בניה חתום"
    assert "תכנית היתר חתומה" in titles and "מכתב החלטה" in titles
    assert "גרמושקה לחישוב אגרות" not in titles


def _manifest(tmp_path) -> ScanManifest:
    requests, entries = _entries()
    address = AddressMatch(
        city="haifa",
        street_code=399,
        street_he="בן גוריון",
        house_number=50,
        label_he="בן גוריון 50, חיפה",
        lat=32.8186,
        lon=34.9899,
        x=0,
        y=0,
        gush=11676,
        helka=3002,
        neighborhood="המושבה הגרמנית",
    )
    building = Building(city="haifa", key="haifa:399:50", x=0, y=0, lat=32.8186, lon=34.9899, height_m=60.6)
    return ScanManifest(
        scan_id="t",
        city="haifa",
        created_at=dt.datetime.now(dt.UTC),
        address=address,
        building=building,
        building_files=["20130857", "334477"],
        requests=requests,
        documents=haifa_scan.archive_documents(entries),
    )


def test_requests_md(tmp_path):
    text = requests_md.render(_manifest(tmp_path))
    assert "## בקשה 33447701 · הוגשה 02/05/1988" in text
    assert text.index("33447701") < text.index("2013085707")  # oldest first
    assert 'פס"ד - בניה ללא היתר' in text  # key events kept
    assert "שליחת דרישות התשלום" not in text  # office routine left out
    assert "(עדיין לא הורד)" in text


def test_haifa_prompts_and_outputs(tmp_path):
    from scanner.pipeline import write_outputs
    from scanner.render import prompts

    manifest = _manifest(tmp_path)
    assert "Haifa Archive Scanner" in prompts.prompt_en(manifest)
    assert "requests.md" in prompts.prompt_he(manifest)
    assert "documents were still downloading" in prompts.prompt_en(manifest)
    write_outputs(manifest, tmp_path)
    assert (tmp_path / "requests.md").exists() and manifest.outputs["requests"] == "requests.md"
    assert "status" in (tmp_path / "index.csv").read_text(encoding="utf-8-sig").splitlines()[0]


# ---------- GIS helpers and maps ----------


def test_ring_area_handles_holes():
    outer = [[0, 0], [0, 100], [100, 100], [100, 0], [0, 0]]  # clockwise, as Esri writes outer rings
    hole = [[10, 10], [20, 10], [20, 20], [10, 20], [10, 10]]
    assert haifa_gis.ring_area([outer]) == 10_000
    assert haifa_gis.ring_area([outer, hole]) == 9_900


def test_street_types_are_optional():
    streets = [gis.Street(399, "בן גוריון", None), gis.Street(1, "שדרות הלוטם", None), gis.Street(2, "בן יהודה", None)]
    assert haifa_gis.match_streets("שדרות בן גוריון", streets)[0].code == 399
    assert haifa_gis.match_streets("שדרות הלוטם", streets)[0].code == 1


def test_haifa_address_parsing():
    parsed = gis.parse_address("שד' בן גוריון 10א, חיפה", haifa_gis.CITY_WORDS)
    assert parsed is not None and (parsed.number, parsed.entrance) == (10, "א")


def test_city_detection():
    assert cities.detect_city("בן גוריון 10, חיפה") == "haifa"
    assert cities.detect_city("Ben Gurion 10 Haifa") == "haifa"
    assert cities.detect_city("לבונטין 15") == "tel-aviv"
    assert cities.parse_city("haifa") == "haifa" and cities.parse_city("jerusalem") is None


def test_mapwarper_coverage():
    assert len(mapwarper.maps_at(32.8186, 34.9899)) >= 3  # German Colony
    assert mapwarper.maps_at(32.7600, 35.0200) == []  # Carmel ridge, outside every map


def test_palopenmaps_name_their_source_and_unknown_rights():
    maps = haifa_scan.haifa_maps(32.7600, 35.0200)  # Carmel: Palestine Open Maps only
    assert [m.map_id for m in maps] == ["pal63k-1880", "pal-1940s", "pal20k-1940s", "aerial1940s"]  # oldest first
    for m in maps:
        assert m.credit and "Palestine Open Maps (palopenmaps.org), layer" in m.credit
        assert m.credit.endswith(config.PALOPENMAPS_RIGHTS) and "Publishing rights unknown" in m.credit
        assert haifa_scan.map_link(m) == "https://palopenmaps.org/"
    by_id = {m.map_id: m.credit or "" for m in maps}
    assert "Survey of Palestine. Scan: National Library of Israel" in by_id["pal-1940s"]
    assert "Royal Air Force" in by_id["aerial1940s"] and "Shaul Holtzman" in by_id["aerial1940s"]
    assert "Palestine Exploration Fund" in by_id["pal63k-1880"]
    # Downtown also keeps the mapwarper maps, which carry no credit of their own.
    downtown = haifa_scan.haifa_maps(32.8186, 34.9899)
    assert any(m.folder == "mapwarper" and m.credit is None for m in downtown)


def test_map_caption_is_printed_under_the_image():
    from PIL import Image

    from scanner.sources import mapjunction

    canvas = Image.new("RGB", (1024, 1024), (200, 220, 240))
    out = mapjunction._with_caption(canvas, "Source: " + "word " * 60 + config.PALOPENMAPS_RIGHTS)
    assert out.width == 1024 and out.height > 1024 + 40  # wrapped onto several lines
    strip = out.crop((0, 1024, 1024, out.height))
    assert strip.getextrema() != ((255, 255), (255, 255), (255, 255))  # text was drawn


def test_credited_maps_reach_prompts_and_slides(tmp_path):
    from PIL import Image

    from scanner.models import Document
    from scanner.pipeline import write_outputs
    from scanner.render import prompts

    manifest = _manifest(tmp_path)
    assert "publishing rights are unknown" not in prompts.prompt_en(manifest)
    credit = haifa_scan.haifa_maps(32.76, 35.02)[1].credit
    (tmp_path / "maps").mkdir()
    Image.new("RGB", (64, 64)).save(tmp_path / "maps" / "M-001.jpg")
    manifest.documents.append(
        Document(
            doc_id="M-001",
            source="map",
            title="City map",
            file="maps/M-001.jpg",
            page_images=["maps/M-001.jpg"],
            original_url="https://palopenmaps.org/",
            note=credit,
            pages=1,
        )
    )
    assert "publishing rights are unknown" in prompts.prompt_en(manifest)
    assert "זכויות הפרסום שלהן אינן ידועות" in prompts.prompt_he(manifest)
    write_outputs(manifest, tmp_path)
    assert "Publishing rights unknown" in (tmp_path / "index.csv").read_text(encoding="utf-8-sig")
    from pptx import Presentation

    texts = [
        shape.text_frame.text
        for slide in Presentation(str(tmp_path / "maps_plans.pptx")).slides
        for shape in slide.shapes
        if shape.has_text_frame
    ]
    assert any("Palestine Open Maps" in t and "Publishing rights unknown" in t for t in texts)


def test_building_key():
    assert Building(building_id=2287, x=0, y=0, lat=0, lon=0).key == "tel-aviv:2287"
    assert (
        AddressMatch(
            city="haifa", street_code=1, street_he="מוריה", house_number=5, label_he="x", lat=0, lon=0, x=0, y=0
        ).label_en
        == "מוריה 5, Haifa"
    )
