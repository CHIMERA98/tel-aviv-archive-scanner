"""Signed calls from the scanner job to the Cloudflare edge (progress, uploads, completion).

Every request carries `X-Scanner-Signature: t=<unix seconds>,v1=<hex>` where v1 is
HMAC-SHA256(key, f"{t}.{METHOD}.{path}.{sha256(body) hex}") and `X-Content-SHA256: <the same digest>`.
The edge rejects signatures older than 5 minutes. It streams file bodies to R2 without hashing them itself.
"""

import asyncio
import hashlib
import hmac
import time
from pathlib import Path
from urllib.parse import quote

import httpx

SINGLE_PUT_LIMIT = 90 * 1024 * 1024  # Cloudflare accepts request bodies up to 100 MB
PART_SIZE = 50 * 1024 * 1024
UPLOAD_CONCURRENCY = 6


def sign(key: bytes, method: str, path: str, body: bytes, now: int | None = None) -> str:
    t = str(now if now is not None else int(time.time()))
    digest = hashlib.sha256(body).hexdigest()
    mac = hmac.new(key, f"{t}.{method.upper()}.{path}.{digest}".encode(), hashlib.sha256).hexdigest()
    return f"t={t},v1={mac}"


class EdgeClient:
    def __init__(self, base_url: str, key: bytes, scan_id: str) -> None:
        self._base = base_url.rstrip("/")
        self._key = key
        self._scan = scan_id
        self._client = httpx.AsyncClient(timeout=120)

    async def __aenter__(self) -> "EdgeClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    async def _call(self, method: str, path: str, *, body: bytes = b"", json: object = None) -> httpx.Response:
        if json is not None:
            body = httpx.Request("POST", "http://x", json=json).content
        headers = {
            "X-Scanner-Signature": sign(self._key, method, path, body),
            "X-Content-SHA256": hashlib.sha256(body).hexdigest(),
        }
        if json is not None:
            headers["Content-Type"] = "application/json"
        attempts = 4
        for attempt in range(attempts):
            last = attempt == attempts - 1
            try:
                response = await self._client.request(method, self._base + path, content=body, headers=headers)
            except httpx.TransportError:
                if last:
                    raise
            else:
                if response.status_code < 500 or last:
                    response.raise_for_status()
                    return response
            await asyncio.sleep(2**attempt)
            headers["X-Scanner-Signature"] = sign(self._key, method, path, body)
        raise AssertionError("unreachable")

    def _scan_path(self, suffix: str) -> str:
        return f"/internal/scans/{self._scan}{suffix}"

    async def job(self) -> dict:
        return (await self._call("GET", self._scan_path(""))).json()

    async def progress(self, stage: str, status: str, detail: str | None) -> None:
        await self._call(
            "POST", self._scan_path("/progress"), json={"stage": stage, "status": status, "detail": detail}
        )

    async def done(self, summary: dict) -> None:
        await self._call("POST", self._scan_path("/done"), json=summary)

    async def failed(self, error: str, *, blocked: bool = False) -> None:
        await self._call("POST", self._scan_path("/failed"), json={"error": error, "blocked": blocked})

    async def upload_file(self, local: Path, rel: str) -> None:
        size = local.stat().st_size
        file_path = self._scan_path(f"/files/{quote(rel)}")
        if size <= SINGLE_PUT_LIMIT:
            await self._call("PUT", file_path, body=local.read_bytes())
            return
        upload_id = (await self._call("POST", file_path + "?uploads")).json()["upload_id"]
        parts = []
        with local.open("rb") as handle:
            number = 1
            while chunk := handle.read(PART_SIZE):
                part_path = f"{file_path}?upload_id={quote(upload_id)}&part={number}"
                etag = (await self._call("PUT", part_path, body=chunk)).json()["etag"]
                parts.append({"part": number, "etag": etag})
                number += 1
        await self._call("POST", f"{file_path}?upload_id={quote(upload_id)}&complete", json={"parts": parts})

    async def upload_tree(self, root: Path) -> int:
        files = [p for p in sorted(root.rglob("*")) if p.is_file()]
        slots = asyncio.Semaphore(UPLOAD_CONCURRENCY)

        async def one(path: Path) -> None:
            async with slots:
                await self.upload_file(path, path.relative_to(root).as_posix())

        await asyncio.gather(*(one(p) for p in files))
        return len(files)
