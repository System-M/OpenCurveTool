# Open Curve Tool – Spezifikation

**Deutsch** · [English](SPECIFICATION.en.md)

Stand: 2026-11-17 · Status: v1.2 – umgesetzt (Umsetzungsstand und Abweichungen in Abschnitt 16)

Python-Desktopanwendung zur Auswertung von Indizierdaten (Zylinderdruck + PI-Stromsignal) für zwei
Zylinder „High“ und „Low“. Funktionsumfang und Bedienoberfläche sind an AVL Concerto angelehnt, damit
der Umstieg leichtfällt.

---

## 1. Ziel und Abgrenzung

### 1.1 Ziel

Aus einer zeitbasierten CSV-Messdatei werden – wie in Concerto – kurbelwinkelbasierte Arbeitsspiele
gebildet, daraus Verläufe und Kennwerte je Zyklus berechnet und über die Zyklen statistisch ausgewertet.
Die Ergebnisse werden in einer interaktiven Oberfläche dargestellt, die dem Concerto-Arbeitsablauf
folgt: Channel Browser, Layouts mit Diagrammseiten, Kurven per Drag & Drop, Messcursor, Zyklusnavigation,
Export und Bericht. Dieselbe Auswertung ist zusätzlich ohne Oberfläche als Stapellauf aufrufbar.

### 1.2 Was „realitätsnah“ hier heißt

- Gleiche **Kennwertdefinitionen und Namen** wie in der AVL-Welt üblich (PMAX, APMAX, IMEP, AI50 …).
- Gleiche **Standardverfahren** (Nullpunktkorrektur, Heizverlauf nach 1. Hauptsatz, Summenheizverlauf,
  Zyklusstatistik).
- Die internen Algorithmen von Concerto sind nicht offengelegt. Zahlengleichheit wird daher nicht
  behauptet, sondern über Toleranzen gegen einen Concerto-Referenzexport geprüft (Abschnitt 13).
- Gleiches **Bedienkonzept** (Abschnitt 10): Aufbau des Hauptfensters, Begriffe, Mausbedienung und
  die Trennung von Layout und Daten.

### 1.3 Nicht-Ziele

- Lesen/Schreiben von AVL-Dateiformaten (IFile, Concerto-Layouts etc.) – Eingabe ist ausschließlich CSV.
- Grafischer Formeleditor (CalcGraf), Skript-Automatisierung, Datenbank-/Prüfstandsdaten-Verwaltung.
- Prozessrechnung mit Wandwärme, Ladungswechselanalyse, Gastemperatur/-masse.
- Übernahme von AVL-Logos, -Icons oder Originalgrafiken; das Programm tritt unter eigenem Namen auf.

### 1.4 Name

Projekt- und Programmname ist **Open Curve Tool**. So erscheint er in Titelzeile, „About“-Dialog,
Bericht und Dokumentation. Technische Kurzform ist `opencurve` (Startskript, Paket, Einstellungsordner),
Dateiendung für Layouts `.oclayout`.

---

## 2. Begriffe

| Begriff | Bedeutung |
|---|---|
| High / Low | Die beiden Zylinder; Kürzel `H` / `L` |
| DMR | Zylinderdrucksignal |
| PI_RM | Rückmeldesignal des PI-Stroms (Stromverlauf der Ansteuerung) |
| CA / °KW | Kurbelwinkel in Grad |
| ZOT | Zünd-OT, intern 0 °KW |
| Zyklus | Ein Arbeitsspiel (720 °KW bei 4-Takt, 360 °KW bei 2-Takt) |

---

## 3. Eingabedatei

### 3.1 Spalten

| Spalte | Pflicht | Inhalt | Einheit (Default) |
|---|---|---|---|
| `Time` | ja | Zeitstempel, hohe Auflösung | s |
| `CA High` | ja¹ | Kurbelwinkel Zylinder High, 0–720 | °KW |
| `CA Low` | ja¹ | Kurbelwinkel Zylinder Low, 0–720 | °KW |
| `DMR_H` | nein | Zylinderdruck High, Rohspannung des Messverstärkers, **10 mV/bar** | V |
| `DMR_L` | nein | Zylinderdruck Low, Rohspannung des Messverstärkers, **10 mV/bar** | V |
| `PI_RM_H` | nein | PI-Strom-Rückmeldung High | A |
| `PI_RM_L` | nein | PI-Strom-Rückmeldung Low | A |
| `KLS_H` | nein | Klopfsensor High (Annahme: Körperschallsignal) | V (Annahme) |
| `KLS_L` | nein | Klopfsensor Low (Annahme: Körperschallsignal) | V (Annahme) |

Der Druck wird beim Einlesen aus der Rohspannung in bar umgerechnet. Die Empfindlichkeit ist
**parametrierbar** (`dmr_sens_mv_per_bar`, Voreinstellung 10 mV/bar), je Zylinder getrennt, über
Konfigurationsdatei und Dialog „Channel Scaling“ (10.9); eine Änderung rechnet alles neu. Alle Verläufe
und Kennwerte sind in bar. Im Datensatz `TIME` bleibt zusätzlich die Rohspannung verfügbar.
PI_RM liegt direkt in Ampere vor; Faktor und Offset sind ebenfalls einstellbar.

¹ Die Kurbelwinkelinformation ist immer vorhanden. Fehlt die CA-Spalte eines Zylinders, für den ein
DMR- oder PI_RM-Signal vorliegt, wird die Datei mit Fehlermeldung abgewiesen.

Spaltenreihenfolge beliebig. Namensabgleich tolerant: Groß-/Kleinschreibung egal, führende/nachfolgende
Leerzeichen egal, Leerzeichen ≙ Unterstrich (`CA High` = `CA_High` = `ca high`), Einheit in Klammern
wird abgetrennt (`DMR_H [bar]`). Unbekannte Spalten werden ignoriert und im Log gemeldet.

### 3.2 Format

- Trennzeichen automatisch erkannt: `;`, `,`, Tab. Dezimalzeichen automatisch: `.` oder `,`
  (Kombination `,` als Trenner **und** Dezimalzeichen wird als Fehler abgewiesen).
- Kodierung: UTF-8 (mit/ohne BOM), Fallback Latin-1.
- Erste Zeile = Spaltennamen. Eine optionale zweite Zeile mit Einheiten wird erkannt und übersprungen.
- Alle Werte werden als `float64` gelesen. `Time` wird nie gerundet oder auf `float32` reduziert;
  alle Flankenzeiten werden aus den Originalzeitstempeln interpoliert.
- Leere Zellen / `NaN`: Lücken ≤ `max_gap_samples` (Default 3) werden linear interpoliert, größere
  Lücken machen den betroffenen Zyklus ungültig.
- `Time` muss streng monoton steigen, sonst Abbruch mit Zeilennummer. Abtastrate und Jitter werden
  ermittelt und protokolliert; nicht-äquidistante Abtastung ist zulässig.
- **Die Abtastrate ist je Datei verschieden** (30 kHz bis 150 kHz) und wird immer aus `Time` bestimmt,
  nie vorausgesetzt. Alle davon abhängigen Größen (KW-Raster, Filter, Klopfband, Mindestdauern) leiten
  sich aus der ermittelten Rate ab.
- Typische Messdauer: einige Sekunden, also bis etwa 1–2 Mio. Zeilen und einige Dutzend Zyklen je
  Zylinder. Obergrenze für die Auslegung: 10 Mio. Zeilen ohne Sonderbehandlung.

### 3.3 Fehlende Spalten – Auswertumfang

Fehlen können nur die Signalspalten DMR, PI_RM und KLS. Jeder Zylinder wird unabhängig behandelt;
fehlt ein Signal, wird der Umfang reduziert, nicht abgebrochen.

| Vorhandenes Signal | Auswertung für diesen Zylinder |
|---|---|
| DMR | Druck- und Verbrennungskennwerte, Klopfen aus dem Druck (7.4) |
| PI_RM | PI-Kennwerte in °KW und ms (Abschnitt 8) |
| DMR und PI_RM | zusätzlich Zündverzug (Ansteuerbeginn → Brennbeginn) |
| KLS | Klopfsensor-Kennwerte (7.5) |
| keines | Zylinder wird übersprungen (Hinweis) |

Fehlen DMR, PI_RM **und** KLS für beide Zylinder → Abbruch mit Fehlermeldung.

### 3.4 Referenzformat (Beispieldatei)

