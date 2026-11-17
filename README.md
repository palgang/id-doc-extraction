# ID document extraction on GCP (German MVP)

Extracts name, date of birth, document number, nationality, expiry and other data from scans
of German identity documents (Personalausweis, Reisepass) using **Document AI** OCR,
**ICAO 9303 MRZ validation** and **Gemini** on Vertex AI. Runs on Cloud Run.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design.

```
app/
  main.py        FastAPI: /v1/extract, /events/gcs, web UI, /healthz
  pipeline.py    OCR -> MRZ -> Gemini -> reconcile -> validate
  mrz.py         MRZ parsing, check digits, OCR repair (TD1/TD2/TD3)
  ocr.py         Document AI client
  llm.py         Gemini client (structured output)
  schemas.py     response models
infra/terraform  all GCP resources
scripts/         deploy.sh, smoke_test.sh, run_local.sh, make_specimens.py
tests/           unit + API tests (no cloud needed)
```

## Run the tests

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest
python scripts/make_specimens.py samples   # synthetic test images
```

## Deploy

Prerequisites: a GCP project with billing, `gcloud` logged in as an Owner (or Editor plus
Service Usage Admin and Project IAM Admin), Terraform ≥ 1.6.

```bash
PROJECT_ID=my-project scripts/deploy.sh
```

This enables the APIs, creates the Document AI processor, buckets, service accounts and
registry, runs the tests and builds the image in Cloud Build, then deploys Cloud Run and the
Eventarc trigger. Optional variables: `REGION` (default `europe-west3`), `INVOKER`
(default: your gcloud account), `GEMINI_MODEL`, `VERTEX_LOCATION`.

## Use

**API** (front and back of one ID card in one call):

```bash
export SERVICE_URL=$(terraform -chdir=infra/terraform output -raw service_url)
scripts/smoke_test.sh samples/01_personalausweis_front.png samples/01_personalausweis_back.png
```

**Web UI**: `gcloud run services proxy id-extractor --region europe-west3 --port 8080`, then open
http://localhost:8080.

**Batch**: copy a file (image or 2-page PDF) into the input bucket. The JSON result appears
as `<name>.json` in the output bucket and the input is deleted.

```bash
gsutil cp samples/03_reisepass.png gs://$PROJECT_ID-id-extractor-in/
gsutil cat gs://$PROJECT_ID-id-extractor-out/03_reisepass.png.json
```

## Configuration (environment variables)

| Variable | Default | |
|---|---|---|
| `GCP_PROJECT_ID` | — | Project for Document AI and Vertex AI |
| `DOCAI_LOCATION` | `eu` | Document AI location |
| `DOCAI_PROCESSOR_ID` | — | OCR processor id; unset = Gemini reads the image alone |
| `VERTEX_LOCATION` | `global` | Vertex AI location for Gemini |
| `GEMINI_MODEL` | `gemini-3.5-flash` | Model id |
| `GEMINI_ENABLED` | `true` | `false` = Document AI + MRZ only |
| `OUTPUT_BUCKET` | — | Bucket for event-driven results |
| `DELETE_INPUT_AFTER_PROCESSING` | `true` | |
| `MAX_UPLOAD_BYTES` / `MAX_FILES` | 15 MB / 4 | |

## Privacy

Only use synthetic or specimen documents for testing. Never commit real scans or results;
`.gitignore` excludes `data/` and `results/`.
