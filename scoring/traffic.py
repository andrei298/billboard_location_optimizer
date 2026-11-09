"""Din coordonate -> strada (OpenStreetMap / Overpass) -> masini pe zi, viteza, secunde vizibile.

Toate cifrele de aici sunt ESTIMARI (vezi docs/scor.md). Secundele vizibile si
procentul acoperit exacte vin mai tarziu din Unity (results_<id>.json).

Raspunsurile Overpass se salveaza in data/cache/, ca demo-ul sa mearga si fara internet.
"""

from __future__ import annotations

import json
import math
import sys
import threading
import urllib.parse
import urllib.request
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"
USER_AGENT = "SightLine-hackathon/0.1 (scoring)"
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
OVERPASS_TIMEOUT_S = 20

# Zona descarcata o singura data (tot Bucurestiul): pinurile din ea se calculeaza
# instant si fara internet.  Descarcare:  python3 scoring/traffic.py prefetch
ZONA_DEMO = (44.33, 25.96, 44.55, 26.25)  # sud, vest, nord, est

# Masini pe zi pe O banda, dupa tipul drumului (estimare de pornire, de calibrat).
MASINI_PE_BANDA_PE_ZI = {
    "motorway": 18000,
    "trunk": 14000,
    "primary": 10000,
    "secondary": 7000,
    "tertiary": 4000,
    "unclassified": 1500,
    "residential": 800,
    "living_street": 200,
}
# Cate benzi presupunem cand OSM nu are tag-ul "lanes" (drum cu dublu sens).
BENZI_IMPLICITE = {"motorway": 4, "trunk": 4, "primary": 4, "secondary": 2, "tertiary": 2}

# Viteze legale implicite in Romania pentru valorile text din "maxspeed".
VITEZE_RO = {"RO:urban": 50, "RO:rural": 90, "RO:trunk": 100, "RO:motorway": 130}
VITEZA_IMPLICITA_KMH = 50

# De la ce distanta se citeste un panou de 4 m inaltime (scalat liniar cu inaltimea).
DISTANTA_CITIRE_M_PENTRU_4M = 150

# Pana vine Unity, presupunem acelasi procent acoperit peste tot.
PROCENT_ACOPERIT_IMPLICIT = 20

DRUMURI_AUTO = "|".join(MASINI_PE_BANDA_PE_ZI)


# ---------- Overpass ----------

_overpass_lock = threading.Lock()  # o singura cerere Overpass odata (server cu mai multe fire)


