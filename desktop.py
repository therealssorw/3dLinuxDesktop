"""Talks to the real desktop: finds installed apps and their logos, launches things."""
import os, re, shlex, shutil, subprocess, hashlib, configparser

HOME = os.path.expanduser("~")
CACHE = os.path.join(os.environ.get("XDG_CACHE_HOME", os.path.join(HOME, ".cache")), "shipdesk", "icons")

APP_DIRS = [
    os.path.join(HOME, ".local/share/applications"),
    os.path.join(HOME, ".local/share/flatpak/exports/share/applications"),
    "/var/lib/flatpak/exports/share/applications",
    "/var/lib/snapd/desktop/applications",
    "/usr/local/share/applications",
    "/usr/share/applications",
]
ICON_ROOTS = [
    os.path.join(HOME, ".local/share/icons"),
    os.path.join(HOME, ".icons"),
    os.path.join(HOME, ".local/share/flatpak/exports/share/icons"),
    "/var/lib/flatpak/exports/share/icons",
    "/usr/local/share/icons",
    "/usr/share/icons",
]
THEMES = ["breeze", "breeze-dark", "hicolor", "Papirus", "Yaru", "oxygen", "Adwaita", "gnome"]
PREFERRED = ["firefox", "dolphin", "konsole", "kate", "blender", "steam", "systemsettings",
             "kdenlive", "krita", "code", "discord", "spotify", "vlc", "gwenview", "okular",
             "libreoffice-writer", "discover", "godot", "obs", "gimp", "inkscape", "thunderbird"]


class App:
    def __init__(self, app_id, name, exe, icon, terminal, path):
        self.id, self.name, self.exe, self.icon, self.terminal, self.path = app_id, name, exe, icon, terminal, path


def list_apps():
    """All launchable apps, de-duplicated by desktop id (earlier dirs win)."""
    apps = {}
    for d in APP_DIRS:
        if not os.path.isdir(d):
            continue
        for root, _dirs, files in os.walk(d):
            for f in files:
                if not f.endswith(".desktop"):
                    continue
                app_id = f[:-8]
                if app_id in apps or app_id.startswith("shipdesk"):
                    continue
                app = _parse(os.path.join(root, f), app_id)
                if app:
                    apps[app_id] = app
    return sorted(apps.values(), key=lambda a: a.name.lower())


def _parse(path, app_id, allow_hidden=False):
    cp = configparser.RawConfigParser(strict=False, interpolation=None)
    cp.optionxform = str
    try:
        cp.read(path, encoding="utf-8")
        s = cp["Desktop Entry"]
    except Exception:
        return None
    if s.get("Type", "Application") != "Application":
        return None
    if not allow_hidden and (s.get("NoDisplay", "false").lower() == "true" or s.get("Hidden", "false").lower() == "true"):
        return None
    exe = s.get("Exec")
    name = s.get("Name")
    if not exe or not name:
        return None
    return App(app_id, name, exe, s.get("Icon", ""), s.get("Terminal", "false").lower() == "true", path)


def default_pins(apps, count):
    """Pick a sensible first set of apps for the stations."""
    by_id = {a.id: a for a in apps}
    picked = []
    for want in PREFERRED:
        for a in apps:
            low = a.id.lower()
            if a.id in picked:
                continue
            if low == want or low.endswith("." + want) or low.startswith(want + "_") or low == "org.kde." + want:
                picked.append(a.id)
                break
        if len(picked) >= count:
            break
    for a in apps:
        if len(picked) >= count:
            break
        if a.id not in picked:
            picked.append(a.id)
    return [p for p in picked if p in by_id][:count]


def launch_app(app):
    cmd = re.sub(r"%[fFuUdDnNickvm]", "", app.exe).replace("%%", "%").strip()
    args = shlex.split(cmd)
    if app.terminal:
        term = shutil.which("konsole") or shutil.which("x-terminal-emulator") or shutil.which("xterm")
        if term:
            args = [term, "-e"] + args
    return _spawn(args)


def open_file(path):
    opener = shutil.which("xdg-open") or shutil.which("kde-open") or shutil.which("kde-open5")
    if not opener:
        raise RuntimeError("xdg-open not found")
    return _spawn([opener, path])


