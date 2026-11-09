"""Cat de "centrala" e fiecare strada in reteaua orasului (betweenness centrality).

Idee: simulam multe trasee cele mai rapide intre puncte din zona si numaram cate
trec pe fiecare strada. Strazile prin care trec multe trasee au, de regula, trafic
mai mare decat altele de acelasi tip si cu aceleasi benzi. Metoda e folosita in
studiile de trafic ca aproximare cand nu exista numaratori reale.

Rezultatul e un FACTOR pe strada (0.5 .. 2.0), relativ la mediana strazilor de
acelasi tip: 1.0 = tipic pentru tipul lui, 2.0 = mult mai circulata, 0.5 = ocolita.

Se calculeaza o singura data pe zona descarcata (data/cache/overpass_area_*.json)
si se salveaza in data/cache/centrality_*.json.

Rulare:  python3 scoring/centrality.py
"""

from __future__ import annotations

import heapq
import json
import math
import random
import time
from bisect import bisect_left as _bisect_left
from bisect import bisect_right as _bisect_right
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"

# Viteze tipice (km/h) folosite DOAR pentru a alege traseul cel mai rapid.
VITEZA_TRASEU_KMH = {
    "motorway": 90, "trunk": 70, "primary": 50, "secondary": 40, "tertiary": 35,
    "unclassified": 30, "residential": 25, "living_street": 10,
}
NUMAR_SURSE = 2000         # cate puncte de plecare simulam (esantion, determinist)
SEED = 42
FACTOR_MIN, FACTOR_MAX = 0.5, 2.0


def _road_type(tags: dict) -> str:
    return tags.get("highway", "").removesuffix("_link")


def _segment_m(a: dict, b: dict) -> float:
    k = 111_320.0
    dy = (b["lat"] - a["lat"]) * k
    dx = (b["lon"] - a["lon"]) * k * math.cos(math.radians((a["lat"] + b["lat"]) / 2))
    return math.hypot(dx, dy)


def build_graph(ways: list[dict]):
    """Graf orientat: noduri = intersectii si capete de strada, muchii = bucati de strada intre ele.

    Punctele intermediare (doar forma curbei) sunt comasate, ca graful sa fie mic.
    Strazile cu sens unic au muchii doar in sensul de mers.
    Intoarce (adiacenta, muchii_pe_strada).
    """
    # un punct e nod daca e capat de strada sau apare de mai multe ori (intersectie)
    seen: dict[tuple, int] = {}
    for way in ways:
        for p in way["geometry"]:
            k = (p["lat"], p["lon"])
            seen[k] = seen.get(k, 0) + 1

    node_id: dict[tuple, int] = {}
    adj: list[list[tuple[int, float, int]]] = []
    edges_per_way: dict[int, int] = {}

    def nid(p: dict) -> int:
        key = (p["lat"], p["lon"])
        if key not in node_id:
            node_id[key] = len(adj)
            adj.append([])
        return node_id[key]

    def pieces(g: list[dict]):
        """Imparte strada la noduri: (punct_start, punct_final, lungime_m)."""
        start, length = g[0], 0.0
        for i in range(1, len(g)):
            length += _segment_m(g[i - 1], g[i])
            k = (g[i]["lat"], g[i]["lon"])
            if i == len(g) - 1 or seen[k] > 1:
                yield start, g[i], length
                start, length = g[i], 0.0

    for way in ways:
        tags = way.get("tags", {})
        speed = VITEZA_TRASEU_KMH.get(_road_type(tags), 25) / 3.6
        oneway = tags.get("oneway")
        count = 0
        for a, b, length in pieces(way["geometry"]):
            u, v = nid(a), nid(b)
            if u == v:
                continue
            t = length / speed
            if oneway != "-1":           # sensul desenat (sau ambele sensuri)
                adj[u].append((v, t, way["id"]))
                count += 1
            if oneway != "yes":          # sensul invers (dublu sens sau oneway=-1)
                adj[v].append((u, t, way["id"]))
                count += 1
        edges_per_way[way["id"]] = max(count, 1)
    return adj, edges_per_way


