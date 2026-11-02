"""Source endpoints and scanning limits."""

import os
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
POLITE_HOST_SUFFIXES = (".tel-aviv.gov.il", ".haifa.muni.il", "gis-net.co.il")
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

# ---------- Haifa ----------

HAIFA_GIS = "https://gisserver.haifa.muni.il/arcgiswebadaptor/rest/services/PublicSite"
HAIFA_ADDRESSES = f"{HAIFA_GIS}/Haifa_Stat_Public/MapServer/0"
HAIFA_BUILDINGS = f"{HAIFA_GIS}/Building_2021/MapServer/0"
HAIFA_CONSERVATION = f"{HAIFA_GIS}/Haifa_Shimur_Public/MapServer/0"
HAIFA_PLAN_PARCELS = f"{HAIFA_GIS}/Haifa_Eng_Public/MapServer/7"  # current gush/helka, linked to Complot
HAIFA_PLANS_APPROVED = f"{HAIFA_GIS}/Haifa_Eng_Public/MapServer/9"
HAIFA_PLANS_DEPOSITED = f"{HAIFA_GIS}/Haifa_Eng_Public/MapServer/8"
HAIFA_SR = 2039  # Israeli TM grid, metres

# Complot runs Haifa's planning committee site. Its page endpoint has a strict DoS guard (HTTP 429 for 20+ minutes),
# so every page call goes through one paced client; see docs/HAIFA_PLAN.md §2.
# COMPLOT_BASE can be pointed elsewhere for local end-to-end runs that must not reach Complot.
COMPLOT_BASE = os.environ.get("COMPLOT_BASE", "https://handasi.complot.co.il")
COMPLOT_PAGES = f"{COMPLOT_BASE}/magicscripts/mgrqispi.dll?appname=cixpa&prgname="
COMPLOT_SITE_ID = 16
COMPLOT_CITY_CODE = 4000
COMPLOT_INTERVAL_S = 12.0  # default gap between page calls; the edge can raise it after a block
# A first delivery downloads at most this many documents before the scan opens; the rest arrive in the background.
HAIFA_FIRST_DELIVERY_DOCS = 15
# Haifa permit plans reach 70 MB each. Larger files stay downloadable one by one but are left out of bundle.zip,
# which keeps the ZIP usable and the worker's in-memory disk bounded.
HAIFA_BUNDLE_MAX_FILE_BYTES = 25 * 1024 * 1024

MAPWARPER_TILES = "https://mapwarper.net/maps/tile/{id}/{{z}}/{{x}}/{{y}}.png"
# Warped Haifa maps on mapwarper.net (MapJunction has none). Fixed here because the site's search is user content.
# bbox: west, south, east, north.
HAIFA_MAPS = (
    (109152, "Haifa 1918", 1918, (34.998212, 32.8019782, 35.0171229, 32.8165401)),
    (108633, "Haifa 1939 (town plan)", 1939, (34.9959379, 32.7980432, 35.0107292, 32.8116719)),
    (108625, "Haifa 1939", 1939, (34.9886595, 32.7955378, 35.0148632, 32.8290166)),
    (108549, "Haifa 1942", 1942, (34.9526076, 32.8013747, 35.0378219, 32.84637)),
    (109147, "Haifa 1945", 1945, (34.9881717, 32.7898859, 35.0336092, 32.8284267)),
    (108628, "Haifa 1949", 1949, (34.9867347, 32.7975553, 35.0183737, 32.825381)),
    (109144, "Haifa 1949 (full sheet)", 1949, (34.9860047, 32.7971918, 35.0191955, 32.8255564)),
)
# Haifa's neighbourhood plans reach ~1.6 km² (חפ/421); city-wide ones start around 6 km².
HAIFA_MAX_PLAN_AREA_M2 = 2_000_000
