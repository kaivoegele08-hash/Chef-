"""Natürlicherer Sprechstil: Einstiege im Funk, Tempo nach Lage, Atempausen."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from pitwall.engineer.events import Priority
from pitwall.engineer.humanize import Humanizer
from pitwall.voice.tts import PiperTTS, trim_silence


def test_humanizer_adds_varied_openers_but_not_everywhere():
    h = Humanizer("de", seed=3)
    outs = [h.apply("Sprit ist gut.", "Sprit ist gut.", Priority.INFO, t=i * 60.0, category="fuel")[0]
            for i in range(60)]
    with_opener = [o for o in outs if o != "Sprit ist gut."]
    assert 8 < len(with_opener) < 40  # manchmal, nicht immer
    assert all(o.endswith("Sprit ist gut.") for o in outs)
    # Box-Ruf, Ziel und Start bleiben unangetastet
    for key in ("strategy.box_this_lap", "race.finished", "race.lights_out"):
        assert h.apply("Box, Box.", "Box, Box.", Priority.CRITICAL, key=key)[0] == "Box, Box."


def test_humanizer_avoids_double_words_and_uses_driver_name():
    h = Humanizer("de", driver_name="Kai", seed=1)
    seen_name = False
    for i in range(200):
        d, s = h.apply("Gut gemacht, P6.", "Gut gemacht, P sechs.", Priority.INFO, t=i * 60.0, category="race")
        assert not d.startswith("Gut. Gut")
        if d.startswith("Kai, "):
            seen_name = True
            assert d == "Kai, gut gemacht, P6." and s.startswith("Kai, gut gemacht")
    assert seen_name
    # Nomen bleiben groß
    h2 = Humanizer("de", driver_name="Kai", seed=1)
    h2._name_used_t = -1e9
    for i in range(100):
        d, _ = h2.apply("Sprit reicht.", "Sprit reicht.", Priority.CRITICAL, t=i * 60.0, category="fuel")
        if d.startswith("Kai"):
            assert d == "Kai, Sprit reicht."
            break


def test_humanizer_disabled():
    h = Humanizer("en", driver_name="Kai", enabled=False)
    assert h.apply("Fuel is fine.", "Fuel is fine.", Priority.INFO) == ("Fuel is fine.", "Fuel is fine.")


class _FakeVoice:
    def __init__(self) -> None:
        self.configs: list = []

    def synthesize(self, text, syn_config=None):
        self.configs.append(syn_config)
        sr = 22050
        for _sentence in text.split(". "):
            tone = (0.5 * np.sin(np.arange(int(sr * 0.5)) / 5)).astype(np.float32)
            pad = np.zeros(int(sr * 0.3), np.float32)
            yield SimpleNamespace(sample_rate=sr, audio_float_array=np.concatenate([pad, tone, pad]))


def _tts(natural: bool) -> PiperTTS:
    t = PiperTTS("x", __import__("pathlib").Path("."), 0.92, natural=natural, noise_scale=0.72,
                 noise_w_scale=0.9, seed=1)
    t._voice = _FakeVoice()
    return t


def test_piper_style_rate_and_pauses():
    t = _tts(True)
    t.set_style("CRITICAL")
    a_crit, sr = t.synthesize("Box Box. Diese Runde rein.")
    t.set_style("CHATTER")
    a_chat, _ = t.synthesize("Box Box. Diese Runde rein.")
    cfgs = t._voice.configs
    assert cfgs[0].length_scale < cfgs[1].length_scale  # dringend = schneller
    assert cfgs[0].noise_scale == 0.72 and cfgs[0].noise_w_scale == 0.9
    # Piper-Eigenstille getrimmt und durch kürzere, natürliche Pause ersetzt
    plain = _tts(False)
    a_plain, _ = plain.synthesize("Box Box. Diese Runde rein.")
    assert a_crit.size < a_plain.size and a_chat.size < a_plain.size
    assert a_crit.size < a_chat.size  # bei CRITICAL kürzere Pause


def test_trim_silence():
    x = np.concatenate([np.zeros(1000), np.ones(10) * 0.5, np.zeros(1000)]).astype(np.float32)
    y = trim_silence(x, pad=5)
    assert y.size == 20
    assert trim_silence(np.zeros(100, np.float32)).size == 0
