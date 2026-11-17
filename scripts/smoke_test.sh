#!/usr/bin/env bash
# Call the deployed API with one or more files of the same document.
#   SERVICE_URL=https://... scripts/smoke_test.sh samples/01_personalausweis_front.png samples/01_personalausweis_back.png
set -euo pipefail
: "${SERVICE_URL:?set SERVICE_URL}"
[[ $# -ge 1 ]] || { echo "usage: $0 FILE [FILE...]"; exit 2; }
args=()
for f in "$@"; do args+=(-F "files=@$f"); done
TOKEN="${ID_TOKEN:-$(gcloud auth print-identity-token)}"
curl -sS --fail-with-body -H "Authorization: Bearer $TOKEN" "${args[@]}" "$SERVICE_URL/v1/extract" | python3 -m json.tool