Aus einem Ausschnitt einer echten Messdatei abgeleitet (die Datei selbst ist nicht Teil des
Repositories); dieses Format ist der Hauptanwendungsfall und muss ohne Importdialog-Korrektur
geladen werden. Die Zeilen unten stammen aus dem Testdatengenerator und zeigen nur das Format:

```
Time,CA High,CA Low,DMR_L,DMR_H,PI_RM_L,PI_RM_H,KLS_H,KLS_L
0.0,275,35,2.0098258853,1.1325708446,1.9951874232,1.9802063610,…
3.3333333333333335e-05,275,35,1.9997641788,1.1328852729,2.0175835712,1.9943145317,…
6.666666666666667e-05,275,35,1.9897024722,1.1325708446,2.0047559503,2.0007805861,…
```

| Merkmal | Befund |
|---|---|
| Trennzeichen / Dezimalzeichen | `,` / `.`; keine Einheitenzeile |
| Zahlenformat | Volle `float64`-Darstellung, teils Exponentialschreibweise (`e-05`) |
| Spaltenreihenfolge | CA erst High, dann Low; Signale erst `_L`, dann `_H` |
| Abtastung | Äquidistant, Start bei 0; im Ausschnitt Δt = 1/30000 s (30 kHz), andere Dateien bis 150 kHz |
| Kurbelwinkel | **Ganzzahlig in 1°-Stufen**, je Stufe etwa 3 Abtastwerte; Wertebereich 0–720 (4-Takt) |
| Bezug High/Low | Beide CA-Spalten schalten gleichzeitig mit festem Versatz (im Testdatensatz CA Low = CA High − 240°) |
| Signalauflösung | DMR in Stufen der Wandlerauflösung, Größenordnung 0,03 bar |
| Ruhelagen | DMR mit Spannungsoffset in der Größenordnung von 1 V (rechnerisch rund 100 bar); PI_RM mit Messoffset von einigen Ampere |

Echte Messdateien enthalten zusätzlich die Spalten `kls_h` und `kls_l` (Groß-/Kleinschreibung ist
beim Einlesen egal); der Testdatengenerator hängt sie als `KLS_H`, `KLS_L` an.

Daraus folgt: Der Winkel zwischen den Stufen muss rekonstruiert werden (5.1), das KW-Raster richtet
sich nach der Abtastdichte (5.3), und die Klopfauswertung ist bei 30 kHz auf Frequenzen unter 12 kHz
begrenzt (7.4).
Eine solche Ruhelage kann kein Absolutdruck sein; das Drucksignal trägt also einen Offset und
braucht zwingend eine Nullpunktkorrektur (5.4).

**OT-Lage (bestätigt):** Der Dateiwert 0 ist der Zünd-OT, für High und Low jeweils im eigenen
Kurbelwinkel. Dazu passt der Ausschnitt: kurz nach 0° liegt der Druck über der Ruhelage und fällt
(Expansion), um 270° ist er konstant auf Ruhelage (Ausschieben).

---

## 4. Konfiguration

TOML-Datei (`--config`), alle Werte optional; ohne Geometrie entfallen die volumenabhängigen Kennwerte
(Abschnitt 7.3). Gemeinsame Werte unter `[engine]`, Abweichungen je Zylinder unter `[cyl.H]` / `[cyl.L]`.

```toml
[engine]
strokes        = 4          # 4 (CA 0–720, Referenzdaten) | 2 | "auto" (aus CA-Hub erkannt)
name           = "Jenbacher Type 6 (assumed)"   # Voreinstellung, siehe 6.1
bore_mm        = 190.0      # veröffentlicht
stroke_mm      = 220.0      # veröffentlicht
conrod_mm      = 425.0      # ANNAHME, nicht veröffentlicht
compression    = 11.0       # Verdichtungsverhältnis ε; versionsabhängig (10,5 … 11,0 gefunden)
pin_offset_mm  = 0.0        # Desachsierung; Annahme
kappa          = 1.32       # Polytropen-/Isentropenexponent für Heizverlauf

[cyl.H]
ca_offset_deg  = 0.0        # wird auf CA der Datei addiert, sodass ZOT = 0 °KW
dmr_sens_mv_per_bar = 10.0  # Empfindlichkeit der Druckmesskette; p[bar] = U[V] · 1000 / Wert
dmr_offset_bar = 0.0        # nach der Umrechnung addiert
pi_factor      = 1.0        # Signal liegt bereits in A vor
pi_offset      = 0.0

[cyl.L]
ca_offset_deg  = 0.0
dmr_sens_mv_per_bar = 10.0  # je Zylinder getrennt einstellbar
dmr_offset_bar = 0.0
pi_factor      = 1.0
pi_offset      = 0.0

[ca]
resolution_deg = "auto"     # Raster der KW-basierten Verläufe: "auto" | 0.1 | 0.2 | 0.5 | 1.0
resample       = "interp"   # "interp" | "binmean"
angle_recon    = "smooth"   # Winkel zwischen Geberstufen: "smooth" | "linear"
angle_smooth_deg = 30.0     # Glättungsfenster für "smooth"
step_mode      = "floor"    # Bedeutung der Winkelstufe: "floor" | "round" (siehe 5.1)
max_gap_samples = 3         # Lücken bis zu dieser Länge werden interpoliert

[pegging]
method         = "thermo"   # "none" | "thermo" | "fixed"
ca1_deg        = -100.0
ca2_deg        = -65.0
window_deg     = 2.0        # Mittelungsfenster um ca1/ca2
kappa          = 1.32
fixed_ca_deg   = -180.0     # nur "fixed"
fixed_p_bar    = 1.0        # nur "fixed"

[filter]
pressure       = "savgol"   # "none" | "moving_average" | "savgol" | "butter"
window_deg     = 3.0        # mindestens 5 Rasterpunkte
butter_cutoff_hz = 5000

[heat_release]
start_deg      = -30.0
end_deg        = 90.0

[pi]
threshold_mode = "relative" # "relative" (Anteil vom Zyklusmaximum) | "absolute" (A)
threshold      = 0.10
hysteresis     = 0.05
min_duration_ms = 0.05
min_gap_ms     = 0.10
baseline       = "auto"     # Messoffset: "auto" (Median je Zyklus) | fester Wert in A
hydraulic_delay_ms = 0.0    # optional: Ansteuerbeginn → hydraulischer Beginn

[knock]
enabled        = true
band_hz        = [2000, 10000]   # passend zu 190 mm Bohrung, siehe 7.4
filter_order   = 4
window_deg     = [-10, 70]       # Auswertefenster
ref_window_deg = [-90, -40]      # Referenzfenster für den Rauschpegel (vor Zündung)
threshold_bar  = 1.0             # ANNAHME: KP_PK darüber = klopfender Zyklus
threshold_ratio = 0.0            # optional zusätzlich: KP_PK / KP_REF; 0 = aus

[output]
cycles         = "all"      # "all" | "10:200"
csv_delimiter  = ";"
csv_decimal    = "."
plots          = ["png"]    # "png" | "pdf" | "html"
```

---

## 5. Signalaufbereitung

Reihenfolge fest: Einlesen → Skalierung → Winkelbezug → Zykluserkennung → KW-Raster →
Nullpunktkorrektur → Filterung → Kennwerte.

### 5.1 Winkelbezug

- Interne Konvention: 4-Takt α ∈ [−360, +360), 2-Takt α ∈ [−180, +180), **0 = ZOT**.
- `α = wrap(CA_Datei + ca_offset_deg)`. Für die Referenzdaten (0–720) heißt das bei
  `ca_offset_deg = 0`: Dateiwerte 0…359 bleiben, 360…719 werden zu −360…−1. Da der Dateiwert 0 der
  Zünd-OT beider Zylinder ist, bleibt `ca_offset_deg` bei 0 und dient nur als Feinkorrektur.
- Der OT-Bezug wird **nie stillschweigend verschoben** (1 °KW OT-Fehler verfälscht IMEP um mehrere
  Prozent). Liegt das Druckmaximum des mittleren Zyklus mehr als ±40 °KW neben 0, erfolgt eine Warnung
  mit Vorschlag für `ca_offset_deg`.
