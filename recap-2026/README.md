# BWTV Liga 2026 — Recap-Video

Gesamt-Recap für die BWTV Triathlonliga (kein Video pro Einzelliga).

**Output:** `bwtv_recap_2026.mp4` — 1080x1920, 5,0 s, 30 fps, H.264 High / yuv420p

## Szene

Eine durchgehende Szene auf weißem Hintergrund:

| Zeit | Was passiert |
|---|---|
| 0,0–0,5 s | BWTV-Liga-Logo (Kontrast-Variante `Logo BWTV Liga Black.svg`) zentriert, bereits sichtbar |
| 0,5–1,0 s | Team-Logos poppen quasi gleichzeitig rein — Scatter-Layout, elastic-out Scale 0 → 1,1 → 1,0 |
| 1,0–1,5 s | „WIR SEHEN UNS 2027" Pop-In (Scale + Fade, back-out 0,72 → 1,05 → 1,0) |
| 1,5–5,0 s | Standbild / Hold bis Videoende |

## Bauen

```bash
python3 recap-2026/build_recap.py
```

Voraussetzungen: `python3`, `node` mit global installiertem `playwright`
(inkl. Chromium) und `ffmpeg` mit `libx264`.

Ablauf: Layout in Python → `build/recap.html` → 150 Einzelframes über
Playwright → `ffmpeg`. Die Szenenzeit wird pro Frame explizit über
`window.__setT(t)` gesetzt statt CSS-Animationen in Echtzeit laufen zu lassen —
dadurch ist jeder Frame exakt reproduzierbar.

## Assets

* **BWTV-Logo:** `Logo BWTV Liga Black.svg` aus dem Repo-Root (Kontrast-Variante
  für weißen Hintergrund). Der Sponsor-Teil „powered by RACEPEDIA" bleibt
  bewusst farbig.
* **Team-Logos:** alle 53 Vereine aus `team-logos.json` (Kopie der Datei aus dem
  `bwtv-liga-carousel`-Skill). Die SVG-Dateien kommen aus dem Repo-Root.
* **Schrift:** `assets/Inter-latin-var.woff2`, als Data-URI ins HTML eingebettet.

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

## Layout

Die Positionen entstehen per Poisson-Disk-Sampling (organisch verstreut, kein
Raster), gefolgt von einer Relaxation, die die Boxen auseinanderschiebt, bis
sich nichts mehr überlappt und nichts in die reservierten Zonen für
BWTV-Logo und Outro-Text ragt. `SEED` in `build_recap.py` steuert das Layout —
gleicher Seed, gleiches Bild. Für eine andere Streuung einfach den Seed ändern.

## Bekannte Abweichung vom Briefing

Der Stagger ist kleiner als die im Briefing genannten 0,02–0,05 s: bei 53 Logos
wären das 1,1–2,6 s Einlaufzeit statt der gewünschten 0,5 s. Stattdessen liegen
alle Startzeitpunkte zufällig in einem Fenster von 0,22 s
(`T_LOGOS_SPREAD`) — das gewünschte „auf einmal"-Gefühl bleibt, der Pop passt
aber ins vorgesehene Zeitfenster.
