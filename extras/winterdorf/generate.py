"""Erzeugt drei eigene Voxel-Bilder für das Winterdorf-Projekt (Minecraft-Server, Start 1.12.2026).

Alle Texturen sind prozedural und selbst gezeichnet – keine fremden Bilder.
Aufruf: python3 generate.py  → blockpalette.png, beispielhaus.png, dorf_grundriss.png
"""

from __future__ import annotations

import math
import random
import zlib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
N = 8  # Texturauflösung pro Blockseite


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_B if bold else FONT, size)


# ---------------------------------------------------------------- Texturen
Color = tuple[int, int, int]


def clamp(c: float) -> int:
    return max(0, min(255, int(c)))


def jit(rng: random.Random, base: Color, amt: int) -> Color:
    d = rng.randint(-amt, amt)
    return (clamp(base[0] + d), clamp(base[1] + d), clamp(base[2] + d))


def tex(kind: str, face: str, seed: int) -> list[list[Color]]:
    """Gibt ein N×N-Farbraster für Blocktyp und Seite ('top' | 'side') zurück."""
    rng = random.Random(zlib.crc32(f"{kind}/{face}/{seed % 7}".encode()))
    g: list[list[Color]] = [[(0, 0, 0)] * N for _ in range(N)]
    for r in range(N):
        for c in range(N):
            g[r][c] = _px(kind, face, r, c, rng)
    return g


def _px(kind: str, face: str, r: int, c: int, rng: random.Random) -> Color:
    if kind in ("snow", "snow_layer"):
        col = jit(rng, (238, 243, 250), 5)
        return (col[0] - 12, col[1] - 6, col[2]) if rng.random() < 0.08 else col
    if kind == "spruce_planks":
        if r in (3, 7):
            return jit(rng, (78, 56, 33), 4)
        if (r < 3 and c == 5) or (3 < r < 7 and c == 1):
            return jit(rng, (88, 64, 38), 4)
        return jit(rng, (116, 86, 52), 7)
    if kind == "dark_oak_planks":
        if r in (3, 7):
            return jit(rng, (40, 26, 12), 3)
        if (r < 3 and c == 2) or (3 < r < 7 and c == 6):
            return jit(rng, (48, 31, 15), 3)
        return jit(rng, (68, 45, 22), 5)
    if kind in ("spruce_log", "dark_oak_log"):
        bark, inner, ring = (
            ((60, 40, 22), (118, 88, 54), (92, 66, 40))
            if kind == "spruce_log"
            else ((58, 44, 28), (82, 56, 30), (62, 42, 22))
        )
        if face == "top":
            d = max(abs(r - 3.5), abs(c - 3.5))
            if d > 3:
                return jit(rng, bark, 5)
            return jit(rng, ring if int(d) % 2 == 1 else inner, 4)
        return jit(rng, (bark[0] - 14, bark[1] - 10, bark[2] - 6) if c % 3 == 0 else bark, 6)
    if kind == "cobblestone":
        crack = [
            "..#...#.", "###..#..", "..#.###.", ".#....#.",
            "##..#.##", "..###...", ".#...#..", "#....###",
        ]
        if crack[r][c] == "#":
            return jit(rng, (78, 78, 80), 6)
        return jit(rng, (128, 128, 130), 16)
    if kind == "stone_bricks":
        if r in (3, 7) or (r < 3 and c == 3) or (3 < r < 7 and c == 7):
            return jit(rng, (88, 88, 90), 4)
        return jit(rng, (124, 124, 126), 8)
    if kind == "deepslate_tiles":
        if r % 4 == 3 or c % 4 == 3:
            return jit(rng, (32, 32, 37), 3)
        return jit(rng, (66, 66, 72), 6)
    if kind == "glass_lit":
        if r in (0, 7) or c in (0, 7):
            return jit(rng, (96, 70, 42), 4)
        if r == c or r == c + 1:
            return (255, 236, 180)
        return jit(rng, (250, 190, 100), 10)
    if kind == "glass":
        if r in (0, 7) or c in (0, 7):
            return jit(rng, (60, 44, 26), 3)
        if r + c in (5, 6):
            return (214, 232, 242)
        return jit(rng, (128, 166, 190), 6)
    if kind == "door":
        if r in (0, 7) or c in (0, 7):
            return jit(rng, (40, 26, 12), 3)
        if c == 6 and r == 4:
            return (170, 170, 160)
        return jit(rng, (62, 40, 20), 5)
    if kind == "grass":
        return jit(rng, (96, 66, 38) if face == "side" else (110, 130, 70), 8)
    if kind == "path":
        return jit(rng, (120, 112, 104), 18)
    if kind == "leaves":
        return jit(rng, (36, 66, 48) if rng.random() > 0.15 else (24, 46, 34), 8)
    if kind == "lantern":
        if r in (0, 7) or c in (0, 7):
            return (50, 50, 56)
        return jit(rng, (255, 200, 90), 15)
    if kind == "smoke":
        return jit(rng, (200, 200, 205), 8)
    return (255, 0, 255)


