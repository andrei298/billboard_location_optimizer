/* SightLine · vederea șoferului (P1 Victoria)
 *
 * Se leagă de pagina lui P4 (results.html) fără să-i schimbe codul:
 *  - citește campania din sessionStorage["sightline-campaign"] (pinuri, dimensiune panou, reclama);
 *  - pune în caseta ".scene" poze reale Street View cu panoul desenat la coordonatele pinului;
 *  - ascultă butoanele existente [data-distance] (150 / 80 / 30 m);
 *  - calculează cât din panou e acoperit de clădiri și copaci (OpenStreetMap), la 1,2 m (ochii șoferului).
 *
 * Rezultatul vizibilității se pune în sessionStorage["sightline-visibility"] (pe id-ul pinului),
 * ca scoring/ să-l poată folosi în locul valorii implicite.
 *
 * Cheia Google se citește din web/config.local.js (window.SIGHTLINE_CONFIG.googleMapsApiKey), nu se scrie în cod.
 * Pozele Google nu se salvează; răspunsurile OpenStreetMap se păstrează în localStorage.
 */
(function () {
  "use strict";

  const DISTANCES = [150, 80, 30];
  const APPROACH_M = 220;     // cât mergem înapoi pe stradă de la panou
  const EYE_H = 1.2;          // ochii șoferului
  const CAM_H = 2.5;          // camera Street View e pe acoperișul mașinii Google
  const IMG_W = 640, IMG_H = 480, FOV = 70;
  const TREE_R = 3.5, TREE_LOW = 3, TREE_TOP = 12;   // coroana unui copac de stradă
  const DEFAULT_BUILDING_H = 12;
  const MOUNTS = { unipol: "Unipol", two: "2 stâlpi", wall: "Pe clădire" };

  const LOG = [];   // jurnal scurt, pentru depanare: SightLineView.log
  const log = t => { LOG.push(`${new Date().toLocaleTimeString()} ${t}`); if (LOG.length > 200) LOG.shift(); };
  // așteaptă o promisiune cel mult ms milisecunde
  const withTimeout = (p, ms, what) => Promise.race([p, new Promise((_, rej) => setTimeout(() => rej(new Error(`${what}: peste ${Math.round(ms / 1000)} s`)), ms))]);
  const rad = d => d * Math.PI / 180, deg = r => r * 180 / Math.PI;
  const sub = (a, b) => ({ x: a.x - b.x, y: a.y - b.y }), add = (a, b) => ({ x: a.x + b.x, y: a.y + b.y });
  const mul = (a, s) => ({ x: a.x * s, y: a.y * s }), len = a => Math.hypot(a.x, a.y);
  const norm = a => mul(a, 1 / (len(a) || 1)), dot2 = (a, b) => a.x * b.x + a.y * b.y;

  // ---------------------------------------------------------------- OpenStreetMap
  const osmMem = {};
  let osmQueue = Promise.resolve();
  function overpass(lat, lon) { return overpassNow(lat, lon); }
  // cererile mari către Overpass merg una câte una (altfel răspunde 429); cele mici, pentru clădiri, în paralel
  function overpassSerial(q, totalMs, rounds) {
    const job = osmQueue.then(() => overpassQuery(q, totalMs, rounds));
    osmQueue = job.catch(() => null);
    return job;
  }
  const OVERPASS_EPS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
                        "https://maps.mail.ru/osm/tools/overpass/api/interpreter"];
  // o cerere Overpass, încercând pe rând serverele, cu limită totală de timp
  async function overpassQuery(q, totalMs, rounds) {
    const t0 = Date.now();
    for (let i = 0; i < OVERPASS_EPS.length * rounds; i++) {
      const left = totalMs - (Date.now() - t0); if (left <= 500) break;
      if (i >= OVERPASS_EPS.length) await new Promise(r => setTimeout(r, Math.min(2000, left / 4)));
      const ctl = new AbortController(), timer = setTimeout(() => ctl.abort(), Math.max(500, totalMs - (Date.now() - t0)));
      try {
        const r = await fetch(OVERPASS_EPS[i % OVERPASS_EPS.length], { method: "POST", body: "data=" + encodeURIComponent(q),
          headers: { "Content-Type": "application/x-www-form-urlencoded" }, signal: ctl.signal });
        if (r.ok) return await r.json();
      } catch (e) {} finally { clearTimeout(timer); }
    }
    return null;
  }

  // străzile din cache-ul serverului (scoring/server.py, GET /strazi): instant, fără internet; null în afara zonei
  async function roadsFromServer(lat, lon) {
    const ctl = new AbortController(), timer = setTimeout(() => ctl.abort(), 4000);
    try {
      const r = await fetch(`/strazi?lat=${lat}&lon=${lon}&r=${APPROACH_M + 40}`, { signal: ctl.signal });
      if (!r.ok) return null;
      const j = await r.json();
      return j && j.elements && j.elements.length ? j.elements : null;
    } catch (e) { return null; } finally { clearTimeout(timer); }
  }

  async function overpassNow(lat, lon) {
    const key = `sl_osm3_${lat.toFixed(5)}_${lon.toFixed(5)}`;
    if (osmMem[key]) return osmMem[key];
    try { const c = localStorage.getItem(key); if (c) return (osmMem[key] = JSON.parse(c)); } catch (e) {}
    const around = APPROACH_M + 20;
    const qObstacles = `way(around:${around},${lat},${lon})["building"];
      node(around:${around},${lat},${lon})["natural"="tree"];
      way(around:${around},${lat},${lon})["natural"="tree_row"];`;
    let json = null;
    const roads = await roadsFromServer(lat, lon);
    if (roads) {
      log(`străzi din cache-ul serverului: ${roads.length}`);
      // clădirile și copacii: cerere separată, cu limită; fără ele mergem mai departe (marcat)
      const obs = await overpassQuery(`[out:json][timeout:8];(${qObstacles});out geom;`, 6000, 1);
      json = { elements: roads.concat(obs ? obs.elements || [] : []), noBuildings: !obs };
      if (!obs) log("clădirile OSM n-au venit la timp: continui fără ele");
    } else {
      const q = `[out:json][timeout:40];(
        way(around:${APPROACH_M + 40},${lat},${lon})["highway"~"^(motorway|trunk|primary|secondary|tertiary|unclassified|residential|motorway_link|trunk_link|primary_link|secondary_link|tertiary_link|living_street|service|road)$"];
        ${qObstacles}
      );out geom;`;
      json = await overpassSerial(q, 45000, 2);
      if (!json) throw new Error("OpenStreetMap (Overpass) nu răspunde");
    }
    osmMem[key] = json;
    if (!json.noBuildings) { try { localStorage.setItem(key, JSON.stringify(json)); } catch (e) {} }
    return json;
  }

  function buildingHeight(tags) {
    const h = parseFloat(tags.height);
    if (Number.isFinite(h) && h > 0) return h;
    const lv = parseFloat(tags["building:levels"]);
    if (Number.isFinite(lv) && lv > 0) return lv * 3.2 + 1;
    return DEFAULT_BUILDING_H;
  }

  function pointInPoly(p, poly) {
    let inside = false;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const a = poly[i], b = poly[j];
      if ((a.y > p.y) !== (b.y > p.y) && p.x < (b.x - a.x) * (p.y - a.y) / (b.y - a.y) + a.x) inside = !inside;
    }
    return inside;
  }

  // ---------------------------------------------------------------- Scena: panou + traseul șoferului
  // Totul în metri, față de pin (x = est, y = nord).
  function buildScene(json, pin, size, mount) {
    const kx = 111320 * Math.cos(rad(pin.lat)), ky = 111320;
    const L = g => ({ x: (g.lon - pin.lon) * kx, y: (g.lat - pin.lat) * ky });
    const G = v => ({ lat: pin.lat + v.y / ky, lon: pin.lon + v.x / kx });
    const els = json.elements || [];

    // drumurile de serviciu contează doar ca legătură (nu parcări, alei de casă)
    const roadsRaw = els.filter(e => e.type === "way" && e.tags && e.tags.highway && e.geometry && e.geometry.length > 1
      && !(e.tags.highway === "service" && /^(parking_aisle|driveway|drive-through|emergency_access)$/.test(e.tags.service || "")));
    if (!roadsRaw.length) throw new Error("nicio stradă lângă pin");
    const buildings = els.filter(e => e.type === "way" && e.tags && e.tags.building && e.geometry && e.geometry.length > 2)
      .map(e => ({ poly: e.geometry.map(L), h: buildingHeight(e.tags) }));
    const trees = [];
    els.forEach(e => {
      if (e.type === "node" && e.tags && e.tags.natural === "tree") trees.push(L(e));
      if (e.type === "way" && e.tags && e.tags.natural === "tree_row" && e.geometry) {
        const pts = e.geometry.map(L);
        for (let i = 0; i < pts.length - 1; i++) {
          const n = Math.max(1, Math.round(len(sub(pts[i + 1], pts[i])) / 7));
          for (let k = 0; k < n; k++) trees.push(add(pts[i], mul(sub(pts[i + 1], pts[i]), k / n)));
        }
      }
    });

    const makeWays = flipped => roadsRaw.map(e => {
      const pts = e.geometry.map(L);
      const ow = e.tags.oneway;
      if (ow === "-1") pts.reverse();
      const oneway = ow === "yes" || ow === "-1" || e.tags.junction === "roundabout";
      if (flipped && !oneway) pts.reverse();
      return { tags: e.tags, pts, oneway };
    });
    const nearest = ways => {
      let best = { d: 1e9 };
      ways.forEach((w, wi) => w.pts.slice(0, -1).forEach((a, i) => {
        const b = w.pts[i + 1], ab = sub(b, a), l2 = dot2(ab, ab);
        const t = l2 ? Math.max(0, Math.min(1, -dot2(a, ab) / l2)) : 0;
        const q = add(a, mul(ab, t));
        // străzile mari au prioritate (un panou de pe bulevard nu se leagă de o străduță laterală),
        // iar podurile / pasajele doar dacă pinul e chiar pe ele
        const cls = w.tags.highway || "", bridge = w.tags.bridge || w.tags.tunnel || (parseInt(w.tags.layer) || 0) !== 0;
        const bonus = /^(motorway|trunk|primary)/.test(cls) ? 10 : /^secondary/.test(cls) ? 8 : /^tertiary/.test(cls) ? 4 : /^(service|road)$/.test(cls) ? -15 : 0;
        const sc = len(q) - bonus + (bridge && len(q) > 3 ? 40 : 0);
        if (sc < (best.sc ?? 1e9)) best = { d: len(q), sc, wi, i, q, dir: norm(ab) };
      }));
      return best;
    };

    // mergem înapoi pe stradă (și pe străzile legate) până la 220 m
    function walk(flipped) {
      const ways = makeWays(flipped), best = nearest(ways), road = ways[best.wi];
      const back = [best.q]; let cur = road.pts, idx = best.i, acc = 0; const used = new Set([best.wi]);
      while (acc < APPROACH_M) {
        const last = back[back.length - 1];
        if (idx >= 0) {
          const v = cur[idx], d = len(sub(v, last));
          if (acc + d >= APPROACH_M) { back.push(add(last, mul(sub(v, last), (APPROACH_M - acc) / (d || 1)))); break; }
          if (d > 0.01) { back.push(v); acc += d; }
          idx--; continue;
        }
        // la intersecție continuăm pe aceeași stradă (același nume), cu cea mai mică schimbare de direcție
        const s = cur[0], curName = ways[[...used].pop()].tags.name || "";
        const goingBack = norm(sub(cur[0], cur[1] || cur[0]));   // direcția în care mergem înapoi
        let found = -1, rev = false, bestScore = 1e9;
        ways.forEach((w, wi) => {
          if (used.has(wi)) return;
          let r = null;
          if (len(sub(w.pts[w.pts.length - 1], s)) < 2) r = false;
          else if (!w.oneway && len(sub(w.pts[0], s)) < 2) r = true;
          if (r === null) return;
          const pts = r ? w.pts.slice().reverse() : w.pts;
          const next = pts.length > 1 ? norm(sub(pts[pts.length - 2], s)) : goingBack;
          const turn = deg(Math.acos(Math.max(-1, Math.min(1, dot2(next, goingBack)))));
          const score = turn + ((w.tags.name || "") === curName ? 0 : 100);
          if (score < bestScore) { bestScore = score; found = wi; rev = r; }
        });
        // dacă nu se poate merge drept, urmăm strada de pe care vin mașinile (viraj de până la ~100°):
        // șoferul vede panoul și înainte să vireze
        if (found < 0 || bestScore >= 100 + 100) break;
        used.delete(found); used.add(found);
        cur = ways[found].pts.slice(); if (rev) cur.reverse(); idx = cur.length - 2;
      }
      back.reverse();
      // direcția de mers în dreptul panoului: din punctul aflat cu ~30 m înainte până la panou
      const end = back[back.length - 1];
      let from = back[0];
      for (let i = back.length - 2; i >= 0; i--) { from = back[i]; if (len(sub(end, back[i])) >= 30) break; }
      return { best, road, back, dir: norm(sub(end, from)) };
    }
    // circulație pe dreapta: alegem sensul în care pinul e în dreapta șoferului
    let wk = walk(false);
    const leftOfRoad = wk.dir.x * (-wk.best.q.y) - wk.dir.y * (-wk.best.q.x) > 0;
    if (!wk.road.oneway && leftOfRoad) wk = walk(true);
    // strada are prea puțin drum în fața panoului din sensul ăsta (ex. vine dintr-o fundătură)?
    // atunci panoul se întoarce spre mașinile din sensul opus, dacă de acolo au mai mult drum
    const reachOf = w => len(sub(w.back[0], w.best.q));
    if (!wk.road.oneway && reachOf(wk) < 150) {
      const alt = walk(!leftOfRoad);
      if (reachOf(alt) > reachOf(wk) + 30) wk = alt;
    }
    const { best, road, back, dir } = wk;
    const lanes = Math.min(Math.max(parseInt(road.tags.lanes) || 2, 1), 8);
    const right = { x: dir.y, y: -dir.x };
    const laneOff = road.oneway ? 0 : Math.min(lanes, 4) * 3.5 / 4;
    const drive = back.map((p, i) => {
      const d = i < back.length - 1 ? norm(sub(back[i + 1], p)) : dir;
      return add(p, mul({ x: d.y, y: -d.x }, laneOff));
    });

    // poziția și orientarea panoului
    // jumătatea carosabilului: din tag-ul width, altfel benzi + loc de parcare pe margine
    const wTag = parseFloat(road.tags.width);
    const parking = /^(primary|secondary|tertiary|trunk)/.test(road.tags.highway) ? 2.5 : 1.5;
    const roadHalf = Number.isFinite(wTag) && wTag > 3 ? wTag / 2 : lanes * 3.5 / 2 + parking;
    let c = { x: 0, y: 0 };                             // panoul stă exact pe pin…
    if (best.d < roadHalf + 1.5) {                      // …doar dacă pinul e pe carosabil, îl mut pe trotuar, pe partea pinului
      const side = best.d > 0.5 ? norm(mul(best.q, -1)) : right;
      c = add(best.q, mul(side, roadHalf + 2.5));
    }
    let n = mul(dir, -1);                               // fața spre mașinile care vin
    let mountedOn = null;
    let moved = 0;
    // panoul pe stâlp nu poate sta într-o clădire sau lipit de ea: căutăm cel mai apropiat loc liber de pin,
    // între marginea carosabilului și clădiri (tot panoul, cu ambele capete, la cel puțin 1,5 m de pereți)
    if (mount !== "wall") {
      const rr = { x: -n.y, y: n.x };
      const distToBuildings = p => {
        let m = 1e9;
        for (const b of buildings) {
          if (pointInPoly(p, b.poly)) return -1;
          for (let i = 0; i < b.poly.length - 1; i++) {
            const a = b.poly[i], ab = sub(b.poly[i + 1], a), l2 = dot2(ab, ab);
            const t = l2 ? Math.max(0, Math.min(1, dot2(sub(p, a), ab) / l2)) : 0;
            m = Math.min(m, len(sub(p, add(a, mul(ab, t)))));
          }
        }
        return m;
      };
      const W2 = (size.width || 12) / 2;
      const free = p => [-W2, -W2 / 2, 0, W2 / 2, W2].every(x => distToBuildings(add(p, mul(rr, x))) >= 1.5)
        && len(sub(p, best.q)) >= roadHalf + 1.5 - 0.01;
      if (!free(c)) {
        const out = best.d > 0.5 ? norm(mul(best.q, -1)) : right;   // dinspre drum spre pin
        let bestP = null, bestD = 1e9;
        for (let along = -24; along <= 24; along += 2) for (let off = roadHalf + 1.5; off <= roadHalf + 14; off += 0.5) {
          const p = add(add(best.q, mul(dir, along)), mul(out, off));
          const dd = len(sub(p, c));
          if (dd < bestD && free(p)) { bestD = dd; bestP = p; }
        }
        if (!bestP) throw new Error("Nu e loc liber pentru un panou pe stâlp lângă pin (clădiri lipite de stradă). Mută pinul sau alege „Pe clădire”.");
        moved = bestD; c = bestP;
      }
    }
    if (mount === "wall") {
      let bw = { d: 10 };   // panoul de clădire doar pe o fațadă aflată la cel mult 10 m de pin
      buildings.forEach((b, bi) => b.poly.forEach((a, i) => {
        const e = b.poly[(i + 1) % b.poly.length], ab = sub(e, a), l2 = dot2(ab, ab);
        if (l2 < 4) return;                           // pereți sub 2 m nu țin un panou
        const t = Math.max(0.05, Math.min(0.95, dot2(sub(c, a), ab) / l2));
        const q = add(a, mul(ab, t)), d = len(sub(c, q));
        if (d < bw.d) bw = { d, q, ab, bi };
      }));
      if (bw.q) {
        let nrm = norm({ x: -bw.ab.y, y: bw.ab.x });
        if (pointInPoly(add(bw.q, mul(nrm, 0.5)), buildings[bw.bi].poly)) nrm = mul(nrm, -1);
        c = add(bw.q, mul(nrm, 0.35)); n = nrm; mountedOn = bw.bi;
      } else {
        throw new Error("Nu e nicio clădire la pin (cel mai apropiat perete e la peste 10 m). Mută pinul pe fațada unei clădiri sau alege „Unipol” / „2 stâlpi”.");
      }
    }

    return {
      pin, L, G, c, n, r: { x: -n.y, y: n.x }, drive, buildings, trees, mountedOn,
      W: size.width || 12, H: size.height || 4,
      E: mount === "wall" ? Math.max(6, (size.height || 4) / 2 + 4) : 6, mount,
      roadName: road.tags.name || road.tags.highway, lanes, oneway: road.oneway, moved, noBuildings: !!json.noBuildings,
    };
  }

  // cât de departe de panou începe traseul (dacă strada e scurtă, nu există punct la 150 m)
  function reach(S) { return len(sub(S.drive[0], S.c)); }
  function hasPoint(S, d) { return reach(S) >= d - 10; }

  function viewpoint(S, d) {
    for (let i = 0; i < S.drive.length - 1; i++) {
      const a = S.drive[i], b = S.drive[i + 1];
      for (let s = 0; s <= 40; s++) {
        const p = add(a, mul(sub(b, a), s / 40));
        if (len(sub(p, S.c)) <= d) return p;
      }
    }
    return S.drive[0];
  }

  // ---------------------------------------------------------------- Vizibilitate (raze 2,5D prin clădiri și copaci OSM)
  function facePoints(S, nx, ny) {
    const pts = [];
    for (let iy = 0; iy < ny; iy++) for (let ix = 0; ix < nx; ix++) {
      const x = ((ix + 0.5) / nx - 0.5) * S.W, z = S.E + S.H / 2 - (iy + 0.5) / ny * S.H;
      pts.push({ p: add(S.c, mul(S.r, x)), z });
    }
    return pts;
  }

  function rayBlocked(S, eye, tgt) {
    const d = sub(tgt.p, eye), L2 = len(d);
    const hAt = t => EYE_H + (tgt.z - EYE_H) * t;
    const tEnd = 1 - 0.6 / (L2 || 1);                 // ultimele 60 cm = peretele pe care stă panoul
    for (const b of S.buildings) {
      const P = b.poly;
      for (let i = 0; i < P.length - 1; i++) {
        const a = P[i], e = sub(P[i + 1], a);
        const den = d.x * e.y - d.y * e.x; if (!den) continue;
        const w = sub(a, eye);
        const t = (w.x * e.y - w.y * e.x) / den, u = (w.x * d.y - w.y * d.x) / den;
        if (t > 0.002 && t < tEnd && u >= 0 && u <= 1 && hAt(t) < b.h) return true;
      }
    }
    for (const tr of S.trees) {
      const t0 = Math.max(0, Math.min(1, dot2(sub(tr, eye), d) / (L2 * L2)));
      if (len(sub(add(eye, mul(d, t0)), tr)) > TREE_R) continue;
      const half = Math.sqrt(Math.max(0, TREE_R * TREE_R - len(sub(add(eye, mul(d, t0)), tr)) ** 2)) / L2;
      const h1 = hAt(Math.max(0, t0 - half)), h2 = hAt(Math.min(tEnd, t0 + half));
      if (Math.max(h1, h2) >= TREE_LOW && Math.min(h1, h2) <= TREE_TOP && t0 < tEnd) return true;
    }
    return false;
  }

  function visibilityAt(S, eye) {
    const cells = facePoints(S, 8, 4);
    if (dot2(sub(eye, S.c), S.n) <= 0) return { pct: 0, cells: cells.map(() => false) };   // îl vezi din spate
    const ok = cells.map(t => !rayBlocked(S, eye, t));
    return { pct: 100 * ok.filter(Boolean).length / ok.length, cells: ok };
  }

  function visibility(S) {
    const byDistance = {};
    DISTANCES.forEach(d => { byDistance[d] = visibilityAt(S, viewpoint(S, d)); });
    // media pe tot traseul (din 10 în 10 m, între 200 și 20 m de panou), doar unde există stradă
    let sum = 0, n = 0;
    for (let d = 200; d >= 20; d -= 10) { if (!hasPoint(S, d) && n) continue; sum += visibilityAt(S, viewpoint(S, d)).pct; n++; }
    const visible = sum / (n || 1);
    return { byDistance, visible_pct: visible, occluded_pct: 100 - visible };
  }

  // ---------------------------------------------------------------- Google Street View
  let G = null, svc = null, gLoading = null;
  function loadGoogle(key) {   // o singură încărcare, chiar dacă o cer mai mulți pini deodată
    if (!gLoading) gLoading = loadGoogleNow(key).catch(e => { gLoading = null; throw e; });
    return gLoading;
  }
  async function loadGoogleNow(key) {
    if (G) return G;
    await new Promise((res, rej) => {
      window.__slGoogle = res;
      const s = document.createElement("script");
      s.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(key)}&v=weekly&loading=async&callback=__slGoogle`;
      s.onerror = () => rej(new Error("Google Maps nu s-a încărcat"));
      document.head.appendChild(s);
    });
    G = await google.maps.importLibrary("streetView");
    svc = new G.StreetViewService();
    return G;
  }

  async function findPano(S, d) {
    const vp = S.G(viewpoint(S, d));
    let res = null;
    for (const radius of [12, 25, 40]) {   // întâi strict pe stradă, ca să nu prindem o panoramă de pe o stradă laterală
      const opts = { location: { lat: vp.lat, lng: vp.lon }, radius, preference: G.StreetViewPreference.NEAREST };
      if (G.StreetViewSource && G.StreetViewSource.GOOGLE) opts.sources = [G.StreetViewSource.GOOGLE];
      else opts.source = G.StreetViewSource.OUTDOOR;
      try { res = (await svc.getPanorama(opts)).data; break; } catch (e) {}
    }
    if (!res) throw new Error("fără Street View");
    const pos = S.L({ lat: res.location.latLng.lat(), lon: res.location.latLng.lng() });
    const to = sub(S.c, pos), dist = len(to);
    return {
      pano: res.location.pano, date: res.imageDate, pos, dist,
      heading: (deg(Math.atan2(to.x, to.y)) + 360) % 360,
      pitch: deg(Math.atan2(S.E - CAM_H, dist)) * 0.6,
    };
  }

  // ---------------------------------------------------------------- Desenarea panoului în perspectivă
  function project(v, cam) {
    const h = rad(cam.heading), p = rad(cam.pitch);
    const fwd = [Math.sin(h) * Math.cos(p), Math.cos(h) * Math.cos(p), Math.sin(p)];
    const rgt = [Math.cos(h), -Math.sin(h), 0];
    const up = [-Math.sin(h) * Math.sin(p), -Math.cos(h) * Math.sin(p), Math.cos(p)];
    const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
    const z = dot(v, fwd); if (z < 0.5) return null;
    const f = (cam.W / 2) / Math.tan(rad(cam.fov) / 2);
    return { x: cam.W / 2 + f * dot(v, rgt) / z, y: cam.H / 2 - f * dot(v, up) / z, z };
  }

  // ---------------------------------------------------------------- Ce acoperă panoul, desenat în poză
  // Pentru fiecare pătrățel din grila de vizibilitate (8 × 4):
  //  - acoperit de clădire (0): punem înapoi pixelii din poză (clădirea rămâne în fața panoului);
  //  - acoperit parțial de copac (0 < v < 1): punem înapoi doar pixelii de frunziș din poză (verde, natural);
  //  - opțional (butonul „Zone acoperite”): culoare peste pătrățelele acoperite.
  function cellQuad(S, cam, ix, iy) {
    const top = S.E + S.H / 2, x0 = -S.W / 2 + S.W * ix / 8, x1 = x0 + S.W / 8, z0 = top - S.H * iy / 4, z1 = z0 - S.H / 4;
    const P = (x, z) => { const q = add(S.c, mul(S.r, x)), v = sub(q, cam.pos); return project([v.x, v.y, z + (cam.dz || 0) - CAM_H], cam); };
    const pts = [P(x0, z0), P(x1, z0), P(x1, z1), P(x0, z1)];
    return pts.some(p => !p) ? null : pts;
  }
  function inQuad(x, y, q) {
    let sign = 0;
    for (let i = 0; i < 4; i++) {
      const a = q[i], b = q[(i + 1) % 4], cr = (b.x - a.x) * (y - a.y) - (b.y - a.y) * (x - a.x);
      if (cr !== 0) { if (sign && Math.sign(cr) !== sign) return false; sign = Math.sign(cr); }
    }
    return true;
  }
  const isFoliage = (r, g, b) => (g > r + 6 && g > b + 4) || (g > 40 && g < 110 && r < g + 4 && b < g - 2 && r + g + b < 260);

  function drawOcclusion(g, S, cam, cells, original, showMask) {
    if (!cells || !original) return;
    let out;
    try { out = g.getImageData(0, 0, cam.W, cam.H); } catch (e) { return; }   // poza fără CORS: nu putem citi pixelii
    const o = original.data, d = out.data;
    const masks = [];
    cells.forEach((v, i) => {
      const val = v === true ? 1 : v === false ? 0 : Number(v);
      if (val >= 1) return;
      const q = cellQuad(S, cam, i % 8, Math.floor(i / 8)); if (!q) return;
      masks.push({ q, val });
      const xs = q.map(p => p.x), ys = q.map(p => p.y);
      const xa = Math.max(0, Math.floor(Math.min(...xs))), xb = Math.min(cam.W - 1, Math.ceil(Math.max(...xs)));
      const ya = Math.max(0, Math.floor(Math.min(...ys))), yb = Math.min(cam.H - 1, Math.ceil(Math.max(...ys)));
      for (let y = ya; y <= yb; y++) for (let x = xa; x <= xb; x++) {
        if (!inQuad(x + 0.5, y + 0.5, q)) continue;
        const k = (y * cam.W + x) * 4;
        if (val <= 0) { d[k] = o[k]; d[k + 1] = o[k + 1]; d[k + 2] = o[k + 2]; }
        else if (isFoliage(o[k], o[k + 1], o[k + 2])) {          // frunziș: se vede panoul printre frunze, cât spune procentul
          const a = 1 - val;
          d[k] = d[k] * val + o[k] * a; d[k + 1] = d[k + 1] * val + o[k + 1] * a; d[k + 2] = d[k + 2] * val + o[k + 2] * a;
        }
      }
    });
    g.putImageData(out, 0, 0);
    if (showMask) masks.forEach(({ q, val }) => {
      g.fillStyle = val <= 0 ? "rgb(229 57 53 / 45%)" : "rgb(255 179 0 / 40%)";
      g.beginPath(); q.forEach((p, i) => i ? g.lineTo(p.x, p.y) : g.moveTo(p.x, p.y)); g.closePath(); g.fill();
    });
  }

  function drawBoard(g, S, cam, img) {
    const { W, H, E } = S, bottom = E - H / 2, top = E + H / 2;
    // x = de-a lungul panoului, z = înălțime de la sol, b = în spatele feței
    const P = (x, z, b) => {
      const q = add(add(S.c, mul(S.r, x)), mul(S.n, -b)), v = sub(q, cam.pos);
      return project([v.x, v.y, z + (cam.dz || 0) - CAM_H], cam);
    };
    const f = (cam.W / 2) / Math.tan(rad(cam.fov) / 2);
    const quad = (pts, color) => {
      if (pts.some(p => !p)) return;
      g.fillStyle = color; g.beginPath(); pts.forEach((p, i) => i ? g.lineTo(p.x, p.y) : g.moveTo(p.x, p.y)); g.closePath(); g.fill();
    };
    const foot = (x, r) => {   // umbră de contact + placa de beton: stâlpul „stă” pe sol
      const a = P(x, 0, 0.5); if (!a) return;
      const w = f * r * 4.5 / a.z, h = Math.max(2, w * 0.22);
      const gr = g.createRadialGradient(a.x, a.y, 0, a.x, a.y, w);
      gr.addColorStop(0, "rgb(0 0 0 / 45%)"); gr.addColorStop(1, "rgb(0 0 0 / 0%)");
      g.save(); g.translate(a.x, a.y); g.scale(1, h / w); g.translate(-a.x, -a.y);
      g.fillStyle = gr; g.beginPath(); g.arc(a.x, a.y, w, 0, Math.PI * 2); g.fill(); g.restore();
      const pl = [P(x - r * 2.2, 0.15, 0.5 - r * 2.2), P(x + r * 2.2, 0.15, 0.5 - r * 2.2), P(x + r * 2.2, 0, 0.5 - r * 2.2), P(x - r * 2.2, 0, 0.5 - r * 2.2)];
      if (pl.every(Boolean)) { g.fillStyle = "#7b7d80"; g.beginPath(); pl.forEach((p, i) => i ? g.lineTo(p.x, p.y) : g.moveTo(p.x, p.y)); g.closePath(); g.fill(); }
    };
    if (S.mount === "unipol") foot(0, 0.45);
    if (S.mount === "two") { foot(-W / 3, 0.25); foot(W / 3, 0.25); }
    const pole = (x, r) => {
      const a = P(x, -0.3, 0.5), b = P(x, bottom - 0.2, 0.5); if (!a || !b) return;
      const wa = f * r / a.z, wb = f * r / b.z;
      const gr = g.createLinearGradient(a.x - wa, 0, a.x + wa, 0);
      gr.addColorStop(0, "#43474d"); gr.addColorStop(0.45, "#8a8f96"); gr.addColorStop(1, "#3a3d42");
      g.fillStyle = gr; g.beginPath();
      g.moveTo(a.x - wa * 1.15, a.y); g.lineTo(a.x + wa * 1.15, a.y); g.lineTo(b.x + wb, b.y); g.lineTo(b.x - wb, b.y); g.closePath(); g.fill();
    };
    if (S.mount === "unipol") pole(0, 0.45);
    if (S.mount === "two") { pole(-W / 3, 0.25); pole(W / 3, 0.25); }

    const fr = S.mount === "wall" ? 0.15 : 0.3, depth = S.mount === "wall" ? 0.25 : 0.2;
    quad([P(-W / 2 - fr, top + fr, depth), P(W / 2 + fr, top + fr, depth), P(W / 2 + fr, bottom - fr, depth), P(-W / 2 - fr, bottom - fr, depth)], "#24272c");
    if (S.mount === "unipol") quad([P(-1.5, bottom - 0.2, 0.3), P(1.5, bottom - 0.2, 0.3), P(1.5, bottom - 0.5, 0.3), P(-1.5, bottom - 0.5, 0.3)], "#5b5f66");

    if (dot2(sub(cam.pos, S.c), S.n) <= 0) return;      // din spate se vede doar rama
    const N = 10, iw = img.width, ih = img.height, grid = [];
    for (let j = 0; j <= N; j++) { grid.push([]); for (let i = 0; i <= N; i++) grid[j].push(P(-W / 2 + W * i / N, top - H * j / N, 0)); }
    for (let j = 0; j < N; j++) for (let i = 0; i < N; i++) {
      const p00 = grid[j][i], p10 = grid[j][i + 1], p01 = grid[j + 1][i], p11 = grid[j + 1][i + 1];
      if (!p00 || !p10 || !p01 || !p11) continue;
      const s = (a, b) => ({ x: iw * a / N, y: ih * b / N });
      tri(g, img, s(i, j), s(i + 1, j), s(i, j + 1), p00, p10, p01);
      tri(g, img, s(i + 1, j), s(i + 1, j + 1), s(i, j + 1), p10, p11, p01);
    }
  }

  function tri(g, img, s0, s1, s2, d0, d1, d2) {
    const cx = (d0.x + d1.x + d2.x) / 3, cy = (d0.y + d1.y + d2.y) / 3;
    const grow = p => { const dx = p.x - cx, dy = p.y - cy, l = Math.hypot(dx, dy) || 1; return { x: p.x + dx / l * 0.6, y: p.y + dy / l * 0.6 }; };
    const e0 = grow(d0), e1 = grow(d1), e2 = grow(d2);
    const u1x = s1.x - s0.x, u1y = s1.y - s0.y, u2x = s2.x - s0.x, u2y = s2.y - s0.y;
    const v1x = d1.x - d0.x, v1y = d1.y - d0.y, v2x = d2.x - d0.x, v2y = d2.y - d0.y;
    const det = u1x * u2y - u2x * u1y; if (!det) return;
    const a = (v1x * u2y - v2x * u1y) / det, c = (v2x * u1x - v1x * u2x) / det;
    const b = (v1y * u2y - v2y * u1y) / det, d = (v2y * u1x - v1y * u2x) / det;
    g.save(); g.beginPath(); g.moveTo(e0.x, e0.y); g.lineTo(e1.x, e1.y); g.lineTo(e2.x, e2.y); g.closePath(); g.clip();
    g.transform(a, b, c, d, d0.x - a * s0.x - c * s0.y, d0.y - b * s0.x - d * s0.y);
    g.drawImage(img, 0, 0); g.restore();
  }

  function fallbackAd() {
    const c = document.createElement("canvas"); c.width = 1200; c.height = 400;
    const g = c.getContext("2d");
    g.fillStyle = "#c62828"; g.fillRect(0, 0, 1200, 400);
    g.fillStyle = "#fff"; g.font = "bold 120px system-ui"; g.textAlign = "center"; g.textBaseline = "middle";
    g.fillText("RECLAMA TA", 600, 200);
    return c;
  }

  // ---------------------------------------------------------------- Vizibilitate pentru toate pinurile (o folosește scorul)
  let campaign = null;
  try { campaign = JSON.parse(sessionStorage.getItem("sightline-campaign") || "null"); } catch (e) {}
  const store = (() => { try { return JSON.parse(sessionStorage.getItem("sightline-visibility") || "{}"); } catch (e) { return {}; } })();

  // ---------------------------------------------------------------- Vizibilitate prin modelul 3D Google (Cesium)
  // Vede ce nu e în OpenStreetMap: copacii reali, stâlpii, marchizele. Rulează într-un viewer Cesium ascuns (în afara ecranului);
  // tile-urile nu se salvează, se păstrează doar procentele.
  const CESIUM_JS = "https://cesium.com/downloads/cesiumjs/releases/1.120/Build/Cesium/Cesium.js";
  const RAY_SKIP_M = 4;      // primii metri din fața șoferului (mașini parcate, borduri) nu contează
  const RAY_END_M = 6;       // ultimii 6 m dinaintea panoului: copacul în care e „înfipt” panoul în model nu contează
                             // (panoul ar sta în fața crengilor, iar coroana din model îl înghite cu totul)
  // În fotogrammetria Google, coroanele copacilor sunt „blocuri” pline până la sol (pe Dorobanți fac un tunel peste drum).
  // În realitate șoferul vede pe sub coroane, printre trunchiuri. De aceea ignorăm lovirile sub 3,5 m de la nivelul drumului
  // și continuăm raza; ce e mai sus (coroana reală, clădiri) blochează. Clădirile joase le prinde oricum OpenStreetMap.
  const CANOPY_BASE_M = 3.5;
  const CZ_BUDGET_MS = 30000;   // cel mult 30 s de model 3D pe pin; altfel mergem mai departe fără el
  // Deasupra, o lovire în afara oricărei clădiri OSM e un copac. Coroanele din fotogrammetrie sunt umflate și pline,
  // dar frunzișul real lasă să se vadă printre ramuri (și toamna/iarna mult mai mult): un pătrățel din spatele unui copac
  // contează 35% vizibil, nu 0. În spatele unei clădiri contează 0.
  const TREE_SEE = 0.35;
  let cz = null, czQueue = Promise.resolve();

  function cesiumToken() { return (window.SIGHTLINE_CONFIG && window.SIGHTLINE_CONFIG.cesiumIonToken) || ""; }

  async function czInit() {
    if (cz) return cz;
    if (typeof Cesium === "undefined") await new Promise((res, rej) => {
      const sc = document.createElement("script"); sc.src = CESIUM_JS; sc.onload = res;
      sc.onerror = () => rej(new Error("CesiumJS nu s-a încărcat")); document.head.appendChild(sc);
    });
    Cesium.Ion.defaultAccessToken = cesiumToken();
    Cesium.RequestScheduler.maximumRequestsPerServer = 8;   // lasă loc și pentru pozele Street View
    const div = document.createElement("div");
    div.style.cssText = "position:fixed;left:-12000px;top:0;width:720px;height:450px;";   // mai mic = mai puține tile-uri de încărcat
    document.body.appendChild(div);
    const viewer = new Cesium.Viewer(div, { globe: false, baseLayerPicker: false, geocoder: false, homeButton: false, sceneModePicker: false,
      navigationHelpButton: false, timeline: false, animation: false, fullscreenButton: false, infoBox: false, selectionIndicator: false,
      contextOptions: { webgl: { preserveDrawingBuffer: true } } });
    viewer.canvas.style.width = "100%"; viewer.canvas.style.height = "100%"; viewer.resize();   // fără widgets.css canvasul rămâne 300×150
    const sc = viewer.scene, ssc = sc.screenSpaceCameraController;
    ssc.enableInputs = false; ssc.enableCollisionDetection = false;                              // altfel camera de la 1,2 m e mutată
    sc.fog.enabled = false; sc.skyAtmosphere.show = false; sc.skyBox.show = false; sc.sun.show = false; sc.moon.show = false;
    sc.backgroundColor = Cesium.Color.BLACK;
    const tiles = await Cesium.createGooglePhotorealistic3DTileset();
    tiles.maximumScreenSpaceError = 12;   // detaliu suficient pentru clădiri și coroane, se încarcă mult mai repede
    tiles.cullRequestsWhileMoving = false;
    viewer.scene.primitives.add(tiles);
    cz = { viewer, tiles };
    viewer.scene.renderError.addEventListener((sc, e) => {   // randarea s-a oprit: o repornim (altfel razele nu mai lovesc nimic)
      log(`3D: eroare de randare (${String(e && e.message || e).slice(0, 60)}), repornesc`);
      setTimeout(() => { try { viewer.useDefaultRenderLoop = true; } catch (x) {} }, 50);
    });
    return cz;
  }

  async function czWait(maxMs) {
    // cadru următor; dacă tab-ul e în fundal (requestAnimationFrame oprit), forțăm randarea ca să nu stea pe loc
    const frame = () => new Promise(r => {
      let done = false; const fin = () => { if (!done) { done = true; r(); } };
      requestAnimationFrame(fin);
      setTimeout(() => { if (!done) { try { cz.viewer.render(); } catch (e) {} fin(); } }, 60);
    });
    const t0 = Date.now(); let ok = 0;
    await new Promise(r => setTimeout(r, 800));
    while (Date.now() - t0 < maxMs) { ok = cz.tiles.tilesLoaded ? ok + 1 : 0; if (ok >= 8) break; await frame(); }
  }

  // nivelul drumului (înălțime elipsoidală): din suprafața modelului 3D pe traseu, partea de jos (nu mașini, nu coroane)
  async function czRoadLevel(S) {
    // 5 puncte pe traseu (fiecare rază de sus în jos costă o randare; mai multe încetinesc mult)
    const scene = cz.viewer.scene, pts = [200, 150, 80, 30, 10].map(d => S.G(viewpoint(S, d)));
    cz.viewer.camera.setView({ destination: Cesium.Cartesian3.fromDegrees(S.pin.lon, S.pin.lat - 0.0022, 380),
      orientation: { heading: 0, pitch: rad(-50), roll: 0 } });
    // tile-urile pot veni greu: încercăm de mai multe ori (până la ~15 s) până lovesc razele cel puțin 3 puncte
    let hs = [];
    for (let attempt = 0; attempt < 4 && hs.length < 3; attempt++) {
      await czWait(attempt ? 3500 : 4000);
      hs = [];
      for (const p of pts) {   // rază de sus în jos în fiecare punct al traseului
        const top = Cesium.Cartesian3.fromDegrees(p.lon, p.lat, 1500), bottom = Cesium.Cartesian3.fromDegrees(p.lon, p.lat, -200);
        const dir = Cesium.Cartesian3.normalize(Cesium.Cartesian3.subtract(bottom, top, new Cesium.Cartesian3()), new Cesium.Cartesian3());
        const hit = scene.pickFromRay(new Cesium.Ray(top, dir));
        if (hit && hit.position) hs.push(Cesium.Cartographic.fromCartesian(hit.position).height);
      }
      if (S.czLate && S.czLate()) break;
    }
    if (hs.length < 3) {
      try {
        const res = await withTimeout(scene.sampleHeightMostDetailed(pts.map(p => Cesium.Cartographic.fromDegrees(p.lon, p.lat))), 4000, "nivelul solului");
        hs = res.map(c => c.height).filter(Number.isFinite);
      } catch (e) {}
    }
    hs.sort((a, b) => a - b);
    return hs.length >= 3 ? hs[Math.floor(hs.length * 0.2)] : null;
  }

  // înălțimea solului (elipsoid) într-un punct: cea mai joasă suprafață dintr-un mic cerc (ca să nu luăm coroana unui copac)
  function czGroundAt(lat, lon, radiusM) {
    const C3 = Cesium.Cartesian3, hs = [];
    const k = 111320 * Math.cos(rad(lat));
    for (let i = 0; i < 9; i++) {
      const a = i * Math.PI / 4, r = i === 8 ? 0 : radiusM;
      const la = lat + r * Math.sin(a) / 111320, lo = lon + r * Math.cos(a) / k;
      const top = C3.fromDegrees(lo, la, 1500), bot = C3.fromDegrees(lo, la, -200);
      const hit = cz.viewer.scene.pickFromRay(new Cesium.Ray(top, C3.normalize(C3.subtract(bot, top, new C3()), new C3())));
      if (hit && hit.position) hs.push(Cesium.Cartographic.fromCartesian(hit.position).height);
    }
    return hs.length ? Math.min(...hs) : null;
  }

  // diferența de nivel dintre baza panoului și solul de sub fiecare panoramă Street View (pantă, trotuar)
  async function czGroundOffsets(S, shots) {
    if (!cz || S.roadLevel == null) return;
    cz.viewer.useDefaultRenderLoop = true;
    try { await czGroundOffsetsRun(S, shots); } finally { cz.viewer.useDefaultRenderLoop = false; }
  }
  async function czGroundOffsetsRun(S, shots) {
    const b = S.G(S.c);
    cz.viewer.camera.setView({ destination: Cesium.Cartesian3.fromDegrees(b.lon, b.lat - 0.0018, 300),
      orientation: { heading: 0, pitch: rad(-55), roll: 0 } });
    await czWait(8000);
    const hb = S.roadLevel + 0.15;   // baza panoului = nivelul trotuarului (sub panou modelul are des tufe / coroane)
    Object.values(shots).forEach(sh => {
      if (!sh || sh.error) return;
      const p = S.G(sh.pos);
      let hp = czGroundAt(p.lat, p.lon, 1.5);
      if (hp == null || Math.abs(hp - S.roadLevel) > 4) hp = S.roadLevel;
      sh.dz = Math.max(-1.5, Math.min(1.5, hb - hp));
    });
  }

  // Vizibilitatea din modelul 3D, prin randare (rapid: 2 imagini pe distanță, nu sute de raze):
  //  - panoul e un dreptunghi magenta; îl randăm o dată fără oraș (cât ar ocupa) și o dată cu orașul (cât se mai vede);
  //  - din oraș tăiem tot ce e sub 3,5 m (coroanele „pline până la sol” din fotogrammetrie; clădirile joase le dă OSM)
  //    și zona de lângă panou (copacul în care e „înfipt” panoul în model);
  //  - ce acoperă orașul rămas contează ca frunziș (TREE_SEE); clădirile se scad separat din OSM.
  async function czRefine(S, onStep) {
    log(`${S.locId}: 3D start`);
    try { return await czRefineRun(S, onStep); }
    finally { if (cz) cz.viewer.useDefaultRenderLoop = false; }   // oprit cât nu calculăm (nu mai cere tile-uri)
  }
  async function czRefineRun(S, onStep) {
    const deadline = Date.now() + CZ_BUDGET_MS;
    S.czLate = () => Date.now() > deadline;
    await withTimeout(czInit(), 20000, "încărcarea modelului 3D");
    cz.viewer.useDefaultRenderLoop = true;
    log(`${S.locId}: 3D încărcat`);
    const road = await czRoadLevel(S);
    log(`${S.locId}: nivel drum ${road && road.toFixed(1)}`);
    if (S.czLate()) throw new Error("oprit (prea lent)");
    if (road === null) throw new Error("modelul 3D nu are date pe traseu");
    S.roadLevel = road;
    const C3 = Cesium.Cartesian3, v = cz.viewer, t = cz.tiles;
    const toCart = (lp, z) => { const g = S.G(lp); return C3.fromDegrees(g.lon, g.lat, road + z); };
    const top = S.E + S.H / 2, bot = S.E - S.H / 2;
    const at = (x, z) => toCart(add(S.c, mul(S.r, x)), z);
    const ent = v.entities.add({ polygon: { hierarchy: [at(-S.W / 2, bot), at(S.W / 2, bot), at(S.W / 2, top), at(-S.W / 2, top)],
      perPositionHeight: true, material: Cesium.Color.fromBytes(255, 0, 255) } });
    const bc = S.G(S.c);
    // colecțiile de tăiere se creează o singură dată și doar se mută / pornesc / opresc
    // (dacă le scoți din tileset, Cesium 1.120 crapă la randare: „reading '_target'”)
    const enuM = Cesium.Transforms.eastNorthUpToFixedFrame(C3.fromDegrees(bc.lon, bc.lat, road));
    if (!cz.planes) {
      cz.planes = new Cesium.ClippingPlaneCollection({ modelMatrix: enuM, planes: [new Cesium.ClippingPlane(new C3(0, 0, 1), -CANOPY_BASE_M)], edgeWidth: 0 });
      t.clippingPlanes = cz.planes;
    } else cz.planes.modelMatrix = enuM;
    cz.planes.enabled = true;
    const zone = [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([a, b2]) => S.G(add(add(S.c, mul(S.r, a * (S.W / 2 + 2))), mul(S.n, b2 * RAY_END_M))));
    const poly = new Cesium.ClippingPolygon({ positions: C3.fromDegreesArray(zone.flatMap(g => [g.lon, g.lat])) });
    if (!cz.polys) { cz.polys = new Cesium.ClippingPolygonCollection({ polygons: [poly] }); t.clippingPolygons = cz.polys; }
    else { cz.polys.removeAll(); cz.polys.add(poly); }
    cz.polys.enabled = true;

    const frames = async n => { for (let i = 0; i < n; i++) await new Promise(r => {
      let done = false; requestAnimationFrame(() => { done = true; r(); }); setTimeout(() => { if (!done) { v.render(); r(); } }, 60); }); };
    const grab = () => {
      v.render();
      const c = document.createElement("canvas"); c.width = v.canvas.width; c.height = v.canvas.height;
      const g = c.getContext("2d", { willReadFrequently: true }); g.drawImage(v.canvas, 0, 0);
      return g.getImageData(0, 0, c.width, c.height).data;
    };
    const isM = (d, i) => d[i] > 150 && d[i + 2] > 150 && d[i + 1] < 0.6 * Math.min(d[i], d[i + 2]);
    const out = {};
    try {
      for (const d of DISTANCES) {
        if (!hasPoint(S, d) && d !== DISTANCES[DISTANCES.length - 1]) continue;
        if (onStep) onStep(d);
        const vp = viewpoint(S, d), g = S.G(vp), to = sub(S.c, vp), dist = len(to);
        v.camera.setView({ destination: C3.fromDegrees(g.lon, g.lat, road + EYE_H),
          orientation: { heading: Math.atan2(to.x, to.y), pitch: Math.atan2(S.E - EYE_H, dist), roll: 0 } });
        v.camera.frustum.fov = Math.min(rad(60), Math.max(rad(4), 2 * Math.atan((S.W / 2 + 1) / Math.max(dist, 1)) * 1.3));
        await czWait(4000);
        if (S.czLate()) throw new Error("oprit (prea lent)");
        log(`${S.locId}: ${d} m tile-uri gata`);
        t.show = false; await frames(2); const B = grab();
        t.show = true; await frames(2); const A = grab();
        // pătrățelul fiecărui pixel: proiectăm colțurile pătrățelelor în fereastră
        const sx = v.canvas.width / v.canvas.clientWidth, sy = v.canvas.height / v.canvas.clientHeight;
        const quads = [];
        for (let iy = 0; iy < 4; iy++) for (let ix = 0; ix < 8; ix++) {
          const x0 = -S.W / 2 + S.W * ix / 8, x1 = x0 + S.W / 8, z0 = top - S.H * iy / 4, z1 = z0 - S.H / 4;
          const q = [[x0, z0], [x1, z0], [x1, z1], [x0, z1]].map(([x, z]) => {
            const w = Cesium.SceneTransforms.worldToWindowCoordinates(v.scene, at(x, z));
            return w ? { x: w.x * sx, y: w.y * sy } : null;
          });
          quads.push(q.some(p => !p) ? null : { q, xa: Math.min(...q.map(p => p.x)), xb: Math.max(...q.map(p => p.x)), ya: Math.min(...q.map(p => p.y)), yb: Math.max(...q.map(p => p.y)) });
        }
        const ref = new Array(32).fill(0), seen = new Array(32).fill(0), W = v.canvas.width;
        for (let i = 0; i < B.length; i += 4) {
          if (!isM(B, i)) continue;
          const px = (i / 4) % W + 0.5, py = Math.floor(i / 4 / W) + 0.5;
          let cell = -1;
          for (let k = 0; k < 32; k++) { const Q = quads[k]; if (Q && px >= Q.xa && px <= Q.xb && py >= Q.ya && py <= Q.yb && inQuad(px, py, Q.q)) { cell = k; break; } }
          if (cell < 0) continue;
          ref[cell]++; if (isM(A, i)) seen[cell]++;
        }
        out[d] = ref.map((r, k) => { if (!r) return 1; const f = seen[k] / r; return f + (1 - f) * TREE_SEE; });
      }
    } finally {
      v.entities.remove(ent);
      cz.planes.enabled = false; cz.polys.enabled = false;
    }
    return out;
  }

  // ---------------------------------------------------------------- Pozele Street View pentru un pin
  function googleKey() { return (window.SIGHTLINE_CONFIG && window.SIGHTLINE_CONFIG.googleMapsApiKey) || ""; }

  async function fetchShots(S) {
    const key = googleKey(), shots = {};
    if (!key) return shots;
    await loadGoogle(key);
    for (const d of DISTANCES) {
      if (!hasPoint(S, d)) {
        shots[d] = { error: `Strada are doar ~${Math.round(reach(S))} m în fața panoului (până la intersecția anterioară), deci nu există un punct la ${d} m.` };
        continue;
      }
      try {
        log(`panoramă ${d} m`);
        const p = await findPano(S, d);
        // fetch + createImageBitmap (un <img> cu decode() se poate bloca când pagina are și un WebGL activ)
        const url = `https://maps.googleapis.com/maps/api/streetview?size=${IMG_W}x${IMG_H}&pano=${p.pano}&heading=${p.heading.toFixed(1)}&pitch=${p.pitch.toFixed(1)}&fov=${FOV}&return_error_code=true&key=${encodeURIComponent(key)}`;
        const ctl = new AbortController(), timer = setTimeout(() => ctl.abort(), 10000);
        let im;
        try {
          const resp = await fetch(url, { signal: ctl.signal });
          if (!resp.ok) throw new Error(`Street View ${resp.status}`);
          im = await createImageBitmap(await resp.blob());
        } finally { clearTimeout(timer); }
        const cv = document.createElement("canvas"); cv.width = IMG_W; cv.height = IMG_H;
        const g = cv.getContext("2d", { willReadFrequently: true }); g.drawImage(im, 0, 0, IMG_W, IMG_H);
        let pixels = null; try { pixels = g.getImageData(0, 0, IMG_W, IMG_H); } catch (e) {}
        shots[d] = { ...p, img: im, pixels };
      } catch (e) { shots[d] = { error: `Nu există Street View lângă punctul de ${d} m.` }; }
    }
    return shots;
  }

  // Verificare încrucișată: modelul 3D zice „copac în față”, dar poza reală arată ce e acolo.
  // Dacă în dreptul pătrățelului din poză nu e frunziș (e cer, fațadă, stradă), copacul din model e o umflătură → vizibil.
  // Dacă e frunziș, păstrăm cât acoperă, proporțional cu cât frunziș e în pătrățel.
  const PHOTO_MIN_FOLIAGE = 0.15;
  function photoCheck(S) {
    if (!S.shots) return false;
    let changed = false;
    DISTANCES.forEach(d => {
      const sh = S.shots[d], v = S.vis.byDistance[d];
      if (!sh || sh.error || !sh.pixels || !v) return;
      const cam = { pos: sh.pos, heading: sh.heading, pitch: sh.pitch, fov: FOV, W: IMG_W, H: IMG_H, dz: sh.dz || 0 };
      const o = sh.pixels.data;
      const cells = v.cells.map((c, i) => {
        const val = c === true ? 1 : c === false ? 0 : Number(c);
        if (!(val > 0 && val < 1)) return c;                       // doar pătrățelele „copac”
        const q = cellQuad(S, cam, i % 8, Math.floor(i / 8)); if (!q) return c;
        const xs = q.map(p => p.x), ys = q.map(p => p.y);
        const xa = Math.max(0, Math.floor(Math.min(...xs))), xb = Math.min(IMG_W - 1, Math.ceil(Math.max(...xs)));
        const ya = Math.max(0, Math.floor(Math.min(...ys))), yb = Math.min(IMG_H - 1, Math.ceil(Math.max(...ys)));
        let tot = 0, fol = 0;
        for (let y = ya; y <= yb; y++) for (let x = xa; x <= xb; x++) {
          if (!inQuad(x + 0.5, y + 0.5, q)) continue;
          const k = (y * IMG_W + x) * 4; tot++;
          if (isFoliage(o[k], o[k + 1], o[k + 2])) fol++;
        }
        if (!tot) return c;
        const share = fol / tot;
        const nv = share < PHOTO_MIN_FOLIAGE ? 1 : 1 - (1 - TREE_SEE) * Math.min(1, share / 0.6);
        if (Math.abs(nv - val) > 0.01) changed = true;
        return nv;
      });
      S.vis.byDistance[d] = { pct: 100 * cells.reduce((a, c) => a + (c === true ? 1 : c === false ? 0 : Number(c)), 0) / cells.length, cells };
    });
    if (changed) {
      const valid = DISTANCES.filter(d => hasPoint(S, d) || d === DISTANCES[DISTANCES.length - 1]);
      const visible = valid.reduce((a, d) => a + S.vis.byDistance[d].pct, 0) / (valid.length || 1);
      S.vis.visible_pct = visible; S.vis.occluded_pct = 100 - visible;
    }
    return changed;
  }

  // ---------------------------------------------------------------- Vizibilitate pentru un pin: OSM (clădiri) + Cesium (copaci și restul)
  // Calculul pe pin are două etape:
  //  1) rapid (câteva secunde): străzi + clădiri OSM + pozele Street View → poze și clasament imediat;
  //  2) rafinare (modelul 3D Google, ~20 s pe pin, una după alta): copacii, verificați cu poza → procent actualizat,
  //     clasament recalculat la final. Dacă modelul 3D e lent sau cade, rămâne rezultatul din etapa 1.
  function saveStore(loc, S, czError) {
    const prev = store[loc.id];
    store[loc.id] = {
      key: S.key, source: S.source, occluded_pct: Math.round(S.vis.occluded_pct * 10) / 10, mount: S.mount,
      by_distance: Object.fromEntries(DISTANCES.map(d => [d, Math.round(S.vis.byDistance[d].pct)])),
      cesium_cells: S.czCells || (prev && prev.key === S.key ? prev.cesium_cells : null),
      road_level: S.roadLevel != null ? S.roadLevel : (prev && prev.key === S.key ? prev.road_level : null),
      cesium_error: czError || null, street_reach_m: Math.round(reach(S)), refined: !!S.refined,
      road_name: S.roadName, buildings: S.buildings.length, osm_trees: S.trees.length, no_buildings: !!S.noBuildings,
    };
    try { sessionStorage.setItem("sightline-visibility", JSON.stringify(store)); } catch (e) {}
  }

  function applyCz(S, czCells) {
    S.czCells = czCells;
    const valid = DISTANCES.filter(d => czCells[d]);
    valid.forEach(d => {
      const v = S.vis.byDistance[d], cells = v.cells.map((ok, i) => ok ? Number(czCells[d][i]) : 0);
      S.vis.byDistance[d] = { pct: 100 * cells.reduce((a, c) => a + c, 0) / cells.length, cells };
    });
    const visible = valid.reduce((a, d) => a + S.vis.byDistance[d].pct, 0) / (valid.length || 1);
    S.vis.visible_pct = visible; S.vis.occluded_pct = 100 - visible;
    S.source = "osm+cesium";
    if (S.shots && Object.values(S.shots).some(x => x && x.pixels)) { photoCheck(S); S.source = "osm+cesium+foto"; }
  }

  async function computeFast(loc, mount) {
    const size = (campaign && campaign.billboardSize) || {};
    log(`${loc.id}: OSM`);
    const json = await overpass(loc.lat, loc.lon);
    const S = buildScene(json, { lat: loc.lat, lon: loc.lon }, size, mount);
    S.locId = loc.id; S.mount = mount; S.source = "osm";
    S.key = `v13|${loc.lat.toFixed(6)},${loc.lon.toFixed(6)}|${mount}|${size.width || 12}x${size.height || 4}`;
    S.vis = visibility(S);
    if (googleKey()) {
      try { S.shots = await withTimeout(fetchShots(S), 20000, "Street View"); } catch (e) { console.warn("SightLine:", e); S.shots = S.shots || {}; }
      log(`${loc.id}: poze ${Object.values(S.shots || {}).filter(x => x && x.img).length}`);
    }
    // rafinarea făcută deja în tabul ăsta (aceeași poziție, același panou): o refolosim
    const prev = store[loc.id];
    if (prev && prev.key === S.key && prev.cesium_cells) {
      S.roadLevel = prev.road_level; applyCz(S, prev.cesium_cells); S.refined = true;
    }
    saveStore(loc, S);
    return S;
  }

  function refine(S, loc, onStatus) {   // o singură rafinare pe scenă, oricine o cere
    if (onStatus) S.onRefineStep = onStatus;
    if (!S.refineP) S.refineP = refineNow(S, loc);
    return S.refineP;
  }
  async function refineNow(S, loc) {
    const onStatus = d => S.onRefineStep && S.onRefineStep(d);
    if (S.refined || !cesiumToken()) return S;
    let czCells = null, czError = null;
    const run1 = () => czQueue.then(() => czRefine(S, onStatus));
    let job = run1(); czQueue = job.catch(() => null);
    try { czCells = await job; }
    catch (e) {
      if (/prea lent/.test(e.message)) { czError = "prea lent, sărit"; log(`${loc.id}: 3D prea lent, sărit`); }
      else {
        console.warn("SightLine: modelul 3D a eșuat, reîncerc:", e);
        job = run1(); czQueue = job.catch(() => null);
        try { czCells = await job; } catch (e2) { czError = e2.message || String(e2); console.warn("SightLine: Cesium:", e2); }
      }
    }
    if (czCells) {
      if (S.shots && S.roadLevel != null) {
        const g = czQueue.then(async () => { await czInit(); await czGroundOffsets(S, S.shots); });
        czQueue = g.catch(() => null);
        try { await g; } catch (e) { console.warn("SightLine: nivelul solului:", e); }
      }
      applyCz(S, czCells);
      log(`${loc.id}: rafinat cu modelul 3D`);
    }
    S.refined = true;
    saveStore(loc, S, czError);
    window.dispatchEvent(new CustomEvent("sightline:refined", { detail: { id: loc.id } }));
    return S;
  }

  async function computeFor(loc, mount, onStatus) {
    const S = await computeFast(loc, mount);
    return refine(S, loc, onStatus);
  }

  // Promisiunea pe care o așteaptă results.html înainte de POST /score: { id_pin: { occluded_pct, ... } }.
  // Se rezolvă după etapa rapidă (cel mult 40 s); după rafinarea tuturor pinilor, clasamentul se recalculează.
  const fastJobs = {}, refineJobs = {};
  const ready = (campaign && campaign.locations && campaign.locations.length)
    ? (() => {
        const fast = Promise.all(campaign.locations.map(l => (fastJobs[l.id] = computeFast(l, "unipol").catch(e => { log(`${l.id}: ${e.message}`); return null; }))));
        fast.then(async scenes => {
          const before = JSON.stringify(campaign.locations.map(l => store[l.id] && store[l.id].occluded_pct));
          await Promise.all(campaign.locations.map((l, i) => (refineJobs[l.id] = scenes[i] ? refine(scenes[i], l).catch(() => scenes[i]) : Promise.resolve(null))));
          const after = JSON.stringify(campaign.locations.map(l => store[l.id] && store[l.id].occluded_pct));
          if (after !== before && typeof window.loadScore === "function") window.loadScore(campaign);
        });
        return Promise.race([fast, new Promise(r => setTimeout(r, 40000))]).then(() => store);
      })()
    : Promise.resolve(store);

  // ---------------------------------------------------------------- Interfața din caseta „Randare 3D”
  const CSS = `
    .slv { position: relative; background: #111; aspect-ratio: 4 / 3; }
    .slv canvas, .slv .slv-live { position: absolute; inset: 0; width: 100%; height: 100%; }
    .slv .slv-live { display: none; } .slv.live .slv-live { display: block; } .slv.live canvas.slv-shot { display: none; }
    .slv canvas.slv-over { pointer-events: none; z-index: 5; display: none; } .slv.live canvas.slv-over { display: block; }
    .slv-bar { position: absolute; z-index: 6; left: 10px; right: 10px; bottom: 10px; display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
    .slv-bar select, .slv-bar button { font: 600 11px system-ui, sans-serif; padding: 6px 9px; border-radius: 999px; border: 1px solid rgb(255 255 255 / 55%);
      background: rgb(255 255 255 / 85%); color: #2b3328; cursor: pointer; }
    .slv-vis { margin-left: auto; font: 700 11px system-ui, sans-serif; padding: 6px 10px; border-radius: 999px; background: rgb(20 24 20 / 75%); color: #fff;
      display: flex; gap: 6px; align-items: center; }
    .slv-grid { display: inline-grid; grid-template-columns: repeat(8, 6px); gap: 1px; }
    .slv-grid i { width: 6px; height: 4px; display: block; }
    .slv-msg { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; padding: 24px; text-align: center;
      color: #e8ece6; font: 500 13px system-ui, sans-serif; background: rgb(17 17 17 / 70%); z-index: 4; }
    .slv-msg:empty { display: none; }`;

  function init() {
    const scene = document.querySelector(".scene");
    if (!scene || !campaign || !campaign.locations || !campaign.locations.length) return;

    const style = document.createElement("style"); style.textContent = CSS; document.head.appendChild(style);
    const box = document.createElement("div"); box.className = "slv";
    box.innerHTML = `<canvas class="slv-shot" width="${IMG_W}" height="${IMG_H}"></canvas><div class="slv-live"></div><canvas class="slv-over"></canvas>
      <div class="slv-msg">Pregătesc vederea șoferului…</div>
      <div class="slv-bar"><select class="slv-pin"></select><select class="slv-mount"></select><button class="slv-livebtn" type="button">Live 360°</button><button class="slv-maskbtn" type="button">Zone acoperite</button>
      <span class="slv-vis"></span></div>`;
    const svg = scene.querySelector("svg"), overlayLabel = scene.querySelector(".scene-overlay");
    scene.appendChild(box);
    const $ = s => box.querySelector(s), msg = t => { $(".slv-msg").textContent = t || ""; };

    campaign.locations.forEach((l, i) => $(".slv-pin").add(new Option(`Pin ${i + 1}`, l.id)));
    Object.entries(MOUNTS).forEach(([k, v]) => $(".slv-mount").add(new Option(v, k)));

    const img = new Image();
    let ad = fallbackAd();
    if (campaign.creativePreview) { img.onload = () => { ad = img; paint(); }; img.src = campaign.creativePreview; }

    const key = (window.SIGHTLINE_CONFIG && window.SIGHTLINE_CONFIG.googleMapsApiKey) || "";
    let S = null, shots = {}, dist = 150, live = null, run = 0, showMask = false;

    function showVis(pending) {
      const v = S && S.vis; if (!v) { $(".slv-vis").innerHTML = ""; return; }
      if (!pending && S && !S.refined && cesiumToken()) pending = "se rafinează cu modelul 3D…";
      if (!hasPoint(S, dist)) { $(".slv-vis").innerHTML = `fără punct la ${dist} m · ${v.visible_pct.toFixed(0)}% în medie`; return; }
      const at = v.byDistance[dist];
      const st = store[S.locId] || {};
      const nb = st.no_buildings ? " · fără clădiri OSM (n-au venit la timp)" : "";
      const src0 = st.source === "osm+cesium+foto" ? "clădiri + copaci (3D Google, verificat cu poza)"
        : st.source === "osm+cesium" ? "clădiri + copaci (3D Google)" : `doar clădiri OSM${st.cesium_error ? " (3D: " + st.cesium_error + ")" : ""}`;
      const src = src0 + nb;
      $(".slv-vis").innerHTML = `vizibil ${at.pct.toFixed(0)}% la ${dist} m · ${v.visible_pct.toFixed(0)}% în medie · ${pending || src}
        <span class="slv-grid">${at.cells.map(c => `<i style="background:${c === true || c >= 1 ? "#4caf50" : c > 0 ? "#ffb300" : "#e53935"}"></i>`).join("")}</span>`;
    }

    function paint() {
      if (box.classList.contains("live")) { drawLive(); return; }
      const shot = shots[dist], g = $(".slv-shot").getContext("2d");
      g.fillStyle = "#111"; g.fillRect(0, 0, IMG_W, IMG_H);
      if (!shot || !S) return;
      if (shot.error) { msg(shot.error); return; }
      msg("");
      g.drawImage(shot.img, 0, 0, IMG_W, IMG_H);
      const cam = { pos: shot.pos, heading: shot.heading, pitch: shot.pitch, fov: FOV, W: IMG_W, H: IMG_H, dz: shot.dz || 0 };
      drawBoard(g, S, cam, ad);
      const at = S.vis && S.vis.byDistance[dist];
      if (at && hasPoint(S, dist)) drawOcclusion(g, S, cam, at.cells, shot.pixels, showMask);
      g.font = "600 11px system-ui"; g.fillStyle = "rgb(255 255 255 / 85%)";
      g.fillText(`Street View${shot.date ? " " + shot.date : ""} · panorama la ${shot.dist.toFixed(0)} m de panou · panou simulat SightLine`, 12, 20);
    }

    async function load() {
      const my = ++run;
      const loc = campaign.locations.find(l => l.id === $(".slv-pin").value) || campaign.locations[0];
      const mount = $(".slv-mount").value;
      shots = {}; S = null; box.classList.remove("live"); paint(); showVis();
      try {
        msg("Caut strada, clădirile și copacii în OpenStreetMap…");
        const before = store[loc.id] && store[loc.id].occluded_pct;
        if (mount === "unipol" && fastJobs[loc.id]) S = await fastJobs[loc.id];
        if (!S || S.mount !== mount) S = await computeFast(loc, mount);
        if (my !== run) return;
        if (!S) throw new Error("nu am putut calcula scena");
        // a doua etapă (modelul 3D) în fundal: când e gata, redesenăm și, dacă s-a schimbat, recalculăm clasamentul
        const refP = (mount === "unipol" && refineJobs[loc.id]) ? refineJobs[loc.id] : refine(S, loc, d => { if (my === run) showVis(`modelul 3D Google: ${d} m…`); });
        Promise.resolve(refP).then(() => {
          if (my !== run) return;
          showVis(); setCaption(); paint();
          if (mount !== "unipol" && store[loc.id] && store[loc.id].occluded_pct !== before && typeof window.loadScore === "function") window.loadScore(campaign);
        });
        if (mount !== "unipol" && before !== undefined && before !== store[loc.id].occluded_pct && typeof window.loadScore === "function") window.loadScore(campaign);
        window.dispatchEvent(new CustomEvent("sightline:visibility", { detail: { id: loc.id, ...store[loc.id] } }));
        showVis();
        if (!cesiumToken()) $(".slv-vis").title = "Fără token Cesium: vizibilitatea ține cont doar de clădiri și de copacii din OpenStreetMap.";
        if (!key) { msg("Vizibilitatea e calculată. Pentru poze Street View pune cheia Google în web/config.local.js."); return; }
        if (!S.shots) { msg("Încarc pozele Street View…"); S.shots = await fetchShots(S); if (my !== run) return; }
        shots = S.shots;
        if (svg) svg.style.display = "none";
        if (overlayLabel) overlayLabel.textContent = "Street View + panou SightLine";
        setCaption();
        paint();
      } catch (e) { msg("Nu am putut pregăti vederea: " + e.message); }
    }

    function setCaption() {
      if (!S) return;
      const loc = { id: S.locId };
      const sub = scene.parentElement && scene.parentElement.querySelector(".caption-sub");
      if (sub) sub.textContent = `${S.moved > 0.5 ? `Panoul mutat cu ${S.moved.toFixed(0)} m față de pin (pinul era în / lângă o clădire) · ` : ""}Poze reale Street View · ${S.roadName} · vizibilitate: ${store[loc.id] && /^osm\+cesium/.test(store[loc.id].source) ? "clădiri OSM + model 3D Google" + (store[loc.id].source.endsWith("foto") ? ", verificat cu poza" : "") : "clădiri OSM"}`;
    }

    // ---- live: panorama Google, panoul desenat deasupra
    function openLive() {
      const shot = shots[dist]; if (!shot || shot.error || !G) return;
      if (box.classList.contains("live")) { box.classList.remove("live"); $(".slv-livebtn").textContent = "Live 360°"; paint(); return; }
      box.classList.add("live"); $(".slv-livebtn").textContent = "Înapoi la poză";
      const pov = { heading: shot.heading, pitch: shot.pitch };
      if (!live) {
        live = new G.StreetViewPanorama($(".slv-live"), { pano: shot.pano, pov, zoom: 1, addressControl: false, fullscreenControl: false,
          motionTracking: false, motionTrackingControl: false, showRoadLabels: false, zoomControl: false, scrollwheel: false, panControl: false });
        ["pov_changed", "zoom_changed", "position_changed"].forEach(ev => live.addListener(ev, () => requestAnimationFrame(drawLive)));
        new ResizeObserver(() => requestAnimationFrame(drawLive)).observe(box);
      } else { live.setPano(shot.pano); live.setPov(pov); live.setZoom(1); }
    }
    function drawLive() {
      if (!live || !S || !box.classList.contains("live")) return;
      const cv = $(".slv-over"), dpr = window.devicePixelRatio || 1, W = cv.clientWidth, H = cv.clientHeight;
      cv.width = W * dpr; cv.height = H * dpr;
      const g = cv.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, W, H);
      const p = live.getPosition(), pov = live.getPov(); if (!p) return;
      drawBoard(g, S, { pos: S.L({ lat: p.lat(), lon: p.lng() }), heading: pov.heading, pitch: pov.pitch, dz: (shots[dist] && shots[dist].dz) || 0,
        fov: 180 / Math.pow(2, live.getZoom() || 1), W, H }, ad);
    }

    // butoanele existente 150 / 80 / 30 m din pagina lui P4
    document.querySelectorAll("[data-distance]").forEach(b => b.addEventListener("click", () => {
      dist = +b.dataset.distance;
      if (box.classList.contains("live")) { box.classList.remove("live"); $(".slv-livebtn").textContent = "Live 360°"; }
      paint(); showVis();
    }));
    $(".slv-pin").onchange = load;
    $(".slv-mount").onchange = load;
    $(".slv-livebtn").onclick = openLive;
    $(".slv-maskbtn").onclick = () => { showMask = !showMask; $(".slv-maskbtn").textContent = showMask ? "Ascunde zonele" : "Zone acoperite"; paint(); };
    load();
  }

  window.SightLineView = { buildScene, visibility, viewpoint, DISTANCES, ready, log: LOG, _cz: () => cz };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
