#!/usr/bin/env bash
# Deploy the ID extractor with Terraform + Cloud Build.
#
#   PROJECT_ID=my-project [REGION=europe-west3] [INVOKER=user:me@example.com] scripts/deploy.sh
#
# Needs: gcloud (authenticated), terraform >= 1.6 (scripts/install_terraform.sh installs it).
set -euo pipefail

: "${PROJECT_ID:?set PROJECT_ID}"
REGION="${REGION:-europe-west3}"
INVOKER="${INVOKER:-user:$(gcloud config get-value account 2>/dev/null)}"
TAG="${TAG:-$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TF="terraform -chdir=$ROOT/infra/terraform"

TF_VARS=(-var "project_id=$PROJECT_ID" -var "region=$REGION" -var "invoker_members=[\"$INVOKER\"]")
[[ -n "${GEMINI_MODEL:-}" ]] && TF_VARS+=(-var "gemini_model=$GEMINI_MODEL")
[[ -n "${VERTEX_LOCATION:-}" ]] && TF_VARS+=(-var "vertex_location=$VERTEX_LOCATION")

echo "==> 1/3 Bootstrap: APIs, registry, buckets, Document AI processor, service accounts"
$TF init -input=false -upgrade >/dev/null
$TF apply -input=false -auto-approve "${TF_VARS[@]}"

REPO="$($TF output -raw image_repo)"
BUILD_SA="$($TF output -raw build_service_account)"
IMAGE="$REPO/id-extractor:$TAG"

echo "==> 2/3 Test, build and push $IMAGE"
gcloud builds submit "$ROOT" \
  --project "$PROJECT_ID" --region "$REGION" \
  --config "$ROOT/cloudbuild.yaml" \
  --substitutions "_IMAGE=$IMAGE" \
  --service-account "$BUILD_SA" \
  --default-buckets-behavior=regional-user-owned-bucket

echo "==> 3/3 Deploy Cloud Run service and Eventarc trigger"
$TF apply -input=false -auto-approve "${TF_VARS[@]}" -var "image=$IMAGE"

echo
echo "Service URL:   $($TF output -raw service_url)"
echo "Input bucket:  gs://$($TF output -raw input_bucket)   (drop a scan here)"
echo "Output bucket: gs://$($TF output -raw output_bucket)  (JSON results appear here)"
echo "Try it:        SERVICE_URL=\$(terraform -chdir=infra/terraform output -raw service_url) scripts/smoke_test.sh samples/03_reisepass.png"
echo "Web UI:        gcloud run services proxy id-extractor --region $REGION --port 8080  -> http://localhost:8080"
