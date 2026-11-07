"""Vorlage „Steak Farm“: Grundform nach einem echten Schnellrestaurant-Gebäude (Turm + Flachbau + Pylon),
umgesetzt in der Winterdorf-Blockpalette. Name und Schriftzug sind frei erfunden.

Aufruf: python3 steakfarm.py → steakfarm_vorlage.png
"""

from __future__ import annotations

import random
from collections import Counter

from PIL import Image, ImageDraw

from generate import OUT, N, Voxels, font, gradient, tex

FONT3x5 = {
    "S": ["XXX", "X..", "XXX", "..X", "XXX"], "T": ["XXX", ".X.", ".X.", ".X.", ".X."],
    "E": ["XXX", "X..", "XX.", "X..", "XXX"], "A": ["XXX", "X.X", "XXX", "X.X", "X.X"],
    "K": ["X.X", "X.X", "XX.", "X.X", "X.X"], "F": ["XXX", "X..", "XX.", "X..", "X.."],
    "R": ["XX.", "X.X", "XX.", "X.X", "X.X"], "M": ["X.X", "XXX", "X.X", "X.X", "X.X"],
}
NAMES = {
    "stone_bricks": "Steinziegel", "deepslate_tiles": "Tiefenschieferfliesen", "cobblestone": "Bruchstein",
    "dark_oak_log": "Dunkeleichenstamm", "dark_oak_planks": "Dunkeleichenbretter",
    "spruce_planks": "Fichtenholzbretter", "spruce_log": "Fichtenstamm", "snow": "Schneeblock",
    "glass": "Glas (Büro, kühl)", "glass_lit": "Glas (Restaurant, beleuchtet)", "door": "Dunkeleichentür",
    "snow_layer": "Schneeschicht", "lantern": "Laterne", "leaves": "Fichtenlaub",
}

TX0, TX1, TY0, TY1, TZ1 = 0, 12, 0, 10, 22     # Turm
RX0, RX1, RY0, RY1, RZ1 = 13, 33, 2, 10, 10    # Restaurant
PX0, PX1, PY, PZ1 = 36, 40, 9, 25              # Pylon


