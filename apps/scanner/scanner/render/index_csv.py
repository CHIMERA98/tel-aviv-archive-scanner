"""index.csv: one row per document, the entry point for people and AI agents alike."""

import csv
from pathlib import Path

from scanner.models import Document

COLUMNS = (
    "doc_id",
    "source",
    "title",
    "doc_type",
    "date",
    "year",
    "permit_no",
    "request_no",
    "plan_no",
    "plan_status",
    "pages",
    "file",
    "original_url",
    "note",
)


def write_index(documents: list[Document], dest: Path) -> None:
    # utf-8-sig so Excel opens Hebrew correctly.
    with dest.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for doc in documents:
            row = doc.model_dump(include=set(COLUMNS))
            row["date"] = doc.date.isoformat() if doc.date else ""
            writer.writerow(["" if row.get(col) is None else row[col] for col in COLUMNS])
