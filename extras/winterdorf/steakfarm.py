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

# Proportionen nach Foto: hoher Turm hinten links, dunkler Flachbau davor rechts (überlappt den Turm),
# schmaler Pylon an der rechten Ecke, zwei Fahnenmasten vor dem Turm.
TX0, TX1, TY0, TY1, TZ1 = 0, 16, 0, 10, 27     # Turm: 17 breit, 11 tief, 28 hoch
RX0, RX1, RY0, RY1, RZ1 = 10, 30, 7, 14, 11    # Restaurant: 21 breit, 8 tief, 12 hoch, 4 vor dem Turm
PX0, PX1, PY0, PY1, PZ1 = 32, 34, 12, 14, 15   # Pylon: 3 breit, 16 hoch
GLASS_X = range(5, 11)                          # senkrechter Glasstreifen im Turm


def letter(v: Voxels, ch: str, x0: int, y: int, ztop: int, kind: str = "snow") -> None:
    for r, row in enumerate(FONT3x5[ch]):
        for c, px in enumerate(row):
            if px == "X":
                v.set(x0 + c, y, ztop - r, kind)


def build() -> tuple[Voxels, set[tuple[int, int, int]], set[tuple[int, int, int]]]:
    v = Voxels()
    # --- Turm: heller Stein, Glasstreifen, verglastes Erdgeschoss, dunkles Band oben
    v.fill(TX0, TY0, 0, TX1, TY1, 0, "stone_bricks")
    v.fill(TX0, TY0, 1, TX1, TY1, TZ1 - 3, "stone_bricks")
    v.fill(TX0, TY0, TZ1 - 2, TX1, TY1, TZ1, "deepslate_tiles")
    for x in range(TX0, TX1 + 1):
        for z in range(1, 5):                        # Erdgeschoss rundum verglast
            v.set(x, TY1, z, "dark_oak_log" if x % 4 == 0 else "glass")
        v.set(x, TY1, 5, "dark_oak_planks")
    for x in GLASS_X:                                # Glasfassade über alle Etagen
        for z in range(6, TZ1 - 2):
            if x in (GLASS_X.start, GLASS_X.stop - 1):
                kind = "dark_oak_log"
            elif (z - 5) % 4 == 0:
                kind = "dark_oak_planks"
            else:
                kind = "glass"
            v.set(x, TY1, z, kind)
    # --- Restaurant: dunkler Kasten, Eingang links, großes Fenster rechts, Schriftzug oben
    v.fill(RX0, RY0, 0, RX1, RY1, RZ1, "deepslate_tiles")
    for z in range(1, 5):
        for x in range(13, 17):                      # Eingang (Glas + Tür)
            v.set(x, RY1, z, "dark_oak_log" if x in (13, 16) else ("door" if z < 3 and x == 14 else "glass_lit"))
        for x in range(21, 28):                      # großes Fenster
            v.set(x, RY1, z, "dark_oak_log" if x in (21, 27) else "glass_lit")
    x = RX0 + 1
    for ch in "STEAK":
        letter(v, ch, x, RY1, RZ1 - 1)
        x += 4
    for y in (9, 10, 11):                            # Seitenfenster rechts
        for z in range(1, 5):
            v.set(RX1, y, z, "glass_lit")
    # --- Pylon an der rechten Ecke, etwas höher als der Flachbau, Monogramm S/F
    v.fill(PX0, PY0, 0, PX1, PY1, 1, "stone_bricks")
    v.fill(PX0, PY0, 2, PX1, PY1, PZ1, "spruce_planks")
    letter(v, "S", PX0, PY1, PZ1 - 1)
    letter(v, "F", PX0, PY1, PZ1 - 7)
    building = set(v.b)
    # --- Zwei Fahnenmasten vor dem Turm (wie die senkrechten Banner auf dem Foto)
    decor: set[tuple[int, int, int]] = set()
    for bx in (0, 5):
        for z in range(0, 21):
            v.set(bx, 13, z, "dark_oak_log"); decor.add((bx, 13, z))
        for z in range(7, 20):
            for c in range(1, 4):
                v.set(bx + c, 13, z, "spruce_planks"); decor.add((bx + c, 13, z))
        for ch, top in (("S", 18), ("F", 12)):
            for r, row in enumerate(FONT3x5[ch]):
                for c, px in enumerate(row):
                    if px == "X":
                        v.set(bx + 1 + c, 13, top - r, "snow")
    # --- Gelände
    for gx in range(-4, 38):
        for gy in range(-3, 21):
            if (gx, gy, -1) not in v.b:
                v.set(gx, gy, -1, "grass")
    for gx in range(-4, 38):                          # freigeschaufelter Gehweg
        for gy in (17, 18, 19):
            v.set(gx, gy, -1, "path")
    for gx in range(14, 16):
        for gy in (15, 16):
            v.set(gx, gy, -1, "path")
    # Beete mit Bruchsteinrand, Büschen und Schneekugeln (die weißen Kugeln vom Foto)
    for bx0, bx1, by in ((0, 9, 14), (17, 30, 15)):
        for gx in range(bx0, bx1 + 1):
            v.set(gx, by + 1, 0, "cobblestone")
        for gx in range(bx0 + 1, bx1, 3):
            if (gx, by, 0) not in v.b:
                v.set(gx, by, 0, "leaves")
        for gx in range(bx0 + 2, bx1, 4):
            if (gx, by, 0) not in v.b:
                v.set(gx, by, 0, "snow", (0.15, 0.15, 0, 0.85, 0.85, 0.7))
    for lx in (12, 17):
        v.fill(lx, 16, 0, lx, 16, 1, "spruce_log")
        v.set(lx, 16, 2, "lantern", (0.3, 0.3, 0, 0.7, 0.7, 0.5))
    # kleine Fichte (statt der kahlen Bäume vom Foto)
    for z, rad in ((0, 0), (1, 1), (2, 1), (3, 1), (4, 0), (5, 0)):
        for dx in range(-rad, rad + 1):
            for dy in range(-rad, rad + 1):
                v.set(4 + dx, 16 + dy, z, "spruce_log" if (z == 0) else "leaves")
    v.snow_cover(skip={"path", "lantern"})
    return v, building, decor


