"""Linie de comandă (totul rulează local, fără API-uri).

    cd scoring
    python -m analiza_panou reclama.png
    python -m analiza_panou reclama.jpg --json
    python -m analiza_panou reclama.jpg --out rezultat.json
    python -m analiza_panou reclama.jpg --dimensiune 12x4    # doar pentru avertismentul de format
"""

import argparse
import json
import sys

from .analiza import analizeaza


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="analiza_panou",
                                description="Outdoor Ad Quality Score: scor din 100 pentru o reclamă outdoor (local).")
    p.add_argument("imagine", help="reclama (JPG sau PNG; merg și WEBP, BMP, TIFF)")
    p.add_argument("--dimensiune", choices=["12x4", "4x3"], default=None,
                   help="dimensiunea panoului în metri; opțional, folosită doar pentru avertismentul de format")
    p.add_argument("--ocr", choices=["easyocr", "rapidocr"], default=None, help="motorul OCR local (implicit easyocr)")
    p.add_argument("--json", action="store_true", help="afișează tot rezultatul ca JSON")
    p.add_argument("--out", help="salvează rezultatul JSON în acest fișier")
    a = p.parse_args()

    panou = tuple(float(x) for x in a.dimensiune.split("x")) if a.dimensiune else (None, None)
    try:
        rez = analizeaza(a.imagine, *panou, backend=a.ocr)
    except (FileNotFoundError, OSError) as e:
        sys.exit(f"Nu pot deschide imaginea: {e}. Formate acceptate: JPG, PNG (și WEBP, BMP, TIFF).")

    date = rez.to_dict()
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(date, f, ensure_ascii=False, indent=2)
    if a.json:
        print(json.dumps(date, ensure_ascii=False, indent=2))
        return

    if rez.evaluation_status == "completa":
        print(f"\nSCOR: {rez.score}/100 — {rez.rating}   (acoperire {rez.coverage_pct}%)\n")
    elif rez.evaluation_status == "provizorie":
        print(f"\nSCOR PROVIZORIU: {rez.score_provisional}/100 — evaluare incompletă (acoperire {rez.coverage_pct}%)\n")
    else:
        print(f"\nFĂRĂ SCOR FINAL — {rez.rating}\n")
    for c in rez.criteria:
        valoare = c.status if c.scor is None else f"{c.scor:>3}/100"
        marcaj = "  (incert)" if c.status == "incert" else ""
        print(f"  {c.nume:<26} {valoare:>11}  pondere {c.pondere}%{marcaj}")

    if rez.texts:
        print("\nText detectat:")
        for t in rez.texts:
            contrast = "–" if t["local_contrast"] is None else f"{t['local_contrast']}:1"
            eticheta = " [watermark, exclus]" if t["tip"] == "watermark" else (" [incert]" if t["incert"] else "")
            print(f"  „{t['text']}”{eticheta} — caractere {t['char_height_rel'] * 100:.1f}% din imagine, "
                  f"contrast {contrast}, încredere OCR {t['confidence']}")
    print("\nPăreri:")
    for o in rez.opinions:
        print(f"  • {o}")
    if rez.recommendations:
        print("\nRecomandări:")
        for r in rez.recommendations:
            print(f"  → {r}")
    if rez.warnings:
        print("\nAvertismente:")
        for w in rez.warnings:
            print(f"  ! {w}")
    t = rez.technical
    print(f"\nTehnic (nu intră în scor): {t['latime_px']}×{t['inaltime_px']} px, raport {t['raport_aspect']} "
          f"({t['format_apropiat']}), {t['format_fisier']}"
          + (f", calitate JPEG ~{t['compresie']['calitate_jpeg_estimata']}" if t['compresie']['calitate_jpeg_estimata'] else ""))
    for o in t["observatii"]:
        print(f"  - {o}")
    print()


if __name__ == "__main__":
    main()
