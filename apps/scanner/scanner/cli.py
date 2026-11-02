"""Command line: look up an address, or run a full scan into a folder."""

import argparse
import asyncio
import sys
import time
import uuid
from pathlib import Path

from scanner import cities, haifa_scan
from scanner.http import Fetcher
from scanner.models import AddressMatch, Building, City
from scanner.pipeline import run_scan
from scanner.sources.complot import ComplotClient, LocalCache


def _city(args: argparse.Namespace) -> City:
    return cities.parse_city(args.city) or cities.detect_city(args.address)


async def _resolve(fetcher: Fetcher, city: City, text: str, pick: int) -> tuple[AddressMatch, Building]:
    found = await cities.lookup(fetcher, city, text, limit=8)
    if not found:
        raise SystemExit(f"No address found for: {text}")
    address, building = found[min(pick, len(found) - 1)]
    if building is None:
        raise SystemExit(f"No building footprint found at {address.label_he}")
    return address, building


async def _lookup(args: argparse.Namespace) -> None:
    async with Fetcher() as fetcher:
        found = await cities.lookup(fetcher, _city(args), args.address, limit=8)
        if not found:
            print("No matches")
            return
        for i, (m, b) in enumerate(found):
            print(f"[{i}] {m.label_he} / {m.label_en}  street={m.street_code} gush/helka={m.gush}/{m.helka}")
            if b:
                extra = f"height {b.height_m} m" if b.city == "haifa" else f"built {b.year_built}, {b.floors} floors"
                kept = f", conservation {b.conservation.status} {b.conservation.grade or ''}" if b.conservation else ""
                print(f"     building {b.key}: {extra}{kept}")


async def _scan_haifa(args: argparse.Namespace, fetcher: Fetcher, report) -> None:
    """Local Haifa scan: first delivery, then every pending document, then the final files.

    Complot pages and documents are cached in <out>/.complot-cache, so running it again sends no repeat calls.
    """
    address, building = await _resolve(fetcher, "haifa", args.address, args.pick)
    scan_id = args.scan_id or uuid.uuid4().hex[:12]
    root = Path(args.out) / scan_id
    print(f"{address.label_he} -> {building.key}")
    async with ComplotClient(LocalCache(Path(args.out) / ".complot-cache"), interval=args.interval) as client:
        manifest = await haifa_scan.first_delivery(fetcher, client, scan_id, address, building, root, report)
        remaining = haifa_scan.pending(manifest)
        print(f"First delivery: {client.calls} Complot calls; {len(remaining)} documents pending")
        if not args.first_only:
            for i, doc in enumerate(remaining):
                await haifa_scan.fetch_document(client, doc, root)
                report("archive", "running", f"{i + 1}/{len(remaining)} {doc.doc_id} {doc.status}")
            await haifa_scan.finish(manifest, root)
        print(f"Complot calls: {client.calls}")
    counts = haifa_scan.summary(manifest)
    print(
        f"-> {root}\n  {counts['documents_ready']}/{counts['documents_total']} archive docs, "
        f"{counts['counts']['plan']} plan files, {counts['counts']['map']} maps, {counts['requests']} requests"
    )
    for warning in manifest.warnings:
        print(f"  warning: {warning}")


async def _scan(args: argparse.Namespace) -> None:
    started = time.monotonic()

    def report(stage: str, status: str, detail: str | None) -> None:
        elapsed = time.monotonic() - started
        print(f"[{elapsed:6.1f}s] {stage:8} {status:8} {detail or ''}", flush=True)

    async with Fetcher() as fetcher:
        if _city(args) == "haifa":
            await _scan_haifa(args, fetcher, report)
            print(f"Done in {time.monotonic() - started:.0f}s")
            return
        address, building = await _resolve(fetcher, "tel-aviv", args.address, args.pick)
        print(f"{address.label_he} -> building {building.building_id}")
        scan_id = args.scan_id or uuid.uuid4().hex[:12]
        root = Path(args.out) / scan_id
        manifest = await run_scan(fetcher, scan_id, address, building, root, report)
    counts = {s: sum(1 for d in manifest.documents if d.source == s) for s in ("archive", "plan", "map")}
    print(f"Done in {time.monotonic() - started:.0f}s -> {root}")
    print(f"  {counts['archive']} archive docs, {counts['plan']} plan files, {counts['map']} maps")
    for warning in manifest.warnings:
        print(f"  warning: {warning}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="tlv-scanner")
    sub = parser.add_subparsers(dest="command", required=True)
    lookup = sub.add_parser("lookup", help="Find an address and its building")
    lookup.add_argument("address")
    lookup.add_argument("--city", choices=cities.CITIES, help="Default: Haifa if the text says so, else Tel Aviv")
    scan = sub.add_parser("scan", help="Scan a building into a folder")
    scan.add_argument("address")
    scan.add_argument("--city", choices=cities.CITIES, help="Default: Haifa if the text says so, else Tel Aviv")
    scan.add_argument("--interval", type=float, default=18.0, help="Haifa: seconds between Complot page calls")
    scan.add_argument("--first-only", action="store_true", help="Haifa: stop after the first delivery")
    scan.add_argument("--out", default="out")
    scan.add_argument("--pick", type=int, default=0, help="Which address match to use (see lookup)")
    scan.add_argument("--scan-id")
    args = parser.parse_args(argv)
    try:
        asyncio.run(_lookup(args) if args.command == "lookup" else _scan(args))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
