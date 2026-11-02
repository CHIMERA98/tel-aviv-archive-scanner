"""One scan: collect maps, plans and archive documents for a building, then render the outputs."""

import asyncio
import inspect
import re
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

from scanner import config
from scanner.http import Fetcher, FileTooLargeError, SourceBlockedError
from scanner.models import AddressMatch, Building, Document, HistoricMap, Plan, ScanManifest
from scanner.render import bundle, decks, index_csv, pages, prompts, requests_md
from scanner.sources import gis, handasa, mapjunction, taba

STAGES = ("maps", "plans", "archive", "pages", "outputs")

ProgressFn = Callable[[str, str, str | None], Awaitable[None] | None]


class Progress:
    def __init__(self, callback: ProgressFn | None) -> None:
        self._callback = callback

    async def __call__(self, stage: str, status: str, detail: str | None = None) -> None:
        if self._callback is None:
            return
        result = self._callback(stage, status, detail)
        if inspect.isawaitable(result):
            await result


def _safe(name: str) -> str:
    return re.sub(r"[^\w\-.א-ת]+", "_", name).strip("_") or "x"


async def _collect_maps(fetcher: Fetcher, building: Building, root: Path, warnings: list[str]) -> list[Document]:
    found = await mapjunction.find_maps(fetcher, building.lat, building.lon)
    return await render_maps(
        fetcher, found, building, root, warnings, lambda m: f"https://www.mapjunction.com/?map={m.map_id}"
    )


async def render_maps(
    fetcher: Fetcher,
    found: list[HistoricMap],
    building: Building,
    root: Path,
    warnings: list[str],
    link: Callable[[HistoricMap], str],
) -> list[Document]:
    """Crop each map around the building (any XYZ tile source) into maps/M-xxx.jpg."""
    rendered = await asyncio.gather(
        *(mapjunction.render(fetcher, m, building.lat, building.lon) for m in found), return_exceptions=True
    )
    docs: list[Document] = []
    for historic, image in zip(found, rendered, strict=True):
        if isinstance(image, BaseException):
            warnings.append(f"Map '{historic.title}' could not be rendered: {image}")
            continue
        if image is None:
            continue
        doc_id = f"M-{len(docs) + 1:03d}"
        rel = f"maps/{doc_id}.jpg"
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(image)
        docs.append(
            Document(
                doc_id=doc_id,
                source="map",
                title=historic.title,
                doc_type=historic.folder,
                year=historic.year,
                original_url=link(historic),
                file=rel,
                pages=1,
                page_images=[rel],
                note=historic.credit,
            )
        )
    return docs


async def _collect_plans(
    fetcher: Fetcher, building: Building, root: Path, warnings: list[str]
) -> tuple[list[Plan], list[Document]]:
    plans = await gis.plans_at(fetcher, building)
    file_lists = await asyncio.gather(*(taba.plan_files(fetcher, p) for p in plans), return_exceptions=True)
    jobs: list[tuple[Plan, str, str]] = []
    for plan, files in zip(plans, file_lists, strict=True):
        if isinstance(files, BaseException):
            warnings.append(f"Plan {plan.plan_no}: document list unavailable ({files})")
            continue
        for url in files:
            jobs.append((plan, url, f"plans/{_safe(plan.plan_no)}/{_safe(url.rsplit('/', 1)[-1])}"))

    results = await asyncio.gather(
        *(taba.download(fetcher, url, root / rel) for _, url, rel in jobs), return_exceptions=True
    )
    docs: list[Document] = []
    too_large: list[int] = []
    for (plan, url, rel), result in zip(jobs, results, strict=True):
        doc_id = f"P-{len(docs) + 1:03d}"
        failed = isinstance(result, BaseException)
        if isinstance(result, FileTooLargeError):
            too_large.append(result.size)
            note = f"large file ({result.size / 1_000_000:.0f} MB): open the original link"
        elif failed:
            warnings.append(f"Plan {plan.plan_no}: could not download {url} ({result})")
            note = "download failed"
        else:
            note = None
        docs.append(
            Document(
                doc_id=doc_id,
                source="plan",
                title=plan.name or f'תב"ע {plan.plan_no}',
                doc_type='תב"ע',
                date=plan.in_force_date,
                year=plan.in_force_date.year if plan.in_force_date else None,
                plan_no=plan.plan_no,
                plan_status=plan.status,
                original_url=url,
                file=None if failed else rel,
                note=note,
            )
        )
    if too_large:
        warnings.append(
            f"{len(too_large)} large plan files ({sum(too_large) / 1_000_000:.0f} MB) were linked, not copied; "
            "see original_url in index.csv"
        )
    return plans, docs


