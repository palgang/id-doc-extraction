#!/usr/bin/env bash
# Run the service locally against real GCP services using your gcloud ADC credentials.
#   gcloud auth application-default login
#   GCP_PROJECT_ID=my-project DOCAI_PROCESSOR_ID=abc123 scripts/run_local.sh
# Without DOCAI_PROCESSOR_ID, Gemini reads the image alone; with GEMINI_ENABLED=false, Document AI + MRZ only.
set -euo pipefail
cd "$(dirname "$0")/.."
exec uvicorn app.main:app --reload --port "${PORT:-8080}"
