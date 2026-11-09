"""Parametrii analizei.

Toate pragurile de aici sunt repere orientative, configurabile; nu sunt adevăruri absolute.
Perechile (xs, ys) se interpolează liniar: valoarea măsurată x → scor y (0-100).
Nu se presupun dimensiuni fizice ale panoului și nu se estimează distanțe de citire în metri.
"""

# --- Ponderile criteriilor (sumă 100) -----------------------------------------------
PONDERI = {
    "lizibilitate_text": 25,
    "contrast_text": 20,
    "cantitate_text": 15,
    "aglomerare": 15,
    "ierarhie": 15,
    "separare": 10,
}

NUME = {
    "lizibilitate_text": "Lizibilitatea textului",
    "contrast_text": "Contrastul text–fundal",
    "cantitate_text": "Cantitatea de text",
    "aglomerare": "Aglomerarea vizuală",
    "ierarhie": "Ierarhia tipografică",
    "separare": "Separarea elementelor",
}

# acoperire = suma ponderilor criteriilor evaluate
#   ≥ 80%: scor final valid, afișat împreună cu acoperirea
#   60–79%: doar scor provizoriu, cu avertisment
#   < 60%: fără scor final
ACOPERIRE_VALIDA_PCT = 80
ACOPERIRE_MINIMA_PCT = 60

# --- OCR ------------------------------------------------------------------------------
OCR_BACKEND_PREFERAT = "easyocr"      # "easyocr" sau "rapidocr"; se poate schimba cu ANALIZA_OCR
OCR_LIMBI = ["en", "ro"]              # EasyOCR, alfabet latin cu diacritice românești
OCR_LATURA_MAX = 1600                 # imaginile mai mari se micșorează pentru OCR
OCR_LATURA_MICA = 640                 # imaginile mai mici se măresc pentru OCR...
OCR_LATURA_TINTA_MICI = 1000          # ...până la ~1000 px (maximum ×4)
OCR_INCREDERE_MINIMA = 0.30           # detecțiile sub acest prag sunt ignorate
OCR_INCREDERE_SIGURA = 0.60           # sub acest prag textul e marcat ca incert
OCR_CARACTERE_MINIME = 2              # detecțiile cu un singur caracter alfanumeric sunt ignorate
OCR_PRAG_PREPROCESARE = 0.50          # dacă încrederea medie e sub prag, se mai rulează o trecere cu CLAHE
OCR_LATIMI_REDIMENSIONARE = (800, 500, 300)
OCR_PONDERI_REDIMENSIONARE = {800: 0.25, 500: 0.35, 300: 0.40}
DEDUP_IOU = 0.5                       # suprapunere peste care două detecții sunt aceeași regiune
DEDUP_SIMILARITATE = 0.6              # similaritate textuală pentru suprapuneri parțiale
DEDUP_INCLUDERE = 0.8                 # o cutie inclusă în alta, cu text conținut în ea, e duplicat

# --- Măsurători pe regiuni ---------------------------------------------------------------
CONTRAST_PERCENTILA = 20              # percentila inferioară a contrastului pe zonele unei regiuni
CONTRAST_SEPARABILITATE_MIN = 0.55    # sub ea, separarea litere/fundal într-o zonă nu e fiabilă
TEXT_FRACTIE_MIN, TEXT_FRACTIE_MAX = 0.03, 0.75

# importanța unei regiuni = (caracter / caracterul dominant) ^ exponent
IMPORTANTA_EXPONENT = 1.5
IMPORTANTA_MINIMA = 0.05
PRAG_TEXT_DOMINANT = 0.6              # regiunile cu caractere ≥ 60% din cele dominante = text dominant
PONDERE_REGIUNE_DOMINANTA = 0.6       # agregare: 60% textul dominant + 40% media ponderată a tuturor

# --- 1. Lizibilitate --------------------------------------------------------------------
LIZ_PONDERI = {"dimensiune": 0.40, "redimensionare": 0.35, "claritate": 0.25}
# înălțimea caracterelor raportată la √(lățime·înălțime), ca să nu depindă de formatul imaginii
LIZ_DIM_DOMINANT = ([0.010, 0.020, 0.035, 0.055], [10, 45, 80, 100])
LIZ_DIM_SECUNDAR = ([0.004, 0.008, 0.015, 0.025], [10, 40, 75, 100])
LIZ_SEPARABILITATE = ([0.50, 0.85], [0, 100])
LIZ_CLARITATE_MUCHII = ([0.12, 0.40], [0, 100])
LIZ_PRAG_GREU_DE_CITIT = 60

# --- 2. Contrast ---------------------------------------------------------------------------
# raport de luminanță (formula WCAG, folosită doar ca reper orientativ)
CONTRAST_PRAGURI = ([1.0, 2.0, 3.0, 4.5, 7.0, 10.0], [0, 35, 60, 80, 95, 100])
CONTRAST_CATEGORII = [(2.0, "slab"), (3.0, "modest"), (4.5, "acceptabil"), (7.0, "bun"), (float("inf"), "foarte bun")]