FULL = {
    "snow", "spruce_planks", "dark_oak_planks", "spruce_log", "dark_oak_log", "cobblestone",
    "stone_bricks", "deepslate_tiles", "glass_lit", "glass", "door", "grass", "path", "leaves",
}


# ---------------------------------------------------------------- Iso-Renderer
class Voxels:
    def __init__(self) -> None:
        # Schlüssel: (x, y, z) Zelle; Wert: (Typ, Box relativ zur Zelle)
        self.b: dict[tuple[int, int, int], tuple[str, tuple[float, ...]]] = {}

    def set(self, x: int, y: int, z: int, kind: str,
            box: tuple[float, ...] = (0, 0, 0, 1, 1, 1)) -> None:
        self.b[(x, y, z)] = (kind, box)

    def fill(self, x0: int, y0: int, z0: int, x1: int, y1: int, z1: int, kind: str) -> None:
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                for z in range(z0, z1 + 1):
                    self.set(x, y, z, kind)

    def solid(self, p: tuple[int, int, int]) -> bool:
        v = self.b.get(p)
        return v is not None and v[0] in FULL and v[1] == (0, 0, 0, 1, 1, 1)

    def snow_cover(self, skip: set[str] = frozenset()) -> None:  # type: ignore[assignment]
        tops: dict[tuple[int, int], int] = {}
        for (x, y, z) in self.b:
            if tops.get((x, y), -999) < z:
                tops[(x, y)] = z
        for (x, y), z in tops.items():
            kind, box = self.b[(x, y, z)]
            if kind in skip or kind not in FULL or box[5] < 1:
                continue
            self.set(x, y, z + 1, "snow_layer", (0, 0, 0, 1, 1, 0.125))

    def render(self, s: int, shade: tuple[float, float, float] = (1.0, 0.82, 0.64),
               pad: int = 20, bg: Image.Image | None = None,
               outline: bool = True) -> Image.Image:
        keys = list(self.b)
        def proj(x: float, y: float, z: float) -> tuple[float, float]:
            return ((x - y) * s, (x + y) * s / 2 - z * s)
        xs, ys = [], []
        for (x, y, z) in keys:
            for cx, cy, cz in ((x, y, z + 1), (x + 1, y + 1, z), (x + 1, y, z), (x, y + 1, z)):
                px, py = proj(cx, cy, cz)
                xs.append(px)
                ys.append(py)
        ox, oy = -min(xs) + pad, -min(ys) + pad
        w, h = int(max(xs) - min(xs) + 2 * pad), int(max(ys) - min(ys) + 2 * pad)
        img = bg.resize((w, h)) if bg is not None else Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)

        def P(x: float, y: float, z: float) -> tuple[float, float]:
            px, py = proj(x, y, z)
            return (px + ox, py + oy)

        order = sorted(keys, key=lambda k: (k[0] + k[1] + k[2], k[2]))
        for (x, y, z) in order:
            kind, (bx0, by0, bz0, bx1, by1, bz1) = self.b[(x, y, z)]
            X0, Y0, Z0, X1, Y1, Z1 = x + bx0, y + by0, z + bz0, x + bx1, y + by1, z + bz1
            seed = x * 73856093 ^ y * 19349663 ^ z * 83492791
            full = (bx0, by0, bz0, bx1, by1, bz1) == (0, 0, 0, 1, 1, 1) and kind in FULL
            faces = []
            if not (full and self.solid((x, y, z + 1))):
                faces.append(("top", (X0, Y0, Z1), (X1 - X0, 0, 0), (0, Y1 - Y0, 0), shade[0], 1.0))
            if not (full and self.solid((x, y + 1, z))):
                faces.append(("side", (X0, Y1, Z1), (X1 - X0, 0, 0), (0, 0, -(Z1 - Z0)), shade[1], Z1 - Z0))
            if not (full and self.solid((x + 1, y, z))):
                faces.append(("side", (X1, Y1, Z1), (0, -(Y1 - Y0), 0), (0, 0, -(Z1 - Z0)), shade[2], Z1 - Z0))
            for face, o, u, v, f, hh in faces:
                t = tex(kind, face, seed)
                rows = max(1, round(N * hh)) if face == "side" else N
                for r in range(rows):
                    for c in range(N):
                        a0, a1 = c / N, (c + 1) / N
                        b0, b1 = r / rows, (r + 1) / rows
                        pts = []
                        for aa, bb in ((a0, b0), (a1, b0), (a1, b1), (a0, b1)):
                            pts.append(P(o[0] + u[0] * aa + v[0] * bb,
                                         o[1] + u[1] * aa + v[1] * bb,
                                         o[2] + u[2] * aa + v[2] * bb))
                        col = t[r][c]
                        if kind in ("glass_lit", "lantern"):
                            ff = max(f, 0.92)
                        else:
                            ff = f
                        d.polygon(pts, fill=(clamp(col[0] * ff), clamp(col[1] * ff), clamp(col[2] * ff), 255))
                if outline:
                    corners = [P(*o), P(o[0] + u[0], o[1] + u[1], o[2] + u[2]),
                               P(o[0] + u[0] + v[0], o[1] + u[1] + v[1], o[2] + u[2] + v[2]),
                               P(o[0] + v[0], o[1] + v[1], o[2] + v[2])]
                    d.line(corners + [corners[0]], fill=(0, 0, 0, 60), width=max(1, s // 14))
        return img


def gradient(w: int, h: int, top: Color, bottom: Color) -> Image.Image:
    img = Image.new("RGBA", (w, h))
    d = ImageDraw.Draw(img)
    for yy in range(h):
        t = yy / max(1, h - 1)
        d.line([(0, yy), (w, yy)], fill=tuple(clamp(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,))
    return img


# ---------------------------------------------------------------- 1) Blockpalette
def blockpalette() -> None:
    W, H, SS = 1800, 1150, 2
    img = gradient(W * SS, H * SS, (226, 236, 246), (246, 249, 252))
    d = ImageDraw.Draw(img)
    d.text((70 * SS, 45 * SS), "Winterdorf – Blockpalette", font=font(54 * SS, True), fill=(30, 38, 52))
    d.text((70 * SS, 115 * SS), "Mammutbaum-Biom · am Boden gebaut · Schnee per Hand",
           font=font(26 * SS), fill=(80, 92, 110))

    cards = [
        ("Fichte", [("spruce_planks", "Fichtenholzbretter"), ("spruce_log", "Fichtenstamm")],
         "Wände, Giebel, Palisade"),
        ("Dunkeleiche", [("dark_oak_log", "Dunkeleichenstamm"), ("dark_oak_planks", "Dunkeleichenbretter")],
         "Fachwerk, Balken, Türen"),
        ("Bruchstein", [("cobblestone", "Bruchstein")], "Steinsockel, Kamin, Wege"),
        ("Steinziegel", [("stone_bricks", "Steinziegel")], "Sockelkante, Tor, Brunnen"),
        ("Tiefenschiefer", [("deepslate_tiles", "Tiefenschieferfliesen")], "Steile Dächer"),
        ("Schnee", [("snow", "Schneeblock"), ("snow_layer", "Schneeschicht")],
         "Boden & Dächer (per Hand)"),
    ]
    cw, ch = 520, 450
    x0s = [70, 640, 1210]
    y0s = [180, 660]
    for i, (title, blocks, use) in enumerate(cards):
        cx, cy = x0s[i % 3] * SS, y0s[i // 3] * SS
        d.rounded_rectangle([cx, cy, cx + cw * SS, cy + ch * SS], radius=22 * SS,
                            fill=(255, 255, 255, 255), outline=(205, 214, 226), width=2 * SS)
        # Würfel rendern
        v = Voxels()
        main = blocks[0][0]
        if main == "snow_layer":
            v.set(0, 0, 0, "snow_layer", (0, 0, 0, 1, 1, 0.125))
        else:
            v.set(0, 0, 0, main)
        big = v.render(84 * SS, pad=4)
        img.alpha_composite(big, (cx + 40 * SS, cy + 40 * SS))
        if len(blocks) > 1:
            v2 = Voxels()
            if blocks[1][0] == "snow_layer":
                v2.set(0, 0, 0, "snow_layer", (0, 0, 0, 1, 1, 0.125))
            else:
                v2.set(0, 0, 0, blocks[1][0])
            sm = v2.render(52 * SS, pad=4)
            img.alpha_composite(sm, (cx + 245 * SS, cy + 40 * SS + big.height - sm.height))
        ty = cy + 300 * SS
        d.text((cx + 34 * SS, ty), title, font=font(38 * SS, True), fill=(30, 38, 52))
        names = " · ".join(n for _, n in blocks)
        d.text((cx + 34 * SS, ty + 52 * SS), names, font=font(20 * SS), fill=(70, 80, 96))
        d.text((cx + 34 * SS, ty + 86 * SS), "Einsatz: " + use, font=font(21 * SS, True), fill=(46, 104, 160))
        d.rectangle([cx + 34 * SS, ty + 46 * SS, cx + 120 * SS, ty + 48 * SS], fill=(46, 104, 160))
    img.convert("RGB").resize((W, H), Image.LANCZOS).save(OUT / "blockpalette.png", optimize=True)


# ---------------------------------------------------------------- 2) Beispielhaus
def beispielhaus() -> None:
    v = Voxels()
    L, B = 10, 6  # Grundfläche x 0..10, y 0..6
    # Boden (Podsol/Gras) mit Schnee
    for x in range(-6, 17):
        for y in range(-6, 14):
            v.set(x, y, -1, "grass")
    # Freigeschaufelter Weg zur Tür
    for y in range(7, 14):
        v.set(5, y, -1, "path")
        v.set(4, y, -1, "path")
    # Steinsockel
    v.fill(0, 0, 0, L, B, 1, "cobblestone")
    v.fill(0, 0, 2, L, B, 2, "stone_bricks")
    # Holzwände + Fachwerk
    v.fill(0, 0, 3, L, B, 6, "spruce_planks")
    for (x, y) in ((0, 0), (L, 0), (0, B), (L, B), (5, B), (5, 0), (0, 3), (L, 3)):
        v.fill(x, y, 3, x, y, 6, "dark_oak_log")
    for x in range(0, L + 1):
        v.set(x, 0, 7, "dark_oak_log"); v.set(x, B, 7, "dark_oak_log")
    for y in range(0, B + 1):
        v.set(0, y, 7, "dark_oak_log"); v.set(L, y, 7, "dark_oak_log")
    v.fill(1, 1, 7, L - 1, B - 1, 7, "spruce_planks")
    # Fenster (beleuchtet) und Tür
    for x in (2, 3, 7, 8):
        v.set(x, B, 4, "glass_lit"); v.set(x, B, 5, "glass_lit")
    for y in (1, 2, 4, 5):
        v.set(L, y, 4, "glass_lit"); v.set(L, y, 5, "glass_lit")
    v.set(5, B, 3, "door"); v.set(5, B, 4, "door"); v.set(5, B, 5, "dark_oak_planks")
    # Eingangstreppe
    v.fill(4, 7, 0, 6, 7, 2, "stone_bricks")
    v.fill(4, 8, 0, 6, 8, 1, "stone_bricks")
    v.fill(4, 9, 0, 6, 9, 0, "stone_bricks")
    # Steiles Satteldach (2 hoch je 1 nach innen), First entlang x
    for k in range(0, 5):
        ya, yb = -1 + k, 7 - k
        za = 8 + 2 * k
        for x in range(-1, L + 2):
            for zz in (za, za + 1):
                v.set(x, ya, zz, "deepslate_tiles")
                v.set(x, yb, zz, "deepslate_tiles")
        if yb - ya > 1:
            for x in range(0, L + 1):
                for y in range(ya + 1, yb):
                    for zz in (za, za + 1):
                        v.set(x, y, zz, "spruce_planks")
    v.set(L, 3, 10, "glass_lit"); v.set(L, 3, 11, "glass_lit")
    # Firstbalken
    for x in range(-1, L + 2):
        v.set(x, 3, 18, "dark_oak_log")
    # Kamin
    v.fill(8, 1, 12, 8, 1, 19, "cobblestone")
    # Laternenpfosten
    for (lx, ly) in ((2, 10), (8, 10)):
        v.fill(lx, ly, 0, lx, ly, 1, "spruce_log")
        v.set(lx, ly, 2, "lantern", (0.3, 0.3, 0, 0.7, 0.7, 0.5))
    # Fichten (eine große hinten, eine kleine vorn)
    def spruce(tx: int, ty: int, height: int, mega: bool) -> None:
        tw = 2 if mega else 1
        for zz in range(0, height - 3):
            for dx in range(tw):
                for dy in range(tw):
                    v.set(tx + dx, ty + dy, zz, "spruce_log")
        cx, cy = tx + (tw - 1) / 2, ty + (tw - 1) / 2
        start = 3 if mega else 1
        for zz in range(start, height + 1):
            frac = (height - zz) / (height - start)
            rad = (0.9 + frac * (4.6 if mega else 2.2)) * (0.7 if (zz % 3 == 0) else 1.0)
            r = int(rad) + 1
            for x in range(int(cx) - r, int(cx) + r + 2):
                for y in range(int(cy) - r, int(cy) + r + 2):
                    if math.hypot(x - cx, y - cy) <= rad and (x, y, zz) not in v.b:
                        v.set(x, y, zz, "leaves")
        v.set(int(cx), int(cy), height + 1, "leaves")
    spruce(-5, -5, 24, True)
    spruce(14, -3, 13, False)
    spruce(-5, 9, 8, False)
    v.snow_cover(skip={"path", "lantern", "cobblestone"} )
    # Kaminkrone bleibt schneefrei (Rauch), aber Sockel/Treppen sollen Schnee tragen:
    # Treppen/Sockel bekommen manuell Schnee, Kamin nicht.
    for (x, y, z), (k, box) in list(v.b.items()):
        if k == "cobblestone" and z < 3 and (x, y, z + 1) not in v.b:
            v.set(x, y, z + 1, "snow_layer", (0, 0, 0, 1, 1, 0.125))
    for i, (sx, sy, sz) in enumerate(((8, 1, 20), (8, 0, 21), (9, 0, 22))):
        v.set(sx, sy, sz, "smoke", (0.2 + i * 0.05, 0.2, 0, 0.8 - i * 0.05, 0.8, 0.6 - i * 0.1))

    SS = 2
    s = 22 * SS
    pic = v.render(s, pad=40 * SS)
    W = max(pic.width, 1500 * SS)
    H = pic.height + 260 * SS
    bg = gradient(W, H, (176, 196, 222), (232, 238, 246))
    # ein paar Schneeflocken
    rng = random.Random(4)
    dd = ImageDraw.Draw(bg)
    for _ in range(260):
        fx, fy, fr = rng.randint(0, W), rng.randint(0, H), rng.choice((2, 3, 4)) * SS
        dd.rectangle([fx, fy, fx + fr, fy + fr], fill=(255, 255, 255, 200))
    bg.alpha_composite(pic, ((W - pic.width) // 2, 150 * SS))
    d = ImageDraw.Draw(bg)
    d.text((50 * SS, 36 * SS), "Beispielhaus – Winterdorf", font=font(46 * SS, True), fill=(26, 34, 48))
    d.text((50 * SS, 98 * SS),
           "11 × 7 Blöcke · Steinsockel 3 hoch · Fichtenwände mit Dunkeleichen-Fachwerk · Dach 2:1",
           font=font(22 * SS), fill=(55, 66, 84))
    # Legende unten
    items = [("cobblestone", "Bruchstein-Sockel"), ("stone_bricks", "Steinziegel-Kante"),
             ("spruce_planks", "Fichtenwände"), ("dark_oak_log", "Fachwerk"),
             ("deepslate_tiles", "Tiefenschiefer-Dach"), ("snow_layer", "Schneeschicht (Hand)")]
    lx, ly = 50 * SS, H - 120 * SS
    for i, (kind, label) in enumerate(items):
        if i == 3:
            lx, ly = 50 * SS, ly + 52 * SS
        t = tex(kind, "side", 1)
        sz = 4 * SS
        for r in range(N):
            for c in range(N):
                d.rectangle([lx + c * sz, ly + r * sz, lx + (c + 1) * sz - 1, ly + (r + 1) * sz - 1], fill=t[r][c])
        d.rectangle([lx, ly, lx + N * sz, ly + N * sz], outline=(40, 40, 50), width=SS)
        d.text((lx + N * sz + 10 * SS, ly + 5 * SS), label, font=font(19 * SS), fill=(30, 38, 52))
        lx += N * sz + 30 * SS + int(d.textlength(label, font=font(19 * SS)))
    bg.convert("RGB").resize((W // SS, H // SS), Image.LANCZOS).save(OUT / "beispielhaus.png", optimize=True)


# ---------------------------------------------------------------- 3) Dorf-Grundriss
def dorf_grundriss() -> None:
    G = 111
    C = G // 2
    cell = 9
    rng = random.Random(12)
    grid: dict[tuple[int, int], str] = {}
    label_pos: list[tuple[str, int, int]] = []

    def put(x: int, y: int, k: str, force: bool = True) -> None:
        if 0 <= x < G and 0 <= y < G and (force or (x, y) not in grid):
            grid[(x, y)] = k

    R_PAL = 48
    gate_half = 2
    # Palisade
    palisade = 0
    for x in range(G):
        for y in range(G):
            dist = math.hypot(x - C, y - C)
            if abs(dist - R_PAL) < 0.55:
                if y > C and abs(x - C) <= gate_half:
                    continue
                put(x, y, "palisade")
                palisade += 1
    # Torhaus: zwei Türme aus Steinziegeln
    gy = C + R_PAL
    for sx in (C - gate_half - 3, C + gate_half + 1):
        for x in range(sx, sx + 3):
            for y in range(gy - 1, gy + 2):
                put(x, y, "stone_bricks")
    for x in range(C - gate_half, C + gate_half + 1):
        put(x, gy, "gate")
    label_pos.append(("Tor", C, gy + 3))
    # Hauptweg Tor → Platz
    for y in range(C, G):
        for x in range(C - 1, C + 2):
            put(x, y, "path", force=False) if (x, y) in grid and grid[(x, y)] != "gate" else put(x, y, "path", force=False)
    # Dorfplatz
    R_PL = 10
    for x in range(G):
        for y in range(G):
            dd = math.hypot(x - C, y - C)
            if dd <= R_PL:
                put(x, y, "cobblestone" if dd > R_PL - 1.2 else "stone_bricks")
    for x in range(C - 1, C + 2):
        for y in range(C - 1, C + 2):
            put(x, y, "tree_xmas")
    put(C, C, "star")
    for a in range(0, 360, 90):
        put(C + round(6 * math.cos(math.radians(a + 45))), C + round(6 * math.sin(math.radians(a + 45))), "lantern")
    # Brunnen
    wx, wy = C + 5, C - 5
    for x in range(wx - 1, wx + 2):
        for y in range(wy - 1, wy + 2):
            put(x, y, "stone_bricks")
    put(wx, wy, "water")
    label_pos.append(("Dorfplatz", C, C + R_PL + 3))

    houses: list[tuple[int, int, int, int, str]] = []

    def house(cx: int, cy: int, w: int, h: int, name: str) -> None:
        x0, y0 = cx - w // 2, cy - h // 2
        for x in range(x0, x0 + w):
            for y in range(y0, y0 + h):
                edge = x in (x0, x0 + w - 1) or y in (y0, y0 + h - 1)
                ridge = (y == y0 + h // 2) if w >= h else (x == x0 + w // 2)
                put(x, y, "cobblestone" if edge else ("ridge" if ridge else "roof"))
        houses.append((x0, y0, w, h, name))

    # Taverne im Norden
    tx, ty = C, C - 27
    house(tx, ty, 17, 11, "Taverne")
    put(tx + 5, ty - 2, "chimney"); put(tx - 5, ty - 2, "chimney")
    label_pos.append(("Taverne", tx, ty - 9))
    # Häuser im Kreis
    angles = [15, 50, 130, 165, 200, 235, 305, 340]
    for i, a in enumerate(angles):
        rad = 27
        hx = C + round(rad * math.cos(math.radians(a)))
        hy = C + round(rad * math.sin(math.radians(a)))
        w, h = (11, 7) if (abs(math.cos(math.radians(a))) < 0.6) else (7, 11)
        house(hx, hy, w, h, str(i + 1))
    # Wege vom Platz zu jedem Gebäude
    for x0, y0, w, h, _ in houses:
        hx, hy = x0 + w // 2, y0 + h // 2
        dist = math.hypot(hx - C, hy - C)
        steps = int(dist * 2)
        for i in range(steps):
            t = i / steps
            px, py = round(C + (hx - C) * t), round(C + (hy - C) * t)
            if (px, py) not in grid:
                put(px, py, "path")
    # Mammutbäume (2×2-Stamm, Krone)
    trees: list[tuple[int, int]] = []
    tries = 0
    while len(trees) < 46 and tries < 5000:
        tries += 1
        x, y = rng.randint(2, G - 4), rng.randint(2, G - 4)
        dd = math.hypot(x - C, y - C)
        if R_PL + 3 < dd < R_PAL - 3 or dd > R_PAL + 3:
            if abs(x - C) < 4 and y > C:
                continue
            if all((x + i, y + j) not in grid for i in range(-5, 7) for j in range(-5, 7)) and \
                    all(math.hypot(x - a, y - b) > 7 for a, b in trees):
                trees.append((x, y))
    for x, y in trees:
        cr = rng.choice((3.2, 3.8, 4.5))
        for i in range(-5, 7):
            for j in range(-5, 7):
                if math.hypot(i - 0.5, j - 0.5) <= cr and (x + i, y + j) not in grid:
                    put(x + i, y + j, "leaves_dark" if math.hypot(i - 0.5, j - 0.5) < cr * 0.55 else "leaves")
        for i in range(2):
            for j in range(2):
                put(x + i, y + j, "trunk")
        for i in range(-6, 8):
            for j in range(-6, 8):
                if math.hypot(i - 0.5, j - 0.5) <= cr + 1.6 and (x + i, y + j) not in grid and rng.random() < 0.35:
                    put(x + i, y + j, "podzol")

    colors: dict[str, Color] = {
        "palisade": (92, 64, 36), "stone_bricks": (128, 128, 132), "gate": (60, 40, 20),
        "path": (128, 120, 110), "cobblestone": (108, 108, 112), "tree_xmas": (30, 92, 50),
        "star": (255, 210, 60), "lantern": (255, 190, 80), "water": (60, 110, 190),
        "roof": (72, 72, 80), "ridge": (44, 44, 50), "chimney": (100, 100, 104),
        "trunk": (70, 46, 24), "leaves": (46, 84, 58), "leaves_dark": (32, 64, 44),
        "podzol": (206, 200, 196),
    }
    raised = {"palisade", "stone_bricks", "gate", "roof", "ridge", "cobblestone", "trunk",
              "leaves", "leaves_dark", "tree_xmas", "chimney"}
    SS = 2
    cs = cell * SS
    mapw = G * cs
    legend_w = 470 * SS
    top = 130 * SS
    W, H = mapw + legend_w + 90 * SS, top + mapw + 60 * SS
    img = Image.new("RGB", (W, H), (240, 243, 247))
    d = ImageDraw.Draw(img)
    mx, my = 40 * SS, top
    for x in range(G):
        for y in range(G):
            k = grid.get((x, y), "snow")
            base = colors.get(k, (244, 247, 251))
            col = jit(rng, base, 7 if k != "snow" else 4)
            if k == "roof" and rng.random() < 0.2:
                col = jit(rng, (236, 240, 248), 5)  # Schnee auf dem Dach
            px, py = mx + x * cs, my + y * cs
            d.rectangle([px, py, px + cs - 1, py + cs - 1], fill=col)
            if k in raised:
                dark = tuple(clamp(c * 0.72) for c in col)
                lite = tuple(clamp(c * 1.15) for c in col)
                d.rectangle([px, py + cs - 2 * SS, px + cs - 1, py + cs - 1], fill=dark)
                d.rectangle([px + cs - 2 * SS, py, px + cs - 1, py + cs - 1], fill=dark)
                d.rectangle([px, py, px + cs - 1, py + SS - 1], fill=lite)
    # feines Blockraster
    for i in range(0, G + 1, 10):
        d.line([(mx + i * cs, my), (mx + i * cs, my + mapw)], fill=(200, 208, 220), width=1)
        d.line([(mx, my + i * cs), (mx + mapw, my + i * cs)], fill=(200, 208, 220), width=1)
    d.rectangle([mx, my, mx + mapw, my + mapw], outline=(120, 130, 150), width=2 * SS)
    # Hausnummern
    for x0, y0, w, h, name in houses:
        if name == "Taverne":
            continue
        cx, cy = mx + (x0 + w / 2) * cs, my + (y0 + h / 2) * cs
        r = 15 * SS
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255), outline=(30, 38, 52), width=2 * SS)
        f = font(17 * SS, True)
        d.text((cx, cy), name, font=f, fill=(30, 38, 52), anchor="mm")

    def tag(text: str, gx: float, gy: float) -> None:
        f = font(20 * SS, True)
        cx, cy = mx + gx * cs, my + gy * cs
        tw = d.textlength(text, font=f)
        d.rounded_rectangle([cx - tw / 2 - 10 * SS, cy - 16 * SS, cx + tw / 2 + 10 * SS, cy + 16 * SS],
                            radius=8 * SS, fill=(255, 255, 255), outline=(30, 38, 52), width=2 * SS)
        d.text((cx, cy), text, font=f, fill=(30, 38, 52), anchor="mm")

    for text, gx, gy in label_pos:
        tag(text, gx + 0.5, gy + 0.5)
    tag("Palisade", C + R_PAL * math.cos(math.radians(-35)) + 0.5,
        C + R_PAL * math.sin(math.radians(-35)) + 0.5)

    # Titel
    d.text((40 * SS, 34 * SS), "Winterdorf – Grundriss", font=font(48 * SS, True), fill=(26, 34, 48))
    d.text((40 * SS, 94 * SS), f"Draufsicht · 1 Kästchen = 1 Block · Feld {G} × {G} Blöcke",
           font=font(22 * SS), fill=(70, 80, 96))
    # Legende
    lx, ly = mx + mapw + 40 * SS, top
    d.text((lx, ly), "Legende", font=font(30 * SS, True), fill=(26, 34, 48))
    ly += 52 * SS
    entries = [
        ("roof", "Hausdach (Tiefenschiefer, verschneit)"),
        ("cobblestone", "Steinsockel / Platzrand (Bruchstein)"),
        ("stone_bricks", "Dorfplatz, Torhaus, Brunnen (Steinziegel)"),
        ("path", "Freigeschaufelte Wege"),
        ("palisade", "Palisade (Fichtenstämme)"),
        ("gate", "Tor (Dunkeleiche)"),
        ("tree_xmas", "Weihnachtsbaum (Fichte)"),
        ("trunk", "Mammutfichte – 2×2-Stamm"),
        ("leaves", "Fichtenkrone"),
        ("water", "Brunnen"),
        ("lantern", "Laterne"),
        ("snow", "Schnee (per Hand)"),
    ]
    for k, text in entries:
        col = colors.get(k, (244, 247, 251))
        s = 26 * SS
        d.rectangle([lx, ly, lx + s, ly + s], fill=col, outline=(60, 66, 80), width=SS)
        d.text((lx + s + 14 * SS, ly + s / 2), text, font=font(19 * SS), fill=(30, 38, 52), anchor="lm")
        ly += 38 * SS
    ly += 16 * SS
    d.text((lx, ly), "Gebäude", font=font(26 * SS, True), fill=(26, 34, 48))
    ly += 42 * SS
    info = [
        "Taverne: 17 × 11, Nordseite, 2 Kamine",
        "Häuser 1–8: je 11 × 7 im Ring (r ≈ 27)",
        f"Dorfplatz: Ø {2 * R_PL + 1} Blöcke",
        f"Palisade: r = {R_PAL}, ≈ {palisade} Stämme",
        "   pro Lage (× Höhe, z. B. 4 → ≈ " + f"{palisade * 4})",
        "Tor im Süden, 5 breit, Hauptweg 3 breit",
    ]
    for line in info:
        d.text((lx, ly), line, font=font(19 * SS), fill=(40, 48, 62))
        ly += 32 * SS
    # Nordpfeil + Maßstab
    ny = ly + 40 * SS
    ax = lx + 40 * SS
    d.polygon([(ax, ny), (ax - 18 * SS, ny + 50 * SS), (ax, ny + 38 * SS), (ax + 18 * SS, ny + 50 * SS)],
              fill=(30, 38, 52))
    d.text((ax, ny + 70 * SS), "N", font=font(24 * SS, True), fill=(30, 38, 52), anchor="mm")
    sx, sy = lx + 110 * SS, ny + 30 * SS
    for i in range(10):
        d.rectangle([sx + i * cs, sy, sx + (i + 1) * cs - 1, sy + 12 * SS],
                    fill=(30, 38, 52) if i % 2 == 0 else (255, 255, 255), outline=(30, 38, 52))
    d.text((sx, sy + 22 * SS), "10 Blöcke", font=font(18 * SS), fill=(30, 38, 52))
    img.resize((W // SS, H // SS), Image.LANCZOS).save(OUT / "dorf_grundriss.png", optimize=True)


if __name__ == "__main__":
    blockpalette()
    beispielhaus()
    dorf_grundriss()
    print("fertig:", ", ".join(p.name for p in sorted(OUT.glob("*.png"))))
