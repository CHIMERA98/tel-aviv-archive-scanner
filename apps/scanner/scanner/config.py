"""Source endpoints and scanning limits."""

from zoneinfo import ZoneInfo

ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")

USER_AGENT = "TelAvivArchiveScanner/1.0 (+https://tel-aviv-archive-scanner.work)"

GIS_MAPSERVER = "https://gisn.tel-aviv.gov.il/arcgis/rest/services/IView2/MapServer"
LAYER_ADDRESSES = 527
LAYER_BUILDINGS = 513
LAYER_PLANS = 528

HANDASA_BASE = "https://handasa.tel-aviv.gov.il"
HANDASA_SEARCH_PAGE = f"{HANDASA_BASE}/Pages/searchResultsAnonPageNew.aspx"
HANDASA_PROCESS_QUERY = f"{HANDASA_BASE}/_vti_bin/client.svc/ProcessQuery"
HANDASA_DOCVIEWER = f"{HANDASA_BASE}/API/Pages/DocViewer.aspx"
HANDASA_SOURCE_ID = "8413cd39-2156-4e00-b54d-11efd9abdb89"
HANDASA_SAFE_QUERY_TEMPLATE = "querygroup://webroot/Pages/SearchResultsAnonPageNew.aspx?groupname=Default"

TABA_BASE = "https://gisn.tel-aviv.gov.il"

MAPJUNCTION_FILTER = "https://alpha.mapjunction.com/api/maps/filter/"

# Plans covering more than this (m²) are city-wide and say nothing about one building.
MAX_PLAN_AREA_M2 = 300_000

# Larger plan scans (some are 50-70 MB, served at ~80 KB/s) are linked instead of copied.
MAX_PLAN_FILE_BYTES = 15 * 1024 * 1024

# Politeness toward the municipality's servers: simultaneous requests per host and minimum gap between starts.
POLITE_HOST_SUFFIXES = (".tel-aviv.gov.il",)
PER_HOST_CONCURRENCY = 3
PER_HOST_MIN_INTERVAL_S = 0.25
# CDNs (map tiles) can take more.
CDN_CONCURRENCY = 12

# Page images served to AI agents.
PAGE_IMAGE_MAX_EDGE = 1600
PAGE_IMAGE_JPEG_QUALITY = 72
MAX_PAGES_PER_DOCUMENT = 60

# Historic map crops: zoom level and how many tiles across.
MAP_ZOOM = 18
MAP_GRID = 4  # tiles are 512 px, so the crop is 2048 px across
