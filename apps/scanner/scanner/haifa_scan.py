"""A Haifa scan in three steps (docs/HAIFA_PLAN.md §3):

1. first_delivery — GIS, maps, every building file and permit request, plans, and the most important documents.
   Every document is listed from here on; the rest are `pending`.
2. fetch_document — one pending document at a time, in priority order (the Haifa worker or a local run).
3. finish — rebuild index.csv, prompts, requests.md, decks and the ZIP once nothing is pending.

Complot calls go through one ComplotClient. A ComplotBlockedError propagates to the caller untouched: whatever was
fetched so far is in the cache, so running the same step again later continues where it stopped.
"""

import asyncio
import re
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path

from scanner import config
from scanner.http import Fetcher, FileTooLargeError
from scanner.models import AddressMatch, Building, Document, HistoricMap, PermitRequest, Plan, ScanManifest
from scanner.pipeline import Progress, ProgressFn, attach_pages, render_document_pages, render_maps, write_outputs
from scanner.render import pages
from scanner.sources import complot, haifa_gis, mapwarper, palopenmaps, taba
from scanner.sources.complot import ComplotBlockedError, ComplotClient, ComplotError, ComplotSuspectError, DocumentRef

# Download order for request documents: lower first (docs/HAIFA_PLAN.md §3.3).
PRIORITY_RULES: tuple[tuple[int, Callable[[str, str], bool]], ...] = (
    (0, lambda name, subject: "היתר" in name and "חתום" in name),
    (0, lambda name, subject: name.startswith("היתר בניה") or subject == "היתר בניה"),
    (1, lambda name, subject: "תכנית היתר" in name or ("חתומ" in name and "תכנית" in name)),
    (2, lambda name, subject: "החלט" in name or "החלטות" in subject or "פרוטוקול" in name),
    (3, lambda name, subject: "תכנית ראשית" in name),
    (4, lambda name, subject: "פיקוח" in name),
    (9, lambda name, subject: "גרמושקה" in name or "אגרות" in name),
)
DEFAULT_RANK = 5
FIRST_DELIVERY_MAX_RANK = 2  # signed permits, signed plans and decisions
# A document whose link page came back without a link: genuinely missing, unless the next answer shows a block.
SUSPECT_NOTE = "download failed: Complot gave no download link"


def rank(name: str, subject: str | None) -> int:
    for value, test in PRIORITY_RULES:
        if test(name, subject or ""):
            return value
    return DEFAULT_RANK


def _safe(name: str) -> str:
    return re.sub(r"[^\w\-.א-ת]+", "_", name).strip("_") or "x"


def _year(value: date | None) -> int | None:
    return value.year if value else None


# ---------- archive ----------


async def collect_requests(
    client: ComplotClient, address: AddressMatch, warnings: list[str], progress: Progress | None = None
) -> tuple[list[str], list[PermitRequest], list[tuple[PermitRequest, DocumentRef]]]:
    """Building files at the address, every permit request in them, and every document of those requests.

    A city-wide batch file (one applicant's works at dozens of addresses) is listed in the warnings but not opened:
    its requests are about other buildings, and opening them costs one Complot call each.
    """
    files = await client.building_files(address.street_code, address.house_number)
    requests: list[PermitRequest] = []
    documents: list[tuple[PermitRequest, DocumentRef]] = []
    seen: set[str] = set()
    kept: list[str] = []
    for ref in files:
        building_file = await client.building_file(ref.number)
        if building_file.is_batch():
            applicants = sorted({r.applicant for r in building_file.requests if r.applicant})
            warnings.append(
                f"Building file {ref.number} covers {len(building_file.addresses)} addresses across the city"
                + (f" ({', '.join(applicants[:3])})" if applicants else "")
                + f", so its {len(building_file.requests)} requests weren't opened: "
                + complot.page_url("GetTikFile", t=ref.number)
            )
            continue
        kept.append(ref.number)
        for request_ref in building_file.requests:
            if request_ref.request_no in seen:
                continue
            seen.add(request_ref.request_no)
            request, docs = await client.request(request_ref.request_no, ref.number)
            request.submitted = request.submitted or request_ref.submitted
            request.permit_no = request.permit_no or request_ref.permit_no
            request.permit_date = request.permit_date or request_ref.permit_date
            requests.append(request)
            documents += [(request, doc) for doc in docs]
            if progress:
                await progress("archive", "running", f"{len(requests)} requests read")
    return kept, requests, documents