- **Gestufter Kurbelwinkel** (Regelfall, siehe 3.4: 1°-Stufen bei etwa 3 Abtastwerten je Grad): Der
  Winkel jedes Abtastwerts wird aus den Stufenflanken rekonstruiert. Als Flankenzeit gilt die Mitte
  zwischen letztem Abtastwert der alten und erstem der neuen Stufe (Unsicherheit ± ½ Abtastintervall,
  hier ca. ± 0,15 °KW bei 1500 1/min und 30 kHz).
  - `linear`: zeitlineare Interpolation zwischen benachbarten Flanken.
  - `smooth` (Default): zusätzlich Ausgleich der Flankenzeiten über ein gleitendes Fenster
    (`angle_smooth_deg`), da sich die Drehzahl innerhalb weniger Grad kaum ändert. Das verringert den
    Winkelfehler aus der Zeitrasterung deutlich; wichtig für IMEP und Heizverlauf.
- Schalten beide CA-Spalten gleichzeitig, wird der feste Versatz High/Low ermittelt und protokolliert;
  er wird nicht vorausgesetzt.
- **Bedeutung der Stufe (`step_mode`):** Bei `floor` (Voreinstellung) steht der Dateiwert N für den
  Bereich N…N+1, der Wert N ist also an der Stufenflanke gerade erreicht. Bei `round` steht N für
  N−0,5…N+0,5. Welche Konvention das Messsystem verwendet, ist nicht bekannt (Abschnitt 15); die falsche
  Wahl verschiebt alle Winkel um 0,5 °KW.

### 5.2 Zykluserkennung

- Zyklusgrenze = Rücksprung des **internen** Winkels α von +360 auf −360, also im Ladungswechsel-OT –
  nicht der Rücksprung des Dateiwerts von 720 auf 0. Liegt der Zünd-OT beim Dateiwert 0, fiele der
  Rücksprung der Datei mitten in die Verbrennung und würde sie auf zwei Zyklen verteilen.
  Je Zylinder getrennt, da eigener CA; die Zyklen von High und Low sind dadurch zeitlich versetzt.
- Unvollständige Zyklen am Dateianfang/-ende werden verworfen. Nummerierung ab 1.
- Plausibilität: Zyklusdauer weicht > 20 % vom Median ab, CA nicht monoton innerhalb des Zyklus oder
  NaN-Lücke → Zyklus `valid = 0`, bleibt in der Tabelle, fließt nicht in die Statistik ein.
- Je Zyklus: Startzeit, Dauer, mittlere Drehzahl `n = (Periode/360) / T_Zyklus · 60` [1/min].

### 5.3 KW-Raster

`resolution_deg = "auto"` wählt die kleinste Rasterweite aus 0,1 / 0,2 / 0,5 / 1,0 °KW, die nicht feiner
ist als der mittlere Winkelabstand zweier Abtastwerte – es wird also nicht feiner gerastert, als die
Messung auflöst (1500 1/min: bei 30 kHz 0,30 °KW je Abtastwert → Raster 0,5 °KW; bei 150 kHz
0,06 °KW → Raster 0,1 °KW). Ein fester Wert ist
einstellbar; liegt er unter der Abtastdichte, erscheint ein Hinweis.

Jeder Zyklus wird auf ein äquidistantes Raster (`resolution_deg`) gebracht:
`interp` = lineare Interpolation, `binmean` = Mittelwert aller Samples je Winkelschritt (bei starker
Überabtastung rauschärmer). Die Rohdaten bleiben für zeitbasierte Auswertungen (PI-Flanken, Klopfen)
unverändert erhalten.

### 5.4 Nullpunktkorrektur (Druck)

- `thermo` (Default): polytrope Verdichtung zwischen α1 und α2,
  `Δp = (p2·V2^n − p1·V1^n) / (V1^n − V2^n)`, `p_korr = p + Δp`, je Zyklus; p1/p2 als Fenstermittel.
  Benötigt Geometrie.
- `fixed`: Fenstermittel bei `fixed_ca_deg` wird auf `fixed_p_bar` gesetzt.
- `none`: Signal ist bereits Absolutdruck. Für die Referenzdaten ungeeignet, da das Rohsignal einen
  Offset in der Größenordnung von 100 bar trägt (3.4).
- Fällt `thermo` mangels Geometrie aus → automatisch `fixed` mit Warnung; ohne Korrektur wären PMAX
  und alle Druckverläufe um den Offset verfälscht.
- Die ermittelte Korrektur wird je Zyklus als Kennwert `POFFS` [bar] ausgegeben, damit eine Drift des
  Messverstärkers über die Messung sichtbar ist.

### 5.5 Filterung

Gefilterter Druck wird **nur** für Ableitung und Heizverlauf verwendet (nullphasig). PMAX, IMEP und
Klopfkennwerte werden aus dem ungefilterten Signal bestimmt.

---

## 6. Geometrie

```
Vh   = π/4 · B² · S                Vc = Vh / (ε − 1)          r = S/2
s(α) = sqrt((l + r)² − e²) − r·cos α − sqrt(l² − (r·sin α − e)²)
V(α) = Vc + π/4 · B² · s(α)
```

`dV/dα` analytisch bzw. zentraler Differenzenquotient auf dem KW-Raster. Die Geometrie gilt für High
und Low gemeinsam; eine Abweichung je Zylinder bleibt einstellbar.

### 6.1 Voreinstellung: Jenbacher Typ 6

Bis die Daten des konkreten Motors vorliegen, ist ein Jenbacher Typ 6 (J612/J616/J620/J624)
voreingestellt. Alle Werte sind in „Engine Parameters“ änderbar.

| Parameter | Wert | Herkunft |
|---|---|---|
| Bohrung `BORE` | 190 mm | Veröffentlichtes Datenblatt |
| Hub `STROKE` | 220 mm | Veröffentlichtes Datenblatt |
| Hubvolumen je Zylinder | 6,24 l | Datenblatt; stimmt mit π/4 · 190² · 220 überein |
| Arbeitsverfahren `NRSTROKE` | 4-Takt, V 60° | Datenblatt; passt zu CA 0–720 |
| Nenndrehzahl | 1500 1/min | Datenblatt; passt zur Beispieldatei (ca. 3,3 Abtastwerte je Grad bei 30 kHz) |
| Verdichtung `EPSILON` | 11,0 | **Versionsabhängig:** 11,0 in Datenblättern zum 620 GS-N.L, 10,5 beim J620 GS-J701 |
| Pleuellänge `CONROD` | 425 mm | **Annahme**, nicht veröffentlicht; aus üblichem Schubstangenverhältnis r/l ≈ 0,26 |
| Desachsierung `PINOFF` | 0 mm | Annahme |
| κ Heizverlauf / Nullpunktkorrektur | 1,32 | Annahme für Magergasbetrieb |

Einordnung der beiden Annahmen: Die Pleuellänge beeinflusst IMEP praktisch nicht und die
Umsatzpunkte nur gering. Die Verdichtung wirkt dagegen direkt auf Nullpunktkorrektur und Heizverlauf;
ein Fehler von 0,5 Einheiten verschiebt Druckniveau und Wärmeumsatz spürbar. Solange diese Werte nicht
bestätigt sind, kennzeichnet der Bericht die Thermodynamik-Kennwerte mit „assumed engine data“.

Das Brennverfahren (fremdgezündeter Magergasmotor mit gespülter Vorkammer, Mitteldruck 22–24 bar)
dient nur der Plausibilisierung; es geht nicht in die Rechnung ein.

---

## 7. Kennwerte aus dem Zylinderdruck (je Zyklus)

### 7.1 Ohne Geometrie berechenbar

| Name | Einheit | Definition |
|---|---|---|
| `PMAX` | bar | Maximaldruck |
| `APMAX` | °KW | Lage von PMAX aus einer Ausgleichsparabel über ± 3 °KW (unempfindlich gegen Rauschen am flachen Maximum) |
| `RMAX` | bar/°KW | Maximaler Druckanstieg dp/dα |
| `ARMAX` | °KW | Lage von RMAX |
| `SPEED` | 1/min | Mittlere Drehzahl des Zyklus |
| `POFFS` | bar | Nullpunktkorrektur des Zyklus (5.4) |

Dazu kommen die Klopfkennwerte nach 7.4; auch sie brauchen keine Geometrie.

### 7.2 Mit Geometrie

