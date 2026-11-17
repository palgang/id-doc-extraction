"""OCR via Google Cloud Document AI (Enterprise Document OCR processor)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

from .config import Settings

log = logging.getLogger(__name__)


@dataclass
class OcrResult:
    text: str
    pages: int


class OcrEngine(Protocol):
    name: str

    def ocr(self, content: bytes, mime_type: str) -> OcrResult: ...


class DocumentAiOcr:
    name = "documentai"

    def __init__(self, settings: Settings) -> None:
        from google.api_core.client_options import ClientOptions
        from google.cloud import documentai_v1 as documentai

        self._documentai = documentai
        self._client = documentai.DocumentProcessorServiceClient(
            client_options=ClientOptions(api_endpoint=f"{settings.docai_location}-documentai.googleapis.com")
        )
        self._processor = self._client.processor_path(
            settings.project_id, settings.docai_location, settings.docai_processor_id
        )

    def ocr(self, content: bytes, mime_type: str) -> OcrResult:
        documentai = self._documentai
        request = documentai.ProcessRequest(
            name=self._processor,
            raw_document=documentai.RawDocument(content=content, mime_type=mime_type),
            process_options=documentai.ProcessOptions(
                ocr_config=documentai.OcrConfig(
                    # The MRZ is printed in OCR-B; hinting German keeps umlauts in the VIZ.
                    hints=documentai.OcrConfig.Hints(language_hints=["de", "en"]),
                    enable_native_pdf_parsing=True,
                )
            ),
        )
        doc = self._client.process_document(request=request, timeout=60).document
        return OcrResult(text=doc.text, pages=len(doc.pages))


class NoOcr:
    """Used when Document AI is not configured (local development): Gemini alone reads the image."""

    name = "none"

    def ocr(self, content: bytes, mime_type: str) -> OcrResult:
        return OcrResult(text="", pages=1)


@lru_cache
def get_ocr_engine(settings: Settings) -> OcrEngine:
    if settings.docai_enabled:
        return DocumentAiOcr(settings)
    log.warning("DOCAI_PROCESSOR_ID not set: running without Document AI OCR")
    return NoOcr()
