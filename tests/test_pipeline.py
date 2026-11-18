from datetime import date

from app.llm import Image
from app.mrz import build_td1, build_td3
from app.ocr import OcrResult
from app.pipeline import Pipeline
from app.schemas import GeminiExtraction

TODAY = date(2026, 11, 17)
ID_MRZ = build_td1("L01X00T47", "830812", "<", "310630", "SCHAEFER", "JUERGEN")
PASS_MRZ = build_td3("C4J0R2KLM", "900101", "M", "330101", "MUSTERMANN", "ERIKA")


class FakeOcr:
    name = "fake"

    def __init__(self, text: str) -> None:
        self.text = text

    def ocr(self, content: bytes, mime_type: str) -> OcrResult:
        return OcrResult(text=self.text, pages=1)


class FakeExtractor:
    model = "fake-gemini"

    def __init__(self, result: GeminiExtraction | None = None, error: Exception | None = None) -> None:
        self.result, self.error = result, error

    def extract(self, images, ocr_text, model=None):
        self.called_with_model = model
        if self.error:
            raise self.error
        return self.result


def viz(**overrides) -> GeminiExtraction:
    base = dict(
        document_type="ID_CARD", issuing_country="DEU", surname="Schäfer", given_names="Jürgen",
        date_of_birth="1983-08-12", place_of_birth="BERLIN", nationality="DEUTSCH",
        document_number="L01X00T47", card_access_number="123456", date_of_issue="2021-07-01",
        date_of_expiry="2031-06-30", issuing_authority="Stadt Musterstadt",
        address="Musterstraße 1, 12345 Musterstadt", height_cm=180, eye_colour="BRAUN",
    )
    base.update(overrides)
    return GeminiExtraction(**base)


IMG = [Image(content=b"x", mime_type="image/png")]


def run(ocr_text: str, extraction=None, error=None):
    return Pipeline(FakeOcr(ocr_text), FakeExtractor(extraction, error), today=TODAY).run(IMG)


def test_id_card_happy_path_keeps_umlauts_and_verifies():
    r = run("\n".join(["BUNDESREPUBLIK DEUTSCHLAND", *ID_MRZ]), viz())
    assert r.status == "OK", r.issues
    assert r.document.type == "ID_CARD" and r.document.label == "Personalausweis"
    assert r.fields["surname"].value == "Schäfer" and r.fields["surname"].source == "mrz+visual"
    assert r.fields["surname"].verified
    assert r.fields["date_of_birth"].value == "1983-08-12" and r.fields["date_of_birth"].verified
    assert r.fields["nationality"].value == "DEU" and r.fields["nationality"].verified
    assert r.fields["address"].source == "visual" and not r.fields["address"].verified
    assert r.mrz["valid"] and r.mrz["read_by"] == "documentai"
    assert r.processing.llm_model == "fake-gemini"


def test_passport():
    r = run("\n".join(PASS_MRZ), viz(document_type="PASSPORT", surname="Mustermann", given_names="Erika",
                                     date_of_birth="1990-01-01", document_number="C4J0R2KLM", sex="M",
                                     date_of_issue="2023-01-02", date_of_expiry="2033-01-01",
                                     card_access_number=None, address=None))
    assert r.status == "OK", r.issues
    assert r.document.label == "Reisepass"
    assert r.fields["sex"].value == "M" and r.fields["sex"].verified


def test_visual_mismatch_on_checksummed_field_prefers_mrz():
    r = run("\n".join(ID_MRZ), viz(date_of_birth="1983-08-13"))
    assert r.fields["date_of_birth"].value == "1983-08-12"
    assert r.fields["date_of_birth"].source == "mrz"
    assert any(i.code == "FIELD_MISMATCH" for i in r.issues)


def test_name_mismatch_needs_review():
    r = run("\n".join(ID_MRZ), viz(surname="Schneider"))
    assert r.status == "REVIEW"
    assert not r.fields["surname"].verified


