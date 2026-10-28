#!/usr/bin/env bash
# Baut pitwall-relay.exe (Windows-Helper für Proton) mit mingw.
# Bazzite: in einer distrobox ausführen:  distrobox enter pitwall -- sudo dnf -y install mingw64-gcc
set -euo pipefail
cd "$(dirname "$0")/.."
CC="${CC:-x86_64-w64-mingw32-gcc}"
"$CC" -O2 -s -Wall -Wextra -o pitwall/bridge/pitwall-relay.exe pitwall/bridge/pitwall_relay.c -lws2_32 -lwinmm
echo "gebaut: pitwall/bridge/pitwall-relay.exe ($(stat -c %s pitwall/bridge/pitwall-relay.exe) Bytes)"