| Name | Einheit | Definition |
|---|---|---|
| `IMEP` | bar | `1/Vh · ∮ p dV` über den ganzen Zyklus |
| `IMEPH` | bar | Hochdruckschleife (−180…+180 °KW), nur 4-Takt |
| `IMEPL` | bar | Ladungswechselschleife (= IMEP − IMEPH), nur 4-Takt |
| `Q(α)` | J/°KW | Heizverlauf: `κ/(κ−1)·p·dV/dα + 1/(κ−1)·V·dp/dα` |
| `I(α)` | J | Summenheizverlauf: `∫ Q dα` über [`start_deg`, `end_deg`] |
| `QMAX`, `AQMAX` | J/°KW, °KW | Maximum des Heizverlaufs und Lage |
| `IMAX` | J | Maximum des Summenheizverlaufs (umgesetzte Wärme) |
| `AI05`, `AI10`, `AI50`, `AI90` | °KW | Lage 5/10/50/90 % Umsatz; 0 % = Minimum, 100 % = Maximum von `I` |
| `SOC` | °KW | Brennbeginn = `AI05` |
| `BD` | °KW | Brenndauer `AI90 − AI10` |
| `POLY_C`, `POLY_E` | – | Polytropenexponent Kompression/Expansion (Regression log p / log V), Diagnosewert |

Umsatzpunkte werden zwischen den Rasterpunkten linear interpoliert.

### 7.3 Fehlende Geometrie

Nur Abschnitt 7.1 und 7.4 werden berechnet; Spalten aus 7.2 bleiben leer; einmalige Warnung im Log und
im Bericht.

### 7.4 Klopfauswertung

Standardmäßig aktiv für jeden Zylinder mit DMR-Signal. Sie arbeitet zeitbasiert auf dem ungefilterten,
in bar umgerechneten Drucksignal, nicht auf dem KW-Raster.

1. **Bandpass:** Butterworth (`filter_order`), nullphasig, Band `band_hz`.
2. **Auswertefenster** `window_deg` je Zyklus (Voreinstellung −10…+70 °KW).
3. **Referenzfenster** `ref_window_deg` (Voreinstellung −90…−40 °KW, vor der Zündung): liefert den
   Rauschpegel der Messkette im selben Band.

| Name | Einheit | Definition |
|---|---|---|
| `KP_PK` | bar | Spitzenwert des Betrags des bandpassgefilterten Drucks im Auswertefenster |
| `AKP_PK` | °KW | Lage von `KP_PK` |
| `KP_INT` | bar·°KW | Integral des Betrags über das Auswertefenster |
| `KP_REF` | bar | Spitzenwert des Betrags im Referenzfenster |
| `KP_RATIO` | – | `KP_PK / KP_REF` |
| `KNOCK` | 0/1 | Klopfender Zyklus: `KP_PK > threshold_bar`; mit `threshold_ratio > 0` zusätzlich `KP_RATIO > threshold_ratio` |

Der bandpassgefilterte Druck steht als Kanal `PKNK_H/L` in den Datensätzen `CA` und `TIME` zur Anzeige
bereit.

**Band und Abtastrate.** Die Voreinstellung 2–10 kHz ist auf die Bohrung von 190 mm abgestimmt (erste
Brennraum-Eigenfrequenz nach eigener Abschätzung rund 3 kHz). Ausgewertet wird nur, wenn die
Abtastrate ≥ 2,5 × obere Bandgrenze ist, hier also ab 25 kHz; sonst wird das Band mit Hinweis gekürzt.

**Grenzen der Aussage:**

- Die Schwelle von 1,0 bar ist eine Annahme und muss für den Motor festgelegt werden; `KP_PK` und
  `KP_INT` selbst hängen nicht von ihr ab.
- Bei 30 kHz werden Anteile über 15 kHz nicht erfasst und können, falls die Messkette kein
  Anti-Aliasing-Filter hat, ins Band zurückfalten. Für Klopfuntersuchungen sind Messungen ab 50 kHz
  vorzuziehen.
- Die Wandlerauflösung von ca. 0,031 bar (3.4) begrenzt die kleinste erkennbare Amplitude.
- Der Bandpass unterscheidet nicht zwischen Klopfen und anderen Schwingungen im Band, etwa Resonanzen
  eines Indizierkanals oder der Sensoraufnahme. `KP_REF` und `KP_RATIO` machen solche Dauerschwingungen
  sichtbar.

### 7.5 Klopfsensor (KLS)

Die Spalten `KLS_H` / `KLS_L` werden als Körperschall-Klopfsensor gedeutet (Annahme, ebenso die
Einheit Volt). Das Signal wird unverändert als Kanal `KLS_H/L` bereitgestellt und – wenn die
Klopfauswertung aktiv ist – mit demselben Band, Auswerte- und Referenzfenster wie der Druck (7.4)
ausgewertet. Geometrie und Drucksignal sind dafür nicht nötig.

| Name | Einheit | Definition |
|---|---|---|
| `KLS_PK` | V | Spitzenwert des Betrags des bandpassgefilterten Sensorsignals im Auswertefenster |
| `AKLS_PK` | °KW | Lage von `KLS_PK` |
| `KLS_INT` | V·°KW | Integral des Betrags über das Auswertefenster |
| `KLS_REF` | V | Spitzenwert im Referenzfenster (Grundrauschen) |
| `KLS_RATIO` | – | `KLS_PK / KLS_REF` |

Das gefilterte Signal steht als `KLSF_H/L` zur Anzeige bereit. Aus dem Klopfsensor wird **keine**
Klopfentscheidung abgeleitet; `KNOCK` und `KNOCK_FREQ` beruhen allein auf dem Druck. Die Laufzeit des
Körperschalls zum Sensor wird nicht korrigiert.

---

## 8. Kennwerte aus dem PI-Stromsignal

### 8.1 Ereigniserkennung

1. Das Signal trägt einen Messoffset von einigen Ampere. Er wird je Zyklus als Median des
   Signals außerhalb der Ansteuerungen bestimmt (`baseline = "auto"`) oder fest vorgegeben und vom
   Signal abgezogen; Rauschen = MAD. Der Kanal `PI` im Datensatz `CA` ist offsetkorrigiert, der
   Rohkanal `PI_RM` im Datensatz `TIME` bleibt unverändert.
2. Ereignis = Bereich über Schwelle; Ende erst unter `Schwelle − Hysterese`.
3. Ereignisse kürzer als `min_duration_ms` werden verworfen; Lücken kürzer als `min_gap_ms`
   verschmelzen (Boost-/Haltephase und Stromregelung sollen nicht als mehrere Ansteuerungen zählen).
4. Flankenzeit = linear interpolierter Schwellendurchgang zwischen zwei Samples (Subsample-genau,
   deshalb die volle Zeitauflösung aus `Time`). Winkel aus der Zeit über die α(t)-Zuordnung.

### 8.2 Kennwerte

Je Zyklus `PI_N` (Anzahl Ansteuerungen), je Ereignis *k* = 1…N (Spaltensuffix `_1`, `_2`, …):

| Name | Einheit | Definition |
|---|---|---|
| `PI_SOE_k` | °KW | Ansteuerbeginn (steigende Flanke) |
| `PI_EOE_k` | °KW | Ansteuerende (fallende Flanke) |
| `PI_TSOE_k`, `PI_TEOE_k` | s | Dieselben Flanken als Absolutzeit |
| `PI_DUR_k` | ms | Ansteuerdauer |
| `PI_DURA_k` | °KW | Ansteuerdauer als Winkel |
| `PI_IPK_k` | A | Spitzenstrom |
| `PI_TPK_k` | ms | Zeit von Ansteuerbeginn bis Spitzenstrom |
| `PI_IHOLD_k` | A | Mittlerer Haltestrom (Median der zweiten Ereignishälfte) |
| `PI_RISE_k` | ms | Anstiegszeit 10 → 90 % des Spitzenstroms |
| `PI_CHG_k` | A·ms | Stromintegral über das Ereignis |

Alle Stromwerte (`PI_IPK`, `PI_IHOLD`, `PI_CHG`) sind offsetkorrigiert, also echte Ströme. Zusätzlich
je Zyklus `PI_OFFS` [A]: der abgezogene Messoffset, zur Kontrolle auf Drift.

Kombiniert mit DMR: `IGD` = `SOC − PI_SOE_1` in °KW und `IGD_T` in ms (Zündverzug, bezogen auf den
elektrischen Ansteuerbeginn; mit `hydraulic_delay_ms` wahlweise auf den hydraulischen Beginn).

Hinweis: Das Stromsignal zeigt die **elektrische** Ansteuerung, nicht den hydraulischen Einspritzbeginn.

---

## 9. Statistik und Zylindervergleich

- Für jeden skalaren Kennwert über alle gültigen Zyklen: Mittelwert, Standardabweichung, Min, Max,
  COV [%], Anzahl.
- Bei Messungen von wenigen Sekunden liegen nur einige Dutzend Zyklen vor. Die Zyklenzahl steht deshalb
  neben jedem Statistikwert; unter 10 gültigen Zyklen wird der COV mit Hinweis gekennzeichnet.
