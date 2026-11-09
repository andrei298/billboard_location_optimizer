/*
 * Campaign Brief Generator — validarea și generatorul local (rezervă pentru api.js).
 *
 * api.js generează brief-ul cu AI; acest fișier validează datele și construiește brief-ul din reguli
 * și șabloane alese după obiectiv și ton. E folosit când serviciul AI nu răspunde și pentru câmpurile
 * respinse la verificarea răspunsului AI.
 *
 * Structura brief-ului: strategie (direcție, diferențiator, explicație), 3 concepte creative,
 * maximum 4 recomandări outdoor și maximum 5 verificări în checklist.
 * Nu se generează statistici, date demografice, avantaje nedeclarate ale produsului sau specificații de panou.
 */
(function (global) {
  "use strict";

  const OBIECTIVE = {
    awareness: "Brand Awareness",
    vanzari: "Vânzări",
    lansare: "Lansare produs",
    eveniment: "Promovare eveniment",
  };

  const TONURI = {
    profesional: "Profesional",
    prietenos: "Prietenos",
    premium: "Premium",
    energic: "Energic",
  };

  const LIMITE = {
    brand: [2, 60],
    produs: [2, 80],
    public: [10, 400],
    diferentiator: [3, 300],
  };

  const MAX_RECOMANDARI = 4;
  const MAX_CHECKLIST = 5;
  const MAX_CUVINTE_EXPLICATIE = 60;
  const MAX_CUVINTE_SLOGAN = 8;

  // ---------- validare ----------

  function valideaza(date) {
    const erori = {};
    const brand = (date.brand || "").trim();
    const produs = (date.produs || "").trim();
    const publicTinta = (date.publicTinta || "").trim();
    const diferentiator = (date.diferentiator || "").trim();

    if (!brand) erori.brand = "Introdu numele brandului.";
    else if (brand.length < LIMITE.brand[0]) erori.brand = "Numele brandului trebuie să aibă cel puțin 2 caractere.";
    else if (brand.length > LIMITE.brand[1]) erori.brand = "Numele brandului poate avea cel mult 60 de caractere.";
    else if (!/[\p{L}\p{N}]/u.test(brand)) erori.brand = "Numele brandului trebuie să conțină litere sau cifre.";

    if (!produs) erori.produs = "Descrie produsul sau serviciul promovat.";
    else if (produs.length < LIMITE.produs[0]) erori.produs = "Produsul trebuie descris în cel puțin 2 caractere.";
    else if (produs.length > LIMITE.produs[1]) erori.produs = "Descrierea produsului poate avea cel mult 80 de caractere; păstrează doar esențialul.";
    else if (!/\p{L}/u.test(produs)) erori.produs = "Descrierea produsului trebuie să conțină cuvinte.";

    // opțional: se validează doar dacă e completat
    if (diferentiator && diferentiator.length < LIMITE.diferentiator[0]) {
      erori.diferentiator = "Descrie diferențiatorul în cel puțin 3 caractere sau lasă câmpul gol.";
    } else if (diferentiator.length > LIMITE.diferentiator[1]) {
      erori.diferentiator = "Diferențiatorul poate avea cel mult 300 de caractere.";
    }

    if (!publicTinta) erori.publicTinta = "Descrie publicul țintă.";
    else if (publicTinta.length < LIMITE.public[0]) erori.publicTinta = "Descrie publicul în cel puțin 10 caractere (de ex. cine sunt și ce îi interesează).";
    else if (publicTinta.length > LIMITE.public[1]) erori.publicTinta = "Descrierea publicului poate avea cel mult 400 de caractere.";

    if (!OBIECTIVE[date.obiectiv]) erori.obiectiv = "Alege obiectivul campaniei.";
    if (!TONURI[date.ton]) erori.ton = "Alege tonul comunicării.";
    return erori;
  }

  // ---------- utilitare text ----------

  function cuvinte(text) {
    return text.split(/\s+/).filter(Boolean);
  }

  // cuvinte scrise intenționat cu majuscule în interior (ex. „BMW”, „iX”, „iPhone”) rămân neschimbate
  function scrisIntentionat(text) {
    const primul = cuvinte(text)[0] || "";
    return /\p{Lu}/u.test(primul.slice(1));
  }

  function majuscula(text) {
    if (scrisIntentionat(text)) return text;
    return text.charAt(0).toLocaleUpperCase("ro-RO") + text.slice(1);
  }

  function inPropozitie(text) {
    if (scrisIntentionat(text)) return text;
    return text.charAt(0).toLocaleLowerCase("ro-RO") + text.slice(1);
  }

  const CUVINTE_LEGATURA = new Set(["cu", "din", "de", "la", "în", "pe", "pentru", "și", "a", "al", "ale", "cel", "cea",
    "cei", "cele", "fără", "sau", "ori", "prin", "despre", "între", "spre", "lui", "o", "un", "una"]);

  // descrierea întreagă până la 5 cuvinte; altfel primele 5, fără cuvinte de legătură la final
  function scurt(text) {
    const lista = cuvinte(text.replace(/[.!?;:,]+$/u, ""));
    if (lista.length <= 5) return lista.join(" ");
    const rezultat = lista.slice(0, 5);
    while (rezultat.length > 1 && CUVINTE_LEGATURA.has(rezultat[rezultat.length - 1].toLocaleLowerCase("ro-RO"))) {
      rezultat.pop();
    }
    return rezultat.join(" ").replace(/[,;:]+$/u, "");
  }

  function limiteazaCuvinte(text, maxim) {
    const lista = cuvinte(text);
    return lista.length <= maxim ? text : lista.slice(0, maxim).join(" ").replace(/[,;:]+$/u, "") + ".";
  }

  function hash(text) {
    let h = 2166136261;
    for (let i = 0; i < text.length; i += 1) {
      h ^= text.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return h >>> 0;
  }

  function generatorAleator(seed) {
    let a = seed || 1;
    return function () {
      a = (a + 0x6d2b79f5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function alege(lista, cate, aleator) {
    const disponibile = [...lista];
    const rezultat = [];
    while (rezultat.length < cate && disponibile.length > 0) {
      rezultat.push(disponibile.splice(Math.floor(aleator() * disponibile.length), 1)[0]);
    }
    return rezultat;
  }

  function completeaza(sablon, v) {
    return sablon
      .replaceAll("{brand}", v.brand)
      .replaceAll("{Produs}", majuscula(v.produs))
      .replaceAll("{produs}", inPropozitie(v.produs))
      .replaceAll("{ton}", v.ton || "");
  }

  // ---------- sloganuri (formulate fără acord gramatical cu produsul) ----------

  const SLOGANURI_OBIECTIV = {
    awareness: ["Când spui {produs}, spui {brand}.", "{Produs}? Gândește-te la {brand}.",
      "Pentru {produs}, există {brand}.", "{brand}. De azi, îl ții minte."],
    vanzari: ["Alege {brand}. Alege azi.", "{brand}. Comandă acum.", "Azi e ziua pentru {produs}.",
      "{Produs} de la {brand}. Acum."],
    lansare: ["Nou de la {brand}: {produs}.", "{brand} prezintă: {produs}.", "Fii printre primii. {brand}.",
      "S-a lansat: {produs}."],
    eveniment: ["{brand} te invită: {produs}.", "Nu rata: {produs}.", "Notează în calendar: {produs}.",
      "{Produs}. Fii acolo."],
  };

  const SLOGANURI_TON = {
    profesional: ["{brand}. Soluția clară pentru {produs}.", "Precizie în {produs}. {brand}.", "{brand}. Seriozitate, de la primul contact."],
    prietenos: ["{brand} e aici pentru tine.", "Hai la {brand}!", "Cu drag, de la {brand}."],
    premium: ["{brand}: excelență, fără compromis.", "{Produs}. Semnătura {brand}.", "Detaliile fac diferența. {brand}."],
    energic: ["Pornește acum. {brand}!", "Mai mult ritm. Mai mult {brand}.", "Energie pură: {brand}!"],
  };

  // ---------- strategie ----------

  const DIRECTIE = {
    awareness: "Construiește asocierea dintre {brand} și {produs} printr-o singură imagine ușor de recunoscut, repetată consecvent.",
    vanzari: "Pune în față motivul de a alege acum {produs} și un singur pas clar către cumpărare.",
    lansare: "Prezintă {produs} ca noutate: produsul la vedere, semnalul „Nou” și un motiv de a-l încerca.",
    eveniment: "Transformă panoul într-o invitație la {produs}: numele, data, locul și un motiv clar de a veni.",
  };

  const MOTIV_OBIECTIV = {
    awareness: "La notorietate contează ca numele să fie recunoscut și asociat cu produsul, nu detaliile.",
    vanzari: "Un panou văzut din mers convinge doar dacă motivul și pasul următor sunt evidente imediat.",
    lansare: "O noutate atrage atenția doar dacă produsul se vede clar și se înțelege ce e nou.",
    eveniment: "Fără dată și loc citite din prima, invitația se pierde, oricât de atrăgător e vizualul.",
  };

  const NUANTA_TON = {
    profesional: "Tonul profesional cere afirmații sobre, care pot fi susținute.",
    prietenos: "Tonul prietenos cere o formulare caldă, ca o invitație directă.",
    premium: "Tonul premium cere puține elemente și mult spațiu liber.",
    energic: "Tonul energic cere formulări scurte, cu ritm.",
  };

  // indicii în descrierea publicului → cum se comunică (nu date demografice)
  const INDICII_PUBLIC = [
    { regex: /tiner|student|adolesc|elev|gen ?z/iu, eticheta: "publicul tânăr", comunicare: "vrea mesaje foarte scurte și vizual puternic" },
    { regex: /famil|părin|parin|copii|mame|mămic|tați/iu, eticheta: "familiile", comunicare: "răspund la beneficii practice, de zi cu zi" },
    { regex: /șofer|sofer|navetiș|navetis|trafic|mașin|masin/iu, eticheta: "cei aflați în trafic", comunicare: "au nevoie de un mesaj înțeles dintr-o privire" },
    { regex: /business|antreprenor|manager|compan|firm|profesioni|corporat|b2b/iu, eticheta: "publicul profesional", comunicare: "caută argumente concrete și credibile" },
    { regex: /sport|fitness|alerg|sal[aă]|antrenament/iu, eticheta: "publicul activ", comunicare: "răspunde la energie și mișcare" },
    { regex: /senior|pension|vârstn|varstn/iu, eticheta: "publicul matur", comunicare: "are nevoie de claritate, text mare și ton respectuos" },
    { regex: /cartier|local|oraș|oras|bucureșt|bucurest|zon[aă]/iu, eticheta: "publicul local", comunicare: "devine mai atent la referințele locale" },
    { regex: /lux|exclusiv|premium|venit(uri)? mari/iu, eticheta: "publicul exigent", comunicare: "apreciază o estetică rafinată și un mesaj minimal" },
    { regex: /buget|preț|pret|reducer|economi|ofert/iu, eticheta: "publicul atent la preț", comunicare: "vrea beneficiul de preț spus clar" },
  ];

  // ---------- concepte creative (idei diferite ca abordare, nu reformulări) ----------

  const CONCEPTE = {
    primPlan: { nume: "Prim-plan",
      descriere: "{Produs}, foarte aproape, pe un fundal uni în culorile {brand}, cu logo-ul alături.",
      motiv: "Se înțelege dintr-o privire ce promovezi; nimic nu concurează cu produsul." },
    moment: { nume: "Momentul potrivit",
      descriere: "O singură scenă din viața publicului, chiar în clipa în care {produs} își face rostul.",
      motiv: "Publicul se recunoaște în scenă, iar beneficiul se vede fără explicații." },
    inainteDupa: { nume: "Înainte și după",
      descriere: "Panoul împărțit în două: aceeași situație fără {produs} și cu {produs}.",
      motiv: "Contrastul dintre cele două jumătăți arată beneficiul fără text suplimentar." },
    tipografic: { nume: "Doar cuvinte",
      descriere: "Fără imagine: un titlu foarte mare pe culoarea {brand}, cu logo-ul jos.",
      motiv: "Se citește cel mai repede și se remarcă printre panourile încărcate." },
    detaliu: { nume: "Detaliul uriaș",
      descriere: "Un singur detaliu din {produs}, mărit mult peste mărimea reală, care umple panoul.",
      motiv: "Scara neobișnuită oprește privirea și face panoul ușor de ținut minte." },
    invitatie: { nume: "Invitația directă",
      descriere: "Un personaj care privește spre trecător și îl invită, cu un gest clar, spre {produs}.",
      motiv: "Privirea directă atrage atenția și face apelul la acțiune personal." },
    data: { nume: "Data în centru",
      descriere: "Data și locul scrise foarte mare, ca element principal, cu numele {produs} deasupra.",
      motiv: "Informația esențială e prima citită: publicul pleacă știind când și unde." },
    dezvaluire: { nume: "Dezvăluirea",
      descriere: "Imaginea cu {produs}, pe jumătate acoperită de o bandă cu „Nou” scris mare.",
      motiv: "Curiozitatea îi face pe oameni să caute produsul ca să-l vadă complet." },
  };

  const CONCEPTE_OBIECTIV = {
    awareness: ["primPlan", "moment", "tipografic", "detaliu"],
    vanzari: ["primPlan", "inainteDupa", "invitatie", "tipografic"],
    lansare: ["dezvaluire", "primPlan", "detaliu", "moment"],
    eveniment: ["data", "invitatie", "moment", "tipografic"],
  };

  // ---------- recomandări outdoor (lizibilitate, ierarhie, mesaj, compoziție) ----------

  const IERARHIE = {
    awareness: "Ierarhie: imaginea principală, apoi numele {brand}; restul textului poate lipsi.",
    vanzari: "Ierarhie: beneficiul, apoi apelul la acțiune, apoi logo-ul; oferta, dacă există, imediat după beneficiu.",
    lansare: "Ierarhie: produsul, semnalul „Nou”, apoi numele {brand}.",
    eveniment: "Ierarhie: numele evenimentului, data și locul, apoi organizatorul.",
  };

  const COMPOZITIE = {
    profesional: "Compoziție: grilă aliniată, 1–2 culori de bază și un singur accent.",
    prietenos: "Compoziție: culori calde, forme rotunjite și oameni sau situații familiare.",
    premium: "Compoziție: mult spațiu liber, paletă restrânsă și o singură imagine de foarte bună calitate.",
    energic: "Compoziție: culori saturate, contrastante, și o direcție de mișcare (de ex. diagonală).",
  };

  // ---------- checklist (verificări practice, specifice campaniei) ----------

  const VERIFICARE_OBIECTIV = {
    awareness: "Acoperă imaginea de pe machetă: numele {brand} trebuie să rămână ușor de citit și de reținut.",
    vanzari: "Confirmă că oferta și canalul de comandă de pe panou sunt valabile pe toată perioada afișării.",
    lansare: "Confirmă că {produs} se poate găsi acolo unde trimite panoul, din prima zi de afișare.",
    eveniment: "Verifică pe machetă data, ora și locul evenimentului, caracter cu caracter.",
  };

  // ---------- generare ----------

  function genereazaBrief(dateBrute) {
    const date = {
      brand: dateBrute.brand.trim(),
      produs: dateBrute.produs.trim().replace(/[.!?]+$/u, ""),
      publicTinta: dateBrute.publicTinta.trim().replace(/\s+/g, " "),
      diferentiator: (dateBrute.diferentiator || "").trim().replace(/\s+/g, " ").replace(/[.!?]+$/u, ""),
      obiectiv: dateBrute.obiectiv,
      ton: dateBrute.ton,
    };
    const v = { brand: date.brand, produs: date.produs, ton: TONURI[date.ton].toLowerCase() };
    const vScurt = { brand: date.brand, produs: scurt(date.produs) };
    const aleator = generatorAleator(hash(JSON.stringify(date).toLocaleLowerCase("ro-RO")));
    const indicii = INDICII_PUBLIC.filter((i) => i.regex.test(date.publicTinta));

    // strategie
    const explicatie = [
      MOTIV_OBIECTIV[date.obiectiv],
      date.diferentiator ? "Diferențiatorul declarat devine argumentul principal, spus într-o singură propoziție." : "",
      indicii.length ? `${majuscula(indicii[0].eticheta)} ${indicii[0].comunicare}.` : "",
      NUANTA_TON[date.ton],
    ].filter(Boolean).join(" ");
    const strategie = {
      directie: completeaza(DIRECTIE[date.obiectiv], v),
      diferentiator: date.diferentiator
        ? majuscula(date.diferentiator) + "."
        : "Nespecificat. Completează câmpul despre ce diferențiază produsul, ca strategia să se sprijine pe un avantaj real.",
      explicatie: limiteazaCuvinte(explicatie, MAX_CUVINTE_EXPLICATIE),
    };

    // 3 concepte diferite, fiecare cu un slogan propriu de cel mult 8 cuvinte
    const sloganuri = alege([...SLOGANURI_OBIECTIV[date.obiectiv], ...SLOGANURI_TON[date.ton]], 8, aleator)
      .map((s) => completeaza(s, vScurt))
      .filter((s) => cuvinte(s).length <= MAX_CUVINTE_SLOGAN);
    sloganuri.push(`${date.brand}.`, `${date.brand}. Acum.`, `Alege ${date.brand}.`);
    const concepte = alege(CONCEPTE_OBIECTIV[date.obiectiv], 3, aleator).map((cheie, i) => ({
      nume: CONCEPTE[cheie].nume,
      slogan: sloganuri[i],
      descriere: completeaza(CONCEPTE[cheie].descriere, v),
      motiv: CONCEPTE[cheie].motiv,
    }));

    // recomandări outdoor: lizibilitate, ierarhie, mesaj, compoziție
    const recomandari = [
      "Lizibilitate: titlul de cel mult 7 cuvinte, cu litere mari și contrast puternic față de fundal.",
      completeaza(IERARHIE[date.obiectiv], v),
      date.diferentiator
        ? `Mesaj: un singur argument pe panou — ${inPropozitie(scurt(date.diferentiator))}; restul informațiilor rămân pe alte canale.`
        : "Mesaj: o singură idee pe panou; restul informațiilor rămân pe site, în magazin sau pe alte canale.",
      COMPOZITIE[date.ton],
    ].slice(0, MAX_RECOMANDARI);

    // checklist: verificări practice, specifice campaniei
    const checklist = [
      `Arată macheta câtorva oameni din publicul țintă și verifică dacă înțeleg mesajul dintr-o privire.`,
      completeaza(VERIFICARE_OBIECTIV[date.obiectiv], v),
      date.diferentiator
        ? "Verifică dacă afirmația despre diferențiator poate fi susținută în fața clienților."
        : `Alege unul dintre cele trei concepte și verifică dacă sloganul lui se potrivește tonului ${v.ton}.`,
      "Verifică ortografia, diacriticele și datele de contact de pe machetă.",
      "Compară macheta finală cu strategia: același mesaj și aceeași ordine de citire.",
    ].slice(0, MAX_CHECKLIST);

    return {
      titlu: `${date.brand} — ${OBIECTIVE[date.obiectiv]}`,
      strategie,
      concepte,
      recomandari,
      checklist,
    };
  }

  global.CampaignBrief = {
    OBIECTIVE, TONURI, valideaza, genereazaBrief,
    LIMITE_REZULTAT: { MAX_RECOMANDARI, MAX_CHECKLIST, MAX_CUVINTE_EXPLICATIE, MAX_CUVINTE_SLOGAN },
  };
})(window);