def build() -> tuple[Voxels, set[tuple[int, int, int]]]:
    v = Voxels()
    # --- Turm (heller Stein, Glasfassade vorn, dunkles Band oben)
    v.fill(TX0, TY0, 0, TX1, TY1, 0, "stone_bricks")
    v.fill(TX0, TY0, 1, TX1, TY1, TZ1 - 2, "stone_bricks")
    v.fill(TX0, TY0, TZ1 - 1, TX1, TY1, TZ1, "deepslate_tiles")
    for x in range(TX0, TX1 + 1):
        for z in range(1, TZ1 - 1):
            if x in (0, 4, 8, 12):
                kind = "dark_oak_log"
            elif z <= 4:
                kind = "glass"                       # Erdgeschoss komplett verglast
            elif (z - 1) % 4 == 3:
                kind = "dark_oak_planks"             # Brüstung zwischen den Etagen
            else:
                kind = "glass"
            v.set(x, TY1, z, kind)
    for y in (2, 3, 7, 8):                           # Seitenfenster
        for z in range(6, TZ1 - 2, 4):
            v.set(TX1, y, z, "glass"); v.set(TX1, y, z + 1, "glass")
    # --- Restaurant (dunkler Flachbau)
    v.fill(RX0, RY0, 0, RX1, RY1, 0, "stone_bricks")
    v.fill(RX0, RY0, 1, RX1, RY1, RZ1, "deepslate_tiles")
    for x0 in (15, 25):
        for x in range(x0, x0 + 5):
            for z in (1, 2, 3):
                v.set(x, RY1, z, "glass_lit" if x not in (x0, x0 + 4) else "dark_oak_log")
    for x in range(21, 24):
        for z in (1, 2, 3):
            v.set(x, RY1, z, "door" if (x == 22 and z < 3) else "glass_lit")
    for y in (4, 5, 7, 8):
        for z in (1, 2, 3):
            v.set(RX1, y, z, "glass_lit")
    # Schriftzug STEAK aus Schneeblöcken im Band
    x = 14
    for ch in "STEAK":
        for r, row in enumerate(FONT3x5[ch]):
            for c, px in enumerate(row):
                if px == "X":
                    v.set(x + c, RY1, 9 - r, "snow")
        x += 4
    # --- Pylon mit FARM (senkrecht)
    v.fill(PX0, PY, 0, PX1, PY, 1, "stone_bricks")
    v.fill(PX0, PY, 2, PX1, PY, PZ1, "dark_oak_planks")
    v.fill(PX0, PY, PZ1, PX1, PY, PZ1, "dark_oak_log")
    top = PZ1 - 2
    for ch in "FARM":
        for r, row in enumerate(FONT3x5[ch]):
            for c, px in enumerate(row):
                if px == "X":
                    v.set(PX0 + 1 + c, PY, top - r, "snow")
        top -= 6
    building = set(v.b)
    # --- Gelände
    for gx in range(-4, 45):
        for gy in range(-3, 20):
            if (gx, gy, -1) not in v.b:
                v.set(gx, gy, -1, "grass")
    for gx in range(-4, 45):                          # freigeschaufelter Gehweg
        for gy in (14, 15, 16):
            v.set(gx, gy, -1, "path")
    for gx in range(21, 24):
        for gy in range(11, 14):
            v.set(gx, gy, -1, "path")
    # Pflanzbeete mit Bruchsteinrand, Büschen und Schneekugeln
    for bx0, bx1 in ((1, 11), (14, 19), (25, 32)):
        for gx in range(bx0, bx1 + 1):
            for gy in (11, 12):
                edge = gx in (bx0, bx1) or gy == 12
                if edge:
                    v.set(gx, gy, 0, "cobblestone")
        for gx in range(bx0 + 1, bx1, 3):
            v.set(gx, 11, 0, "leaves")
        v.set(bx1 - 1, 11, 0, "snow")
    # Laternen und kleine Fichte am Eingang
    for lx in (20, 24):
        v.fill(lx, 12, 0, lx, 12, 1, "spruce_log")
        v.set(lx, 12, 2, "lantern", (0.3, 0.3, 0, 0.7, 0.7, 0.5))
    for z, rad in ((0, 1), (1, 1), (2, 0), (3, 0)):
        for dx in range(-rad, rad + 1):
            for dy in range(-rad, rad + 1):
                v.set(35 + dx, 13 + dy, z, "leaves")
    v.snow_cover(skip={"path", "lantern"})
    return v, building