def _spawn(args):
    env = dict(os.environ)
    for k in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        env.pop(k, None)
    return subprocess.Popen(args, cwd=HOME, env=env, start_new_session=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------------------------------------------------------------- icons
_icon_index = None


def _build_index():
    """name -> best file path, preferring big PNGs then SVGs."""
    global _icon_index
    best = {}

    def consider(name, path, score):
        if name not in best or score > best[name][0]:
            best[name] = (score, path)

    for root in ICON_ROOTS:
        if not os.path.isdir(root):
            continue
        themes = [t for t in THEMES if os.path.isdir(os.path.join(root, t))]
        themes += [t for t in sorted(os.listdir(root)) if t not in themes and os.path.isdir(os.path.join(root, t))]
        for ti, theme in enumerate(themes):
            tdir = os.path.join(root, theme)
            for dp, _dn, files in os.walk(tdir):
                rel = dp[len(tdir):].lower()
                if "apps" not in rel:
                    continue
                m = re.search(r"(\d+)(x\d+)?", rel)
                size = int(m.group(1)) if m else (512 if "scalable" in rel else 32)
                for f in files:
                    base, ext = os.path.splitext(f)
                    ext = ext.lower()
                    if ext not in (".png", ".svg", ".svgz"):
                        continue
                    score = min(size, 256) + (40 if ext == ".png" and size >= 64 else 0) - ti * 2
                    if ext != ".png":
                        score = 200 - ti * 2
                    if "symbolic" in base:
                        score -= 300
                    consider(base, os.path.join(dp, f), score)
    for d in ("/usr/share/pixmaps", "/usr/local/share/pixmaps"):
        if os.path.isdir(d):
            for f in os.listdir(d):
                base, ext = os.path.splitext(f)
                if ext.lower() in (".png", ".svg", ".xpm"):
                    consider(base, os.path.join(d, f), 20 if ext.lower() != ".xpm" else -100)
    _icon_index = {k: v[1] for k, v in best.items()}


def icon_png(icon):
    """Return a PNG file path for an icon name/path, or None (caller draws a letter tile)."""
    global _icon_index
    if not icon:
        return None
    src = None
    if os.path.isabs(icon):
        src = icon if os.path.exists(icon) else None
    else:
        if _icon_index is None:
            _build_index()
        src = _icon_index.get(icon) or _icon_index.get(os.path.splitext(icon)[0])
    if not src:
        return None
    ext = os.path.splitext(src)[1].lower()
    if ext == ".png":
        return src
    if ext in (".svg", ".svgz"):
        return _svg_to_png(src)
    return None


def _svg_to_png(src, size=256):
    os.makedirs(CACHE, exist_ok=True)
    key = hashlib.sha1((src + str(os.path.getmtime(src))).encode()).hexdigest()[:16]
    out = os.path.join(CACHE, key + ".png")
    if os.path.exists(out):
        return out
    try:
        import cairosvg
        cairosvg.svg2png(url=src, write_to=out, output_width=size, output_height=size)
        return out
    except Exception:
        pass
    for tool, args in (("rsvg-convert", ["-w", str(size), "-h", str(size), "-o", out, src]),
                       ("ksvgtopng6", [str(size), str(size), src, out]),
                       ("ksvgtopng5", [str(size), str(size), src, out]),
                       ("ksvgtopng", [str(size), str(size), src, out]),
                       ("inkscape", ["-w", str(size), "-h", str(size), "-o", out, src]),
                       ("magick", ["-background", "none", "-resize", f"{size}x{size}", src, out]),
                       ("convert", ["-background", "none", "-resize", f"{size}x{size}", src, out])):
        exe = shutil.which(tool)
        if not exe:
            continue
        try:
            subprocess.run([exe] + args, timeout=20, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if os.path.exists(out) and os.path.getsize(out) > 0:
                return out
        except Exception:
            pass
    return None


# ---------------------------------------------------------------- KDE taskbar
def find_app(app_id):
    """Look an app up by desktop id, including ones hidden from the menu."""
    app_id = app_id[:-8] if app_id.endswith(".desktop") else app_id
    for d in APP_DIRS:
        if not os.path.isdir(d):
            continue
        direct = os.path.join(d, app_id + ".desktop")
        paths = [direct] if os.path.exists(direct) else []
        if not paths:
            for root, _dirs, files in os.walk(d):
                if app_id + ".desktop" in files:
                    paths.append(os.path.join(root, app_id + ".desktop")); break
        for p in paths:
            app = _parse(p, app_id, allow_hidden=True)
            if app:
                return app
    return None


def _query(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=4).stdout.strip()
    except Exception:
        return ""


def _preferred(kind):
    if kind == "browser":
        return _query(["xdg-settings", "get", "default-web-browser"])
    if kind == "filemanager":
        return _query(["xdg-mime", "query", "default", "inode/directory"]) or "org.kde.dolphin.desktop"
    if kind == "mailer":
        return _query(["xdg-mime", "query", "default", "x-scheme-handler/mailto"])
    if kind == "terminal":
        return "org.kde.konsole.desktop"
    return ""


def taskbar_apps():
    """Apps pinned to the KDE panel's task manager, in panel order."""
    conf = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.join(HOME, ".config")),
                        "plasma-org.kde.plasma.desktop-appletsrc")
    try:
        lines = open(conf, encoding="utf-8", errors="replace").read().splitlines()
    except OSError:
        return []
    out, seen = [], set()
    for line in lines:
        if not line.startswith("launchers="):
            continue
        for entry in line[len("launchers="):].split(","):
            entry = entry.strip()
            entry = re.sub(r"^\[[^\]]*\](\\n)?", "", entry)      # drop an activity prefix
            app = None
            if entry.startswith("applications:"):
                app = find_app(entry[len("applications:"):])
            elif entry.startswith("preferred://"):
                target = _preferred(entry[len("preferred://"):])
                app = find_app(target) if target else None
            elif entry.startswith("file://") and entry.endswith(".desktop"):
                path = entry[len("file://"):]
                app = _parse(path, os.path.basename(path)[:-8], allow_hidden=True)
            if app and app.id not in seen:
                seen.add(app.id); out.append(app)
    return out
