#!/usr/bin/env bash
# One-time Google Cloud setup for the scanner (Cloud Run in Tel Aviv, me-west1).
# Usage: PROJECT=<gcp-project-id> EDGE_URL=https://... ./infra/gcp/setup.sh [probe|all]
#   probe — only check that the city's sites answer from me-west1 (run this first)
#   all   — enable APIs, build the image, create the job, the lookup service and the dispatch account
set -euo pipefail

: "${PROJECT:?set PROJECT to the Google Cloud project id}"
REGION=me-west1
REPO=scanner
IMAGE="$REGION-docker.pkg.dev/$PROJECT/$REPO/scanner:latest"
RUNTIME_SA="scanner-runtime@$PROJECT.iam.gserviceaccount.com"
DISPATCH_SA="edge-dispatch@$PROJECT.iam.gserviceaccount.com"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STEP="${1:-all}"

gcloud config set project "$PROJECT" >/dev/null
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com \
  secretmanager.googleapis.com iam.googleapis.com >/dev/null

probe() {
  echo "== Probing the city's sites from $REGION"
  gcloud run jobs deploy geo-probe --region "$REGION" --image curlimages/curl:8.10.1 --tasks 1 --max-retries 0 \
    --command sh --args=-c,'for u in "https://gisn.tel-aviv.gov.il/arcgis/rest/services/IView2/MapServer/527?f=json" "https://handasa.tel-aviv.gov.il/Pages/searchResultsAnonPageNew.aspx?buildingId=2287"; do curl -s -o /dev/null -w "PROBE %{http_code} $u\n" "$u"; done' \
    --quiet >/dev/null
  gcloud run jobs execute geo-probe --region "$REGION" --wait --quiet >/dev/null
  sleep 5
  gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=geo-probe AND textPayload:PROBE" \
    --limit 10 --format "value(textPayload)" --freshness 10m
  gcloud run jobs delete geo-probe --region "$REGION" --quiet >/dev/null
  echo "200 = reachable; 571 = blocked by the municipality's geo-filter."
}

if [[ "$STEP" == "probe" ]]; then probe; exit 0; fi
: "${EDGE_URL:?set EDGE_URL to the edge Worker URL}"

echo "== Artifact Registry + image"
gcloud artifacts repositories describe "$REPO" --location "$REGION" >/dev/null 2>&1 ||
  gcloud artifacts repositories create "$REPO" --repository-format docker --location "$REGION" --quiet
gcloud builds submit "$ROOT/apps/scanner" --tag "$IMAGE" --region "$REGION" --quiet

echo "== Shared HMAC secret (also set on the Worker as SCANNER_HMAC_KEY)"
if ! gcloud secrets describe scanner-hmac >/dev/null 2>&1; then
  openssl rand -hex 32 | tr -d '\n' | gcloud secrets create scanner-hmac --data-file=- --replication-policy automatic
fi

echo "== Service accounts"
gcloud iam service-accounts describe "$RUNTIME_SA" >/dev/null 2>&1 ||
  gcloud iam service-accounts create scanner-runtime --display-name "Scanner runtime"
gcloud iam service-accounts describe "$DISPATCH_SA" >/dev/null 2>&1 ||
  gcloud iam service-accounts create edge-dispatch --display-name "Cloudflare edge: starts scanner jobs"
gcloud secrets add-iam-policy-binding scanner-hmac --member "serviceAccount:$RUNTIME_SA" \
  --role roles/secretmanager.secretAccessor --quiet >/dev/null

echo "== Scanner job (one execution per scan)"
gcloud run jobs deploy scanner --region "$REGION" --image "$IMAGE" --tasks 1 --max-retries 0 \
  --task-timeout 45m --cpu 2 --memory 4Gi --service-account "$RUNTIME_SA" \
  --set-env-vars "EDGE_URL=$EDGE_URL,SCAN_TIMEOUT_S=2400" --set-secrets "SCANNER_HMAC_KEY=scanner-hmac:latest" --quiet

echo "== Lookup service (address -> building), scale to zero"
gcloud run deploy lookup --region "$REGION" --image "$IMAGE" --command python --args=-m,scanner.service \
  --cpu 1 --memory 512Mi --min-instances 0 --max-instances 2 --concurrency 20 --service-account "$RUNTIME_SA" \
  --set-secrets "SCANNER_HMAC_KEY=scanner-hmac:latest" --allow-unauthenticated --quiet
# Requests are still authenticated: every /lookup call must carry the HMAC signature.

echo "== Let the edge start (only) this job"
gcloud run jobs add-iam-policy-binding scanner --region "$REGION" --member "serviceAccount:$DISPATCH_SA" \
  --role roles/run.developer --quiet >/dev/null
gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SA" --member "serviceAccount:$DISPATCH_SA" \
  --role roles/iam.serviceAccountUser --quiet >/dev/null

echo
echo "Lookup URL: $(gcloud run services describe lookup --region "$REGION" --format 'value(status.url)')"
echo "Next: create a key for $DISPATCH_SA and store it on the Worker (see infra/gcp/README.md)."