def main() -> None:
    v, building, decor = build()
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
    counts.update(v.b[p][0] for p in decor)
    W = pic.width + max(elev_w, 520 * SS) + 120 * SS
    H = max(pic.height, elev_h + 760 * SS) + 170 * SS
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
           "Form nach Foto: Turm hinten, Flachbau davor rechts, Pylon, Fahnen · Winterdorf-Palette · Name frei erfunden",
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
        col = tuple(sum(t[r][c][i] for r in range(N) for c in range(N)) // (N * N) for i in range(3))
        d.rectangle([px, py, px + cs - 1, py + cs - 1], fill=col, outline=(0, 0, 0, 50))
    for i in range(0, fx1 - fx0 + 2, 5):
        d.text((ex + i * cs, gy0 + elev_h + 6 * SS), str(i), font=font(13 * SS), fill=(90, 100, 116))
    dims = [
        f"Turm: {TX1 - TX0 + 1} × {TY1 - TY0 + 1}, {TZ1 + 1} hoch, Glasstreifen 6 breit",
        f"Restaurant: {RX1 - RX0 + 1} × {RY1 - RY0 + 1}, {RZ1 + 1} hoch, 4 vor dem Turm,",
        f"   überlappt dessen rechte {TX1 - RX0 + 1} Blöcke",
        f"Pylon: {PX1 - PX0 + 1} breit, {PZ1 + 1} hoch · Gesamtbreite {PX1 + 1} Blöcke",
        "Fahnen (21 hoch) fehlen in der Frontansicht",
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
