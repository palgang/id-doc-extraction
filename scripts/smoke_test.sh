#!/usr/bin/env bash
# Call the deployed API with one or more files of the same document.
#   SERVICE_URL=https://... scripts/smoke_test.sh samples/01_personalausweis_front.png samples/01_personalausweis_back.png
#   MODEL=gemini-3.1-pro-preview picks another Gemini model (see GET /v1/models).
set -euo pipefail
: "${SERVICE_URL:?set SERVICE_URL}"
[[ $# -ge 1 ]] || { echo "usage: $0 FILE [FILE...]"; exit 2; }
args=()
for f in "$@"; do args+=(-F "files=@$f"); done
[[ -n "${MODEL:-}" ]] && args+=(-F "model=$MODEL")
# Service accounts need an explicit audience; user accounts reject the flag.
TOKEN="${ID_TOKEN:-$(gcloud auth print-identity-token --audiences="$SERVICE_URL" 2>/dev/null || gcloud auth print-identity-token)}"
curl -sS --fail-with-body -H "Authorization: Bearer $TOKEN" "${args[@]}" "$SERVICE_URL/v1/extract" | python3 -m json.tool
