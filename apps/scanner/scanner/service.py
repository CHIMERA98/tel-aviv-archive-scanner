"""Lookup service (Cloud Run service in Tel Aviv): address search and building resolution.

The edge can't call the city's GIS reliably itself (geo-block), so it asks this service.
Requests are signed exactly like the scanner's calls to the edge (see edge_client.sign).

  GET /health
  GET /lookup?q=<address text>  -> {"matches": [{"address": ..., "building": ... | null}, ...]}
"""

import asyncio
import hashlib
import hmac
import os
import time
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from scanner.http import Fetcher, SourceBlockedError
from scanner.sources import gis

MAX_SKEW_S = 300
STREETS_TTL_S = 24 * 3600


class State:
    def __init__(self) -> None:
        self.fetcher: Fetcher | None = None
        self.streets: list[gis.Street] = []
        self.streets_loaded_at = 0.0
        self.lock = asyncio.Lock()


state = State()


def _fetcher() -> Fetcher:
    assert state.fetcher is not None, "lifespan not started"
    return state.fetcher


def verify(request: Request, body: bytes) -> bool:
    key = os.environ.get("SCANNER_HMAC_KEY", "").encode()
    header = request.headers.get("x-scanner-signature", "")
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    if not key or "t" not in parts or "v1" not in parts or not parts["t"].isdigit():
        return False
    if abs(time.time() - int(parts["t"])) > MAX_SKEW_S:
        return False
    path = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    message = f"{parts['t']}.{request.method}.{path}.{hashlib.sha256(body).hexdigest()}"
    expected = hmac.new(key, message.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, parts["v1"])


async def streets() -> list[gis.Street]:
    async with state.lock:
        if not state.streets or time.time() - state.streets_loaded_at > STREETS_TTL_S:
            state.streets = await gis.load_streets(_fetcher())
            state.streets_loaded_at = time.time()
    return state.streets


async def health(_: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


async def lookup(request: Request) -> JSONResponse:
    if not verify(request, await request.body()):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    query = (request.query_params.get("q") or "").strip()[:120]
    if not query:
        return JSONResponse({"error": "missing q"}, status_code=400)
    try:
        matches = await gis.search_addresses(_fetcher(), query, await streets(), limit=5)
        buildings = await asyncio.gather(*(gis.building_at(_fetcher(), m.x, m.y) for m in matches))
    except SourceBlockedError as err:
        return JSONResponse({"error": str(err)}, status_code=503)
    return JSONResponse(
        {
            "matches": [
                {
                    "address": m.model_dump(mode="json") | {"label_en": m.label_en},
                    "building": b.model_dump(mode="json") if b else None,
                }
                for m, b in zip(matches, buildings, strict=True)
            ]
        }
    )


@asynccontextmanager
async def lifespan(_: Starlette):
    async with Fetcher() as fetcher:
        state.fetcher = fetcher
        yield


app = Starlette(routes=[Route("/health", health), Route("/lookup", lookup)], lifespan=lifespan)


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), log_level="info")


if __name__ == "__main__":
    main()