def main() -> None:
    v, building = build()
    SS = 2
    pic = v.render(15 * SS, pad=30 * SS)
    # Frontansicht (vorderster Block je x/z)
    front: dict[tuple[int, int], str] = {}
    depth: dict[tuple[int, int], int] = {}
    for (x, y, z) in building:
        if depth.get((x, z), -99) < y:
            depth[(x, z)] = y
            front[(x, z)] = v.b[(x, y, z)][0]
    fx0, fx1 = min(k[0] for k in front), max(k[0] for k in front)
    fz1 = max(k[1] for k in front)
    cs = 11 * SS
    elev_w, elev_h = (fx1 - fx0 + 1) * cs, (fz1 + 1) * cs

    def shell(p: tuple[int, int, int]) -> bool:
        x, y, z = p
        return any(q not in building for q in ((x + 1, y, z), (x - 1, y, z), (x, y + 1, z),
                                               (x, y - 1, z), (x, y, z + 1), (x, y, z - 1)))
    counts = Counter(v.b[p][0] for p in building if shell(p))
    W = pic.width + elev_w + 120 * SS
    H = max(pic.height, elev_h + 620 * SS) + 170 * SS
    img = gradient(W, H, (178, 198, 224), (234, 240, 247))
    rng = random.Random(9)
    d = ImageDraw.Draw(img)
    for _ in range(320):
        fx, fy, fr = rng.randint(0, W), rng.randint(0, H), rng.choice((2, 3, 4)) * SS
        d.rectangle([fx, fy, fx + fr, fy + fr], fill=(255, 255, 255, 200))
    img.alpha_composite(pic, (0, 150 * SS + (H - 150 * SS - pic.height) // 2))
    d = ImageDraw.Draw(img)
    d.text((50 * SS, 36 * SS), "Vorlage „Steak Farm“ – winterlich", font=font(46 * SS, True), fill=(26, 34, 48))
    d.text((50 * SS, 98 * SS),
           "Grundform nach Foto (Turm + Flachbau + Pylon) · Winterdorf-Blockpalette · Name frei erfunden",
           font=font(22 * SS), fill=(55, 66, 84))

    # Frontansicht zeichnen
    ex, ey = pic.width + 40 * SS, 160 * SS
    panel = (ex - 20 * SS, ey - 20 * SS, W - 30 * SS, H - 40 * SS)
    d.rounded_rectangle(panel, radius=18 * SS, fill=(255, 255, 255, 235), outline=(200, 210, 224), width=2 * SS)
    d.text((ex, ey), "Frontansicht (Straßenseite)", font=font(26 * SS, True), fill=(26, 34, 48))
    d.text((ex, ey + 38 * SS), "1 Kästchen = 1 Block", font=font(17 * SS), fill=(80, 90, 106))
    gy0 = ey + 74 * SS
    for (x, z), kind in front.items():
        t = tex(kind, "side", 1)
        px, py = ex + (x - fx0) * cs, gy0 + (fz1 - z) * cs
        col = t[3][3] if kind not in ("snow",) else (250, 252, 255)
        d.rectangle([px, py, px + cs - 1, py + cs - 1], fill=col, outline=(0, 0, 0, 50))
    for i in range(0, fx1 - fx0 + 2, 5):
        d.text((ex + i * cs, gy0 + elev_h + 6 * SS), str(i), font=font(13 * SS), fill=(90, 100, 116))
    dims = [
        f"Turm: {TX1 - TX0 + 1} × {TY1 - TY0 + 1} Blöcke, {TZ1 + 1} hoch (5 Etagen à 4)",
        f"Restaurant: {RX1 - RX0 + 1} × {RY1 - RY0 + 1} Blöcke, {RZ1 + 1} hoch, Flachdach",
        f"Pylon: {PX1 - PX0 + 1} breit, {PZ1 + 1} hoch · Gesamtbreite {PX1 + 1} Blöcke",
        "Schrift 3 × 5 pro Buchstabe, aus Schneeblöcken",
    ]
    ly = gy0 + elev_h + 40 * SS
    d.text((ex, ly), "Maße", font=font(24 * SS, True), fill=(26, 34, 48)); ly += 36 * SS
    for line in dims:
        d.text((ex, ly), line, font=font(18 * SS), fill=(40, 48, 62)); ly += 28 * SS
    ly += 14 * SS
    d.text((ex, ly), "Material (Außenhülle, hohl)", font=font(24 * SS, True), fill=(26, 34, 48))
    ly += 40 * SS
    for kind, n in counts.most_common():
        t = tex(kind, "side", 1)
        sz = 3 * SS
        for r in range(N):
            for c in range(N):
                d.rectangle([ex + c * sz, ly + r * sz, ex + (c + 1) * sz - 1, ly + (r + 1) * sz - 1], fill=t[r][c])
        stacks = f"≈ {n / 64:.1f} Stacks" if n >= 64 else ""
        d.text((ex + 34 * SS, ly + 12 * SS), f"{NAMES.get(kind, kind)}", font=font(18 * SS), fill=(30, 38, 52), anchor="lm")
        d.text((W - 60 * SS, ly + 12 * SS), f"{n}  {stacks}", font=font(18 * SS, True), fill=(30, 38, 52), anchor="rm")
        ly += 34 * SS
    d.text((ex, ly + 10 * SS), "Ohne Zwischendecken und Innenausbau – plant etwas Puffer ein.",
           font=font(15 * SS), fill=(90, 100, 116))
    img.convert("RGB").resize((W // SS, H // SS), Image.LANCZOS).save(OUT / "steakfarm_vorlage.png", optimize=True)
    print("fertig:", W // SS, H // SS)


if __name__ == "__main__":
    main()
