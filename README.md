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

## Deploy from Cloud Shell (recommended)

Cloud Shell is already signed in and has `gcloud`, Terraform and git, so no service account
key is needed. Open [Cloud Shell](https://shell.cloud.google.com/?show=terminal) and run:

```bash
git clone https://github.com/palgang/id-doc-extraction.git && cd id-doc-extraction
export PROJECT_ID=your-project-id          # needs billing; you need Owner on it
gcloud config set project "$PROJECT_ID"
scripts/deploy.sh                          # ~10 minutes; answers no prompts
```

Then test the deployed service with the synthetic samples:

```bash
export SERVICE_URL=$(terraform -chdir=infra/terraform output -raw service_url)
scripts/smoke_test.sh samples/01_personalausweis_front.png samples/01_personalausweis_back.png
scripts/smoke_test.sh samples/03_reisepass.png
```

Terraform state is stored in the bucket `gs://$PROJECT_ID-tfstate` (created by `deploy.sh`,
versioned), so later deploys from any machine update the existing resources. Set
`STATE_BUCKET` to use another bucket. Local state from older checkouts is moved there on the
next deploy.

## Deploy from elsewhere

Prerequisites: a GCP project with billing, `gcloud` logged in as an Owner (or Editor plus
Service Usage Admin and Project IAM Admin), either as a user or as a service account
(`gcloud auth activate-service-account --key-file=key.json`), Terraform ≥ 1.6
(`scripts/install_terraform.sh` installs it if the Terraform registry is unreachable).

```bash
PROJECT_ID=my-project scripts/deploy.sh
```

This enables the APIs, creates the Document AI processor, buckets, service accounts and
registry, runs the tests and builds the image in Cloud Build, then deploys Cloud Run and the
Eventarc trigger. Optional variables: `REGION` (default `europe-west3`), `INVOKER`
(default: your gcloud account), `STATE_BUCKET` (default `$PROJECT_ID-tfstate`),
`GEMINI_MODEL`, `GEMINI_ALLOWED_MODELS`, `VERTEX_LOCATION`.

Right after the APIs are first enabled, Cloud Build and Eventarc can reject requests for a
few minutes while IAM propagates. `deploy.sh` retries those steps, so a first deploy can
pause for a minute or two.

## Use

**API** (front and back of one ID card in one call):

```bash
export SERVICE_URL=$(terraform -chdir=infra/terraform output -raw service_url)
scripts/smoke_test.sh samples/01_personalausweis_front.png samples/01_personalausweis_back.png
```

**Choosing the Gemini model**: every request uses `GEMINI_MODEL` unless it names another
one. `GET /v1/models` lists the models that may be chosen; pass one as the form field
`model`, or set `MODEL` for the smoke test:

```bash
MODEL=gemini-3.1-pro-preview scripts/smoke_test.sh samples/03_reisepass.png
curl -H "Authorization: Bearer $(gcloud auth print-identity-token)" "$SERVICE_URL/v1/models"
```

A model that is not on the list is rejected with HTTP 422. The response reports the model
used in `processing.llm_model`. The web UI shows the same list as a dropdown and remembers
your choice in the browser.

**Web UI**: start a local proxy, then open http://localhost:8080:

```bash
gcloud run services proxy id-extractor --project "$PROJECT_ID" --region europe-west3 --port 8080
```

Always pass `--project`: without it, gcloud uses whatever project is set in your local
config and reports that the service does not exist. Only the accounts in `INVOKER` (and
project Owners) may call the service; add more with
`gcloud run services add-iam-policy-binding id-extractor --project "$PROJECT_ID" --region europe-west3 --member user:you@example.com --role roles/run.invoker`.

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
| `GEMINI_MODEL` | `gemini-3.5-flash` | Default model id |
| `GEMINI_ALLOWED_MODELS` | see below | Comma-separated model ids callers may choose; `GEMINI_MODEL` is always included |
| `GEMINI_ENABLED` | `true` | `false` = Document AI + MRZ only |
| `OUTPUT_BUCKET` | — | Bucket for event-driven results |
| `DELETE_INPUT_AFTER_PROCESSING` | `true` | |
| `MAX_UPLOAD_BYTES` / `MAX_FILES` | 15 MB / 4 | |

The built-in model list is `gemini-3.5-flash`, `gemini-3.5-flash-lite`, `gemini-3.6-flash`,
`gemini-3.7-flash`, `gemini-3.8-flash` and `gemini-3.1-pro-preview` (in `app/config.py`).
Google adds and retires Vertex AI models often; set `GEMINI_ALLOWED_MODELS` to change the
list without a code change. A model that is listed but not available in your project or
`VERTEX_LOCATION` returns the result with an `LLM_UNAVAILABLE` issue (MRZ-only data).

## Privacy

Only use synthetic or specimen documents for testing. Never commit real scans or results;
`.gitignore` excludes `data/` and `results/`.
