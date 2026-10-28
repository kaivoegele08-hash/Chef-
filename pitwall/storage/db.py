"""Langzeitgedächtnis (SQLite): lernt über Sessions hinweg.

Pro Sim/Strecke/Auto: Spritverbrauch, Boxenverlust, typische und beste
Rundenzeiten, Notizen des Fahrers (Balance/Setup), Schwachkurven, Debriefs.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sim TEXT, track TEXT, car TEXT, session_type TEXT,
    started REAL, ended REAL, laps INTEGER DEFAULT 0,
    start_pos INTEGER, result_pos INTEGER, best_lap REAL,
    recording TEXT, meta TEXT
);
CREATE TABLE IF NOT EXISTS laps (
    session_id INTEGER, lap INTEGER, time_s REAL, fuel_used REAL, compound TEXT, tyre_age INTEGER,
    valid INTEGER, clean INTEGER, position INTEGER, sectors TEXT, ts REAL
);
CREATE INDEX IF NOT EXISTS laps_session ON laps(session_id);
CREATE TABLE IF NOT EXISTS pit_loss (sim TEXT, track TEXT, value REAL, ts REAL);
CREATE TABLE IF NOT EXISTS notes (sim TEXT, track TEXT, car TEXT, kind TEXT, text TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS corners (
    session_id INTEGER, sim TEXT, track TEXT, corner INTEGER, name TEXT, loss_s REAL, cause TEXT, ts REAL
);
CREATE TABLE IF NOT EXISTS debriefs (session_id INTEGER, text TEXT, data TEXT, ts REAL);
"""


