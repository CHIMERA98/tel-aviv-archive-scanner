"""Engineering archive (ארכיון הנדסה): SharePoint search for a building's documents, DocViewer downloads.

The public results page runs a SharePoint CSOM search. Anonymous callers may only run it through the
page's "safe query" template, so we send the same QueryTemplate and SafeQueryPropertiesTemplateUrl
the page does, with the building ID in ResultsUrl. See docs/REBUILD_PLAN.md §12.1.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from xml.sax.saxutils import escape

from scanner import config
from scanner.http import Fetcher

# The archive stores 1.1.2040 or 1.1.1900 when a document has no date.
NO_DATE_YEARS = (1900, 2040)
PAGE_SIZE = 500

# The public page hides some internal document types unless they are marked publishable.
QUERY_TEMPLATE = (
    "({?TlvMPEngBuildingID:{QueryString.buildingId}} TlvMPEngIsConnectedDoc:false "
    "TlvMPEngSensitiveByFolderId:false "
    '(TlvMPEngDocumentType<>"תיק פקוח" AND -TlvMPEngDocumentType:"פיקוח-אחר" '
    'AND -TlvMPEngDocumentType:"מכתבים/תכתובות-שימור" AND TlvMPEngDocumentType<>"תביעות,צווים מינהליים" '
    'AND TlvMPEngDocumentType<>"דואר נכנס ויוצא פיקוח על הבניה" AND TlvMPEngDocumentType<>"בקשה להיתר לשפוץ")) '
    "OR ({?TlvMPEngBuildingID:{QueryString.buildingId}} TlvMPEngIsConnectedDoc:false TlvMPEngPublishable:true)"
)

FIELDS = (
    "UniqueID",
    "FileExtension",
    "TlvMPEngFolderId",
    "TlvMPEngFolderStreetCodeHouseNum",
    "TlvMPEngFolderBlocksParcels",
    "TlvMPEngDocumentType",
    "TlvMPEngDocDate",
    "TlvMPEngRequestNum",
    "TlvMPEngPermitNum",
    "TlvMPEngOnlineReqNum",
)

DIGEST = re.compile(r'id="__REQUESTDIGEST"\s+value="([^"]+)"')
MS_DATE = re.compile(r"/Date\((-?\d+)\)/")


@dataclass(frozen=True)
class ArchiveRecord:
    guid: str
    doc_type: str
    date: date | None
    permit_no: str | None
    request_no: str | None
    online_request_no: str | None
    folder_no: str | None
    street_house: str | None  # e.g. "384_27" (street code _ house number)
    gush_helka: str | None  # e.g. "6977_22"
    extension: str

    @property
    def download_url(self) -> str:
        return f"{config.HANDASA_DOCVIEWER}?id={self.guid}"


def _ms_date(value: str | None) -> date | None:
    match = MS_DATE.search(value or "")
    if not match:
        return None
    when = datetime.fromtimestamp(int(match.group(1)) / 1000, tz=config.ISRAEL_TZ).date()
    return None if when.year <= NO_DATE_YEARS[0] or when.year >= NO_DATE_YEARS[1] else when


def _clean(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _request_body(building_id: int, start_row: int) -> str:
    def prop(id_: int, name: str, type_: str, value: str) -> str:
        return (
            f'<SetProperty Id="{id_}" ObjectPathId="1" Name="{name}">'
            f'<Parameter Type="{type_}">{value}</Parameter></SetProperty>'
        )

    results_url = f"{config.HANDASA_SEARCH_PAGE}?buildingId={building_id}"
    select = "".join(
        f'<Method Name="Add" Id="{100 + i}" ObjectPathId="2"><Parameters>'
        f'<Parameter Type="String">{field}</Parameter></Parameters></Method>'
        for i, field in enumerate(FIELDS)
    )
    return (
        '<Request xmlns="http://schemas.microsoft.com/sharepoint/clientquery/2009" SchemaVersion="15.0.0.0" '
        'LibraryVersion="16.0.0.0" ApplicationName="Javascript Library"><Actions>'
        '<ObjectPath Id="10" ObjectPathId="1" />'
        + prop(11, "QueryTemplate", "String", escape(QUERY_TEMPLATE))
        + prop(12, "StartRow", "Number", str(start_row))
        + prop(13, "RowLimit", "Number", str(PAGE_SIZE))
        + prop(14, "Culture", "Number", "1037")
        + prop(15, "SourceId", "Guid", "{" + config.HANDASA_SOURCE_ID + "}")
        + prop(16, "TrimDuplicates", "Boolean", "false")
        + prop(17, "ResultsUrl", "String", escape(results_url))
        + prop(18, "SafeQueryPropertiesTemplateUrl", "String", escape(config.HANDASA_SAFE_QUERY_TEMPLATE))
        + prop(19, "IgnoreSafeQueryPropertiesTemplateUrl", "Boolean", "false")
        + '<ObjectPath Id="20" ObjectPathId="2" />'
        + select
        + '<ObjectPath Id="30" ObjectPathId="3" />'
        '<Method Name="ExecuteQuery" Id="31" ObjectPathId="3"><Parameters>'
        '<Parameter ObjectPathId="1" /></Parameters></Method>'
        "</Actions><ObjectPaths>"
        '<Constructor Id="1" TypeId="{80173281-fffd-47b6-9a49-312e06ff8428}" />'
        '<Property Id="2" ParentId="1" Name="SelectProperties" />'
        '<Constructor Id="3" TypeId="{8d2ac302-db2f-46fe-9015-872b35f15098}" />'
        "</ObjectPaths></Request>"
    )


def parse_results(payload: list) -> tuple[list[ArchiveRecord], int]:
    """Turn one ProcessQuery response into records and the total row count."""
    header = payload[0] if payload else {}
    if header.get("ErrorInfo"):
        raise RuntimeError(f"Archive search failed: {header['ErrorInfo'].get('ErrorMessage')}")
    container = next((x for x in payload if isinstance(x, dict) and "ResultTables" in x), None)
    if container is None:
        # ExecuteQuery returns the table collection keyed by query id.
        container = next(
            (
                v
                for x in payload
                if isinstance(x, dict)
                for v in x.values()
                if isinstance(v, dict) and "ResultTables" in v
            ),
            None,
        )
    if container is None or not container["ResultTables"]:
        return [], 0
    table = container["ResultTables"][0]
    records = []
    for row in table.get("ResultRows", []):
        guid = (row.get("UniqueID") or "").strip("{}").upper()
        if not guid:
            continue
        records.append(
            ArchiveRecord(
                guid=guid,
                doc_type=(_clean(row.get("TlvMPEngDocumentType")) or "מסמך"),
                date=_ms_date(row.get("TlvMPEngDocDate")),
                permit_no=_clean(row.get("TlvMPEngPermitNum")),
                request_no=_clean(row.get("TlvMPEngRequestNum")),
                online_request_no=_clean(row.get("TlvMPEngOnlineReqNum")),
                folder_no=_clean(row.get("TlvMPEngFolderId")),
                street_house=_clean(row.get("TlvMPEngFolderStreetCodeHouseNum")),
                gush_helka=_clean(row.get("TlvMPEngFolderBlocksParcels")),
                extension=(_clean(row.get("FileExtension")) or "pdf").lower(),
            )
        )
    return records, int(table.get("TotalRows") or len(records))


async def list_documents(fetcher: Fetcher, building_id: int) -> list[ArchiveRecord]:
    page = await fetcher.get(config.HANDASA_SEARCH_PAGE, params={"buildingId": building_id})
    page.raise_for_status()
    digest = DIGEST.search(page.text)
    if not digest:
        raise RuntimeError("Archive page did not include a request digest; the site may have changed")

    records: list[ArchiveRecord] = []
    seen: set[str] = set()
    start = 0
    while True:
        response = await fetcher.post(
            config.HANDASA_PROCESS_QUERY,
            content=_request_body(building_id, start).encode("utf-8"),
            headers={
                "Content-Type": "text/xml",
                "X-RequestDigest": digest.group(1),
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        response.raise_for_status()
        batch, total = parse_results(response.json())
        for record in batch:
            if record.guid not in seen:
                seen.add(record.guid)
                records.append(record)
        start += len(batch)
        if not batch or start >= total:
            return records


async def download(fetcher: Fetcher, record: ArchiveRecord, dest: Path) -> int:
    """Download one document; returns its size. Rejects HTML error pages posing as files."""
    size = await fetcher.download(record.download_url, dest)
    with dest.open("rb") as handle:
        head = handle.read(64)
    if (record.extension == "pdf" and not head.startswith(b"%PDF")) or head.lstrip()[:1] == b"<":
        dest.unlink()
        raise RuntimeError(f"Archive returned a non-document response for {record.guid}")
    return size
