"""OCR local și măsurători pe fiecare regiune de text.

Backend: EasyOCR (preferat) sau RapidOCR (rezervă). Ambele rulează pe calculator, fără API.
Toate coordonatele întoarse sunt în pixelii imaginii originale.
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
import warnings
from difflib import SequenceMatcher

import cv2
import numpy as np

from . import config as C

_cititoare: dict = {}


# --- Backend --------------------------------------------------------------------------------

def alege_backend(preferat: str | None = None) -> str:
    preferat = preferat or os.environ.get("ANALIZA_OCR") or C.OCR_BACKEND_PREFERAT
    ordine = [preferat] + [b for b in ("easyocr", "rapidocr") if b != preferat]
    for b in ordine:
        try:
            __import__(b)
            return b
        except ImportError:
            continue
    raise RuntimeError("Niciun OCR local instalat. Rulează: pip install easyocr")


def _citeste(rgb: np.ndarray, backend: str) -> list[dict]:
    """O singură rulare OCR pe `rgb`; coordonatele sunt în pixelii lui `rgb`."""
    if backend == "easyocr":
        if "easyocr" not in _cititoare:
            import easyocr
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                _cititoare["easyocr"] = easyocr.Reader(C.OCR_LIMBI, gpu=False, verbose=False)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            rez = _cititoare["easyocr"].readtext(np.ascontiguousarray(rgb), detail=1, paragraph=False)
        brute = [(np.asarray(b, dtype=np.float64), t, float(c)) for b, t, c in rez]
    else:
        if "rapidocr" not in _cititoare:
            logging.getLogger("RapidOCR").setLevel(logging.WARNING)
            from rapidocr import RapidOCR
            logging.getLogger("RapidOCR").setLevel(logging.WARNING)
            _cititoare["rapidocr"] = RapidOCR()
            logging.getLogger("RapidOCR").setLevel(logging.WARNING)
        r = _cititoare["rapidocr"](np.ascontiguousarray(rgb[..., ::-1]))
        brute = [] if r.boxes is None else [
            (np.asarray(b, dtype=np.float64), t, float(c)) for b, t, c in zip(r.boxes, r.txts, r.scores)]

    fragmente = []
    for poligon, text, conf in brute:
        text = (text or "").strip()
        # un singur caracter izolat e aproape mereu o formă confundată cu o literă (cerc → „O”, ornament → „9”)
        if not text or conf < C.OCR_INCREDERE_MINIMA or len(normalizeaza(text)) < C.OCR_CARACTERE_MINIME:
            continue
        x0, y0 = poligon.min(axis=0)
        x1, y1 = poligon.max(axis=0)
        h = (np.linalg.norm(poligon[3] - poligon[0]) + np.linalg.norm(poligon[2] - poligon[1])) / 2
        fragmente.append({"text": text, "poligon": poligon, "conf": conf,
                          "bbox": [float(x0), float(y0), float(x1), float(y1)], "h_cutie": float(h)})
    return fragmente


def _scaleaza(f: dict, k: float) -> dict:
    f = dict(f)
    f["poligon"] = f["poligon"] * k
    f["bbox"] = [v * k for v in f["bbox"]]
    f["h_cutie"] = f["h_cutie"] * k
    return f


# --- Text: normalizare și similaritate ---------------------------------------------------------

def normalizeaza(text: str) -> str:
    t = unicodedata.normalize("NFKD", text)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return re.sub(r"[^0-9a-z]", "", t.lower())


def similaritate(a: str, b: str) -> float:
    a, b = normalizeaza(a), normalizeaza(b)
    if not a and not b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def recuperare(original: str, gasit: str) -> float:
    """Ce fracție din caracterele textului original se regăsește, în ordine, în textul găsit."""
    a, b = normalizeaza(original), normalizeaza(gasit)
    if not a:
        return 1.0
    return sum(m.size for m in SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks()) / len(a)


# --- Geometrie ----------------------------------------------------------------------------------

def _arie(b) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _intersectie(a, b) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def iou(a, b) -> float:
    i = _intersectie(a, b)
    u = _arie(a) + _arie(b) - i
    return i / u if u > 0 else 0.0


def gol_intre(a, b) -> float:
    """Distanța dintre două dreptunghiuri (0 dacă se ating sau se suprapun)."""
    dx = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
    dy = max(0.0, max(a[1], b[1]) - min(a[3], b[3]))
    return max(dx, dy)


def _uneste(a, b):
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]


# --- Rulări OCR, deduplicare -----------------------------------------------------------------------

def _e_duplicat(f: dict, p: dict) -> bool:
    i = iou(f["bbox"], p["bbox"])
    if i >= C.DEDUP_IOU:
        return True
    if i >= 0.2 and similaritate(f["text"], p["text"]) >= C.DEDUP_SIMILARITATE:
        return True
    mic, mare = (f, p) if _arie(f["bbox"]) <= _arie(p["bbox"]) else (p, f)
    if _arie(mic["bbox"]) > 0 and _intersectie(f["bbox"], p["bbox"]) / _arie(mic["bbox"]) >= C.DEDUP_INCLUDERE:
        return normalizeaza(mic["text"]) in normalizeaza(mare["text"])
    return False


def deduplica(fragmente: list[dict]) -> list[dict]:
    pastrate: list[dict] = []
    for f in sorted(fragmente, key=lambda f: -f["conf"]):
        for p in pastrate:
            if _e_duplicat(f, p):
                p["treceri"] += 1
                break
        else:
            pastrate.append(dict(f, treceri=1))
    return pastrate


def ocr_complet(rgb: np.ndarray, backend: str) -> tuple[list[dict], list[str]]:
    """OCR pe imaginea originală (scalată doar pentru OCR) + o trecere cu CLAHE dacă e necesar."""
    H0, W0 = rgb.shape[:2]
    latura = max(W0, H0)
    if latura > C.OCR_LATURA_MAX:
        k = C.OCR_LATURA_MAX / latura
    elif latura < C.OCR_LATURA_MICA:
        k = min(4.0, C.OCR_LATURA_TINTA_MICI / latura)
    else:
        k = 1.0
    lucru = rgb if k == 1.0 else cv2.resize(
        rgb, (round(W0 * k), round(H0 * k)), interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_CUBIC)

    fragmente = [_scaleaza(f, 1 / k) for f in _citeste(lucru, backend)]
    treceri = ["original"]
    incredere = float(np.mean([f["conf"] for f in fragmente])) if fragmente else 0.0
    if not fragmente or incredere < C.OCR_PRAG_PREPROCESARE or k > 1.0:
        gri = cv2.cvtColor(lucru, cv2.COLOR_RGB2GRAY)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gri)
        fragmente += [_scaleaza(f, 1 / k) for f in _citeste(cv2.cvtColor(clahe, cv2.COLOR_GRAY2RGB), backend)]
        treceri.append("contrast_CLAHE")
    return deduplica(fragmente), treceri


def retentie_la_redimensionare(rgb: np.ndarray, regiuni: list[dict], backend: str) -> list[int]:
    """Rulează OCR pe versiuni micșorate și notează, pe fiecare regiune, cât din text se mai recunoaște."""
    H0, W0 = rgb.shape[:2]
    latimi = [w for w in C.OCR_LATIMI_REDIMENSIONARE if w < 0.9 * W0]
    for r in regiuni:
        r["retentie"] = {}
    for w in latimi:
        h = max(1, round(H0 * w / W0))
        mic = cv2.resize(rgb, (w, h), interpolation=cv2.INTER_AREA)
        gasite = [_scaleaza(f, W0 / w) for f in _citeste(mic, backend)]
        for r in regiuni:
            candidati = [f for f in gasite
                         if _arie(r["bbox"]) > 0 and _intersectie(r["bbox"], f["bbox"]) / min(
                             _arie(r["bbox"]), max(_arie(f["bbox"]), 1e-9)) > 0.3]
            if not candidati:
                r["retentie"][w] = 0.0
                continue
            impreuna = " ".join(f["text"] for f in sorted(candidati, key=lambda f: f["bbox"][0]))
            r["retentie"][w] = round(max(recuperare(r["text"], impreuna),
                                         max(recuperare(r["text"], f["text"]) for f in candidati)), 3)
    return latimi


# --- Măsurători pe regiune ---------------------------------------------------------------------------

def _luminanta(rgb_u8: np.ndarray) -> np.ndarray:
    a = rgb_u8.astype(np.float64) / 255.0
    lin = np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)
    return lin[..., 0] * 0.2126 + lin[..., 1] * 0.7152 + lin[..., 2] * 0.0722


def _otsu(gri_u8: np.ndarray) -> float:
    t, _ = cv2.threshold(np.ascontiguousarray(gri_u8), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return float(t)


def _separabilitate(valori: np.ndarray, masca: np.ndarray) -> float:
    """Varianța dintre clase / varianța totală (0 = nu se separă, 1 = separare perfectă)."""
    v = valori.astype(np.float64)
    total = v.var()
    if total < 1e-9 or masca.all() or not masca.any():
        return 0.0
    w1 = masca.mean()
    return float(w1 * (1 - w1) * (v[masca].mean() - v[~masca].mean()) ** 2 / total)


def raport_contrast(l1: float, l2: float) -> float:
    return (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)


def masoara_regiune(rgb: np.ndarray, r: dict) -> None:
    """Completează pe regiune: înălțimea caracterelor, contrastul local, separabilitatea, claritatea muchiilor."""
    H, W = rgb.shape[:2]
    x0, y0 = max(0, int(r["bbox"][0])), max(0, int(r["bbox"][1]))
    x1, y1 = min(W, int(np.ceil(r["bbox"][2]))), min(H, int(np.ceil(r["bbox"][3])))
    h = max(1, y1 - y0)
    pad = max(2, round(0.5 * h))
    X0, Y0, X1, Y1 = max(0, x0 - pad), max(0, y0 - pad), min(W, x1 + pad), min(H, y1 + pad)
    zona = rgb[Y0:Y1, X0:X1]
    lum = _luminanta(zona)
    gri = cv2.cvtColor(zona, cv2.COLOR_RGB2GRAY)
    ix0, iy0, ix1, iy1 = x0 - X0, y0 - Y0, x1 - X0, y1 - Y0
    inel = np.ones(lum.shape, dtype=bool)
    inel[iy0:iy1, ix0:ix1] = False

    r.update(char_h=0.6 * h, char_h_estimat=True, contrast=None, contrast_incert=True,
             contrast_zone=[], separabilitate=None, claritate_muchii=None, bbox_glife=list(r["bbox"]))
    l_in, g_in = lum[iy0:iy1, ix0:ix1], gri[iy0:iy1, ix0:ix1]
    if l_in.size < 30:
        return
    hi = g_in > _otsu(g_in)
    if hi.all() or not hi.any():
        return

    # care clasă sunt literele: cea mai depărtată de fundalul din jur
    fundal_inel = float(np.median(lum[inel])) if inel.any() else None
    m_hi, m_lo = float(np.median(l_in[hi])), float(np.median(l_in[~hi]))
    text_deschis = (abs(m_hi - fundal_inel) > abs(m_lo - fundal_inel)) if fundal_inel is not None else hi.mean() < 0.5
    masca_text = hi if text_deschis else ~hi

    # înălțimea caracterelor: mediana componentelor conexe de mărimea unei litere
    n, _, st, _ = cv2.connectedComponentsWithStats(masca_text.astype(np.uint8), connectivity=8)
    latime_cutie = ix1 - ix0
    litere = [i for i in range(1, n)
              if 0.25 * h <= st[i, cv2.CC_STAT_HEIGHT] <= 1.05 * h and st[i, cv2.CC_STAT_WIDTH] <= 3 * h
              and st[i, cv2.CC_STAT_AREA] >= 3 and st[i, cv2.CC_STAT_WIDTH] < 0.95 * latime_cutie]
    if len(litere) >= 2:
        r["char_h"], r["char_h_estimat"] = float(np.median([st[i, cv2.CC_STAT_HEIGHT] for i in litere])), False
        # conturul real al literelor (cutia OCR are spațiu de rezervă în jur): folosit pentru distanțe și margini
        gx0 = min(st[i, cv2.CC_STAT_LEFT] for i in litere) + ix0 + X0
        gy0 = min(st[i, cv2.CC_STAT_TOP] for i in litere) + iy0 + Y0
        gx1 = max(st[i, cv2.CC_STAT_LEFT] + st[i, cv2.CC_STAT_WIDTH] for i in litere) + ix0 + X0
        gy1 = max(st[i, cv2.CC_STAT_TOP] + st[i, cv2.CC_STAT_HEIGHT] for i in litere) + iy0 + Y0
        r["bbox_glife"] = [float(gx0), float(gy0), float(gx1), float(gy1)]

    # contrast pe zone (fundal neuniform): Otsu pe fiecare zonă, fundal = pixeli non-text + inelul din jur
    n_zone = int(np.clip(round(latime_cutie / (1.5 * h)), 1, 6))
    capete = np.linspace(ix0, ix1, n_zone + 1).astype(int)
    contraste, fiabile, etas = [], [], []
    for a, b in zip(capete[:-1], capete[1:]):
        gs, ls = gri[iy0:iy1, a:b], lum[iy0:iy1, a:b]
        if gs.size < 20:
            continue
        hs = gs > _otsu(gs)
        txt = hs if text_deschis else ~hs
        frac = float(txt.mean())
        if frac in (0.0, 1.0):
            continue
        eta = _separabilitate(gs, hs)
        fundal = np.concatenate([ls[~txt], lum[:, a:b][inel[:, a:b]]])
        c = raport_contrast(float(np.median(ls[txt])), float(np.median(fundal)))
        contraste.append(c)
        etas.append(eta)
        fiabile.append(eta >= C.CONTRAST_SEPARABILITATE_MIN and C.TEXT_FRACTIE_MIN <= frac <= C.TEXT_FRACTIE_MAX)
    if contraste:
        sigure = [c for c, f in zip(contraste, fiabile) if f]
        baza = sigure or contraste
        r["contrast"] = float(np.percentile(baza, C.CONTRAST_PERCENTILA))
        r["contrast_incert"] = len(sigure) < 0.5 * len(contraste)
        r["contrast_zone"] = [round(c, 2) for c in contraste]
        r["separabilitate"] = float(np.median(etas))

    # claritatea muchiilor literelor: panta maximă a muchiei față de amplitudinea text–fundal
    amplitudine = abs(float(np.median(l_in[masca_text])) - float(np.median(l_in[~masca_text])))
    if amplitudine >= 0.02:
        gx = cv2.Sobel(l_in, cv2.CV_64F, 1, 0, ksize=3)
        gy = cv2.Sobel(l_in, cv2.CV_64F, 0, 1, ksize=3)
        r["claritate_muchii"] = float(np.clip(np.percentile(np.hypot(gx, gy), 98) / (4 * amplitudine), 0, 1))
    else:
        r["claritate_muchii"] = 0.0


# --- Watermark ---------------------------------------------------------------------------------------

_RE_STOCK = re.compile(C.WATERMARK_DOMENII, re.I)
_RE_DOMENIU = re.compile(r"[a-z0-9-]+\s?[.·]\s?(com|net|org|ro|eu)\b", re.I)
_NUME_STOCK = [normalizeaza(n) for n in C.WATERMARK_DOMENII.replace("\\s?", "").replace("\\.", "").split("|")]


def e_watermark(text: str) -> bool:
    """Watermark de bancă de imagini sau metadate. Un URL obișnuit NU e considerat watermark.
    Tolerează greșelile OCR („shulterstock”) prin similaritate cu numele băncilor de imagini."""
    if _RE_STOCK.search(text):
        return True
    cuvinte = [normalizeaza(c) for c in re.split(r"[\s.·,/]+", text) if c]
    candidati = cuvinte + [a + b for a, b in zip(cuvinte, cuvinte[1:])]
    if any(len(c) >= 5 and SequenceMatcher(None, c, n).ratio() >= 0.8 for c in candidati for n in _NUME_STOCK):
        return True
    return bool(re.search(r"\d{7,}", text) and _RE_DOMENIU.search(text))


def marcheaza_watermark(regiuni: list[dict]) -> None:
    """Setează r["tip"]; un număr lung (ID de imagine) e watermark doar dacă stă lângă un watermark."""
    for r in regiuni:
        r["tip"] = "watermark" if e_watermark(r["text"]) else "text"
    marcate = [r for r in regiuni if r["tip"] == "watermark"]
    for r in regiuni:
        if r["tip"] == "text" and re.fullmatch(r"[\d\s.·\-]{7,}", r["text"]) and any(
                gol_intre(r["bbox"], w["bbox"]) < 2 * max(r["h_cutie"], w["h_cutie"]) for w in marcate):
            r["tip"] = "watermark"


# --- Rânduri și blocuri ---------------------------------------------------------------------------------

def _centru_y(b):
    return (b[1] + b[3]) / 2


def grupeaza_randuri(regiuni: list[dict]) -> list[dict]:
    randuri: list[dict] = []
    for r in sorted(regiuni, key=lambda r: _centru_y(r["bbox"])):
        for rd in randuri:
            ch = min(r["char_h"], rd["char_h"])
            raport = r["char_h"] / rd["char_h"]
            gol_x = max(0.0, max(r["bbox"][0], rd["bbox"][0]) - min(r["bbox"][2], rd["bbox"][2]))
            if (abs(_centru_y(r["bbox"]) - _centru_y(rd["bbox"])) < 0.5 * min(r["h_cutie"], rd["h_cutie"])
                    and C.RAND_RAPORT_MARIME[0] < raport < C.RAND_RAPORT_MARIME[1]
                    and gol_x < C.RAND_GOL_ORIZONTAL * ch):
                rd["regiuni"].append(r)
                rd["bbox"] = _uneste(rd["bbox"], r["bbox"])
                rd["char_h"] = float(np.median([x["char_h"] for x in rd["regiuni"]]))
                rd["h_cutie"] = float(np.median([x["h_cutie"] for x in rd["regiuni"]]))
                break
        else:
            randuri.append({"regiuni": [r], "bbox": list(r["bbox"]), "char_h": r["char_h"], "h_cutie": r["h_cutie"]})
    for rd in randuri:
        rd["regiuni"].sort(key=lambda x: x["bbox"][0])
        rd["text"] = " ".join(x["text"] for x in rd["regiuni"])
        rd["bbox_glife"] = _uneste_toate([x["bbox_glife"] for x in rd["regiuni"]])
    return sorted(randuri, key=lambda rd: rd["bbox"][1])


def _uneste_toate(cutii: list) -> list:
    rez = list(cutii[0])
    for c in cutii[1:]:
        rez = _uneste(rez, c)
    return rez


def grupeaza_blocuri(randuri: list[dict]) -> list[dict]:
    blocuri: list[dict] = []
    for rd in randuri:
        for b in blocuri:
            gol = rd["bbox"][1] - b["bbox"][3]
            se_suprapun = min(rd["bbox"][2], b["bbox"][2]) > max(rd["bbox"][0], b["bbox"][0])
            raport = rd["char_h"] / b["char_h"]
            if (gol < C.BLOC_GOL_VERTICAL * max(rd["char_h"], b["char_h"]) and se_suprapun
                    and C.BLOC_RAPORT_MARIME[0] < raport < C.BLOC_RAPORT_MARIME[1]):
                b["randuri"].append(rd)
                b["bbox"] = _uneste(b["bbox"], rd["bbox"])
                b["char_h"] = float(np.median([x["char_h"] for x in b["randuri"]]))
                break
        else:
            blocuri.append({"randuri": [rd], "bbox": list(rd["bbox"]), "char_h": rd["char_h"]})
    for b in blocuri:
        b["text"] = " / ".join(rd["text"] for rd in b["randuri"])
        b["regiuni"] = [r for rd in b["randuri"] for r in rd["regiuni"]]
        b["bbox_glife"] = _uneste_toate([rd["bbox_glife"] for rd in b["randuri"]])
    return blocuri
