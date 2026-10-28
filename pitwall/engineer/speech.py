"""Zahlen sprechfreundlich formatieren (Funkstil) – Deutsch und Englisch.

Jeder Wert wird zweimal gerendert:

* ``display`` für Dashboard/Overlay (Ziffern: "0,4 s", "1:32,5", "P3")
* ``spoken`` für die Sprachausgabe ("null Komma vier", "eins zweiunddreißig
  Komma fünf", "P drei")
"""

from __future__ import annotations

import math
from dataclasses import dataclass

_DE_UNITS = ["null", "eins", "zwei", "drei", "vier", "fünf", "sechs", "sieben", "acht", "neun", "zehn", "elf",
             "zwölf", "dreizehn", "vierzehn", "fünfzehn", "sechzehn", "siebzehn", "achtzehn", "neunzehn"]
_DE_TENS = ["", "", "zwanzig", "dreißig", "vierzig", "fünfzig", "sechzig", "siebzig", "achtzig", "neunzig"]

_EN_UNITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
             "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_EN_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def int_words_de(n: int, standalone: bool = True) -> str:
    if n < 0:
        return "minus " + int_words_de(-n, standalone)
    if n < 20:
        word = _DE_UNITS[n]
        if n == 1 and not standalone:
            return "ein"
        return word
    if n < 100:
        t, u = divmod(n, 10)
        if u == 0:
            return _DE_TENS[t]
        return ("ein" if u == 1 else _DE_UNITS[u]) + "und" + _DE_TENS[t]
    if n < 1000:
        h, rest = divmod(n, 100)
        head = "hundert" if h == 1 else _DE_UNITS[h] + "hundert"
        return head + (int_words_de(rest, standalone) if rest else "")
    if n < 1_000_000:
        th, rest = divmod(n, 1000)
        head = "tausend" if th == 1 else int_words_de(th, False) + "tausend"
        return head + (int_words_de(rest, standalone) if rest else "")
    return str(n)


def int_words_en(n: int) -> str:
    if n < 0:
        return "minus " + int_words_en(-n)
    if n < 20:
        return _EN_UNITS[n]
    if n < 100:
        t, u = divmod(n, 10)
        return _EN_TENS[t] + ("-" + _EN_UNITS[u] if u else "")
    if n < 1000:
        h, rest = divmod(n, 100)
        return _EN_UNITS[h] + " hundred" + (" " + int_words_en(rest) if rest else "")
    if n < 1_000_000:
        th, rest = divmod(n, 1000)
        return int_words_en(th) + " thousand" + (" " + int_words_en(rest) if rest else "")
    return str(n)


@dataclass(frozen=True, slots=True)
class Spoken:
    display: str
    spoken: str

    def __str__(self) -> str:
        return self.display


