"""Page images for AI agents and slide previews.

Every page gets one image whose longest edge is PAGE_IMAGE_MAX_EDGE. Long sheets (plans, elevations) become an
unreadable strip at that size, so they are also cut into overlapping tiles along their length. Each document also
gets contact sheets: grids of small thumbnails of every page (and tile), so an AI can skim a whole document for
photographs or drawings in one image.
"""

import io
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFont

from scanner import config

Image.MAX_IMAGE_PIXELS = 400_000_000  # municipal plan scans are huge but trusted
XPS_SUFFIXES = (".dwfx", ".xps", ".oxps")


# Returns the region `box` of a page (in source units) rendered at `scale`.
Region = Callable[[tuple[float, float, float, float], float], Image.Image]


@dataclass
class RenderedPages:
    total: int  # pages in the source, which may be more than were rendered
    pages: list[Path] = field(default_factory=list)
    tiles: list[list[Path]] = field(default_factory=list)  # per rendered page; empty when it wasn't tiled
    sheets: list[tuple[Path, list[str]]] = field(default_factory=list)  # (image, cell labels in reading order)


def _save_jpeg(image: Image.Image, dest: Path, max_edge: int = config.PAGE_IMAGE_MAX_EDGE) -> None:
    image = image.convert("RGB")
    image.thumbnail((max_edge, max_edge))
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest, "JPEG", quality=config.PAGE_IMAGE_JPEG_QUALITY, optimize=True)


def tile_spans(long: int, short: int) -> list[tuple[int, int]]:
    """(start, end) along the long edge of a page `long` x `short` px, or [] when it doesn't need tiles."""
    if long <= config.TILE_EDGE or long < config.TILE_MIN_ASPECT * short:
        return []
    step = config.TILE_EDGE - config.TILE_OVERLAP
    starts = [min(i * step, long - config.TILE_EDGE) for i in range(math.ceil((long - config.TILE_EDGE) / step) + 1)]
    return [(start, start + config.TILE_EDGE) for start in starts]


def tile_scale(width: float, height: float, native: float) -> float | None:
    """Scale that brings the page's short edge to TILE_SHORT_EDGE within MAX_TILES_PER_PAGE tiles, or None when
    tiles wouldn't be sharper than the plain page image. `native` caps the scale (no blurry upscaling)."""
    long, short = max(width, height), min(width, height)
    if not short or long < config.TILE_MIN_ASPECT * short:
        return None
    step = config.TILE_EDGE - config.TILE_OVERLAP
    longest = config.TILE_EDGE + (config.MAX_TILES_PER_PAGE - 1) * step
    scale = min(config.TILE_SHORT_EDGE / short, longest / long, native)
    overview_short = short * min(config.PAGE_IMAGE_MAX_EDGE / long, native)
    return scale if short * scale >= config.TILE_MIN_GAIN * overview_short else None


def _tiles(image_at: Region, width: float, height: float, scale: float, dest: Path, prefix: str) -> list[Path]:
    """Cut a page into tiles along its long edge."""
    wide = width >= height
    long_px, short_px = round(max(width, height) * scale), round(min(width, height) * scale)
    written = []
    for n, (start, end) in enumerate(tile_spans(long_px, short_px), 1):
        a, b = start / scale, end / scale
        box = (a, 0, b, height) if wide else (0, a, width, b)
        out = dest / f"{prefix}-t{n:02d}.jpg"
        _save_jpeg(image_at(box, scale), out, max_edge=config.TILE_EDGE)
        written.append(out)
    return written


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except (OSError, TypeError):  # Pillow without FreeType: the small bitmap font
        return ImageFont.load_default()