def test_no_mrz_falls_back_to_visual_and_requests_review():
    r = run("Vorderseite ohne MRZ", viz(mrz_lines=[]))
    assert r.status == "REVIEW"
    assert any(i.code == "MRZ_NOT_FOUND" for i in r.issues)
    assert r.fields["surname"].source == "visual"


def test_gemini_mrz_used_when_documentai_mrz_is_unreadable():
    garbled = [ID_MRZ[0], "83O8X2<<<<<<<<<<<<<<<<<<<<<<<<", ID_MRZ[2]]
    r = run("\n".join(garbled), viz(mrz_lines=ID_MRZ))
    assert r.mrz["valid"] and r.mrz["read_by"] == "gemini"
    assert r.status == "OK", r.issues


def test_expired_document_is_flagged_but_ok():
    mrz = build_td1("L01X00T47", "830812", "<", "200630", "SCHAEFER", "JUERGEN")
    r = run("\n".join(mrz), viz(date_of_expiry="2020-06-30", date_of_issue="2010-07-01"))
    assert r.status == "OK"
    assert any(i.code == "DOCUMENT_EXPIRED" for i in r.issues)


def test_inconsistent_dates():
    r = run("\n".join(ID_MRZ), viz(date_of_issue="2032-01-01"))
    assert r.status == "REVIEW"
    assert any(i.code == "DATE_INCONSISTENT" for i in r.issues)


def test_unusual_validity_period_warning():
    r = run("\n".join(ID_MRZ), viz(date_of_issue="2024-06-30"))
    assert any(i.code == "VALIDITY_PERIOD_UNUSUAL" for i in r.issues)


def test_llm_failure_degrades_to_mrz_only():
    r = run("\n".join(ID_MRZ), error=RuntimeError("quota"))
    assert r.status == "REVIEW"
    assert r.fields["surname"].value == "SCHAEFER" and r.fields["surname"].source == "mrz"
    assert any(i.code == "LLM_UNAVAILABLE" for i in r.issues)


def test_not_an_id_document():
    r = run("Rechnung Nr. 4711", GeminiExtraction(document_type="NOT_AN_ID_DOCUMENT"))
    assert r.status == "FAILED"
    assert r.fields == {}


def test_foreign_document_is_best_effort():
    mrz = build_td3("X12345678", "900101", "F", "330101", "DUPONT", "MARIE", issuing="FRA", nationality="FRA")
    r = run("\n".join(mrz), None)
    assert any(i.code == "UNSUPPORTED_ISSUER" for i in r.issues)
    assert r.fields["surname"].value == "DUPONT"


def test_tampering_indicator_forces_review():
    r = run("\n".join(ID_MRZ), viz(tampering_indicators=["photo edges look pasted"]))
    assert r.status == "REVIEW"


def test_selected_model_is_passed_and_reported():
    extractor = FakeExtractor(viz())
    r = Pipeline(FakeOcr("\n".join(ID_MRZ)), extractor, today=TODAY).run(IMG, model="gemini-3.8-flash")
    assert extractor.called_with_model == "gemini-3.8-flash"
    assert r.processing.llm_model == "gemini-3.8-flash"


def test_no_llm_reports_no_model_even_if_one_was_requested():
    from app.llm import NoExtractor
    r = Pipeline(FakeOcr("\n".join(ID_MRZ)), NoExtractor(), today=TODAY).run(IMG, model="gemini-3.8-flash")
    assert r.processing.llm_model is None


def test_allowed_models_always_include_default(monkeypatch):
    from app.config import get_settings
    monkeypatch.setenv("GEMINI_MODEL", "my-tuned-model")
    monkeypatch.setenv("GEMINI_ALLOWED_MODELS", "gemini-3.5-flash,,my-tuned-model")
    get_settings.cache_clear()
    try:
        assert get_settings().gemini_allowed_models == ("my-tuned-model", "gemini-3.5-flash")
    finally:
        get_settings.cache_clear()
