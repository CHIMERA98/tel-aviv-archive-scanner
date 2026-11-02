"""bundle.zip: everything a person or AI needs, without the per-page images."""

import zipfile
from pathlib import Path

INCLUDED_DIRS = ("archive", "plans", "maps")
INCLUDED_FILES = ("index.csv", "PROMPT.he.md", "PROMPT.en.md", "requests.md", "archive.pptx", "maps_plans.pptx")


def build_bundle(root: Path, dest: Path, skip: frozenset[str] = frozenset()) -> None:
    """`skip`: paths (relative to root) left out, e.g. very large plans that are linked from index.csv instead."""
    # Scans are already compressed; storing them avoids burning CPU for ~0% gain.
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_STORED) as archive:
        for name in INCLUDED_FILES:
            path = root / name
            if path.exists():
                method = zipfile.ZIP_DEFLATED if path.suffix in {".csv", ".md"} else zipfile.ZIP_STORED
                archive.write(path, name, compress_type=method)
        for folder in INCLUDED_DIRS:
            for path in sorted((root / folder).rglob("*")):
                rel = path.relative_to(root).as_posix()
                if path.is_file() and rel not in skip:
                    archive.write(path, rel)