- Für jeden Verlauf (p, Q, I, PI-Strom): mittlerer Zyklus, Min-/Max-Hüllkurve, Standardabweichung.
- Klopfen: `KNOCK_FREQ` [%] = Anteil klopfender an den gültigen Zyklen; für `KP_PK` zusätzlich das
  95-%-Perzentil.
- High gegen Low: Differenz der Mittelwerte je Kennwert, überlagerte Verläufe.
- Zyklusauswahl über `--cycles`.

---

## 10. Benutzeroberfläche

### 10.1 Grundsatz

Wer Concerto kennt, soll ohne Umlernen arbeiten können: WorkFlow-Leiste links, Kanäle rechts im
Channel Browser, Darstellung in Layouts aus Seiten mit Diagrammen, Kurven per Drag & Drop ins Diagramm,
Messcursor mit Werten in der Legende, Layout und Daten getrennt (ein Layout passt auf jede Messung).

**Vorlage ist die klassische Concerto-Oberfläche** (Bildschirmfoto liegt nur lokal vor, nicht im
Repository): Menüleiste
und Symbolleisten (kein Menüband), WorkFlow-Leiste, Layoutfenster mit Seitenreitern, Channel Browser,
Zyklusauswahl in der Statuszeile. Aufteilung, Benennung und Reihenfolge der Bedienelemente folgen
diesem Bild. Was das Bild nicht zeigt (Menüinhalte, Kontextmenüs, Dialoge, Tastenkürzel), wird nach
üblicher Bedienlogik umgesetzt und bei Vorliegen weiterer Screenshots angeglichen (Abschnitt 15, Punkt 9).

### 10.2 Hauptfenster

```
+-----------------------------------------------------------------------------+
| Open Curve Tool - <workspace> - overview.oclayout * - [Page 1 - Cycle: 1/5] |
| File  Edit  View  Components  Extras  Execute  Window  Help                 |
| New  Open  Save  Print  |  Page v  |  Design  |  Zoom Back  Show All        |
| Data Explorer | Object Wizard | Calculator | Formula Editor | Evaluation    |
+----------+---------------------------------------------+--------------------+
| WorkFlow | [Indicating Diagram 1 x]                    | Channel Browser    |
|          |  Test Parameter:  BORE, STROKE, CONROD ...  | FILE1 (meas.csv)   |
| Data     |  +----------------------------------------+ |--------------------|
| Access   |  |              :     Crank Angle  deg    | | CA    Crank Angle  |
|          |  |              :     PCYL_H       bar    | | CY    Cycle Based  |
| Data     |  |            .-:-.   PCYL_L       bar    | | TIME  Time Based   |
| Display  |  |          .'  :  '.                     | | STAT  Statistics   |
|          |  |  ______.'    :    '.________________   | | PAR   Parameters   |
| Calcu-   |  +----------------------------------------+ |--------------------|
| lations  |   -90 -60 -30   0   30   60   90   120      | <channel filter>   |
|          |              Crank Angle [deg]              | PCYL_H   bar       |
| Export   |                                             | PI_H     A         |
| Utilities|                                             | Q_H      J/deg     |
+----------+---------------------------------------------+--------------------+
| Ready                                      Zoom - ---o--- +    [ 1 ]  Cycle |
+-----------------------------------------------------------------------------+
```

- **Titelzeile:** Programmname, Arbeitsumgebung, Layoutdatei (`*` bei ungespeicherten Änderungen),
  aktive Seite und `Cycle: aktuell/gesamt`.
- **Menüleiste:** „File“, „Edit“, „View“, „Components“, „Extras“, „Execute“, „Window“, „Help“.
- **Symbolleiste 1:** Neu/Öffnen/Speichern/Drucken, Seitenauswahl „Page“, Umschalter „Design“
  (Bearbeitungsmodus), Zoom- und Cursorwerkzeuge.
- **Symbolleiste 2:** „Data Explorer“ (Messdateien laden und verwalten), „Object Wizard“ (Diagramm,
  Tabelle, Text einfügen), „Calculator“, „Formula Editor“, „Evaluation“ (Auswerteeinstellungen, an der
  Stelle von „GCA/Burn“). Ein Gegenstück zu „CalcGraf“ gibt es nicht (Abschnitt 1.3).
- **WorkFlow-Leiste (links):** große Schaltflächen „Data Access“, „Data Display“, „Calculations“,
  „Export“, „Utilities“; jede öffnet ein Ausklappmenü mit Symbol, Titel und einzeiliger Beschreibung
  (z. B. „Calculations“ → „Calculator“, „Formula Editor“). Unten Reiter „WorkFlow“ und „Layouts“
  (Liste der verfügbaren Layouts).
- **Layoutfenster (Mitte):** ein Reiter je geöffneter Seite; die Seite wird als Blatt mit Rand
  dargestellt, im Design-Modus mit Raster.
- **Channel Browser (rechts):** siehe 10.3. Am rechten Rand senkrechte Reiter „Object Library“ und
  „Layout Explorer“.
- **Statuszeile:** Meldung/Rechenstatus links, Zoomregler, Zyklusauswahl rechts.
- Alle Seitenbereiche sind andockbar, verschiebbar und ausblendbar; die Anordnung wird gespeichert.
- An der Stelle von AVL-Logo und Concerto-Schriftzug steht „Open Curve Tool“; ein Firmenlogo auf
  der Seite ist ein frei wählbares Bildobjekt.
- **Sprache: Englisch.** Alle Texte der Oberfläche – Menüs, Dialoge, Achs- und Legendenbeschriftung,
  Meldungen, Protokoll und Bericht – sind englisch; die Begriffe folgen der englischen
  Concerto-Oberfläche. Kurbelwinkel wird als `°CA` beschriftet. Eine Sprachumschaltung gibt es nicht.
  In dieser Spezifikation sind Bedienelemente mit ihrem englischen Namen in Anführungszeichen genannt.

### 10.3 Channel Browser und Data Explorer

Der Channel Browser ist wie in der Vorlage dreigeteilt:

1. **Dateiliste:** geladene Messungen als `FILE1 (dateiname.csv)`, `FILE2 (…)`, …
2. **Datensätze** der gewählten Datei, mit Anzahl der Punkte bzw. Zyklen in Klammern:

   | Datensatz | Inhalt | x-Bezug |
   |---|---|---|
   | `CA – Crank Angle` | `PCYL_H/L`, `PFILT_H/L` (gefiltert), `DP_H/L` (Druckanstieg), `V_H/L`, `Q_H/L`, `I_H/L`, `MFB_H/L` (Umsatzanteil 0…1), `PI_H/L`, `PKNK_H/L`, `KLS_H/L`, `KLSF_H/L` je Zyklus, dazu Mittel/Min/Max | Kurbelwinkel |
   | `CY – Cycle Based` | Alle Kennwerte aus Abschnitt 7 und 8, z. B. `PMAX_H`, `IMEP_L`, `PI_SOE_1_H` | Zyklusnummer |
   | `TIME – Time Based` | Rohsignale `DMR_H/L` [V], `PI_RM_H/L`, `CA_H/L`; dazu `P_H/L` (Druck in bar, nullpunktkorrigiert), `PI_H/L` (offsetkorrigiert), `PKNK_H/L`, `KLS_H/L`, `KLSF_H/L`, `ALPHA_H/L` (rekonstruierter Winkel), `SPEED_H/L` | Zeit |
   | `STAT – Statistics` | Mittelwert, Streuung, COV je Kennwert | – |
   | `PAR – Parameters` | `FILENAME`, `ENGINE`, `NRSTROKE`, `BORE`, `STROKE`, `CONROD`, `EPSILON`, `PINOFF`, `POLY_COEFF`, `FSAMPLE`, `DURATION`, `NSAMPLES`, `CARES` sowie je Zylinder `CAOFFS`, `PSENS`, `NCYCLES` | – |

3. **Kanalliste** des gewählten Datensatzes mit den Spalten Name, Einheit, Beschreibung; darüber das
   Filterfeld „Edit Channel Name Filter“ (Platzhalter `*`, z. B. `P*`).

Der Zylinder steht als Suffix `_H` / `_L` im Kanalnamen. Die Parameternamen entsprechen den
Concerto-Namen, die Zuordnung zu Abschnitt 4 ist eins zu eins (`BORE` = `bore_mm` usw.).

„Data Explorer“ (Symbolleiste 2 und WorkFlow „Data Access“) öffnet die Dateiverwaltung: Messdateien
laden, entfernen, Dateiinformationen ansehen (Abtastrate, Dauer, Zyklenzahl, erkannte Spalten).

