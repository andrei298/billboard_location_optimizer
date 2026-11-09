- utilizare pentru Ubuntu / Linux

1) Instalare (o singura data)

- sudo apt update
- sudo apt install python3 python3-venv python3-pip
- python3 -m venv .venv
- source .venv/bin/activate
- pip install -r scoring/requirements.txt

2) Chei

- cp .env.example .env
- cp web/config.example.js web/config.local.js

Deschide fisierele si completeaza `GEMINI_API_KEY` si `googleMapsApiKey`.

3) Pornire

- source .venv/bin/activate
- python3 scoring/analiza_panou/server_site.py

Deschide in browser: http://localhost:8000

4) Oprire

- Ctrl + c apoi deactivate

5) Dezinstalare

rm -rf <folderul-proiectului> ~/.EasyOCR