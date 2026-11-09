"""Cele 6 criterii ale scorului publicitar. Fiecare întoarce un Criteriu cu scor 0-100 (sau fără scor),
măsurătorile care îl justifică și recomandări legate de problema măsurată.

Un criteriu fără scor e:
- „neaplicabil”: caracteristica sigur nu există în reclamă;
- „neevaluabil”: algoritmul nu a putut determina rezultatul (de ex. OCR-ul nu a găsit text — asta
  nu dovedește că reclama nu are text)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import cv2
import numpy as np

from . import config as C
from .ocr import gol_intre


@dataclass
class Criteriu:
    id: str
    nume: str
    scor: int | None          # 0-100; None dacă e neaplicabil sau neevaluabil
    pondere: int
    valoare: float | None     # măsurătoarea principală
    unitate: str
    nivel: str                # "bun" | "acceptabil" | "slab" | "neaplicabil" | "neevaluabil"
    parere: str
    recomandare: str | None = None              # prima recomandare (compatibilitate)
    status: str = "evaluat"                      # "evaluat" | "incert" | "neaplicabil" | "neevaluabil"
    masuratori: dict = field(default_factory=dict)
    recomandari: list[str] = field(default_factory=list)


@dataclass
class Context:
    W: int
    H: int
    D: float                       # √(W·H): dimensiunea de referință, independentă de format
    regiuni: list[dict]            # regiunile de text publicitar (fără watermark)
    randuri: list[dict]
    blocuri: list[dict]
    bloc_dominant: dict | None
    regiune_dominanta: dict | None
    gri_mic: np.ndarray            # imagine gri, lățime AGL_LATIME
    k_mic: float                   # factor original → gri_mic


# --- Utilitare -------------------------------------------------------------------------------------

def _interp(x: float, puncte: tuple) -> float:
    xs, ys = puncte
    return float(np.interp(x, xs, ys))


def _nivel(scor: float) -> str:
    return "bun" if scor >= 75 else "acceptabil" if scor >= 50 else "slab"


def _combina(componente: dict[str, float | None], ponderi: dict[str, float]) -> float | None:
    disponibile = {k: v for k, v in componente.items() if v is not None}
    if not disponibile:
        return None
    return sum(v * ponderi[k] for k, v in disponibile.items()) / sum(ponderi[k] for k in disponibile)


def _agrega(ctx: Context, valori: list[float | None]) -> float | None:
    """60% regiunea dominantă + 40% media tuturor regiunilor, ponderată cu importanța lor."""
    perechi = [(r, v) for r, v in zip(ctx.regiuni, valori) if v is not None]
    if not perechi:
        return None
    medie = float(np.average([v for _, v in perechi], weights=[r["importanta"] for r, _ in perechi]))
    dom = next((v for r, v in perechi if r is ctx.regiune_dominanta), None)
    return medie if dom is None else C.PONDERE_REGIUNE_DOMINANTA * dom + (1 - C.PONDERE_REGIUNE_DOMINANTA) * medie


def _neaplicabil(id_: str, motiv: str) -> Criteriu:
    """Caracteristica sigur nu există în reclamă."""
    return Criteriu(id_, C.NUME[id_], None, C.PONDERI[id_], None, "", "neaplicabil", motiv, status="neaplicabil")


def _neevaluabil(id_: str, motiv: str) -> Criteriu:
    """Algoritmul nu a putut determina rezultatul; nu înseamnă scor 0 și nici că elementul lipsește."""
    return Criteriu(id_, C.NUME[id_], None, C.PONDERI[id_], None, "", "neevaluabil", motiv, status="neevaluabil")


_FARA_TEXT_DETECTAT = ("OCR-ul nu a detectat text, deci criteriul nu a putut fi evaluat. Asta nu dovedește că "
                       "reclama nu are text: textul poate fi stilizat, prea mic sau slab contrastat pentru OCR.")


def _final(id_, scor, valoare, unitate, parere, recomandari, status, masuratori) -> Criteriu:
    scor = int(round(float(np.clip(scor, 0, 100))))
    return Criteriu(id_, C.NUME[id_], scor, C.PONDERI[id_], None if valoare is None else round(float(valoare), 3),
                    unitate, _nivel(scor), parere, recomandari[0] if recomandari else None, status,
                    masuratori, recomandari)


def _citat(text: str, n: int = 40) -> str:
    return f"„{text if len(text) <= n else text[:n - 1] + '…'}”"


# --- 1. Lizibilitatea textului ------------------------------------------------------------------------

def lizibilitate(ctx: Context) -> Criteriu:
    if not ctx.regiuni:
        return _neevaluabil("lizibilitate_text", _FARA_TEXT_DETECTAT)
    dom = ctx.regiune_dominanta
    for r in ctx.regiuni:
        s_dim = _interp(r["char_rel"], C.LIZ_DIM_DOMINANT if r is dom else C.LIZ_DIM_SECUNDAR)
        s_red = None
        if r["retentie"] and r["conf"] >= C.OCR_INCREDERE_SIGURA:
            pond = {w: C.OCR_PONDERI_REDIMENSIONARE[w] for w in r["retentie"]}
            s_red = 100 * sum(pond[w] * r["retentie"][w] for w in pond) / sum(pond.values())
        s_clar = None
        if r["separabilitate"] is not None:
            # separarea literelor de fundal (relativă), muchiile literelor și cât de mare e diferența de luminanță
            s_clar = (0.4 * _interp(r["separabilitate"], C.LIZ_SEPARABILITATE)
                      + 0.3 * _interp(r["claritate_muchii"] or 0.0, C.LIZ_CLARITATE_MUCHII)
                      + 0.3 * _interp(r["contrast"], C.CONTRAST_PRAGURI))
        r["lizibilitate_componente"] = {"dimensiune": round(s_dim), "redimensionare": None if s_red is None else round(s_red),
                                        "claritate": None if s_clar is None else round(s_clar)}
        r["scor_lizibilitate"] = _combina({"dimensiune": s_dim, "redimensionare": s_red, "claritate": s_clar},
                                          C.LIZ_PONDERI)
    scor = _agrega(ctx, [r["scor_lizibilitate"] for r in ctx.regiuni])

    componente = {k: _agrega(ctx, [r["lizibilitate_componente"][k] for r in ctx.regiuni])
                  for k in ("dimensiune", "redimensionare", "claritate")}
    incert = dom["conf"] < C.OCR_INCREDERE_SIGURA or dom["char_h_estimat"] or componente["redimensionare"] is None
    motive_incert = [m for m, c in ((f"încrederea OCR pentru textul dominant e {dom['conf']:.2f}",
                                     dom["conf"] < C.OCR_INCREDERE_SIGURA),
                                    ("înălțimea caracterelor dominante e doar estimată din cutia OCR", dom["char_h_estimat"]),
                                    ("imaginea e prea mică pentru testul de redimensionare", componente["redimensionare"] is None))
                     if c]

    greu = []
    for r in sorted(ctx.regiuni, key=lambda r: -r["importanta"]):
        if r["scor_lizibilitate"] is None or r["scor_lizibilitate"] >= C.LIZ_PRAG_GREU_DE_CITIT:
            continue
        comp, motive = r["lizibilitate_componente"], []
        if comp["dimensiune"] < 60:
            motive.append(f"caractere mici ({r['char_rel'] * 100:.1f}% din dimensiunea imaginii)")
        if comp["redimensionare"] is not None and comp["redimensionare"] < 60:
            cea_mai_mica = min(r["retentie"])
            motive.append(f"la lățimea de {cea_mai_mica} px OCR mai recunoaște doar "
                          f"{r['retentie'][cea_mai_mica] * 100:.0f}% din text")
        if comp["claritate"] is not None and comp["claritate"] < 60:
            motive.append("literele se separă slab de fundal sau au margini neclare")
        greu.append((r, motive or ["scor combinat scăzut"]))

    if not greu:
        parere = f"Textul se identifică ușor și la dimensiuni mici; textul dominant {_citat(dom['text'])} " \
                 f"are caractere de {dom['char_rel'] * 100:.1f}% din dimensiunea imaginii."
    else:
        parere = "Greu de citit la dimensiune redusă: " + "; ".join(
            f"{_citat(r['text'])} ({', '.join(m)})" for r, m in greu[:3]) + "."
    recomandari = []
    for r, motive in greu[:3]:
        if any(m.startswith("caractere mici") for m in motive):
            recomandari.append(f"{_citat(r['text'])} are caractere de {r['char_rel'] * 100:.1f}% din dimensiunea "
                               f"imaginii: mărește-l sau scoate-l dacă nu e esențial.")
        elif any("recunoaște doar" in m for m in motive):
            recomandari.append(f"{_citat(r['text'])} nu mai e recunoscut la dimensiune redusă: "
                               f"folosește un font mai gros și mai simplu, cu spațiere mai mare.")
        else:
            recomandari.append(f"{_citat(r['text'])} se separă slab de fundal: îngroașă literele sau "
                               f"pune textul pe un fundal uniform.")
    return _final("lizibilitate_text", scor, dom["char_rel"] * 100, "% (caracter dominant / dimensiunea imaginii)",
                  parere, recomandari, "incert" if incert else "evaluat",
                  {"componente": {k: None if v is None else round(v) for k, v in componente.items()},
                   "ponderi_componente": C.LIZ_PONDERI, "text_dominant": dom["text"],
                   "texte_greu_de_citit": [{"text": r["text"], "motive": m} for r, m in greu],
                   "motive_incertitudine": motive_incert})


# --- 2. Contrastul text–fundal --------------------------------------------------------------------------

def _categorie_contrast(c: float) -> str:
    return next(nume for prag, nume in C.CONTRAST_CATEGORII if c < prag)


def contrast(ctx: Context) -> Criteriu:
    masurate = [r for r in ctx.regiuni if r["contrast"] is not None]
    if not masurate:
        if not ctx.regiuni:
            return _neevaluabil("contrast_text", _FARA_TEXT_DETECTAT)
        return _neevaluabil("contrast_text", "Textul a fost detectat, dar literele nu au putut fi separate fiabil de "
                                             "fundal în nicio regiune, deci contrastul nu a putut fi măsurat.")
    scor = _agrega(ctx, [None if r["contrast"] is None else _interp(r["contrast"], C.CONTRAST_PRAGURI)
                         for r in ctx.regiuni])
    dom = ctx.regiune_dominanta
    pond_total = sum(r["importanta"] for r in ctx.regiuni)
    pond_incert = sum(r["importanta"] for r in ctx.regiuni if r["contrast"] is None or r["contrast_incert"])
    incert = dom["contrast"] is None or pond_incert > 0.5 * pond_total
    relevante = [r for r in masurate if r["importanta"] >= 0.1] or masurate
    slab = min(relevante, key=lambda r: r["contrast"])

    def descriere(r):
        return f"{_citat(r['text'])} {r['contrast']:.1f}:1 ({_categorie_contrast(r['contrast'])})"

    parere = (f"Contrast local al textului dominant: {descriere(dom)}" if dom["contrast"] is not None
              else "Contrastul textului dominant nu a putut fi măsurat fiabil")
    if slab is not dom:
        parere += f"; cel mai slab text important: {descriere(slab)}"
    parere += ". Raportul de luminanță (formula WCAG) e folosit doar ca reper orientativ."
    recomandari = [f"{_citat(r['text'])} are contrast local {r['contrast']:.1f}:1 față de fundalul imediat "
                   f"(sub 3:1): schimbă culoarea textului sau pune-l pe un fundal uniform."
                   for r in sorted(relevante, key=lambda r: r["contrast"]) if r["contrast"] < 3.0][:3]
    return _final("contrast_text", scor, slab["contrast"], ":1 (cel mai slab text important)", parere, recomandari,
                  "incert" if incert else "evaluat",
                  {"contrast_text_dominant": None if dom["contrast"] is None else round(dom["contrast"], 2),
                   "contrast_minim_text_important": round(slab["contrast"], 2),
                   "percentila_folosita": C.CONTRAST_PERCENTILA,
                   "fractie_masuratori_incerte": round(pond_incert / pond_total, 2) if pond_total else None})


# --- 3. Cantitatea de text -----------------------------------------------------------------------------------

_RE_TELEFON = re.compile(r"(?<![\w])\+?\d[\d\s().\-/]{6,}\d(?![\w])")
_RE_PRET = re.compile(r"(?:[€$£]\s?\d+(?:[.,]\d+)?|\d+(?:[.,]\d+)?\s?(?:%|(?:lei|ron|eur|euro|kj|kcal|kg|ml|km|min)\b|€|\$))",
                      re.I)


def tokeni(text: str) -> list[str]:
    """Cuvinte; un telefon, un preț sau un procent contează ca un singur element."""
    t = _RE_TELEFON.sub(" § ", text)
    t = _RE_PRET.sub(" § ", t)
    return re.findall(r"§|[^\W_]+(?:['’][^\W_]+)*", t)


def _treapta(n: float) -> int:
    for prag, scor in C.CANT_TREPTE:
        if n <= prag:
            return scor
    return C.CANT_PESTE


def cantitate(ctx: Context) -> Criteriu:
    if not ctx.regiuni:
        return _neevaluabil("cantitate_text", _FARA_TEXT_DETECTAT)
    ch_max = ctx.regiune_dominanta["char_h"]
    dominante = [r for r in ctx.regiuni if r["char_h"] >= C.PRAG_TEXT_DOMINANT * ch_max]
    cuv_total = sum(len(tokeni(r["text"])) for r in ctx.regiuni)
    cuv_dom = sum(len(tokeni(r["text"])) for r in dominante)
    caractere = sum(len(re.sub(r"\W", "", r["text"])) for r in ctx.regiuni)
    n_randuri, n_blocuri = len(ctx.randuri), len(ctx.blocuri)
    pondere_secundar = 1 - cuv_dom / cuv_total if cuv_total else 0.0

    baza = _treapta(cuv_total)
    aj_randuri = _interp(n_randuri, C.CANT_AJ_RANDURI)
    aj_dom = _interp(cuv_dom, C.CANT_AJ_CUVINTE_DOMINANTE)
    aj_sec = _interp(pondere_secundar, C.CANT_AJ_TEXT_SECUNDAR) if cuv_dom <= 7 else 0.0
    ajustare = float(np.clip(aj_randuri + aj_dom + aj_sec, -C.CANT_AJUSTARE_MAX, C.CANT_AJUSTARE_MAX))
    scor = baza + ajustare
    sec = round(cuv_total * C.SECUNDE_PE_CUVANT, 1)

    parere = (f"{cuv_total} cuvinte în {n_randuri} rânduri și {n_blocuri} blocuri; textul dominant are {cuv_dom} "
              f"cuvinte, restul ({pondere_secundar * 100:.0f}%) e text secundar. Scor de bază {baza} "
              f"(praguri pe numărul de cuvinte), ajustat cu {ajustare:+.0f}.")
    recomandari = []
    if cuv_total > 12:
        recomandari.append(f"Reclama are {cuv_total} cuvinte (peste 12): redu textul secundar "
                           f"({cuv_total - cuv_dom} cuvinte) la strictul necesar.")
    if cuv_dom > 7:
        recomandari.append(f"Textul dominant are {cuv_dom} cuvinte: un titlu de până la 7 cuvinte se înțelege dintr-o privire.")
    if n_randuri > 6:
        recomandari.append(f"Textul e împărțit în {n_randuri} rânduri: grupează-l în mai puține rânduri.")
    return _final("cantitate_text", scor, cuv_total, "cuvinte", parere, recomandari, "evaluat",
                  {"cuvinte_total": cuv_total, "cuvinte_text_dominant": cuv_dom, "caractere": caractere,
                   "randuri": n_randuri, "blocuri": n_blocuri, "pondere_text_secundar": round(pondere_secundar, 2),
                   "scor_baza": baza, "ajustare": round(ajustare, 1),
                   "ajustari": {"randuri": round(aj_randuri, 1), "cuvinte_dominante": round(aj_dom, 1),
                                "text_secundar": round(aj_sec, 1)},
                   "secunde_lectura_informativ": sec})


# --- 4. Aglomerarea vizuală ------------------------------------------------------------------------------------

def _dreptunghi_mic(ctx: Context, bbox, extindere: float = 0.0):
    h, w = ctx.gri_mic.shape
    k = ctx.k_mic
    return (int(np.clip((bbox[0] - extindere) * k, 0, w)), int(np.clip((bbox[1] - extindere) * k, 0, h)),
            int(np.clip(np.ceil((bbox[2] + extindere) * k), 0, w)), int(np.clip(np.ceil((bbox[3] + extindere) * k), 0, h)))


def densitate_in_jurul_textului(ctx: Context, muchii: np.ndarray) -> float | None:
    """Densitatea muchiilor într-un inel de o înălțime de caracter în jurul blocurilor importante."""
    importante = [b for b in ctx.blocuri if b["importanta"] >= 0.5]
    if not importante:
        return None
    text_masca = np.zeros(muchii.shape, dtype=bool)
    for r in ctx.regiuni:
        x0, y0, x1, y1 = _dreptunghi_mic(ctx, r["bbox"])
        text_masca[y0:y1, x0:x1] = True
    inel = np.zeros(muchii.shape, dtype=bool)
    for b in importante:
        x0, y0, x1, y1 = _dreptunghi_mic(ctx, b["bbox"], extindere=b["char_h"])
        inel[y0:y1, x0:x1] = True
    inel &= ~text_masca
    return float(muchii[inel].mean()) if inel.sum() >= 50 else None


def aglomerare(ctx: Context) -> Criteriu:
    gri = cv2.GaussianBlur(ctx.gri_mic, (5, 5), 0)
    muchii = cv2.Canny(gri, *C.AGL_CANNY) > 0
    h, w = muchii.shape
    d_mic = float(np.sqrt(h * w))

    zona_text = densitate_in_jurul_textului(ctx, muchii)

    text_masca = np.zeros(muchii.shape, dtype=np.uint8)
    for r in ctx.regiuni:
        x0, y0, x1, y1 = _dreptunghi_mic(ctx, r["bbox"])
        text_masca[y0:y1, x0:x1] = 1
    k_dil = max(3, int(round(0.02 * d_mic)) | 1)
    continut = cv2.dilate(muchii.astype(np.uint8), np.ones((k_dil, k_dil), np.uint8)) | text_masca
    ocupare = float(continut.mean())

    celule = np.array([muchii[i * h // 4:(i + 1) * h // 4, j * w // 4:(j + 1) * w // 4].mean()
                       for i in range(4) for j in range(4)])
    incarcate = float((celule > C.AGL_CELULA_INCARCATA).mean())

    k_inch = max(3, int(round(0.03 * d_mic)) | 1)
    inchis = cv2.morphologyEx(muchii.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((k_inch, k_inch), np.uint8)) | text_masca
    n, _, st, _ = cv2.connectedComponentsWithStats(inchis, connectivity=8)
    regiuni = int(sum(1 for i in range(1, n) if st[i, cv2.CC_STAT_AREA] >= C.AGL_REGIUNE_ARIE_MIN * h * w))

    componente = {
        "zona_text": None if zona_text is None else _interp(zona_text, C.AGL_ZONA_TEXT),
        "ocupare": _interp(ocupare, C.AGL_OCUPARE),
        "distributie": _interp(incarcate, C.AGL_DISTRIBUTIE),
        "regiuni": _interp(regiuni, C.AGL_REGIUNI),
    }
    scor = _combina(componente, C.AGL_PONDERI)
    parere = (f"{incarcate * 16:.0f} din 16 zone ale imaginii sunt încărcate cu detalii; ~{regiuni} regiuni vizuale "
              f"distincte; {ocupare * 100:.0f}% din suprafață e ocupată de text și elemente")
    parere += "." if zona_text is None else f"; densitatea detaliilor în jurul textului important: {zona_text * 100:.0f}%."
    recomandari = []
    if zona_text is not None and zona_text > C.AGL_PRAG_FUNDAL_INCARCAT:
        recomandari.append(f"În jurul textului {_citat(ctx.bloc_dominant['text'])} densitatea detaliilor e "
                           f"{zona_text * 100:.0f}% (peste {C.AGL_PRAG_FUNDAL_INCARCAT * 100:.0f}%): simplifică sau "
                           f"estompează fundalul în zona textului.")
    if regiuni > 10:
        recomandari.append(f"~{regiuni} regiuni vizuale concurează pentru atenție: grupează sau elimină elemente.")
    if incarcate > 0.5:
        recomandari.append(f"{incarcate * 16:.0f} din 16 zone sunt încărcate: lasă zone libere în jurul mesajului.")
    return _final("aglomerare", scor, zona_text if zona_text is not None else ocupare,
                  "densitate muchii în jurul textului" if zona_text is not None else "fracție ocupată",
                  parere, recomandari, "evaluat",
                  {"componente": {k: None if v is None else round(v) for k, v in componente.items()},
                   "ponderi_componente": C.AGL_PONDERI,
                   "densitate_muchii_in_jurul_textului": None if zona_text is None else round(zona_text, 3),
                   "fractie_ocupata": round(ocupare, 3), "celule_incarcate_din_16": int(round(incarcate * 16)),
                   "densitati_celule_4x4": [round(float(c), 3) for c in celule], "regiuni_vizuale": regiuni,
                   "densitate_muchii_globala": round(float(muchii.mean()), 3)})


# --- 5. Ierarhia tipografică --------------------------------------------------------------------------------

def ierarhie(ctx: Context) -> Criteriu:
    if not ctx.blocuri:
        return _neevaluabil("ierarhie", _FARA_TEXT_DETECTAT)
    dom = ctx.bloc_dominant
    altele = [b for b in ctx.blocuri if b is not dom]
    fragmente_dom = sum(1 for r in dom["regiuni"] if r["char_h"] >= C.IER_PRAG_CONCURENT * dom["char_h"])
    fragmentat = fragmente_dom > C.IER_FRAGMENTE_MAX or len(dom["randuri"]) > 3
    mai_mare = [b for b in altele if b["char_h"] > dom["char_h"]]

    if not altele:
        raport, s_dif, s_conc, niveluri, concurente = None, 100.0, 100.0, 1, []
        s_str = 100.0
    else:
        raport = dom["char_h"] / float(np.median([b["char_h"] for b in altele]))
        s_dif = _interp(raport, C.IER_RAPORT)
        marimi = sorted((b["char_h"] for b in ctx.blocuri), reverse=True)
        niveluri = 1 + sum(1 for a, b in zip(marimi, marimi[1:]) if a / b >= C.IER_PRAG_NIVEL)
        s_str = float(C.IER_STRUCTURA_NIVELURI.get(niveluri, C.IER_STRUCTURA_MULTE))
        concurente = [b for b in altele if b["char_h"] >= C.IER_PRAG_CONCURENT * dom["char_h"]]
        s_conc = float(C.IER_CONCURENTA.get(len(concurente), C.IER_CONCURENTA_MULTE))
    if fragmentat:
        s_str = max(0.0, s_str - C.IER_PENALIZARE_FRAGMENTARE)
    scor = _combina({"diferentiere": s_dif, "structura": s_str, "concurenta": s_conc}, C.IER_PONDERI)

    if not altele:
        parere = f"Un singur bloc de text ({_citat(dom['text'])}): nu există text secundar cu care să concureze."
    else:
        parere = (f"Blocul dominant vizual {_citat(dom['text'])} are caractere de {raport:.1f}× mediana celorlalte "
                  f"blocuri; {niveluri} niveluri de mărime; {len(concurente)} blocuri concurente de mărime apropiată.")
    if mai_mare:
        parere += (f" Atenție: {_citat(mai_mare[0]['text'])} are caractere mai mari, dar e mai puțin dominant "
                   f"(suprafață, contrast sau poziție); textul cel mai mare nu e neapărat mesajul principal.")
    recomandari = []
    if raport is not None and raport < 1.5:
        recomandari.append(f"Titlul {_citat(dom['text'])} e doar de {raport:.1f}× mai mare decât textul secundar: "
                           f"un raport de ~1,5–2× îl face clar dominant.")
    if concurente:
        recomandari.append(f"{len(concurente)} blocuri au aproape aceeași mărime cu titlul "
                           f"({', '.join(_citat(b['text'], 25) for b in concurente[:3])}): micșorează-le ca să nu concureze.")
    if fragmentat:
        recomandari.append(f"Titlul e fragmentat în {fragmente_dom} regiuni / {len(dom['randuri'])} rânduri: "
                           f"adună-l într-un bloc compact.")
    return _final("ierarhie", scor, raport if raport is not None else 1.0, "× (titlu / mediana celorlalte blocuri)",
                  parere, recomandari, "evaluat",
                  {"componente": {"diferentiere": round(s_dif), "structura": round(s_str), "concurenta": round(s_conc)},
                   "ponderi_componente": C.IER_PONDERI, "bloc_dominant": dom["text"],
                   "raport_dominant_secundar": None if raport is None else round(raport, 2),
                   "niveluri_de_marime": niveluri, "blocuri_concurente": [b["text"] for b in concurente],
                   "titlu_fragmentat": fragmentat, "blocuri": len(ctx.blocuri)})


# --- 6. Separarea elementelor -----------------------------------------------------------------------------------

def separare(ctx: Context) -> Criteriu:
    if not ctx.blocuri:
        return _neevaluabil("separare", _FARA_TEXT_DETECTAT)
    bl = ctx.blocuri
    pond = [b["importanta"] for b in bl]

    vecin, coliziuni = [], []
    for i, a in enumerate(bl):
        cel_mai_aproape = None
        for j, b in enumerate(bl):
            if j == i:
                continue
            g = gol_intre(a["bbox_glife"], b["bbox_glife"]) / min(a["char_h"], b["char_h"])
            cel_mai_aproape = g if cel_mai_aproape is None else min(cel_mai_aproape, g)
            if j > i and g < C.SEP_COLIZIUNE:
                coliziuni.append((a, b, g))
        vecin.append(cel_mai_aproape)
    if len(bl) > 1:
        s_dist = float(np.average([_interp(g, C.SEP_DISTANTA) for g in vecin], weights=pond))
    else:
        s_dist = 100.0
    s_col = max(0.0, 100.0 - C.SEP_PENALIZARE_COLIZIUNE * len(coliziuni))

    margini = [min(b["bbox_glife"][0], b["bbox_glife"][1], ctx.W - b["bbox_glife"][2], ctx.H - b["bbox_glife"][3])
               / b["char_h"] for b in bl]
    s_marg = float(np.average([_interp(m, C.SEP_MARGINE) for m in margini], weights=pond))
    scor = _combina({"distante": s_dist, "coliziuni": s_col, "margini": s_marg}, C.SEP_PONDERI)

    parere = f"{len(bl)} blocuri de text"
    if len(bl) > 1:
        i_min = int(np.argmin(vecin))
        parere += f"; cea mai mică distanță între blocuri: {vecin[i_min]:.2f} înălțimi de caracter " \
                  f"(la {_citat(bl[i_min]['text'], 25)})"
    parere += f"; {len(coliziuni)} coliziuni; marginea minimă față de marginea imaginii: {min(margini):.2f} înălțimi de caracter."
    recomandari = []
    for a, b, g in coliziuni[:2]:
        recomandari.append(f"{_citat(a['text'], 25)} și {_citat(b['text'], 25)} sunt la {g:.2f} înălțimi de caracter "
                           f"unul de altul: mărește spațierea dintre ele.")
    for b, m in zip(bl, margini):
        if m < 0.1 and b["importanta"] >= 0.3:
            recomandari.append(f"{_citat(b['text'], 25)} e la {m:.2f} înălțimi de caracter de marginea imaginii: "
                               f"risc de decupare la tipar; depărtează-l de margine.")
    if len(bl) > 1 and s_dist < 60 and not coliziuni:
        recomandari.append("Blocurile de text sunt apropiate între ele: mărește spațierea ca să se citească separat.")
    return _final("separare", scor, min(vecin) if len(bl) > 1 else min(margini),
                  "înălțimi de caracter (cea mai mică distanță)", parere, recomandari, "evaluat",
                  {"componente": {"distante": round(s_dist), "coliziuni": round(s_col), "margini": round(s_marg)},
                   "ponderi_componente": C.SEP_PONDERI,
                   "distanta_la_cel_mai_apropiat_bloc": [None if g is None else round(g, 2) for g in vecin],
                   "coliziuni": len(coliziuni), "margini_in_inaltimi_de_caracter": [round(m, 2) for m in margini]})
