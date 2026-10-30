"""Page images for AI agents and slide previews."""

import io
from pathlib import Path

import pymupdf
from PIL import Image

from scanner import config

Image.MAX_IMAGE_PIXELS = 400_000_000  # municipal plan scans are huge but trusted


def _save_jpeg(image: Image.Image, dest: Path) -> None:
    image = image.convert("RGB")
    image.thumbnail((config.PAGE_IMAGE_MAX_EDGE, config.PAGE_IMAGE_MAX_EDGE))
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest, "JPEG", quality=config.PAGE_IMAGE_JPEG_QUALITY, optimize=True)


def render_pages(source: Path, out_dir: Path, prefix: str) -> tuple[int, list[Path]]:
    """Render up to MAX_PAGES_PER_DOCUMENT pages as JPEGs. Returns (total page count, written files)."""
    suffix = source.suffix.lower()
    written: list[Path] = []
    if suffix == ".pdf":
        with pymupdf.open(source) as doc:
            total = doc.page_count
            for index in range(min(total, config.MAX_PAGES_PER_DOCUMENT)):
                page = doc.load_page(index)
                longest = max(page.rect.width, page.rect.height) or 1
                zoom = min(config.PAGE_IMAGE_MAX_EDGE / longest, 4.0)
                pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
                image = Image.open(io.BytesIO(pix.tobytes("png")))
                dest = out_dir / f"{prefix}-{index + 1:03d}.jpg"
                _save_jpeg(image, dest)
                written.append(dest)
        return total, written

    with Image.open(source) as image:
        total = getattr(image, "n_frames", 1)
        for index in range(min(total, config.MAX_PAGES_PER_DOCUMENT)):
            image.seek(index)
            dest = out_dir / f"{prefix}-{index + 1:03d}.jpg"
            _save_jpeg(image.copy(), dest)
            written.append(dest)
    return total, written
