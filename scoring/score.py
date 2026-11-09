"""Scorul SightLine: care locatie are cel mai mic cost pe impresie reala.

Formulele sunt cele din docs/scor.md:
    secunde_necesare    = cuvinte_text * SECUNDE_PE_CUVANT
    factor_vizibilitate = (1 - procent_acoperit/100) * min(1, secunde_vizibile / secunde_necesare)
    impresii_reale_luna = masini_pe_zi * 30 * factor_vizibilitate
    cost_impresie_reala = pret_lunar_eur / impresii_reale_luna

Pretul lunar poate fi un numar exact (3000) sau un interval estimat ((2500, 3500)).
Pentru interval, clasamentul foloseste mijlocul intervalului, iar costul se raporteaza
si ca interval (minim / maxim).

Intrare: un JSON cu coordonate + pret (vezi scoring/input_demo.json).
Rulare:  python3 scoring/score.py [cale/input.json]
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Union

# Parametru de calibrat (secunde necesare pentru a citi un cuvant).
SECUNDE_PE_CUVANT = float(os.getenv("SECUNDE_PE_CUVANT", "0.3"))
ZILE_PE_LUNA = 30

Pret = Union[float, tuple[float, float]]


@dataclass
class Location:
    location_id: str
    lat: float
    lon: float
    monthly_price_eur: Pret          # numar exact sau (min, max)
    daily_cars_est: float            # din traffic_<id>.json
    visible_seconds: float           # din results_<id>.json (Unity)
    occluded_pct: float              # din results_<id>.json (Unity), 0..100
    address: str = ""


def price_range(price: Pret) -> tuple[float, float]:
    """Intoarce (min, max) pentru un pret exact sau un interval."""
    if isinstance(price, (int, float)):
        lo = hi = float(price)
    else:
        lo, hi = (float(p) for p in price)
        if lo > hi:
            lo, hi = hi, lo
    if lo < 0:
        raise ValueError(f"pret negativ: {price}")
    return lo, hi


def count_words(text: str) -> int:
    return len(text.split())


def visibility_factor(visible_seconds: float, occluded_pct: float, total_words: int) -> float:
    occluded = min(max(occluded_pct, 0.0), 100.0)
    needed = total_words * SECUNDE_PE_CUVANT
    time_part = 1.0 if needed <= 0 else min(1.0, max(visible_seconds, 0.0) / needed)
    return (1 - occluded / 100) * time_part


def readable_words(visible_seconds: float, total_words: int) -> int:
    """Cate cuvinte apuca soferul sa citeasca in secundele vizibile."""
    if visible_seconds <= 0:
        return 0
    return min(total_words, math.floor(visible_seconds / SECUNDE_PE_CUVANT + 1e-9))


def score_location(loc: Location, total_words: int) -> dict:
    factor = visibility_factor(loc.visible_seconds, loc.occluded_pct, total_words)
    impressions = loc.daily_cars_est * ZILE_PE_LUNA * factor
    lo, hi = price_range(loc.monthly_price_eur)
    mid = (lo + hi) / 2
    if impressions > 0:
        cost = mid / impressions
        cost_min, cost_max = lo / impressions, hi / impressions
    else:
        # Panoul nu e vazut deloc: cost infinit, merge la coada clasamentului.
        cost = cost_min = cost_max = None
    return {
        "location_id": loc.location_id,
        "address": loc.address,
        "lat": loc.lat,
        "lon": loc.lon,
        "monthly_price_eur": mid,
        "monthly_price_range_eur": [lo, hi],
        "daily_cars_est": loc.daily_cars_est,
        "visible_seconds": loc.visible_seconds,
        "occluded_pct": loc.occluded_pct,
        "readable_words": readable_words(loc.visible_seconds, total_words),
        "total_words": total_words,
        "visibility_factor": round(factor, 4),
        "real_impressions_month": round(impressions),
        "cost_per_real_impression_eur": cost,
        "cost_range_eur": [cost_min, cost_max],
    }


def rank_locations(locations: list[Location], creative_text: str) -> list[dict]:
    """Calculeaza scorul pentru fiecare locatie si le sorteaza (cea mai buna prima)."""
    if not locations:
        raise ValueError("lista de locatii e goala")
    words = count_words(creative_text)
    scored = [score_location(loc, words) for loc in locations]
    scored.sort(key=lambda s: (s["cost_per_real_impression_eur"] is None,
                               s["cost_per_real_impression_eur"] or 0.0))
    for i, s in enumerate(scored, start=1):
        s["rank"] = i
    return scored


def best_location(ranked: list[dict]) -> dict | None:
    """Prima locatie din clasament, sau None daca niciuna nu e vizibila."""
    best = ranked[0]
    return best if best["cost_per_real_impression_eur"] is not None else None


def advantage_ratio(ranked: list[dict]) -> float | None:
    """De cate ori mai multe impresii reale pe euro da castigatorul fata de cea mai slaba locatie vizibila."""
    costs = [s["cost_per_real_impression_eur"] for s in ranked if s["cost_per_real_impression_eur"]]
    if len(costs) < 2:
        return None
    return costs[-1] / costs[0]


# ---------- citire din fisierele contractelor (data/) ----------

def load_location(data_dir: Path, location_id: str, price: Pret) -> Location:
    """Construieste o Location din scene_config_, results_ si traffic_<id>.json."""
    def read(name: str) -> dict:
        return json.loads((data_dir / f"{name}_{location_id}.json").read_text(encoding="utf-8"))

    scene, results, traffic = read("scene_config"), read("results"), read("traffic")
    return Location(
        location_id=location_id,
        lat=scene["lat"],
        lon=scene["lon"],
        address=scene.get("address", ""),
        monthly_price_eur=price,
        daily_cars_est=traffic["daily_cars_est"],
        visible_seconds=results["visible_seconds"],
        occluded_pct=results["occluded_pct"],
    )


# ---------- din coordonate ----------

INPUT_IMPLICIT = Path(__file__).resolve().parent / "input_demo.json"


def normalize_input(data: dict) -> dict:
    """Accepta si formatul trimis de site (web/index.html: billboardSize, id, monthlyPriceEur)."""
    size = data.get("billboard_size_m") or data.get("billboardSize") or {}
    locations = []
    for i, item in enumerate(data.get("locations") or []):
        price = item.get("monthly_price_eur", item.get("monthlyPriceEur"))
        if price is None or item.get("lat") is None or item.get("lon") is None:
            raise ValueError(f"locatia {i + 1}: lipsesc lat, lon sau pretul")
        locations.append({
            "location_id": str(item.get("location_id") or item.get("id") or chr(ord("A") + i)),
            "lat": float(item["lat"]),
            "lon": float(item["lon"]),
            "monthly_price_eur": price,
            # procentul acoperit masurat de P1 (web/sightline-view.js); lipsa -> estimarea din traffic.py
            "occluded_pct": (None if item.get("occluded_pct") is None
                             else min(max(float(item["occluded_pct"]), 0.0), 100.0)),
        })
    if not locations:
        raise ValueError("nu a fost trimisa nicio locatie")
    return {
        "creative_text": data.get("creative_text") or data.get("creativeText") or "",
        "billboard_size_m": {"width": float(size.get("width", 12)), "height": float(size.get("height", 4))},
        "locations": locations,
    }


def locations_from_input(data: dict, roads: dict | None = None) -> list[Location]:
    """Intrare: doar coordonate + pret. Restul se estimeaza din OpenStreetMap (traffic.py).

    Daca se da `roads`, se completeaza cu informatiile despre strada pentru fiecare location_id.
    """
    from traffic import analyze_coordinates

    data = normalize_input(data)
    height = data["billboard_size_m"]["height"]
    locations = []
    for item in data["locations"]:
        lat, lon = item["lat"], item["lon"]
        price = item["monthly_price_eur"]
        if isinstance(price, list):
            price = tuple(price)
        try:
            info = analyze_coordinates(lat, lon, height)
        except RuntimeError as e:
            # un pin cu probleme (fara strada langa el, OSM indisponibil) nu strica restul:
            # are 0 masini, deci ajunge ultimul in clasament, cu motivul in "error"
            print(f"  {item['location_id']}: EROARE {e}")
            info = {"road_name": "(nicio strada gasita langa pin)", "road_type": "-", "lanes": 0,
                    "lanes_toward_billboard": 0, "centrality_factor": 1.0, "distance_to_road_m": None,
                    "speed_kmh": 0, "daily_cars_est": 0, "visible_seconds": 0, "occluded_pct": 100,
                    "error": str(e)}
        info["occluded_source"] = "estimat" if item["occluded_pct"] is None else "masurat"
        if roads is not None:
            roads[item["location_id"]] = info
        locations.append(Location(
            location_id=item["location_id"],
            lat=lat,
            lon=lon,
            monthly_price_eur=price,
            daily_cars_est=info["daily_cars_est"],
            visible_seconds=info["visible_seconds"],
            occluded_pct=info["occluded_pct"] if item["occluded_pct"] is None else item["occluded_pct"],
            address=info["road_name"],
        ))
        if "error" in info:
            continue
        print(f"  {locations[-1].location_id}: {info['road_name']} ({info['road_type']}, "
              f"{info['lanes']} benzi, {info['lanes_toward_billboard']:g} pe sensul panoului, "
              f"factor retea {info['centrality_factor']:.2f}, {info['speed_kmh']:.0f} km/h, "
              f"la {info['distance_to_road_m']} m de pin) -> {info['daily_cars_est']} masini/zi")
    return locations


def analyze(data: dict) -> dict:
    """Coordonate + pret -> clasament si castigator, ca JSON (folosit de site prin server.py)."""
    roads: dict = {}
    locations = locations_from_input(data, roads)
    ranked = rank_locations(locations, normalize_input(data)["creative_text"])
    for s in ranked:
        road = roads[s["location_id"]]
        s["road_name"] = road["road_name"]
        s["road_type"] = road["road_type"]
        s["lanes"] = road["lanes"]
        s["lanes_toward_billboard"] = road["lanes_toward_billboard"]
        s["centrality_factor"] = road["centrality_factor"]
        s["speed_kmh"] = road["speed_kmh"]
        s["visible_surface_pct"] = 100 - s["occluded_pct"]
        s["occluded_source"] = road["occluded_source"]
        if "error" in road:
            s["error"] = road["error"]
            s["visible_surface_pct"] = 0
    best = best_location(ranked)
    return {
        "best_location_id": best["location_id"] if best else None,
        "best": best,
        "locations": ranked,
        "estimates_note": ("Masinile pe zi sunt estimate din tipul drumului si benzi (OpenStreetMap). "
                           + ("Suprafata vizibila e masurata cu raze de la 1,2 m prin cladirile din OpenStreetMap si modelul 3D Google; "
                              "secundele vizibile sunt estimate din viteza."
                              if all(s["occluded_source"] == "masurat" for s in ranked) else
                              "Secundele vizibile si suprafata vizibila sunt aproximari.")),
    }


def print_report(ranked: list[dict]) -> None:
    print()
    print(f"{'#':<3}{'ID':<4}{'lat':>9}{'lon':>10}{'masini/zi':>11}{'pret EUR':>12}"
          f"{'impresii/luna':>15}{'EUR/impresie':>14}")
    for s in ranked:
        lo, hi = s["monthly_price_range_eur"]
        pret = f"{lo:.0f}" if lo == hi else f"{lo:.0f}-{hi:.0f}"
        cost = s["cost_per_real_impression_eur"]
        cost_txt = "nevizibil" if cost is None else f"{cost:.4f}"
        print(f"{s['rank']:<3}{s['location_id']:<4}{s['lat']:>9.4f}{s['lon']:>10.4f}"
              f"{s['daily_cars_est']:>11.0f}{pret:>12}{s['real_impressions_month']:>15}{cost_txt:>14}")

    best = best_location(ranked)
    if best is None:
        print("\nNiciun panou nu e vizibil.")
        return
    cmin, cmax = best["cost_range_eur"]
    print(f"\nCastigator: {best['location_id']} {best['address']}")
    print(f"Coordonate: {best['lat']:.6f}, {best['lon']:.6f}")
    interval = "" if cmin == cmax else f" (interval {cmin:.4f}-{cmax:.4f})"
    print(f"Cost pe impresie reala: {best['cost_per_real_impression_eur']:.4f} EUR{interval}")

    # Toate criteriile care intra in formula, pentru castigator.
    lo, hi = best["monthly_price_range_eur"]
    pret_txt = f"{lo:.0f} EUR" if lo == hi else f"{lo:.0f}-{hi:.0f} EUR (folosit mijlocul: {best['monthly_price_eur']:.0f} EUR)"
    print(f"Pret lunar:               {pret_txt}")
    print(f"Masini pe zi (estimare):  {best['daily_cars_est']:.0f}")
    print(f"Secunde vizibile:         {best['visible_seconds']:.2f} s")
    print(f"Suprafata vizibila:       {100 - best['occluded_pct']:.0f}%")
    print(f"Impresii reale pe luna:   {best['real_impressions_month']} "
          f"({best['daily_cars_est']:.0f} x {ZILE_PE_LUNA} x {best['visibility_factor']:.4f})")


def main(argv: list[str]) -> None:
    input_path = Path(argv[1]) if len(argv) > 1 else INPUT_IMPLICIT
    data = json.loads(input_path.read_text(encoding="utf-8"))
    print(f"Intrare: {input_path}")
    print("Strazi gasite in OpenStreetMap:")
    locations = locations_from_input(data)
    print_report(rank_locations(locations, data.get("creative_text", "")))


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main(sys.argv)
