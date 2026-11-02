"""Data shapes shared by sources, renderers and the manifest."""

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Source = Literal["archive", "plan", "map"]
City = Literal["tel-aviv", "haifa"]
DocumentStatus = Literal["ready", "pending", "failed"]


class AddressMatch(BaseModel):
    city: City = "tel-aviv"
    street_code: int
    street_he: str
    street_en: str | None = None
    house_number: int
    entrance: str = ""
    label_he: str
    lat: float
    lon: float
    x: float  # EPSG:3857
    y: float
    gush: int | None = None
    helka: int | None = None
    neighborhood: str | None = None

    @property
    def label_en(self) -> str:
        street = (self.street_en or self.street_he).title()
        label = f"{street} {self.house_number}{self.entrance}".strip()
        return f"{label}, Haifa" if self.city == "haifa" else label


class Conservation(BaseModel):
    """Haifa's conservation register (מבנים לשימור)."""

    status: str | None = None
    name: str | None = None
    grade: str | None = None
    file_no: str | None = None  # e.g. בש/11136
    survey: str | None = None
    surveyors: str | None = None


class Building(BaseModel):
    city: City = "tel-aviv"
    # Stable identity for reuse and caching: "tel-aviv:<id_binyan>" or "haifa:<street code>:<house number>".
    key: str = ""
    building_id: int | None = None  # Tel Aviv's id_binyan
    x: float  # EPSG:3857 point inside the footprint
    y: float
    lat: float
    lon: float
    outline: list[list[tuple[float, float]]] = Field(default_factory=list)  # rings of (lon, lat)
    year_built: int | None = None
    floors: int | None = None
    building_type: str | None = None
    name: str | None = None
    height_m: float | None = None
    conservation: Conservation | None = None

    @model_validator(mode="after")
    def _default_key(self) -> "Building":
        if not self.key and self.building_id is not None:
            self.key = f"{self.city}:{self.building_id}"
        return self


class Plan(BaseModel):
    plan_no: str
    id_taba: int | None = None
    name: str | None = None
    status: str | None = None
    in_force_date: dt.date | None = None
    deposit_date: dt.date | None = None
    area_m2: float | None = None
    documents_url: str | None = None
    iplan_url: str | None = None


class HistoricMap(BaseModel):
    map_id: str
    title: str
    year: int | None = None
    folder: str | None = None
    tiles: str
    zoom_min: int = 0
    zoom_max: int = 20
    thumbnail: str | None = None
    credit: str | None = None  # source and rights text, printed under the image and kept as the document's note


class Document(BaseModel):
    """One row of index.csv / one item the AI can open."""

    doc_id: str  # A-001 archive, P-001 plan file, M-001 map
    source: Source
    title: str
    doc_type: str | None = None
    date: dt.date | None = None
    year: int | None = None
    permit_no: str | None = None
    request_no: str | None = None
    plan_no: str | None = None
    plan_status: str | None = None
    original_url: str | None = None
    file: str | None = None  # path inside the scan folder
    pages: int = 0
    page_images: list[str] = Field(default_factory=list)
    note: str | None = None
    status: DocumentStatus = "ready"  # Haifa documents start "pending" and arrive in the background
    size: int | None = None  # bytes of `file`, when known
    guid: str | None = None  # Haifa: Complot document id
    priority: int = 0  # Haifa: download order, lower first


class PermitEvent(BaseModel):
    kind: str | None = None  # נוכחי / סגור
    description: str
    date: dt.date | None = None
    end_date: dt.date | None = None
    handler: str | None = None


class PermitRequest(BaseModel):
    """One Haifa permit request (בקשה להיתר) from Complot, summarised in requests.md."""

    request_no: str
    building_file: str
    submitted: dt.date | None = None
    request_type: str | None = None
    main_use: str | None = None
    description: str | None = None
    essence: list[str] = Field(default_factory=list)  # מהות הבקשה
    permit_no: str | None = None
    permit_date: dt.date | None = None
    online_request_no: str | None = None  # רישוי זמין
    parties: list[tuple[str, str]] = Field(default_factory=list)  # (role, name)
    events: list[PermitEvent] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    url: str | None = None


class ScanManifest(BaseModel):
    scan_id: str
    city: City = "tel-aviv"
    created_at: dt.datetime
    address: AddressMatch
    building: Building
    archive_folder_no: str | None = None
    building_files: list[str] = Field(default_factory=list)  # Haifa: Complot building file numbers
    requests: list[PermitRequest] = Field(default_factory=list)
    plans: list[Plan] = Field(default_factory=list)
    documents: list[Document] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    outputs: dict[str, str] = Field(default_factory=dict)  # name -> path inside the scan folder
