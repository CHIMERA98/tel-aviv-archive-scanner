# Archive Scanner

Historic maps, zoning plans and engineering-archive records for one building, in one place.

- Tel Aviv-Yafo: <https://tel-aviv-archive-scanner.work>
- Haifa: <https://haifa.tel-aviv-archive-scanner.work>

Type an address and the scanner collects what the city has published about the building: historic maps of the site,
the zoning plans (תב"ע) that apply to it, and the documents in its building file (תיק בניין). Results come as page
images, a document index, two PowerPoint decks and a ZIP, and stay online for 24 hours. An AI app (ChatGPT, Claude)
can connect to the scanner over MCP and read a scan's documents directly.

made by Uri Yahalom (with Claude Code)

## How it's built

| Part | What it does |
|---|---|
| [`apps/edge`](apps/edge) | Cloudflare Worker (Hono): the web app, sign-in by email link, quotas, the scan queue, results in R2, and the MCP server for AI apps. |
| [`apps/scanner`](apps/scanner) | Python: the address lookup service and the scan jobs that talk to the municipal sources and build the outputs. Runs on Google Cloud Run in `me-west1`, since the municipal sites only answer Israeli IP addresses. See its [README](apps/scanner/README.md). |
| [`infra/gcp`](infra/gcp) | Sets up the Cloud Run jobs, the lookup service and their permissions. |

Haifa's planning-committee site blocks callers that ask too often, so every call to it goes through one paced, cached
worker that stops as soon as the site pushes back.

## License

Copyright © 2026 Uri Yahalom. All rights reserved.

The source is published so people can read how the scanner works. No license is granted to copy, modify,
redistribute or host it; ask first: uri.ym2@gmail.com.