# --- 3. Cantitate de text ---------------------------------------------------------------------
CANT_TREPTE = [(7, 100), (12, 85), (18, 65), (25, 40), (35, 20)]   # (până la N cuvinte, scor)
CANT_PESTE = 10
CANT_AJUSTARE_MAX = 10
CANT_AJ_RANDURI = ([3, 6, 10], [5, 0, -5])
CANT_AJ_CUVINTE_DOMINANTE = ([3, 7, 12], [5, 0, -5])
CANT_AJ_TEXT_SECUNDAR = ([0.0, 0.5, 0.9], [0, 2, 5])   # aplicat doar dacă textul dominant e scurt (≤ 7 cuvinte)
SECUNDE_PE_CUVANT = 0.3               # doar informativ, ca în docs/scor.md

# --- 4. Aglomerare --------------------------------------------------------------------------
AGL_LATIME = 800
AGL_CANNY = (50, 150)
AGL_PONDERI = {"zona_text": 0.35, "ocupare": 0.25, "distributie": 0.20, "regiuni": 0.20}
# Calibrare pe setul de test (Canny 50/150 după blur 5×5, la 800 px lățime): reclamele curate au densitate
# globală < 0,01; fotografiile de produs reale 0,03–0,06; fundalurile foarte încărcate ~0,07.
AGL_ZONA_TEXT = ([0.02, 0.05, 0.09, 0.14], [100, 75, 45, 15])     # densitatea muchiilor în jurul textului
AGL_OCUPARE = ([0.45, 0.70, 0.90, 1.00], [100, 75, 45, 30])       # fracția ocupată de text și elemente
AGL_CELULA_INCARCATA = 0.05                                       # densitate peste care o celulă 4×4 e încărcată
AGL_DISTRIBUTIE = ([0.25, 0.50, 0.75, 1.00], [100, 70, 45, 25])
AGL_REGIUNI = ([3, 6, 10, 18, 30], [100, 80, 55, 25, 10])
AGL_REGIUNE_ARIE_MIN = 0.005                                      # regiuni sub 0,5% din imagine sunt ignorate
AGL_PRAG_FUNDAL_INCARCAT = 0.05

# --- 5. Ierarhie ----------------------------------------------------------------------------
IER_PONDERI = {"diferentiere": 0.5, "structura": 0.3, "concurenta": 0.2}
IER_RAPORT = ([1.0, 1.25, 1.5, 2.0], [30, 55, 80, 100])           # titlu / mediana celorlalte blocuri
IER_PRAG_NIVEL = 1.25                                             # diferență de mărime care face un nivel nou
IER_STRUCTURA_NIVELURI = {1: 40, 2: 100, 3: 100, 4: 75}           # 5+ niveluri: IER_STRUCTURA_MULTE
IER_STRUCTURA_MULTE = 50
IER_PRAG_CONCURENT = 0.85                                         # bloc cu caractere ≥ 85% din titlu = concurent
IER_CONCURENTA = {0: 100, 1: 60, 2: 35}                           # 3+: IER_CONCURENTA_MULTE
IER_CONCURENTA_MULTE = 15
IER_FRAGMENTE_MAX = 4
IER_PENALIZARE_FRAGMENTARE = 25

# --- 6. Separare ----------------------------------------------------------------------------
SEP_PONDERI = {"distante": 0.45, "coliziuni": 0.30, "margini": 0.25}
SEP_DISTANTA = ([0.0, 0.3, 0.7, 1.2], [10, 45, 80, 100])          # gol / înălțimea caracterelor
SEP_COLIZIUNE = 0.15                                              # gol sub 0,15 caractere = coliziune
SEP_PENALIZARE_COLIZIUNE = 40
SEP_MARGINE = ([0.0, 0.1, 0.5, 1.0], [10, 55, 85, 100])           # margine / înălțimea caracterelor

# --- Grupare în rânduri și blocuri -------------------------------------------------------------
RAND_RAPORT_MARIME = (0.7, 1.43)
RAND_GOL_ORIZONTAL = 2.0              # în înălțimi de caracter
BLOC_RAPORT_MARIME = (0.67, 1.5)
BLOC_GOL_VERTICAL = 1.0               # în înălțimi de caracter

# --- Watermark / metadate (excluse din scor, raportate separat) --------------------------------
WATERMARK_DOMENII = (r"shutterstock|istock|gettyimages|getty images|adobe\s?stock|stock\.adobe|depositphotos|"
                     r"dreamstime|alamy|123rf|freepik|vecteezy|canstockphoto|bigstock|pond5|envato|"
                     r"pixabay|unsplash|pexels")

# --- Etichete -----------------------------------------------------------------------------------
ETICHETE = [(80, "Foarte bună"), (65, "Bună, cu mici ajustări"), (50, "Mediocră, are nevoie de modificări"),
            (0, "Slabă")]
