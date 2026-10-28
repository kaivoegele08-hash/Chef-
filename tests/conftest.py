from __future__ import annotations

import pytest

from pitwall.config import Config


@pytest.fixture()
def cfg(tmp_path, monkeypatch) -> Config:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "cfg"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "data"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return Config.defaults({"simulator": {"hz": 10}})


@pytest.fixture()
def cfg_en(tmp_path, monkeypatch) -> Config:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return Config.defaults({"general": {"language": "en"}, "simulator": {"hz": 10}})
