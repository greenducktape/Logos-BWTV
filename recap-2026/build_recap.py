#!/usr/bin/env python3
"""
BWTV Liga 2026 — Recap Video Build
==================================

Baut das 7s-Recap-Video (1080x1920, h264) aus den Team-Logos dieses Repos.

Szene (eine durchgehende Szene, weisser Hintergrund):
  0.0-0.5s  BWTV-Liga-Logo (Kontrast-Variante Black) zentriert, bereits sichtbar
  0.5-1.0s  Team-Logos poppen simultan/minimal gestaffelt rein
            (Scatter-Layout, elastic-out Scale 0 -> 1.1 -> 1.0)
  1.0-1.5s  "WIR SEHEN UNS 2027" Pop-In (Scale + Fade)
  1.5-7.0s  Ausklang -- die Team-Logos schweben in mehreren
            Tiefenebenen weiter wie Wolken, BWTV steht deckend davor

Pipeline:  Layout (Python) -> recap.html -> Playwright-Frames -> ffmpeg (h264)

Aufruf:    python3 recap-2026/build_recap.py
"""

from __future__ import annotations

import base64
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass

# ---------------------------------------------------------------- Konstanten

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BUILD = os.path.join(HERE, "build")
FRAMES = os.path.join(BUILD, "frames")

W, H = 1080, 1920
FPS = 30
DURATION = 7.0
N_FRAMES = int(round(DURATION * FPS))

SEED = 20261  # fester Seed -> reproduzierbares Scatter-Layout

# Timeline (Sekunden)
T_LOGOS_START = 0.50   # erster Team-Logo-Pop
T_LOGOS_SPREAD = 0.22  # Stagger-Fenster (alle Starts liegen darin)
T_LOGOS_DUR = 0.46     # Dauer einer einzelnen Elastic-Pop-Animation
T_TEXT_START = 1.00
T_TEXT_DUR = 0.42

# ------------------------------------------------------------ Tiefenstaffelung
#
# Jedes Team-Logo bekommt eine Tiefe d zwischen 0 (weit hinten) und 1 (vorne).
# Deckkraft, Groesse, Unschaerfe und Drift-Amplitude haengen daran -- dadurch
# wirken die Logos wie Wolken in unterschiedlichen Entfernungen, waehrend das
# BWTV-Logo deckend davor steht.
DEPTH_OPACITY = (0.13, 0.78)   # hinten -> vorne
DEPTH_SCALE = (0.72, 1.16)
DEPTH_BLUR = (0.95, 0.0)       # px, hinten unschaerfer
DEPTH_DRIFT = (0.45, 1.0)      # Parallaxe: vorne bewegt sich mehr
DEPTH_NEAR = 0.30              # ab hier gilt ein Logo nicht mehr als "weit weg"

# Sanftes Schweben (Sekunden bzw. px/Grad, vor Tiefen-Skalierung)
DRIFT_PERIOD = (6.0, 13.0)
DRIFT_X = (6.0, 13.0)
DRIFT_Y = (5.0, 9.5)
DRIFT_ROT = (0.6, 2.2)

# BWTV-Liga-Logo (Kontrast-Variante fuer weissen Hintergrund)
BWTV_LOGO = "Logo BWTV Liga Black.svg"
BWTV_W = 600           # Renderbreite in px
BWTV_CX, BWTV_CY = 540, 780

# Text-Block
TEXT_CY = 1180

# Team-Logos: Maximalgroesse und Mindestabstand zum Bildrand.
# GAP ist so gewaehlt, dass auch zwei maximal gegeneinander driftende Nachbarn
# (je DRIFT_X[1] px) einander nicht beruehren.
MAX_LOGO_W, MAX_LOGO_H = 172, 126
EDGE = 26
GAP = 2 * DRIFT_X[1] + 8

# Team-Logos, die zusaetzlich zur team-logos.json aufgenommen werden sollen.
# (Auf der Platte liegt "TNB Malterdingen white.svg", das in der team-logos.json
#  fehlt. Bewusst leer gelassen -- die JSON ist laut Briefing die Quelle.)
EXTRA_LOGOS: list[str] = []

# Reservierte Zonen (x0, y0, x1, y1) fuer BWTV-Logo und Outro-Text.
# pad = 0 sind die reinen Bounding-Boxen: so nah duerfen die hintersten,
# fast durchsichtigen Logos heran. Die vorderen Ebenen halten mehr Abstand.
def reserved_zones(pad: float) -> list[tuple[float, float, float, float]]:
    bh = BWTV_W / bwtv_aspect()
    logo = (BWTV_CX - BWTV_W / 2 - pad, BWTV_CY - bh / 2 - pad,
            BWTV_CX + BWTV_W / 2 + pad, BWTV_CY + bh / 2 + pad)
    text = (170 - pad, TEXT_CY - 136 - pad, 910 + pad, TEXT_CY + 136 + pad)

    # Bleibt zwischen Logo und Text nur ein schmaler Korridor, wird er
    # mitreserviert. Sonst rutschen dort Logos hinein, fuer die er zu eng ist,
    # und die Relaxation bekommt sie nicht mehr heraus.
    if text[1] - logo[3] < MIN_CORRIDOR:
        return [(min(logo[0], text[0]), logo[1], max(logo[2], text[2]), text[3])]
    return [logo, text]


