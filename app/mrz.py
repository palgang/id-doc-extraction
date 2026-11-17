"""ICAO Doc 9303 Machine Readable Zone (MRZ) detection, parsing and validation.

Supports the three standard formats:

* TD1 - 3 lines x 30 chars (German Personalausweis since 2010, residence permits)
* TD2 - 2 lines x 36 chars (older ID cards, some visas)
* TD3 - 2 lines x 44 chars (passports, e.g. German Reisepass)

The parser is deliberately self-contained (no third-party MRZ library) so that the
checksum logic is easy to audit. OCR engines frequently confuse a handful of glyphs in
the OCR-B font (O/0, I/1, B/8, S/5, Z/2, G/6), so numeric fields are repaired with a
small substitution table and every repair is reported back to the caller.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

FILLER = "<"
_WEIGHTS = (7, 3, 1)
_VALID_CHARS = re.compile(r"^[A-Z0-9<]+$")

# Letters that OCR typically emits instead of digits (and vice versa) in OCR-B.
_TO_DIGIT = {"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "B": "8", "G": "6", "T": "7"}
_TO_ALPHA = {v: k for k, v in {"O": "0", "I": "1", "Z": "2", "S": "5", "B": "8", "G": "6"}.items()}

# German MRZ uses the single letter "D" (padded "D<<") instead of "DEU".
_COUNTRY_ALIASES = {"D": "DEU"}

FORMAT_LENGTHS = {"TD1": (3, 30), "TD2": (2, 36), "TD3": (2, 44)}


def char_value(c: str) -> int:
    if c.isdigit():
        return int(c)
    if "A" <= c <= "Z":
        return ord(c) - ord("A") + 10
    if c == FILLER:
        return 0
    raise ValueError(f"invalid MRZ character: {c!r}")


def check_digit(data: str) -> str:
    """ICAO 9303 check digit (weights 7-3-1, modulo 10)."""
    return str(sum(char_value(c) * _WEIGHTS[i % 3] for i, c in enumerate(data)) % 10)


def normalize_country(code: str) -> str:
    code = code.replace(FILLER, "")
    return _COUNTRY_ALIASES.get(code, code)


def transliterate_de(text: str) -> str:
    """Transliterate a German visual-zone name to MRZ form (ICAO 9303 part 3)."""
    table = {
        "Ä": "AE", "Ö": "OE", "Ü": "UE", "ß": "SS", "ä": "AE", "ö": "OE", "ü": "UE",
        "É": "E", "È": "E", "Ê": "E", "Á": "A", "À": "A", "Â": "A", "Ç": "C", "Ñ": "N",
        "Í": "I", "Ó": "O", "Ú": "U", "Ë": "E", "Ï": "I",
        "é": "E", "è": "E", "ê": "E", "ë": "E", "á": "A", "à": "A", "â": "A", "ç": "C", "ñ": "N",
        "í": "I", "ï": "I", "ó": "O", "ú": "U",
    }
    out = "".join(table.get(ch, ch) for ch in text).upper()
    out = re.sub(r"[^A-Z]+", " ", out)
    return " ".join(out.split())


@dataclass
class CheckResult:
    field: str
    expected: str
    actual: str

    @property
    def valid(self) -> bool:
        return self.expected == self.actual


@dataclass
class MRZResult:
    format: str
    lines: list[str]
    document_code: str
    issuing_state: str
    document_number: str
    surname: str
    given_names: str
    nationality: str
    birth_date: date | None
    sex: str | None
    expiry_date: date | None
    optional_data: str
    checks: list[CheckResult] = field(default_factory=list)
    corrections: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return bool(self.checks) and all(c.valid for c in self.checks)

    @property
    def document_type(self) -> str:
        code = self.document_code[:1]
        return {"P": "PASSPORT", "I": "ID_CARD", "A": "ID_CARD", "C": "ID_CARD", "V": "VISA"}.get(code, "UNKNOWN")

    def to_dict(self) -> dict:
        return {
            "format": self.format,
            "lines": self.lines,
            "valid": self.valid,
            "document_code": self.document_code,
            "issuing_state": self.issuing_state,
            "document_number": self.document_number,
            "surname": self.surname,
            "given_names": self.given_names,
            "nationality": self.nationality,
            "birth_date": self.birth_date.isoformat() if self.birth_date else None,
            "sex": self.sex,
            "expiry_date": self.expiry_date.isoformat() if self.expiry_date else None,
            "optional_data": self.optional_data,
            "checks": [{"field": c.field, "valid": c.valid} for c in self.checks],
            "corrections": self.corrections,
        }


class MRZError(ValueError):
    pass


# --------------------------------------------------------------------------- helpers


def _clean_line(line: str) -> str:
    line = line.strip().upper()
    for bad in ("«", "‹", "＜", "≪", "〈", "(", "["):
        line = line.replace(bad, FILLER)
    return re.sub(r"\s+", "", line)


def _parse_date(yymmdd: str, *, is_expiry: bool, today: date | None = None) -> date | None:
    if not yymmdd.isdigit():
        return None
    today = today or date.today()
    yy, mm, dd = int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:])
    if is_expiry:
        year = 2000 + yy
    else:
        year = 2000 + yy if 2000 + yy <= today.year else 1900 + yy
    try:
        return date(year, mm, dd)
    except ValueError:
        return None


def _parse_name(field_: str) -> tuple[str, str]:
    field_ = field_.rstrip(FILLER)
    surname, _, given = field_.partition("<<")
    clean = lambda s: " ".join(s.replace(FILLER, " ").split())  # noqa: E731
    return clean(surname), clean(given)


def _repair_numeric(value: str) -> str:
    return "".join(_TO_DIGIT.get(c, c) for c in value)


def _repair_alpha(value: str) -> str:
    return "".join(_TO_ALPHA.get(c, c) for c in value)


class _Builder:
    """Collects check results and corrections while slicing a raw MRZ."""

    def __init__(self) -> None:
        self.checks: list[CheckResult] = []
        self.corrections: list[str] = []

    def numeric(self, name: str, value: str) -> str:
        fixed = _repair_numeric(value)
        if fixed != value:
            self.corrections.append(f"{name}: {value!r} -> {fixed!r}")
        return fixed

    def alpha(self, name: str, value: str) -> str:
        fixed = _repair_alpha(value)
        if fixed != value:
            self.corrections.append(f"{name}: {value!r} -> {fixed!r}")
        return fixed

    def check(self, name: str, data: str, digit: str) -> str:
        digit = self.numeric(f"{name}_check", digit)
        # A filler check digit is allowed for empty optional fields (ICAO 9303 §4.9).
        expected = check_digit(data)
        actual = "0" if digit == FILLER and data.strip(FILLER) == "" else digit
        self.checks.append(CheckResult(name, expected, actual))
        return digit

    def document_number(self, value: str, digit: str) -> tuple[str, str]:
        """German document numbers are alphanumeric, so OCR repair is ambiguous.

        Try the raw value first; if the checksum fails, try single-character
        letter/digit swaps and keep the first one that validates.
        """
        digit = _repair_numeric(digit)
        if check_digit(value) == digit:
            return value, digit
        swaps = {**_TO_DIGIT, **_TO_ALPHA}
        for i, ch in enumerate(value):
            alt = swaps.get(ch)
            if alt is None:
                continue
            candidate = value[:i] + alt + value[i + 1:]
            if check_digit(candidate) == digit:
                self.corrections.append(f"document_number: {value!r} -> {candidate!r}")
                return candidate, digit
        return value, digit


# --------------------------------------------------------------------------- parsers


def _parse_td1(lines: list[str], today: date | None) -> MRZResult:
    l1, l2, l3 = lines
    b = _Builder()
    doc_code = l1[0:2]
    issuing = b.alpha("issuing_state", l1[2:5])
    raw_num, num_cd = l1[5:14], l1[14]
    positions = list(range(5, 14))
    optional1 = l1[15:30]
    # Long document numbers (> 9 chars) overflow into the optional field (ICAO 9303-5 §4.2.4).
    if num_cd == FILLER and optional1.strip(FILLER):
        ext = optional1.split(FILLER, 1)[0]
        raw_num, num_cd = raw_num + ext[:-1], ext[-1]
        positions += list(range(15, 15 + len(ext) - 1))
    doc_num, num_cd = b.document_number(raw_num, num_cd)
    b.check("document_number", doc_num, num_cd)
    fixed_l1 = list(l1)
    for pos, ch in zip(positions, doc_num):
        fixed_l1[pos] = ch
    if len(positions) == 9:
        fixed_l1[14] = num_cd
    else:
        fixed_l1[15 + len(doc_num) - 9] = num_cd

    dob = b.numeric("birth_date", l2[0:6])
    dob_cd = b.check("birth_date", dob, l2[6])
    sex = l2[7]
    exp = b.numeric("expiry_date", l2[8:14])
    exp_cd = b.check("expiry_date", exp, l2[14])
    nationality = b.alpha("nationality", l2[15:18])
    optional2 = l2[18:29]
    composite = "".join(fixed_l1)[5:30] + dob + dob_cd + exp + exp_cd + optional2
    b.check("composite", composite, l2[29])

    surname, given = _parse_name(l3)
    return MRZResult(
        format="TD1", lines=lines, document_code=doc_code.rstrip(FILLER),
        issuing_state=normalize_country(issuing), document_number=doc_num.rstrip(FILLER),
        surname=surname, given_names=given, nationality=normalize_country(nationality),
        birth_date=_parse_date(dob, is_expiry=False, today=today),
        sex=sex if sex in ("M", "F", "X") else None,
        expiry_date=_parse_date(exp, is_expiry=True, today=today),
        optional_data=(optional1 + optional2).strip(FILLER),
        checks=b.checks, corrections=b.corrections,
    )


def _parse_two_line(lines: list[str], fmt: str, today: date | None) -> MRZResult:
    l1, l2 = lines
    b = _Builder()
    doc_code = l1[0:2]
    issuing = b.alpha("issuing_state", l1[2:5])
    surname, given = _parse_name(l1[5:])

    doc_num, num_cd = b.document_number(l2[0:9], l2[9])
    b.check("document_number", doc_num, num_cd)
    nationality = b.alpha("nationality", l2[10:13])
    dob = b.numeric("birth_date", l2[13:19])
    dob_cd = b.check("birth_date", dob, l2[19])
    sex = l2[20]
    exp = b.numeric("expiry_date", l2[21:27])
    exp_cd = b.check("expiry_date", exp, l2[27])

    if fmt == "TD3":
        optional = l2[28:42]
        opt_cd = b.check("personal_number", optional, l2[42])
        composite = doc_num + num_cd + dob + dob_cd + exp + exp_cd + optional + opt_cd
        b.check("composite", composite, l2[43])
    else:  # TD2
        optional = l2[28:35]
        composite = doc_num + num_cd + dob + dob_cd + exp + exp_cd + optional
        b.check("composite", composite, l2[35])

    return MRZResult(
        format=fmt, lines=lines, document_code=doc_code.rstrip(FILLER),
        issuing_state=normalize_country(issuing), document_number=doc_num.rstrip(FILLER),
        surname=surname, given_names=given, nationality=normalize_country(nationality),
        birth_date=_parse_date(dob, is_expiry=False, today=today),
        sex=sex if sex in ("M", "F", "X") else None,
        expiry_date=_parse_date(exp, is_expiry=True, today=today),
        optional_data=optional.strip(FILLER),
        checks=b.checks, corrections=b.corrections,
    )


def parse_mrz(lines: Iterable[str], today: date | None = None) -> MRZResult:
    """Parse already-segmented MRZ lines. Raises MRZError if the shape is wrong."""
    lines = [_clean_line(line) for line in lines if line.strip()]
    for fmt, (count, length) in FORMAT_LENGTHS.items():
        if len(lines) == count and all(len(line) == length for line in lines):
            if not all(_VALID_CHARS.match(line) for line in lines):
                raise MRZError("MRZ contains invalid characters")
            if fmt == "TD1":
                return _parse_td1(lines, today)
            return _parse_two_line(lines, fmt, today)
    raise MRZError(f"unrecognised MRZ shape: {[len(line) for line in lines]}")


def _fit_length(line: str, length: int) -> str | None:
    """OCR often drops or adds trailing fillers; pad/trim if the line is close."""
    if len(line) == length:
        return line
    if length - 3 <= len(line) < length:
        return line + FILLER * (length - len(line))
    if length < len(line) <= length + 3 and line[length:].strip(FILLER) == "":
        return line[:length]
    return None


def find_mrz_candidates(text: str) -> list[list[str]]:
    """Find runs of MRZ-looking lines in free OCR text, best candidates first."""
    raw = [_clean_line(line) for line in text.splitlines()]
    looks_mrz = [len(line) >= 25 and FILLER in line and _VALID_CHARS.match(line) is not None for line in raw]

    candidates: list[list[str]] = []
    for fmt, (count, length) in FORMAT_LENGTHS.items():
        for i in range(len(raw) - count + 1):
            window = raw[i:i + count]
            if not all(looks_mrz[i:i + count]):
                continue
            fitted = [_fit_length(line, length) for line in window]
            if all(fitted):
                candidates.append(fitted)  # type: ignore[arg-type]
    # Also handle OCR that glued all MRZ lines into one long line.
    for line, ok in zip(raw, looks_mrz):
        if not ok:
            continue
        for fmt, (count, length) in FORMAT_LENGTHS.items():
            if len(line) == count * length:
                candidates.append([line[k * length:(k + 1) * length] for k in range(count)])
    return candidates


def extract_mrz(text: str, today: date | None = None) -> MRZResult | None:
    """Return the best MRZ found in OCR text: a fully valid one if any, else the
    one with the most passing checks."""
    best: MRZResult | None = None
    for cand in find_mrz_candidates(text):
        try:
            result = parse_mrz(cand, today=today)
        except MRZError:
            continue
        if result.valid:
            return result
        if best is None or sum(c.valid for c in result.checks) > sum(c.valid for c in best.checks):
            best = result
    return best


def build_td1(doc_number: str, birth: str, sex: str, expiry: str, surname: str, given: str,
              issuing: str = "D<<", nationality: str = "D<<", doc_code: str = "ID") -> list[str]:
    """Build a valid TD1 MRZ. Used for synthetic test data only."""
    num = doc_number.ljust(9, FILLER)
    l1 = (doc_code + issuing + num + check_digit(num)).ljust(30, FILLER)
    l2 = birth + check_digit(birth) + sex + expiry + check_digit(expiry) + nationality
    l2 = l2.ljust(29, FILLER)
    l2 += check_digit(l1[5:30] + l2[0:7] + l2[8:15] + l2[18:29])
    name = (surname.replace(" ", FILLER) + "<<" + given.replace(" ", FILLER))[:30].ljust(30, FILLER)
    return [l1, l2, name]


def build_td3(doc_number: str, birth: str, sex: str, expiry: str, surname: str, given: str,
              issuing: str = "D<<", nationality: str = "D<<", personal: str = "") -> list[str]:
    """Build a valid TD3 MRZ. Used for synthetic test data only."""
    l1 = ("P<" + issuing + surname.replace(" ", FILLER) + "<<" + given.replace(" ", FILLER))[:44].ljust(44, FILLER)
    num = doc_number.ljust(9, FILLER)
    opt = personal.ljust(14, FILLER)
    opt_cd = check_digit(opt) if personal else FILLER
    body = num + check_digit(num) + nationality + birth + check_digit(birth) + sex + expiry + check_digit(expiry) + opt + opt_cd
    composite_src = body[0:10] + body[13:20] + body[21:43]
    return [l1, body + check_digit(composite_src)]
