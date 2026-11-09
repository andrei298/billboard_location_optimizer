"""Teste automate pe imagini sintetice: valori calculate + direcția schimbării scorurilor.

    cd scoring
    python -m analiza_panou.test_analiza          # rulează toate testele și afișează valorile
    python -m pytest analiza_panou/test_analiza.py  # (dacă pytest e instalat)

Imaginile sunt generate determinist (seed fix). Reclamele reale se verifică separat, calitativ,
cu `python -m analiza_panou.verificare_reale <folder>`.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import config as C
from .analiza import Rezultat, analizeaza, scor_general
from .criterii import Criteriu

W, H = 1500, 500
DIR = Path(tempfile.gettempdir()) / "analiza_panou_teste"
_cache: dict[str, Rezultat] = {}


def _font(marime: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    for nume in (("arialbd.ttf", "DejaVuSans-Bold.ttf") if bold else ("arial.ttf", "DejaVuSans.ttf")):
        try:
            return ImageFont.truetype(nume, marime)
        except OSError:
            pass
    return ImageFont.load_default(marime)


def _centrat(d: ImageDraw.ImageDraw, text: str, font, y: int, culoare, latime: int = W) -> None:
    x0, _, x1, _ = d.textbbox((0, 0), text, font=font)
    d.text(((latime - (x1 - x0)) // 2, y), text, fill=culoare, font=font)


def _fundal_complex(w: int, h: int, seed: int = 3) -> Image.Image:
    rng = np.random.default_rng(seed)
    img = Image.new("RGB", (w, h), (120, 110, 100))
    d = ImageDraw.Draw(img)
    for _ in range(900):
        x, y = int(rng.integers(0, w)), int(rng.integers(0, h))
        s = int(rng.integers(6, 60))
        culoare = tuple(int(v) for v in rng.integers(40, 230, 3))
        (d.ellipse if rng.random() < 0.5 else d.rectangle)((x, y, x + s, y + int(s * rng.uniform(0.4, 1.5))), fill=culoare)
    return img.filter(ImageFilter.GaussianBlur(0.8))


# --- Generatoare (una pe test) ----------------------------------------------------------------------

def _minimalist(cale, watermark=False):
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    _centrat(d, "PIZZA", _font(230), 110, (20, 20, 20))
    if watermark:
        d.text((W - 430, H - 40), "shutterstock.com · 1234567890", fill=(150, 150, 150), font=_font(24, bold=False))
    img.save(cale)


def _multe_cuvinte(cale):
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    randuri = ["Cea mai buna pizza din oras cu blat subtire",
               "Ingrediente proaspete alese in fiecare dimineata",
               "Livrare gratuita pentru comenzi de peste cincizeci lei",
               "Program zilnic de luni pana duminica fara pauza",
               "Comanda online sau vino direct in restaurant azi"]
    for i, r in enumerate(randuri):
        d.text((60, 40 + i * 88), r, fill=(20, 20, 20), font=_font(52))
    img.save(cale)


def _text_pe_fundal(cale, text_rgb, fundal_rgb):
    img = Image.new("RGB", (W, H), fundal_rgb)
    _centrat(ImageDraw.Draw(img), "SALE 50%", _font(200), 140, text_rgb)
    img.save(cale)


def _peste_fotografie(cale):
    img = _fundal_complex(W, H)
    _centrat(ImageDraw.Draw(img), "PIZZA", _font(230), 110, (255, 255, 255))
    img.save(cale)


def _fara_text(cale):
    img = Image.new("RGB", (W, H), (240, 200, 60))
    d = ImageDraw.Draw(img)
    d.ellipse((550, 60, 950, 460), fill=(200, 40, 30))
    d.ellipse((640, 150, 860, 370), fill=(250, 230, 200))
    img.save(cale)


def _dominant_secundar(cale, latime=W, gol=150):
    k = latime / W
    img = Image.new("RGB", (latime, round(H * k)), (250, 205, 30))
    d = ImageDraw.Draw(img)
    _centrat(d, "PIZZA 20 MIN", _font(round(150 * k)), round(70 * k), (20, 20, 20), latime)
    _centrat(d, "Comanda pe pizzeria.ro", _font(round(60 * k)), round((70 + 150 + gol - 20) * k), (20, 20, 20), latime)
    img.save(cale)


def _titluri_concurente(cale):
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    # trei titluri de aceeași mărime, în poziții diferite (blocuri distincte care concurează)
    for t, (x, y) in zip(["PIZZA", "BURGER", "PASTA"], [(70, 40), (820, 190), (200, 340)]):
        d.text((x, y), t, fill=(20, 20, 20), font=_font(100))
    img.save(cale)


GENERATOARE = {
    "01_minimalist": _minimalist,
    "02_multe_cuvinte": _multe_cuvinte,
    "03_alb_pe_negru": lambda c: _text_pe_fundal(c, (255, 255, 255), (0, 0, 0)),
    "04_gri_deschis_pe_alb": lambda c: _text_pe_fundal(c, (195, 195, 195), (255, 255, 255)),
    "05_peste_fotografie": _peste_fotografie,
    "06_fara_text": _fara_text,
    "07_watermark": lambda c: _minimalist(c, watermark=True),
    "08a_rezolutie_mare": lambda c: _dominant_secundar(c, latime=2000),
    "08b_rezolutie_mica": lambda c: _dominant_secundar(c, latime=1000),
    "09_dominant_secundar": _dominant_secundar,
    "09b_blocuri_apropiate": lambda c: _dominant_secundar(c, gol=0),
    "10_titluri_concurente": _titluri_concurente,
}


def rezultat(nume: str) -> Rezultat:
    if nume not in _cache:
        DIR.mkdir(exist_ok=True)
        cale = DIR / f"{nume}.png"
        GENERATOARE[nume](cale)
        _cache[nume] = analizeaza(cale)
    return _cache[nume]


def crit(nume: str, id_: str):
    return next(c for c in rezultat(nume).criteria if c.id == id_)


# --- Teste ------------------------------------------------------------------------------------------

def test_01_minimalist():
    r = rezultat("01_minimalist")
    assert any("PIZZA" in t["text"].upper() for t in r.texts)
    assert crit("01_minimalist", "cantitate_text").scor == 100
    assert crit("01_minimalist", "ierarhie").scor == 100          # un singur bloc: nu se penalizează
    assert crit("01_minimalist", "contrast_text").scor >= 90
    assert crit("01_minimalist", "lizibilitate_text").scor >= 80
    assert r.score is not None and r.score >= 85


def test_02_multe_cuvinte_scad_cantitatea():
    c = crit("02_multe_cuvinte", "cantitate_text")
    assert c.masuratori["cuvinte_total"] >= 30
    assert c.scor <= 40
    assert c.scor < crit("01_minimalist", "cantitate_text").scor


def test_03_alb_pe_negru_contrast_foarte_bun():
    c = crit("03_alb_pe_negru", "contrast_text")
    assert c.masuratori["contrast_text_dominant"] >= 10
    assert c.scor >= 95


def test_04_gri_pe_alb_contrast_mai_mic():
    slab, bun = crit("04_gri_deschis_pe_alb", "contrast_text"), crit("03_alb_pe_negru", "contrast_text")
    assert slab.scor is not None, "textul gri trebuie totuși detectat"
    assert slab.masuratori["contrast_text_dominant"] < 2.0
    assert slab.scor < bun.scor - 40
    assert slab.recomandari, "contrastul slab trebuie să genereze o recomandare"


def test_05_text_peste_fotografie():
    foto, curat = crit("05_peste_fotografie", "aglomerare"), crit("01_minimalist", "aglomerare")
    assert foto.masuratori["densitate_muchii_in_jurul_textului"] > curat.masuratori["densitate_muchii_in_jurul_textului"]
    assert foto.scor < curat.scor
    assert foto.scor > 0, "o fotografie complexă nu primește automat zero"
    assert crit("05_peste_fotografie", "contrast_text").scor <= crit("01_minimalist", "contrast_text").scor


def test_06_fara_text_neevaluabil():
    """OCR-ul nu găsește text → criteriile de text sunt neevaluabile (nu neaplicabile, nu 0)."""
    r = rezultat("06_fara_text")
    for id_ in ("lizibilitate_text", "contrast_text", "cantitate_text", "ierarhie", "separare"):
        c = crit("06_fara_text", id_)
        assert c.status == "neevaluabil" and c.scor is None, (id_, c.status)
    assert crit("06_fara_text", "aglomerare").scor is not None
    assert r.coverage_pct == 15 and r.score is None and r.score_provisional is not None
    assert r.evaluation_status == "insuficienta"


def test_07_watermark_exclus():
    r = rezultat("07_watermark")
    assert any(t["tip"] == "watermark" for t in r.texts), "watermark-ul trebuie detectat"
    assert crit("07_watermark", "cantitate_text").masuratori["cuvinte_total"] == \
        crit("01_minimalist", "cantitate_text").masuratori["cuvinte_total"]


def test_08_rezolutie_nu_schimba_radical_scorul():
    mare, mic = rezultat("08a_rezolutie_mare"), rezultat("08b_rezolutie_mica")
    assert abs(mare.score - mic.score) <= 8, (mare.score, mic.score)


def test_09_ierarhie_titlu_dominant():
    c = crit("09_dominant_secundar", "ierarhie")
    assert c.masuratori["raport_dominant_secundar"] >= 1.8
    assert c.scor >= 80


def test_09b_spatierea_imbunatateste_separarea():
    assert crit("09_dominant_secundar", "separare").scor > crit("09b_blocuri_apropiate", "separare").scor


def test_10_titluri_concurente():
    c = crit("10_titluri_concurente", "ierarhie")
    assert len(c.masuratori["blocuri_concurente"]) >= 2
    assert c.scor <= 70 and c.scor < crit("09_dominant_secundar", "ierarhie").scor


# --- Scorul general cu criterii N/A (fără OCR, rapide) -------------------------------------------------------

def _criterii(scoruri: dict[str, int | None]) -> list[Criteriu]:
    """Cele 6 criterii; cele lipsă din `scoruri` sau cu None sunt neevaluabile."""
    return [Criteriu(id_, C.NUME[id_], scoruri.get(id_), pondere, None, "", "", "",
                     status="evaluat" if scoruri.get(id_) is not None else "neevaluabil")
            for id_, pondere in C.PONDERI.items()]


def test_na_toate_evaluate_scor_final():
    g = scor_general(_criterii({id_: 70 for id_ in C.PONDERI}))
    assert g["coverage_pct"] == 100 and g["evaluation_status"] == "completa" and g["score"] == 70


def test_na_scor_mare_din_putine_criterii_nu_e_evaluare_completa():
    # doar aglomerarea (15%) și separarea (10%), ambele 100: scor mare, dar evaluare de 25%
    g = scor_general(_criterii({"aglomerare": 100, "separare": 100}))
    assert g["coverage_pct"] == 25
    assert g["score"] is None and g["evaluation_status"] == "insuficienta"
    assert g["score_provisional"] == 100     # păstrat doar ca valoare provizorie


def test_na_acoperire_60_79_doar_provizoriu():
    # lizibilitatea (25%) neevaluabilă → 75%
    g = scor_general(_criterii({id_: 90 for id_ in C.PONDERI if id_ != "lizibilitate_text"}))
    assert g["coverage_pct"] == 75 and g["score"] is None
    assert g["evaluation_status"] == "provizorie" and g["score_provisional"] == 90


def test_na_praguri_80_si_60():
    fara_contrast = scor_general(_criterii({id_: 80 for id_ in C.PONDERI if id_ != "contrast_text"}))
    assert fara_contrast["coverage_pct"] == 80 and fara_contrast["evaluation_status"] == "completa"
    assert fara_contrast["score"] == 80
    sase_zeci = scor_general(_criterii({id_: 80 for id_ in C.PONDERI if id_ not in ("lizibilitate_text", "aglomerare")}))
    assert sase_zeci["coverage_pct"] == 60 and sase_zeci["evaluation_status"] == "provizorie"
    sub = scor_general(_criterii({id_: 80 for id_ in ("contrast_text", "cantitate_text", "ierarhie")}))
    assert sub["coverage_pct"] == 50 and sub["evaluation_status"] == "insuficienta" and sub["score"] is None


def test_na_nu_inseamna_zero():
    # un criteriu neevaluabil nu trage în jos scorul celorlalte (nu e tratat ca 0)
    g = scor_general(_criterii({id_: 60 for id_ in C.PONDERI if id_ != "separare"}))
    assert g["score_provisional"] == 60 and g["coverage_pct"] == 90


def test_na_niciun_criteriu_evaluat():
    g = scor_general(_criterii({}))
    assert g["coverage_pct"] == 0 and g["score"] is None and g["score_provisional"] is None
    assert g["evaluation_status"] == "insuficienta"


def test_reproductibil():
    DIR.mkdir(exist_ok=True)
    cale = DIR / "01_minimalist.png"
    _minimalist(cale)
    assert analizeaza(cale).to_dict() == analizeaza(cale).to_dict()


def main() -> None:
    teste = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    esecuri = 0
    for nume, f in teste:
        try:
            f()
            stare = "OK  "
        except AssertionError as e:
            stare, esecuri = f"EȘEC ({e})", esecuri + 1
        print(f"{stare:<6} {nume}")
    print()
    for nume in GENERATOARE:
        r = rezultat(nume)
        scoruri = " ".join(f"{c.id[:6]}={'–' if c.scor is None else c.scor}" for c in r.criteria)
        print(f"{nume:<24} scor={r.score if r.score is not None else '–':<4} acoperire={r.coverage_pct:<3} {scoruri}")
    print(f"\n{len(teste) - esecuri}/{len(teste)} teste trecute")
    raise SystemExit(1 if esecuri else 0)


if __name__ == "__main__":
    main()
