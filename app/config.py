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
    gemini_allowed_models: tuple[str, ...]
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


# Gemini models on Vertex AI that callers may pick per request. Override with
# GEMINI_ALLOWED_MODELS (comma-separated) when Google releases or retires models.
DEFAULT_ALLOWED_MODELS = (
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.6-flash",
    "gemini-3.7-flash",
    "gemini-3.8-flash",
    "gemini-3.1-pro-preview",
)


def _allowed_models(default_model: str) -> tuple[str, ...]:
    raw = os.getenv("GEMINI_ALLOWED_MODELS")
    models = [m.strip() for m in raw.split(",")] if raw else list(DEFAULT_ALLOWED_MODELS)
    models = [m for m in models if m]
    # The configured default is always selectable, and listed first.
    return tuple(dict.fromkeys([default_model, *models]))


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@lru_cache
def get_settings() -> Settings:
    gemini_model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
    return Settings(
        project_id=os.getenv("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT"),
        docai_location=os.getenv("DOCAI_LOCATION", "eu"),
        docai_processor_id=os.getenv("DOCAI_PROCESSOR_ID"),
        vertex_location=os.getenv("VERTEX_LOCATION", "global"),
        gemini_model=gemini_model,
        gemini_allowed_models=_allowed_models(gemini_model),
        gemini_enabled=_bool("GEMINI_ENABLED", True),
        output_bucket=os.getenv("OUTPUT_BUCKET"),
        delete_input_after_processing=_bool("DELETE_INPUT_AFTER_PROCESSING", True),
        max_upload_bytes=int(os.getenv("MAX_UPLOAD_BYTES", str(15 * 1024 * 1024))),
        max_files=int(os.getenv("MAX_FILES", "4")),
    )
