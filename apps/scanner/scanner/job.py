"""Cloud Run job entrypoint: one execution scans one building.

Environment:
  SCAN_ID          set per execution by the edge (container override)
  EDGE_URL         e.g. https://tel-aviv-archive-scanner.work
  SCANNER_HMAC_KEY shared secret (from Secret Manager)
  SCAN_TIMEOUT_S   hard limit for the whole scan (default 2400)
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

from scanner.edge_client import EdgeClient
from scanner.http import Fetcher, SourceBlockedError
from scanner.models import AddressMatch, Building
from scanner.pipeline import run_scan


async def run() -> int:
    scan_id = os.environ["SCAN_ID"]
    timeout = float(os.environ.get("SCAN_TIMEOUT_S", "2400"))
    async with EdgeClient(os.environ["EDGE_URL"], os.environ["SCANNER_HMAC_KEY"].encode(), scan_id) as edge:
        job = await edge.job()
        address = AddressMatch.model_validate(job["address"])
        building = Building.model_validate(job["building"])
        with tempfile.TemporaryDirectory(prefix="scan-") as tmp:
            root = Path(tmp) / scan_id
            try:
                async with Fetcher() as fetcher:
                    manifest = await asyncio.wait_for(
                        run_scan(fetcher, scan_id, address, building, root, edge.progress), timeout
                    )
                await edge.progress("upload", "running", None)
                count = await edge.upload_tree(root)
                await edge.progress("upload", "done", f"{count} files")
            except SourceBlockedError as err:
                await edge.failed(str(err), blocked=True)
                return 2
            except TimeoutError:
                await edge.failed(f"Scan exceeded {timeout:.0f}s")
                return 3
            except Exception as err:
                await edge.failed(f"{type(err).__name__}: {err}")
                raise
        counts = {s: sum(1 for d in manifest.documents if d.source == s) for s in ("archive", "plan", "map")}
        await edge.done({"counts": counts, "warnings": manifest.warnings[:50], "outputs": manifest.outputs})
    return 0


def main() -> None:
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()
