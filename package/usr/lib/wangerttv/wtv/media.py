"""Empfangsdienste: AirPlay (UxPlay) und Spotify Connect (librespot) – laufen in der TV-Sitzung."""
import os
import threading
import time

from . import config
from .util import bus, log, spawn, which

LIBRESPOT_EVENT = os.path.join(config.LIB, "bin", "wtv-librespot-event")


class Receivers:
    def __init__(self, settings):
        self.settings = settings
        self.procs = {}
        self.nowplaying = {}
        self._lock = threading.Lock()
        self._restarts = {}

    def _cmd(self, name):
        dev = (self.settings.get("device_name") or "WangertTV Box").strip()[:40]
        if name == "airplay":
            if not which("uxplay"):
                return None
            return ["uxplay", "-n", dev, "-nh", "-fs", "-nohold"]
        if name == "spotify":
            if not which("librespot"):
                return None
            cache = os.path.join(config.CACHE_DIR, "librespot")
            return ["librespot", "--name", dev, "--device-type", "tv", "--bitrate", "320",
                    "--backend", "alsa", "--initial-volume", "70", "--enable-volume-normalisation",
                    "--cache", cache, "--disable-audio-cache", "--onevent", LIBRESPOT_EVENT]
        return None

    def wanted(self, name):
        key = {"airplay": "airplay", "spotify": "spotify_connect"}[name]
        return bool(self.settings.get(key)) and bool(self.settings.get("setup_done"))

    def start(self, name):
        if config.DEV:
            return
        cmd = self._cmd(name)
        if not cmd:
            return
        with self._lock:
            proc = self.procs.get(name)
            if proc and proc.poll() is None:
                return
            env = dict(os.environ, WTV_PORT=str(config.PORT))
            self.procs[name] = spawn(cmd, logfile=os.path.join(config.LOG_DIR, f"{name}.log"), env=env)
            log.info("%s gestartet", name)

    def stop(self, name):
        with self._lock:
            proc = self.procs.pop(name, None)
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                proc.kill()

    def restart_all(self):
        for name in ("airplay", "spotify"):
            self.stop(name)
        self.apply()

    def apply(self):
        for name in ("airplay", "spotify"):
            if self.wanted(name):
                self.start(name)
            else:
                self.stop(name)

    def supervise(self):
        def loop():
            while True:
                time.sleep(5)
                for name in ("airplay", "spotify"):
                    proc = self.procs.get(name)
                    if self.wanted(name) and proc is not None and proc.poll() is not None:
                        count = self._restarts.get(name, 0) + 1
                        self._restarts[name] = count
                        if count <= 20:
                            log.warning("%s abgestürzt – Neustart %d", name, count)
                            time.sleep(min(60, 2 * count))
                            self.procs.pop(name, None)
                            self.start(name)
        threading.Thread(target=loop, daemon=True, name="receivers").start()

    def librespot_event(self, data):
        event = data.get("PLAYER_EVENT", "")
        if event in ("playing", "started", "changed", "track_changed", "paused", "loading"):
            name = data.get("NAME", "")
            artists = data.get("ARTISTS", "").replace("\n", ", ")
            covers = [c for c in (data.get("COVERS", "") or "").split("\n") if c.startswith("https://")]
            self.nowplaying = {
                "source": "spotify", "playing": event != "paused",
                "title": name or self.nowplaying.get("title", ""),
                "artist": artists or self.nowplaying.get("artist", ""),
                "cover": covers[0] if covers else self.nowplaying.get("cover", ""), "ts": time.time(),
            }
        elif event in ("stopped", "session_disconnected", "unavailable"):
            self.nowplaying = {}
        bus.emit("nowplaying", nowplaying=self.nowplaying)