- Nicht vorhandene Signale und davon abhängige Kanäle erscheinen nicht in der Kanalliste.
- Mehrere Messdateien gleichzeitig ladbar (Vergleich). Eine Datei ist „aktiv“; das Layout zeigt die
  aktive Datei, weitere lassen sich als Vergleichskurven überlagern.
- Öffnen über Menü, zuletzt verwendete Dateien oder Ablegen der CSV auf dem Fenster.
- Suchfeld zum Filtern der Kanäle; Kontextmenü: in neuem Diagramm zeigen, Eigenschaften, exportieren.
- Der Importdialog zeigt das erkannte Format (Trenn-/Dezimalzeichen, Einheitenzeile), eine Vorschau
  und die korrigierbare Spaltenzuordnung. Er erscheint nur, wenn die automatische Erkennung scheitert
  oder die Spalten nicht zugeordnet werden können, sowie auf Wunsch über „Open with Import Options“.
  Das Referenzformat (3.4) lädt ohne Rückfrage.

### 10.4 Layouts und Seiten

- Ein Layout ist eine Datei (`.oclayout`, JSON) mit beliebig vielen Seiten. Eine Seite enthält frei
  angeordnete Objekte: 2D-Diagramm, Tabelle, Textfeld, Parametertabelle (Name, Einheit, Wert aus dem
  Datensatz `PAR`, wie „Test Parameter“ in der Vorlage), Bild.
- „Layout Explorer“ zeigt Seiten und Objekte des Layouts als Baum; „Object Library“ hält
  vorkonfigurierte Diagramme und Tabellen, die per Doppelklick auf der Seite eingefügt werden.
- Layouts verweisen auf Kanäle über Namen, nicht auf eine Datei. Fehlt ein Kanal in der aktiven
  Messung, bleibt die Kurve leer und der Legendeneintrag wird ausgegraut – kein Fehler.
- Umschalter „Design“: im Design-Modus Objekte einfügen, verschieben, in der Größe ändern, kopieren,
  löschen; sonst Analysemodus (Zoom, Cursor), in dem Objekte nicht versehentlich verschoben werden.
- Mitgeliefert ist das Layout „Standard“ (`opencurve/layouts/Standard.oclayout`) mit den Seiten
  „Indicating Diagram“, „PI Current Diagram“, „Cycle Diagram“, „Heat Release Diagram“,
  „Knock Diagram“, „High Low Comparison“ und „Raw Data“. Eigene Layouts liegen im Benutzerordner und erscheinen im Reiter „Layouts“.
- Beim Start wird das zuletzt verwendete Layout geladen.

### 10.5 Diagramme

- Kurve hinzufügen: Kanal aus dem Channel Browser ins Diagramm ziehen. Ablegen auf einer vorhandenen Achse
  nutzt diese, Ablegen am Rand legt eine neue y-Achse an.
- x-Achse ergibt sich aus der Kanalgruppe (°KW, Zeit, Zyklusnummer) oder ist ein beliebiger Kanal
  (x-y-Darstellung, z. B. p über V).
- Mehrere y-Achsen nebeneinander sowie übereinander gestapelte Achsbereiche mit gemeinsamer x-Achse.
- Achsen linear/logarithmisch; Autoskalierung oder feste Grenzen (Doppelklick auf die Achse).
- Zoom: Rechteck aufziehen, Mausrad, Achse ziehen; „Zoom Back“ (Verlauf) und „Show All“.
- Legende als Tabelle im Diagramm wie in der Vorlage: Kopfzeile mit x-Kanal, Einheit und Cursorposition,
  darunter je Kurve Name, Einheit und Wert am Cursor in der Kurvenfarbe. Verschiebbar; weitere Spalten
  (Datei, Zyklus, zweiter Cursor, Differenz) zuschaltbar.
- Darstellung wie in der Vorlage: weißer Zeichenbereich, gepunktetes Gitter, Achstitel mit Einheit in
  eckigen Klammern, Cursor als rote senkrechte Linie.
- Kurveneigenschaften: Farbe, Linienart, Linienstärke, Marker, Sichtbarkeit, Achszuordnung.
- Feste Farbzuordnung High/Low über alle Diagramme.
- Markierungen aus der Auswertung einblendbar: AI05/AI50/AI90, PMAX, PI-Flanken, Auswertefenster,
  Klopf- und Referenzfenster, Klopfschwelle.
- Zeitbasierte Rohdaten werden für die Anzeige min/max-reduziert, sodass Spitzen sichtbar bleiben.

### 10.6 Messcursor

- Zwei Cursor (C1, C2) je Diagramm; Anzeige von x, y je Kurve sowie Δx und Δy.
- Verschieben mit Maus oder Pfeiltasten (schrittweise von Abtastpunkt zu Abtastpunkt).
- Cursor wahlweise über alle Diagramme einer Seite mit gleichem x-Bezug gekoppelt.
- Bei °KW-Diagrammen wird zusätzlich die zugehörige Zeit angezeigt, bei Zeitdiagrammen der Winkel.
- Cursorwerte in die Zwischenablage kopierbar.

### 10.7 Zyklusnavigation

- Zyklusauswahl rechts in der Statuszeile (Zahlenfeld mit Auf/Ab, Beschriftung „Cycle“) wie in der
  Vorlage; der aktive Zyklus steht zusätzlich in der Titelzeile. Ergänzend Schieberegler und Abspielen.
- Anzeigearten für °KW-Kurven: Einzelzyklus, Zyklusbereich überlagert, alle Zyklen, mittlerer Zyklus,
  Hüllkurve; global einstellbar und je Kurve überschreibbar.
- Klick auf einen Punkt im Zyklustrend oder eine Tabellenzeile springt zu diesem Zyklus.
- Ungültige Zyklen sind gekennzeichnet und lassen sich ein-/ausblenden; einzelne Zyklen können von
  Hand aus der Statistik ausgeschlossen werden.

### 10.8 Tabellen

Kennwerttabelle (Zyklen × Kennwerte) und Statistiktabelle; Spaltenauswahl, Sortierung, Kopieren in die
Zwischenablage, Export als CSV.

### 10.9 Einstellungen und Neuberechnung

Alle Werte aus Abschnitt 4 sind über Dialoge änderbar: „Engine Parameters“ (Geometrie, OT-Versatz je
Zylinder), „Channel Scaling“ (Druckempfindlichkeit in mV/bar und Offset je Zylinder, Faktor und Offset
für PI_RM; mit Vorschau des umgerechneten Wertebereichs), „Evaluation“ (Nullpunktkorrektur, Filter, Heizverlauf-Fenster, κ), „PI Detection“ (Schwelle,
Hysterese, Mindestdauer), „Knock“. Nach „Apply“ wird im Hintergrund neu gerechnet und alle
Diagramme aktualisieren sich. Einstellungen sind als TOML-Datei speicher- und ladbar.

### 10.10 Formelkanäle

Eigene Kanäle über Formeln aus vorhandenen Kanälen (z. B. `IMEP_H - IMEP_L`, `P_H * 1e5`), Eingabe als
Text mit den üblichen Rechen- und Statistikfunktionen. Zwei Zugänge wie in der Vorlage: „Calculator“
(Formel per Tasten und Kanalauswahl zusammenklicken) und „Formula Editor“ (Texteditor für mehrere
Formeln). Formelkanäle erscheinen im Channel Browser und werden im Layout gespeichert.

### 10.11 Export und Druck

- Seite als PNG, Seite oder ganzes Layout als PDF (A4 quer; „Print“ erzeugt ebenfalls ein PDF);
  Diagramm und Cursorwerte in die Zwischenablage.
- Daten der sichtbaren Kurven oder eines Kanals als CSV.
- Ergebnistabellen und Bericht (Abschnitt 11) der aktiven Messung in einen wählbaren Ordner.
- Arbeitsumgebung (geladene Dateien, Layout, Einstellungen) speicher- und wiederherstellbar.

### 10.12 Zweite Vorlage: nur der Seitenaufbau

Die zweite Vorlage (Bildschirmfoto eines eigenen früheren Programms, nicht im Repository)
wurde probeweise vollständig übernommen und dann auf Wunsch wieder zurückgenommen. Entschieden ist:

