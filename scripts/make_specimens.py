"""Generate SYNTHETIC German-style ID card and passport images for testing.

The images are clearly marked "MUSTER / SPECIMEN", use fictional people, and only imitate
the layout loosely (field labels and MRZ); they are not reproductions of real documents.

    python scripts/make_specimens.py samples/
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.mrz import build_td1, build_td3, transliterate_de  # noqa: E402

FONT_DIRS = ["/usr/share/fonts/truetype/dejavu", "/Library/Fonts", "C:/Windows/Fonts"]


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    for d in FONT_DIRS:
        p = Path(d) / name
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default(size=size)


SANS, BOLD, MONO = "DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSansMono.ttf"

PEOPLE = [
    dict(surname="Schäfer", given="Jürgen Karl", birth="12.08.1983", place="KÖLN", sex="M",
         number="L01X00T47", can="481516", issued="01.07.2021", expiry="30.06.2031",
         authority="Stadt Köln", address="Musterstraße 12, 50667 Köln", height=181, eyes="BRAUN"),
    dict(surname="Weiß", given="Anna-Lena", birth="03.02.1999", place="MÜNCHEN", sex="F",
         number="C4J0R2KLM", can="230342", issued="15.03.2024", expiry="14.03.2034",
         authority="Landeshauptstadt München", address="Beispielweg 3a, 80331 München", height=168, eyes="GRÜN"),
]


def yymmdd(d: str) -> str:
    dd, mm, yyyy = d.split(".")
    return yyyy[2:] + mm + dd


def card(size=(1012, 638), color=(222, 232, 226)) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", size, color)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((4, 4, size[0] - 5, size[1] - 5), radius=36, outline=(150, 160, 155), width=3)
    return img, draw


def label_value(draw, x, y, label, value, size=26):
    draw.text((x, y), label, font=font(SANS, 15), fill=(70, 80, 90))
    draw.text((x, y + 18), value, font=font(BOLD, size), fill=(15, 15, 20))


def watermark(img: Image.Image) -> Image.Image:
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.text((img.size[0] * 0.18, img.size[1] * 0.38), "MUSTER · SPECIMEN", font=font(BOLD, 64), fill=(200, 40, 40, 90))
    return Image.alpha_composite(img.convert("RGBA"), layer.rotate(12)).convert("RGB")


def photo(draw, box):
    draw.rectangle(box, fill=(190, 196, 200), outline=(120, 120, 120))
    x0, y0, x1, y1 = box
    cx = (x0 + x1) // 2
    draw.ellipse((cx - 60, y0 + 40, cx + 60, y0 + 170), fill=(150, 156, 160))
    draw.rectangle((cx - 100, y0 + 190, cx + 100, y1), fill=(150, 156, 160))


def scanify(img: Image.Image, seed: int) -> Image.Image:
    rnd = random.Random(seed)
    img = img.rotate(rnd.uniform(-2.0, 2.0), expand=True, fillcolor=(245, 245, 245))
    return img.filter(ImageFilter.GaussianBlur(0.6))


def id_card(p: dict, out: Path, seed: int) -> None:
    front, d = card()
    d.text((40, 28), "BUNDESREPUBLIK DEUTSCHLAND", font=font(BOLD, 30), fill=(20, 20, 20))
    d.text((40, 66), "PERSONALAUSWEIS · IDENTITY CARD · CARTE D'IDENTITÉ", font=font(SANS, 16), fill=(60, 60, 60))
    d.text((760, 28), p["number"], font=font(MONO, 30), fill=(160, 20, 20))
    photo(d, (40, 110, 300, 450))
    label_value(d, 330, 110, "Name / Surname / Nom", p["surname"].upper())
    label_value(d, 330, 170, "Vornamen / Given names / Prénoms", p["given"])
    label_value(d, 330, 230, "Geburtstag / Date of birth", p["birth"])
    label_value(d, 640, 230, "Staatsangehörigkeit / Nationality", "DEUTSCH")
    label_value(d, 330, 290, "Geburtsort / Place of birth", p["place"])
    label_value(d, 330, 350, "Gültig bis / Date of expiry", p["expiry"])
    d.text((40, 560), "Unterschrift", font=font(SANS, 15), fill=(70, 80, 90))
    d.text((860, 560), p["can"], font=font(MONO, 30), fill=(20, 20, 20))
    scanify(watermark(front), seed).save(out / f"{seed:02d}_personalausweis_front.png")

    back, d = card(color=(228, 230, 236))
    label_value(d, 40, 30, "Anschrift / Address", p["address"], 24)
    label_value(d, 40, 100, "Größe / Height", f"{p['height']} cm", 24)
    label_value(d, 300, 100, "Augenfarbe / Colour of eyes", p["eyes"], 24)
    label_value(d, 40, 170, "Behörde / Authority", p["authority"], 24)
    label_value(d, 600, 170, "Datum / Date", p["issued"], 24)
    mrz = build_td1(p["number"], yymmdd(p["birth"]), "<", yymmdd(p["expiry"]),
                    transliterate_de(p["surname"]), transliterate_de(p["given"]))
    d.rectangle((0, 400, 1012, 638), fill=(240, 240, 240))
    for i, line in enumerate(mrz):
        d.text((48, 420 + i * 66), line, font=font(MONO, 50), fill=(10, 10, 10))
    scanify(watermark(back), seed + 1).save(out / f"{seed:02d}_personalausweis_back.png")


def passport(p: dict, out: Path, seed: int) -> None:
    page, d = card(size=(1250, 880), color=(236, 226, 232))
    d.text((40, 30), "REISEPASS · PASSPORT · PASSEPORT", font=font(BOLD, 28), fill=(20, 20, 20))
    d.text((40, 70), "BUNDESREPUBLIK DEUTSCHLAND · FEDERAL REPUBLIC OF GERMANY", font=font(SANS, 16), fill=(60, 60, 60))
    photo(d, (40, 120, 330, 520))
    label_value(d, 370, 110, "Typ / Type", "P")
    label_value(d, 520, 110, "Kode / Code", "D")
    label_value(d, 720, 110, "Pass-Nr. / Passport No.", p["number"])
    label_value(d, 370, 170, "1. Name / Surname", p["surname"].upper())
    label_value(d, 370, 230, "2. Vornamen / Given names", p["given"])
    label_value(d, 370, 290, "3. Staatsangehörigkeit / Nationality", "DEUTSCH")
    label_value(d, 370, 350, "4. Geburtstag / Date of birth", p["birth"])
    label_value(d, 760, 350, "5. Geschlecht / Sex", p["sex"])
    label_value(d, 370, 410, "6. Geburtsort / Place of birth", p["place"])
    label_value(d, 370, 470, "7. Ausstellungsdatum / Date of issue", p["issued"])
    label_value(d, 370, 530, "8. Gültig bis / Date of expiry", p["expiry"])
    label_value(d, 370, 590, "9. Behörde / Authority", p["authority"])
    mrz = build_td3(p["number"], yymmdd(p["birth"]), p["sex"], yymmdd(p["expiry"]),
                    transliterate_de(p["surname"]), transliterate_de(p["given"]))
    d.rectangle((0, 690, 1250, 880), fill=(244, 244, 244))
    for i, line in enumerate(mrz):
        d.text((30, 712 + i * 72), line, font=font(MONO, 40), fill=(10, 10, 10))
    scanify(watermark(page), seed).save(out / f"{seed:02d}_reisepass.png")


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "samples")
    out.mkdir(parents=True, exist_ok=True)
    id_card(PEOPLE[0], out, 1)
    passport(PEOPLE[1], out, 3)
    print(f"wrote synthetic specimens to {out}/")


if __name__ == "__main__":
    main()
