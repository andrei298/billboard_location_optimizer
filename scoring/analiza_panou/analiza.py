"""Outdoor Ad Quality Score: evaluarea locală și deterministă a unei reclame outdoor.

Intrare obligatorie: imaginea reclamei (JPG sau PNG). Fără API extern: OCR local (EasyOCR; RapidOCR ca rezervă),
OpenCV și NumPy. Scorul se calculează determinist în Python, din 6 criterii:

    1. Lizibilitatea textului     25%   dimensiunea caracterelor, recunoașterea la redimensionare, claritatea locală
    2. Contrastul text–fundal     20%   luminanța literelor față de fundalul imediat, percentila inferioară pe zone
    3. Cantitatea de text         15%   cuvinte (telefon / preț / procent = 1), rânduri, text dominant vs. secundar
    4. Aglomerarea vizuală        15%   Canny: detalii în jurul textului, ocupare, distribuție 4×4, regiuni concurente
    5. Ierarhia tipografică       15%   bloc dominant vs. mediana celorlalte, niveluri de mărime, concurență
    6. Separarea elementelor      10%   distanțe între blocuri, coliziuni, margini (în înălțimi de caracter)

Criteriile fără scor (neaplicabile sau neevaluabile) nu primesc 0. Acoperirea = suma ponderilor criteriilor
evaluate: de la 80% scorul e final, între 60 și 79% e doar provizoriu, iar sub 60% nu există scor final.
Calitatea tehnică a fișierului se raportează separat și nu intră în scor. Nu se presupun dimensiuni fizice
sau distanțe de citire.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

from . import config as C
from .criterii import Context, Criteriu, aglomerare, cantitate, contrast, ierarhie, lizibilitate, separare
from .ocr import (alege_backend, grupeaza_blocuri, grupeaza_randuri, marcheaza_watermark, masoara_regiune,
                  ocr_complet, retentie_la_redimensionare)
from .tehnic import detecteaza_fotografie_panou, informatii_tehnice

PONDERI = C.PONDERI

__all__ = ["analizeaza", "Rezultat", "Criteriu", "PONDERI", "incarca_imagine"]


@dataclass
class Rezultat:
    score: int | None                 # scorul final; None dacă acoperirea e sub 80%
    rating: str
    criteria: list[Criteriu]
    opinions: list[str]
    recommendations: list[str]
    warnings: list[str] = field(default_factory=list)
    texts: list[dict] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    coverage_pct: int = 100           # cât din ponderi a putut fi evaluat
    score_provisional: int | None = None   # scorul calculat pe criteriile disponibile, chiar dacă acoperirea e mică
    technical: dict = field(default_factory=dict)
    evaluation_status: str = "completa"    # "completa" (≥ 80%) | "provizorie" (60–79%) | "insuficienta" (< 60%)

    def to_dict(self) -> dict:
        return asdict(self)


def incarca_imagine(cale: str | Path) -> Image.Image:
    """Încarcă imaginea la dimensiunile originale (rotire EXIF aplicată, transparența pe alb)."""
    img = Image.open(cale)
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
        fundal = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(fundal, img)
    return img.convert("RGB")


def _eticheta(scor: int) -> str:
    return next(nume for prag, nume in C.ETICHETE if scor >= prag)


def scor_general(criterii: list[Criteriu]) -> dict:
    """Scorul general, acoperirea și statusul evaluării.

    Criteriile fără scor (neaplicabile sau neevaluabile) nu primesc 0; scorul se calculează din cele evaluate
    (ponderi renormalizate), dar e prezentat ca final doar dacă acoperirea e de cel puțin 80%.
    """
    evaluate = [c for c in criterii if c.scor is not None]
    acoperire = sum(c.pondere for c in evaluate)
    provizoriu = round(sum(c.scor * c.pondere for c in evaluate) / acoperire) if evaluate else None
    if provizoriu is not None and acoperire >= C.ACOPERIRE_VALIDA_PCT:
        status, scor, eticheta = "completa", provizoriu, _eticheta(provizoriu)
    elif provizoriu is not None and acoperire >= C.ACOPERIRE_MINIMA_PCT:
        status, scor = "provizorie", None
        eticheta = f"Scor provizoriu: au putut fi evaluate criterii care însumează doar {acoperire}% din ponderi"
    else:
        status, scor = "insuficienta", None
        eticheta = (f"Fără scor final: au putut fi evaluate criterii care însumează doar {acoperire}% din ponderi "
                    f"(minimum {C.ACOPERIRE_MINIMA_PCT}%)")
    return {"score": scor, "score_provisional": provizoriu, "coverage_pct": acoperire,
            "evaluation_status": status, "rating": eticheta}


def _alege_dominant(blocuri: list[dict], W: int, H: int) -> dict | None:
    """Blocul dominant vizual: dimensiunea caracterelor, suprafața, contrastul și poziția (nu doar mărimea)."""
    if not blocuri:
        return None
    ch_max = max(b["char_h"] for b in blocuri)
    arie_max = max((b["bbox"][2] - b["bbox"][0]) * (b["bbox"][3] - b["bbox"][1]) for b in blocuri) or 1.0
    for b in blocuri:
        contraste = [r["contrast"] for r in b["regiuni"] if r["contrast"] is not None]
        c = float(np.median(contraste)) if contraste else 1.0
        cx, cy = (b["bbox"][0] + b["bbox"][2]) / 2, (b["bbox"][1] + b["bbox"][3]) / 2
        centralitate = 1 - np.hypot((cx - W / 2) / (W / 2), (cy - H / 2) / (H / 2)) / np.sqrt(2)
        arie = (b["bbox"][2] - b["bbox"][0]) * (b["bbox"][3] - b["bbox"][1])
        b["dominanta"] = round(float(0.6 * b["char_h"] / ch_max + 0.2 * arie / arie_max
                                     + 0.1 * min(c, 7.0) / 7.0 + 0.1 * centralitate), 3)
    return max(blocuri, key=lambda b: b["dominanta"])


def analizeaza(cale_imagine: str | Path, latime_panou_m: float | None = None, inaltime_panou_m: float | None = None,
               backend: str | None = None) -> Rezultat:
    """Analizează reclama. Dimensiunile panoului sunt opționale și se folosesc doar pentru avertismentul de format."""
    rgb = np.ascontiguousarray(np.asarray(incarca_imagine(cale_imagine)))
    H, W = rgb.shape[:2]
    D = float(np.sqrt(W * H))
    be = alege_backend(backend)

    # --- Etapa 1: OCR, măsurători pe regiuni, watermark ---------------------------------------------
    toate, treceri = ocr_complet(rgb, be)
    for r in toate:
        masoara_regiune(rgb, r)
        r["char_rel"] = r["char_h"] / D
    marcheaza_watermark(toate)
    regiuni = [r for r in toate if r["tip"] == "text"]
    latimi = retentie_la_redimensionare(rgb, regiuni, be)

    randuri = grupeaza_randuri(regiuni)
    blocuri = grupeaza_blocuri(randuri)
    bloc_dom = _alege_dominant(blocuri, W, H)
    reg_dom = max(bloc_dom["regiuni"], key=lambda r: r["char_h"]) if bloc_dom else None
    for r in regiuni:
        r["importanta"] = float(np.clip((r["char_h"] / reg_dom["char_h"]) ** C.IMPORTANTA_EXPONENT,
                                        C.IMPORTANTA_MINIMA, 1.0))
    for b in blocuri:
        b["importanta"] = max(r["importanta"] for r in b["regiuni"])

    k_mic = C.AGL_LATIME / W
    gri_mic = cv2.resize(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), (C.AGL_LATIME, max(1, round(H * k_mic))),
                         interpolation=cv2.INTER_AREA if k_mic < 1 else cv2.INTER_CUBIC)
    ctx = Context(W, H, D, regiuni, randuri, blocuri, bloc_dom, reg_dom, gri_mic, k_mic)

    # --- Criteriile și scorul general ----------------------------------------------------------------------
    criterii = [lizibilitate(ctx), contrast(ctx), cantitate(ctx), aglomerare(ctx), ierarhie(ctx), separare(ctx)]
    evaluate = [c for c in criterii if c.scor is not None]
    general = scor_general(criterii)
    acoperire, provizoriu = general["coverage_pct"], general["score_provisional"]

    # --- Avertismente ---------------------------------------------------------------------------------------
    avertismente = []
    if not regiuni:
        avertismente.append("OCR-ul nu a detectat text. Asta nu dovedește că reclama nu are text, deci criteriile "
                            "de text sunt neevaluabile (nu primesc 0).")
    neevaluabile = [c.nume for c in criterii if c.status == "neevaluabil"]
    if general["evaluation_status"] == "provizorie":
        avertismente.append(f"Evaluare incompletă: acoperire {acoperire}% (sub {C.ACOPERIRE_VALIDA_PCT}%). Scorul "
                            f"{provizoriu} e doar provizoriu. Neevaluate: {', '.join(neevaluabile)}.")
    elif general["evaluation_status"] == "insuficienta":
        avertismente.append(f"Evaluare insuficientă: acoperire {acoperire}% (sub {C.ACOPERIRE_MINIMA_PCT}%), deci nu "
                            f"există un scor final valid. Neevaluate: {', '.join(neevaluabile) or '–'}.")
    for c in criterii:
        if c.status == "incert":
            motive = c.masuratori.get("motive_incertitudine")
            avertismente.append(f"{c.nume}: măsurătoare incertă" + (f" ({'; '.join(motive)})." if motive else "."))
    nesigure = [r for r in regiuni if r["conf"] < C.OCR_INCREDERE_SIGURA]
    if nesigure:
        avertismente.append(f"{len(nesigure)} regiuni de text au fost recunoscute cu încredere scăzută "
                            f"({', '.join(repr(r['text']) for r in nesigure[:4])}); OCR-ul poate să nu fi găsit tot textul.")
    watermarks = [r for r in toate if r["tip"] == "watermark"]
    if watermarks:
        avertismente.append("Watermark / metadate excluse din scor: " + ", ".join(repr(r["text"]) for r in watermarks) + ".")
    foto_panou = detecteaza_fotografie_panou(rgb, regiuni)
    if foto_panou:
        avertismente.append(f"Imaginea pare o fotografie a unui panou instalat (cadru patrulater de "
                            f"{foto_panou['fractie_din_imagine'] * 100:.0f}% din imagine, cu scenă în jur). Evaluarea se "
                            f"referă la toată fotografia; pentru un scor corect folosește grafica originală a reclamei.")

    panou = (latime_panou_m, inaltime_panou_m) if latime_panou_m and inaltime_panou_m else None
    tehnic = informatii_tehnice(cale_imagine, rgb, panou)
    tehnic["posibila_fotografie_de_panou"] = foto_panou
    if panou and tehnic["procent_decupat_la_umplerea_panoului"] > 5:
        avertismente.append(f"Formatul imaginii ({tehnic['raport_aspect']}:1) diferă de panoul declarat "
                            f"({panou[0]:g}×{panou[1]:g} m): ~{tehnic['procent_decupat_la_umplerea_panoului']:.0f}% s-ar "
                            f"pierde dacă imaginea umple panoul.")

    # --- Păreri și recomandări (doar din indicatorii calculați) ------------------------------------------
    forte = sorted((c for c in evaluate if c.nivel == "bun"), key=lambda c: -c.scor * c.pondere)
    slabe = sorted((c for c in evaluate if c.nivel != "bun"), key=lambda c: -(100 - c.scor) * c.pondere)
    pareri = [c.parere for c in forte[:2]] + [c.parere for c in slabe[:3]]
    recomandari = [rec for c in sorted(evaluate, key=lambda c: -(100 - c.scor) * c.pondere) for rec in c.recomandari]

    texte = [{
        "text": r["text"], "tip": r["tip"],
        "bbox": [int(round(v)) for v in r["bbox"]],
        "poligon": np.asarray(r["poligon"]).round().astype(int).tolist(),
        "confidence": round(r["conf"], 3), "incert": r["conf"] < C.OCR_INCREDERE_SIGURA,
        "relative_height": round((r["bbox"][3] - r["bbox"][1]) / H, 4),
        "relative_area": round((r["bbox"][2] - r["bbox"][0]) * (r["bbox"][3] - r["bbox"][1]) / (W * H), 5),
        "char_height_rel": round(r["char_rel"], 4), "char_height_estimat": r["char_h_estimat"],
        "local_contrast": None if r["contrast"] is None else round(r["contrast"], 2),
        "local_contrast_incert": r["contrast_incert"],
        "recunoastere_la_redimensionare": r.get("retentie") or None,
        "importanta": round(r.get("importanta", 0.0), 3),
        "treceri_ocr": r["treceri"],
    } for r in sorted(toate, key=lambda r: (r["bbox"][1], r["bbox"][0]))]

    metrici = {
        "ocr_backend": be, "ocr_treceri": treceri, "latimi_testate_redimensionare": latimi,
        "regiuni_text": len(regiuni), "regiuni_watermark": len(watermarks), "randuri": len(randuri),
        "blocuri": len(blocuri), "bloc_dominant": bloc_dom["text"] if bloc_dom else None,
        "ponderi": C.PONDERI, "criterii_neaplicabile": [c.id for c in criterii if c.status == "neaplicabil"],
        "criterii_neevaluabile": [c.id for c in criterii if c.status == "neevaluabil"],
    }
    return Rezultat(score=general["score"], rating=general["rating"], criteria=criterii, opinions=pareri,
                    recommendations=recomandari, warnings=avertismente, texts=texte, metrics=metrici,
                    coverage_pct=acoperire, score_provisional=provizoriu, technical=tehnic,
                    evaluation_status=general["evaluation_status"])
