#!/usr/bin/env bash
# PIT WALL starten (Linux). Legt beim ersten Mal die Umgebung an.
#   ./start.sh                 → Auto-Erkennung des Spiels, Dashboard im Browser
#   ./start.sh --sim simulator → Testrennen ohne Spiel
#   ./start.sh doctor          → Setup prüfen
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

if ! command -v uv >/dev/null 2>&1; then
  if [ -x "$HOME/.local/bin/uv" ]; then
    export PATH="$HOME/.local/bin:$PATH"
  else
    echo "uv fehlt. Installieren mit:  brew install uv   oder   curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
  fi
fi

EXTRAS="${PITWALL_EXTRAS:-all}"
STAMP=".venv/.pitwall-installed"
if [ ! -d .venv ]; then
  echo "→ Lege Python-Umgebung an (.venv) …"
  uv venv --python 3.12 .venv
fi
if [ ! -f "$STAMP" ] || [ pyproject.toml -nt "$STAMP" ]; then
  echo "→ Installiere Abhängigkeiten ([$EXTRAS]) …"
  uv pip install --python .venv/bin/python -e ".[$EXTRAS]"
  touch "$STAMP"
fi

# Stimme beim ersten Start holen (≈ 60 MB)
VOICE="$(.venv/bin/python - <<'PY'
from pitwall.config import Config
c = Config.load()
print(c.get("voice.piper_voice" if c.language == "de" else "voice.piper_voice_en") if c.get("voice.tts_provider") == "piper" else "")
PY
)"
if [ -n "$VOICE" ]; then
  VDIR="$(.venv/bin/python -c 'from pitwall.config import Config; from pitwall.voice.tts import voices_dir; print(voices_dir(Config.load()))')"
  if [ ! -f "$VDIR/$VOICE.onnx" ]; then
    echo "→ Lade Stimme $VOICE …"
    .venv/bin/pitwall voices download "$VOICE" || echo "  (Download fehlgeschlagen – läuft ohne Piper weiter)"
  fi
fi

# Relay-Wrapper für Proton-Spiele verfügbar machen
mkdir -p "$HOME/.local/bin"
[ -e "$HOME/.local/bin/pitwall-launch" ] || ln -s "$PWD/scripts/pitwall-launch" "$HOME/.local/bin/pitwall-launch" 2>/dev/null || true

case "${1:-}" in
  doctor|dry-run|record|voices|say|devices) exec .venv/bin/pitwall "$@" ;;
  *) exec .venv/bin/pitwall run "$@" ;;
esac
