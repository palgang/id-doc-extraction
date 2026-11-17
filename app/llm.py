"""Structured extraction of the visual inspection zone with Gemini on Vertex AI."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

from .config import Settings
from .schemas import GeminiExtraction

log = logging.getLogger(__name__)

PROMPT = """\
You are a document-verification assistant. The attached image(s) show one identity document,
possibly front and back (for example a German Personalausweis or Reisepass).

Read every field from the document itself and return JSON matching the schema.
Rules:
- Copy names exactly as printed in the visual zone, keeping umlauts (Ä, Ö, Ü, ß) and accents.
- Convert every date to YYYY-MM-DD. German documents print dates as DD.MM.YYYY.
- "nationality" is the value as printed (e.g. DEUTSCH); "issuing_country" is the ISO alpha-3 code.
- Transcribe the machine readable zone (MRZ) lines character by character into "mrz_lines",
  keeping every '<'. Do not "correct" them.
- Use null for anything not visible or illegible. Never guess or invent values.
- Report glare, blur, cropping or other quality problems in "image_quality_issues", and any
  visible sign of manipulation in "tampering_indicators".
- If the images are not an identity document, set document_type to NOT_AN_ID_DOCUMENT.
{ocr_hint}"""

OCR_HINT = """
For reference, a separate OCR engine read the following text. It may contain errors; the image
is authoritative.
<ocr_text>
{text}
</ocr_text>
"""


@dataclass
class Image:
    content: bytes
    mime_type: str


class Extractor(Protocol):
    model: str | None

    def extract(self, images: list[Image], ocr_text: str) -> GeminiExtraction | None: ...


class GeminiExtractor:
    def __init__(self, settings: Settings) -> None:
        from google import genai

        self.model = settings.gemini_model
        self._client = genai.Client(vertexai=True, project=settings.project_id, location=settings.vertex_location)

    def extract(self, images: list[Image], ocr_text: str) -> GeminiExtraction | None:
        from google.genai import types

        hint = OCR_HINT.format(text=ocr_text[:8000]) if ocr_text.strip() else ""
        parts: list = [types.Part.from_bytes(data=img.content, mime_type=img.mime_type) for img in images]
        parts.append(PROMPT.format(ocr_hint=hint))
        response = self._client.models.generate_content(
            model=self.model,
            contents=parts,
            config=types.GenerateContentConfig(
                temperature=0,
                response_mime_type="application/json",
                response_schema=GeminiExtraction,
            ),
        )
        if response.parsed is not None:
            return response.parsed  # type: ignore[return-value]
        return GeminiExtraction.model_validate_json(response.text or "{}")


class NoExtractor:
    model = None

    def extract(self, images: list[Image], ocr_text: str) -> GeminiExtraction | None:
        return None


@lru_cache
def get_extractor(settings: Settings) -> Extractor:
    if settings.gemini_enabled and settings.project_id:
        return GeminiExtractor(settings)
    log.warning("Gemini disabled or no project configured: MRZ-only extraction")
    return NoExtractor()
