#!/usr/bin/env python3
"""ShipDesk - a walkable 3D desktop set inside the ship.

Files are books in the library racks, folders are rack sections, apps are stations
along the big window. Opening anything drops you to the normal 2D desktop.
"""
import os, sys, json, math, time, signal, hashlib, argparse, colorsys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import numpy as np
from panda3d.core import (loadPrcFileData, WindowProperties, GeomVertexFormat, GeomVertexData, Geom,
                          GeomTriangles, GeomPoints, GeomNode, NodePath, AmbientLight, DirectionalLight,
                          Vec3, Point3, Vec4, TextNode, CardMaker, TransparencyAttrib, PNMImage, Texture,
                          Filename, BitMask32, InputDevice, GamepadButton, KeyboardButton, ClockObject,
                          AntialiasAttrib, TransformState, SamplerState)

ap = argparse.ArgumentParser()
ap.add_argument("--windowed", action="store_true", help="run in a window instead of fullscreen")
ap.add_argument("--selftest", metavar="DIR", help="render test screenshots into DIR and exit")
ARGS = ap.parse_args()

loadPrcFileData("", """
window-title ShipDesk
sync-video 1
framebuffer-multisample 1
multisamples 4
textures-power-2 none
default-near 0.08
default-far 5000
audio-library-name p3openal_audio
text-minfilter linear_mipmap_linear
text-pixels-per-unit 48
notify-level-device warning
""")

from direct.showbase.ShowBase import ShowBase
from direct.gui.OnscreenText import OnscreenText
from panda3d.bullet import (BulletWorld, BulletTriangleMesh, BulletTriangleMeshShape, BulletRigidBodyNode,
                            BulletPlaneShape, BulletCapsuleShape, BulletCharacterControllerNode,
                            BulletBoxShape, ZUp)
import desktop, padmouse, voice

HOME = os.path.expanduser("~")
CONF_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.join(HOME, ".config")), "shipdesk")
DATA_DIR = os.path.join(os.environ.get("XDG_DATA_HOME", os.path.join(HOME, ".local/share")), "shipdesk")
PID_FILE = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "shipdesk.pid")

DEFAULT_CONF = {
    "library_root": "~",          # folder the library opens on first run / HOME button
    "fullscreen": True,
    "mouse_sensitivity": 0.12,
    "stick_look_speed": 150.0,    # degrees per second
    "invert_y": False,
    "walk_speed": 4.2,
    "run_speed": 9.5,             # sprint speed
    "start_on_login": True,
    "stations": "taskbar",        # "taskbar" = the apps pinned to your KDE panel; "custom" = pick each one with F
    "voice_model": "base",        # speech model: tiny, base, small (bigger = slower, more accurate)
    "voice_language": "",         # "" = work it out; or a code like "en" or "ja"       # open ShipDesk automatically when you log in to KDE
    "minimize_on_open": True,     # drop to the normal desktop when you open something
    "pointer_speed": 1100.0,      # controller pointer speed on the 2D desktop, px/s
    "volume": 0.8,
    "show_hidden_files": False,
    # controller buttons (Panda3D names). On a DualSense: face_a=Cross, face_b=Circle,
    # face_x=Triangle, face_y=Square. Swap face_x/face_y here if yours come out reversed.
    "pad": {"interact": "face_a", "back": "face_b", "grab": "face_y", "apps": "face_x",
            "jump": "rshoulder", "run": "lstick", "desktop": "start", "ship": "guide",
            "voice": "lshoulder"},
}

CAT_COLORS = {  # book colours by kind of file
    "doc": (0.20, 0.38, 0.78), "image": (0.20, 0.62, 0.34), "video": (0.55, 0.28, 0.75),
    "audio": (0.90, 0.52, 0.12), "code": (0.10, 0.60, 0.62), "archive": (0.55, 0.38, 0.22),
    "3d": (0.85, 0.30, 0.42), "other": (0.50, 0.52, 0.56),
}
EXT_CAT = {}
for cat, exts in {
    "doc": "txt md pdf doc docx odt rtf tex epub xls xlsx ods csv ppt pptx odp",
    "image": "png jpg jpeg gif webp bmp svg tif tiff psd kra xcf heic ico",
    "video": "mp4 mkv mov avi webm m4v kdenlive mlt",
    "audio": "mp3 wav flac ogg m4a opus aac mid",
    "code": "py js ts c h cpp hpp rs go java sh html css json yaml yml toml xml gd lua rb php sql",
    "archive": "zip tar gz xz bz2 7z rar deb iso appimage",
    "3d": "blend blend1 obj fbx glb gltf stl dae ply usd",
}.items():
    for e in exts.split():
        EXT_CAT[e] = cat

BOOKS_PER_SHELF = 15
SHELF_ORDER = (1, 2, 0)          # fill eye level first, then top, then bottom
LIB_ZONE = (3.0, 16.5, 6.5, 22.5)  # x0, x1, y0, y1 of the library floor


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(default, dict):
            merged = dict(default); merged.update(data)
            return merged
        return data
    except Exception:
        return dict(default) if isinstance(default, dict) else default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


def tri_node(arr, name):
    """Build one static mesh from an (n, 9) float array of position, normal, colour."""
    n = len(arr)
    packed = np.zeros(n, dtype=[("v", "<f4", 3), ("n", "<f4", 3), ("c", "u1", 4)])
    packed["v"] = arr[:, 0:3]; packed["n"] = arr[:, 3:6]
    packed["c"][:, :3] = np.clip(arr[:, 6:9] * 255, 0, 255).astype(np.uint8); packed["c"][:, 3] = 255
    vdata = GeomVertexData(name, GeomVertexFormat.getV3n3c4(), Geom.UHStatic)
    vdata.uncleanSetNumRows(n)
    vdata.modifyArray(0).modifyHandle().copyDataFrom(packed.tobytes())
    prim = GeomTriangles(Geom.UHStatic); prim.addNextVertices(n)
    geom = Geom(vdata); geom.addPrimitive(prim)
    node = GeomNode(name); node.addGeom(geom)
    return node


def box_node():
    """Unit cube centred on the origin, with flat normals."""
    faces = []
    for axis in range(3):
        for sign in (-1, 1):
            nrm = [0, 0, 0]; nrm[axis] = sign
            a, b = (axis + 1) % 3, (axis + 2) % 3
            quad = []
            for da, db in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                p = [0, 0, 0]; p[axis] = 0.5 * sign; p[a] = 0.5 * da; p[b] = 0.5 * db
                quad.append(p)
            if sign < 0:
                quad.reverse()
            for i in (0, 1, 2, 0, 2, 3):
                faces.append(quad[i] + nrm + [1, 1, 1])
    return tri_node(np.array(faces, dtype=np.float32), "box")