ZONE_PAD_NEAR = 46    # Abstand fuer die vorderen Ebenen
ZONE_PAD_FAR = 18     # Abstand fuer die hinterste Ebene
MIN_CORRIDOR = 120    # darunter lohnt sich der Spalt zwischen Logo und Text nicht


# ------------------------------------------------------------------ Helfer

def die(msg: str) -> None:
    print(f"FEHLER: {msg}", file=sys.stderr)
    sys.exit(1)


def norm_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def svg_aspect(path: str) -> float:
    """Seitenverhaeltnis (w/h) aus viewBox oder width/height des SVG."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        head = fh.read(4000)
    m = re.search(r'viewBox\s*=\s*"([\d.eE+\- ,]+)"', head)
    if m:
        parts = [float(v) for v in re.split(r"[ ,]+", m.group(1).strip()) if v]
        if len(parts) == 4 and parts[2] > 0 and parts[3] > 0:
            return parts[2] / parts[3]
    mw = re.search(r'\bwidth\s*=\s*"([\d.]+)', head)
    mh = re.search(r'\bheight\s*=\s*"([\d.]+)', head)
    if mw and mh and float(mh.group(1)) > 0:
        return float(mw.group(1)) / float(mh.group(1))
    return 1.0


_bwtv_aspect_cache: list[float] = []


def bwtv_aspect() -> float:
    if not _bwtv_aspect_cache:
        _bwtv_aspect_cache.append(svg_aspect(os.path.join(REPO, BWTV_LOGO)))
    return _bwtv_aspect_cache[0]


MIMES = {".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2"}


def data_uri(path: str, payload: bytes | None = None) -> str:
    mime = MIMES[os.path.splitext(path)[1].lower()]
    if payload is None:
        with open(path, "rb") as fh:
            payload = fh.read()
    return f"data:{mime};base64," + base64.b64encode(payload).decode("ascii")


# ----------------------------------------------------- Logos schwarz faerben

# Reihenfolge wichtig: 6 Stellen vor 3 Stellen, sonst wird #rrggbb nach drei
# Zeichen abgeschnitten. Der Lookahead verhindert Treffer in laengeren Werten.
COLOR_RE = re.compile(
    r'((?:fill|stroke|stop-color|flood-color|color)\s*[:=]\s*"?\s*)'
    r'(#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})(?![0-9a-fA-F]))'
)

# Ab dieser Luminanz-Spanne gilt ein Logo als bewusst zweifarbig; die helle
# Ebene bleibt dann als Aussparung stehen, statt mit der dunklen zu verschmelzen.
DUOTONE_SPLIT = 0.40


def _lum(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.299 * r + 0.587 * g + 0.114 * b


def blackify_svg(path: str) -> bytes:
    """
    Faerbt ein Team-Logo auf reines Schwarz/Weiss um.

    Ein simples brightness(0) wuerde zweifarbige Logos (z.B. SV Ludwigsburg 08
    gelb/schwarz oder SV Waiblingen blau/grau) zu unlesbaren Klecksen
    verschmelzen. Darum:
      - einfarbiges Logo            -> alles Schwarz
      - Logo mit grossem Luminanz-  -> dunkle Ebene Schwarz, helle Ebene Weiss
        Kontrast                       (Weiss = Aussparung auf weissem Grund)
      - Logo mit geringem Kontrast  -> alles Schwarz

    Weiss bleibt danach per Luminanz-Filter im HTML transparent, die
    Antialiasing-Kanten rendert der Rasterizer sauber auf der schwarzen Form.
    """
    with open(path, encoding="utf-8", errors="replace") as fh:
        svg = fh.read()

    colors = {m.group(2).lower() for m in COLOR_RE.finditer(svg)}
    if not colors:
        return svg.encode("utf-8")

    lums = {c: _lum(c) for c in colors}
    span = max(lums.values()) - min(lums.values())
    if len(colors) > 1 and span > DUOTONE_SPLIT:
        cut = (max(lums.values()) + min(lums.values())) / 2
        mapping = {c: ("#000000" if l < cut else "#ffffff") for c, l in lums.items()}
    else:
        mapping = {c: "#000000" for c in colors}

    return COLOR_RE.sub(
        lambda m: m.group(1) + mapping[m.group(2).lower()], svg
    ).encode("utf-8")


# ------------------------------------------------------------- Logo-Auflösung

@dataclass
class Logo:
    name: str
    file: str
    aspect: float


def load_logos() -> list[Logo]:
    with open(os.path.join(HERE, "team-logos.json"), encoding="utf-8") as fh:
        data = json.load(fh)

    on_disk = {f for f in os.listdir(REPO) if f.lower().endswith(".svg")}
    by_norm = {norm_name(f): f for f in on_disk}

    logos: list[Logo] = []
    missing: list[str] = []
    for team in data["teams"]:
        want = urllib.parse.unquote(team["file"])
        fname = want if want in on_disk else by_norm.get(norm_name(want))
        if not fname:
            missing.append(f'{team["name"]} -> {want}')
            continue
        logos.append(Logo(team["name"], fname,
                          svg_aspect(os.path.join(REPO, fname))))

    for fname in EXTRA_LOGOS:
        if fname not in on_disk:
            missing.append(f"EXTRA -> {fname}")
            continue
        logos.append(Logo(os.path.splitext(fname)[0], fname,
                          svg_aspect(os.path.join(REPO, fname))))

    if missing:
        die("Logo-Dateien nicht gefunden:\n  " + "\n  ".join(missing))
    return logos


# ------------------------------------------------------------ Scatter-Layout

def poisson_points(x0, y0, x1, y1, radius, zones, rng, tries=30):
    """Bridson Poisson-Disk-Sampling -- organisch verstreut, ohne Ueberlappung."""
    cell = radius / math.sqrt(2)
    gw = max(1, int(math.ceil((x1 - x0) / cell)))
    gh = max(1, int(math.ceil((y1 - y0) / cell)))
    grid: list[int | None] = [None] * (gw * gh)
    pts: list[tuple[float, float]] = []

    def blocked(px, py):
        return any(zx0 <= px <= zx1 and zy0 <= py <= zy1
                   for zx0, zy0, zx1, zy1 in zones)

    def fits(px, py):
        if not (x0 <= px <= x1 and y0 <= py <= y1) or blocked(px, py):
            return False
        gx, gy = int((px - x0) / cell), int((py - y0) / cell)
        for iy in range(max(0, gy - 2), min(gh, gy + 3)):
            for ix in range(max(0, gx - 2), min(gw, gx + 3)):
                idx = grid[iy * gw + ix]
                if idx is not None:
                    qx, qy = pts[idx]
                    if (qx - px) ** 2 + (qy - py) ** 2 < radius * radius:
                        return False
        return True

    def add(px, py):
        pts.append((px, py))
        grid[int((py - y0) / cell) * gw + int((px - x0) / cell)] = len(pts) - 1
        return len(pts) - 1

    # Startpunkt suchen (ausserhalb der reservierten Zonen)
    for _ in range(4000):
        px, py = rng.uniform(x0, x1), rng.uniform(y0, y1)
        if not blocked(px, py):
            active = [add(px, py)]
            break
    else:
        return []

    while active:
        i = active[rng.randrange(len(active))]
        ax, ay = pts[i]
        for _ in range(tries):
            ang = rng.uniform(0, 2 * math.pi)
            rad = rng.uniform(radius, 2 * radius)
            nx, ny = ax + math.cos(ang) * rad, ay + math.sin(ang) * rad
            if fits(nx, ny):
                active.append(add(nx, ny))
                break
        else:
            active.remove(i)
    return pts


def relax(boxes: list[list[float]], zones_for, iterations: int = 400,
          gap: float = GAP) -> None:
    """
    Schiebt die Logo-Boxen [cx, cy, w, h] auseinander, bis sie sich weder
    gegenseitig noch die reservierten Zonen ueberlappen und alle im Bild
    liegen. Verschieben statt Verkleinern -- so bleiben die Logos gross und
    fuellen die Flaeche gleichmaessig bis dicht an Logo und Text heran.
    """
    n = len(boxes)
    for _ in range(iterations):
        moved = 0.0

        # Nachbarn auseinanderdruecken (Achse der geringsten Ueberlappung)
        for i in range(n):
            cxi, cyi, wi, hi = boxes[i]
            for j in range(i + 1, n):
                cxj, cyj, wj, hj = boxes[j]
                ox = (wi + wj) / 2 + gap - abs(cxi - cxj)
                oy = (hi + hj) / 2 + gap - abs(cyi - cyj)
                if ox <= 0 or oy <= 0:
                    continue
                if ox < oy:
                    d = ox / 2 * (1 if cxi >= cxj else -1)
                    boxes[i][0] += d
                    boxes[j][0] -= d
                else:
                    d = oy / 2 * (1 if cyi >= cyj else -1)
                    boxes[i][1] += d
                    boxes[j][1] -= d
                moved += min(ox, oy)
                cxi, cyi = boxes[i][0], boxes[i][1]

        # Aus den reservierten Zonen herausschieben
        for b, zones in zip(boxes, zones_for):
            for zx0, zy0, zx1, zy1 in zones:
                x0, y0 = b[0] - b[2] / 2, b[1] - b[3] / 2
                x1, y1 = b[0] + b[2] / 2, b[1] + b[3] / 2
                if x0 >= zx1 or x1 <= zx0 or y0 >= zy1 or y1 <= zy0:
                    continue
                push = min((zx1 - x0, 0, 1), (x1 - zx0, 0, -1),
                           (zy1 - y0, 1, 1), (y1 - zy0, 1, -1))
                dist, axis, sign = push
                b[axis] += dist * sign
                moved += dist

        # Im Bild halten
        for b in boxes:
            b[0] = min(max(b[0], EDGE + b[2] / 2), W - EDGE - b[2] / 2)
            b[1] = min(max(b[1], EDGE + b[3] / 2), H - EDGE - b[3] / 2)

        if moved < 0.5:
            break


def violations(boxes: list[list[float]], zones_for, gap: float = GAP) -> list[int]:
    """Indizes aller Boxen, die noch ueberlappen oder aus dem Bild ragen."""
    bad: set[int] = set()
    n = len(boxes)
    for i in range(n):
        cx, cy, w, h = boxes[i]
        x0, y0, x1, y1 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
        if x0 < EDGE - 0.5 or y0 < EDGE - 0.5 or x1 > W - EDGE + 0.5 or y1 > H - EDGE + 0.5:
            bad.add(i)
        for zx0, zy0, zx1, zy1 in zones_for[i]:
            if x0 < zx1 and x1 > zx0 and y0 < zy1 and y1 > zy0:
                bad.add(i)
        for j in range(i + 1, n):
            cxj, cyj, wj, hj = boxes[j]
            if (abs(cx - cxj) < (w + wj) / 2 + gap * 0.5
                    and abs(cy - cyj) < (h + hj) / 2 + gap * 0.5):
                bad.add(i)
                bad.add(j)
    return sorted(bad)


def settle(boxes: list[list[float]], zones_for) -> None:
    """
    Relaxation bis alles sitzt. Boxen, die auch danach noch anecken, werden
    schrittweise verkleinert -- lieber ein etwas kleineres Logo als eines,
    das den Text oder das BWTV-Logo anschneidet.
    """
    for _ in range(40):
        relax(boxes, zones_for)
        bad = violations(boxes, zones_for)
        if not bad:
            return
        for i in bad:
            boxes[i][2] *= 0.94
            boxes[i][3] *= 0.94
    if violations(boxes, zones_for):
        die("Scatter-Layout konnte nicht ueberlappungsfrei aufgeloest werden.")


def build_layout(logos: list[Logo], rng: random.Random) -> list[dict]:
    """Scatter-Positionen + Groessen fuer alle Team-Logos."""
    n = len(logos)
    margin = 70
    box = (margin, margin, W - margin, H - margin)
    # Gesampelt wird gegen die engen Boxen, damit auch direkt neben Logo und
    # Text Punkte entstehen -- dort landen spaeter die hintersten Ebenen.
    sample_zones = reserved_zones(ZONE_PAD_FAR + 18)

    # Radius so waehlen, dass knapp mehr Punkte als Logos entstehen.
    best = None
    lo, hi = 90.0, 240.0
    for _ in range(26):
        r = (lo + hi) / 2
        pts = poisson_points(*box, r, sample_zones, random.Random(SEED))
        if len(pts) >= n:
            best = (r, pts)
            lo = r          # groesserer Radius = luftiger, solange es reicht
        else:
            hi = r
        if hi - lo < 1.0:
            break
    if not best:
        die("Scatter-Layout: zu wenig Platz fuer alle Team-Logos.")
    pts = best[1]

    # Ueberzaehlige Punkte entfernen: immer den, der seinem naechsten
    # Nachbarn am naechsten ist -> die Verteilung bleibt gleichmaessig.
    pts = list(pts)
    while len(pts) > n:
        worst_i, worst_d = 0, float("inf")
        for i, (px, py) in enumerate(pts):
            d = min(((qx - px) ** 2 + (qy - py) ** 2)
                    for j, (qx, qy) in enumerate(pts) if j != i)
            if d < worst_d:
                worst_i, worst_d = i, d
        pts.pop(worst_i)

    rng.shuffle(pts)

    # Tiefe zuweisen. Punkte, die nah an Logo oder Text liegen, muessen in die
    # hinterste Ebene -- dort sind sie so blass, dass sie nicht stoeren.
    near_zones = reserved_zones(ZONE_PAD_NEAR)
    tight = [any(zx0 < px < zx1 and zy0 < py < zy1
                 for zx0, zy0, zx1, zy1 in near_zones) for px, py in pts]

    far_slots = [i / max(1, n - 1) * DEPTH_NEAR for i in range(n)]
    rest_slots = [DEPTH_NEAR + (1 - DEPTH_NEAR) * i / max(1, n - 1)
                  for i in range(n)]
    rng.shuffle(far_slots)
    rng.shuffle(rest_slots)
    depths = [far_slots.pop() if t else rest_slots.pop() for t in tight]

    # Groesse: gleiche optische Flaeche pro Logo, skaliert mit der Tiefe,
    # begrenzt durch Bildrand und reservierte Zonen.
    target_area = 12_400.0
    boxes: list[list[float]] = []
    for logo, (px, py), d in zip(logos, pts, depths):
        a = max(0.55, min(3.2, logo.aspect))
        area = target_area * lerp(*DEPTH_SCALE, d) ** 2
        w = math.sqrt(area * a)
        h = math.sqrt(area / a)
        k = min(1.0, MAX_LOGO_W / w, MAX_LOGO_H / h)
        boxes.append([px, py, w * k, h * k])

    zones_for = [reserved_zones(ZONE_PAD_FAR if d < DEPTH_NEAR else ZONE_PAD_NEAR)
                 for d in depths]
    settle(boxes, zones_for)

    items: list[dict] = []
    for logo, (cx, cy, w, h), d in zip(logos, boxes, depths):
        amp = lerp(*DEPTH_DRIFT, d)
        items.append({
            "name": logo.name,
            "src": data_uri(os.path.join(REPO, logo.file),
                            blackify_svg(os.path.join(REPO, logo.file))),
            "x": round(cx, 2), "y": round(cy, 2),
            "w": round(w, 2), "h": round(h, 2),
            "rot": round(rng.uniform(-7.0, 7.0), 2),
            # Tiefenstaffelung
            "op": round(lerp(*DEPTH_OPACITY, d), 4),
            "blur": round(lerp(*DEPTH_BLUR, d), 2),
            "z": int(round(d * 100)),
            # Schweben: zwei entkoppelte Sinusse plus leichte Drehung
            "ax": round(rng.uniform(*DRIFT_X) * amp, 2),
            "ay": round(rng.uniform(*DRIFT_Y) * amp, 2),
            "ar": round(rng.uniform(*DRIFT_ROT) * amp, 3),
            "px": round(rng.uniform(*DRIFT_PERIOD), 3),
            "py": round(rng.uniform(*DRIFT_PERIOD), 3),
            "pr": round(rng.uniform(*DRIFT_PERIOD), 3),
            "fx": round(rng.uniform(0, 2 * math.pi), 4),
            "fy": round(rng.uniform(0, 2 * math.pi), 4),
            "fr": round(rng.uniform(0, 2 * math.pi), 4),
            # Minimaler Stagger: alle Starts innerhalb von T_LOGOS_SPREAD,
            # damit die Logos praktisch "auf einmal" reinknallen.
            "t0": round(T_LOGOS_START + rng.uniform(0.0, T_LOGOS_SPREAD), 4),
            "dur": T_LOGOS_DUR,
        })
    return items


# --------------------------------------------------------------------- HTML

HTML_TEMPLATE = """<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BWTV Liga 2026 — Recap</title>
<style>
  @font-face {{
    font-family: 'Source Sans 3'; font-style: normal; font-weight: 400 900;
    font-display: block; src: url({font_uri}) format('woff2');
  }}

  /* Die Buehne ist immer weiss -- das ist der Bildgrund des Videos und kein
     Theme-Token. Nur die Huelle drumherum folgt dem Theme des Betrachters. */
  :root {{
    --ground: #E4E9ED;
    --ground-edge: #D2DAE0;
    --ink: #1B2429;
    --ink-quiet: #5C6B75;
    --line: #C3CDD5;
    --accent: #00ADEB;
    --focus: #F18D5A;
    --shadow: 0 2px 6px rgba(17, 32, 41, .07), 0 18px 48px rgba(17, 32, 41, .16);
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      --ground: #0E1418;
      --ground-edge: #070B0E;
      --ink: #E6EDF2;
      --ink-quiet: #8B9BA6;
      --line: #26323A;
      --accent: #4EC5F5;
      --shadow: 0 2px 6px rgba(0, 0, 0, .5), 0 18px 56px rgba(0, 0, 0, .65);
    }}
  }}
  :root[data-theme="dark"] {{
    --ground: #0E1418;
    --ground-edge: #070B0E;
    --ink: #E6EDF2;
    --ink-quiet: #8B9BA6;
    --line: #26323A;
    --accent: #4EC5F5;
    --shadow: 0 2px 6px rgba(0, 0, 0, .5), 0 18px 56px rgba(0, 0, 0, .65);
  }}

  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  html, body {{ height: 100%; }}
  body {{
    background: var(--ground); color: var(--ink);
    font-family: 'Source Sans 3', system-ui, sans-serif;
    -webkit-font-smoothing: antialiased;
    display: flex; align-items: center; justify-content: center;
    overflow: hidden;
  }}

  #page {{
    display: flex; flex-direction: column; align-items: center;
    gap: 14px; padding: 16px;
  }}

  /* Der Rahmen bekommt die skalierte Groesse, die Buehne bleibt intern
     immer exakt 1080x1920 -- so ist die Live-Ansicht pixelgleich zum Video. */
  #frame {{
    width: calc({W}px * var(--scale));
    height: calc({H}px * var(--scale));
    box-shadow: var(--shadow);
    background: #FFFFFF;
  }}
  #stage {{
    position: relative; width: {W}px; height: {H}px; background: #FFFFFF;
    transform: scale(var(--scale)); transform-origin: 0 0;
    font-family: 'Source Sans 3', sans-serif;
  }}

  /* dark-logo Variante statt der grauen: Team-Logos rein schwarz.
     Die SVGs sind beim Build schon auf Schwarz/Weiss reduziert; der Filter
     macht daraus Deckkraft (schwarz = deckend, weiss = Aussparung) und faengt
     zugleich das eine Logo mit eingebettetem Pixelbild mit ab. */
  .logo-black {{ filter: url(#logo-black-filter); }}

  .team {{
    position: absolute; will-change: transform, opacity;
    transform-origin: 50% 50%; opacity: 0;
  }}
  .team img {{ width: 100%; height: 100%; object-fit: contain; display: block; }}

  #bwtv {{
    position: absolute; left: {bwtv_cx}px; top: {bwtv_cy}px;
    width: {bwtv_w}px; height: {bwtv_h}px;
    transform: translate(-50%, -50%); z-index: 120;
  }}
  #bwtv img {{ width: 100%; height: 100%; object-fit: contain; display: block; }}

  #outro {{
    position: absolute; left: 50%; top: {text_cy}px; z-index: 120;
    width: 100%; text-align: center; color: #000000;
    transform: translate(-50%, -50%) scale(0.72); opacity: 0;
    transform-origin: 50% 50%; will-change: transform, opacity;
  }}
  #outro .l1 {{
    font-size: 76px; font-weight: 800; letter-spacing: 6px;
    line-height: 1.05; text-transform: uppercase;
  }}
  #outro .l2 {{
    font-size: 138px; font-weight: 900; letter-spacing: 2px;
    line-height: 1.02; margin-top: 8px;
  }}

  #ui {{
    display: flex; align-items: baseline; gap: 16px;
    width: calc({W}px * var(--scale)); min-width: 260px;
  }}
  #ui .label {{
    flex: 1; font-size: 12px; font-weight: 600; letter-spacing: .09em;
    text-transform: uppercase; color: var(--ink-quiet);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }}
  #replay {{
    font: inherit; font-size: 13px; font-weight: 700; letter-spacing: .04em;
    color: var(--ink); background: transparent;
    border: 1px solid var(--line); border-radius: 999px;
    padding: 6px 15px; cursor: pointer;
    transition: border-color .15s, color .15s;
  }}
  #replay:hover {{ border-color: var(--accent); color: var(--accent); }}
  #replay:focus-visible {{ outline: 2px solid var(--focus); outline-offset: 3px; }}

  /* Aufnahmemodus: die Buehne rendert unskaliert und allein im Bild. */
  body.capture {{ background: #FFFFFF; overflow: visible; }}
  body.capture #page {{ padding: 0; gap: 0; }}
  body.capture #frame {{ box-shadow: none; }}
  body.capture #ui {{ display: none; }}
</style></head>
<body>
<svg width="0" height="0" aria-hidden="true" style="position:absolute"><defs>
  <filter id="logo-black-filter" color-interpolation-filters="sRGB"
          x="-4%" y="-4%" width="108%" height="108%">
    <!-- RGB auf Schwarz, Alpha = Alpha - Luminanz -->
    <feColorMatrix type="matrix" values="0 0 0 0 0
                                         0 0 0 0 0
                                         0 0 0 0 0
                                         -0.299 -0.587 -0.114 1 0"/>
    <feComponentTransfer><feFuncA type="table" tableValues="0 0.55 0.88 1 1"/></feComponentTransfer>
  </filter>
</defs></svg>

<div id="page">
  <div id="frame">
    <div id="stage">
      <div id="teams"></div>
      <div id="bwtv"><img src="{bwtv_uri}" alt="BWTV Triathlonliga"></div>
      <div id="outro"><div class="l1">Wir sehen uns</div><div class="l2">2027</div></div>
    </div>
  </div>
  <div id="ui">
    <span class="label">BWTV Triathlonliga · Recap 2026</span>
    <button id="replay" type="button">Nochmal abspielen</button>
  </div>
</div>

<script>
/* Alles gekapselt: nach aussen sichtbar sind nur __setT, __capture und
   __ready. Sonst kollidieren Namen wie "frame" mit den globalen Aliassen,
   die der Browser fuer Elemente mit id anlegt. */
(function () {{
'use strict';

const ITEMS = {items_json};
const CFG = {cfg_json};

const stage = document.getElementById('stage');
const teamsEl = document.getElementById('teams');
const outro = document.getElementById('outro');

const nodes = ITEMS.map(it => {{
  const d = document.createElement('div');
  d.className = 'team';
  d.style.left   = (it.x - it.w / 2) + 'px';
  d.style.top    = (it.y - it.h / 2) + 'px';
  d.style.width  = it.w + 'px';
  d.style.height = it.h + 'px';
  d.style.zIndex = String(it.z);
  const img = document.createElement('img');
  img.className = 'logo-black';
  /* Tiefenunschaerfe: hintere Ebenen leicht weich, vordere knackscharf */
  if (it.blur > 0.01) {{
    img.style.filter = 'url(#logo-black-filter) blur(' + it.blur + 'px)';
  }}
  img.src = it.src;
  img.alt = it.name;
  d.appendChild(img);
  teamsEl.appendChild(d);
  return d;
}});

const clamp01 = v => v < 0 ? 0 : v > 1 ? 1 : v;

/* elastic-out: 0 -> 1.1 (Peak bei ~34%) -> 1.0 */
function elasticOut(x) {{
  if (x <= 0) return 0;
  if (x >= 1) return 1;
  return 1 - Math.pow(2, -10 * x) * Math.cos(x * 9.4);
}}

/* back-out mit ~18% Overshoot -> Text-Pop 0.72 -> 1.05 -> 1.0 */
function backOut(x) {{
  if (x <= 0) return 0;
  if (x >= 1) return 1;
  const c1 = 2.4, c3 = c1 + 1, t = x - 1;
  return 1 + c3 * t * t * t + c1 * t * t;
}}

const TAU = Math.PI * 2;

/* Die einzige Quelle der Wahrheit fuer die Szene: setzt den Zustand fuer
   einen beliebigen Zeitpunkt. Die Live-Wiedergabe ruft sie pro Bildschirm-
   frame auf, der Video-Export pro Videoframe -- beides ergibt dasselbe Bild. */
window.__setT = function (t) {{
  for (let i = 0; i < ITEMS.length; i++) {{
    const it = ITEMS[i];
    const p = clamp01((t - it.t0) / it.dur);
    const s = elasticOut(p);

    /* Schweben: zwei entkoppelte Sinusse mit unterschiedlichen Perioden,
       dazu eine langsame Drehung. Laeuft durchgehend, auch waehrend des
       Pops -- die Logos landen also in eine bereits treibende Wolke. */
    const dx = it.ax * Math.sin(TAU * t / it.px + it.fx);
    const dy = it.ay * Math.sin(TAU * t / it.py + it.fy);
    const dr = it.ar * Math.sin(TAU * t / it.pr + it.fr);

    nodes[i].style.opacity = String(p <= 0 ? 0 : it.op * clamp01(p / 0.12));
    nodes[i].style.transform =
      'translate(' + dx.toFixed(3) + 'px,' + dy.toFixed(3) + 'px) ' +
      'rotate(' + (it.rot + dr).toFixed(4) + 'deg) scale(' + s.toFixed(5) + ')';
  }}
  const tp = clamp01((t - CFG.textStart) / CFG.textDur);
  const ts = 0.72 + 0.28 * backOut(tp);
  outro.style.opacity = String(tp <= 0 ? 0 : clamp01(tp / 0.35));
  outro.style.transform = 'translate(-50%, -50%) scale(' + ts.toFixed(5) + ')';
}};

/* Buehne auf den Viewport einpassen. Intern bleibt sie 1080x1920, skaliert
   wird nur die Darstellung -- Layout und Animation sind aufloesungsunabhaengig. */
function fit() {{
  if (document.body.classList.contains('capture')) return;
  const pad = 32, ui = 56;
  const s = Math.min((window.innerWidth - pad) / {W},
                     (window.innerHeight - pad - ui) / {H});
  document.documentElement.style.setProperty('--scale', Math.max(s, 0.05).toFixed(5));
}}
window.addEventListener('resize', fit);
fit();

/* Wiedergabe in Echtzeit. Der Ausklang laeuft endlos weiter -- die Logos
   schweben also auch nach dem Ende der 7 Sekunden. */
let raf = null, t0 = 0;

function tick(now) {{
  window.__setT((now - t0) / 1000);
  raf = requestAnimationFrame(tick);
}}

function play() {{
  if (raf !== null) cancelAnimationFrame(raf);
  t0 = performance.now();
  raf = requestAnimationFrame(tick);
}}

/* Bei reduzierter Bewegung nichts animieren: direkt das Endbild zeigen. */
const calm = window.matchMedia('(prefers-reduced-motion: reduce)');

function start() {{
  if (calm.matches) {{
    if (raf !== null) {{ cancelAnimationFrame(raf); raf = null; }}
    window.__setT(CFG.duration);
  }} else {{
    play();
  }}
}}

document.getElementById('replay').addEventListener('click', start);
calm.addEventListener('change', start);

/* Frame-genauer Export: Animation anhalten, unskaliert rendern, Chrome weg.
   Der Renderer setzt danach jeden Zeitpunkt selbst ueber __setT(). */
window.__capture = function () {{
  if (raf !== null) {{ cancelAnimationFrame(raf); raf = null; }}
  document.body.classList.add('capture');
  document.documentElement.style.setProperty('--scale', '1');
}};

window.__ready = (async () => {{
  const imgs = Array.from(document.images);
  await Promise.all(imgs.map(im => im.complete && im.naturalWidth
    ? null
    : new Promise(res => {{ im.onload = res; im.onerror = res; }})));
  await document.fonts.ready;
  window.__setT(0);
  fit();
  start();
  return true;
}})();
}})();
</script></body></html>
"""


def write_html(items: list[dict]) -> str:
    bwtv_h = BWTV_W / bwtv_aspect()
    html = HTML_TEMPLATE.format(
        W=W, H=H,
        font_uri=data_uri(os.path.join(HERE, "assets", "SourceSans3-latin-var.woff2")),
        bwtv_uri=data_uri(os.path.join(REPO, BWTV_LOGO)),
        bwtv_cx=BWTV_CX, bwtv_cy=BWTV_CY,
        bwtv_w=round(BWTV_W, 2), bwtv_h=round(bwtv_h, 2),
        text_cy=TEXT_CY,
        items_json=json.dumps(items, ensure_ascii=False),
        cfg_json=json.dumps({
            "textStart": T_TEXT_START, "textDur": T_TEXT_DUR,
            "duration": DURATION,
        }),
    )
    out = os.path.join(HERE, "bwtv_recap_2026.html")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(html)

    # Variante fuer Hosts, die das Dokumentgeruest selbst mitbringen
    # (z.B. Artifacts): nur Stylesheet und Body-Inhalt, ohne <html>/<head>.
    with open(os.path.join(BUILD, "artifact.html"), "w", encoding="utf-8") as fh:
        fh.write(to_fragment(html))
    return out


def to_fragment(html: str) -> str:
    """Stylesheet + Body-Inhalt aus dem fertigen Dokument herausloesen."""
    css = html[html.index("<style>"):html.index("</style></head>") + len("</style>")]
    body = html[html.index("<body>") + len("<body>"):html.index("</body></html>")]
    return f"<title>BWTV Liga 2026 — Recap</title>\n{css}\n{body}"


# ---------------------------------------------------------------- Rendering

def render_frames(html_path: str) -> None:
    if os.path.isdir(FRAMES):
        shutil.rmtree(FRAMES)
    os.makedirs(FRAMES)
    env = dict(os.environ, RECAP_HTML=html_path, RECAP_FRAMES=FRAMES,
               RECAP_W=str(W), RECAP_H=str(H), RECAP_FPS=str(FPS),
               RECAP_N=str(N_FRAMES))
    node_path = subprocess.run(["npm", "root", "-g"], capture_output=True,
                               text=True, check=True).stdout.strip()
    env["NODE_PATH"] = node_path
    subprocess.run(["node", os.path.join(HERE, "render_frames.js")],
                   env=env, check=True)
    got = len([f for f in os.listdir(FRAMES) if f.endswith(".png")])
    if got != N_FRAMES:
        die(f"Nur {got} von {N_FRAMES} Frames gerendert.")


def encode(out_path: str) -> None:
    ffmpeg = shutil.which("ffmpeg") or die("ffmpeg nicht gefunden.")
    subprocess.run([
        ffmpeg, "-y", "-loglevel", "error",
        "-framerate", str(FPS), "-i", os.path.join(FRAMES, "f%04d.png"),
        "-c:v", "libx264", "-profile:v", "high", "-level", "4.0",
        "-preset", "slow", "-crf", "17",
        "-pix_fmt", "yuv420p", "-color_primaries", "bt709",
        "-color_trc", "bt709", "-colorspace", "bt709",
        "-movflags", "+faststart", "-r", str(FPS),
        out_path,
    ], check=True)


# --------------------------------------------------------------------- Main

def main() -> None:
    os.makedirs(BUILD, exist_ok=True)
    rng = random.Random(SEED)

    logos = load_logos()
    print(f"Team-Logos: {len(logos)}")

    items = build_layout(logos, rng)
    with open(os.path.join(BUILD, "layout.json"), "w", encoding="utf-8") as fh:
        json.dump([{k: v for k, v in it.items() if k != "src"} for it in items],
                  fh, ensure_ascii=False, indent=2)

    html_path = write_html(items)
    print(f"HTML: {html_path} ({os.path.getsize(html_path) / 1e6:.1f} MB)")

    if "--html-only" in sys.argv:
        return

    render_frames(html_path)
    print(f"Frames: {N_FRAMES} @ {W}x{H}, {FPS} fps")

    out = os.path.join(HERE, "bwtv_recap_2026.mp4")
    encode(out)
    print(f"Video: {out} ({os.path.getsize(out) / 1e6:.1f} MB, {DURATION:.1f}s)")


if __name__ == "__main__":
    main()
