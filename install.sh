#!/usr/bin/env bash
# ShipDesk installer for KDE Plasma on Ubuntu/Kubuntu.
#   ./install.sh            install (run once; after that ShipDesk updates itself from GitHub on every start)
#   ./install.sh --uinput   also let the controller drive the 2D desktop pointer (asks for sudo)
#   ./install.sh --uninstall
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/shipdesk"
VENV="$DATA/venv"; BIN="$HOME/.local/bin"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
SHORTCUT="Ctrl+Alt+B"

if [[ "${1:-}" == "--uninstall" ]]; then
  rm -rf "$DATA/app" "$VENV" "$DATA/requirements.stamp" "$BIN/shipdesk" "$BIN/shipdesk-toggle" "$BIN/shipdesk-dictate" "$APPS/shipdesk-dictate.desktop" "$APPS/shipdesk.desktop" "$APPS/shipdesk-toggle.desktop" "${XDG_CONFIG_HOME:-$HOME/.config}/autostart/shipdesk.desktop"
  echo "ShipDesk removed (this folder, $SRC, is left alone). Your saved layout is still in $DATA/state.json; delete that folder to forget it."
  echo "If the $SHORTCUT shortcut is still listed, remove it in System Settings > Shortcuts."
  exit 0
fi

command -v python3 >/dev/null || { echo "python3 is missing: sudo apt install python3 python3-venv"; exit 1; }
if ! python3 -c "import venv, ensurepip" 2>/dev/null; then
  echo "Python's venv module is missing. Run:  sudo apt install python3-venv   then run this again."; exit 1
fi

mkdir -p "$DATA" "$BIN" "$APPS"
rm -rf "$DATA/app"   # older installs copied the files here; the repo folder is used directly now

echo "==> Setting up its Python environment (downloads the 3D engine the first time)"
[[ -x "$VENV/bin/python" ]] || python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet panda3d numpy
"$VENV/bin/python" -m pip install --quiet cairosvg python-xlib || echo "   (optional extras failed to install; SVG logos or X11 pointer control may be limited)"
cp "$SRC/requirements.txt" "$DATA/requirements.stamp"

cat > "$BIN/shipdesk" <<LAUNCH
#!/bin/sh
# Starts ShipDesk from $SRC, pulling the newest version from GitHub first.
REPO="$SRC"
if [ -d "\$REPO/.git" ] && [ -z "\$SHIPDESK_NO_UPDATE" ]; then
  GIT_TERMINAL_PROMPT=0 timeout 10 git -C "\$REPO" pull --ff-only -q >/dev/null 2>&1 || true
fi
if ! cmp -s "\$REPO/requirements.txt" "$DATA/requirements.stamp"; then
  "$VENV/bin/python" -m pip install -q -r "\$REPO/requirements.txt" >/dev/null 2>&1 || true
  cp "\$REPO/requirements.txt" "$DATA/requirements.stamp"
fi
exec "$VENV/bin/python" "\$REPO/main.py" "\$@"
LAUNCH
cat > "$BIN/shipdesk-toggle" <<TOGGLE
#!/bin/sh
# Ctrl+Alt+B: flips between the ship and the normal desktop; starts the ship if it is not running.
PIDF="\${XDG_RUNTIME_DIR:-/tmp}/shipdesk.pid"
if [ -f "\$PIDF" ] && kill -0 "\$(cat "\$PIDF")" 2>/dev/null; then
  kill -USR1 "\$(cat "\$PIDF")"
else
  exec "$BIN/shipdesk"
fi
TOGGLE
cat > "$BIN/shipdesk-dictate" <<DICT
#!/bin/sh
# Ctrl+Alt+V: start or stop voice typing (ShipDesk must be running; it can be minimised).
PIDF="\${XDG_RUNTIME_DIR:-/tmp}/shipdesk.pid"
if [ -f "\$PIDF" ] && kill -0 "\$(cat "\$PIDF")" 2>/dev/null; then
  kill -USR2 "\$(cat "\$PIDF")"
else
  notify-send -a ShipDesk "ShipDesk voice typing" "Start ShipDesk first (Ctrl+Alt+B)." 2>/dev/null || true
fi
DICT
chmod +x "$BIN/shipdesk" "$BIN/shipdesk-toggle" "$BIN/shipdesk-dictate"

