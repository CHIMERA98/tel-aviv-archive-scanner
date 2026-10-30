# Scanner (Python)

Collects, for one Tel Aviv-Yafo building: historic maps (MapJunction), zoning plans (GIS layer 528 + tabaot
documents) and the engineering archive (SharePoint search + DocViewer). Produces `index.csv`, page images,
`PROMPT.he.md` / `PROMPT.en.md`, two PowerPoint decks and `bundle.zip`.

The municipal sites only answer **Israeli IP addresses** — run it from Israel or from Google Cloud `me-west1`.

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m scanner lookup 'הגר"א 27'
.venv/bin/python -m scanner scan 'הגר"א 27' --out out
.venv/bin/pytest            # offline tests
```

Entry points:
- `scanner.cli` — local lookup / scan.
- `scanner.job` — Cloud Run job: one execution per scan, reports to the edge and uploads results through it.
- `scanner.service` — Cloud Run service: signed `/lookup` for the edge (address → building).
