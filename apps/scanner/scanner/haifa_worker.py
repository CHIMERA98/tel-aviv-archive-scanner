"""Cloud Run job `haifa-worker`: the one place Haifa scans talk to Complot (docs/HAIFA_PLAN.md §3.2).

The edge runs at most one execution. It loops on POST /internal/haifa/next, which hands out, in priority order,
documents someone opened, first deliveries of new scans, background documents, and finishing work. It exits when
there is nothing to do, or at the first HTTP 429 from Complot (after telling the edge, which pauses Complot for
45 minutes and restarts the worker afterwards). Every finished unit is stored, so an exit loses nothing.

Environment: EDGE_URL, SCANNER_HMAC_KEY (as the Tel Aviv job), WORKER_MAX_S (default 10200, under the 3 h timeout).
"""

import asyncio
import contextlib
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

from scanner import config, haifa_scan
from scanner.edge_client import EdgeCache, EdgeClient
from scanner.http import Fetcher, SourceBlockedError
from scanner.models import AddressMatch, Building, ScanManifest
from scanner.sources.complot import ComplotBlockedError, ComplotClient

log = logging.getLogger("haifa-worker")
HEARTBEAT_S = 60


async def first_delivery(edge: EdgeClient, fetcher: Fetcher, client: ComplotClient, work: dict) -> None:
    scan = edge.for_scan(work["scan_id"])
    address = AddressMatch.model_validate(work["address"])
    building = Building.model_validate(work["building"])
    with tempfile.TemporaryDirectory(prefix="haifa-") as tmp:
        root = Path(tmp) / work["scan_id"]
        manifest = await haifa_scan.first_delivery(
            fetcher, client, work["scan_id"], address, building, root, scan.progress
        )
        await scan.progress("upload", "running", None)
        count = await scan.upload_tree(root)
        await scan.progress("upload", "done", f"{count} files")
    remaining = haifa_scan.pending(manifest)
    if remaining:
        await scan.haifa_delivered(
            haifa_scan.summary(manifest),
            [{"doc_id": d.doc_id, "guid": d.guid, "priority": d.priority} for d in remaining],
        )
    else:
        await scan.done(haifa_scan.summary(manifest))


async def _manifest(scan: EdgeClient, root: Path) -> ScanManifest:
    if not await scan.download_file("manifest.json", root / "manifest.json"):
        raise RuntimeError("manifest.json is missing")
    return ScanManifest.model_validate_json((root / "manifest.json").read_text(encoding="utf-8"))


async def document(edge: EdgeClient, client: ComplotClient, work: dict) -> None:
    scan = edge.for_scan(work["scan_id"])
    with tempfile.TemporaryDirectory(prefix="haifa-") as tmp:
        root = Path(tmp)
        manifest = await _manifest(scan, root)
        doc = next((d for d in manifest.documents if d.doc_id == work["doc_id"]), None)
        if doc is None or doc.status != "pending":
            await scan.haifa_document(work["doc_id"], doc.status if doc else "failed", doc.pages if doc else 0)
            return
        await haifa_scan.fetch_document(client, doc, root)
        for rel in [doc.file, *doc.image_files()]:
            if rel:
                await scan.upload_file(root / rel, rel)
        (root / "manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        await scan.upload_file(root / "manifest.json", "manifest.json")
        await scan.haifa_document(doc.doc_id, doc.status, doc.pages)


async def finish(edge: EdgeClient, work: dict) -> None:
    """Rebuild index, prompts, requests.md, decks and the ZIP from everything the scan has in R2."""
    scan = edge.for_scan(work["scan_id"])
    with tempfile.TemporaryDirectory(prefix="haifa-") as tmp:
        root = Path(tmp)
        manifest = await _manifest(scan, root)
        await scan.progress("outputs", "running", "final files")
        needed = set()
        for doc in manifest.documents:
            if doc.file and (doc.size or 0) <= config.HAIFA_BUNDLE_MAX_FILE_BYTES:  # bigger ones stay out of the ZIP
                needed.add(doc.file)
            if doc.page_images and doc.source != "map":
                needed.add(doc.page_images[0])  # slide previews
        for rel in sorted(needed):
            if not await scan.download_file(rel, root / rel):
                log.warning("scan %s: %s is missing in R2", work["scan_id"], rel)
        await haifa_scan.finish(manifest, root)
        for rel in [*manifest.outputs.values(), "manifest.json"]:
            await scan.upload_file(root / rel, rel)
        await scan.progress("outputs", "done", "all documents in")
    await scan.done(haifa_scan.summary(manifest))


async def run() -> int:
    deadline = time.monotonic() + float(os.environ.get("WORKER_MAX_S", "10200"))
    async with (
        EdgeClient(os.environ["EDGE_URL"], os.environ["SCANNER_HMAC_KEY"].encode()) as edge,
        Fetcher() as fetcher,
        ComplotClient(EdgeCache(edge)) as client,
    ):

        async def heartbeat() -> None:
            while True:
                with contextlib.suppress(Exception):
                    await edge.haifa_heartbeat()
                await asyncio.sleep(HEARTBEAT_S)

        beat = asyncio.create_task(heartbeat())
        try:
            while time.monotonic() < deadline:
                work = await edge.haifa_next()
                kind = work.get("kind")
                client.interval = float(work.get("interval_s") or client.interval)
                if kind not in ("scan", "document", "finish"):
                    log.info("nothing to do (%s); %d Complot calls this run", kind, client.calls)
                    return 0
                log.info("work: %s", json.dumps({k: work.get(k) for k in ("kind", "scan_id", "doc_id")}))
                try:
                    if kind == "scan":
                        await first_delivery(edge, fetcher, client, work)
                    elif kind == "document":
                        await document(edge, client, work)
                    else:
                        await finish(edge, work)
                except ComplotBlockedError as err:
                    await edge.for_scan(work["scan_id"]).haifa_blocked(str(err))
                    log.warning("Complot blocked us after %d calls; stopping", client.calls)
                    return 2
                except SourceBlockedError as err:
                    await edge.for_scan(work["scan_id"]).failed(str(err), blocked=True)
                except Exception as err:
                    log.exception("unit failed")
                    scan = edge.for_scan(work["scan_id"])
                    if kind == "document":
                        await scan.haifa_document(work["doc_id"], "failed", 0)
                    else:
                        await scan.failed(f"{type(err).__name__}: {err}"[:500])
            return 0
        finally:
            beat.cancel()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()
