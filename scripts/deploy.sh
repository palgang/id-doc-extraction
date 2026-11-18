#!/usr/bin/env bash
# Deploy the ID extractor with Terraform + Cloud Build.
#
#   PROJECT_ID=my-project [REGION=europe-west3] [INVOKER=user:me@example.com] scripts/deploy.sh
#
# Needs: gcloud (authenticated as a user or a service account),
# terraform >= 1.6 (scripts/install_terraform.sh installs it).
set -euo pipefail

: "${PROJECT_ID:?set PROJECT_ID}"
REGION="${REGION:-europe-west3}"
STATE_BUCKET="${STATE_BUCKET:-$PROJECT_ID-tfstate}"
TAG="${TAG:-$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TF="terraform -chdir=$ROOT/infra/terraform"

# Everything below runs against PROJECT_ID, whatever project gcloud is configured for.
export CLOUDSDK_CORE_PROJECT="$PROJECT_ID"

if [[ -z "${INVOKER:-}" ]]; then
  ACCOUNT="$(gcloud config get-value account 2>/dev/null)"
  if [[ "$ACCOUNT" == *.gserviceaccount.com ]]; then INVOKER="serviceAccount:$ACCOUNT"; else INVOKER="user:$ACCOUNT"; fi
fi

TF_VARS=(-var "project_id=$PROJECT_ID" -var "region=$REGION" -var "invoker_members=[\"$INVOKER\"]")
[[ -n "${GEMINI_MODEL:-}" ]] && TF_VARS+=(-var "gemini_model=$GEMINI_MODEL")
# Comma-separated, e.g. GEMINI_ALLOWED_MODELS=gemini-3.5-flash,gemini-3.1-pro-preview
if [[ -n "${GEMINI_ALLOWED_MODELS:-}" ]]; then
  TF_VARS+=(-var "gemini_allowed_models=[$(sed 's/[^,][^,]*/"&"/g' <<<"${GEMINI_ALLOWED_MODELS// /}")]")
fi
[[ -n "${VERTEX_LOCATION:-}" ]] && TF_VARS+=(-var "vertex_location=$VERTEX_LOCATION")

# Right after APIs are enabled, Cloud Build and Eventarc can reject calls for a few minutes
# until IAM for their service agents has propagated. Retry instead of failing the deploy.
retry() {
  local n
  for n in 1 2 3 4 5 6; do
    "$@" && return 0
    [[ $n -lt 6 ]] && { echo "    attempt $n failed, retrying in 60s (permission propagation)..."; sleep 60; }
  done
  return 1
}

echo "==> 0/3 Terraform state in gs://$STATE_BUCKET"
gcloud services enable serviceusage.googleapis.com cloudresourcemanager.googleapis.com storage.googleapis.com
if ! gcloud storage buckets describe "gs://$STATE_BUCKET" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://$STATE_BUCKET" --location="$REGION" --uniform-bucket-level-access
  gcloud storage buckets update "gs://$STATE_BUCKET" --versioning
fi
INIT=(init -input=false -upgrade -backend-config="bucket=$STATE_BUCKET" -backend-config="prefix=id-extractor")
# Older checkouts kept state locally; move it into the bucket once.
[[ -f "$ROOT/infra/terraform/terraform.tfstate" ]] && INIT+=(-migrate-state -force-copy)
$TF "${INIT[@]}" >/dev/null

echo "==> 1/3 Bootstrap: APIs, registry, buckets, Document AI processor, service accounts"
$TF apply -input=false -auto-approve "${TF_VARS[@]}"

REPO="$($TF output -raw image_repo)"
BUILD_SA="$($TF output -raw build_service_account)"
IMAGE="$REPO/id-extractor:$TAG"

echo "==> 2/3 Test, build and push $IMAGE"
retry gcloud builds submit "$ROOT" \
  --project "$PROJECT_ID" --region "$REGION" \
  --config "$ROOT/cloudbuild.yaml" \
  --substitutions "_IMAGE=$IMAGE" \
  --service-account "$BUILD_SA" \
  --default-buckets-behavior=regional-user-owned-bucket

echo "==> 3/3 Deploy Cloud Run service and Eventarc trigger"
retry $TF apply -input=false -auto-approve "${TF_VARS[@]}" -var "image=$IMAGE"

echo
echo "Service URL:   $($TF output -raw service_url)"
echo "Input bucket:  gs://$($TF output -raw input_bucket)   (drop a scan here)"
echo "Output bucket: gs://$($TF output -raw output_bucket)  (JSON results appear here)"
echo "Try it:        SERVICE_URL=\$(terraform -chdir=infra/terraform output -raw service_url) scripts/smoke_test.sh samples/03_reisepass.png"
echo "Web UI:        gcloud run services proxy id-extractor --project $PROJECT_ID --region $REGION --port 8080  -> http://localhost:8080"
