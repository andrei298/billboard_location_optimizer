"""Informații tehnice despre fișier (raportate separat, NU intră în scorul publicitar)
și detectarea unei posibile fotografii a unui panou deja instalat."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image

_TABEL_JPEG_LUMINANTA = np.array([
    16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55, 14, 13, 16, 24, 40, 57, 69, 56,
    14, 17, 22, 29, 51, 87, 80, 62, 18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92,
    49, 64, 78, 87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99], dtype=np.float64)

_FORMATE = {"3:1": 3.0, "2:1": 2.0, "16:9": 16 / 9, "3:2": 1.5, "4:3": 4 / 3, "1:1": 1.0, "3:4": 0.75,
            "1:√2 (A3/A4 vertical)": 1 / np.sqrt(2), "2:3": 2 / 3, "9:16": 9 / 16}


def _calitate_jpeg(img: Image.Image) -> int | None:
    """Estimarea calității JPEG din tabelul de cuantizare al luminanței (formula libjpeg)."""
    tabele = getattr(img, "quantization", None)
    if not tabele or 0 not in tabele:
        return None
    q = np.sort(np.asarray(tabele[0], dtype=np.float64))
    s = float(np.median(q * 100 / np.sort(_TABEL_JPEG_LUMINANTA)))
    calitate = (200 - s) / 2 if s <= 100 else 5000 / s
    return int(np.clip(round(calitate), 1, 100))


def _blocuri_jpeg(gri: np.ndarray) -> float:
    """Raportul dintre salturile de la granițele grilei 8×8 și cele din interior (≈1 = fără artefacte)."""
    g = gri[:1024, :1024].astype(np.float64)
    dx, dy = np.abs(np.diff(g, axis=1)), np.abs(np.diff(g, axis=0))
    bx, by = (np.arange(dx.shape[1]) % 8) == 7, (np.arange(dy.shape[0]) % 8) == 7
    interior = (dx[:, ~bx].mean() + dy[~by, :].mean()) / 2
    if interior < 0.25:
        return 1.0
    return float(((dx[:, bx].mean() + dy[by, :].mean()) / 2) / interior)


def informatii_tehnice(cale: str | Path, rgb: np.ndarray, panou_m: tuple[float, float] | None = None) -> dict:
    H, W = rgb.shape[:2]
    with Image.open(cale) as f:
        format_fisier = f.format
        calitate = _calitate_jpeg(f) if f.format == "JPEG" else None

    raport = W / H
    nume_format = min(_FORMATE, key=lambda k: abs(np.log(raport / _FORMATE[k])))
    if abs(np.log(raport / _FORMATE[nume_format])) > 0.04:
        nume_format = "nestandard"

    gri = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    mic = gri if W <= 1200 else cv2.resize(gri, (1200, round(H * 1200 / W)), interpolation=cv2.INTER_AREA)
    var_laplace = float(cv2.Laplacian(mic, cv2.CV_64F).var())
    sobel = np.hypot(cv2.Sobel(mic, cv2.CV_64F, 1, 0), cv2.Sobel(mic, cv2.CV_64F, 0, 1))
    muchii_puternice = float(np.percentile(sobel, 99) / (4 * 255))
    blocuri = _blocuri_jpeg(gri)

    observatii = []
    if muchii_puternice < 0.12:
        observatii.append("muchiile cele mai puternice sunt moi: imaginea poate fi neclară sau mărită artificial")
    if blocuri > 1.3:
        observatii.append(f"artefacte de compresie vizibile (raport grilă 8×8 = {blocuri:.2f})")
    if calitate is not None and calitate < 70:
        observatii.append(f"JPEG salvat cu calitate estimată {calitate}")
    if W * H < 1_000_000:
        observatii.append(f"rezoluție mică ({W}×{H} px): pentru tipar e nevoie de fișierul sursă")

    info = {
        "latime_px": W, "inaltime_px": H, "megapixeli": round(W * H / 1e6, 2), "format_fisier": format_fisier,
        "raport_aspect": round(raport, 3), "format_apropiat": nume_format,
        "blur": {"varianta_laplacian": round(var_laplace, 1), "muchii_puternice": round(muchii_puternice, 3)},
        "compresie": {"raport_grila_8x8": round(blocuri, 2), "calitate_jpeg_estimata": calitate},
        "observatii": observatii,
        "nota": "Informațiile tehnice nu intră în scorul publicitar.",
    }
    if panou_m:
        r_panou = panou_m[0] / panou_m[1]
        info["panou_declarat_m"] = list(panou_m)
        info["procent_decupat_la_umplerea_panoului"] = round((1 - min(raport, r_panou) / max(raport, r_panou)) * 100, 1)
    return info


def detecteaza_fotografie_panou(rgb: np.ndarray, regiuni: list[dict]) -> dict | None:
    """Caută un cadru patrulater mare care conține textul, înconjurat de o scenă cu detalii proprii.
    Dacă îl găsește, imaginea poate fi o fotografie a unui panou instalat, nu grafica originală."""
    H, W = rgb.shape[:2]
    k = 800 / W if W > 800 else 1.0
    mic = cv2.resize(rgb, (round(W * k), round(H * k)), interpolation=cv2.INTER_AREA) if k != 1.0 else rgb
    gri = cv2.GaussianBlur(cv2.cvtColor(mic, cv2.COLOR_RGB2GRAY), (5, 5), 0)
    muchii = cv2.Canny(gri, 50, 150)
    h, w = muchii.shape
    contururi, _ = cv2.findContours(cv2.dilate(muchii, np.ones((3, 3), np.uint8)), cv2.RETR_LIST,
                                    cv2.CHAIN_APPROX_SIMPLE)
    arie_text = sum((r["bbox"][2] - r["bbox"][0]) * (r["bbox"][3] - r["bbox"][1]) for r in regiuni) * k * k
    cel_mai_bun = None
    for c in contururi:
        aprox = cv2.approxPolyDP(c, 0.02 * cv2.arcLength(c, True), True)
        if len(aprox) != 4 or not cv2.isContourConvex(aprox):
            continue
        fractie = cv2.contourArea(aprox) / (h * w)
        if not 0.12 <= fractie <= 0.85:
            continue
        x, y, bw, bh = cv2.boundingRect(aprox)
        if x <= 2 and y <= 2 and x + bw >= w - 2 and y + bh >= h - 2:
            continue
        masca = np.zeros((h, w), np.uint8)
        cv2.fillConvexPoly(masca, aprox.reshape(-1, 2), 1)
        if arie_text > 0:
            in_cadru = sum(masca[int(np.clip((r["bbox"][1] + r["bbox"][3]) / 2 * k, 0, h - 1)),
                                 int(np.clip((r["bbox"][0] + r["bbox"][2]) / 2 * k, 0, w - 1))]
                           * (r["bbox"][2] - r["bbox"][0]) * (r["bbox"][3] - r["bbox"][1]) * k * k for r in regiuni)
            if in_cadru / arie_text < 0.7:
                continue
        exterior = muchii[masca == 0] > 0
        if exterior.size == 0 or exterior.mean() < 0.03:
            continue
        if cel_mai_bun is None or fractie > cel_mai_bun["fractie_din_imagine"]:
            cel_mai_bun = {"cadru_px": (aprox.reshape(-1, 2) / k).round().astype(int).tolist(),
                           "fractie_din_imagine": round(float(fractie), 2),
                           "densitate_detalii_in_afara_cadrului": round(float(exterior.mean()), 3)}
    return cel_mai_bun