def _fetch_overpass(query: str, timeout_s: int = OVERPASS_TIMEOUT_S) -> dict:
    body = urllib.parse.urlencode({"data": query}).encode()
    last_error = None
    with _overpass_lock:
        for url in OVERPASS_URLS:
            try:
                req = urllib.request.Request(url, data=body, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                    return json.load(resp)
            except Exception as e:  # incercam urmatorul server
                last_error = e
    raise RuntimeError(f"OpenStreetMap (Overpass) nu raspunde: {last_error}")


# ---------- zona descarcata o data ----------

def _area_file(bbox: tuple) -> Path:
    s, w, n, e = bbox
    return CACHE_DIR / f"overpass_area_{s}_{w}_{n}_{e}.json"


TAGURI_PASTRATE = ("highway", "name", "lanes", "lanes:forward", "lanes:backward",
                   "oneway", "maxspeed", "junction")


def prefetch_area(bbox: tuple = ZONA_DEMO) -> int:
    """Descarca toate drumurile auto din bbox in data/cache/ (compactat). Intoarce numarul de drumuri."""
    query = (f'[out:json][timeout:300];way{bbox}[highway~"^({DRUMURI_AUTO})(_link)?$"];'
             f'out tags geom;')
    data = _fetch_overpass(query, timeout_s=400)
    ways = [{"type": "way", "id": e["id"],
             "tags": {k: v for k, v in e.get("tags", {}).items() if k in TAGURI_PASTRATE},
             "geometry": [{"lat": round(p["lat"], 6), "lon": round(p["lon"], 6)} for p in e["geometry"]]}
            for e in data.get("elements", []) if e.get("type") == "way" and e.get("geometry")]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _area_file(bbox).write_text(json.dumps({"elements": ways}, ensure_ascii=False, separators=(",", ":")),
                                encoding="utf-8")
    _loaded_areas.clear()
    return len(ways)


# zona -> (bbox, [(lat_min, lat_max, lon_min, lon_max, strada)])
_loaded_areas: dict[Path, tuple[tuple, list[tuple]]] = {}
_areas_lock = threading.Lock()


def _cached_areas() -> list[tuple[tuple, list[tuple]]]:
    with _areas_lock:
        for f in CACHE_DIR.glob("overpass_area_*.json"):
            if f not in _loaded_areas:
                bbox = tuple(float(x) for x in f.stem.removeprefix("overpass_area_").split("_"))
                indexed = []
                for e in json.loads(f.read_text(encoding="utf-8")).get("elements", []):
                    if e.get("type") == "way" and e.get("geometry"):
                        lats = [p["lat"] for p in e["geometry"]]
                        lons = [p["lon"] for p in e["geometry"]]
                        indexed.append((min(lats), max(lats), min(lons), max(lons), e))
                _loaded_areas[f] = (bbox, indexed)
        return list(_loaded_areas.values())


def _roads_from_area(lat: float, lon: float, radius_m: int) -> list[dict] | None:
    """Drumurile din zona descarcata, daca punctul (cu raza lui) e in interiorul ei."""
    margin_lat = radius_m / 111_320
    margin_lon = radius_m / (111_320 * math.cos(math.radians(lat)))
    for (s, w, n, e), indexed in _cached_areas():
        if s + margin_lat <= lat <= n - margin_lat and w + margin_lon <= lon <= e - margin_lon:
            # filtru rapid pe dreptunghiul strazii, apoi distanta exacta
            return [way for la0, la1, lo0, lo1, way in indexed
                    if la0 - margin_lat <= lat <= la1 + margin_lat
                    and lo0 - margin_lon <= lon <= lo1 + margin_lon
                    and distance_to_way_m(lat, lon, way) <= radius_m]
    return None


RAZA_DESCARCARE_M = 150   # in afara zonei: o singura cerere pe pin, apoi filtram local


def roads_near(lat: float, lon: float, radius_m: int = 50) -> list[dict]:
    """Drumurile auto din jurul punctului: din zona descarcata, din cache sau de la Overpass."""
    from_area = _roads_from_area(lat, lon, radius_m)
    if from_area is not None:
        return from_area
    old_cache = CACHE_DIR / f"overpass_{lat:.5f}_{lon:.5f}_r{radius_m}.json"
    cache_file = CACHE_DIR / f"overpass_{lat:.5f}_{lon:.5f}_r{RAZA_DESCARCARE_M}.json"
    if old_cache.exists():
        data = json.loads(old_cache.read_text(encoding="utf-8"))
    elif cache_file.exists():
        data = json.loads(cache_file.read_text(encoding="utf-8"))
    else:
        query = (f'[out:json][timeout:{OVERPASS_TIMEOUT_S}];'
                 f'way(around:{RAZA_DESCARCARE_M},{lat},{lon})[highway~"^({DRUMURI_AUTO})(_link)?$"];'
                 f'out tags geom;')
        try:
            data = _fetch_overpass(query)
        except RuntimeError as e:
            raise RuntimeError(f"pinul {lat:.5f}, {lon:.5f} e in afara Bucurestiului (zona salvata) "
                               f"si OpenStreetMap nu raspunde acum. Incearca din nou peste un minut.") from e
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    ways = [e for e in data.get("elements", []) if e.get("type") == "way" and e.get("geometry")]
    return [w for w in ways if distance_to_way_m(lat, lon, w) <= radius_m]


# ---------- geometrie ----------

def _distance_to_segment_m(lat, lon, a, b) -> float:
    """Distanta (m) de la punct la segmentul a-b, proiectie plana locala."""
    k = 111_320.0
    kx = k * math.cos(math.radians(lat))
    px, py = 0.0, 0.0
    ax, ay = (a["lon"] - lon) * kx, (a["lat"] - lat) * k
    bx, by = (b["lon"] - lon) * kx, (b["lat"] - lat) * k
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    t = 0.0 if seg2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg2))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(cx - px, cy - py)


def distance_to_way_m(lat: float, lon: float, way: dict) -> float:
    g = way["geometry"]
    if len(g) == 1:
        return _distance_to_segment_m(lat, lon, g[0], g[0])
    return min(_distance_to_segment_m(lat, lon, g[i], g[i + 1]) for i in range(len(g) - 1))


def nearest_road(lat: float, lon: float, ways: list[dict]) -> dict | None:
    return min(ways, key=lambda w: distance_to_way_m(lat, lon, w), default=None)


# Un panou e orientat spre strada mare: dintre drumurile aflate la cel mult
# TOLERANTA_M fata de cel mai apropiat, il alegem pe cel mai circulat.
TOLERANTA_M = 20


def billboard_road(lat: float, lon: float, ways: list[dict]) -> dict | None:
    closest = nearest_road(lat, lon, ways)
    if closest is None:
        return None
    limit = distance_to_way_m(lat, lon, closest) + TOLERANTA_M
    candidates = [w for w in ways if distance_to_way_m(lat, lon, w) <= limit]
    return max(candidates, key=lambda w: estimate_daily_cars(w.get("tags", {}), centrality_for(w)))


