"""Data shapes shared by sources, renderers and the manifest."""

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field

Source = Literal["archive", "plan", "map"]


class AddressMatch(BaseModel):
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

    @property
    def label_en(self) -> str:
        street = (self.street_en or self.street_he).title()
        return f"{street} {self.house_number}{self.entrance}".strip()


class Building(BaseModel):
    building_id: int
    x: float  # EPSG:3857 point inside the footprint
    y: float
    lat: float
    lon: float
    outline: list[list[tuple[float, float]]] = Field(default_factory=list)  # rings of (lon, lat)
    year_built: int | None = None
    floors: int | None = None
    building_type: str | None = None
    name: str | None = None


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


class ScanManifest(BaseModel):
    scan_id: str
    created_at: dt.datetime
    address: AddressMatch
    building: Building
    archive_folder_no: str | None = None
    plans: list[Plan] = Field(default_factory=list)
    documents: list[Document] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    outputs: dict[str, str] = Field(default_factory=dict)  # name -> path inside the scan folder
