"""HTTP entry points (Cloud Run).

* ``POST /v1/extract``   synchronous API: upload 1..n images/PDF of one document, get JSON back.
* ``POST /events/gcs``   Eventarc target: a file landed in the input bucket; write JSON to the output bucket.
* ``GET  /``             minimal upload page for manual testing.
* ``GET  /healthz``      liveness probe.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse

from .config import get_settings
from .llm import Image, get_extractor
from .ocr import get_ocr_engine
from .pipeline import Pipeline
from .schemas import ExtractionResult

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(levelname)s %(name)s %(message)s")
log = logging.getLogger("id_extraction")

app = FastAPI(title="ID Document Extraction", version="0.1.0")
_STATIC = Path(__file__).parent / "static"


def get_pipeline() -> Pipeline:
    settings = get_settings()
    return Pipeline(get_ocr_engine(settings), get_extractor(settings))


def sniff_mime(content: bytes) -> str | None:
    """Detect the file type from magic bytes; never trust the client's Content-Type."""
    if content.startswith(b"%PDF"):
        return "application/pdf"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    if content[:4] == b"GIF8":
        return "image/gif"
    if content[:2] == b"BM":
        return "image/bmp"
    return None


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (_STATIC / "index.html").read_text(encoding="utf-8")


@app.post("/v1/extract", response_model=ExtractionResult)
async def extract(files: list[UploadFile] = File(..., description="Front/back images or a PDF of ONE document")) -> ExtractionResult:
    settings = get_settings()
    if not files or len(files) > settings.max_files:
        raise HTTPException(400, f"Upload between 1 and {settings.max_files} files of the same document.")
    images: list[Image] = []
    for f in files:
        content = await f.read(settings.max_upload_bytes + 1)
        if len(content) > settings.max_upload_bytes:
            raise HTTPException(413, f"{f.filename}: file exceeds {settings.max_upload_bytes} bytes.")
        mime = sniff_mime(content)
        if mime is None:
            raise HTTPException(415, f"{f.filename}: unsupported file type (use JPEG, PNG, TIFF, WEBP or PDF).")
        images.append(Image(content=content, mime_type=mime))
    return get_pipeline().run(images)


@app.post("/events/gcs")
async def gcs_event(request: Request) -> Response:
    """Handle a ``google.cloud.storage.object.v1.finalized`` CloudEvent from Eventarc.

    Returns 2xx for permanent problems (so Eventarc does not retry forever) and 5xx for
    transient ones (so it does).
    """
    from google.cloud import storage

    settings = get_settings()
    event = await request.json()
    bucket_name, object_name = event.get("bucket"), event.get("name")
    if not bucket_name or not object_name:
        return Response(status_code=204)
    if object_name.endswith("/"):
        return Response(status_code=204)

    client = storage.Client(project=settings.project_id)
    blob = client.bucket(bucket_name).blob(object_name)
    request_id = request.headers.get("ce-id") or uuid.uuid4().hex
    try:
        content = blob.download_as_bytes()
    except Exception:  # noqa: BLE001
        log.warning("object vanished or unreadable request_id=%s", request_id)
        return Response(status_code=204)

    mime = sniff_mime(content)
    if mime is None or len(content) > settings.max_upload_bytes:
        payload: dict = {"request_id": request_id, "status": "FAILED", "source": f"gs://{bucket_name}/{object_name}",
                         "issues": [{"code": "UNSUPPORTED_FILE", "message": "Unsupported type or too large.", "severity": "error"}]}
    else:
        result = get_pipeline().run([Image(content=content, mime_type=mime)], request_id=request_id)
        payload = {"source": f"gs://{bucket_name}/{object_name}", **result.model_dump(mode="json")}

    if not settings.output_bucket:
        raise HTTPException(500, "OUTPUT_BUCKET not configured")
    out = client.bucket(settings.output_bucket).blob(f"{object_name}.json")
    out.upload_from_string(json.dumps(payload, ensure_ascii=False, indent=2), content_type="application/json")
    if settings.delete_input_after_processing:
        try:
            blob.delete()
        except Exception:  # noqa: BLE001
            log.warning("could not delete input request_id=%s", request_id)
    return Response(status_code=204)
