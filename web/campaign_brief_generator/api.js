/*
 * Campaign Brief Generator — generare cu AI prin API gratuit.
 *
 * API: Pollinations (https://text.pollinations.ai/openai) — gratuit, fără cont și fără cheie API,
 * apelabil direct din browser. (Fără cont nu sunt permiși parametri ca `seed`: cererea primește 402.)
 *
 * Răspunsul e verificat înainte de afișare:
 *  - structura JSON fixă, numărul de elemente și limitele de cuvinte;
 *  - artefactele de formatare (markdown, ghilimele, liniuțe de listă) sunt eliminate;
 *  - fără statistici, dimensiuni sau distanțe inventate, fără pași specifici doar panourilor digitale
 *    sau tipărite, fără obligații legale în checklist, fără repetarea textuală a datelor introduse.
 * Câmpurile respinse se completează din generatorul local (brief.js); dacă serviciul nu răspunde,
 * tot brief-ul e generat local.
 */
(function (global) {
  "use strict";

  const URL_API = "https://text.pollinations.ai/openai";
  const TIMEOUT_MS = 35000;
  const NUME_SERVICIU = "Pollinations.ai";
  const L = () => global.CampaignBrief.LIMITE_REZULTAT;

  const OBIECTIV_AI = {
    awareness: "Brand Awareness (recunoașterea numelui, nu vânzarea imediată)",
    vanzari: "Vânzări (o acțiune concretă: cumpărare, comandă, vizită)",
    lansare: "Lansare produs (produsul nou e vedeta)",
    eveniment: "Promovare eveniment (data, locul și participarea sunt esențiale)",
  };

  function construiesteMesaje(date) {
    const sistem = `Ești director de creație pentru panouri outdoor. Răspunzi DOAR cu JSON valid, în română naturală și corectă, cu diacritice, exact cu această structură:
{"strategie":{"directie":"direcția de comunicare, o propoziție","diferentiator":"ce merită evidențiat, o propoziție","explicatie":"de ce, maximum 60 de cuvinte"},
"concepte":[{"nume":"2-3 cuvinte","slogan":"maximum 8 cuvinte","descriere":"ce se vede concret pe panou","motiv":"de ce se potrivește campaniei"},{"nume":"","slogan":"","descriere":"","motiv":""},{"nume":"","slogan":"","descriere":"","motiv":""}],
"recomandari":["o propoziție completă despre lizibilitate","o propoziție completă despre ierarhia vizuală","o propoziție completă despre mesaj","o propoziție completă despre compoziție"],
"checklist":["maximum 5 verificări practice, fiecare o propoziție completă, specifică acestei campanii"]}
Reguli:
- Nu repeta datele primite; interpretează-le și propune ceva concret.
- Nu inventa avantaje sau caracteristici ale produsului: nu promite viteză, timp de livrare, preț, calitate sau alte beneficii care nu apar în date. Nu folosi cifre care nu apar în date. Diferențiatorul se bazează doar pe ce a declarat utilizatorul; dacă nu a declarat nimic, spune ce ar trebui clarificat.
- Cele 3 concepte au idei vizuale cu adevărat diferite (de ex. produs, situație de folosire, tipografie), nu reformulări.
- Evită clișeele de marketing („calitate superioară”, „cel mai bun”, „experiență unică”). Preferă idei specifice.
- Nu inventa statistici, rezultate comerciale, dimensiuni, distanțe sau specificații ale panoului.
- Nu presupune că panoul e digital sau tipărit. Fără obligații legale sau cerințe tehnice în checklist.
- Text scurt, fără markdown.`;

    const utilizator = [
      `Brand: ${date.brand}`,
      `Produs/serviciu: ${date.produs}`,
      `Ce îl diferențiază: ${date.diferentiator ? date.diferentiator : "nespecificat"}`,
      `Public țintă: ${date.publicTinta}`,
      `Obiectiv: ${OBIECTIV_AI[date.obiectiv]}`,
      `Ton: ${global.CampaignBrief.TONURI[date.ton]}`,
    ].join("\n");

    return [{ role: "system", content: sistem }, { role: "user", content: utilizator }];
  }

  // ---------- curățare și verificare ----------

  function extrageJSON(text) {
    if (typeof text !== "string") throw new Error("răspuns gol");
    const inceput = text.indexOf("{");
    const sfarsit = text.lastIndexOf("}");
    if (inceput < 0 || sfarsit <= inceput) throw new Error("răspunsul nu conține JSON");
    return JSON.parse(text.slice(inceput, sfarsit + 1));
  }

  // elimină artefactele de formatare: markdown, numerotare, liniuțe de listă, ghilimele exterioare
  function curataFormatare(valoare) {
    if (typeof valoare !== "string") return null;
    let t = valoare
      .replace(/\*\*|__|`+|~~/g, "")
      .replace(/^\s*(#{1,6}\s*|[-*•▪●]\s+|\d+[.)]\s+)/u, "")
      .replace(/\s+/g, " ")
      .trim();
    t = t.replace(/^[„"“'«]+|["”'»]+$/gu, "").trim();
    return t || null;
  }

  function cuvinte(text) {
    return text.split(/\s+/).filter(Boolean);
  }

  function numereDinDate(date) {
    return new Set(`${date.brand} ${date.produs} ${date.publicTinta} ${date.diferentiator || ""}`.match(/\d+(?:[.,]\d+)?/g) || []);
  }

  // statistici, dimensiuni, distanțe sau promisiuni cu cifre inventate.
  // `strict`: orice cifră care nu apare în date e respinsă (strategie, concepte);
  // altfel sunt permise numerele mici, pentru sfaturi ca „cel mult 7 cuvinte” (recomandări, checklist).
  function areInventii(text, permise, strict) {
    if (/%|procent/iu.test(text)) return true;
    if (/(studii(le)?|statistic\w*|cercetări(le)?|sondaje(le)?) (arată|indică|demonstrează)/iu.test(text)) return true;
    if (/\d+(?:[.,]\d+)?\s?(m|cm|mm|metri|metru|km|px|dpi|secunde|sec|minute|min|ore|h)\b/iu.test(text)
        && !(text.match(/\d+(?:[.,]\d+)?/g) || []).every((n) => permise.has(n))) return true;
    return (text.match(/\d+(?:[.,]\d+)?/g) || [])
      .some((n) => !permise.has(n) && (strict || Number(n.replace(",", ".")) > 10));
  }

  // clișee de marketing și superlative goale
  const CLISEE = /\b(calitate superioar\w*|superior\w*|cel mai bun|cea mai bună|cei mai buni|experiență unică|de neuitat|unic\w* pe piață|revoluționar\w*|inegalabil\w*|de top|excepțional\w*|perfect\w*|ultra-?\w*)\b/iu;

  // pași valabili doar pentru un anumit tip de panou (tipul nu e specificat)
  const SPECIFIC_TIP_PANOU = /\b(digital\w*|ecran\w*|LED|animați\w*|animat\w*|spot(ul|uri|urile)?|loop|rotați\w*|luminozitat\w*|pixel\w*|px|video|tipar\w*|tipări\w*|imprimar\w*|imprimat\w*|print\w*|vinil\w*|mesh|finisaj\w*|laminar\w*|material\w*|intemperi\w*|buton\w*|click|clic|dpi|CMYK|RGB)\b/iu;
  // obligații legale sau cerințe tehnice neverificate
  const LEGAL_TEHNIC = /\b(leg(e|ea|i|ii|al\w*)|autoriza\w*|aviz\w*|permis\w*|primări\w*|reglement\w*|GDPR|ANPC|copyright|drept(uri)? de autor|licen\w*|conform\w*|standard\w*|rezoluți\w*|format(ul)? fișier\w*)\b/iu;

  // textul care copiază aproape cuvânt cu cuvânt datele introduse nu aduce nimic nou
  function repetaDatele(text, date) {
    const t = text.toLowerCase();
    return [date.publicTinta, date.diferentiator].some((camp) => camp && camp.length > 25 && t.includes(camp.toLowerCase()));
  }

  function text(valoare, maximCaractere, permise, date, optiuni = {}) {
    const t = curataFormatare(valoare);
    if (!t || t.length < 3 || t.length > maximCaractere) return null;
    if (optiuni.minimCuvinte && cuvinte(t).length < optiuni.minimCuvinte) return null;
    if (areInventii(t, permise, optiuni.strict) || CLISEE.test(t) || repetaDatele(t, date)) return null;
    return t;
  }

  // recomandările și verificările trebuie să fie propoziții complete (cel puțin 4 cuvinte)
  function lista(valoare, maxim, permise, date, filtru) {
    if (!Array.isArray(valoare)) return [];
    const vazute = new Set();
    return valoare
      .map((v) => text(v, 260, permise, date, { minimCuvinte: 4 }))
      .filter((v) => v && !(filtru && filtru.test(v)) && !vazute.has(v.toLowerCase()) && vazute.add(v.toLowerCase()))
      .slice(0, maxim);
  }

  // completează o listă AI cu elemente locale până la `tinta`, fără dubluri
  function completeaza(ai, local, tinta) {
    const rezultat = [...ai];
    for (const punct of local) {
      if (rezultat.length >= tinta) break;
      if (!rezultat.some((x) => x.toLowerCase() === punct.toLowerCase())) rezultat.push(punct);
    }
    return rezultat;
  }

  function conceptValid(c, permise, date) {
    if (!c || typeof c !== "object") return null;
    const strict = { strict: true };
    const concept = {
      nume: text(c.nume, 50, permise, date, strict),
      slogan: text(c.slogan, 90, permise, date, { strict: true, minimCuvinte: 2 }),
      descriere: text(c.descriere ?? c.ideeVizuala, 320, permise, date, { strict: true, minimCuvinte: 5 }),
      motiv: text(c.motiv ?? c.avantaj, 320, permise, date, { strict: true, minimCuvinte: 4 }),
    };
    if (Object.values(concept).some((v) => v == null)) return null;
    if (cuvinte(concept.slogan).length > L().MAX_CUVINTE_SLOGAN) return null;
    if (SPECIFIC_TIP_PANOU.test(concept.descriere)) return null;
    return concept;
  }

  /**
   * Combină răspunsul AI cu brief-ul local: fiecare câmp valid din AI îl înlocuiește pe cel local;
   * câmpurile lipsă sau respinse rămân din generarea locală.
   */
  function combina(brut, date, local) {
    const permise = numereDinDate(date);
    const rezultat = JSON.parse(JSON.stringify(local));
    const locale = [];
    const raspuns = brut && typeof brut === "object" ? brut : {};

    const st = raspuns.strategie && typeof raspuns.strategie === "object" ? raspuns.strategie : {};
    const strict = { strict: true, minimCuvinte: 4 };
    const explicatie = text(st.explicatie, 600, permise, date, strict);
    const strategieAI = {
      directie: text(st.directie, 300, permise, date, strict),
      // diferențiatorul nu poate fi inventat: dacă utilizatorul nu a declarat unul, rămâne textul local
      diferentiator: date.diferentiator ? text(st.diferentiator, 300, permise, date, { strict: true }) : null,
      explicatie: explicatie && cuvinte(explicatie).length <= L().MAX_CUVINTE_EXPLICATIE ? explicatie : null,
    };
    for (const [cheie, valoare] of Object.entries(strategieAI)) {
      if (valoare) rezultat.strategie[cheie] = valoare;
      else if (cheie !== "diferentiator" || date.diferentiator) locale.push(`strategie.${cheie}`);
    }

    // concepte: exact 3, cu nume, slogan și descriere diferite
    const concepte = [];
    for (const c of Array.isArray(raspuns.concepte) ? raspuns.concepte : []) {
      const valid = conceptValid(c, permise, date);
      if (!valid || concepte.length >= 3) continue;
      const repetat = concepte.some((x) => ["nume", "slogan", "descriere"].some((k) => x[k].toLowerCase() === valid[k].toLowerCase()));
      if (!repetat) concepte.push(valid);
    }
    if (concepte.length < 3) locale.push("concepte");
    for (const c of local.concepte) {
      if (concepte.length >= 3) break;
      if (!concepte.some((x) => x.nume.toLowerCase() === c.nume.toLowerCase() || x.slogan.toLowerCase() === c.slogan.toLowerCase())) {
        concepte.push(c);
      }
    }
    rezultat.concepte = concepte;

    const recomandari = lista(raspuns.recomandari, L().MAX_RECOMANDARI, permise, date, SPECIFIC_TIP_PANOU);
    if (recomandari.length < 2) locale.push("recomandari");
    rezultat.recomandari = recomandari.length >= 2 ? recomandari : local.recomandari;

    const checklist = lista(raspuns.checklist, L().MAX_CHECKLIST, permise, date,
      new RegExp(`${SPECIFIC_TIP_PANOU.source}|${LEGAL_TEHNIC.source}`, "iu"));
    if (checklist.length < 3) locale.push("checklist");
    rezultat.checklist = checklist.length >= 3 ? completeaza(checklist, local.checklist, 3) : local.checklist;

    return { brief: rezultat, campuriLocale: locale };
  }

  // ---------- apel ----------

  async function genereazaCuAI(dateBrute) {
    const date = Object.assign({}, dateBrute, { diferentiator: (dateBrute.diferentiator || "").trim() });
    const local = global.CampaignBrief.genereazaBrief(date);
    const controller = new AbortController();
    const limita = setTimeout(() => controller.abort(), TIMEOUT_MS);
    try {
      const raspuns = await fetch(URL_API, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model: "openai",
          messages: construiesteMesaje(date),
          response_format: { type: "json_object" },
          private: true,
        }),
        signal: controller.signal,
      });
      if (!raspuns.ok) throw new Error(`serviciul AI a răspuns cu eroarea ${raspuns.status}`);
      const continut = (await raspuns.json())?.choices?.[0]?.message?.content;
      const { brief, campuriLocale } = combina(extrageJSON(continut), date, local);
      return { brief, sursa: campuriLocale.length === 0 ? "ai" : "partial", campuriLocale };
    } catch (error) {
      const motiv = error.name === "AbortError"
        ? `serviciul AI nu a răspuns în ${TIMEOUT_MS / 1000} de secunde`
        : error instanceof TypeError ? "nu există conexiune la serviciul AI" : error.message;
      return { brief: local, sursa: "local", motiv };
    } finally {
      clearTimeout(limita);
    }
  }

  global.CampaignBriefAI = { genereazaCuAI, NUME_SERVICIU, _combina: combina };
})(window);
