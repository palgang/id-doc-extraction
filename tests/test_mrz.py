from datetime import date

import pytest

from app.mrz import (
    MRZError, build_td1, build_td3, check_digit, extract_mrz, parse_mrz, transliterate_de,
)

TODAY = date(2026, 11, 17)

# Published German specimen MRZs ("Erika Mustermann"), not real personal data.
SPECIMEN_ID_2010 = [
    "IDD<<T220001293<<<<<<<<<<<<<<<",
    "6408125<2010315D<<<<<<<<<<<<<4",
    "MUSTERMANN<<ERIKA<<<<<<<<<<<<<",
]
SPECIMEN_PASSPORT = [
    "P<D<<MUSTERMANN<<ERIKA<<<<<<<<<<<<<<<<<<<<<<",
    "C01X00T478D<<6408125F2702283<<<<<<<<<<<<<<<4",
]


def test_check_digit_icao_example():
    # ICAO 9303 part 3 worked example.
    assert check_digit("520727") == "3"
    assert check_digit("L898902C<") == "3"
    assert check_digit("<<<<<<<<<<<<<<") == "0"


def test_german_id_card_specimen():
    r = parse_mrz(SPECIMEN_ID_2010, today=TODAY)
    assert r.valid and r.format == "TD1"
    assert r.document_type == "ID_CARD"
    assert r.issuing_state == "DEU" and r.nationality == "DEU"
    assert r.document_number == "T22000129"
    assert (r.surname, r.given_names) == ("MUSTERMANN", "ERIKA")
    assert r.birth_date == date(1964, 8, 12)
    assert r.expiry_date == date(2020, 10, 31)
    assert r.sex is None  # older German ID cards leave sex empty


def test_german_passport_specimen():
    r = parse_mrz(SPECIMEN_PASSPORT, today=TODAY)
    assert r.valid and r.format == "TD3" and r.document_type == "PASSPORT"
    assert r.document_number == "C01X00T47"
    assert r.sex == "F"
    assert r.expiry_date == date(2027, 2, 28)


def test_builders_roundtrip():
    td1 = build_td1("LZ6311T47", "950229", "M", "320101", "MUELLER", "JAN")  # 29 Feb 1995 is invalid
    r = parse_mrz(td1, today=TODAY)
    assert r.valid and r.birth_date is None  # check digits fine, but date impossible
    td3 = build_td3("C4J0R2KLM", "900101", "M", "330101", "SCHAEFER", "JOHANNES", personal="")
    r = parse_mrz(td3, today=TODAY)
    assert r.valid and r.given_names == "JOHANNES"


def test_birth_century_and_expiry_century():
    td1 = build_td1("L01X00T47", "080305", "F", "300101", "A", "B")
    assert parse_mrz(td1, today=TODAY).birth_date == date(2008, 3, 5)
    td1 = build_td1("L01X00T47", "380305", "F", "300101", "A", "B")
    assert parse_mrz(td1, today=TODAY).birth_date == date(1938, 3, 5)


def test_tampered_date_fails_checksum():
    lines = list(SPECIMEN_ID_2010)
    lines[1] = "6408135" + lines[1][7:]  # 12 -> 13 August
    r = parse_mrz(lines, today=TODAY)
    assert not r.valid
    failed = {c.field for c in r.checks if not c.valid}
    assert "birth_date" in failed and "composite" in failed


def test_ocr_confusions_in_numeric_fields_are_repaired():
    lines = list(SPECIMEN_ID_2010)
    lines[1] = "64O8l25<2Ol03lSD<<<<<<<<<<<<<4".upper()  # O/0, L/1, S/5 confusions
    r = parse_mrz(lines, today=TODAY)
    assert r.valid
    assert r.birth_date == date(1964, 8, 12)
    assert r.corrections


def test_ocr_confusion_in_alphanumeric_document_number():
    lines = list(SPECIMEN_PASSPORT)
    lines[1] = "CO1X00T478" + SPECIMEN_PASSPORT[1][10:]  # letter O instead of digit 0
    r = parse_mrz(lines, today=TODAY)
    assert r.valid
    assert r.document_number == "C01X00T47"


def test_extract_from_noisy_ocr_text():
    text = "\n".join([
        "BUNDESREPUBLIK DEUTSCHLAND",
        "Anschrift / Address",
        "12345 MUSTERSTADT",
        "IDD<<T220001293<<<<<<<<<<<<",  # OCR dropped trailing fillers
        "6408125 < 2010315D<<<<<<<<<<<<<4",  # OCR inserted spaces
        "MUSTERMANN<<ERIKA«<<<<<<<<<<<<",  # guillemet instead of filler
    ])
    r = extract_mrz(text, today=TODAY)
    assert r is not None and r.valid and r.surname == "MUSTERMANN"


def test_extract_glued_lines():
    r = extract_mrz("".join(SPECIMEN_PASSPORT), today=TODAY)
    assert r is not None and r.valid and r.format == "TD3"


def test_extract_returns_none_without_mrz():
    assert extract_mrz("REISEPASS\nPASSPORT\nName MUSTERMANN", today=TODAY) is None


def test_bad_shape_raises():
    with pytest.raises(MRZError):
        parse_mrz(["P<D<<SHORT", "123"])


def test_transliteration():
    assert transliterate_de("Schäfer-Müller") == "SCHAEFER MUELLER"
    assert transliterate_de("Groß") == "GROSS"
    assert transliterate_de("Zoë") == "ZOE"