- **Erscheinungsbild wie in 10.2–10.7:** dunkelblaue WorkFlow-Leiste mit den Reitern „WorkFlow“ und
  „Layouts“, Symbolleiste 1 mit Text und Symbol, Channel Browser mit getrennter Datei- und
  Datensatzliste, Anzeigeart, Schieberegler und Zyklusfeld in der Statuszeile, gepunktetes Gitter,
  Legende ohne Zellrahmen, sichtbares Meldungsfenster.
- **Aus der zweiten Vorlage übernommen:** die Standardseiten (10.4), insbesondere „Indicating Diagram“
  als seitenfüllendes Druckdiagramm mit der umrahmten Tabelle „Test Parameter:“ oben links im
  Diagramm; die Tabelle ist nie kleiner als ihr Inhalt. Dazu der Kanal `MFB_H/L` und die
  Kurbelwinkelachse mit runder Teilung (alle 10° im Normalausschnitt).

`referenz/opencurvetool_overview.png` zeigt den umgesetzten Stand.

### 10.13 Technik und Reaktionszeiten

- Oberfläche mit Qt (`PySide6`) und `pyqtgraph`; `matplotlib` nur für PDF-/Berichtsausgabe.
- **Plattformen: Windows 10/11 und Linux** (X11 und Wayland) aus demselben Quellcode, ohne
  plattformspezifische Funktionsunterschiede. Pfade über `pathlib`, Einstellungen im jeweiligen
  Benutzerverzeichnis, Layout- und Einstellungsdateien zwischen beiden Systemen austauschbar,
  Darstellung unabhängig von der Bildschirmskalierung. Tests laufen auf beiden Systemen.
- Rechenkern ohne GUI-Abhängigkeit, damit Stapellauf und Tests ohne Oberfläche funktionieren.
- Einlesen und Rechnen laufen im Hintergrund mit Fortschrittsanzeige; die Oberfläche bleibt bedienbar.
- Zoomen/Verschieben bleibt bei 10 Mio. Rohdatenpunkten flüssig; Zykluswechsel wirkt ohne spürbare
  Verzögerung.

---

## 11. Ausgaben

Alle Dateien in `<outdir>/<Messdateiname>_…`.

| Datei | Inhalt |
|---|---|
| `_cycles.csv` | Eine Zeile je Zylinder und Zyklus: `cyl`, `cycle`, `valid`, `t_start`, alle Kennwerte |
| `_stats.csv` | Statistik je Zylinder und Kennwert |
| `_curves_H.csv`, `_curves_L.csv` | KW-Raster: mittlerer Zyklus + Hüllkurven für p, V, Q, I, PI-Strom |
| `_allcycles_H.npz`, `_allcycles_L.npz` | Alle Einzelzyklen auf KW-Raster (optional, `--save-cycles`) |
| `_report.html` | Bericht: Messinfo, Konfiguration, Warnungen, Statistiktabellen, alle Diagramme |
| `_log.txt` | Protokoll inkl. erkanntem Format, Abtastrate, verworfenen Zyklen |

Diagramme (je Zylinder, zusätzlich High/Low überlagert); sie bilden zugleich die Standardlayouts der
Oberfläche und den Inhalt des Berichts im Stapellauf:

1. Druck über °KW: Einzelzyklen dünn, Mittelwert fett; PI-Strom auf zweiter Achse.
2. p-V-Diagramm linear und doppelt-logarithmisch.
3. Heizverlauf und Summenheizverlauf mit Markierung AI05/AI50/AI90.
4. PI-Strom im Detail mit markierten Flanken (Zoom auf Ansteuerfenster).
5. Zyklustrend: IMEP, PMAX, AI50, PI_SOE, PI_DUR über Zyklusnummer.
6. Streudiagramm PMAX über APMAX, Histogramm IMEP.
7. Zeitbasierte Rohdatenansicht (Übersicht + Ausschnitt über die ersten Zyklen).
8. Klopfen: Druck mit überlagertem Bandpasssignal und Fenstern, `KP_PK` über Zyklusnummer mit
   Schwelle, Histogramm `KP_PK`, `KP_PK` über AI50.

---

## 12. Aufruf und Aufbau

```
python opencurve.py                         # Oberfläche starten
python opencurve.py messung.csv             # Oberfläche mit geladener Messung
python opencurve.py --batch messung.csv [--config motor.toml] [--out ergebnis/]
                                        [--layout standard.oclayout]
                                        [--cyl H,L] [--cycles 10:200]
                                        [--no-plots] [--save-cycles] [-v]
```

- Ein Startskript `opencurve.py`; der Code liegt in einem Paket:

  ```
  opencurve.py        Start (Oberfläche oder --batch)
  opencurve/core/     Einlesen, Zyklen, Thermodynamik, PI, Statistik, Export
  opencurve/gui/      Hauptfenster, Explorer, Diagramme, Dialoge
  opencurve/layouts/  Standardlayouts
  tests/              Tests und Testdatengenerator
  ```

- Python ≥ 3.11; Abhängigkeiten `numpy`, `scipy`, `pandas`, `matplotlib`, `PySide6`, `pyqtgraph`.
- Weitergabe vorerst ausschließlich als Python-Programm (Quellcode + `requirements.txt`) für Windows
  und Linux; ein fertiges Programm (EXE/AppImage) ist nicht Teil dieses Umfangs.
- Stapellauf: Exit-Code 0 = ok, 1 = Eingabefehler (Datei/Spalten/Format), 2 = keine auswertbaren
  Zyklen; am Ende Kurzfassung auf der Konsole (Zyklenzahl, Mittelwerte der Hauptkennwerte je Zylinder,
  Anzahl Warnungen).

---

## 13. Prüfung und Abnahme

### 13.1 Synthetische Testdaten

Generator `make_testdata.py` erzeugt CSV-Dateien mit bekannten Sollwerten: Schleppdruck polytrop +
Verbrennung nach Vibe-Funktion (IMEP und AI50 vorgegeben), PI-Strom mit Boost-/Haltephase und
definierten Flanken, Rauschen, wahlweise fehlende Spalten, gestufter CA, `,`/`.`-Dezimalformat.
Standardausgabe ist exakt das Referenzformat aus 3.4 (30 kHz, ganzzahliger CA in 1°-Stufen, gleiche
Spaltenreihenfolge und Zahlendarstellung); die Toleranzen unten gelten für dieses Format. Zusätzlich
Varianten mit 50, 100 und 150 kHz, mit Messoffset auf DMR und PI_RM sowie mit aufgeprägter
Klopfschwingung bekannter Amplitude in einem Teil der Zyklen.

| Kennwert | Toleranz gegen Sollwert |
|---|---|
| IMEP | ± 0,5 % |
| PMAX | ± 0,1 % |
| AI50 | ± 0,2 °KW |
| APMAX | ± 0,3 °KW |
| AI05, AI10, AI90 | ± 0,5 °KW |
| PI-Flankenzeiten | ± 0,2 Abtastintervall, mindestens ± 2 µs |
| PI_SOE (Winkel) | ± 0,1 °KW |
| KP_PK (aufgeprägte Schwingung im Band) | −25 % … +5 % der aufgeprägten Amplitude |
| KNOCK (klopfende / nicht klopfende Zyklen) | Alle richtig zugeordnet |

Diese Toleranzen sind die mit dem umgesetzten Programm bei 30, 50, 100 und 150 kHz erreichten Werte
(Tests in `tests/test_core.py`). Zwei Punkte weichen vom ersten Entwurf ab:

- **PI-Flanken:** Die Schwelle hängt vom abgetasteten Spitzenwert ab; an Knickstellen des Signals ist
  die lineare Interpolation zwischen zwei Abtastwerten nicht exakt. Bei hohen Abtastraten begrenzt das
  Signalrauschen (ca. 1–2 µs), nicht das Zeitraster.
- **KP_PK:** Der Bandpass dämpft eine schlagartig einsetzende, abklingende Schwingung nahe der unteren
  Bandgrenze um rund 15 %. Das ist eine Eigenschaft des Verfahrens; die Einstufung der Zyklen als
  klopfend ist davon nicht betroffen.

### 13.2 Vergleich mit Concerto

Sobald ein Concerto-Export (Zykluskennwerte + mittlerer Druckverlauf) zu einer Messdatei vorliegt:
gleiche Einstellungen (κ, Nullpunktkorrektur, Auswertefenster, Filter) übernehmen und Abweichungen der
Mittelwerte dokumentieren. Zielwerte: IMEP ± 1 %, AI50 ± 0,3 °KW, PMAX ± 0,2 %.

### 13.3 Pflichttests