def archive_documents(entries: list[tuple[PermitRequest, DocumentRef]]) -> list[Document]:
    """One pending Document per request document: ids in date order, priorities newest request first."""
    unique: dict[str, tuple[PermitRequest, DocumentRef]] = {}
    for request, ref in entries:
        unique.setdefault(ref.guid, (request, ref))
    by_date = sorted(unique.values(), key=lambda e: (e[1].date or date.max, e[0].request_no, e[1].name))
    newest_first = sorted(unique.values(), key=lambda e: e[0].submitted or date.min, reverse=True)
    position = {ref.guid: i for i, (_, ref) in enumerate(newest_first)}
    docs = []
    for i, (request, ref) in enumerate(by_date):
        docs.append(
            Document(
                doc_id=f"A-{i + 1:03d}",
                source="archive",
                title=ref.name,
                doc_type=ref.subject,
                date=ref.date,
                year=_year(ref.date),
                permit_no=request.permit_no,
                request_no=request.request_no,
                original_url=complot.page_url("GetDocumentIFN", guid="{" + ref.guid + "}"),
                status="pending",
                guid=ref.guid,
                priority=rank(ref.name, ref.subject) * 10_000 + position[ref.guid],
            )
        )
    return docs


async def fetch_document(client: ComplotClient, doc: Document, root: Path) -> Document:
    """Download one archive document and render its pages. Blocks propagate; other failures mark it failed."""
    try:
        path = await client.document(doc.guid or "", root / "archive" / f"{doc.doc_id}.pdf")
    except ComplotBlockedError:
        raise
    except ComplotSuspectError:
        doc.status, doc.note = "failed", SUSPECT_NOTE
        return doc
    except Exception as err:
        doc.status, doc.note = "failed", f"download failed: {err}"[:200]
        return doc
    doc.file = path.relative_to(root).as_posix()
    doc.size = path.stat().st_size
    doc.status, doc.note = "ready", None
    try:
        rendered = await asyncio.to_thread(pages.render_pages, path, root / "pages" / doc.doc_id, doc.doc_id)
    except Exception as err:  # e.g. DWF drawings: keep the file, no page images
        doc.note = f"no page images ({type(err).__name__})"
    else:
        attach_pages(doc, rendered, root)
    return doc


