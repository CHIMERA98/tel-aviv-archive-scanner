"""Complot (handasi.complot.co.il): Haifa's building files, permit requests, plans and their documents.

The page endpoint (magicscripts) blocks an IP for 20+ minutes when called too often (HTTP 429), so:
- one client, one request in flight, at least `interval` seconds between page calls;
- a 429, F5's reject page or two suspect answers in a row (EXPECTED) raise ComplotBlockedError, never retried;
- every page and document goes through a cache first, so nothing is fetched twice.
Pages report "no results" and errors as HTTP 200 HTML; the parsers recognise both.
"""

import asyncio
import contextlib
import hashlib
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode

import httpx

from scanner import config
from scanner.models import PermitEvent, PermitRequest

NO_RESULTS = "לא אותרו תוצאות"
ERROR_PAGE = "אירעה שגיאה"
DOWNLOAD_LINK = re.compile(r'class="download-link"\s+href="([^"]+)"')
JS_CALL = re.compile(r"(\w+)\(([^)]*)\)")
MAX_CACHED_BYTES = 90 * 1024 * 1024
# F5's "request rejected" page, which comes with HTTP 200.
REJECTED_PAGE = re.compile(r"requested URL was rejected|support ID", re.IGNORECASE)
# Text every genuine answer of these pages has, besides "no results" and Complot's error page. A page without it is
# suspect: on 2026-11-02 Complot silently answered every GetDocumentIFN without a link after 83 calls (§12.1).
EXPECTED = {
    "GetTikimByAddress": "תוצאות איתור תיקי בניין",
    "GetTikFile": "פרטי תיק בניין",
    "GetBakashaFile": "פרטי בקשה",
    "GetTabaFile": "פרטי התוכנית",
    "ShowPhoto": 'class="download-link"',
    "GetDocumentIFN": 'class="download-link"',
}
LINK_PAGES = ("ShowPhoto", "GetDocumentIFN")  # their content is one file's link, which a file may lack
SUSPECTS_FOR_BLOCK = 2  # suspect answers in a row that count as a block


class ComplotBlockedError(RuntimeError):
    """Complot's DoS guard refused us (HTTP 429 or silently). Stop all Complot work; the edge pauses it 45 minutes."""


class ComplotError(RuntimeError):
    """Complot answered with its error page or something that isn't the expected file."""


class ComplotSuspectError(ComplotError):
    """Complot answered HTTP 200 without what that page always has: a missing file, or the start of a silent block."""


class SourceCache(Protocol):
    async def get(self, key: str) -> bytes | None: ...
    async def put(self, key: str, data: bytes) -> None: ...


class NoCache:
    async def get(self, key: str) -> bytes | None:
        return None

    async def put(self, key: str, data: bytes) -> None:
        return None


class LocalCache:
    """Folder-backed cache for local runs and tests."""

    def __init__(self, root: Path) -> None:
        self._root = root

    async def get(self, key: str) -> bytes | None:
        path = self._root / key
        return path.read_bytes() if path.exists() else None

    async def put(self, key: str, data: bytes) -> None:
        path = self._root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


# ---------- HTML tables ----------


@dataclass
class Cell:
    text: str = ""
    calls: list[tuple[str, list[str]]] = field(default_factory=list)  # JavaScript calls in links: (name, args)


class _Tables(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[Cell]]] = []
        self._open: list[list[list[Cell]]] = []
        self._cell: Cell | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._open.append([])
        elif tag == "tr" and self._open:
            self._open[-1].append([])
        elif tag in ("td", "th") and self._open and self._open[-1]:
            self._cell = Cell()
            self._open[-1][-1].append(self._cell)
        if self._cell is not None:
            for name, value in attrs:
                if name in ("href", "onclick") and value and "(" in value:
                    for fn, args in JS_CALL.findall(value):
                        if fn not in ("disableDocButton",):
                            self._cell.calls.append((fn, [a.strip().strip("'\"{}") for a in args.split(",")]))

    def handle_endtag(self, tag: str) -> None:
        if tag == "table" and self._open:
            self.tables.append(self._open.pop())
        elif tag in ("td", "th"):
            self._cell = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.text += data


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("‏", "").replace("‎", "")).strip()