class Speaker:
    """Formatierer für eine Sprache."""

    def __init__(self, lang: str = "de", pressure_unit: str = "psi", speed_unit: str = "kmh"):
        self.lang = "en" if lang.startswith("en") else "de"
        self.pressure_unit = pressure_unit
        self.speed_unit = speed_unit

    # -------------------------------------------------------------- Basis
    def int_(self, n: int) -> Spoken:
        words = int_words_de(n) if self.lang == "de" else int_words_en(n)
        return Spoken(str(n), words)

    def decimal(self, value: float, places: int = 1, strip_zero: bool = True) -> Spoken:
        """0.4 → 'null Komma vier' / 'point four'."""
        neg = value < 0
        value = abs(value)
        rounded = round(value, places)
        whole = int(math.floor(rounded + 1e-9))
        frac_digits = f"{rounded:.{places}f}".split(".")[1] if places > 0 else ""
        if strip_zero:
            frac_digits = frac_digits.rstrip("0")
        sep = "," if self.lang == "de" else "."
        display = ("-" if neg else "") + str(whole) + (sep + frac_digits if frac_digits else "")
        if self.lang == "de":
            words = int_words_de(whole)
            if frac_digits:
                words += " Komma " + " ".join(_DE_UNITS[int(d)] for d in frac_digits)
            if neg:
                words = "minus " + words
        else:
            words = "" if whole == 0 and frac_digits else int_words_en(whole)
            if frac_digits:
                words = (words + " " if words else "") + "point " + " ".join(_EN_UNITS[int(d)] for d in frac_digits)
            if neg:
                words = "minus " + words
        return Spoken(display, words.strip())

    # -------------------------------------------------------- Domänen
    def seconds(self, s: float, places: int = 1) -> Spoken:
        """Abstände/Deltas: '0,4' gesprochen ohne Einheit (Funkstil)."""
        return self.decimal(abs(s), places, strip_zero=False)

    def n_laps(self, n: int, case: str = "nom") -> Spoken:
        """'eine Runde' / 'zwei Runden' (case='dat': 'einer Runde')."""
        n = int(n)
        if self.lang == "de":
            if n == 1:
                word = "einer Runde" if case == "dat" else "eine Runde"
                return Spoken("1 Runde", word)
            return Spoken(f"{n} Runden", f"{int_words_de(n)} Runden")
        if n == 1:
            return Spoken("1 lap", "one lap")
        return Spoken(f"{n} laps", f"{int_words_en(n)} laps")

    def seconds_unit(self, s: float, places: int = 1) -> Spoken:
        d = self.decimal(abs(s), places)
        if self.lang == "de":
            unit = "Sekunde" if d.display in ("1", "1,0") else "Sekunden"
            return Spoken(d.display + " s", f"{d.spoken} {unit}")
        unit = "second" if d.display in ("1", "1.0") else "seconds"
        return Spoken(d.display + " s", f"{d.spoken} {unit}")

    def laptime(self, s: float | None, places: int = 1) -> Spoken:
        """92.53 → '1:32,5' / 'eins zweiunddreißig Komma fünf'."""
        if s is None or s <= 0:
            return Spoken("–", "keine Zeit" if self.lang == "de" else "no time")
        minutes = int(s // 60)
        rest = s - minutes * 60
        rest_r = round(rest, places)
        if rest_r >= 60:
            minutes += 1
            rest_r -= 60
        sep = "," if self.lang == "de" else "."
        sec_int = int(rest_r)
        frac = f"{rest_r:.{places}f}".split(".")[1] if places else ""
        display = (f"{minutes}:{sec_int:02d}" if minutes else f"{sec_int}") + (sep + frac if frac else "")
        if self.lang == "de":
            parts = []
            if minutes:
                parts.append(int_words_de(minutes))
                parts.append("null " + _DE_UNITS[sec_int] if sec_int < 10 else int_words_de(sec_int))
            else:
                parts.append(int_words_de(sec_int))
            if frac:
                parts.append("Komma " + " ".join(_DE_UNITS[int(d)] for d in frac))
        else:
            parts = []
            if minutes:
                parts.append(int_words_en(minutes))
                parts.append("oh " + _EN_UNITS[sec_int] if sec_int < 10 else int_words_en(sec_int))
            else:
                parts.append(int_words_en(sec_int))
            if frac:
                parts.append("point " + " ".join(_EN_UNITS[int(d)] for d in frac))
        return Spoken(display, " ".join(parts))

    def position(self, p: int | None) -> Spoken:
        if p is None:
            return Spoken("P?", "unbekannt" if self.lang == "de" else "unknown")
        words = int_words_de(p) if self.lang == "de" else int_words_en(p)
        return Spoken(f"P{p}", f"P {words}")

    def lap(self, n: int) -> Spoken:
        return self.int_(n)

    def laps(self, value: float, places: int = 1) -> Spoken:
        if abs(value - round(value)) < 0.05 or places == 0:
            return self.int_(int(round(value)))
        return self.decimal(value, places)

    def litres(self, value: float, places: int = 1) -> Spoken:
        d = self.decimal(value, places)
        if self.lang == "de":
            return Spoken(d.display + " l", d.spoken + " Liter")
        return Spoken(d.display + " l", d.spoken + (" litre" if d.display == "1" else " litres"))

    def temp(self, c: float) -> Spoken:
        n = int(round(c))
        return Spoken(f"{n} °C", (int_words_de(n) + " Grad") if self.lang == "de" else (int_words_en(n) + " degrees"))

    def pressure(self, kpa: float) -> Spoken:
        if self.pressure_unit == "kpa":
            d = self.decimal(kpa, 0)
            unit = "kPa"
            spoken_unit = "Kilopascal" if self.lang == "de" else "kilopascal"
        elif self.pressure_unit == "bar":
            d = self.decimal(kpa / 100.0, 2)
            unit = "bar"
            spoken_unit = "bar"
        else:
            d = self.decimal(kpa * 0.1450377, 1)
            unit = "psi"
            spoken_unit = "P S I"
        return Spoken(f"{d.display} {unit}", f"{d.spoken} {spoken_unit}")

    def speed(self, ms: float) -> Spoken:
        if self.speed_unit == "mph":
            v = int(round(ms * 2.236936))
            return Spoken(f"{v} mph", f"{self.int_(v).spoken} {'Meilen pro Stunde' if self.lang == 'de' else 'miles per hour'}")
        v = int(round(ms * 3.6))
        return Spoken(f"{v} km/h", f"{self.int_(v).spoken} {'Kilometer pro Stunde' if self.lang == 'de' else 'kilometres per hour'}")

    def metres(self, m: float) -> Spoken:
        n = int(round(m / 5.0) * 5) if m >= 20 else int(round(m))
        return Spoken(f"{n} m", f"{self.int_(n).spoken} {'Meter' if self.lang == 'de' else 'metres'}")

    def percent(self, pct: float) -> Spoken:
        n = int(round(pct))
        return Spoken(f"{n} %", f"{self.int_(n).spoken} {'Prozent' if self.lang == 'de' else 'percent'}")

    def minutes(self, m: float) -> Spoken:
        n = max(1, int(round(m)))
        if self.lang == "de":
            return Spoken(f"{n} min", ("einer Minute" if n == 1 else f"{int_words_de(n)} Minuten"))
        return Spoken(f"{n} min", ("one minute" if n == 1 else f"{int_words_en(n)} minutes"))

    def km(self, km: float) -> Spoken:
        if km < 10:
            d = self.decimal(km, 1)
        else:
            d = self.int_(int(round(km)))
        return Spoken(f"{d.display} km", f"{d.spoken} {'Kilometer' if self.lang == 'de' else 'kilometres'}")

    def clock(self, minutes_total: int) -> Spoken:
        """Spielzeit-Minuten → Uhrzeit (ETS2)."""
        h, m = divmod(int(minutes_total) % (24 * 60), 60)
        if self.lang == "de":
            spoken = f"{int_words_de(h)} Uhr" + (f" {int_words_de(m)}" if m else "")
        else:
            spoken = f"{int_words_en(h)} {('oh ' + _EN_UNITS[m]) if 0 < m < 10 else int_words_en(m) if m else 'hundred'}"
        return Spoken(f"{h:02d}:{m:02d}", spoken)

    def wheel(self, code: str | None) -> Spoken:
        names_de = {"FL": "vorne links", "FR": "vorne rechts", "RL": "hinten links", "RR": "hinten rechts"}
        names_en = {"FL": "front left", "FR": "front right", "RL": "rear left", "RR": "rear right"}
        names = names_de if self.lang == "de" else names_en
        if code is None:
            return Spoken("?", "?")
        return Spoken(names.get(code, code), names.get(code, code))

    def compound(self, name: str | None) -> Spoken:
        de = {"soft": "Soft", "medium": "Medium", "hard": "Hard", "inter": "Intermediates", "wet": "Regenreifen",
              "dry": "Slicks", "inters": "Intermediates", "wets": "Regenreifen", "slicks": "Slicks"}
        en = {"soft": "softs", "medium": "mediums", "hard": "hards", "inter": "inters", "wet": "wets",
              "dry": "slicks", "inters": "inters", "wets": "wets", "slicks": "slicks"}
        table = de if self.lang == "de" else en
        if not name:
            return Spoken("?", "unbekannt" if self.lang == "de" else "unknown")
        val = table.get(name, name)
        return Spoken(val, val)

    def name(self, full_name: str | None) -> Spoken:
        """Gegner beim Nachnamen nennen (wie im Funk)."""
        if not full_name:
            word = "das Auto" if self.lang == "de" else "the car"
            return Spoken(word, word)
        parts = full_name.strip().split()
        last = parts[-1] if parts else full_name
        if len(last) <= 2 and len(parts) > 1:
            last = full_name
        return Spoken(last, last)

    def text(self, s: str) -> Spoken:
        return Spoken(s, s)
