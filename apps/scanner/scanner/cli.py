"""Command line: look up an address, or run a full scan into a folder."""

import argparse
import asyncio
import sys
import time
import uuid
from pathlib import Path

from scanner.http import Fetcher
from scanner.models import AddressMatch, Building
from scanner.pipeline import run_scan
from scanner.sources import gis


async def _resolve(fetcher: Fetcher, text: str, pick: int) -> tuple[AddressMatch, Building]:
    streets = await gis.load_streets(fetcher)
    matches = await gis.search_addresses(fetcher, text, streets)
    if not matches:
        raise SystemExit(f"No address found for: {text}")
    address = matches[min(pick, len(matches) - 1)]
    building = await gis.building_at(fetcher, address.x, address.y)
    if building is None:
        raise SystemExit(f"No building footprint found at {address.label_he}")
    return address, building


async def _lookup(args: argparse.Namespace) -> None:
    async with Fetcher() as fetcher:
        streets = await gis.load_streets(fetcher)
        matches = await gis.search_addresses(fetcher, args.address, streets)
        if not matches:
            print("No matches")
            return
        for i, m in enumerate(matches):
            print(f"[{i}] {m.label_he} / {m.label_en}  street={m.street_code} gush/helka={m.gush}/{m.helka}")
        building = await gis.building_at(fetcher, matches[0].x, matches[0].y)
        if building:
            print(
                f"Building {building.building_id}: built {building.year_built}, {building.floors} floors, "
                f"{building.building_type or ''}"
            )


async def _scan(args: argparse.Namespace) -> None:
    started = time.monotonic()

    def report(stage: str, status: str, detail: str | None) -> None:
        elapsed = time.monotonic() - started
        print(f"[{elapsed:6.1f}s] {stage:8} {status:8} {detail or ''}", flush=True)

    async with Fetcher() as fetcher:
        address, building = await _resolve(fetcher, args.address, args.pick)
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
    scan = sub.add_parser("scan", help="Scan a building into a folder")
    scan.add_argument("address")
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
