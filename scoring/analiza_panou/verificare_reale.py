"""Verificare calitativă pe reclame reale (separat de testele unitare sintetice).

    cd scoring
    python -m analiza_panou.verificare_reale <folder_sau_fisiere...>

Afișează, pentru fiecare imagine, scorul, criteriile, textul detectat și avertismentele, ca să poată fi
comparate cu evaluarea unui om. Nu modifică parametrii: calibrarea se face în config.py, pe tot setul.
"""

import sys
from pathlib import Path

from .analiza import analizeaza

EXTENSII = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    cai: list[Path] = []
    for arg in sys.argv[1:]:
        p = Path(arg)
        cai += sorted(f for f in p.iterdir() if f.suffix.lower() in EXTENSII) if p.is_dir() else [p]
    if not cai:
        sys.exit("Dă un folder sau fișiere cu reclame.")
    for p in cai:
        r = analizeaza(p)
        scor = r.score if r.score is not None else f"– (provizoriu {r.score_provisional})"
        print(f"\n=== {p.name}: {scor}  [{r.rating}, acoperire {r.coverage_pct}%]")
        print("    " + "  ".join(f"{c.id}={'–' if c.scor is None else c.scor}{'?' if c.status == 'incert' else ''}"
                               for c in r.criteria))
        print("    text: " + " | ".join(f"{t['text']}{' [wm]' if t['tip'] == 'watermark' else ''}" for t in r.texts))
        for w in r.warnings:
            print(f"    ! {w}")


if __name__ == "__main__":
    main()