def tables(html: str) -> list[list[list[Cell]]]:
    parser = _Tables()
    parser.feed(html)
    for table in parser.tables:
        for row in table:
            for cell in row:
                cell.text = _clean(cell.text)
    return parser.tables


def _headers(table: list[list[Cell]]) -> list[str]:
    return [c.text for c in table[0]] if table else []


def _find(all_tables: list[list[list[Cell]]], *headers: str) -> list[list[Cell]]:
    """Rows (without the header row) of the first table whose header contains all `headers`."""
    for table in all_tables:
        if all(h in _headers(table) for h in headers):
            return [row for row in table[1:] if row and any(c.text or c.calls for c in row)]
    return []


def _key_values(all_tables: list[list[list[Cell]]], first_key: str) -> dict[str, Cell]:
    for table in all_tables:
        if table and table[0] and table[0][0].text == first_key:
            return {row[0].text: row[1] for row in table if len(row) >= 2}
    return {}


def _cells(row: list[Cell], headers: list[str], *wanted: str) -> list[Cell]:
    """Cells by header name; rows may lack the leading icon column the header has."""
    offset = len(headers) - len(row) if len(row) < len(headers) else 0
    out = []
    for name in wanted:
        index = headers.index(name) - offset if name in headers else -1
        out.append(row[index] if 0 <= index < len(row) else Cell())
    return out