cat > "$APPS/shipdesk.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=ShipDesk
Comment=Walk around your desktop inside the ship
Exec=$BIN/shipdesk
Icon=applications-games
Categories=Utility;
Terminal=false
DESK
cat > "$APPS/shipdesk-toggle.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=ShipDesk toggle
Comment=Switch between ShipDesk and the normal desktop
Exec=$BIN/shipdesk-toggle
Icon=applications-games
NoDisplay=true
Terminal=false
X-KDE-Shortcuts=$SHORTCUT
DESK
cat > "$APPS/shipdesk-dictate.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=ShipDesk voice typing
Comment=Start or stop voice typing
Exec=$BIN/shipdesk-dictate
Icon=audio-input-microphone
NoDisplay=true
Terminal=false
X-KDE-Shortcuts=Ctrl+Alt+V
DESK
echo "==> Making ShipDesk start when you log in"
mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
cat > "${XDG_CONFIG_HOME:-$HOME/.config}/autostart/shipdesk.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=ShipDesk
Comment=Start in the ship
Exec=$BIN/shipdesk
Icon=applications-games
Terminal=false
X-KDE-autostart-after=panel
DESK
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" 2>/dev/null || true

echo "==> Registering the $SHORTCUT shortcut with KDE"
REGISTERED=no
if command -v kwriteconfig6 >/dev/null; then
  kwriteconfig6 --file kglobalshortcutsrc --group services --group shipdesk-toggle.desktop --key _launch "$SHORTCUT" && REGISTERED=yes
  kwriteconfig6 --file kglobalshortcutsrc --group services --group shipdesk-dictate.desktop --key _launch "Ctrl+Alt+V" || true
  command -v kbuildsycoca6 >/dev/null && kbuildsycoca6 >/dev/null 2>&1 || true
  systemctl --user restart plasma-kglobalaccel.service >/dev/null 2>&1 || true
elif command -v kwriteconfig5 >/dev/null; then
  kwriteconfig5 --file kglobalshortcutsrc --group shipdesk-toggle.desktop --key _k_friendly_name "ShipDesk toggle"
  kwriteconfig5 --file kglobalshortcutsrc --group shipdesk-toggle.desktop --key _launch "$SHORTCUT,none,ShipDesk toggle" && REGISTERED=yes
  kwriteconfig5 --file kglobalshortcutsrc --group shipdesk-dictate.desktop --key _k_friendly_name "ShipDesk voice typing"
  kwriteconfig5 --file kglobalshortcutsrc --group shipdesk-dictate.desktop --key _launch "Ctrl+Alt+V,none,ShipDesk voice typing" || true
  command -v kbuildsycoca5 >/dev/null && kbuildsycoca5 >/dev/null 2>&1 || true
  if command -v kquitapp5 >/dev/null && command -v kglobalaccel5 >/dev/null; then
    kquitapp5 kglobalaccel >/dev/null 2>&1 || true; sleep 1; (kglobalaccel5 >/dev/null 2>&1 &)
  fi
fi
if [[ "$REGISTERED" == yes ]]; then
  echo "   Done. If $SHORTCUT does nothing, log out and back in, or set it by hand:"
else
  echo "   Could not find KDE's config tool. Set it by hand:"
fi
echo "   System Settings > Shortcuts > Add New > Application > 'ShipDesk toggle' > add shortcut $SHORTCUT"
echo "   (same for 'ShipDesk voice typing' with Ctrl+Alt+V)"

if [[ "${1:-}" == "--uinput" ]]; then
  echo "==> Allowing your user to create a virtual pointer (needs sudo)"
  echo 'KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", OPTIONS+="static_node=uinput"' | sudo tee /etc/udev/rules.d/70-shipdesk-uinput.rules >/dev/null
  sudo modprobe uinput || true
  sudo udevadm control --reload-rules && sudo udevadm trigger /dev/uinput || true
  echo "   Done. If the controller still cannot move the pointer, log out and back in."
fi

case ":$PATH:" in *":$BIN:"*) ;; *) echo "Note: $BIN is not on your PATH; start ShipDesk from the app menu or with $BIN/shipdesk";; esac
echo
echo "Installed. Start it with $SHORTCUT, from the app menu (ShipDesk), or by running: shipdesk"
