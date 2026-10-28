"""Integrationstest der Proton-Bridge unter Wine (wird übersprungen, wenn Wine/Binaries fehlen)."""

import asyncio
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from pitwall.adapters.assetto_corsa import ACDecoder, ac_maps
from pitwall.adapters.shm import ShmSource

ROOT = Path(__file__).resolve().parents[1]
WINE = shutil.which("wine64") or ("/usr/lib/wine/wine64" if Path("/usr/lib/wine/wine64").exists() else None)
RELAY = ROOT / "pitwall" / "bridge" / "pitwall-relay.exe"
GAME = ROOT / "tests" / "wine" / "fake_ac_game.exe"

pytestmark = pytest.mark.skipif(not (WINE and RELAY.exists() and GAME.exists()), reason="Wine oder Test-Binaries fehlen")


def test_relay_end_to_end(tmp_path):
    env = dict(os.environ, WINEPREFIX=str(tmp_path / "pfx"), WINEDEBUG="-all")
    subprocess.run([WINE, "wineboot", "-i"], env=env, capture_output=True, timeout=180)

    async def run():
        src = ShmSource(ac_maps(30), 30, mode="relay", relay_port=20795)
        await src.start()
        game = subprocess.Popen([WINE, str(GAME), "10"], env=env)
        await asyncio.sleep(1.5)
        relay = subprocess.Popen([WINE, str(RELAY), "--sim", "ac", "--hz", "30", "--port", "20795",
                                  "--quit-after", "3"], env=env, stderr=subprocess.DEVNULL)
        dec = ACDecoder()
        frames = []
        t0 = time.monotonic()
        try:
            async for pkt in src.packets():
                f = dec.safe_feed(pkt)
                if f:
                    frames.append(f)
                if len(frames) > 40 or time.monotonic() - t0 > 12:
                    break
        finally:
            await src.stop()
            relay.terminate()
            game.wait(timeout=30)
        return frames

    frames = asyncio.run(run())
    assert len(frames) > 20
    f = frames[-1]
    assert f.session.track_id == "ks_monza" and f.session.track_length_m == pytest.approx(5758.0)
    assert f.me.position == 5 and f.me.last_lap_s == pytest.approx(107.345)
    assert f.player.speed_ms * 3.6 == pytest.approx(245.0)
    assert f.player.wheels[0].surface_temp() == pytest.approx(88.0)
