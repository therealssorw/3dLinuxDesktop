"""Lets the controller drive the normal 2D desktop (pointer, clicks, a few keys).

Backends, tried in order:
  1. uinput  - a virtual mouse/keyboard made through /dev/uinput. Works on Wayland and X11,
               but your user needs write access to /dev/uinput (install.sh --uinput sets that up).
  2. xtest   - X11 only (python-xlib). Works out of the box in an X11 session.
  3. ydotool - if it is installed and its daemon is running.
"""
import os, struct, time, shutil, subprocess

EV_SYN, EV_KEY, EV_REL = 0, 1, 2
REL_X, REL_Y, REL_HWHEEL, REL_WHEEL = 0, 1, 6, 8
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 0x110, 0x111, 0x112
KEYS = {"esc": 1, "tab": 15, "enter": 28, "space": 57, "up": 103, "left": 105, "right": 106,
        "down": 108, "meta": 125, "backspace": 14}
UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_RELBIT = 0x40045564, 0x40045565, 0x40045566
UI_DEV_SETUP, UI_DEV_CREATE, UI_DEV_DESTROY = 0x405C5503, 0x5501, 0x5502


class UInput:
    name = "uinput"

    def __init__(self):
        import fcntl
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        for ev in (EV_KEY, EV_REL):
            fcntl.ioctl(self.fd, UI_SET_EVBIT, ev)
        for b in (BTN_LEFT, BTN_RIGHT, BTN_MIDDLE, *KEYS.values()):
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, b)
        for r in (REL_X, REL_Y, REL_WHEEL, REL_HWHEEL):
            fcntl.ioctl(self.fd, UI_SET_RELBIT, r)
        setup = struct.pack("HHHH80sI", 0x03, 0x1D0C, 0x0001, 1, b"ShipDesk controller pointer", 0)
        fcntl.ioctl(self.fd, UI_DEV_SETUP, setup)
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        time.sleep(0.2)

    def _ev(self, t, c, v):
        os.write(self.fd, struct.pack("llHHi", 0, 0, t, c, v))

    def _syn(self):
        self._ev(EV_SYN, 0, 0)

    def move(self, dx, dy):
        if dx: self._ev(EV_REL, REL_X, dx)
        if dy: self._ev(EV_REL, REL_Y, dy)
        self._syn()

    def scroll(self, dy):
        self._ev(EV_REL, REL_WHEEL, dy); self._syn()

    def button(self, which, down):
        self._ev(EV_KEY, {"left": BTN_LEFT, "right": BTN_RIGHT, "middle": BTN_MIDDLE}[which], 1 if down else 0)
        self._syn()

    def key(self, name, down):
        self._ev(EV_KEY, KEYS[name], 1 if down else 0); self._syn()

    def close(self):
        try:
            import fcntl
            fcntl.ioctl(self.fd, UI_DEV_DESTROY); os.close(self.fd)
        except Exception:
            pass


class XTest:
    name = "xtest"
    KEYSYM = {"esc": 0xFF1B, "tab": 0xFF09, "enter": 0xFF0D, "space": 0x20, "up": 0xFF52, "left": 0xFF51,
              "right": 0xFF53, "down": 0xFF54, "meta": 0xFFEB, "backspace": 0xFF08}

    def __init__(self):
        if os.environ.get("XDG_SESSION_TYPE", "x11") == "wayland":
            raise RuntimeError("XTest cannot drive native Wayland windows")
        from Xlib import display, X
        from Xlib.ext import xtest
        self.X, self.xtest = X, xtest
        self.d = display.Display()
        if not self.d.query_extension("XTEST"):
            raise RuntimeError("no XTEST")

    def move(self, dx, dy):
        self.d.warp_pointer(dx, dy); self.d.sync()

    def scroll(self, dy):
        b = 4 if dy > 0 else 5
        for _ in range(abs(dy)):
            self.xtest.fake_input(self.d, self.X.ButtonPress, b)
            self.xtest.fake_input(self.d, self.X.ButtonRelease, b)
        self.d.sync()

    def button(self, which, down):
        b = {"left": 1, "middle": 2, "right": 3}[which]
        self.xtest.fake_input(self.d, self.X.ButtonPress if down else self.X.ButtonRelease, b); self.d.sync()

    def key(self, name, down):
        code = self.d.keysym_to_keycode(self.KEYSYM[name])
        self.xtest.fake_input(self.d, self.X.KeyPress if down else self.X.KeyRelease, code); self.d.sync()

    def close(self):
        try: self.d.close()
        except Exception: pass


class YDoTool:
    name = "ydotool"

    def __init__(self):
        self.exe = shutil.which("ydotool")
        if not self.exe:
            raise RuntimeError("no ydotool")
        r = subprocess.run([self.exe, "mousemove", "-x", "0", "-y", "0"], capture_output=True, timeout=3)
        if r.returncode != 0:
            raise RuntimeError("ydotool daemon not reachable")

    def _run(self, *a):
        subprocess.Popen([self.exe, *a], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def move(self, dx, dy): self._run("mousemove", "-x", str(dx), "-y", str(dy))
    def scroll(self, dy): self._run("mousemove", "-w", "-x", "0", "-y", str(dy))
    def button(self, which, down):
        code = {"left": 0, "right": 1, "middle": 2}[which] | (0x40 if down else 0x80)
        self._run("click", hex(code))
    def key(self, name, down): self._run("key", f"{KEYS[name]}:{1 if down else 0}")
    def close(self): pass


def open_backend():
    errors = []
    for cls in (UInput, XTest, YDoTool):
        try:
            return cls(), errors
        except Exception as e:
            errors.append(f"{cls.name}: {e}")
    return None, errors
