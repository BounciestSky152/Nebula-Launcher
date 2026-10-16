#!/usr/bin/env bash
# Install Nebula for the current user (no root needed).
#   ./scripts/install.sh            install / upgrade
#   ./scripts/install.sh --webengine  also install the embedded GOG login (bigger download)
#   ./scripts/install.sh --uninstall
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
VENV="$DATA/nebula/venv"
BIN="$HOME/.local/bin"
APP_ID="io.github.nebula_launcher.Nebula"

if [[ "${1:-}" == "--uninstall" ]]; then
    rm -rf "$VENV" "$BIN/nebula" "$DATA/applications/$APP_ID.desktop" \
        "$DATA/icons/hicolor/scalable/apps/$APP_ID.svg"
    echo "Nebula removed. Your library, prefixes and Proton builds are kept in $DATA/nebula and ~/.config/nebula."
    exit 0
fi

PY="${PYTHON:-python3}"
"$PY" - <<'EOF' || { echo "Python 3.10 or newer is required"; exit 1; }
import sys
sys.exit(0 if sys.version_info >= (3, 10) else 1)
EOF

echo "==> Creating virtual environment in $VENV"
"$PY" -m venv "$VENV"
"$VENV/bin/pip" install --upgrade --quiet pip
EXTRA=""
[[ "${1:-}" == "--webengine" ]] && EXTRA="[webengine]"
echo "==> Installing Nebula and its dependencies (PySide6, requests)"
"$VENV/bin/pip" install --quiet "$ROOT$EXTRA"

mkdir -p "$BIN" "$DATA/applications" "$DATA/icons/hicolor/scalable/apps"
ln -sf "$VENV/bin/nebula" "$BIN/nebula"
cp "$ROOT/data/$APP_ID.desktop" "$DATA/applications/"
sed -i "s|^Exec=nebula|Exec=$BIN/nebula|" "$DATA/applications/$APP_ID.desktop"
cp "$ROOT/data/$APP_ID.svg" "$DATA/icons/hicolor/scalable/apps/"
command -v update-desktop-database >/dev/null && update-desktop-database "$DATA/applications" || true

echo
echo "Nebula installed. Start it from your application menu or run: nebula"
echo
echo "Recommended extras (install with your package manager):"
for tool in umu-run gamemoderun mangohud gamescope innoextract winetricks legendary; do
    if command -v "$tool" >/dev/null; then echo "  [x] $tool"; else echo "  [ ] $tool"; fi
done
echo "Proton: Nebula detects Steam's Proton automatically, or download GE-Proton in Settings > Compatibility."
case ":$PATH:" in *":$BIN:"*) ;; *) echo; echo "Note: add $BIN to your PATH to run 'nebula' from a terminal.";; esac
