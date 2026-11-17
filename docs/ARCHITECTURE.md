# Architecture: German ID document extraction (MVP)

## Goal

Extract the holder's data (name, date of birth, document number, nationality, expiry and
more) from scans or photos of German identity documents, on Google Cloud, using Document AI
and Gemini. The result must say which values can be trusted.

In scope for the MVP:

| Document | MRZ format | Notes |
|---|---|---|
| Personalausweis (2010+ and 2021+ cards) | TD1, 3 × 30, on the back | Front has name, date and place of birth, CAN, expiry. Back has address, height, eye colour, authority, issue date. |
| Reisepass (data page) | TD3, 2 × 44 | Everything on one page. |
| Aufenthaltstitel (residence permit) | TD1 | Works through the same path, labelled best effort. |

Documents from other countries are processed best effort and flagged `UNSUPPORTED_ISSUER`.

## Flow

```
             ┌──────────── sync ────────────┐        ┌────────────── async ───────────────┐
 client ──►  POST /v1/extract (1–4 files)   │        gs://…-in/<name>  ──Eventarc──►  POST /events/gcs
             └───────────────┬──────────────┘        └──────────────────┬─────────────────┘
                             ▼                                          ▼
                    ┌───────────────────── Cloud Run: id-extractor ─────────────────────┐
                    │ 1. sniff file type (magic bytes), size limits                      │
                    │ 2. Document AI Enterprise OCR (eu)  ──►  full text, per file        │
                    │ 3. MRZ locate + parse + ICAO 9303 check digits + OCR repair         │
                    │ 4. Gemini (Vertex AI) on the images + OCR text ──► typed JSON       │
                    │    (VIZ fields, umlauts, its own MRZ transcription as 2nd opinion)  │
                    │ 5. Reconcile MRZ ⇄ VIZ per field, mark verified / source            │
                    │ 6. Validate: expiry, date logic, German validity periods, CAN       │
                    │ 7. Status OK / REVIEW / FAILED + issues                             │
                    └────────────────────────────┬───────────────────────────────────────┘
                                                 ▼
                         JSON response   or   gs://…-out/<name>.json (input deleted)
```

### Why both Document AI and Gemini

* **Document AI OCR** gives a deterministic, high-quality character read. That matters most
  for the MRZ, where a single wrong character breaks a check digit. Document AI's
  pretrained identity parsers cover US documents only, so the generic Enterprise OCR
  processor is used.
* **Gemini** understands layout. It maps labels like "Geburtsort / Place of birth" to fields,
  keeps umlauts that the MRZ transliterates away (Schäfer → SCHAEFER), reads fields that
  only exist in the visual zone (address, place of birth, authority, CAN), and spots image
  quality problems. It is constrained with a JSON response schema and `temperature=0`.
* **The MRZ is the anchor of trust.** Its check digits make it possible to verify document
  number, date of birth and expiry mathematically, independent of either model.

### Reconciliation rules

| Field type | Rule |
|---|---|
| Check-digit fields (document number, birth date, expiry) | If MRZ and VIZ agree: `mrz+visual`, verified. If they disagree and the MRZ check digit passes: MRZ wins, warning. Otherwise: VIZ value, `REVIEW`. |
| Names | Compare after German transliteration (Ä→AE, ß→SS) and allow TD1 truncation. Output the VIZ spelling with umlauts when they match. |
| Nationality, sex | Not protected by a check digit, so only MRZ/VIZ agreement marks them verified. `DEUTSCH` maps to `DEU`. |
| VIZ-only fields | Returned with `source: visual`, `verified: false`. |

If Document AI's MRZ read fails its checksums, Gemini's MRZ transcription is parsed too and
the better one is used (`mrz.read_by` says which). Known OCR-B confusions (O/0, I/1, S/5, B/8,
Z/2, G/6) are repaired in numeric fields, and single swaps are tried in the alphanumeric
document number. Each repair is listed in `mrz.corrections`.

### Status

* `OK`: values extracted and no error-level issue.
* `REVIEW`: something needs a human: missing or failed MRZ, MRZ/VIZ conflict, date
  inconsistency, possible tampering, or Gemini unavailable (MRZ-only result).
* `FAILED`: nothing usable, or not an identity document.

An expired document is reported as a warning, not a failure: the extraction itself is fine.

## Output

```json
{
  "request_id": "…",
  "status": "OK",
  "document": {"type": "ID_CARD", "issuing_country": "DEU", "label": "Personalausweis"},
  "fields": {
    "surname":        {"value": "Schäfer",    "source": "mrz+visual", "verified": true},
    "date_of_birth":  {"value": "1983-08-12", "source": "mrz+visual", "verified": true},
    "address":        {"value": "Musterstraße 12, 50667 Köln", "source": "visual", "verified": false}
  },
  "mrz": {"format": "TD1", "valid": true, "lines": ["…"], "checks": [ … ], "corrections": [], "read_by": "documentai"},
  "issues": [{"code": "DOCUMENT_EXPIRED", "message": "…", "severity": "warning"}],
  "processing": {"ocr_engine": "documentai", "llm_model": "gemini-3.5-flash", "pages": 2, "duration_ms": 2900}
}
```

## GCP resources (Terraform, `infra/terraform`)

| Resource | Purpose |
|---|---|
| Cloud Run `id-extractor` (europe-west3) | API + event handler. Not public: callers need `roles/run.invoker`. |
| Document AI `OCR_PROCESSOR` (eu) | OCR. |
| Vertex AI Gemini (`gemini-3.5-flash`, configurable) | Visual-zone extraction. |
| GCS `<project>-id-extractor-in` / `-out` | Batch path. Uniform access, public access prevented, objects auto-deleted after 1 day. |
| Eventarc trigger | `object.finalized` on the input bucket → `/events/gcs`. |
| Artifact Registry + Cloud Build | Test, build and store the image. |
| Service accounts | `-run` (Document AI user, Vertex AI user, bucket access), `-trigger` (invoke), `-build`. Least privilege, no keys. |

## Data protection (identity documents are personal data)

* Processing in the EU: Cloud Run and buckets in Frankfurt, Document AI `eu`. Gemini runs on
  the `global` endpoint by default because new models launch there first; set
  `vertex_location` to an EU region if the chosen model is available there and you need
  EU-only processing.
* Nothing is persisted by the API path. The bucket path deletes the input after processing,
  and lifecycle rules delete anything left after `retention_days`.
* Logs contain request ids, status and issue codes only, never field values.
* The service has no unauthenticated access. Vertex AI does not use customer data for training.
* Test data is synthetic (`scripts/make_specimens.py`) or the published "Erika Mustermann"
  specimen MRZs.

## Known limits and next steps

* No liveness, chip (NFC/eID) reading or security-feature checks: this extracts data, it
  does not prove authenticity. `tampering_indicators` from Gemini is a hint only.
* Front and back of an ID card must be sent in one API call (or as one 2-page PDF in the
  bucket path) to get both visual and MRZ data.
* Next: an evaluation set with accuracy per field, Document AI Custom Extractor trained on
  German layouts as an alternative to Gemini, Pub/Sub dead-letter queue for the bucket
  path, CMEK on buckets, VPC Service Controls.
