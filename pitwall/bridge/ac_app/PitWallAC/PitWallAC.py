# PitWallAC - Assetto Corsa (2014) In-Game-App fuer PIT WALL
#
# Exportiert Positionen, Runden und Zeiten ALLER Autos per UDP (JSON) an
# 127.0.0.1:20791. Das Shared Memory von AC enthaelt nur das eigene Auto -
# erst mit dieser App kennt der Renningenieur Gaps, Gegner und Verkehr.
#
# Installation: Ordner "PitWallAC" nach
#   <AC>/apps/python/PitWallAC
# kopieren und im Spiel (Optionen -> General -> UI Modules) aktivieren.
#
# AC liefert Python 3.3 ohne das Modul _socket mit. Liegt im Unterordner
# stdlib64/ eine _socket.pyd aus einer offiziellen Python-3.3-x64-Installation
# (PSF-Lizenz), wird sie geladen. Details: docs/setup-assetto-corsa.md
#
# Lizenz: MIT (Teil von PIT WALL)

import json
import os
import sys

import ac
import acsys

APP_NAME = "PitWallAC"
TARGET = ("127.0.0.1", 20791)
RATE_HZ = 10.0

_dir = os.path.dirname(os.path.abspath(__file__))
for sub in ("stdlib64", "stdlib"):
    p = os.path.join(_dir, sub)
    if os.path.isdir(p) and p not in sys.path:
        sys.path.insert(0, p)

try:
    import socket
    _sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    _sock.setblocking(False)
    _status = "sendet an %s:%d" % TARGET
except Exception as exc:
    _sock = None
    _status = "kein socket-Modul (%s)" % exc

_label = None
_timer = 0.0
_sent = 0


def acMain(ac_version):
    global _label
    window = ac.newApp(APP_NAME)
    ac.setSize(window, 260, 60)
    _label = ac.addLabel(window, "PIT WALL: " + _status)
    ac.setPosition(_label, 10, 30)
    ac.log("PitWallAC: " + _status)
    return APP_NAME


def _car(i):
    x, _y, z = ac.getCarState(i, acsys.CS.WorldPosition)
    return {
        "id": i,
        "name": ac.getDriverName(i),
        "car": ac.getCarName(i),
        "connected": bool(ac.isConnected(i)),
        "pos": ac.getCarRealTimeLeaderboardPosition(i) + 1,
        "laps": ac.getCarState(i, acsys.CS.LapCount),
        "spline": ac.getCarState(i, acsys.CS.NormalizedSplinePosition),
        "last": ac.getCarState(i, acsys.CS.LastLap),
        "best": ac.getCarState(i, acsys.CS.BestLap),
        "pit": bool(ac.isCarInPitline(i) or ac.isCarInPit(i)),
        "speed": ac.getCarState(i, acsys.CS.SpeedMS),
        "x": x,
        "z": z,
    }


def acUpdate(delta_t):
    global _timer, _sent
    if _sock is None:
        return
    _timer += delta_t
    if _timer < 1.0 / RATE_HZ:
        return
    _timer = 0.0
    try:
        cars = [_car(i) for i in range(ac.getCarsCount())]
        payload = json.dumps({"v": 1, "focus": ac.getFocusedCar(), "cars": cars}, separators=(",", ":"))
        _sock.sendto(payload.encode("utf-8"), TARGET)
        _sent += 1
        if _label is not None and _sent % 50 == 1:
            ac.setText(_label, "PIT WALL: %d Autos, %d Pakete" % (len(cars), _sent))
    except Exception as exc:
        if _label is not None:
            ac.setText(_label, "PIT WALL Fehler: %s" % exc)


def acShutdown():
    if _sock is not None:
        try:
            _sock.close()
        except Exception:
            pass