class ShipDesk(ShowBase):
    def __init__(self):
        self.conf = load_json(os.path.join(CONF_DIR, "config.json"), DEFAULT_CONF)
        padconf = dict(DEFAULT_CONF["pad"]); padconf.update(self.conf.get("pad") or {}); self.conf["pad"] = padconf
        if not os.path.exists(os.path.join(CONF_DIR, "config.json")):
            try: save_json(os.path.join(CONF_DIR, "config.json"), self.conf)
            except Exception: pass
        self.state = load_json(os.path.join(DATA_DIR, "state.json"),
                               {"pos": [0.0, -6.0, 1.0], "h": 0.0, "p": 0.0, "lib_path": None, "page": 0,
                                "moved": {}, "pins": []})
        self.selftest = bool(ARGS.selftest)

        ShowBase.__init__(self, windowType="none")
        if not self.pipe:
            self.makeDefaultPipe()
        props = WindowProperties()
        props.setTitle("ShipDesk")
        if self.selftest:
            props.setSize(1280, 720)
        elif ARGS.windowed or not self.conf["fullscreen"]:
            props.setSize(1600, 900)
        else:
            props.setSize(self.pipe.getDisplayWidth(), self.pipe.getDisplayHeight())
            props.setFullscreen(True)
        self.openDefaultWindow(props=props)
        if not self.win:
            raise SystemExit("ShipDesk could not open a window")
        self.disableMouse()
        self.setBackgroundColor(0.004, 0.006, 0.015)
        self.render.setAntialias(AntialiasAttrib.MMultisample)
        self.camLens.setFov(78); self.fov = 78.0
        self.camLens.setNearFar(0.08, 5000)

        self.mode = "ship"            # ship | menu | desktop
        self.started = time.time()
        self.toggle_flag = False
        self.focus = None
        self.held = None
        self.items = []               # things you can look at and use
        self.lib_nodes = []
        self.pods = []
        self.toast_until = 0
        self.mouse_backend = None
        self.mouse_errors = []
        self.pad = None
        self.pad_prev = {}
        self.key_prev = {}
        self.step_acc = 0.0
        self.step_i = 0
        self.ptr_frac = [0.0, 0.0]
        self.scroll_t = 0.0
        self.last_save = time.time()
        self.desktop_since = 0

        self.font = self.find_font()
        self.build_world()
        self.build_physics()
        self.build_player()
        self.build_hud()
        self.load_sounds()
        self.apps = desktop.list_apps()
        self.apps_by_id = {a.id: a for a in self.apps}
        self.build_pods()
        self.build_consoles()
        self.build_library_fixtures()
        if not self.state.get("lib_path") or not os.path.isdir(self.state["lib_path"]):
            self.state["lib_path"] = os.path.expanduser(self.conf["library_root"])
        self.restore_moved_books()
        self.populate_library(sound=False)
        self.build_menu()

        self.accept("connect-device", self.on_device)
        self.accept("disconnect-device", self.on_device_gone)
        for dev in self.devices.getDevices(InputDevice.DeviceClass.gamepad):
            self.on_device(dev)
        self.accept("window-event", self.on_window)
        self.accept("f10", self.quit)
        self.buttonThrowers[0].node().setKeystrokeEvent("keystroke")
        self.accept("keystroke", self.on_keystroke)
        signal.signal(signal.SIGUSR1, lambda *_: setattr(self, "toggle_flag", True))
        self.voice = voice.Voice(self.conf)
        self.voice_flag = False
        self.voice_held = False
        signal.signal(signal.SIGUSR2, lambda *_: setattr(self, "voice_flag", True))
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "quit_flag", True))
        self.quit_flag = False
        try:
            with open(PID_FILE, "w") as f:
                f.write(str(os.getpid()))
        except Exception:
            pass

        self.sync_autostart()
        self.grab_mouse(True)
        self.taskMgr.add(self.update, "update")
        self.toast("Welcome aboard.  H shows the controls.", 6)
        if self.selftest:
            self.run_selftest()

    def sync_autostart(self):
        """Keep the 'start when I log in' entry in step with the start_on_login setting."""
        if self.selftest:
            return
        path = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.join(HOME, ".config")), "autostart", "shipdesk.desktop")
        launcher = os.path.join(HOME, ".local", "bin", "shipdesk")
        try:
            if self.conf.get("start_on_login", True) and os.path.exists(launcher):
                want = ("[Desktop Entry]\nType=Application\nName=ShipDesk\nComment=Start in the ship\n"
                        f"Exec={launcher}\nIcon=applications-games\nTerminal=false\nX-KDE-autostart-after=panel\n")
                have = open(path, encoding="utf-8").read() if os.path.exists(path) else None
                if have != want:
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(want)
            elif not self.conf.get("start_on_login", True) and os.path.exists(path):
                os.remove(path)
        except OSError as e:
            print("ShipDesk: could not update the autostart entry:", e, flush=True)

    # ------------------------------------------------------------ world
    def find_font(self):
        for p in ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                  "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
                  "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Regular.otf",
                  "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                  "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"):
            if os.path.exists(p):
                try:
                    f = self.loader.loadFont(Filename.fromOsSpecific(p).getFullpath())
                    if f and f.isValid():
                        f.setPixelsPerUnit(64)
                        return f
                except Exception:
                    pass
        return TextNode.getDefaultFont()

    def build_world(self):
        data = np.load(os.path.join(HERE, "assets", "ship_geom.npz"))
        self.markers = json.load(open(os.path.join(HERE, "assets", "markers.json")))
        self.col_tris = data["col"]
        lit = self.render.attachNewNode(tri_node(data["lit"], "ship"))
        glow = self.render.attachNewNode(tri_node(data["emi"], "ship_glow"))
        glow.setLightOff()
        # simple flat lighting: no shadows, just enough shading to read the shapes
        amb = AmbientLight("amb"); amb.setColor(Vec4(0.66, 0.68, 0.74, 1))
        sun = DirectionalLight("sun"); sun.setColor(Vec4(0.55, 0.53, 0.5, 1))
        fill = DirectionalLight("fill"); fill.setColor(Vec4(0.20, 0.24, 0.30, 1))
        sun_np = self.render.attachNewNode(sun); sun_np.setHpr(-35, -55, 0)
        fill_np = self.render.attachNewNode(fill); fill_np.setHpr(150, -25, 0)
        self.lit_root = self.render.attachNewNode("lit")
        for n in (lit, self.lit_root):
            n.setLight(self.render.attachNewNode(amb)); n.setLight(sun_np); n.setLight(fill_np)
        self.unlit_root = self.render.attachNewNode("unlit"); self.unlit_root.setLightOff()
        self.box = NodePath(box_node())
        # stars outside the window
        rng = np.random.default_rng(3)
        d = rng.normal(size=(1400, 3)); d /= np.linalg.norm(d, axis=1, keepdims=True)
        d = d[d[:, 1] > 0.1] * 3500
        vdata = GeomVertexData("stars", GeomVertexFormat.getV3c4(), Geom.UHStatic)
        packed = np.zeros(len(d), dtype=[("v", "<f4", 3), ("c", "u1", 4)])
        packed["v"] = d; b = rng.integers(120, 255, len(d)); packed["c"] = np.stack([b, b, np.minimum(255, b + 20), b * 0 + 255], 1)
        vdata.uncleanSetNumRows(len(d)); vdata.modifyArray(0).modifyHandle().copyDataFrom(packed.tobytes())
        pts = GeomPoints(Geom.UHStatic); pts.addNextVertices(len(d))
        g = Geom(vdata); g.addPrimitive(pts); gn = GeomNode("stars"); gn.addGeom(g)
        stars = self.unlit_root.attachNewNode(gn); stars.setRenderModeThickness(2)

    def build_physics(self):
        self.world = BulletWorld(); self.world.setGravity(Vec3(0, 0, -14))
        mesh = BulletTriangleMesh()
        tris = self.col_tris.reshape(-1, 3, 3)
        # low platforms get invisible ramps so you can walk straight onto them
        ramps = []
        for x0, y0, x1, y1, top in ((-15.0, 20.0, -4.0, 28.0, 0.3), (-3.2, 0.8, 3.2, 7.2, 0.3)):
            m = 0.55
            inner = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
            outer = [(x0 - m, y0 - m), (x1 + m, y0 - m), (x1 + m, y1 + m), (x0 - m, y1 + m)]
            for i in range(4):
                j = (i + 1) % 4
                a, b = (*outer[i], 0.0), (*outer[j], 0.0)
                c, d = (*inner[j], top), (*inner[i], top)
                ramps += [[a, b, c], [a, c, d]]
            ramps += [[(x0, y0, top), (x1, y0, top), (x1, y1, top)], [(x0, y0, top), (x1, y1, top), (x0, y1, top)]]
        tris = np.concatenate([tris, np.array(ramps, dtype=np.float32)])
        arr = np.concatenate([tris, np.zeros_like(tris), np.ones_like(tris)], axis=2).reshape(-1, 9)
        mesh.addGeom(tri_node(arr, "col").getGeom(0))
        body = BulletRigidBodyNode("ship"); body.addShape(BulletTriangleMeshShape(mesh, dynamic=False))
        self.render.attachNewNode(body); self.world.attachRigidBody(body)
        for nrm, dist in ((Vec3(0, 0, 1), 0.0), (Vec3(1, 0, 0), -19.9), (Vec3(-1, 0, 0), -19.9),
                          (Vec3(0, 1, 0), -29.6), (Vec3(0, -1, 0), -29.25)):
            wall = BulletRigidBodyNode("bound"); wall.addShape(BulletPlaneShape(nrm, dist))
            self.render.attachNewNode(wall); self.world.attachRigidBody(wall)

    def build_player(self):
        shape = BulletCapsuleShape(0.3, 1.1, ZUp)
        self.char = BulletCharacterControllerNode(shape, 0.42, "player")
        self.char_np = self.render.attachNewNode(self.char)
        x, y, z = self.state["pos"]
        self.char_np.setPos(x, y, max(z, 0.9)); self.char_np.setCollideMask(BitMask32.allOn())
        self.world.attachCharacter(self.char)
        self.char.setJumpSpeed(5.5); self.char.setMaxJumpHeight(1.2)
        self.heading, self.pitch = float(self.state["h"]), float(self.state["p"])
        self.camera.reparentTo(self.char_np); self.camera.setPos(0, 0, 0.72)
        self.hand = self.camera.attachNewNode("hand"); self.hand.setPos(0.34, 0.85, -0.28); self.hand.setHpr(20, 10, 0)

    def text3d(self, text, scale, color=(0.6, 0.95, 1, 1), card=(0.02, 0.05, 0.08, 0.88), parent=None, billboard=True):
        tn = TextNode("t"); tn.setFont(self.font); tn.setText(text); tn.setAlign(TextNode.ACenter)
        tn.setTextColor(*color)
        if card:
            tn.setCardColor(*card); tn.setCardAsMargin(0.35, 0.35, 0.18, 0.18); tn.setCardDecal(True)
        np_ = (parent or self.unlit_root).attachNewNode(tn); np_.setScale(scale); np_.setTwoSided(True)
        np_.setTransparency(TransparencyAttrib.MAlpha)
        if billboard:
            np_.setBillboardAxis()
        return np_

    def add_item(self, kind, pos, radius, label, hint, data=None, node=None, reach=3.6):
        it = {"kind": kind, "pos": Point3(*pos), "r": radius, "label": label, "hint": hint,
              "data": data, "node": node, "reach": reach}
        self.items.append(it)
        return it

    # ------------------------------------------------------------ app stations
    def build_pods(self):
        slots = self.markers["winscreens"]
        pins = [p for p in self.state.get("pins", []) if p is None or p in self.apps_by_id]
        self.taskbar_mode = False
        if self.conf.get("stations", "taskbar") == "taskbar":
            bar = desktop.taskbar_apps()
            if bar:
                self.taskbar_mode = True
                for a in bar:
                    self.apps_by_id.setdefault(a.id, a)
                ids = [a.id for a in bar[:len(slots)]]
                lead = (len(slots) - len(ids)) // 2          # keep a short row centred on the window
                pins = [None] * lead + ids + [None] * (len(slots) - len(ids) - lead)
                self.state["pins"] = pins
        if len(pins) != len(slots):
            pins = desktop.default_pins(self.apps, len(slots))
            pins += [None] * (len(slots) - len(pins))
            self.state["pins"] = pins
        for i, (mn, mx) in enumerate(slots):
            x = (mn[0] + mx[0]) / 2
            root = self.unlit_root.attachNewNode(f"pod{i}"); root.setPos(x, 29.05, 1.62)
            base = self.box.copyTo(self.unlit_root); base.setPos(x, 29.28, 0.93); base.setScale(1.0, 0.36, 0.05)
            pod = {"i": i, "root": root, "base": base, "phase": i * 0.7, "card": None, "label": None, "item": None}
            pod["item"] = self.add_item("app", (x, 29.05, 1.62), 0.75, "", "", data=pod, reach=5.0)
            self.pods.append(pod)
            self.set_pod(i, pins[i])

    def set_pod(self, i, app_id):
        pod = self.pods[i]
        for k in ("card", "label", "frame"):
            if pod.get(k): pod[k].removeNode(); pod[k] = None
        app = self.apps_by_id.get(app_id) if app_id else None
        self.state["pins"][i] = app.id if app else None
        cm = CardMaker("icon"); cm.setFrame(-0.42, 0.42, -0.42, 0.42)
        tint = (0.2, 0.75, 0.9)
        if app:
            tex, tint = self.icon_texture(app)
            card = pod["root"].attachNewNode(cm.generate()); card.setTwoSided(True)
            card.setTransparency(TransparencyAttrib.MAlpha)
            if tex:
                card.setTexture(tex)
            else:
                card.setColor(tint[0], tint[1], tint[2], 1)
                letter = self.text3d(app.name[:1].upper(), 0.5, (1, 1, 1, 1), None, card, billboard=False)
                letter.setPos(0, -0.01, -0.17)
            pod["card"] = card
            name = app.name
            hint = "open"
        else:
            name = "empty station"; hint = None
            if getattr(self, "taskbar_mode", False):
                pod["root"].hide(); pod["base"].hide()
        fm = CardMaker("frame"); fm.setFrame(-0.52, 0.52, -0.52, 0.52)
        frame = pod["root"].attachNewNode(fm.generate()); frame.setY(0.03); frame.setTwoSided(True)
        frame.setTransparency(TransparencyAttrib.MAlpha); frame.setColor(tint[0], tint[1], tint[2], 0.22 if app else 0.08)
        pod["frame"] = frame
        pod["base"].setColor(min(1, tint[0] + 0.15), min(1, tint[1] + 0.15), min(1, tint[2] + 0.15), 1)
        label = self.text3d(name if len(name) < 22 else name[:20] + "…", 0.13, parent=self.unlit_root, billboard=False)
        label.setPos(pod["root"].getX(), 29.1, 1.02)
        if not app and getattr(self, "taskbar_mode", False):
            label.hide(); pod["item"]["reach"] = 0
        pod["label"] = label
        pod["item"]["label"] = name
        pod["item"]["hint"] = hint

    def icon_texture(self, app):
        """Returns (texture or None, average colour) for the app's logo."""
        h = int(hashlib.sha1(app.id.encode()).hexdigest()[:6], 16)
        tint = colorsys.hsv_to_rgb((h % 360) / 360.0, 0.55, 0.85)
        try:
            path = desktop.icon_png(app.icon)
            if not path:
                return None, tint
            img = PNMImage()
            if not img.read(Filename.fromOsSpecific(path)):
                return None, tint
            if img.getXSize() > 256:
                small = PNMImage(256, 256, img.getNumChannels()); small.quickFilterFrom(img) if img.getXSize() % 256 == 0 else small.gaussianFilterFrom(1.0, img)
                img = small
            tex = Texture(app.id); tex.load(img)
            tex.setMinfilter(SamplerState.FTLinearMipmapLinear); tex.setWrapU(SamplerState.WMClamp); tex.setWrapV(SamplerState.WMClamp)
            r = g = b = n = 0.0
            step = max(1, img.getXSize() // 16)
            for yy in range(0, img.getYSize(), step):
                for xx in range(0, img.getXSize(), step):
                    a = img.getAlpha(xx, yy) if img.hasAlpha() else 1.0
                    if a > 0.5:
                        r += img.getRed(xx, yy); g += img.getGreen(xx, yy); b += img.getBlue(xx, yy); n += 1
            if n:
                hh, ss, vv = colorsys.rgb_to_hsv(r / n, g / n, b / n)
                tint = colorsys.hsv_to_rgb(hh, min(1, ss * 1.3 + 0.1), max(0.7, vv))
            return tex, tint
        except Exception:
            return None, tint

    def build_consoles(self):
        for c in self.markers["corescreens"]:
            mn, mx = c["bb"]
            pos = ((mn[0] + mx[0]) / 2, (mn[1] + mx[1]) / 2, 1.0)
            self.add_item("console", pos, 0.6, "Core console", "all apps", reach=3.2)
        t = self.text3d("ALL APPS", 0.22); t.setPos(0, 4, 2.3)

    # ------------------------------------------------------------ library
    def build_library_fixtures(self):
        racks = sorted(self.markers["racks"], key=lambda r: (round(r["pos"][1], 1), r["pos"][0]))
        self.racks = [r for r in racks if len(r["shelves"]) >= 3]
        kx, ky = 9.5, 7.6
        post = self.box.copyTo(self.lit_root); post.setPos(kx, ky, 0.6); post.setScale(0.14, 0.14, 1.2); post.setColor(0.1, 0.11, 0.13, 1)
        bar = self.box.copyTo(self.unlit_root); bar.setPos(kx, ky, 1.22); bar.setScale(3.2, 0.1, 0.04); bar.setColor(0.2, 0.9, 1, 1)
        self.lib_header = self.text3d("LIBRARY", 0.26, billboard=False); self.lib_header.setPos(kx, ky, 2.75)
        self.lib_sub = self.text3d("", 0.13, (0.75, 0.8, 0.85, 1), billboard=False); self.lib_sub.setPos(kx, ky, 2.35)
        for dx, text, kind, hint in ((-1.3, "^  UP", "lib_up", "go up a folder"), (0, "HOME", "lib_home", "back to the start folder"),
                                     (1.3, "NEXT  >", "lib_next", "next page of sections")):
            b = self.text3d(text, 0.17, (1, 0.85, 0.5, 1), (0.1, 0.06, 0.02, 0.9), billboard=False); b.setPos(kx + dx, ky, 1.55)
            self.add_item(kind, (kx + dx, ky, 1.6), 0.5, text.replace("^  ", "").replace("  >", ""), hint, reach=4.0)

    def list_dir(self, path, limit=3000):
        dirs, files = [], []
        try:
            with os.scandir(path) as it:
                for n, e in enumerate(it):
                    if n > limit:
                        break
                    if e.name.startswith(".") and not self.conf["show_hidden_files"]:
                        continue
                    try:
                        if e.is_dir():
                            dirs.append(e.path)
                        else:
                            files.append((e.path, e.stat().st_size))
                    except OSError:
                        pass
        except OSError:
            pass
        dirs.sort(key=lambda p: os.path.basename(p).lower()); files.sort(key=lambda f: os.path.basename(f[0]).lower())
        return dirs, files

    def populate_library(self, sound=True):
        for n in self.lib_nodes:
            n.removeNode()
        self.lib_nodes = []
        self.items = [it for it in self.items if it["kind"] not in ("book", "folder")]
        path = self.state["lib_path"]
        cap = BOOKS_PER_SHELF * 3
        dirs, files = self.list_dir(path)
        sections = []
        for i in range(0, max(len(files), 1), cap):
            chunk = files[i:i + cap]
            if chunk or not dirs:
                sections.append({"title": "loose files" if i == 0 else "loose files (cont.)", "path": None, "files": chunk, "more": 0})
        for d in dirs:
            sub_d, sub_f = self.list_dir(d, 600)
            sections.append({"title": os.path.basename(d), "path": d, "files": sub_f[:cap],
                             "more": max(0, len(sub_f) - cap), "ndirs": len(sub_d)})
        per = len(self.racks)
        self.lib_pages = max(1, math.ceil(len(sections) / per))
        self.state["page"] = page = min(max(0, int(self.state.get("page", 0))), self.lib_pages - 1)
        moved = self.state["moved"]
        for rack, sec in zip(self.racks, sections[page * per:(page + 1) * per]):
            rx, ry = rack["pos"][0], rack["pos"][1]
            title = sec["title"] if len(sec["title"]) < 26 else sec["title"][:24] + "…"
            if sec["path"]:
                extra = []
                if sec["more"]: extra.append(f"+{sec['more']} files")
                if sec.get("ndirs"): extra.append(f"{sec['ndirs']} folders")
                sign = self.text3d(title + ("\n" + ", ".join(extra) if extra else ""), 0.13, (1, 0.85, 0.5, 1), (0.1, 0.06, 0.02, 0.9))
                self.add_item("folder", (rx, ry, 3.15), 0.6, sec["title"], "enter folder", data=sec["path"], reach=5.5)
            else:
                sign = self.text3d(title, 0.12, (0.6, 0.95, 1, 1))
            sign.setPos(rx, ry, 3.12); self.lib_nodes.append(sign)
            holder = self.lit_root.attachNewNode("books"); self.lib_nodes.append(holder)
            for i, (fpath, size) in enumerate(sec["files"]):
                if fpath in moved or (self.held and self.held["path"] == fpath):
                    continue
                shelf = rack["shelves"][SHELF_ORDER[i // BOOKS_PER_SHELF]]
                slot = i % BOOKS_PER_SHELF
                dims, color = self.book_look(fpath, size)
                bx = (shelf[0][0] + shelf[1][0]) / 2
                by = shelf[0][1] + 0.11 + slot * 0.128
                bz = shelf[1][2] + dims[2] / 2
                node = self.make_book(holder, dims, color); node.setPos(bx, by, bz)
                self.add_item("book", (bx, by, bz), 0.2, os.path.basename(fpath), "open", node=node,
                              data={"path": fpath, "size": size, "dims": dims, "color": color, "shelved": True}, reach=3.0)
        name = os.path.basename(path.rstrip("/")) or "/"
        self.lib_header.node().setText(name if len(name) < 30 else name[:28] + "…")
        shown = path.replace(HOME, "~", 1) if path.startswith(HOME) else path
        info = f"{shown}    {len(dirs)} folders, {len(files)} files"
        if self.lib_pages > 1:
            info += f"    page {page + 1}/{self.lib_pages}"
        self.lib_sub.node().setText(info if len(info) < 90 else "…" + info[-88:])
        if sound:
            self.play("whoosh")

    def book_look(self, path, size):
        h = int(hashlib.sha1(path.encode("utf-8", "replace")).hexdigest()[:8], 16)
        ext = os.path.splitext(path)[1][1:].lower()
        base = CAT_COLORS[EXT_CAT.get(ext, "other")]
        hh, ss, vv = colorsys.rgb_to_hsv(*base)
        color = colorsys.hsv_to_rgb((hh + ((h % 21) - 10) / 300.0) % 1.0, ss, min(1, vv * (0.85 + (h >> 8) % 30 / 100.0)))
        height = 0.34 + min(0.26, math.log10(max(size, 1) + 10) * 0.03)
        thick = 0.07 + ((h >> 16) % 40) / 1000.0
        return (0.40, thick, height), color

    def make_book(self, parent, dims, color):
        node = parent.attachNewNode("book")
        cover = self.box.copyTo(node); cover.setScale(*dims); cover.setColor(color[0], color[1], color[2], 1)
        band = self.box.copyTo(node); band.setScale(dims[0] + 0.006, dims[1] + 0.006, 0.035); band.setZ(dims[2] * 0.28)
        band.setColor(min(1, color[0] + 0.35), min(1, color[1] + 0.35), min(1, color[2] + 0.35), 1)
        pages = self.box.copyTo(node); pages.setScale(dims[0] - 0.03, dims[1] - 0.02, 0.012); pages.setZ(dims[2] / 2)
        pages.setColor(0.93, 0.9, 0.82, 1)
        node.flattenStrong()
        return node

    def restore_moved_books(self):
        for path, tr in list(self.state["moved"].items()):
            if not os.path.exists(path):
                del self.state["moved"][path]; continue
            try: size = os.path.getsize(path)
            except OSError: size = 0
            dims, color = self.book_look(path, size)
            self.spawn_loose_book(path, size, dims, color, Point3(*tr[:3]), Vec3(*tr[3:6]), None)

    def spawn_loose_book(self, path, size, dims, color, pos, hpr, velocity):
        body = BulletRigidBodyNode("loosebook"); body.setMass(0.6)
        body.addShape(BulletBoxShape(Vec3(dims[0] / 2, dims[1] / 2, dims[2] / 2)))
        body.setFriction(0.9); body.setAngularDamping(0.4); body.setDeactivationTime(0.8)
        bnp = self.lit_root.attachNewNode(body); bnp.setPos(pos); bnp.setHpr(hpr)
        self.make_book(bnp, dims, color)
        if velocity is not None:
            body.setLinearVelocity(velocity); body.setAngularVelocity(Vec3(2, 1, 3))
        self.world.attachRigidBody(body)
        return self.add_item("loose", pos, 0.32, os.path.basename(path), "open", node=bnp,
                             data={"path": path, "size": size, "dims": dims, "color": color, "body": body}, reach=3.2)

    def enter_folder(self, path):
        if not os.path.isdir(path):
            self.toast("That folder is gone."); self.play("deny"); return
        self.state["lib_path"] = path; self.state["page"] = 0
        self.populate_library(); self.save()

    def folder_up(self):
        cur = self.state["lib_path"].rstrip("/") or "/"
        parent = os.path.dirname(cur) or "/"
        if parent == cur:
            self.play("deny"); return
        self.enter_folder(parent)

    def in_library(self):
        p = self.char_np.getPos()
        return LIB_ZONE[0] < p.x < LIB_ZONE[1] and LIB_ZONE[2] < p.y < LIB_ZONE[3]

    # ------------------------------------------------------------ HUD + menu
    def build_hud(self):
        def text(pos, scale, align=TextNode.ACenter, fg=(0.85, 0.97, 1, 1)):
            return OnscreenText(text="", pos=pos, scale=scale, fg=fg, shadow=(0, 0, 0, 0.9), align=align,
                                font=self.font, mayChange=True, parent=self.aspect2d)
        self.hud_dot = text((0, -0.012), 0.05); self.hud_dot.setText("+"); self.hud_dot.setFg((1, 1, 1, 0.55))
        self.hud_focus = text((0, -0.16), 0.062)
        self.hud_hint = text((0, -0.235), 0.044, fg=(1, 0.85, 0.5, 1))
        self.hud_toast = text((0, 0.82), 0.05, fg=(1, 0.95, 0.8, 1))
        self.hud_help = text((-1.72, 0.9), 0.04, TextNode.ALeft)
        self.help_on = False
        self.help_text = ("WASD / left stick   walk          hold Shift or L2 (or click L3)   sprint          Space / R1   jump\n"
                          "Mouse / right stick   look\n"
                          "E / Cross   open the book, app or folder you are looking at\n"
                          "F / Square   pick up or throw a book  -  on an app station: change the app\n"
                          "R   put a loose book back on its shelf\n"
                          "Q / Circle   up one folder (in the library)\n"
                          "Tab / Triangle   all apps\n"
                          "Esc / Options   normal 2D desktop (Ctrl+Alt+B from anywhere)        F10   quit\n"
                          "On the 2D desktop: left stick pointer, Cross click, Circle right click,\n"
                          "right stick scroll, D-pad arrows, Square Enter, Triangle Esc, PS button back to the ship\n"
                          "Voice typing on the 2D desktop: hold L1 and speak, or press Ctrl+Alt+V to start and stop")

    def toast(self, msg, secs=3.5):
        self.hud_toast.setText(msg); self.toast_until = time.time() + secs

    def build_menu(self):
        self.menu_root = self.aspect2d.attachNewNode("menu"); self.menu_root.hide()
        cm = CardMaker("bg"); cm.setFrame(-1.15, 1.15, -0.86, 0.86)
        bg = self.menu_root.attachNewNode(cm.generate()); bg.setColor(0.01, 0.03, 0.05, 0.9); bg.setTransparency(TransparencyAttrib.MAlpha)
        self.menu_title = OnscreenText(text="", pos=(-1.05, 0.74), scale=0.07, fg=(0.6, 0.95, 1, 1), align=TextNode.ALeft,
                                       font=self.font, mayChange=True, parent=self.menu_root)
        self.menu_rows = [OnscreenText(text="", pos=(-1.0, 0.6 - i * 0.085), scale=0.058, fg=(0.9, 0.95, 1, 1), align=TextNode.ALeft,
                                       font=self.font, mayChange=True, parent=self.menu_root) for i in range(15)]
        self.menu_foot = OnscreenText(text="", pos=(-1.05, -0.8), scale=0.042, fg=(1, 0.85, 0.5, 1), align=TextNode.ALeft,
                                      font=self.font, mayChange=True, parent=self.menu_root)
        cm = CardMaker("preview"); cm.setFrame(-0.3, 0.3, -0.3, 0.3)
        self.menu_icon = self.menu_root.attachNewNode(cm.generate()); self.menu_icon.setPos(0.72, 0, 0.2)
        self.menu_icon.setTransparency(TransparencyAttrib.MAlpha)
        self.menu_sel = 0; self.menu_target = None; self.menu_icon_for = None

    def open_menu(self, target_pod=None):
        if not self.apps:
            self.toast("No installed apps were found."); self.play("deny"); return
        self.mode = "menu"; self.menu_target = target_pod
        self.menu_title.setText("ALL APPS" if target_pod is None else f"CHOOSE AN APP FOR STATION {target_pod + 1}")
        self.menu_foot.setText("Up/Down or D-pad: move     Enter / Cross: " + ("launch" if target_pod is None else "put it here") +
                               "     type a letter: jump     Esc / Circle: close")
        self.menu_root.show(); self.hud_focus.setText(""); self.hud_hint.setText(""); self.refresh_menu(); self.play("open")

    def close_menu(self):
        self.menu_root.hide(); self.mode = "ship"; self.recenter_mouse()

    def refresh_menu(self):
        n = len(self.apps); self.menu_sel %= n
        top = min(max(0, self.menu_sel - 7), max(0, n - 15))
        for i, row in enumerate(self.menu_rows):
            j = top + i
            if j < n:
                sel = j == self.menu_sel
                row.setText(("> " if sel else "   ") + self.apps[j].name[:44])
                row.setFg((1, 0.85, 0.5, 1) if sel else (0.85, 0.92, 1, 0.9))
            else:
                row.setText("")
        app = self.apps[self.menu_sel]
        if self.menu_icon_for != app.id:
            tex, tint = self.icon_texture(app); self.menu_icon_for = app.id
            if tex:
                self.menu_icon.setTexture(tex, 1); self.menu_icon.setColor(1, 1, 1, 1)
            else:
                self.menu_icon.clearTexture(); self.menu_icon.setColor(tint[0], tint[1], tint[2], 1)

    def menu_pick(self):
        app = self.apps[self.menu_sel]
        target = self.menu_target
        self.close_menu()
        if target is None:
            self.launch(app)
        else:
            self.set_pod(target, app.id); self.play("dock"); self.save()
            self.toast(f"Station {target + 1} is now {app.name}.")

    def on_keystroke(self, ch):
        if self.mode != "menu" or not ch.isalnum():
            return
        ch = ch.lower()
        n = len(self.apps)
        for k in range(1, n + 1):
            j = (self.menu_sel + k) % n
            if self.apps[j].name.lower().startswith(ch):
                self.menu_sel = j; self.refresh_menu(); self.play("focus"); break

    # ------------------------------------------------------------ sound
    def load_sounds(self):
        self.sfx = {}
        vol = float(self.conf["volume"])
        for name in ("ambient", "step0", "step1", "focus", "open", "whoosh", "thud", "deny", "grab", "dock"):
            try:
                s = self.loader.loadSfx(Filename.fromOsSpecific(os.path.join(HERE, "assets", "sfx", name + ".wav")).getFullpath())
                s.setVolume(vol * {"ambient": 0.45, "step0": 0.5, "step1": 0.5, "focus": 0.35}.get(name, 0.8))
                self.sfx[name] = s
            except Exception:
                pass
        if "ambient" in self.sfx:
            self.sfx["ambient"].setLoop(True); self.sfx["ambient"].play()

    def play(self, name):
        s = self.sfx.get(name)
        if s:
            s.play()

    # ------------------------------------------------------------ input helpers
    def on_device(self, dev):
        try:
            if dev.device_class == InputDevice.DeviceClass.gamepad and self.pad is None:
                self.pad = dev; self.attachInputDevice(dev, prefix="pad")
                self.toast(f"Controller connected: {dev.name}")
        except Exception:
            pass

    def on_device_gone(self, dev):
        if dev == self.pad:
            try: self.detachInputDevice(dev)
            except Exception: pass
            self.pad = None

    def axis(self, name):
        if not self.pad:
            return 0.0
        try:
            v = self.pad.findAxis(getattr(InputDevice.Axis, name)).value
        except Exception:
            return 0.0
        return 0.0 if abs(v) < 0.14 else (v - math.copysign(0.14, v)) / 0.86

    def pad_down(self, button):
        if not self.pad:
            return False
        try:
            return bool(self.pad.findButton(getattr(GamepadButton, button)()).pressed)
        except Exception:
            return False

    def pad_hit(self, button):
        """True on the frame a controller button goes down."""
        now = self.pad_down(button)
        was = self.pad_prev.get(button, False)
        self.pad_prev[button] = now
        return now and not was

    def key_down(self, key):
        mw = self.mouseWatcherNode
        if not mw.hasMouse() and not self.selftest:
            pass
        try:
            btn = KeyboardButton.asciiKey(key) if len(key) == 1 else getattr(KeyboardButton, key)()
            return mw.isButtonDown(btn)
        except Exception:
            return False

    def key_hit(self, key):
        now = self.key_down(key)
        was = self.key_prev.get(key, False)
        self.key_prev[key] = now
        return now and not was

    def act(self, action, *keys):
        """True when an action was just pressed on the keyboard or the controller."""
        hit = False
        for k in keys:
            hit = self.key_hit(k) or hit
        btn = self.conf["pad"].get(action)
        if btn:
            hit = self.pad_hit(btn) or hit
        return hit

    def grab_mouse(self, on):
        if self.selftest:
            return
        props = WindowProperties(); props.setCursorHidden(on)
        props.setMouseMode(WindowProperties.M_confined if on else WindowProperties.M_absolute)
        self.win.requestProperties(props)
        if on:
            self.recenter_mouse()

    def recenter_mouse(self):
        self.win.movePointer(0, self.win.getXSize() // 2, self.win.getYSize() // 2)
        self.skip_mouse = 2

    # ------------------------------------------------------------ switching between 3D and 2D
    def go_desktop(self, minimize=True):
        if self.mode == "menu":
            self.menu_root.hide()
        self.mode = "desktop"; self.desktop_since = time.time()
        self.char.setLinearMovement(Vec3(0, 0, 0), True)
        self.grab_mouse(False); self.save()
        if "ambient" in self.sfx: self.sfx["ambient"].stop()
        if minimize and not self.selftest:
            props = WindowProperties(); props.setMinimized(True); self.win.requestProperties(props)
        if self.pad:
            self.ensure_backend()

    def go_ship(self, from_event=False):
        self.mode = "ship"
        if not from_event:
            props = WindowProperties(); props.setMinimized(False); props.setForeground(True)
            self.win.requestProperties(props)
            self.raise_window()
        self.grab_mouse(True)
        if "ambient" in self.sfx: self.sfx["ambient"].play()
        self.play("dock")
        for b in list(self.pad_prev): self.pad_prev[b] = True
        for k in list(self.key_prev): self.key_prev[k] = True

    def raise_window(self):
        """Ask the window manager to bring the ship back in front (helps on Wayland)."""
        import shutil, subprocess
        for tool, args in (("kdotool", ["search", "--name", "^ShipDesk$", "windowactivate"]),
                           ("wmctrl", ["-a", "ShipDesk"]),
                           ("xdotool", ["search", "--name", "^ShipDesk$", "windowactivate"])):
            exe = shutil.which(tool)
            if exe:
                try:
                    subprocess.Popen([exe] + args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return
                except Exception:
                    pass

    def on_window(self, win):
        if win != self.win or self.selftest:
            return
        p = win.getProperties()
        if not p.getOpen():
            self.quit(); return
        now = time.time()
        if self.mode == "desktop":
            if p.getForeground() and not p.getMinimized() and now - self.desktop_since > 1.0:
                self.go_ship(from_event=True)
        elif not p.getForeground() and now - self.started > 3.0:
            self.go_desktop(minimize=False)

    def launch(self, app):
        try:
            desktop.launch_app(app)
        except Exception as e:
            self.toast(f"Could not start {app.name}: {e}"); self.play("deny"); return
        self.play("open"); self.toast(f"Opening {app.name}…")
        if self.conf["minimize_on_open"]:
            self.taskMgr.doMethodLater(0.5, lambda t: self.go_desktop(), "to-desktop")

    def open_path(self, path):
        if not os.path.exists(path):
            self.toast("That file is gone."); self.play("deny"); return
        try:
            desktop.open_file(path)
        except Exception as e:
            self.toast(f"Could not open it: {e}"); self.play("deny"); return
        self.play("open"); self.toast(f"Opening {os.path.basename(path)}…")
        if self.conf["minimize_on_open"]:
            self.taskMgr.doMethodLater(0.5, lambda t: self.go_desktop(), "to-desktop")

    # ------------------------------------------------------------ interaction
    def find_focus(self):
        cam = self.camera.getPos(self.render)
        fwd = self.render.getRelativeVector(self.camera, Vec3(0, 1, 0))
        best, best_score = None, 1e9
        for it in self.items:
            if it["kind"] == "loose":
                it["pos"] = it["node"].getPos(self.render)
            d = it["pos"] - cam
            dist = d.length()
            if dist > it["reach"] or dist < 0.05:
                continue
            along = d.dot(fwd)
            if along < 0.1:
                continue
            perp = math.sqrt(max(0.0, dist * dist - along * along))
            if perp > it["r"]:
                continue
            score = perp / it["r"] + dist * 0.08
            if score < best_score:
                best, best_score = it, score
        return best

    def describe(self, it):
        k = it["kind"]
        if k in ("book", "loose"):
            size = it["data"]["size"]
            for unit in ("B", "KB", "MB", "GB", "TB"):
                if size < 1024 or unit == "TB": break
                size /= 1024.0
            hint = "E open    F pick up" + ("    R reshelve" if k == "loose" else "")
            return f"{it['label']}   ({size:.0f} {unit})", hint
        if k == "app":
            if self.taskbar_mode:
                return it["label"], "E open"
            return it["label"], ("E open    F change app" if it["hint"] else "F choose an app")
        return it["label"], "E " + it["hint"]

    def use(self, it):
        k = it["kind"]
        if k in ("book", "loose"): self.open_path(it["data"]["path"])
        elif k == "folder": self.enter_folder(it["data"])
        elif k == "app":
            app = self.apps_by_id.get(self.state["pins"][it["data"]["i"]] or "")
            if app: self.launch(app)
            elif not self.taskbar_mode: self.open_menu(it["data"]["i"])
        elif k == "console": self.open_menu(None)
        elif k == "lib_up": self.folder_up()
        elif k == "lib_home": self.enter_folder(os.path.expanduser(self.conf["library_root"]))
        elif k == "lib_next":
            if self.lib_pages > 1:
                self.state["page"] = (self.state["page"] + 1) % self.lib_pages; self.populate_library()
            else:
                self.play("deny"); self.toast("Everything already fits on one page.")

    def grab(self, it):
        if self.held:
            self.throw(); return
        if it is None:
            return
        if it["kind"] == "app":
            if self.taskbar_mode:
                self.toast("These stations follow your KDE taskbar. Pin or unpin apps there."); self.play("deny")
            else:
                self.open_menu(it["data"]["i"])
            return
        if it["kind"] not in ("book", "loose"):
            return
        d = it["data"]
        if it["kind"] == "loose":
            self.world.removeRigidBody(d["body"])
        it["node"].removeNode(); self.items.remove(it)
        self.state["moved"].pop(d["path"], None)
        node = self.make_book(self.hand, d["dims"], d["color"])
        self.held = {"path": d["path"], "size": d["size"], "dims": d["dims"], "color": d["color"], "node": node}
        self.focus = None; self.play("grab")

    def throw(self):
        h = self.held; self.held = None
        pos = h["node"].getPos(self.render); hpr = h["node"].getHpr(self.render); h["node"].removeNode()
        fwd = self.render.getRelativeVector(self.camera, Vec3(0, 1, 0))
        start = self.camera.getPos(self.render) + fwd * 0.7
        res = self.world.rayTestClosest(self.camera.getPos(self.render), start)
        if res.hasHit():
            start = self.camera.getPos(self.render) + fwd * 0.25
        self.spawn_loose_book(h["path"], h["size"], h["dims"], h["color"], start, hpr, fwd * 5.5 + Vec3(0, 0, 1.5))
        self.state["moved"][h["path"]] = [start.x, start.y, start.z, hpr.x, hpr.y, hpr.z]
        self.play("thud")

    def reshelve(self, it):
        d = it["data"]
        self.world.removeRigidBody(d["body"]); it["node"].removeNode(); self.items.remove(it)
        self.state["moved"].pop(d["path"], None)
        self.populate_library(sound=False); self.play("dock"); self.toast("Back on its shelf.")

    # ------------------------------------------------------------ frame update
    def update(self, task):
        dt = min(ClockObject.getGlobalClock().getDt(), 0.05)
        if self.quit_flag:
            self.quit()
        if self.toggle_flag:
            self.toggle_flag = False
            if self.mode == "desktop": self.go_ship()
            else: self.go_desktop()
        self.update_voice()
        if self.toast_until and time.time() > self.toast_until:
            self.hud_toast.setText(""); self.toast_until = 0
        if self.mode == "desktop":
            self.update_desktop(dt)
            time.sleep(0.008)
            return task.cont
        if self.mode == "menu":
            self.update_menu(dt)
        else:
            self.update_ship(dt)
        self.world.doPhysics(dt, 5, 1 / 180.0)
        t = time.time() - self.started
        for pod in self.pods:
            pod["root"].setZ(1.62 + 0.04 * math.sin(t * 1.3 + pod["phase"]))
            pod["root"].setH(18 * math.sin(t * 0.6 + pod["phase"]))
        if time.time() - self.last_save > 15:
            self.save()
        return task.cont

    def update_ship(self, dt):
        c = self.conf
        # look
        if not self.selftest and self.win.getProperties().getForeground():
            md = self.win.getPointer(0)
            cx, cy = self.win.getXSize() // 2, self.win.getYSize() // 2
            if getattr(self, "skip_mouse", 0) > 0:
                self.skip_mouse -= 1
            elif md.getInWindow():
                self.heading -= (md.getX() - cx) * c["mouse_sensitivity"]
                self.pitch -= (md.getY() - cy) * c["mouse_sensitivity"] * (-1 if c["invert_y"] else 1)
            self.win.movePointer(0, cx, cy)
        rx, ry = self.axis("right_x"), self.axis("right_y")
        self.heading -= rx * abs(rx) * c["stick_look_speed"] * dt
        self.pitch += ry * abs(ry) * c["stick_look_speed"] * 0.7 * dt * (-1 if c["invert_y"] else 1)
        self.pitch = max(-85.0, min(85.0, self.pitch)); self.heading %= 360.0
        self.char_np.setH(self.heading); self.camera.setP(self.pitch)
        # walk
        mx = (1 if self.key_down("d") else 0) - (1 if self.key_down("a") else 0) + self.axis("left_x")
        my = (1 if self.key_down("w") else 0) - (1 if self.key_down("s") else 0) + self.axis("left_y") + getattr(self, "auto_walk", 0)
        mag = math.hypot(mx, my)
        if mag > 1: mx, my = mx / mag, my / mag
        if self.pad_hit(c["pad"]["run"]): self.run_toggle = not getattr(self, "run_toggle", False)
        if mag < 0.1: self.run_toggle = False
        running = mag > 0.1 and (self.key_down("shift") or self.key_down("lshift") or self.key_down("rshift")
                                 or self.axis("left_trigger") > 0.4 or getattr(self, "run_toggle", False))
        speed = c["run_speed"] if running else c["walk_speed"]
        self.fov += ((90.0 if running else 78.0) - self.fov) * min(1.0, dt * 8)
        self.camLens.setFov(self.fov)
        self.char.setLinearMovement(Vec3(mx * speed, my * speed, 0), True)
        if self.act("jump", "space") and self.char.isOnGround():
            self.char.doJump()
        if mag > 0.1 and self.char.isOnGround():
            self.step_acc += min(mag, 1) * speed * dt
            if self.step_acc > (2.3 if running else 1.7):
                self.step_acc = 0; self.step_i ^= 1; self.play(f"step{self.step_i}")
        # focus
        it = None if self.held else self.find_focus()
        if it is not self.focus:
            self.focus = it
            if it: self.play("focus")
        if self.held:
            self.hud_focus.setText(os.path.basename(self.held["path"])); self.hud_hint.setText("F throw")
        elif it:
            label, hint = self.describe(it); self.hud_focus.setText(label); self.hud_hint.setText(hint)
        else:
            self.hud_focus.setText(""); self.hud_hint.setText("")
        # actions
        if self.act("interact", "e", "enter") and it:
            self.use(it)
        elif self.act("grab", "f"):
            self.grab(it)
        elif self.key_hit("r") and it and it["kind"] == "loose":
            self.reshelve(it)
        elif self.act("back", "q"):
            if self.in_library(): self.folder_up()
        elif self.act("apps", "tab"):
            self.open_menu(None)
        elif self.act("desktop", "escape"):
            self.go_desktop()
        if self.key_hit("h"):
            self.help_on = not self.help_on; self.hud_help.setText(self.help_text if self.help_on else "")

    def update_menu(self, dt):
        self.char.setLinearMovement(Vec3(0, 0, 0), True)
        ly = self.axis("left_y")
        self.menu_repeat = getattr(self, "menu_repeat", 0) - dt
        move = 0
        if self.key_hit("arrow_down") or self.pad_hit("dpad_down"): move = 1
        if self.key_hit("arrow_up") or self.pad_hit("dpad_up"): move = -1
        if self.key_hit("page_down") or self.pad_hit("rshoulder"): move = 12
        if self.key_hit("page_up") or self.pad_hit("lshoulder"): move = -12
        if abs(ly) > 0.5 and self.menu_repeat <= 0:
            move = -1 if ly > 0 else 1; self.menu_repeat = 0.12
        if move:
            self.menu_sel = max(0, min(len(self.apps) - 1, self.menu_sel + move)); self.refresh_menu(); self.play("focus")
        if self.key_hit("enter") or self.pad_hit(self.conf["pad"]["interact"]):
            self.menu_pick()
        elif self.key_hit("escape") or self.pad_hit(self.conf["pad"]["back"]) or self.key_hit("tab"):
            self.close_menu()

    def update_desktop(self, dt):
        """Minimised: the controller drives the normal desktop pointer."""
        c = self.conf
        if self.pad_hit(c["pad"]["ship"]) or (self.pad_down("back") and self.pad_hit("start")):
            self.go_ship(); return
        mb = self.mouse_backend
        if not mb or not self.pad:
            return
        try:
            lx, ly = self.axis("left_x"), self.axis("left_y")
            slow = 0.35 if self.axis("right_trigger") > 0.5 else 1.0
            self.ptr_frac[0] += lx * abs(lx) * c["pointer_speed"] * slow * dt
            self.ptr_frac[1] -= ly * abs(ly) * c["pointer_speed"] * slow * dt
            dx, dy = int(self.ptr_frac[0]), int(self.ptr_frac[1])
            if dx or dy:
                self.ptr_frac[0] -= dx; self.ptr_frac[1] -= dy; mb.move(dx, dy)
            ry = self.axis("right_y"); self.scroll_t -= dt
            if abs(ry) > 0.3 and self.scroll_t <= 0:
                mb.scroll(1 if ry > 0 else -1); self.scroll_t = 0.16 - 0.11 * abs(ry)
            for btn, kind, name in (("face_a", "button", "left"), ("face_b", "button", "right"),
                                    ("face_y", "key", "enter"), ("face_x", "key", "esc"),
                                    ("dpad_up", "key", "up"), ("dpad_down", "key", "down"),
                                    ("dpad_left", "key", "left"), ("dpad_right", "key", "right"),
                                    ("rshoulder", "key", "tab")):
                now = self.pad_down(btn); was = self.pad_prev.get("2d_" + btn, False)
                if now != was:
                    self.pad_prev["2d_" + btn] = now
                    getattr(mb, kind)(name, now)
        except Exception as e:
            print("ShipDesk: controller pointer stopped:", e, flush=True)
            self.mouse_backend = None; self.mouse_errors = [str(e)]

    # ------------------------------------------------------------ voice typing
    def ensure_backend(self):
        if self.mouse_backend is None and not self.mouse_errors:
            self.mouse_backend, self.mouse_errors = padmouse.open_backend()
            if self.mouse_backend is None:
                print("ShipDesk: cannot send keys or pointer moves ->", "; ".join(self.mouse_errors), flush=True)
        return self.mouse_backend

    def update_voice(self):
        v = self.voice
        want = None
        if self.voice_flag:                       # keyboard shortcut: press to start, press to stop
            self.voice_flag = False
            want = v.state != "recording"
        btn = self.conf["pad"].get("voice")
        if btn and self.mode == "desktop":        # controller: hold to talk
            held = self.pad_down(btn)
            if held != self.voice_held:
                self.voice_held = held; want = held
        if want is True and v.state == "idle":
            if v.start(): self.play("grab")
            else: self.play("deny")
        elif want is False and v.state == "recording":
            v.stop(); self.play("focus")
        res = v.poll()
        if res:
            kind, text = res
            if kind == "error":
                if text: voice.notify(text)
                self.play("deny")
            elif not text:
                voice.notify("Didn't catch anything."); self.play("deny")
            else:
                print(f"ShipDesk voice: heard {len(text)} characters", flush=True)
                self.type_text(text)

    def type_text(self, text):
        if self.mode != "desktop":
            self.toast("Heard: " + text[:80]); voice.set_clipboard(text); self.play("open"); return
        ok = voice.set_clipboard(text + " ")
        mb = self.ensure_backend()
        if ok and mb:
            def paste(task):
                try:
                    mb.key("ctrl", True); mb.key("v", True); mb.key("v", False); mb.key("ctrl", False)
                except Exception as e:
                    voice.notify(f"Copied, but could not paste: {e}. Press Ctrl+V.")
                return task.done
            self.taskMgr.doMethodLater(0.2, paste, "voice-paste")
            self.play("open")
        elif ok:
            voice.notify("Copied what you said. Press Ctrl+V to paste it. (Run install.sh --uinput so it types by itself.)", 5000)
            self.play("open")
        else:
            voice.notify("Heard: " + text + "  (no clipboard tool found to paste it)", 6000); self.play("deny")

    # ------------------------------------------------------------ saving / quitting
    def save(self):
        self.last_save = time.time()
        p = self.char_np.getPos()
        self.state["pos"] = [round(p.x, 3), round(p.y, 3), round(p.z, 3)]
        self.state["h"], self.state["p"] = round(self.heading, 2), round(self.pitch, 2)
        for it in self.items:
            if it["kind"] == "loose":
                n = it["node"]; q, r = n.getPos(), n.getHpr()
                self.state["moved"][it["data"]["path"]] = [round(v, 3) for v in (q.x, q.y, q.z, r.x, r.y, r.z)]
        if self.held:
            q = self.camera.getPos(self.render)
            self.state["moved"][self.held["path"]] = [round(q.x, 3), round(q.y, 3), 0.3, 0, 0, 90]
        try:
            save_json(os.path.join(DATA_DIR, "state.json"), self.state)
        except Exception as e:
            print("ShipDesk: could not save state:", e, flush=True)

    def quit(self):
        self.save()
        if self.mouse_backend:
            self.mouse_backend.close()
        try: os.remove(PID_FILE)
        except OSError: pass
        self.userExit()

    # ------------------------------------------------------------ self test (used during development)
    def run_selftest(self):
        out = ARGS.selftest; os.makedirs(out, exist_ok=True)
        steps = []

        def view(name, pos, h, p, before=None):
            def go(task):
                if before: before()
                self.char_np.setPos(*pos); self.heading, self.pitch = h, p
                return task.done
            def shot(task):
                self.focus = self.find_focus()
                if self.focus:
                    label, hint = self.describe(self.focus); self.hud_focus.setText(label); self.hud_hint.setText(hint)
                self.graphicsEngine.renderFrame()
                self.win.saveScreenshot(Filename.fromOsSpecific(os.path.join(out, name + ".png")))
                print("shot", name, "pos", self.char_np.getPos(), "focus", self.focus and self.focus["label"], flush=True)
                return task.done
            steps.append(go); steps.append(shot)

        view("1_spawn", (0, -9, 0.9), 0, 0)
        view("2_window_apps", (-1.5, 26.6, 0.9), 0, 4)
        view("3_library", (9.5, 4.0, 0.9), 0, 8)
        view("4_aisle", (6.5, 10.3, 0.9), 62, 3)
        view("5_folder_sign", (6.6, 12.6, 0.9), 90, 30)
        view("6_overview", (-14, -24, 0.9), -32, 12)
        def menu(): self.open_menu(None)
        view("7_menu", (0, -3, 0.9), 0, 0, before=menu)
        def thrown():
            self.close_menu()
            book = next(i for i in self.items if i["kind"] == "book")
            self.char_np.setPos(6.5, 9.0, 0.9); self.heading = -90
            self.grab(book); self.throw()
        view("8_thrown_book", (9.5, 5.5, 0.9), 55, -25, before=thrown)
        def enter():
            f = next((i for i in self.items if i["kind"] == "folder"), None)
            if f: self.use(f)
        view("9_subfolder", (9.5, 4.0, 0.9), 0, 8, before=enter)
        def walk_on(task):
            self.char_np.setPos(-9.5, 16.5, 0.9); self.heading, self.pitch = 0, 0; self.auto_walk = 1; return task.done
        def walk_off(task):
            self.auto_walk = 0; print("walked to", self.char_np.getPos(), "(deck step is 0.3 high, sofa blocks at y~21.5)", flush=True); return task.done
        steps.append(walk_on); steps.extend([lambda t: t.done] * 3); steps.append(walk_off)
        def finish(task):
            self.save(); print("items", len(self.items), "apps", len(self.apps), "pins", self.state["pins"], flush=True)
            self.userExit(); return task.done
        steps.append(finish)
        for i, s in enumerate(steps):
            self.taskMgr.doMethodLater(1.0 + i * 0.6, s, f"st{i}")


if __name__ == "__main__":
    ShipDesk().run()