def _date(text: str | None) -> date | None:
    try:
        return datetime.strptime((text or "").strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def _call_arg(cell: Cell, fn: str, index: int = 0) -> str | None:
    for name, args in cell.calls:
        if name == fn and len(args) > index:
            return args[index]
    return None


def is_no_results(html: str) -> bool:
    return NO_RESULTS in html or ERROR_PAGE in html


# ---------- parsers ----------


@dataclass(frozen=True)
class BuildingFileRef:
    number: str
    address: str
    gush: str | None
    helka: str | None


@dataclass
class RequestRef:
    request_no: str
    submitted: date | None
    last_event: str | None
    applicant: str | None
    permit_no: str | None
    permit_date: date | None


@dataclass
class BuildingFile:
    number: str
    requests: list[RequestRef]
    addresses: list[str] = field(default_factory=list)

    @property
    def streets(self) -> set[str]:
        """Street names without the house number and city: "רח' הגפן 4 חיפה" -> "רח' הגפן"."""
        return {re.sub(r"\s+\d+\S*(\s+חיפה)?$", "", a.removesuffix(" חיפה")) for a in self.addresses}

    def is_batch(self) -> bool:
        return len(self.streets) > config.HAIFA_BATCH_FILE_STREETS


@dataclass(frozen=True)
class DocumentRef:
    guid: str
    name: str
    subject: str | None
    date: date | None


@dataclass(frozen=True)
class PlanFileRef:
    args: tuple[str, str, str, str]  # showArchiveFile(ec, en, bn, m)
    name: str
    subject: str | None
    date: date | None


@dataclass
class PlanDetails:
    status: str | None
    status_date: date | None
    deposit_date: date | None
    authority: str | None
    files: list[PlanFileRef]


def parse_building_files(html: str) -> list[BuildingFileRef]:
    if is_no_results(html):
        return []
    found: dict[str, BuildingFileRef] = {}
    for table in tables(html):
        headers = _headers(table)
        if "תיק בניין" not in headers:
            continue
        for row in table[1:]:
            number_cell, address, gush, helka = _cells(row, headers, "תיק בניין", "כתובת", "גוש", "חלקה")
            number = _call_arg(number_cell, "getBuilding") or number_cell.text
            if number and number.isdigit() and number not in found:
                found[number] = BuildingFileRef(number, address.text, gush.text or None, helka.text or None)
    return list(found.values())


def parse_building_file(html: str, number: str) -> BuildingFile:
    requests: list[RequestRef] = []
    for table in tables(html):
        headers = _headers(table)
        if "מספר בקשה" not in headers or "תאריך הגשה" not in headers:
            continue
        for row in table[1:]:
            no, submitted, last, applicant, permit, permit_date = _cells(
                row, headers, "מספר בקשה", "תאריך הגשה", "ארוע אחרון להצגה", "שם המבקש", "היתר", "תאריך היתר"
            )
            request_no = _call_arg(no, "getRequest") or no.text
            if not request_no or not request_no.isdigit():
                continue
            existing = next((r for r in requests if r.request_no == request_no), None)
            if existing:  # a permit shows up as a second row of the same request
                existing.permit_no = existing.permit_no or (permit.text or None)
                existing.permit_date = existing.permit_date or _date(permit_date.text)
                continue
            requests.append(
                RequestRef(
                    request_no=request_no,
                    submitted=_date(submitted.text),
                    last_event=last.text or None,
                    applicant=applicant.text or None,
                    permit_no=permit.text or None,
                    permit_date=_date(permit_date.text),
                )
            )
        break
    # The address list is a headerless one-column table under id="addresses".
    section = re.search(r'id="addresses".*?</table>', html, re.S)
    cells = re.findall(r"<td[^>]*>(.*?)</td>", section.group(0), re.S) if section else []
    addresses = [text for c in cells if (text := _clean(unescape(re.sub(r"<[^>]+>", " ", c))))]
    return BuildingFile(number=number, requests=requests, addresses=addresses)


def parse_request(html: str, request_no: str, building_file: str) -> tuple[PermitRequest, list[DocumentRef]]:
    all_tables = tables(html)
    info = _key_values(all_tables, "מספר תיק בניין")

    def value(key: str) -> str | None:
        cell = info.get(key)
        return cell.text if cell and cell.text else None

    # "מהות הבקשה" is the first one-column table; the later ones are headed "מהות" / "החלטות".
    essence_rows = next(
        (t for t in all_tables if t and len(t[0]) == 1 and t[0][0].text not in ("מהות", "החלטות")), None
    )
    parties = [(r[0].text, r[1].text) for r in _find(all_tables, "סוג בעל עניין", "שם בעל עניין") if len(r) >= 2]
    event_headers = ["סוג אירוע", "תיאור אירוע", "תאריך אירוע", "תאריך סיום אירוע", "מטפל"]
    events = []
    for row in _find(all_tables, *event_headers[:3]):
        kind, desc, when, until, handler = _cells(row, event_headers, *event_headers)
        if desc.text:
            events.append(
                PermitEvent(
                    kind=kind.text or None,
                    description=desc.text,
                    date=_date(when.text),
                    end_date=_date(until.text),
                    handler=handler.text or None,
                )
            )
    decisions = [r[0].text for r in _find(all_tables, "החלטות") if r and r[0].text]
    documents: list[DocumentRef] = []
    for row in _find(all_tables, "שם המסמך", "נושא"):
        guid = _call_arg(row[0], "showIFNFile")
        if not guid:
            continue
        documents.append(
            DocumentRef(
                guid=guid.strip("{}").upper(),
                name=row[0].text or "מסמך",
                subject=row[1].text if len(row) > 1 and row[1].text else None,
                date=_date(row[2].text) if len(row) > 2 else None,
            )
        )
    request = PermitRequest(
        request_no=request_no,
        building_file=building_file,
        submitted=_date(_submitted(html)),
        request_type=value("סוג הבקשה"),
        main_use=value("שימוש עיקרי"),
        description=value("תיאור הבקשה"),
        essence=[r[0].text for r in essence_rows or [] if r[0].text and r[0].text != "לא נמצאו נתונים"],
        permit_no=value("מספר היתר"),
        permit_date=_date(value("תאריך הפקת היתר")),
        online_request_no=value("מספר הבקשה ברישוי זמין"),
        parties=parties,
        events=events,
        decisions=decisions,
        url=page_url("GetBakashaFile", b=request_no),
    )
    return request, documents


def _submitted(html: str) -> str | None:
    match = re.search(r"תאריך הגשה:\s*</[^>]+>\s*<[^>]+>\s*([0-9/]{8,10})", html)
    if match:
        return match.group(1)
    match = re.search(r"תאריך הגשה:[^0-9]{0,200}?(\d{2}/\d{2}/\d{4})", html)
    return match.group(1) if match else None


def parse_plan(html: str) -> PlanDetails:
    all_tables = tables(html)
    info = _key_values(all_tables, "סטטוס תוכנית")
    deposit = None
    for row in _find(all_tables, "מתאריך", "שם השלב"):
        if len(row) >= 2 and "הפקדת" in row[1].text:
            deposit = _date(row[0].text)
    files = []
    for row in _find(all_tables, "שם המסמך", "נושא", "תאריך"):
        for fn, args in row[0].calls:
            if fn == "showArchiveFile" and len(args) == 4:
                files.append(
                    PlanFileRef(
                        args=(args[0], args[1], args[2], args[3]),
                        name=row[0].text,
                        subject=row[1].text if len(row) > 1 and row[1].text else None,
                        date=_date(row[2].text) if len(row) > 2 else None,
                    )
                )
    status = info.get("סטטוס תוכנית")
    status_date = info.get("תאריך הסטטוס")
    authority = info.get("בסמכות")
    return PlanDetails(
        status=status.text if status and status.text else None,
        status_date=_date(status_date.text) if status_date else None,
        deposit_date=deposit,
        authority=authority.text if authority and authority.text else None,
        files=files,
    )


def pick_plan_files(files: list[PlanFileRef]) -> list[PlanFileRef]:
    """Regulations and the approved map sheet; the rest of a plan's archive rarely matters for one building."""
    chosen = [f for f in files if "תקנון" in f.name or "תשריט" in f.name or "תקנון" in (f.subject or "")]
    return chosen or files[:2]


def parse_download_link(html: str) -> str:
    match = DOWNLOAD_LINK.search(html)
    if not match:
        raise ComplotError("Complot did not return a download link" + (" (error page)" if ERROR_PAGE in html else ""))
    return match.group(1)


# ---------- client ----------


def page_url(prgname: str, **params: str | int) -> str:
    query = {"siteid": config.COMPLOT_SITE_ID, **params}
    return f"{config.COMPLOT_PAGES}{prgname}&{urlencode(query, safe='{},')}&arguments={','.join(query)}"


def _cache_key(prgname: str, params: dict[str, str | int]) -> str:
    digest = hashlib.sha1(urlencode(sorted(params.items())).encode()).hexdigest()[:16]
    return f"pages/{prgname}/{digest}.html"


class ComplotClient:
    def __init__(self, cache: SourceCache | None = None, *, interval: float = config.COMPLOT_INTERVAL_S) -> None:
        self.cache: SourceCache = cache or NoCache()
        self.interval = interval
        self.calls = 0  # page calls actually sent to Complot (cache hits excluded)
        self.suspects = 0  # suspect answers in a row (see EXPECTED)
        self._suspect_saved = False
        self._lock = asyncio.Lock()
        self._last = 0.0
        self._client = httpx.AsyncClient(
            headers={"User-Agent": config.USER_AGENT}, timeout=httpx.Timeout(60, read=180), follow_redirects=True
        )

    async def __aenter__(self) -> "ComplotClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    async def _paced_get(self, url: str) -> httpx.Response:
        async with self._lock:
            wait = self._last + self.interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()
            self.calls += 1
            response = await self._client.get(url)
        if response.status_code == 429:
            raise ComplotBlockedError("Complot refused the request (HTTP 429): too many requests")
        response.raise_for_status()
        return response

    async def _fetch(self, prgname: str, **params: str | int) -> str:
        """One paced page call, checked for a block: F5's reject page, or suspect answers in a row."""
        response = await self._paced_get(page_url(prgname, **params))
        html = response.content.decode("utf-8-sig", errors="replace")
        if REJECTED_PAGE.search(html):
            raise ComplotBlockedError("Complot rejected the request (HTTP 200 'request rejected' page)")
        expected = EXPECTED.get(prgname)
        if expected is None or expected in html or NO_RESULTS in html or ERROR_PAGE in html:
            self.suspects = 0
            return html
        self.suspects += 1
        if not self._suspect_saved:  # keep one, so the block page can be recognised directly next time
            self._suspect_saved = True
            with contextlib.suppress(Exception):
                await self.cache.put(f"debug/{prgname}-{datetime.now(UTC):%Y%m%dT%H%M%S}.html", html.encode("utf-8"))
        if self.suspects >= SUSPECTS_FOR_BLOCK:
            raise ComplotBlockedError(f"Complot answered {self.suspects} pages in a row without their content")
        raise ComplotSuspectError(f"Complot answered {prgname} without its content")

    async def page(self, prgname: str, **params: str | int) -> str:
        key = _cache_key(prgname, params)
        cached = await self.cache.get(key)
        if cached is not None:
            return cached.decode("utf-8-sig")
        try:
            html = await self._fetch(prgname, **params)
        except ComplotSuspectError:
            if prgname in LINK_PAGES:  # a file can lack its link; the next document's answer decides
                raise
            html = await self._fetch(prgname, **params)  # a page always has its title: a second miss is a block
        if ERROR_PAGE not in html:  # don't keep transient errors for 30 days
            await self.cache.put(key, html.encode("utf-8"))
        return html

    async def building_files(self, street_code: int, house_number: int) -> list[BuildingFileRef]:
        html = await self.page("GetTikimByAddress", c=config.COMPLOT_CITY_CODE, s=street_code, h=house_number, l="true")
        return parse_building_files(html)

    async def building_file(self, number: str) -> BuildingFile:
        return parse_building_file(await self.page("GetTikFile", t=number), number)

    async def request(self, request_no: str, building_file: str) -> tuple[PermitRequest, list[DocumentRef]]:
        return parse_request(await self.page("GetBakashaFile", b=request_no), request_no, building_file)

    async def plan(self, number: int) -> PlanDetails:
        return parse_plan(await self.page("GetTabaFile", n=number))

    async def plan_file_url(self, ref: PlanFileRef) -> str:
        ec, en, bn, m = ref.args
        return parse_download_link(await self.page("ShowPhoto", ec=ec, en=en, bn=bn, m=m))

    async def document(self, guid: str, dest: Path) -> Path:
        """Download one request document. Its PDF exists only after GetDocumentIFN, which costs one page call."""
        # Documents come as pdf, dwfx, tif, jpg or doc; a tiny "<guid>.name" entry remembers which.
        known = await self.cache.get(f"docs/{guid}.name")
        if known:
            cached = await self.cache.get(f"docs/{guid}.{known.decode()}")
            if cached is not None:
                return _write(dest.with_suffix(f".{known.decode()}"), cached)
        link = parse_download_link(await self._ifn_page(guid))
        ext = (link.rsplit(".", 1)[-1] or "pdf").lower()[:4]
        data = await self._static(link)
        if data[:1] == b"<" or data[:3] == b"\xef\xbb\xbf" or (ext == "pdf" and not data.startswith(b"%PDF")):
            raise ComplotError(f"Complot returned an error page instead of document {guid}")
        if len(data) <= MAX_CACHED_BYTES:  # the edge accepts request bodies up to 100 MB
            try:
                await self.cache.put(f"docs/{guid}.{ext}", data)
                await self.cache.put(f"docs/{guid}.name", ext.encode())
            except httpx.HTTPError:
                pass  # the document is still delivered; it just isn't kept for the next scan
        return _write(dest.with_suffix(f".{ext}"), data)

    async def _ifn_page(self, guid: str) -> str:
        # Not cached: the link it returns is only good once the server has copied the file.
        return await self._fetch("GetDocumentIFN", guid="{" + guid + "}")

    async def _static(self, url: str) -> bytes:
        response = await self._client.get(url)
        if response.status_code == 429:
            raise ComplotBlockedError("Complot refused a download (HTTP 429)")
        response.raise_for_status()
        return response.content


def _write(dest: Path, data: bytes) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest
