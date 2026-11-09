"""Server local care leaga site-ul (web/) de calculul din scoring/.

- GET  /...     -> fisierele din web/ (index.html, results.html, ...)
- POST /score   -> primeste pinurile din formular, intoarce clasamentul (score.analyze)
- GET  /strazi?lat=..&lon=..&r=..  -> strazile din jurul unui punct, din data/cache/ (pentru web/sightline-view.js);
                                      404 daca punctul e in afara zonei descarcate

Rulare:  python3 scoring/server.py   apoi deschide http://localhost:8000
Doar biblioteca standard Python, nimic de instalat.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from score import analyze  # noqa: E402

WEB_DIR = ROOT / "web"
PORT = int(os.getenv("PORT", "8000"))


class Handler(SimpleHTTPRequestHandler):
    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        # P1 (web/sightline-view.js): strazile din cache-ul de aici, ca traseul soferului sa nu depinda de Overpass
        if self.path.startswith("/strazi"):
            try:
                from urllib.parse import parse_qs, urlparse
                from traffic import _roads_from_area
                q = parse_qs(urlparse(self.path).query)
                lat, lon = float(q["lat"][0]), float(q["lon"][0])
                radius = min(int(float(q.get("r", ["260"])[0])), 600)
                ways = _roads_from_area(lat, lon, radius)
                if ways is None:
                    self._send_json(404, {"error": "punctul e in afara zonei din cache"})
                else:
                    self._send_json(200, {"elements": ways})
            except (KeyError, ValueError) as e:
                self._send_json(400, {"error": f"parametri invalizi: {e}"})
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/score":
            self._send_json(404, {"error": "endpoint necunoscut"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
            self._send_json(200, analyze(data))
        except (BrokenPipeError, ConnectionResetError):
            print("  (pagina a fost inchisa sau reincarcata inainte de raspuns)")
        except (ValueError, KeyError) as e:
            self._send_json(400, {"error": f"date invalide: {e}"})
        except Exception as e:  # ex. OpenStreetMap nu raspunde si nu e in cache
            traceback.print_exc()
            self._send_json(502, {"error": str(e)})


def main() -> None:
    # Diacriticele din numele strazilor nu trebuie sa strice serverul (terminalul Windows).
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    handler = partial(Handler, directory=str(WEB_DIR))
    server = ThreadingHTTPServer(("127.0.0.1", PORT), handler)
    print(f"SightLine pe http://localhost:{PORT}  (Ctrl+C pentru oprire)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
