#!/usr/bin/env bash
# PIT WALL einmalig installieren (Linux, z. B. Bazzite). Danach reicht im Terminal:  pitwall
#
#   bash install.sh                (aus dem geklonten Ordner)
#   curl -fsSL https://raw.githubusercontent.com/kaivoegele08-hash/Chef-/claude/pitwall-race-engineer/install.sh | bash
#
# Installiert nichts ins System: uv, PIT WALL und der Befehl landen in ~/.local und ~/pitwall.
set -euo pipefail

REPO="${PITWALL_REPO:-https://github.com/kaivoegele08-hash/Chef-}"
BRANCH="${PITWALL_BRANCH:-claude/pitwall-race-engineer}"
DIR="${PITWALL_DIR:-$HOME/pitwall}"
BIN="$HOME/.local/bin"
mkdir -p "$BIN"
export PATH="$BIN:$PATH"

# 1) uv
if ! command -v uv >/dev/null 2>&1; then
  echo "→ Installiere uv …"
  if command -v brew >/dev/null 2>&1; then brew install uv; else curl -LsSf https://astral.sh/uv/install.sh | sh; fi
fi

# 2) Code: läuft das Skript aus einem Checkout, diesen nutzen – sonst klonen/aktualisieren
SELF="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -n "$SELF" ] && [ -f "$SELF/start.sh" ] && [ -f "$SELF/pyproject.toml" ]; then
  DIR="$SELF"
elif [ -d "$DIR/.git" ]; then
  echo "→ Aktualisiere $DIR …"
  git -C "$DIR" pull --ff-only || echo "  (Aktualisieren übersprungen)"
else
  echo "→ Lade PIT WALL nach $DIR …"
  git clone -b "$BRANCH" "$REPO" "$DIR"
fi

# 3) Befehl "pitwall" überall verfügbar machen
cat > "$BIN/pitwall" <<WRAP
#!/usr/bin/env bash
# PIT WALL – erzeugt von install.sh
#   pitwall              → starten (Spiel wird automatisch erkannt)
#   pitwall test         → Testrennen ohne Spiel
#   pitwall doctor       → Setup prüfen
#   pitwall update       → neueste Version holen
case "\${1:-}" in
  update) exec git -C "$DIR" pull --ff-only ;;
  test)   shift; exec "$DIR/start.sh" --sim simulator "\$@" ;;
  *)      exec "$DIR/start.sh" "\$@" ;;
esac
WRAP
chmod +x "$BIN/pitwall"
ln -sf "$DIR/scripts/pitwall-launch" "$BIN/pitwall-launch"

# 4) Eintrag im Anwendungsmenü (KDE/GNOME)
APPS="$HOME/.local/share/applications"
mkdir -p "$APPS"
cat > "$APPS/pitwall.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=PIT WALL
Comment=KI-Renningenieur
Exec=$BIN/pitwall
Icon=applications-games
Terminal=true
Categories=Game;
DESK

# 5) Erste Einrichtung (Python-Umgebung, Pakete, Stimme) gleich jetzt erledigen
"$DIR/start.sh" doctor --seconds 0 || true

echo
echo "✓ Fertig. Ab jetzt im Terminal:"
echo "    pitwall          starten (Spiel wird automatisch erkannt)"
echo "    pitwall test     Testrennen ohne Spiel"
echo "    pitwall doctor   Setup prüfen"
echo "    pitwall update   neueste Version holen"
echo "  oder im Startmenü: PIT WALL"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "  Hinweis: Terminal einmal neu öffnen, damit der Befehl gefunden wird." ;; esac
