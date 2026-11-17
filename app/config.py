"""Runtime configuration, read from environment variables (12-factor style)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    project_id: str | None
    # Document AI
    docai_location: str
    docai_processor_id: str | None
    # Gemini on Vertex AI
    vertex_location: str
    gemini_model: str
    gemini_enabled: bool
    # Storage (event-driven mode)
    output_bucket: str | None
    delete_input_after_processing: bool
    # Limits
    max_upload_bytes: int
    max_files: int

    @property
    def docai_enabled(self) -> bool:
        return bool(self.project_id and self.docai_processor_id)


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@lru_cache
def get_settings() -> Settings:
    return Settings(
        project_id=os.getenv("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT"),
        docai_location=os.getenv("DOCAI_LOCATION", "eu"),
        docai_processor_id=os.getenv("DOCAI_PROCESSOR_ID"),
        vertex_location=os.getenv("VERTEX_LOCATION", "global"),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash"),
        gemini_enabled=_bool("GEMINI_ENABLED", True),
        output_bucket=os.getenv("OUTPUT_BUCKET"),
        delete_input_after_processing=_bool("DELETE_INPUT_AFTER_PROCESSING", True),
        max_upload_bytes=int(os.getenv("MAX_UPLOAD_BYTES", str(15 * 1024 * 1024))),
        max_files=int(os.getenv("MAX_FILES", "4")),
    )
