"""Voice typing: hold a button, speak, and the words are typed into whatever window has focus.

Recording uses whichever recorder the system has (PipeWire, ALSA, PulseAudio or ffmpeg) on the
default microphone - pick the DualSense microphone in KDE's audio settings to speak into the
controller. Speech is turned into text on this computer with faster-whisper; nothing is sent
anywhere. The speech engine and its model are downloaded the first time voice typing is used.
"""
import os, sys, time, wave, queue, shutil, signal, threading, subprocess

RUNTIME = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
WAV = os.path.join(RUNTIME, "shipdesk-voice.wav")


def notify(msg, ms=2500, title="ShipDesk voice typing"):
    """Desktop notification, so messages are seen even while the ship is minimised."""
    print(f"{title}: {msg}", flush=True)
    for exe, args in (("notify-send", ["-a", "ShipDesk", "-t", str(ms), title, msg]),
                      ("kdialog", ["--title", title, "--passivepopup", msg, str(max(2, ms // 1000))])):
        path = shutil.which(exe)
        if path:
            try:
                subprocess.Popen([path] + args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            except Exception:
                pass


def recorder_command(path):
    if shutil.which("pw-record"):
        return ["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16", path]
    if shutil.which("arecord"):
        return ["arecord", "-q", "-f", "S16_LE", "-r", "16000", "-c", "1", path]
    if shutil.which("parecord"):
        return ["parecord", "--rate=16000", "--channels=1", "--format=s16le", "--file-format=wav", path]
    if shutil.which("ffmpeg"):
        return ["ffmpeg", "-y", "-loglevel", "quiet", "-f", "pulse", "-i", "default", "-ar", "16000", "-ac", "1", path]
    return None


class Voice:
    def __init__(self, conf):
        self.conf = conf
        self.state = "idle"          # idle | recording | busy
        self.proc = None
        self.t0 = 0.0
        self.results = queue.Queue()
        self.model = None
        self.model_name = None

    # ---- called from the main loop
    def start(self):
        if self.state != "idle":
            return False
        cmd = recorder_command(WAV)
        if not cmd:
            notify("No recorder found. Install one with: sudo apt install pipewire-bin alsa-utils")
            return False
        try:
            if os.path.exists(WAV):
                os.remove(WAV)
            self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            notify(f"Could not start recording: {e}")
            return False
        self.state = "recording"; self.t0 = time.time()
        return True

    def stop(self):
        if self.state != "recording":
            return
        proc, self.proc = self.proc, None
        length = time.time() - self.t0
        self.state = "busy"
        threading.Thread(target=self._finish, args=(proc, length), daemon=True).start()

    def poll(self):
        """Returns ('text', words) or ('error', message) when a result is ready, else None."""
        try:
            return self.results.get_nowait()
        except queue.Empty:
            return None

    # ---- background thread
    def _finish(self, proc, length):
        try:
            try:
                proc.send_signal(signal.SIGINT); proc.wait(timeout=3)
            except Exception:
                try: proc.kill()
                except Exception: pass
            if length < 0.35 or not os.path.exists(WAV) or os.path.getsize(WAV) < 2000:
                self.results.put(("error", "")); return
            text = self._transcribe(WAV)
            self.results.put(("text", text))
        except Exception as e:
            self.results.put(("error", f"Voice typing failed: {e}"))
        finally:
            self.state = "idle"

    def _engine(self):
        try:
            import faster_whisper
            return faster_whisper
        except ImportError:
            pass
        if sys.prefix == sys.base_prefix:
            raise RuntimeError("the speech engine is missing; run: pip install faster-whisper")
        notify("Setting up voice typing: downloading the speech engine (one time, a few minutes)…", 8000)
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "faster-whisper"], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError("could not install the speech engine: " + (r.stderr.strip().splitlines() or ["unknown error"])[-1])
        import importlib
        importlib.invalidate_caches()
        import faster_whisper
        return faster_whisper

    def _transcribe(self, path):
        fw = self._engine()
        name = self.conf.get("voice_model") or "base"
        if self.model is None or self.model_name != name:
            cache = os.path.join(os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")), "huggingface")
            if not os.path.isdir(cache):
                notify(f"Downloading the '{name}' speech model (one time)…", 8000)
            self.model = fw.WhisperModel(name, device="cpu", compute_type="int8")
            self.model_name = name
        lang = self.conf.get("voice_language") or None
        segments, _info = self.model.transcribe(path, language=lang, beam_size=1, condition_on_previous_text=False)
        return " ".join(s.text.strip() for s in segments).strip()


def set_clipboard(text):
    """Put text on the clipboard with whatever tool exists. True on success."""
    for tool in ("qdbus6", "qdbus", "qdbus-qt6", "qdbus-qt5"):
        exe = shutil.which(tool)
        if exe:
            try:
                r = subprocess.run([exe, "org.kde.klipper", "/klipper", "org.kde.klipper.klipper.setClipboardContents", text],
                                   capture_output=True, timeout=3)
                if r.returncode == 0:
                    return True
            except Exception:
                pass
    for tool, args in (("wl-copy", []), ("xclip", ["-selection", "clipboard"]), ("xsel", ["--clipboard", "--input"])):
        exe = shutil.which(tool)
        if exe:
            try:
                p = subprocess.Popen([exe] + args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                p.stdin.write(text.encode("utf-8")); p.stdin.close()
                return True
            except Exception:
                pass
    return False