def contact_sheets(cells: list[tuple[Path, str]], dest: Path, prefix: str) -> list[tuple[Path, list[str]]]:
    """Grids of labelled thumbnails, SHEET_COLUMNS wide and at most SHEET_CELLS per sheet."""
    size, label_h, gap = config.SHEET_THUMB_EDGE, 22, 6
    font = _font(16)
    sheets = []
    for first in range(0, len(cells), config.SHEET_CELLS):
        chunk = cells[first : first + config.SHEET_CELLS]
        cols = min(config.SHEET_COLUMNS, len(chunk))
        rows = math.ceil(len(chunk) / cols)
        sheet = Image.new("RGB", (cols * (size + gap) + gap, rows * (size + label_h + gap) + gap), "white")
        draw = ImageDraw.Draw(sheet)
        for i, (path, label) in enumerate(chunk):
            x, y = gap + (i % cols) * (size + gap), gap + (i // cols) * (size + label_h + gap)
            with Image.open(path) as image:
                thumb = image.convert("RGB")
                thumb.thumbnail((size, size))
            sheet.paste(thumb, (x + (size - thumb.width) // 2, y + label_h + (size - thumb.height) // 2))
            draw.rectangle((x, y + label_h, x + size - 1, y + label_h + size - 1), outline=(190, 190, 190))
            draw.text((x + 2, y + 2), label, fill="black", font=font)
        out = dest / f"{prefix}-sheet{first // config.SHEET_CELLS + 1:02d}.jpg"
        dest.mkdir(parents=True, exist_ok=True)
        sheet.save(out, "JPEG", quality=config.PAGE_IMAGE_JPEG_QUALITY, optimize=True)
        sheets.append((out, [label for _, label in chunk]))
    return sheets


def _render_pdf(source: Path, out_dir: Path, prefix: str, result: RenderedPages) -> None:
    # Haifa's permit plans are often DWFx (Autodesk), which is XPS underneath and renders like a PDF.
    with pymupdf.open(source, filetype="xps" if source.suffix.lower() in XPS_SUFFIXES else None) as doc:
        result.total = doc.page_count
        for index in range(min(doc.page_count, config.MAX_PAGES_PER_DOCUMENT)):
            page = doc.load_page(index)
            width, height = page.rect.width or 1, page.rect.height or 1
            zoom = min(config.PAGE_IMAGE_MAX_EDGE / max(width, height), 4.0)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            name = f"{prefix}-{index + 1:03d}"
            _save_jpeg(Image.open(io.BytesIO(pix.tobytes("png"))), out_dir / f"{name}.jpg")
            result.pages.append(out_dir / f"{name}.jpg")

            scale = tile_scale(width, height, native=4.0)
            if not scale:
                result.tiles.append([])
                continue
            # One render at tile scale, then crops: rendering a clip still walks the whole (often huge) page.
            big = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            whole = Image.frombytes("RGB", (big.width, big.height), big.samples)

            def region(box: tuple[float, float, float, float], scale: float, whole: Image.Image = whole) -> Image.Image:
                return whole.crop(tuple(round(v * scale) for v in box))

            result.tiles.append(_tiles(region, width, height, scale, out_dir, name))


def _render_raster(source: Path, out_dir: Path, prefix: str, result: RenderedPages) -> None:
    with Image.open(source) as image:
        result.total = getattr(image, "n_frames", 1)
        for index in range(min(result.total, config.MAX_PAGES_PER_DOCUMENT)):
            image.seek(index)
            frame = image.copy()
            name = f"{prefix}-{index + 1:03d}"
            _save_jpeg(frame, out_dir / f"{name}.jpg")
            result.pages.append(out_dir / f"{name}.jpg")

            def region(box: tuple[float, float, float, float], scale: float, frame: Image.Image = frame) -> Image.Image:
                crop = frame.crop(tuple(round(v) for v in box))
                return crop.resize((max(1, round(crop.width * scale)), max(1, round(crop.height * scale))))

            scale = tile_scale(frame.width, frame.height, native=1.0)
            result.tiles.append(_tiles(region, frame.width, frame.height, scale, out_dir, name) if scale else [])


def render_pages(source: Path, out_dir: Path, prefix: str) -> RenderedPages:
    """Page images (up to MAX_PAGES_PER_DOCUMENT), tiles of long sheets, and contact sheets."""
    result = RenderedPages(total=0)
    if source.suffix.lower() in (".pdf", *XPS_SUFFIXES):
        _render_pdf(source, out_dir, prefix, result)
    else:
        _render_raster(source, out_dir, prefix, result)
    cells: list[tuple[Path, str]] = []
    for number, (page, tiles) in enumerate(zip(result.pages, result.tiles, strict=True), 1):
        cells += [(tile, f"p{number}.t{n}") for n, tile in enumerate(tiles, 1)] or [(page, f"p{number}")]
    result.sheets = contact_sheets(cells, out_dir, prefix)
    return result
