# BWTV Liga 2026 — Recap-Video

Gesamt-Recap für die BWTV Triathlonliga (kein Video pro Einzelliga).

**Output:**

* `bwtv_recap_2026.html` — die Animation als eigenständige Seite. Läuft live im
  Browser, passt sich der Fenstergröße an, hat einen Replay-Button und schwebt
  nach den 7 Sekunden endlos weiter. Alles eingebettet, keine externen Dateien.
* `bwtv_recap_2026.mp4` — dieselbe Szene als Video, 1080x1920, 7,0 s, 30 fps,
  H.264 High / yuv420p. Wird aus genau dieser HTML gerendert.

## Szene

Eine durchgehende Szene auf weißem Hintergrund:

| Zeit | Was passiert |
|---|---|
| 0,0–0,5 s | BWTV-Liga-Logo (Kontrast-Variante `Logo BWTV Liga Black.svg`) zentriert, bereits sichtbar |
| 0,5–1,0 s | Team-Logos poppen quasi gleichzeitig rein — Scatter-Layout, elastic-out Scale 0 → 1,1 → 1,0 |
| 1,0–1,5 s | „WIR SEHEN UNS 2027" Pop-In (Scale + Fade, back-out 0,72 → 1,05 → 1,0) |
| 1,5–7,0 s | Ausklang — die Team-Logos schweben in mehreren Tiefenebenen weiter wie Wolken |

Das Schweben läuft durchgehend, auch während des Pops: die Logos landen also
in ein bereits treibendes Feld. BWTV-Logo und Outro-Text stehen dagegen
bewegungslos und deckend davor.

## Bauen

```bash
python3 recap-2026/build_recap.py              # HTML + Video
python3 recap-2026/build_recap.py --html-only  # nur HTML, in Sekunden
```

Voraussetzungen: `python3`, `node` mit global installiertem `playwright`
(inkl. Chromium) und `ffmpeg` mit `libx264`.

Ablauf: Layout in Python → `bwtv_recap_2026.html` → 210 Einzelframes über
Playwright → `ffmpeg`.

HTML und Video sind dieselbe Datei: `window.__setT(t)` setzt den kompletten
Szenenzustand für einen beliebigen Zeitpunkt und ist die einzige Quelle der
Wahrheit. Die Live-Wiedergabe ruft sie pro Bildschirmframe auf, der Export
pro Videoframe — damit kann das Video nicht von der HTML abweichen, und der
Export bleibt unabhängig von der Rendergeschwindigkeit exakt reproduzierbar.
Vor dem Export ruft der Renderer `window.__capture()` auf: Wiedergabe anhalten,
Skalierung auf 1, Bedienleiste ausblenden. Aufgenommen wird dann nur `#stage`.

`build/artifact.html` ist dieselbe Seite ohne `<html>`/`<head>` — für Hosts,
die das Dokumentgerüst selbst mitbringen.

## Assets

* **BWTV-Logo:** `Logo BWTV Liga Black.svg` aus dem Repo-Root (Kontrast-Variante
  für weißen Hintergrund). Der Sponsor-Teil „powered by RACEPEDIA" bleibt
  bewusst farbig.
* **Team-Logos:** alle 53 Vereine aus `team-logos.json` (Kopie der Datei aus dem
  `bwtv-liga-carousel`-Skill). Die SVG-Dateien kommen aus dem Repo-Root.
* **Schrift:** `assets/SourceSans3-latin-var.woff2` (Source Sans 3 alias
  Source Sans Pro, Google Fonts), als Data-URI ins HTML eingebettet.
  `assets/Inter-latin-var.woff2` liegt noch als Alternative daneben.

### Team-Logos in Schwarz (dark-logo Variante statt der grauen)

Ein pauschales `brightness(0)` würde zweifarbige Logos zu unlesbaren Klecksen
verschmelzen (z. B. SV Ludwigsburg 08 gelb/schwarz, SV Waiblingen blau/grau).
Deshalb werden die SVGs beim Build auf reines Schwarz/Weiß umgefärbt:

* einfarbiges Logo → alles Schwarz
* Logo mit großem Luminanz-Kontrast (> 0,40) → dunkle Ebene Schwarz, helle
  Ebene Weiß (= Aussparung auf weißem Grund)
* Logo mit geringem Kontrast → alles Schwarz

Anschließend macht der SVG-Filter `#logo-black-filter` im HTML aus der Luminanz
die Deckkraft (Schwarz = deckend, Weiß = transparent). Das fängt auch
`TSV Calw.svg` mit ab, das ein eingebettetes Pixelbild enthält und sich nicht
per Textersetzung umfärben lässt.

## Tiefenstaffelung und Schweben

Jedes Team-Logo bekommt eine Tiefe `d` zwischen 0 (weit hinten) und 1 (vorne).
Daran hängen vier Größen gleichzeitig — genau die Kombination erzeugt den
Eindruck unterschiedlicher Entfernungen:

| | hinten (d = 0) | vorne (d = 1) |
|---|---|---|
| Deckkraft | 0,13 | 0,78 |
| Größe | ×0,72 | ×1,16 |
| Unschärfe | 0,95 px | scharf |
| Drift-Amplitude | ×0,45 | ×1,0 (Parallaxe) |

Geschwebt wird über zwei entkoppelte Sinusse in x und y (Perioden 6–13 s, um
6–13 px) plus eine langsame Drehung — dadurch wiederholt sich die Bewegung
optisch nicht. `GAP` im Layout ist so gewählt, dass sich auch zwei maximal
gegeneinander driftende Nachbarn nicht berühren.

Logos, die dicht an BWTV-Logo oder Text liegen, landen zwangsweise in der
hintersten Ebene — dort sind sie so blass, dass sie nichts stören.

## Layout

Die Positionen entstehen per Poisson-Disk-Sampling (organisch verstreut, kein
Raster), gefolgt von einer Relaxation, die die Boxen auseinanderschiebt, bis
sich nichts mehr überlappt und nichts in die reservierten Zonen für
BWTV-Logo und Outro-Text ragt. Bleibt zwischen Logo und Text nur ein schmaler
Korridor (< `MIN_CORRIDOR`), wird er mitreserviert — sonst rutschen dort Logos
hinein, für die er zu eng ist. `SEED` in `build_recap.py` steuert das Layout —
gleicher Seed, gleiches Bild. Für eine andere Streuung einfach den Seed ändern.

## Bekannte Abweichung vom Briefing

Der Stagger ist kleiner als die im Briefing genannten 0,02–0,05 s: bei 53 Logos
wären das 1,1–2,6 s Einlaufzeit statt der gewünschten 0,5 s. Stattdessen liegen
alle Startzeitpunkte zufällig in einem Fenster von 0,22 s
(`T_LOGOS_SPREAD`) — das gewünschte „auf einmal"-Gefühl bleibt, der Pop passt
aber ins vorgesehene Zeitfenster.