async def _collect_archive(
    fetcher: Fetcher, address: AddressMatch, building: Building, root: Path, warnings: list[str]
) -> tuple[str | None, list[Document]]:
    if building.building_id is None:
        raise ValueError("Tel Aviv archive search needs the building's id_binyan")
    records = await handasa.list_documents(fetcher, building.building_id)
    records.sort(key=lambda r: (r.date is None, r.date.isoformat() if r.date else "", r.doc_type))

    folder = Counter(r.folder_no for r in records if r.folder_no).most_common(1)
    folder_no = folder[0][0] if folder else None
    expected = f"{address.street_code}_{address.house_number}"
    if records and not any((r.street_house or "").startswith(expected) for r in records):
        houses = sorted({r.street_house for r in records if r.street_house})
        warnings.append(
            f"Archive file is registered under {', '.join(houses) or 'another address'}, not {expected}; "
            "check that the right building was selected."
        )

    ids = [f"A-{i + 1:03d}" for i in range(len(records))]
    results = await asyncio.gather(
        *(
            handasa.download(fetcher, r, root / f"archive/{doc_id}.{r.extension}")
            for r, doc_id in zip(records, ids, strict=True)
        ),
        return_exceptions=True,
    )
    docs: list[Document] = []
    for record, doc_id, result in zip(records, ids, results, strict=True):
        failed = isinstance(result, BaseException)
        if failed:
            warnings.append(f"{doc_id} ({record.guid}) could not be downloaded: {result}")
        docs.append(
            Document(
                doc_id=doc_id,
                source="archive",
                title=record.doc_type,
                doc_type=record.doc_type,
                date=record.date,
                year=record.date.year if record.date else None,
                permit_no=record.permit_no,
                request_no=record.request_no or record.online_request_no,
                original_url=record.download_url,
                file=None if failed else f"archive/{doc_id}.{record.extension}",
                note="download failed" if failed else None,
            )
        )
    return folder_no, docs


async def render_document_pages(documents: list[Document], root: Path, warnings: list[str]) -> None:
    slots = asyncio.Semaphore(2)

    async def one(doc: Document) -> None:
        if doc.file is None or doc.source == "map" or doc.page_images:
            return
        async with slots:
            try:
                total, written = await asyncio.to_thread(
                    pages.render_pages, root / doc.file, root / "pages" / doc.doc_id, doc.doc_id
                )
            except Exception as err:
                warnings.append(f"{doc.doc_id}: pages could not be rendered ({err})")
                return
        doc.pages = total
        doc.page_images = [p.relative_to(root).as_posix() for p in written]

    await asyncio.gather(*(one(d) for d in documents))


BUNDLE_NOTE = "large file: download it on its own (not in the ZIP)"


def bundle_skips(manifest: ScanManifest, root: Path) -> frozenset[str]:
    """Haifa files too large for bundle.zip; they stay downloadable one by one."""
    if manifest.city != "haifa":
        return frozenset()
    skip = set()
    for doc in manifest.documents:
        if not doc.file:
            continue
        size = (
            doc.size
            if doc.size is not None
            else ((root / doc.file).stat().st_size if (root / doc.file).exists() else 0)
        )
        if size > config.HAIFA_BUNDLE_MAX_FILE_BYTES:
            skip.add(doc.file)
            doc.note = doc.note or BUNDLE_NOTE
    return frozenset(skip)


def write_outputs(manifest: ScanManifest, root: Path) -> None:
    skip = bundle_skips(manifest, root)
    index_csv.write_index(manifest.documents, root / "index.csv")
    (root / "PROMPT.he.md").write_text(prompts.prompt_he(manifest), encoding="utf-8")
    (root / "PROMPT.en.md").write_text(prompts.prompt_en(manifest), encoding="utf-8")
    decks.build_archive_deck(manifest, root, root / "archive.pptx")
    decks.build_maps_plans_deck(manifest, root, root / "maps_plans.pptx")
    manifest.outputs = {
        "index": "index.csv",
        "prompt_he": "PROMPT.he.md",
        "prompt_en": "PROMPT.en.md",
        "archive_deck": "archive.pptx",
        "maps_plans_deck": "maps_plans.pptx",
    }
    if manifest.requests:
        (root / "requests.md").write_text(requests_md.render(manifest), encoding="utf-8")
        manifest.outputs["requests"] = "requests.md"
    bundle.build_bundle(root, root / "bundle.zip", skip)
    manifest.outputs["bundle"] = "bundle.zip"
    (root / "manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")


async def run_scan(
    fetcher: Fetcher,
    scan_id: str,
    address: AddressMatch,
    building: Building,
    root: Path,
    on_progress: ProgressFn | None = None,
) -> ScanManifest:
    progress = Progress(on_progress)
    root.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []

    async def stage[T](name: str, work: Callable[[], Awaitable[T]], describe: Callable[[T], str]) -> T | None:
        await progress(name, "running")
        try:
            result = await work()
        except SourceBlockedError:
            await progress(name, "error", "blocked by the municipal firewall")
            raise
        except Exception as err:
            warnings.append(f"{name}: {err}")
            await progress(name, "error", str(err))
            return None
        await progress(name, "done", describe(result))
        return result

    maps = await stage("maps", lambda: _collect_maps(fetcher, building, root, warnings), lambda r: f"{len(r)} maps")
    plan_result = await stage(
        "plans",
        lambda: _collect_plans(fetcher, building, root, warnings),
        lambda r: f"{len(r[0])} plans, {len(r[1])} files",
    )
    archive = await stage(
        "archive",
        lambda: _collect_archive(fetcher, address, building, root, warnings),
        lambda r: f"{len(r[1])} documents",
    )

    plans, plan_docs = plan_result or ([], [])
    folder_no, archive_docs = archive or (None, [])
    documents = [*archive_docs, *plan_docs, *(maps or [])]

    await stage("pages", lambda: render_document_pages(documents, root, warnings), lambda _: "page images ready")

    manifest = ScanManifest(
        scan_id=scan_id,
        created_at=datetime.now(UTC),
        address=address,
        building=building,
        archive_folder_no=folder_no,
        plans=plans,
        documents=documents,
        warnings=warnings,
    )
    await progress("outputs", "running")
    await asyncio.to_thread(write_outputs, manifest, root)
    await progress("outputs", "done", "bundle ready")
    return manifest
