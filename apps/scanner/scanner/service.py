"""Lookup service (Cloud Run service in Tel Aviv): address search and building resolution.

The edge can't call the city's GIS reliably itself (geo-block), so it asks this service.
Requests are signed exactly like the scanner's calls to the edge (see edge_client.sign).

  GET /health
  GET /lookup?q=<address text>[&city=tel-aviv|haifa]
      -> {"city": ..., "matches": [{"address": ..., "building": ... | null}, ...]}
      Without `city`, "חיפה / Haifa" in the text picks Haifa; otherwise Tel Aviv.
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

from scanner import cities
from scanner.http import Fetcher, SourceBlockedError

MAX_SKEW_S = 300


class State:
    def __init__(self) -> None:
        self.fetcher: Fetcher | None = None
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


async def health(_: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


async def lookup(request: Request) -> JSONResponse:
    if not verify(request, await request.body()):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    query = (request.query_params.get("q") or "").strip()[:120]
    if not query:
        return JSONResponse({"error": "missing q"}, status_code=400)
    city = cities.parse_city(request.query_params.get("city")) or cities.detect_city(query)
    try:
        async with state.lock:  # loads the street list once per city
            await cities.streets(_fetcher(), city)
        found = await cities.lookup(_fetcher(), city, query, limit=5)
    except SourceBlockedError as err:
        return JSONResponse({"error": str(err)}, status_code=503)
    return JSONResponse(
        {
            "city": city,
            "matches": [
                {
                    "address": m.model_dump(mode="json") | {"label_en": m.label_en},
                    "building": b.model_dump(mode="json") if b else None,
                }
                for m, b in found
            ],
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
