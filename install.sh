#!/bin/bash
# GemLogin-on-Wine fix installer (idempotent).
#
# Installs the fix scripts to ~/.local/share/gemlogin-fix, resizes the
# oversized app icon, and points the desktop entry at launch.sh.
#
# Run AFTER installing the app with the vendor's NSIS installer under Wine.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.local/share/gemlogin-fix"

# optional: install.sh --browser 150 152 | --browser all
#  -> pre-download + extract those GemLogin browser builds after the fix
BROWSER_TARGETS=()
if [ $# -gt 0 ]; then
  if [ "$1" != "--browser" ]; then
    echo "install: usage: $0 [--browser VERSION ... | --browser all]"
    exit 1
  fi
  shift
  BROWSER_TARGETS=("$@")
  [ ${#BROWSER_TARGETS[@]} -gt 0 ] || { echo "install: --browser needs at least one version"; exit 1; }
fi
mkdir -p "$DEST"

# 1) install the scripts
cp "$SRC"/scripts/gemfix_patch.js "$SRC"/scripts/inject.py \
   "$SRC"/scripts/launch.sh "$SRC"/scripts/fix_icon.py \
   "$SRC"/scripts/verify.sh "$SRC"/scripts/prepare_browser.py "$DEST"/
chmod +x "$DEST"/*.py "$DEST"/*.sh

# 2) python deps
python3 -c "import websocket" 2>/dev/null \
  || echo "install: WARNING - pip install websocket-client  (needed by inject.py)"
python3 -c "import PIL" 2>/dev/null \
  || echo "install: WARNING - pip install Pillow             (needed by fix_icon.py)"

# 3) icon fix (crash on X11: 3000x3000 _NET_WM_ICON)
python3 "$DEST/fix_icon.py"

# 4) desktop entry
DESK="$HOME/Desktop/GemLogin.desktop"
if [ -f "$DESK" ]; then
  if ! grep -q "gemlogin-fix/launch.sh" "$DESK"; then
    [ -f "$DESK.bak" ] || cp "$DESK" "$DESK.bak"
    sed -i "s|^Exec=.*|Exec=bash $DEST/launch.sh|" "$DESK"
    echo "install: updated $DESK (backup: $DESK.bak)"
  else
    echo "install: desktop entry already patched"
  fi
else
  cat > "$DESK" <<EOF
[Desktop Entry]
Name=GemLogin
Exec=bash $DEST/launch.sh
Type=Application
StartupWMClass=gemlogin.exe
EOF
  mkdir -p "$HOME/.local/share/applications"
  cp "$DESK" "$HOME/.local/share/applications/GemLogin.desktop"
  update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
  echo "install: created $DESK"
fi

# 5) optional: pre-provision browser builds (the app's own downloader is
#    size-blind and times out on the ~200 MB zips on slower links)
if [ ${#BROWSER_TARGETS[@]} -gt 0 ]; then
  echo "install: pre-downloading browser build(s): ${BROWSER_TARGETS[*]}"
  python3 "$DEST/prepare_browser.py" "${BROWSER_TARGETS[@]}"
fi

echo "install: done."
echo "install: launch GemLogin from the desktop icon, or run:"
echo "  bash $DEST/launch.sh"
echo "install: pre-fetch a browser build on demand (skips the app's"
echo "install:        size-blind downloader):"
echo "  python3 $DEST/prepare_browser.py <VERSION> [VERSION ... | all]"