Rechenkern: jede Zeile der Tabelle in 3.3; beide Dezimalformate; Datei mit unvollständigem
ersten/letzten Zyklus; nicht monotone Zeit (muss abbrechen); fehlende CA-Spalte bei vorhandenem Signal
(muss abbrechen); fehlende Geometrie (reduzierter Umfang, kein Abbruch).

Oberfläche: Datei laden und Standardlayout anzeigen; Kanal per Drag & Drop ins Diagramm; Cursorwerte
gegen Rohdaten; Zykluswechsel aktualisiert alle Diagramme; Layout speichern/laden ergibt dieselbe
Darstellung; Layout auf Messung mit fehlenden Signalen (leere Kurven, kein Fehler); Einstellungsänderung
löst Neuberechnung aus; Stapellauf und Oberfläche liefern identische Kennwerte.

---

## 14. Ausbaustufen

Alle sieben Stufen sind umgesetzt; offen ist nur der Feinabgleich der Oberfläche anhand weiterer
Screenshots (Abschnitt 15, Punkt 9).

| Stufe | Inhalt |
|---|---|
| 1 | Rechenkern: Einlesen, Formaterkennung, Zykluserkennung, Winkelrekonstruktion, KW-Raster, Kennwerte 7.1, Testdatengenerator |
| 2 | Oberfläche Grundgerüst nach Vorlage: Hauptfenster, Menüs, WorkFlow-Leiste, Channel Browser, Diagramm mit Drag & Drop, Zoom, Zyklusauswahl |
| 3 | Thermodynamik (Geometrie, Nullpunktkorrektur, Kennwerte 7.2), Klopfauswertung (7.4) und PI-Auswertung (Abschnitt 8) samt Einstellungsdialogen |
| 4 | Messcursor, mehrere Achsen/Achsbereiche, Tabellen, Statistik, High/Low-Vergleich |
| 5 | Layouts speichern/laden, Standardlayouts, Bearbeitungsmodus, mehrere Messdateien |
| 6 | Export/Druck, Bericht, Stapellauf, Arbeitsumgebung |
| 7 | Formelkanäle, Feinabgleich der Oberfläche mit Concerto |

---

## 15. Offene Punkte

| # | Frage | Annahme bis zur Klärung |
|---|---|---|
| 1 | Pleuellänge, Verdichtung und Desachsierung des konkreten Motors (Typ-6-Version); bisher Annahmen (6.1) | 425 mm, ε = 11,0, 0 mm |
| 2 | Was unterscheidet „High“ und „Low“ technisch? | Zwei Zylinder desselben Motors, gleiche Auswertung |
| 4 | Trägt das DMR-Signal einen Spannungsoffset? Die Ruhelage im Ausschnitt entspräche rechnerisch rund 100 bar | Ja, relativ messend → Nullpunktkorrektur je Zyklus |
| 7 | Klopfschwelle und Frequenzband für den Motor; hat die Messkette ein Anti-Aliasing-Filter? Passt κ = 1,32 für den Magergasbetrieb? | 1,0 bar, 2–10 kHz, κ = 1,32 |
| 8 | Welche Concerto-Kennwerte und -Layouts werden tatsächlich genutzt? Referenzexport vorhanden? | Umfang wie oben |
| 9 | Weitere Screenshots der Vorlage: aufgeklappte Menüs, Kontextmenüs, Dialoge (Achse, Kurve, Cursor), genutzte Layoutseiten | Nicht Sichtbares nach üblicher Bedienlogik umgesetzt |
| 13 | Was messen `kls_h` / `kls_l` genau (Klopfsensor?), in welcher Einheit, und welches Frequenzband ist dafür sinnvoll? | Körperschall-Klopfsensor in Volt, Band wie beim Druck (2–10 kHz) |
| 11 | Bedeutet der ganzzahlige Kurbelwinkel „Wert erreicht“ (abgeschnitten) oder „gerundet“? | Abgeschnitten (`step_mode = "floor"`); falsche Wahl = 0,5 °KW Versatz |
| 12 | Wofür steht „PI“, und wo im Arbeitsspiel liegt die Ansteuerung? Ereignisse werden dem Zyklus ihres Beginns zugeordnet; liegt die Ansteuerung um ± 360 °KW (Ladungswechsel-OT), kann sie auf zwei Zyklen fallen | Ansteuerung liegt nicht an der Zyklusgrenze |

Geklärt: CSV-Format, Abtastrate und Kurbelwinkelauflösung durch die Beispieldatei (3.4); DMR ist
Rohspannung mit parametrierbarer Empfindlichkeit (Voreinstellung 10 mV/bar), PI_RM ist in Ampere
(3.1); der Kurbelwinkel läuft von 0 bis 720, also 4-Takt, und der Dateiwert 0 ist der Zünd-OT beider
Zylinder (3.4); die Geometrie ist für High und Low gleich; der Ruhewert von PI_RM ist ein Messoffset
(8.1); Messdauer einige Sekunden bei 30 bis 150 kHz (3.2). Die Nummern entfallener Punkte bleiben frei.

Entschieden: Oberfläche englisch (10.2); Vorlage ist die klassische Concerto-Oberfläche laut
einem lokal vorliegenden Bildschirmfoto (10.1); Zielplattformen Windows und Linux (10.13); Weitergabe vorerst
nur als Python-Programm (12); Motordaten bis auf Weiteres nach Jenbacher Typ 6 (6.1);
Klopfauswertung gehört zum Umfang und ist standardmäßig aktiv (7.4);
Kurbelwinkelspalten sind immer vorhanden (3.1).

---

## 16. Umsetzungsstand (v1.2)

### 16.1 Aufbau

```
opencurve.py            Start: Oberfläche oder --batch
opencurve/core/         Rechenkern ohne GUI-Abhängigkeit
  io.py                 CSV-Import, Formaterkennung, Spaltenzuordnung
  angle.py              Winkelrekonstruktion, Zyklen, KW-Raster
  thermo.py             Geometrie, Nullpunktkorrektur, Druck- und Verbrennungskennwerte
  knock.py, pi.py       Klopf- und PI-Auswertung
  analysis.py           Gesamtablauf, Statistik
  datamodel.py          Datensätze CA/CY/TIME/STAT/PAR, Formelkanäle
  export.py             Ergebnistabellen, Bericht
  synth.py              Testdatengenerator mit Sollwerten
opencurve/gui/          Oberfläche (PySide6, pyqtgraph)
opencurve/layouts/      Standard.oclayout
tests/                  test_core.py, test_gui.py, make_testdata.py
referenz/               Bildschirmfotos des Programms
```

### 16.2 Prüfstand

- 54 automatische Tests (30 Rechenkern, 24 Oberfläche), alle bestanden; die Oberflächentests laufen
  ohne Bildschirm (`QT_QPA_PLATFORM=offscreen`).
- Geprüft wurde ausschließlich mit synthetischen Messungen im Referenzformat, weil keine echte
  Messdatei vorlag. Der Abgleich mit einer echten Messung und mit Concerto (13.2) steht aus.
- Laufzeit: 3 Mio. Zeilen (150 kHz, 20 s, 270 MB) werden in rund 2,5 s gelesen und ausgewertet;
  Zoom in den Rohdaten ca. 15 ms, Cursorbewegung ca. 7 ms, Zykluswechsel ca. 80 ms.
- Ausgeführt unter Linux mit Python 3.13. Unter Windows wurde das Programm noch nicht gestartet; der
  Code verwendet keine plattformspezifischen Funktionen.

### 16.3 Abweichungen von dieser Spezifikation

| Abschnitt | Abweichung |
|---|---|
| 10.2 | Kein „Undo“. „Print“ erzeugt ein PDF statt einen Druckdialog zu öffnen. |
| 10.4 | Objekte aus der „Object Library“ werden per Doppelklick eingefügt, nicht gezogen. |
| 10.5 | Logarithmische Skalierung nur für die erste y-Achse eines Achsbereichs und die x-Achse. Zusätzliche y-Achsen stehen rechts und skalieren automatisch oder nach festen Grenzen. |
| 10.6 | In x-y-Diagrammen (z. B. p über V) gibt es keinen Messcursor. |
| 10.7 | Der Ausschluss eines Zyklus aus der Statistik gilt für beide Zylinder gemeinsam. |
| 11 | Der Bericht verwendet je Diagramm nur eine y-Achse; Druck und PI-Strom stehen deshalb übereinander statt auf zwei Achsen. |
| 8 | PI-Ereignisse werden dem Zyklus ihres Beginns zugeordnet (Abschnitt 15, Punkt 12). |
