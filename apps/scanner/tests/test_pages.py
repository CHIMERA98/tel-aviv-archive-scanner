"""Page rendering: tiles for long sheets and contact sheets. No network."""

import itertools

import pymupdf
from PIL import Image

from scanner import config
from scanner.render import pages


def test_tile_spans_cover_the_sheet_with_overlap():
    spans = pages.tile_spans(6000, 1200)
    assert spans[0] == (0, config.TILE_EDGE)
    assert spans[-1][1] == 6000
    assert all(b[0] < a[1] for a, b in itertools.pairwise(spans))  # neighbours overlap
    assert pages.tile_spans(1600, 1200) == []  # fits in one image
    assert pages.tile_spans(2000, 1500) == []  # not long enough to need tiles


def test_tile_scale_skips_pages_without_extra_detail():
    assert pages.tile_scale(800, 600, native=4.0) is None  # ordinary page
    assert pages.tile_scale(1600, 89, native=1.0) is None  # a strip with no more pixels than its page image
    scale = pages.tile_scale(50_000, 2_800, native=1.0)
    assert scale is not None
    long = 50_000 * scale
    assert len(pages.tile_spans(round(long), round(2_800 * scale))) <= config.MAX_TILES_PER_PAGE


def test_long_raster_gets_tiles_and_a_contact_sheet(tmp_path):
    source = tmp_path / "A-001.jpg"
    Image.new("RGB", (12_000, 1_500), "white").save(source)
    rendered = pages.render_pages(source, tmp_path / "out", "A-001")
    assert rendered.total == 1
    assert len(rendered.pages) == 1
    assert len(rendered.tiles[0]) > 1
    assert all(max(Image.open(t).size) <= config.TILE_EDGE for t in rendered.tiles[0])
    [(sheet, cells)] = rendered.sheets
    assert sheet.exists()
    assert cells == [f"p1.t{n}" for n in range(1, len(rendered.tiles[0]) + 1)]


def test_pdf_pages_fill_several_contact_sheets(tmp_path):
    source = tmp_path / "A-002.pdf"
    doc = pymupdf.open()
    for _ in range(config.SHEET_CELLS + 2):
        doc.new_page(width=595, height=842)
    doc.new_page(width=4000, height=800).insert_text((100, 400), "elevation", fontsize=60)
    doc.save(source)
    rendered = pages.render_pages(source, tmp_path / "out", "A-002")
    assert rendered.tiles[0] == []
    assert len(rendered.tiles[-1]) > 1
    assert [len(cells) for _, cells in rendered.sheets] == [config.SHEET_CELLS, 2 + len(rendered.tiles[-1])]
    assert rendered.sheets[1][1][-1] == f"p{config.SHEET_CELLS + 3}.t{len(rendered.tiles[-1])}"
