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


MAPGAME = ROOT / "tests" / "wine" / "fake_map_game.exe"


def _run_relay(tmp_path, game_args, relay_sim, source, decoder, port, want=10, timeout=15.0):
    env = dict(os.environ, WINEPREFIX=str(tmp_path / "pfx"), WINEDEBUG="-all")
    subprocess.run([WINE, "wineboot", "-i"], env=env, capture_output=True, timeout=180)

    async def run():
        await source.start()
        game = subprocess.Popen([WINE, str(MAPGAME), "12", *game_args], env=env)
        await asyncio.sleep(1.5)
        relay = subprocess.Popen([WINE, str(RELAY), "--sim", relay_sim, "--hz", "30", "--port", str(port),
                                  "--quit-after", "3"], env=env, stderr=subprocess.DEVNULL)
        frames = []
        t0 = time.monotonic()
        try:
            async for pkt in source.packets():
                f = decoder.safe_feed(pkt)
                if f:
                    frames.append(f)
                if len(frames) >= want or time.monotonic() - t0 > timeout:
                    break
        finally:
            await source.stop()
            relay.terminate()
            game.kill()
        return frames

    return asyncio.run(run())


@pytest.mark.skipif(not MAPGAME.exists(), reason="fake_map_game.exe fehlt")
def test_relay_iracing_full_size_map(tmp_path):
    from pitwall.adapters.iracing import MAP_NAME, MAP_SIZE, IRacingDecoder, IRacingSource
    from pitwall.adapters.shm import MapSpec
    from tests.fixtures.irsdk_builder import IrsdkMap, race_values

    m = IrsdkMap(size=MAP_SIZE).set(**race_values())
    files = []
    for i in range(3):
        m.set(SessionTime=600.0 + i, LapDistPct=0.5 + i * 0.01)
        p = tmp_path / f"ir{i}.bin"
        p.write_bytes(m.step())
        files.append(str(p))
    src = IRacingSource(ShmSource([MapSpec(MAP_NAME, "irsdk", MAP_SIZE, 30)], 30, mode="relay", relay_port=20796))
    frames = _run_relay(tmp_path, [f"{MAP_NAME}={','.join(files)}"], "iracing", src, IRacingDecoder(), 20796)
    assert len(frames) >= 5
    f = frames[-1]
    assert f.session.track_name == "Circuit de Spa-Francorchamps"
    assert f.me.driver_name == "Kai Vögele" and f.me.position == 2


@pytest.mark.skipif(not MAPGAME.exists(), reason="fake_map_game.exe fehlt")
def test_relay_rfactor2_plugin_maps(tmp_path):
    from pitwall.adapters.rfactor2 import RF2Decoder, RF2Source, rf2_maps
    from tests.fixtures.rf2_builder import (
        scoring_info,
        scoring_map,
        telem_block,
        telemetry,
        telemetry_map,
        vehicle,
    )

    veh = [vehicle(0, 1, 5, 3000.0), vehicle(1, 2, 5, 2900.0, player=True)]
    sc, te = [], []
    for i in range(3):
        p = tmp_path / f"sc{i}.bin"
        p.write_bytes(scoring_map(scoring_info(currentET=900.0 + i), veh, version=10 + i))
        sc.append(str(p))
        p = tmp_path / f"te{i}.bin"
        p.write_bytes(telemetry_map([telem_block(telemetry(0)), telem_block(telemetry(1, elapsedTime=900.0 + i))],
                                    version=20 + i))
        te.append(str(p))
    src = RF2Source(ShmSource(rf2_maps(30), 30, mode="relay", relay_port=20797))
    frames = _run_relay(tmp_path, [f"$rFactor2SMMP_Scoring$={','.join(sc)}",
                                   f"$rFactor2SMMP_Telemetry$={','.join(te)}"], "rf2", src, RF2Decoder(), 20797)
    assert len(frames) >= 5
    f = frames[-1]
    assert f.player_idx == 1 and f.player.fuel_l == 40.0 and f.session.track_length_m == 7004.0
