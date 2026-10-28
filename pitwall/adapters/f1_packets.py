"""Binär-Layouts der EA SPORTS F1 24/25 UDP-Telemetrie (Formate 2024, 2025, 2026).

Eigene Definitionen nach ``docs/protocols/f1.md`` (dort Quellen und
Größenprüfung). Little Endian, kein Padding. Jedes Paket wird vor dem Parsen
gegen die dokumentierte Größe geprüft.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

HEADER = struct.Struct("<HBBBBBQfIIBB")  # 29 Bytes
assert HEADER.size == 29


class PacketId:
    MOTION = 0
    SESSION = 1
    LAP_DATA = 2
    EVENT = 3
    PARTICIPANTS = 4
    CAR_SETUPS = 5
    CAR_TELEMETRY = 6
    CAR_STATUS = 7
    FINAL_CLASSIFICATION = 8
    LOBBY_INFO = 9
    CAR_DAMAGE = 10
    SESSION_HISTORY = 11
    TYRE_SETS = 12
    MOTION_EX = 13
    TIME_TRIAL = 14
    LAP_POSITIONS = 15
    CAR_TELEMETRY_2 = 16


@dataclass(frozen=True)
class Layout:
    fmt: int
    num_cars: int
    name_len: int
    motion: struct.Struct
    lap: struct.Struct
    participant: struct.Struct
    telemetry: struct.Struct
    status: struct.Struct
    damage: struct.Struct
    final: struct.Struct
    sizes: dict[int, int]


_MOTION_25 = struct.Struct("<ffffffhhhhhhffffff")  # 60
_MOTION_26 = struct.Struct("<ffffffhhhhhhhhhfff")  # 54
_LAP = struct.Struct("<IIHBHBHBHBfffBBBBBBBBBBBBBBBHHBfB")  # 57
_PART_24 = struct.Struct("<BBBBBBB48sBBHB")  # 60
_PART_25 = struct.Struct("<BBBBBBB32sBBHBB12B")  # 57
_PART_26 = struct.Struct("<BHHHBBB32sBBHBB12B")  # 60
_TELE_25 = struct.Struct("<HfffBbHBBH4H4B4BH4f4B")  # 60
_TELE_26 = struct.Struct("<HfffBbHBBH4H4B4BB4f4B")  # 59
_STATUS_25 = struct.Struct("<BBBBBfffHHBBHBBBbfffBfffB")  # 55
_STATUS_26 = struct.Struct("<BBBBBfffHHBBHBBBbfffBffffB")  # 59
_DAMAGE_24 = struct.Struct("<4f4B4B18B")  # 42
_DAMAGE_25 = struct.Struct("<4f4B4B4B18B")  # 46
_FINAL_24 = struct.Struct("<BBBBBBIdBBB8B8B8B")  # 45
_FINAL_25 = struct.Struct("<BBBBBBBIdBBB8B8B8B")  # 46

SESSION_HEAD = struct.Struct("<BbbBHBbBHHBBBBBB")  # 19 Bytes ab Offset 29
MARSHAL = struct.Struct("<fb")
FORECAST = struct.Struct("<BBBbbbbB")
LAP_HISTORY = struct.Struct("<IHBHBHBB")  # 14
STINT_HISTORY = struct.Struct("<BBB")

_SIZES_25 = {0: 1349, 1: 753, 2: 1285, 3: 45, 4: 1284, 5: 1133, 6: 1352, 7: 1239, 8: 1042, 9: 954, 10: 1041,
             11: 1460, 12: 231, 13: 273, 14: 101, 15: 1131}
_SIZES_24 = {**_SIZES_25, 4: 1350, 8: 1020, 9: 1306, 10: 953, 13: 237}
del _SIZES_24[15]
_SIZES_26 = {0: 1325, 1: 926, 2: 1399, 3: 45, 4: 1470, 5: 1233, 6: 1448, 7: 1445, 8: 1134, 9: 1062, 10: 1133,
             11: 1460, 12: 231, 13: 273, 14: 104, 15: 1231, 16: 269}

LAYOUTS: dict[int, Layout] = {
    2024: Layout(2024, 22, 48, _MOTION_25, _LAP, _PART_24, _TELE_25, _STATUS_25, _DAMAGE_24, _FINAL_24, _SIZES_24),
    2025: Layout(2025, 22, 32, _MOTION_25, _LAP, _PART_25, _TELE_25, _STATUS_25, _DAMAGE_25, _FINAL_25, _SIZES_25),
    2026: Layout(2026, 24, 32, _MOTION_26, _LAP, _PART_26, _TELE_26, _STATUS_26, _DAMAGE_25, _FINAL_25, _SIZES_26),
}


def _check() -> None:
    """Selbsttest: Struct-Größen ergeben die dokumentierten Paketgrößen."""
    for lay in LAYOUTS.values():
        n = lay.num_cars
        assert 29 + n * lay.motion.size == lay.sizes[0]
        assert 29 + n * lay.lap.size + 2 == lay.sizes[2]
        assert 29 + 1 + n * lay.participant.size == lay.sizes[4]
        assert 29 + n * lay.telemetry.size + 3 == lay.sizes[6]
        assert 29 + n * lay.status.size == lay.sizes[7]
        assert 29 + 1 + n * lay.final.size == lay.sizes[8]
        assert 29 + n * lay.damage.size == lay.sizes[10]
    assert 29 + SESSION_HEAD.size + 21 * MARSHAL.size + 3 + 64 * FORECAST.size + 32 + 33 + 12 + 8 == 753
    assert 29 + 7 + 100 * LAP_HISTORY.size + 8 * STINT_HISTORY.size == 1460


_check()

# ------------------------------------------------------------------ Enums

SESSION_TYPES = {0: "unknown", 1: "practice", 2: "practice", 3: "practice", 4: "practice", 5: "qualifying",
                 6: "qualifying", 7: "qualifying", 8: "qualifying", 9: "qualifying", 10: "qualifying",
                 11: "qualifying", 12: "qualifying", 13: "qualifying", 14: "qualifying", 15: "race", 16: "race",
                 17: "race", 18: "time_trial"}

TRACKS = {0: "Melbourne", 1: "Paul Ricard", 2: "Shanghai", 3: "Sakhir", 4: "Barcelona", 5: "Monaco",
          6: "Montreal", 7: "Silverstone", 8: "Hockenheim", 9: "Hungaroring", 10: "Spa", 11: "Monza",
          12: "Singapore", 13: "Suzuka", 14: "Abu Dhabi", 15: "Austin", 16: "Interlagos", 17: "Spielberg",
          18: "Sochi", 19: "Mexico", 20: "Baku", 21: "Sakhir Short", 22: "Silverstone Short", 23: "Austin Short",
          24: "Suzuka Short", 25: "Hanoi", 26: "Zandvoort", 27: "Imola", 28: "Portimao", 29: "Jeddah",
          30: "Miami", 31: "Las Vegas", 32: "Lusail", 39: "Silverstone Reverse", 40: "Spielberg Reverse",
          41: "Zandvoort Reverse", 42: "Madrid"}

# Strecken-IDs → Schlüssel der config/tracks/f1_*.toml
TRACK_KEYS = {0: "melbourne", 2: "shanghai", 3: "bahrain", 4: "barcelona", 5: "monaco", 6: "montreal",
              7: "silverstone", 9: "hungaroring", 10: "spa", 11: "monza", 12: "singapore", 13: "suzuka",
              14: "abu_dhabi", 15: "austin", 16: "interlagos", 17: "spielberg", 19: "mexico", 20: "baku",
              26: "zandvoort", 27: "imola", 29: "jeddah", 30: "miami", 31: "las_vegas", 32: "lusail",
              42: "madrid"}

WEATHER = {0: "clear", 1: "light_cloud", 2: "overcast", 3: "light_rain", 4: "heavy_rain", 5: "storm"}
RAIN_INTENSITY = {0: 0.0, 1: 0.0, 2: 0.0, 3: 0.35, 4: 0.75, 5: 1.0}

VISUAL_COMPOUND_F1 = {16: "soft", 17: "medium", 18: "hard", 7: "inter", 8: "wet"}
VISUAL_COMPOUND_F2 = {19: "soft", 20: "soft", 21: "medium", 22: "hard", 15: "wet"}
ACTUAL_COMPOUND = {16: "C5", 17: "C4", 18: "C3", 19: "C2", 20: "C1", 21: "C0", 22: "C6", 7: "inter", 8: "wet"}

ERS_MODES = {0: "none", 1: "medium", 2: "hotlap", 3: "overtake"}
FUEL_MIX = {0: "lean", 1: "standard", 2: "rich", 3: "max"}

PENALTY_TYPES = {0: "drive_through", 1: "stop_go", 2: "grid", 3: "reminder", 4: "time", 5: "warning",
                 6: "disqualified", 7: "removed_from_formation", 8: "parked_too_long", 9: "tyre_regulations",
                 10: "lap_invalidated", 11: "this_and_next_lap_invalidated", 12: "lap_invalidated",
                 13: "this_and_next_lap_invalidated", 14: "this_and_previous_lap_invalidated",
                 15: "this_and_previous_lap_invalidated", 16: "retired", 17: "black_flag_timer"}
CORNER_CUTTING_INFRINGEMENTS = {7, 8, 9, 25, 26, 27, 28, 29}

TEAMS = {0: ("Mercedes", "#27F4D2"), 1: ("Ferrari", "#E8002D"), 2: ("Red Bull Racing", "#3671C6"),
         3: ("Williams", "#64C4FF"), 4: ("Aston Martin", "#229971"), 5: ("Alpine", "#FF87BC"),
         6: ("RB", "#6692FF"), 7: ("Haas", "#B6BABD"), 8: ("McLaren", "#FF8000"), 9: ("Sauber", "#52E252"),
         41: ("F1 Generic", "#888888"), 104: ("Custom Team", "#F5C518"), 129: ("Konnersport", "#00A19C"),
         142: ("APXGP", "#111111"), 154: ("APXGP", "#111111"),
         185: ("Mercedes", "#27F4D2"), 186: ("Ferrari", "#E8002D"), 187: ("Red Bull Racing", "#3671C6"),
         188: ("Williams", "#64C4FF"), 189: ("Aston Martin", "#229971"), 190: ("Alpine", "#FF87BC"),
         191: ("RB", "#6692FF"), 192: ("Haas", "#B6BABD"), 193: ("McLaren", "#FF8000"), 194: ("Sauber", "#52E252"),
         476: ("Mercedes", "#27F4D2"), 477: ("Ferrari", "#E8002D"), 478: ("Red Bull Racing", "#3671C6"),
         479: ("Williams", "#64C4FF"), 480: ("Aston Martin", "#229971"), 481: ("Alpine", "#FF87BC"),
         482: ("RB", "#6692FF"), 483: ("Haas", "#B6BABD"), 484: ("McLaren", "#FF8000"),
         485: ("Audi", "#BB0A30"), 486: ("Cadillac", "#C0C0C0")}


def ms(minutes: int, ms_part: int) -> float:
    return (minutes * 60000 + ms_part) / 1000.0


# ------------------------------------------------------- Bauen (Fixtures)


def pack_header(fmt: int, packet_id: int, session_uid: int = 1, session_time: float = 0.0, frame: int = 0,
                player_idx: int = 0) -> bytes:
    year = {2024: 24, 2025: 25, 2026: 26}.get(fmt, 25)
    return HEADER.pack(fmt, year, 1, 0, 1, packet_id, session_uid, session_time, frame, frame, player_idx, 255)
