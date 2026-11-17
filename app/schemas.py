"""Pydantic models for the Gemini response schema and the public API response."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- Gemini output


class GeminiExtraction(BaseModel):
    """What Gemini is asked to read from the visual inspection zone (VIZ).

    All values are strings exactly as printed (dates normalised to YYYY-MM-DD) so that
    the pipeline, not the model, decides what is trustworthy.
    """

    document_type: Literal["PASSPORT", "ID_CARD", "RESIDENCE_PERMIT", "DRIVING_LICENCE", "OTHER", "NOT_AN_ID_DOCUMENT"] = Field(
        description="Kind of document shown.")
    issuing_country: str | None = Field(None, description="ISO 3166-1 alpha-3 code of the issuing state, e.g. DEU.")
    sides_present: list[Literal["FRONT", "BACK", "DATA_PAGE"]] = Field(default_factory=list)
    surname: str | None = Field(None, description="Name / Nachname, as printed incl. umlauts.")
    given_names: str | None = Field(None, description="Vornamen, as printed incl. umlauts.")
    birth_name: str | None = Field(None, description="Geburtsname, only if printed (e.g. 'geb. X').")
    religious_or_artist_name: str | None = Field(None, description="Ordens- oder Künstlername, if printed.")
    date_of_birth: str | None = Field(None, description="YYYY-MM-DD")
    place_of_birth: str | None = None
    nationality: str | None = Field(None, description="As printed, e.g. DEUTSCH.")
    sex: Literal["M", "F", "X"] | None = None
    document_number: str | None = None
    card_access_number: str | None = Field(None, description="6-digit CAN printed on German ID card front.")
    date_of_issue: str | None = Field(None, description="YYYY-MM-DD")
    date_of_expiry: str | None = Field(None, description="YYYY-MM-DD")
    issuing_authority: str | None = None
    address: str | None = Field(None, description="Anschrift (back of ID card), single line.")
    height_cm: int | None = None
    eye_colour: str | None = None
    mrz_lines: list[str] = Field(default_factory=list, description="MRZ lines transcribed exactly, '<' fillers kept.")
    image_quality_issues: list[str] = Field(default_factory=list, description="e.g. glare, blur, cropped, low resolution.")
    tampering_indicators: list[str] = Field(default_factory=list, description="Visible signs of manipulation, if any.")


# --------------------------------------------------------------------------- API output

Source = Literal["mrz", "visual", "mrz+visual"]


class FieldValue(BaseModel):
    value: str | int | None
    source: Source
    verified: bool = Field(description="True if backed by a valid MRZ checksum or MRZ/visual agreement.")


class Issue(BaseModel):
    code: str
    message: str
    severity: Literal["info", "warning", "error"] = "warning"


class DocumentInfo(BaseModel):
    type: str
    issuing_country: str | None
    label: str | None = Field(None, description="Human readable name, e.g. 'Personalausweis'.")


class ProcessingInfo(BaseModel):
    ocr_engine: str
    llm_model: str | None
    pages: int
    duration_ms: int


class ExtractionResult(BaseModel):
    request_id: str
    status: Literal["OK", "REVIEW", "FAILED"]
    document: DocumentInfo
    fields: dict[str, FieldValue]
    mrz: dict | None
    issues: list[Issue]
    processing: ProcessingInfo
