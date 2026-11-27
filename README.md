# ShipDesk

A walkable 3D desktop for KDE Plasma, set inside your ship model.

## Install

    git clone https://github.com/therealssorw/3dLinuxDesktop.git ~/3dLinuxDesktop && ~/3dLinuxDesktop/install.sh

Add `--uinput` to the install command to let the PS5 controller move the normal 2D pointer
(asks for sudo). Needs `git` and `python3-venv` (`sudo apt install git python3-venv`).

**Updates are automatic:** every time ShipDesk starts it pulls the newest version from this
repo first (it just starts what you have if you are offline). Keep the `~/3dLinuxDesktop`
folder; ShipDesk runs from it.

Start it with **Ctrl+Alt+B**, from the app menu (ShipDesk), or by running `shipdesk`.
Press **Ctrl+Alt+B** again to go back to the normal desktop; the ship keeps running minimised,
so the next press brings you straight back to where you stood.

If Ctrl+Alt+B does nothing after installing, log out and back in, or set it by hand:
System Settings > Shortcuts > Add New > Application > "ShipDesk toggle".

ShipDesk opens by itself when you log in. To stop that, set `"start_on_login": false` in
`~/.config/shipdesk/config.json` and start it once more.

While ShipDesk is running, the KDE panel is switched to auto-hide so the 2D desktop is just
your window; move the pointer to the panel's edge to bring it up. Quitting ShipDesk (F10) puts
the panel back. `shipdesk --restore-panel` does the same by hand, and
`"hide_panel_on_desktop": false` in the config turns this off.

If something goes wrong, the log is `~/.local/share/shipdesk/shipdesk.log`.

## What is where

- **Library** (the racks beside the core): the folder you are in. Loose files sit on the first
  rack; every sub-folder gets its own rack with a name sign. Each file is a book, coloured by
  type (documents blue, images green, video purple, audio orange, code teal, archives brown,
  3D pink). Look at a sign and press E to walk into that folder. The kiosk in front has
  UP, HOME and NEXT (more racks when a folder has over 20 sections).
- **App stations** (along the big window): the apps pinned to your KDE taskbar, in the same
  order, as floating logos (up to ten). E opens one. Pin or unpin apps on the taskbar and the
  stations follow the next time ShipDesk starts. To pick them by hand instead, set
  `"stations": "custom"` in the config file; then F on a station swaps its app.
- **Core consoles** (around the glowing core) or Tab: the full list of installed apps.
- Opening a file or app drops you to the normal 2D desktop with that window in front.

## Controls

| Action | Keyboard / mouse | PS5 controller |
|---|---|---|
| Walk / look | WASD / mouse | left stick / right stick |
| Sprint | hold Shift | hold L2, or click L3 |
| Jump | Space | R1 |
| Open what you look at | E | Cross |
| Pick up or throw a book | F | Square |
| Put a thrown book back on its shelf | R | |
| Up one folder (in the library) | Q | Circle |
| All apps | Tab | Triangle |
| Normal 2D desktop | Esc or Ctrl+Alt+B | Options |
| Controls help / quit | H / F10 | |

On the 2D desktop the controller becomes a mouse: left stick pointer (hold R2 for slow),
Cross click, Circle right click, right stick scroll, D-pad arrows, Square Enter, Triangle Esc,
R1 Tab, **PS button back to the ship**.

## Voice typing

On the 2D desktop, click where you want to type, then **hold L1 and speak**; let go and the
words are typed. From the keyboard, **Ctrl+Alt+V** starts and Ctrl+Alt+V again stops. ShipDesk
has to be running (minimised is fine).

- It listens on your default microphone. To speak into the controller, pick the DualSense
  microphone under System Settings > Sound.
- Speech is recognised on your own computer. The first use downloads the speech engine and
  model (a few hundred MB), so the first sentence takes a few minutes.
- It types by pasting (Ctrl+V). On Wayland that needs `./install.sh --uinput`; without it the
  text is copied and you press Ctrl+V yourself. In a terminal, paste is Ctrl+Shift+V.
- `voice_model` (tiny / base / small) and `voice_language` are in the config file.

Books you throw stay where they land, and your position, folder and stations are remembered.

## Settings

`~/.config/shipdesk/config.json` (created on first run): start folder, fullscreen, mouse and
stick speed, invert Y, walk speed, volume, hidden files, and the controller button mapping.
If Square and Triangle come out swapped on your controller, swap `face_x` and `face_y` there.

## Changing the ship

The ship was converted from `big_ship.blend` (bevels dropped, plant trays removed from the
racks so they can hold books). To re-convert after editing the model:
`pip install bpy numpy` then `python3 tools/export.py -- big_ship.blend assets`.