class LongTermMemory:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()
        self.session_id: int | None = None

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def _exec(self, sql: str, args: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur

    def _query(self, sql: str, args: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self.conn.execute(sql, args).fetchall())

    # ----------------------------------------------------------- Sessions
    def start_session(self, sim: str, track: str, car: str, session_type: str, start_pos: int | None = None,
                      recording: str | None = None, meta: dict[str, Any] | None = None) -> int:
        cur = self._exec("INSERT INTO sessions (sim, track, car, session_type, started, start_pos, recording, meta)"
                         " VALUES (?,?,?,?,?,?,?,?)",
                         (sim, track, car, session_type, time.time(), start_pos, recording, json.dumps(meta or {})))
        self.session_id = int(cur.lastrowid or 0)
        return self.session_id

    def end_session(self, result_pos: int | None, best_lap: float | None, laps: int) -> None:
        if self.session_id is None:
            return
        self._exec("UPDATE sessions SET ended=?, result_pos=?, best_lap=?, laps=? WHERE id=?",
                   (time.time(), result_pos, best_lap, laps, self.session_id))

    def set_start_position(self, pos: int | None) -> None:
        if self.session_id is not None and pos is not None:
            self._exec("UPDATE sessions SET start_pos=? WHERE id=?", (pos, self.session_id))

    def record_lap(self, lap: int, time_s: float | None, fuel_used: float | None, compound: str | None,
                   tyre_age: int | None, valid: bool, clean: bool, position: int | None,
                   sectors: list[float | None] | None = None) -> None:
        if self.session_id is None:
            return
        self._exec("INSERT INTO laps VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (self.session_id, lap, time_s, fuel_used, compound, tyre_age, int(valid), int(clean), position,
                    json.dumps(sectors or []), time.time()))
        self._exec("UPDATE sessions SET laps=MAX(laps, ?) WHERE id=?", (lap, self.session_id))

    def sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        return [dict(r) for r in self._query("SELECT * FROM sessions ORDER BY id DESC LIMIT ?", (limit,))]

    def session_laps(self, session_id: int) -> list[dict[str, Any]]:
        return [dict(r) for r in self._query("SELECT * FROM laps WHERE session_id=? ORDER BY lap", (session_id,))]

    # -------------------------------------------------------- Lernwerte
    def record_pit_loss(self, sim: str, track: str, value: float) -> None:
        self._exec("INSERT INTO pit_loss VALUES (?,?,?,?)", (sim, track, value, time.time()))

    def pit_loss(self, sim: str, track: str) -> list[float]:
        rows = self._query("SELECT value FROM pit_loss WHERE sim=? AND track=? ORDER BY ts DESC LIMIT 12",
                           (sim, track))
        return [float(r["value"]) for r in rows]

    def fuel_per_lap(self, sim: str, track: str, car: str) -> float | None:
        rows = self._query(
            "SELECT l.fuel_used FROM laps l JOIN sessions s ON l.session_id=s.id "
            "WHERE s.sim=? AND s.track=? AND s.car=? AND l.clean=1 AND l.fuel_used > 0 ORDER BY l.ts DESC LIMIT 40",
            (sim, track, car))
        vals = [float(r["fuel_used"]) for r in rows]
        if len(vals) < 3:
            return None
        return statistics.median(vals)

    def lap_stats(self, sim: str, track: str, car: str) -> dict[str, Any]:
        rows = self._query(
            "SELECT l.time_s FROM laps l JOIN sessions s ON l.session_id=s.id "
            "WHERE s.sim=? AND s.track=? AND s.car=? AND l.valid=1 AND l.time_s > 0", (sim, track, car))
        times = sorted(float(r["time_s"]) for r in rows)
        if not times:
            return {"laps": 0}
        return {"laps": len(times), "best_s": round(times[0], 3),
                "typical_s": round(statistics.median(times[: max(3, len(times) // 3)]), 3)}

    def add_note(self, sim: str, track: str, car: str, text: str, kind: str = "note") -> None:
        self._exec("INSERT INTO notes VALUES (?,?,?,?,?,?)", (sim, track, car, kind, text, time.time()))

    def notes(self, sim: str, track: str, car: str, limit: int = 10) -> list[dict[str, Any]]:
        rows = self._query("SELECT kind, text, ts FROM notes WHERE sim=? AND (track=? OR track='') AND "
                           "(car=? OR car='') ORDER BY ts DESC LIMIT ?", (sim, track, car, limit))
        return [dict(r) for r in rows]

    def record_corner_losses(self, sim: str, track: str, items: list[dict[str, Any]]) -> None:
        for it in items:
            self._exec("INSERT INTO corners VALUES (?,?,?,?,?,?,?,?)",
                       (self.session_id, sim, track, int(it["corner"]), it.get("name", ""), float(it["loss_s"]),
                        it.get("cause", ""), time.time()))

    def weak_corners(self, sim: str, track: str, limit: int = 5) -> list[dict[str, Any]]:
        rows = self._query("SELECT corner, name, AVG(loss_s) AS loss, COUNT(*) AS n, "
                           "GROUP_CONCAT(cause, '|') AS causes FROM corners WHERE sim=? AND track=? "
                           "GROUP BY corner ORDER BY loss DESC LIMIT ?", (sim, track, limit))
        out = []
        for r in rows:
            causes = [c for c in (r["causes"] or "").split("|") if c]
            top = max(set(causes), key=causes.count) if causes else ""
            out.append({"corner": r["corner"], "name": r["name"], "avg_loss_s": round(r["loss"], 3), "samples": r["n"],
                        "typical_cause": top})
        return out

    def save_debrief(self, text: str, data: dict[str, Any]) -> None:
        self._exec("INSERT INTO debriefs VALUES (?,?,?,?)", (self.session_id, text, json.dumps(data, default=str),
                                                               time.time()))

    def debriefs(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._query("SELECT d.*, s.sim, s.track, s.session_type, s.result_pos FROM debriefs d "
                           "LEFT JOIN sessions s ON d.session_id=s.id ORDER BY d.ts DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def summary(self, sim: str, track: str, car: str) -> dict[str, Any]:
        return {
            "fuel_per_lap_l": self.fuel_per_lap(sim, track, car),
            "pit_loss_samples_s": self.pit_loss(sim, track),
            "lap_stats": self.lap_stats(sim, track, car),
            "notes": self.notes(sim, track, car),
            "weak_corners": self.weak_corners(sim, track),
            "sessions_here": len(self._query("SELECT id FROM sessions WHERE sim=? AND track=?", (sim, track))),
        }
