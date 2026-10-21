#!/usr/bin/env bash
# Install Super Nova for the current user (no root needed).
#   ./scripts/install.sh            install / upgrade
#   ./scripts/install.sh --webengine  also install the embedded GOG login (bigger download)
#   ./scripts/install.sh --uninstall
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
VENV="$DATA/supernova/venv"
BIN="$HOME/.local/bin"
APP_ID="io.github.super_nova_launcher.SuperNova"
OLD_ID="io.github.nebula_launcher.Nebula"  # the app was called Nebula before

# move a Nebula-era folder over, leaving a link so paths saved before keep working
if [[ -d "$DATA/nebula" && ! -L "$DATA/nebula" && ! -e "$DATA/supernova" ]]; then
    mv "$DATA/nebula" "$DATA/supernova" && ln -s "$DATA/supernova" "$DATA/nebula"
fi

if [[ "${1:-}" == "--uninstall" ]]; then
    rm -rf "$VENV" "$BIN/supernova" "$BIN/nebula" "$DATA/applications/$APP_ID.desktop" \
        "$DATA/icons/hicolor/scalable/apps/$APP_ID.svg" "$DATA/applications/$OLD_ID.desktop" \
        "$DATA/icons/hicolor/scalable/apps/$OLD_ID.svg"
    echo "Super Nova removed. Your library, prefixes and Proton builds are kept in $DATA/supernova and" \
        "~/.config/supernova."
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
echo "==> Installing Super Nova and its dependencies (PySide6, requests)"
"$VENV/bin/pip" install --quiet "$ROOT$EXTRA"

# remember what was installed so the built-in updater only acts on real changes
SHA=""
git -C "$ROOT" rev-parse HEAD >/dev/null 2>&1 && SHA="$(git -C "$ROOT" rev-parse HEAD)"
mkdir -p "$DATA/supernova"
"$VENV/bin/python" - "$DATA/supernova/update-state.json" "$ROOT/pyproject.toml" "$SHA" <<'EOF'
import hashlib, json, pathlib, sys
state_path, pyproject, sha = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]
state = json.loads(state_path.read_text()) if state_path.exists() else {}
data = pyproject.read_bytes()
state["dependency_blob"] = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
if sha:
    state["installed_sha"] = sha
state.pop("verified_sha", None)
state_path.write_text(json.dumps(state, indent=2))
EOF

mkdir -p "$BIN" "$DATA/applications" "$DATA/icons/hicolor/scalable/apps"
ln -sf "$VENV/bin/supernova" "$BIN/supernova"
ln -sf "$VENV/bin/supernova" "$BIN/nebula"  # the old command keeps working
rm -f "$DATA/applications/$OLD_ID.desktop" "$DATA/icons/hicolor/scalable/apps/$OLD_ID.svg"
cp "$ROOT/data/$APP_ID.desktop" "$DATA/applications/"
sed -i "s|^Exec=supernova|Exec=$BIN/supernova|" "$DATA/applications/$APP_ID.desktop"
cp "$ROOT/data/$APP_ID.svg" "$DATA/icons/hicolor/scalable/apps/"
command -v update-desktop-database >/dev/null && update-desktop-database "$DATA/applications" || true

echo
echo "Super Nova installed. Start it from your application menu or run: supernova"
echo
echo "Recommended extras (install with your package manager):"
for tool in umu-run gamemoderun mangohud gamescope innoextract winetricks legendary; do
    if command -v "$tool" >/dev/null; then echo "  [x] $tool"; else echo "  [ ] $tool"; fi
done
echo "Proton: Super Nova detects Steam's Proton automatically, or download GE-Proton in Settings > Compatibility."
case ":$PATH:" in *":$BIN:"*) ;; *) echo; echo "Note: add $BIN to your PATH to run 'supernova' from a terminal.";; esac
