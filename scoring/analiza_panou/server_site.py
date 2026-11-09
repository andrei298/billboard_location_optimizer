"""Serverul site-ului, cu analiza reclamei inclusă.

Extinde serverul existent din scoring/server.py (fără să-l modifice):
- GET  /...               -> fișierele din web/ (ca în scoring/server.py)
- POST /score             -> clasamentul locațiilor (ca în scoring/server.py)
- POST /analiza-reclama   -> {"image": "data:image/jpeg;base64,..."} -> rezultatul analizei locale (JSON)
- POST /sugestii-ai       -> scor general + criterii + recomandări -> sugestii Gemini (JSON)

Rulare (din rădăcina repo-ului):  python scoring/analiza_panou/server_site.py   apoi http://localhost:8000
Analiza rulează local (EasyOCR + OpenCV), fără API-uri; o cerere durează ~10–30 s.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import sys
import tempfile
import threading
import traceback
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SCORING = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCORING))

import server as server_baza  # noqa: E402  scoring/server.py, nemodificat
from analiza_panou.analiza import analizeaza  # noqa: E402

MAX_OCTETI = 25 * 1024 * 1024
MAX_CERERE_SUGESTII_AI = 64 * 1024
MODEL_GEMINI = "gemini-3.1-flash-lite"
_blocare_ocr = threading.Lock()   # modelul OCR e încărcat o singură dată și nu e sigur pe mai multe fire


def _cheie_gemini() -> str:
    cheie = os.getenv("GEMINI_API_KEY", "").strip()
    if cheie:
        return cheie

    cale_env = server_baza.ROOT / ".env"
    try:
        linii = cale_env.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return ""
    for linie in linii:
        nume, separator, valoare = linie.partition("=")
        if separator and nume.strip() == "GEMINI_API_KEY":
            return valoare.strip().strip("\"'")
    return ""


def _valideaza_feedback(date: dict) -> tuple[int | None, list[dict], list[str]]:
    scor_general = date.get("overallScore")
    if scor_general is not None and (
        isinstance(scor_general, bool)
        or not isinstance(scor_general, (int, float))
        or not 0 <= scor_general <= 100
    ):
        raise ValueError("scorul general trebuie să fie între 0 și 100")
    criterii = date.get("criteria", [])
    recomandari = date.get("recommendations", [])
    if not isinstance(criterii, list) or len(criterii) > 10:
        raise ValueError("lista criteriilor nu este validă")
    if not isinstance(recomandari, list) or len(recomandari) > 5:
        raise ValueError("lista recomandărilor nu este validă")

    criterii_valide = []
    for criteriu in criterii:
        if not isinstance(criteriu, dict) or not isinstance(criteriu.get("name"), str):
            raise ValueError("un criteriu are un format invalid")
        scor = criteriu.get("score")
        if scor is not None and (
            isinstance(scor, bool) or not isinstance(scor, (int, float)) or not 0 <= scor <= 100
        ):
            raise ValueError("scorul unui criteriu trebuie să fie între 0 și 100 sau null")
        pondere = criteriu.get("weight")
        if pondere is not None and (
            isinstance(pondere, bool) or not isinstance(pondere, (int, float)) or not 0 <= pondere <= 100
        ):
            raise ValueError("ponderea unui criteriu trebuie să fie între 0 și 100")
        explicatie = criteriu.get("reason", "")
        if not isinstance(explicatie, str):
            raise ValueError("explicația unui criteriu trebuie să fie text")
        criterii_valide.append({
            "name": criteriu["name"][:120],
            "score": round(scor) if scor is not None else None,
            "weight": round(pondere) if pondere is not None else None,
            "reason": explicatie[:300],
        })

    recomandari_valide = []
    for recomandare in recomandari:
        if not isinstance(recomandare, str):
            raise ValueError("recomandările trebuie să fie text")
        recomandari_valide.append(recomandare[:300])
    scor_intreg = round(scor_general) if scor_general is not None else None
    return scor_intreg, criterii_valide, recomandari_valide


def _cere_recomandari_gemini(scor_general: int | None, criterii: list[dict], recomandari: list[str]) -> str:
    cheie = _cheie_gemini()
    if not cheie:
        raise RuntimeError("GEMINI_API_KEY nu este configurată în variabila de mediu sau în fișierul .env.")

    feedback = json.dumps(
        {"scor_general": scor_general, "scoruri_criterii": criterii, "recomandari_locale": recomandari},
        ensure_ascii=False,
    )
    prompt = (
        "Ești un consultant pentru designul reclamelor outdoor. Folosește scorul general, scorurile "
        "criteriilor și recomandările locale pentru a genera 3-5 sugestii concrete, prioritizate și scurte. "
        "Explică fiecare sugestie ca acțiune practică legată de criteriul evaluat. Nu pretinde că ai văzut "
        "imaginea și nu inventa detalii despre reclamă, produs sau public. Dacă datele susțin mai puține "
        "sugestii, returnează doar sugestiile justificate. Răspunde în limba română.\n\n"
        f"{feedback}"
    )
    cerere = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"maxOutputTokens": 500}}
    request = Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_GEMINI}:generateContent",
        data=json.dumps(cerere, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": cheie,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=120) as raspuns:
            rezultat = json.loads(raspuns.read())
    except HTTPError as e:
        print(f"Gemini returned HTTP {e.code}; check the API key, model access, and quota.")
        e.close()
        if e.code == 429:
            raise RuntimeError(
                f"Gemini a limitat cererea (HTTP 429); verifică limita gratuită și disponibilitatea modelului {MODEL_GEMINI} în AI Studio."
            ) from e
        if e.code == 402:
            raise RuntimeError(
                "Gemini a respins cererea (HTTP 402); verifică setările de facturare și cota proiectului Google AI Studio."
            ) from e
        if e.code in {401, 403}:
            raise RuntimeError(
                f"Gemini a respins cheia API (HTTP {e.code}); verifică cheia și accesul la API."
            ) from e
        if e.code == 400:
            raise RuntimeError(
                f"Gemini a respins formatul cererii (HTTP 400); verifică modelul {MODEL_GEMINI} și datele trimise."
            ) from e
        raise RuntimeError(f"Gemini a respins cererea (HTTP {e.code}); verifică cheia și modelul selectat.") from e
    except (URLError, TimeoutError) as e:
        print(f"Gemini connection failed: {e}")
        raise RuntimeError("Nu s-a putut contacta Gemini API. Verifică conexiunea serverului.") from e
    except (json.JSONDecodeError, OSError) as e:
        print(f"Invalid Gemini response: {e}")
        raise RuntimeError("Gemini a returnat un răspuns invalid.") from e

    candidates = rezultat.get("candidates", [])
    parts = candidates[0].get("content", {}).get("parts", []) if candidates and isinstance(candidates[0], dict) else []
    text = "\n".join(part.get("text", "") for part in parts if isinstance(part, dict))
    text = text.strip() if isinstance(text, str) else ""
    if not text:
        raise RuntimeError("Gemini nu a returnat sugestii text.")
    return text[:12000]


class Handler(server_baza.Handler):
    def do_POST(self) -> None:  # noqa: N802
        cale = self.path.partition("?")[0].rstrip("/")
        if cale == "/sugestii-ai":
            self._sugestii_ai()
            return
        if cale != "/analiza-reclama":
            super().do_POST()
            return
        try:
            lungime = int(self.headers.get("Content-Length", 0))
            if lungime > MAX_OCTETI:
                self._send_json(413, {"error": "imaginea e prea mare (maximum 25 MB)"})
                return
            date = json.loads(self.rfile.read(lungime) or b"{}")
            antet, _, continut = str(date["image"]).partition(",")
            if not antet.startswith("data:image/") or ";base64" not in antet:
                raise ValueError("imaginea trebuie trimisă ca data URL base64")
            octeti = base64.b64decode(continut, validate=True)
            with tempfile.TemporaryDirectory() as folder:
                cale = Path(folder) / ("reclama.png" if "png" in antet else "reclama.jpg")
                cale.write_bytes(octeti)
                with _blocare_ocr:
                    rezultat = analizeaza(cale)
            self._send_json(200, rezultat.to_dict())
        except (BrokenPipeError, ConnectionResetError):
            print("  (pagina a fost închisă înainte de rezultatul analizei)")
        except (ValueError, KeyError, binascii.Error) as e:
            self._send_json(400, {"error": f"date invalide: {e}"})
        except OSError as e:  # Pillow nu recunoaște fișierul
            self._send_json(400, {"error": f"imaginea nu poate fi citită: {e}"})
        except Exception as e:
            traceback.print_exc()
            self._send_json(500, {"error": f"analiza a eșuat: {e}"})

    def _sugestii_ai(self) -> None:
        if not _cheie_gemini():
            self._send_json(503, {"error": "GEMINI_API_KEY nu este configurată în variabila de mediu sau în fișierul .env."})
            return
        try:
            lungime = int(self.headers.get("Content-Length", 0))
            if lungime < 0:
                raise ValueError("lungimea cererii este invalidă")
            if lungime > MAX_CERERE_SUGESTII_AI:
                self._send_json(413, {"error": "cererea pentru sugestii este prea mare (maximum 64 KB)"})
                return
            date = json.loads(self.rfile.read(lungime) or b"{}")
            if not isinstance(date, dict):
                raise ValueError("corpul cererii trebuie să fie un obiect JSON")
            scor_general, criterii, recomandari = _valideaza_feedback(date)
            sugestii = _cere_recomandari_gemini(scor_general, criterii, recomandari)
            self._send_json(200, {
                "suggestions": sugestii,
                "model": MODEL_GEMINI,
                "provider": "Gemini API",
            })
        except (BrokenPipeError, ConnectionResetError):
            print("  (pagina a fost închisă înainte de răspunsul Gemini)")
        except (ValueError, KeyError, binascii.Error) as e:
            self._send_json(400, {"error": f"date invalide: {e}"})
        except RuntimeError as e:
            self._send_json(502, {"error": str(e)})
        except Exception as e:
            traceback.print_exc()
            self._send_json(500, {"error": f"generarea sugestiilor Gemini a eșuat: {e}"})


def _incarca_ocr() -> None:
    """Încarcă modelul OCR la pornire, ca prima analiză să nu aștepte și după el."""
    try:
        import numpy as np
        from analiza_panou.ocr import _citeste, alege_backend
        with _blocare_ocr:
            _citeste(np.full((64, 64, 3), 255, dtype=np.uint8), alege_backend())
        print("  OCR local pregătit.")
    except Exception as e:  # analiza va încerca din nou la prima cerere
        print(f"  OCR-ul nu a putut fi pregătit la pornire: {e}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    handler = partial(Handler, directory=str(server_baza.WEB_DIR))
    server = ThreadingHTTPServer(("127.0.0.1", server_baza.PORT), handler)
    print(f"SightLine (cu analiza reclamei) pe http://localhost:{server_baza.PORT}  (Ctrl+C pentru oprire)")
    threading.Thread(target=_incarca_ocr, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
