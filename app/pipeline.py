"""Extraction pipeline: Document AI OCR -> MRZ parse -> Gemini VIZ read -> reconcile -> validate."""

from __future__ import annotations

import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from .llm import Extractor, Image
from .mrz import MRZError, MRZResult, extract_mrz, parse_mrz, transliterate_de
from .ocr import OcrEngine
from .schemas import DocumentInfo, ExtractionResult, FieldValue, GeminiExtraction, Issue, ProcessingInfo

log = logging.getLogger(__name__)

SUPPORTED_MIME_TYPES = {
    "application/pdf", "image/jpeg", "image/png", "image/tiff", "image/webp", "image/gif", "image/bmp",
}

_NATIONALITY_WORDS = {"DEUTSCH": "DEU", "DEUTSCHE": "DEU", "GERMAN": "DEU", "D": "DEU"}

_LABELS = {
    ("ID_CARD", "DEU"): "Personalausweis",
    ("PASSPORT", "DEU"): "Reisepass",
    ("RESIDENCE_PERMIT", "DEU"): "Aufenthaltstitel",
}


class Pipeline:
    def __init__(self, ocr: OcrEngine, extractor: Extractor, today: date | None = None) -> None:
        self.ocr = ocr
        self.extractor = extractor
        self._today = today

    @property
    def today(self) -> date:
        return self._today or date.today()

    def run(self, images: list[Image], request_id: str | None = None, model: str | None = None) -> ExtractionResult:
        """Extract one document. ``model`` picks the Gemini model (already validated by the caller)."""
        started = time.monotonic()
        request_id = request_id or uuid.uuid4().hex
        issues: list[Issue] = []

        # 1. OCR every page/side in parallel.
        with ThreadPoolExecutor(max_workers=max(1, len(images))) as pool:
            ocr_results = list(pool.map(lambda img: self.ocr.ocr(img.content, img.mime_type), images))
        ocr_text = "\n".join(r.text for r in ocr_results)
        pages = sum(r.pages for r in ocr_results)

        # 2. MRZ from Document AI text.
        mrz = extract_mrz(ocr_text, today=self.today) if ocr_text else None

        # 3. Gemini reads the visual zone (and transcribes the MRZ as a second opinion).
        viz: GeminiExtraction | None = None
        try:
            viz = self.extractor.extract(images, ocr_text, model=model)
        except Exception as exc:  # noqa: BLE001 - degrade to MRZ-only, never fail the request
            log.exception("gemini extraction failed request_id=%s", request_id)
            issues.append(Issue(code="LLM_UNAVAILABLE", message=f"Visual-zone extraction failed: {type(exc).__name__}", severity="error"))

        mrz_source = "documentai" if mrz else None
        if viz and viz.mrz_lines and (mrz is None or not mrz.valid):
            alt = _parse_lines(viz.mrz_lines, self.today)
            if alt and (mrz is None or alt.valid or _score(alt) > _score(mrz)):
                mrz, mrz_source = alt, "gemini"

        result = self._reconcile(request_id, mrz, viz, issues)
        if result.mrz is not None:
            result.mrz["read_by"] = mrz_source
        result.processing = ProcessingInfo(
            ocr_engine=self.ocr.name,
            llm_model=(model or self.extractor.model) if self.extractor.model else None,
            pages=pages,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        # Never log field values: they are personal data.
        log.info("extraction request_id=%s status=%s issues=%s", request_id, result.status, [i.code for i in result.issues])
        return result

    # ------------------------------------------------------------------ reconciliation

    def _reconcile(self, request_id: str, mrz: MRZResult | None, viz: GeminiExtraction | None,
                   issues: list[Issue]) -> ExtractionResult:
        fields: dict[str, FieldValue] = {}
        check_ok = {c.field: c.valid for c in mrz.checks} if mrz else {}

        if viz and viz.document_type == "NOT_AN_ID_DOCUMENT" and not mrz:
            issues.append(Issue(code="NOT_AN_ID_DOCUMENT", message="The upload does not look like an identity document.", severity="error"))
            return _result(request_id, "FAILED", DocumentInfo(type="UNKNOWN", issuing_country=None), {}, None, issues)

        if mrz is None:
            issues.append(Issue(code="MRZ_NOT_FOUND", message="No machine readable zone found; values are unverified. For ID cards, upload the back side too.", severity="error"))
        else:
            failed = [c.field for c in mrz.checks if not c.valid]
            if failed:
                issues.append(Issue(code="MRZ_CHECKSUM_FAILED", message=f"MRZ check digits failed for: {', '.join(failed)}", severity="error"))
            if mrz.corrections:
                issues.append(Issue(code="MRZ_OCR_CORRECTED", message=f"{len(mrz.corrections)} OCR confusion(s) in the MRZ were repaired.", severity="info"))

        # Names: MRZ is transliterated and possibly truncated; the VIZ keeps umlauts.
        mrz_names_ok = bool(mrz) and mrz.valid
        for key, mrz_val, viz_val in (
            ("surname", mrz.surname if mrz else None, viz.surname if viz else None),
            ("given_names", mrz.given_names if mrz else None, viz.given_names if viz else None),
        ):
            fields[key] = _merge_name(key, mrz_val, viz_val, mrz_names_ok, issues)

        # Fields that are protected by an MRZ check digit.
        fields["date_of_birth"] = _merge(
            "date_of_birth", mrz.birth_date.isoformat() if mrz and mrz.birth_date else None,
            check_ok.get("birth_date", False), viz.date_of_birth if viz else None, issues)
        fields["date_of_expiry"] = _merge(
            "date_of_expiry", mrz.expiry_date.isoformat() if mrz and mrz.expiry_date else None,
            check_ok.get("expiry_date", False), viz.date_of_expiry if viz else None, issues)
        fields["document_number"] = _merge(
            "document_number", mrz.document_number if mrz else None,
            check_ok.get("document_number", False), _clean_docnum(viz.document_number) if viz else None, issues)
        # Nationality and sex are not covered by any check digit, so only agreement verifies them.
        fields["nationality"] = _merge(
            "nationality", mrz.nationality if mrz else None, False,
            _nationality_code(viz.nationality) if viz else None, issues)
        sex_mrz = mrz.sex if mrz else None
        fields["sex"] = _merge("sex", sex_mrz, False, viz.sex if viz else None, issues)

        # Visual-only fields.
        if viz:
            for key in ("birth_name", "religious_or_artist_name", "place_of_birth", "date_of_issue",
                        "issuing_authority", "address", "height_cm", "eye_colour", "card_access_number"):
                value = getattr(viz, key)
                if value not in (None, ""):
                    fields[key] = FieldValue(value=value, source="visual", verified=False)
            for problem in viz.image_quality_issues:
                issues.append(Issue(code="IMAGE_QUALITY", message=problem, severity="warning"))
            for indicator in viz.tampering_indicators:
                issues.append(Issue(code="POSSIBLE_TAMPERING", message=indicator, severity="error"))

        fields = {k: v for k, v in fields.items() if v.value is not None}

        doc_type = mrz.document_type if mrz else (viz.document_type if viz else "UNKNOWN")
        if mrz and mrz.document_code.startswith(("AR", "IR", "AT")):  # German residence permit codes
            doc_type = "RESIDENCE_PERMIT"
        country = mrz.issuing_state if mrz else (viz.issuing_country.upper() if viz and viz.issuing_country else None)
        document = DocumentInfo(type=doc_type, issuing_country=country, label=_LABELS.get((doc_type, country or "")))

        self._validate(fields, document, issues)

        if not fields:
            status = "FAILED"
        elif any(i.severity == "error" for i in issues):
            status = "REVIEW"
        else:
            status = "OK"
        return _result(request_id, status, document, fields, mrz.to_dict() if mrz else None, issues)

    def _validate(self, fields: dict[str, FieldValue], document: DocumentInfo, issues: list[Issue]) -> None:
        today = self.today
        dob = _date(fields.get("date_of_birth"))
        expiry = _date(fields.get("date_of_expiry"))
        issued = _date(fields.get("date_of_issue"))

        if document.issuing_country and document.issuing_country != "DEU":
            issues.append(Issue(code="UNSUPPORTED_ISSUER", message=f"Issuer {document.issuing_country} is outside the German MVP scope; results are best effort.", severity="warning"))
        if expiry and expiry < today:
            issues.append(Issue(code="DOCUMENT_EXPIRED", message=f"Document expired on {expiry.isoformat()}.", severity="warning"))
        if dob and dob > today:
            issues.append(Issue(code="DATE_INCONSISTENT", message="Date of birth is in the future.", severity="error"))
        if issued and expiry and issued >= expiry:
            issues.append(Issue(code="DATE_INCONSISTENT", message="Date of issue is not before date of expiry.", severity="error"))
        if dob and issued and issued < dob:
            issues.append(Issue(code="DATE_INCONSISTENT", message="Date of issue is before date of birth.", severity="error"))
        # German ID cards and passports are valid for 10 years (6 if the holder was under 24 at issue).
        if document.issuing_country == "DEU" and issued and expiry and document.type in ("ID_CARD", "PASSPORT"):
            years = round((expiry - issued).days / 365.25)
            if years not in (6, 10):
                issues.append(Issue(code="VALIDITY_PERIOD_UNUSUAL", message=f"Validity period of about {years} years is unusual for German {document.label}.", severity="warning"))
        can = fields.get("card_access_number")
        if can and not (isinstance(can.value, str) and can.value.isdigit() and len(can.value) == 6):
            issues.append(Issue(code="CAN_FORMAT", message="Card access number should be 6 digits.", severity="warning"))


# --------------------------------------------------------------------------- helpers


def _result(request_id: str, status: str, document: DocumentInfo, fields: dict, mrz: dict | None,
            issues: list[Issue]) -> ExtractionResult:
    return ExtractionResult(
        request_id=request_id, status=status, document=document, fields=fields, mrz=mrz, issues=issues,
        processing=ProcessingInfo(ocr_engine="", llm_model=None, pages=0, duration_ms=0),
    )


def _score(m: MRZResult) -> int:
    return sum(c.valid for c in m.checks)


def _parse_lines(lines: list[str], today: date) -> MRZResult | None:
    try:
        return parse_mrz(lines, today=today)
    except MRZError:
        return extract_mrz("\n".join(lines), today=today)


def _norm(value: str) -> str:
    return transliterate_de(value).replace(" ", "")


def _merge_name(key: str, mrz_val: str | None, viz_val: str | None, mrz_valid: bool,
                issues: list[Issue]) -> FieldValue:
    if viz_val and mrz_val:
        v, m = _norm(viz_val), _norm(mrz_val)
        # TD1 names are truncated to 30 characters, so the MRZ may be a prefix of the VIZ.
        if v == m or (len(m) >= 3 and v.startswith(m)):
            return FieldValue(value=viz_val, source="mrz+visual", verified=mrz_valid)
        issues.append(Issue(code="FIELD_MISMATCH", message=f"{key}: visual zone and MRZ disagree.", severity="error"))
        return FieldValue(value=viz_val, source="visual", verified=False)
    if mrz_val:
        return FieldValue(value=mrz_val, source="mrz", verified=mrz_valid)
    return FieldValue(value=viz_val, source="visual", verified=False)


def _merge(key: str, mrz_val: str | None, mrz_ok: bool, viz_val: str | None, issues: list[Issue]) -> FieldValue:
    if mrz_val and viz_val:
        if mrz_val == viz_val:
            return FieldValue(value=mrz_val, source="mrz+visual", verified=True)
        if mrz_ok:
            issues.append(Issue(code="FIELD_MISMATCH", message=f"{key}: visual zone disagrees with checksum-verified MRZ; MRZ value used.", severity="warning"))
            return FieldValue(value=mrz_val, source="mrz", verified=True)
        issues.append(Issue(code="FIELD_MISMATCH", message=f"{key}: visual zone and MRZ disagree.", severity="error"))
        return FieldValue(value=viz_val, source="visual", verified=False)
    if mrz_val:
        return FieldValue(value=mrz_val, source="mrz", verified=mrz_ok)
    return FieldValue(value=viz_val, source="visual", verified=False)


def _clean_docnum(value: str | None) -> str | None:
    return value.replace(" ", "").upper() if value else None


def _nationality_code(value: str | None) -> str | None:
    if not value:
        return None
    upper = value.strip().upper()
    return _NATIONALITY_WORDS.get(upper, upper if len(upper) == 3 else None)


def _date(field: FieldValue | None) -> date | None:
    if not field or not isinstance(field.value, str):
        return None
    try:
        return date.fromisoformat(field.value)
    except ValueError:
        return None
