# Scanner (Python)

Collects, for one building, historic maps, zoning plans and engineering-archive documents, and produces `index.csv`,
page images, `PROMPT.he.md` / `PROMPT.en.md`, two PowerPoint decks and `bundle.zip`.

- **Tel Aviv-Yafo:** MapJunction, GIS layer 528 + tabaot documents, the handasa archive (SharePoint search + DocViewer).
- **Haifa** ([docs/HAIFA_PLAN.md](../../docs/HAIFA_PLAN.md)): Haifa GIS, Complot (building files → permit requests →
  documents, plus plan pages), mapwarper.net maps, and `requests.md`. Complot blocks IPs that call it too often, so
  every Complot call goes through one paced, cached client and a 429 is never retried.

The municipal sites only answer **Israeli IP addresses** — run it from Israel or from Google Cloud `me-west1`.

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m scanner lookup 'הגר"א 27'
.venv/bin/python -m scanner scan 'הגר"א 27' --out out
.venv/bin/python -m scanner lookup 'בן גוריון 50' --city haifa       # GIS only, no Complot calls
.venv/bin/python -m scanner scan 'בן גוריון 50' --city haifa --out out  # ~70 Complot calls, cached in out/.complot-cache
.venv/bin/pytest            # offline tests
```

Entry points:
- `scanner.cli` — local lookup / scan.
- `scanner.job` — Cloud Run job: one execution per scan, reports to the edge and uploads results through it.
- `scanner.service` — Cloud Run service: signed `/lookup?q=&city=` for the edge (address → building).
- `scanner.haifa_worker` — Cloud Run job `haifa-worker`: the single Haifa worker; pulls work from the edge queue.
