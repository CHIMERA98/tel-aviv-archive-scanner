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
# Long sheets are also cut into overlapping tiles so their details (and photos pasted on them) stay readable.
TILE_MIN_ASPECT = 2.0  # long edge / short edge
TILE_EDGE = 1600
TILE_OVERLAP = 160
TILE_SHORT_EDGE = 1200  # target short edge of a tiled page, when the source has that much detail
TILE_MIN_GAIN = 1.5  # tile only when tiles are at least this much sharper than the plain page image
MAX_TILES_PER_PAGE = 12
# Contact sheets: thumbnail grids of a document's pages and tiles.
SHEET_THUMB_EDGE = 240
SHEET_COLUMNS = 6
SHEET_CELLS = 24

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
COMPLOT_INTERVAL_S = 18.0  # default gap between page calls; the edge can raise it after a block
# A first delivery downloads at most this many documents before the scan opens; the rest arrive in the background.
HAIFA_FIRST_DELIVERY_DOCS = 15
# A building file whose addresses span more streets than this is a city-wide batch file (e.g. a gas company's tanks
# at 58 addresses): its requests aren't about this building, so they aren't opened.
HAIFA_BATCH_FILE_STREETS = 5
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
# Palestine Open Maps (palopenmaps.org, by Visualizing Palestine): georeferenced maps of mandate Palestine as XYZ
# tiles (docs/HAIFA_PLAN.md §11). It states no license, so every map names its exact source and says so.
PALOPENMAPS_SITE = "https://palopenmaps.org/"
PALOPENMAPS_RIGHTS = "Publishing rights unknown: Palestine Open Maps states no license for these maps."
_POM = "Palestine Open Maps (palopenmaps.org)"
# key, title, year, tiles, deepest zoom used, source (catalog layer name, original author, scan source).
PALOPENMAPS_LAYERS = (
    (
        "pal63k-1880",
        "Western Palestine 1:63,360 (PEF survey, 1870s)",
        1878,
        "https://palopenmaps.org/tiles/pal63k-1880/{z}/{x}/{y}@2x.jpg",
        15,
        f'{_POM}, layer "Western Palestine, 1:63,000, 1870s". Map: Palestine Exploration Fund. '
        "Scans: David Rumsey Historical Map Collection, National Library of Australia.",
    ),
    (
        "pal-1940s",
        "Survey of Palestine city map (1930s-40s)",
        None,
        "https://palopenmaps.org/tiles/pal-1940s/{z}/{x}/{y}.jpg",
        19,
        f'{_POM}, layer "Combined Maps, 1930s/40s". Map: Survey of Palestine. Scan: National Library of Israel.',
    ),
    (
        "pal20k-1940s",
        "Palestine 1:20,000 (Survey of Palestine, 1940s)",
        1942,
        "https://palopenmaps.org/tiles/pal20k-1940s/{z}/{x}/{y}.jpg",
        16,
        f'{_POM}, layer "Palestine, 1:20,000, 1940s". Map: Survey of Palestine. Scan: National Library of Israel.',
    ),
    (
        "aerial1940s",
        "Aerial photographs (RAF, 1940s)",
        1944,
        "https://cdn.jsdelivr.net/gh/bothness/pom-tiles@2d154a81c81c2647f74dd4ceee4b1bee66119e79/aerial1940s/{z}/{x}/{y}.png",
        16,
        f'{_POM}, layer "Aerial imagery, 1940s". Photos: Royal Air Force, via the Hebrew University of Jerusalem; '
        "georeferenced by Shaul Holtzman.",
    ),
)
# Haifa's neighbourhood plans reach ~1.6 km² (חפ/421); city-wide ones start around 6 km².
HAIFA_MAX_PLAN_AREA_M2 = 2_000_000