# ---------- estimari ----------

def road_type(tags: dict) -> str:
    """'primary_link' -> 'primary'."""
    return tags.get("highway", "").removesuffix("_link")


def parse_maxspeed(value: str | None) -> float | None:
    if not value:
        return None
    if value in VITEZE_RO:
        return float(VITEZE_RO[value])
    try:
        return float(value.split()[0])
    except ValueError:
        return None


def lane_count(tags: dict) -> int:
    try:
        return max(1, int(float(tags["lanes"])))
    except (KeyError, ValueError):
        default = BENZI_IMPLICITE.get(road_type(tags), 2)
        return max(1, default // 2) if tags.get("oneway") == "yes" else default


def lanes_toward_billboard(tags: dict) -> float:
    """Benzile din sensul care vede panoul (un panou are o singura fata).

    Sens unic: toate benzile. Dublu sens: jumatate (sau media lanes:forward/backward).
    Asa, un bulevard desenat ca o singura linie in OSM si unul desenat pe sensuri
    separate se compara corect.
    """
    if tags.get("oneway") in ("yes", "-1"):
        return float(lane_count(tags))
    try:
        return (float(tags["lanes:forward"]) + float(tags["lanes:backward"])) / 2
    except (KeyError, ValueError):
        return lane_count(tags) / 2


# Plafon fizic: cate masini pe zi poate duce o banda (in oras, cu semafoare, ~15.000).
CAPACITATE_MAX_PE_BANDA_PE_ZI = {"motorway": 25000, "trunk": 20000}
CAPACITATE_MAX_IMPLICITA = 15000


def estimate_daily_cars(tags: dict, centrality: float = 1.0) -> int:
    """Masini pe zi in sensul panoului = masini/banda (tip drum) x factor retea x benzi pe sens.

    Valoarea pe banda nu poate depasi capacitatea fizica a unei benzi.
    """
    kind = road_type(tags)
    per_lane = MASINI_PE_BANDA_PE_ZI.get(kind, 800) * centrality
    per_lane = min(per_lane, CAPACITATE_MAX_PE_BANDA_PE_ZI.get(kind, CAPACITATE_MAX_IMPLICITA))
    return int(round(per_lane * lanes_toward_billboard(tags), -2))


def centrality_for(road: dict) -> float:
    """Factorul de centralitate din reteaua de strazi (centrality.py); 1.0 daca nu e calculat."""
    from centrality import factor_for_way
    f = factor_for_way(road.get("id"))
    return 1.0 if f is None else f


def estimate_speed_kmh(tags: dict) -> float:
    return parse_maxspeed(tags.get("maxspeed")) or VITEZA_IMPLICITA_KMH


def estimate_visible_seconds(speed_kmh: float, billboard_height_m: float = 4) -> float:
    """Timpul in care soferul parcurge distanta de la care panoul se poate citi."""
    distance = DISTANTA_CITIRE_M_PENTRU_4M * billboard_height_m / 4
    return distance / (speed_kmh / 3.6)


def analyze_coordinates(lat: float, lon: float, billboard_height_m: float = 4) -> dict:
    """Tot ce aflam doar din coordonate, pentru un panou."""
    ways = roads_near(lat, lon, 50) or roads_near(lat, lon, 150)
    road = billboard_road(lat, lon, ways)
    if road is None:
        raise RuntimeError(f"Niciun drum auto langa {lat}, {lon}")
    tags = road.get("tags", {})
    speed = estimate_speed_kmh(tags)
    centrality = centrality_for(road)
    return {
        "road_name": tags.get("name", "(fara nume)"),
        "road_type": road_type(tags),
        "lanes": lane_count(tags),
        "lanes_toward_billboard": lanes_toward_billboard(tags),
        "oneway": tags.get("oneway") in ("yes", "-1"),
        "centrality_factor": centrality,
        "distance_to_road_m": round(distance_to_way_m(lat, lon, road), 1),
        "speed_kmh": speed,
        "daily_cars_est": estimate_daily_cars(tags, centrality),
        "visible_seconds": round(estimate_visible_seconds(speed, billboard_height_m), 2),
        "occluded_pct": PROCENT_ACOPERIT_IMPLICIT,
    }


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "prefetch":
        print(f"Descarc drumurile din zona {ZONA_DEMO} (poate dura 1-5 minute)...")
        print(f"Gata: {prefetch_area()} drumuri salvate in {_area_file(ZONA_DEMO)}")
        from centrality import compute_for_area
        print("Calculez centralitatea strazilor (o data, ~4 minute)...")
        compute_for_area(_area_file(ZONA_DEMO))
    else:
        print("Folosire: python3 scoring/traffic.py prefetch")