def way_betweenness(ways: list[dict], sources: int = NUMAR_SURSE, seed: int = SEED) -> dict[int, float]:
    """Betweenness pe muchii (algoritmul Brandes, cu esantion de surse), mediat pe strada."""
    adj, edges_per_way = build_graph(ways)
    n = len(adj)
    rng = random.Random(seed)
    srcs = rng.sample(range(n), min(sources, n))
    acc: dict[int, float] = {}

    for s in srcs:
        dist = {s: 0.0}
        sigma = {s: 1.0}
        preds: dict[int, list[tuple[int, int]]] = {s: []}
        order = []
        heap = [(0.0, s)]
        done = set()
        while heap:
            d, v = heapq.heappop(heap)
            if v in done:
                continue
            done.add(v)
            order.append(v)
            for w, t, wid in adj[v]:
                nd = d + t
                old = dist.get(w)
                if old is None or nd < old - 1e-9:
                    dist[w] = nd
                    sigma[w] = sigma[v]
                    preds[w] = [(v, wid)]
                    heapq.heappush(heap, (nd, w))
                elif abs(nd - old) <= 1e-9:
                    sigma[w] += sigma[v]
                    preds[w].append((v, wid))
        delta = dict.fromkeys(order, 0.0)
        for w in reversed(order):
            for v, wid in preds[w]:
                c = sigma[v] / sigma[w] * (1.0 + delta[w])
                acc[wid] = acc.get(wid, 0.0) + c
                delta[v] += c

    # media pe muchiile (orientate) ale strazii = flux tipic pe un sens
    return {wid: acc.get(wid, 0.0) / edges_per_way[wid] for wid in edges_per_way}


def percentile_to_factor(p: float) -> float:
    """0 -> 0.5 (cea mai ocolita), 0.5 -> 1.0 (tipica), 1 -> 2.0 (cea mai circulata)."""
    return FACTOR_MIN * (FACTOR_MAX / FACTOR_MIN) ** p


def centrality_factors(ways: list[dict], betweenness: dict[int, float]) -> dict[int, float]:
    """Factor dupa pozitia strazii in clasamentul strazilor de ACELASI TIP.

    Folosim percentila (nu raportul fata de mediana) pentru ca multe strazi mici sunt
    fundaturi cu valoare 0, iar raportul ar exploda.
    """
    by_type: dict[str, list[int]] = {}
    for way in ways:
        by_type.setdefault(_road_type(way.get("tags", {})), []).append(way["id"])
    factors = {}
    for ids in by_type.values():
        values = sorted(betweenness.get(i, 0.0) for i in ids)
        n = len(values)
        for i in ids:
            b = betweenness.get(i, 0.0)
            # percentila cu ex-aequo la mijloc: (sub + jumatate din egale) / n
            below = _bisect_left(values, b)
            equal = _bisect_right(values, b) - below
            p = 0.5 if n == 1 else (below + (equal - 1) / 2) / (n - 1)
            factors[i] = round(percentile_to_factor(p), 3)
    return factors


def _factors_file(area_file: Path) -> Path:
    return area_file.with_name(area_file.name.replace("overpass_area_", "centrality_"))


def compute_for_area(area_file: Path) -> dict[int, float]:
    data = json.loads(area_file.read_text(encoding="utf-8"))
    ways = [e for e in data.get("elements", []) if e.get("type") == "way" and e.get("geometry")]
    t0 = time.time()
    factors = centrality_factors(ways, way_betweenness(ways))
    out = _factors_file(area_file)
    out.write_text(json.dumps({str(k): v for k, v in factors.items()}), encoding="utf-8")
    print(f"{len(ways)} strazi, {time.time() - t0:.0f} s -> {out}")
    return factors


_loaded: dict[int, float] | None = None


def factor_for_way(way_id: int | None) -> float | None:
    """Factorul unei strazi, daca e intr-o zona calculata; altfel None."""
    global _loaded
    if way_id is None:
        return None
    if _loaded is None:
        _loaded = {}
        for f in CACHE_DIR.glob("centrality_*.json"):
            _loaded.update({int(k): v for k, v in json.loads(f.read_text(encoding="utf-8")).items()})
    return _loaded.get(way_id)


if __name__ == "__main__":
    areas = sorted(CACHE_DIR.glob("overpass_area_*.json"))
    if not areas:
        print("Nu exista zona descarcata. Ruleaza intai: python3 scoring/traffic.py prefetch")
    for area in areas:
        compute_for_area(area)
