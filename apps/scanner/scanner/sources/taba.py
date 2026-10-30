"""Zoning plan documents: the tabaot docs page lists scanned files under /taba_raster/."""

import re
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qsl, urljoin, urlsplit

from scanner import config
from scanner.http import Fetcher
from scanner.models import Plan

RASTER_PATH = re.compile(r"/taba_raster/[^\"'\s<>]+?\.(?:pdf|jpe?g|tiff?)", re.IGNORECASE)
PREFERRED_EXTENSIONS = (".pdf", ".tif", ".tiff", ".jpg", ".jpeg")


def extract_files(html: str) -> list[str]:
    """Absolute file URLs from a docs page; when a scan exists as both PDF and JPG, keep the PDF."""
    by_stem: dict[str, str] = {}
    for path in dict.fromkeys(RASTER_PATH.findall(html)):
        pure = PurePosixPath(path)
        stem, ext = pure.stem.lower(), pure.suffix.lower()
        current = by_stem.get(stem)
        if current is None or PREFERRED_EXTENSIONS.index(ext) < PREFERRED_EXTENSIONS.index(
            PurePosixPath(current).suffix.lower()
        ):
            by_stem[stem] = path
    return [urljoin(config.TABA_BASE, path) for path in sorted(by_stem.values())]


async def plan_files(fetcher: Fetcher, plan: Plan) -> list[str]:
    if not plan.documents_url:
        return []
    # Pass the query separately so httpx percent-encodes Hebrew plan numbers (e.g. st_taba=ג).
    parts = urlsplit(plan.documents_url)
    base = f"{parts.scheme}://{parts.netloc}{parts.path}"
    response = await fetcher.get(base, params=parse_qsl(parts.query, keep_blank_values=True))
    response.raise_for_status()
    return extract_files(response.text)


async def download(fetcher: Fetcher, url: str, dest: Path) -> int:
    size = await fetcher.download(url, dest)
    with dest.open("rb") as handle:
        if handle.read(64).lstrip()[:1] == b"<":
            dest.unlink()
            raise RuntimeError(f"Plan file request returned an HTML page: {url}")
    return size
