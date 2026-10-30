# Google Cloud (scanner in Tel Aviv)

The city's sites only answer Israeli IPs, so the scanner runs in Cloud Run `me-west1` (Tel Aviv).

## Owner steps (once)
1. Create a Google Cloud account with billing at https://console.cloud.google.com and a project
   (e.g. `tlv-archive-scanner`).
2. Billing → Budgets & alerts → create a budget (e.g. $10/month) with email alerts.
3. On this Mac: `gcloud auth login` (opens a browser).

## Deploy
```bash
export PATH="/opt/homebrew/share/google-cloud-sdk/bin:$PATH"
PROJECT=<project-id> ./infra/gcp/setup.sh probe          # must print 200s, not 571
PROJECT=<project-id> EDGE_URL=https://<edge> ./infra/gcp/setup.sh all
```

Then connect the edge Worker (from `apps/edge`):
```bash
# Shared HMAC key: copy from Secret Manager straight into the Worker secret (never printed).
gcloud secrets versions access latest --secret scanner-hmac | npx wrangler secret put SCANNER_HMAC_KEY
# Dispatch key: create, store, delete the local copy.
gcloud iam service-accounts keys create /tmp/edge-dispatch.json --iam-account edge-dispatch@<project-id>.iam.gserviceaccount.com
npx wrangler secret put GCP_SA_KEY < /tmp/edge-dispatch.json && rm /tmp/edge-dispatch.json
```
and set `LOOKUP_URL` and `GCP_PROJECT` in `apps/edge/wrangler.jsonc`.

## Update the scanner
```bash
gcloud builds submit apps/scanner --tag me-west1-docker.pkg.dev/<project-id>/scanner/scanner:latest --region me-west1
gcloud run jobs update scanner --region me-west1 --image me-west1-docker.pkg.dev/<project-id>/scanner/scanner:latest
gcloud run deploy lookup --region me-west1 --image me-west1-docker.pkg.dev/<project-id>/scanner/scanner:latest
```