def first_batch(documents: list[Document]) -> list[Document]:
    pending = sorted((d for d in documents if d.status == "pending"), key=lambda d: d.priority)
    important = [d for d in pending if d.priority // 10_000 <= FIRST_DELIVERY_MAX_RANK]
    return important[: config.HAIFA_FIRST_DELIVERY_DOCS]


# ---------- plans ----------


async def collect_plans(
    fetcher: Fetcher, client: ComplotClient, building: Building, root: Path, warnings: list[str]
) -> tuple[list[Plan], list[Document]]:
    plans = await haifa_gis.plans_at(fetcher, building)
    docs: list[Document] = []
    for plan in plans:
        if plan.id_taba is None:
            continue
        plan.documents_url = complot.page_url("GetTabaFile", n=plan.id_taba)
        details = await client.plan(plan.id_taba)
        plan.status = details.status or plan.status
        if details.status_date and (plan.status or "").startswith("בתוקף"):
            plan.in_force_date = details.status_date
        plan.deposit_date = details.deposit_date
        for ref in complot.pick_plan_files(details.files):
            doc_id = f"P-{len(docs) + 1:03d}"
            try:
                url = await client.plan_file_url(ref)
            except ComplotBlockedError:
                raise
            except ComplotError as err:
                warnings.append(f"Plan {plan.plan_no}: no link for {ref.name} ({err})")
                continue
            rel = f"plans/{plan.id_taba}/{_safe(url.rsplit('/', 1)[-1])}"  # Complot's numeric plan id: ASCII paths
            note = None
            try:
                await taba.download(fetcher, url, root / rel)
            except FileTooLargeError as err:
                rel, note = None, f"large file ({err.size / 1_000_000:.0f} MB): open the original link"
            except Exception as err:
                warnings.append(f"Plan {plan.plan_no}: could not download {url} ({err})")
                rel, note = None, "download failed"
            when = plan.in_force_date or plan.deposit_date
            docs.append(
                Document(
                    doc_id=doc_id,
                    source="plan",
                    title=f"{plan.name or plan.plan_no} — {ref.name}",
                    doc_type='תב"ע',
                    date=when,
                    year=_year(when),
                    plan_no=plan.plan_no,
                    plan_status=plan.status,
                    original_url=url,
                    file=rel,
                    note=note,
                )
            )
    return plans, docs


def haifa_maps(lat: float, lon: float) -> list[HistoricMap]:
    """mapwarper's downtown maps and the Palestine Open Maps layers, oldest first (undated city maps as 1930s)."""
    found = mapwarper.maps_at(lat, lon) + palopenmaps.maps_at(lat, lon)
    return sorted(found, key=lambda m: (m.year or 1935, m.title))


def map_link(historic: HistoricMap) -> str:
    return palopenmaps.link(historic) if historic.folder == "palopenmaps" else mapwarper.link(historic)


# ---------- the three steps ----------


async def first_delivery(
    fetcher: Fetcher,
    client: ComplotClient,
    scan_id: str,
    address: AddressMatch,
    building: Building,
    root: Path,
    on_progress: ProgressFn | None = None,
) -> ScanManifest:
    progress = Progress(on_progress)
    root.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []

    await progress("maps", "running")
    maps_task = asyncio.create_task(
        render_maps(fetcher, haifa_maps(building.lat, building.lon), building, root, warnings, map_link)
    )

    try:
        await progress("archive", "running", "building files and permit requests")
        building_files, requests, entries = await collect_requests(client, address, warnings, progress)
        documents = archive_documents(entries)
        await progress("archive", "running", f"{len(requests)} requests, {len(documents)} documents")

        batch = first_batch(documents)
        for i, doc in enumerate(batch):
            await fetch_document(client, doc, root)
            await progress("archive", "running", f"{i + 1}/{len(batch)} key documents")
        for doc in batch:
            if doc.note == SUSPECT_NOTE:  # one more try in the background, where a block can still put it back
                doc.status, doc.note = "pending", None
        ready = sum(1 for d in documents if d.status == "ready")
        await progress("archive", "done", f"{ready} of {len(documents)} documents")

        await progress("plans", "running")
        try:
            plans, plan_docs = await collect_plans(fetcher, client, building, root, warnings)
        except ComplotBlockedError:
            raise
        except Exception as err:
            warnings.append(f"plans: {err}")
            plans, plan_docs = [], []
        await progress("plans", "done", f"{len(plans)} plans, {len(plan_docs)} files")
    except BaseException:
        maps_task.cancel()
        raise

    try:
        maps = await maps_task
    except Exception as err:
        warnings.append(f"maps: {err}")
        maps = []
    await progress("maps", "done", f"{len(maps)} maps")

    await progress("pages", "running")
    await render_document_pages(plan_docs, root, warnings)
    await progress("pages", "done", "page images ready")

    if not documents and not building_files:
        warnings.append("Complot has no building file for this address.")
    manifest = ScanManifest(
        scan_id=scan_id,
        city="haifa",
        created_at=datetime.now(UTC),
        address=address,
        building=building,
        archive_folder_no=", ".join(building_files) or None,
        building_files=building_files,
        requests=requests,
        plans=plans,
        documents=[*documents, *plan_docs, *maps],
        warnings=warnings,
    )
    await progress("outputs", "running")
    await asyncio.to_thread(write_outputs, manifest, root)
    await progress("outputs", "done", "first delivery ready")
    return manifest


def pending(manifest: ScanManifest) -> list[Document]:
    return sorted((d for d in manifest.documents if d.status == "pending"), key=lambda d: d.priority)


async def finish(manifest: ScanManifest, root: Path) -> ScanManifest:
    """Rebuild every output from the documents that are in `root` now."""
    await asyncio.to_thread(write_outputs, manifest, root)
    return manifest


def summary(manifest: ScanManifest) -> dict:
    counts = {s: sum(1 for d in manifest.documents if d.source == s) for s in ("archive", "plan", "map")}
    archive = [d for d in manifest.documents if d.source == "archive"]
    return {
        "counts": counts,
        "documents_total": len(archive),
        "documents_ready": sum(1 for d in archive if d.status == "ready"),
        "requests": len(manifest.requests),
        "warnings": manifest.warnings[:50],
        "outputs": manifest.outputs,
    }
