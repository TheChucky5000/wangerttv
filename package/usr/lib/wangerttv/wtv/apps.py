"""App-Katalog, Starten/Beenden von Apps und der Start-Bildschirm (Launcher)."""
import glob
import json
import os
import signal
import threading
import time

from . import config, icons
from .sway import HOME_WS, sway
from .util import bus, jobs, log, run, spawn, which

CHROME_FLAGS = [
    "--no-first-run", "--no-default-browser-check", "--noerrdialogs", "--disable-infobars",
    "--disable-session-crashed-bubble", "--disable-translate", "--password-store=basic",
    "--autoplay-policy=no-user-gesture-required", "--overscroll-history-navigation=0",
    "--check-for-update-interval=31536000", "--disable-features=Translate,MediaRouter",
    "--ozone-platform=wayland",
    "--enable-features=AcceleratedVideoDecodeLinuxGL,AcceleratedVideoDecodeLinuxZeroCopyGL,"
    "VaapiVideoDecoder,VaapiIgnoreDriverChecks,VaapiVideoDecodeLinuxGL",
]


def chrome_binary():
    for name in ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser"):
        path = which(name)
        if path:
            return path
    return None


def profile_alive(profile):
    """Läuft bereits ein Chrome mit diesem Profil? (Chrome übergibt sonst an die laufende Instanz und beendet sich.)"""
    if not profile:
        return False
    rc, out, _ = run(["pgrep", "-u", str(os.getuid()), "-f", f"--user-data-dir={profile}( |$)"], timeout=5)
    return rc == 0 and bool(out.strip())


def load_catalog():
    """Lädt apps.json und Ergänzungen aus /etc/wangerttv/apps.d/*.json."""
    catalog = []
    files = [os.path.join(config.LIB, "apps.json")] + sorted(glob.glob("/etc/wangerttv/apps.d/*.json"))
    seen = {}
    for path in files:
        try:
            with open(path, encoding="utf-8") as fh:
                entries = json.load(fh)
        except (OSError, ValueError) as exc:
            if os.path.exists(path):
                log.warning("App-Katalog %s fehlerhaft: %s", path, exc)
            continue
        for app in entries if isinstance(entries, list) else []:
            if not isinstance(app, dict) or not app.get("id"):
                continue
            if app["id"] in seen:
                catalog[seen[app["id"]]].update(app)
            else:
                seen[app["id"]] = len(catalog)
                catalog.append(app)
    return catalog


class AppManager:
    def __init__(self, settings, kodi, android):
        self.settings = settings
        self.kodi = kodi
        self.android = android
        self.catalog = load_catalog()
        self.running = {}        # run_key -> {"proc", "ws", "started", "app"}
        self._lock = threading.RLock()
        self._flatpak_cache = {"ts": 0, "ids": set()}
        self._launcher = None
        self._stopping = False

    # ------------------------------------------------------------------ Katalog
    def get(self, app_id):
        return next((a for a in self.catalog if a["id"] == app_id), None)

    def flatpak_installed(self, refresh=False):
        if config.DEV:
            return {"com.spotify.Client"}
        if refresh or time.time() - self._flatpak_cache["ts"] > 30:
            ids = set()
            if which("flatpak"):
                out = run(["flatpak", "list", "--app", "--columns=application"], timeout=15)[1]
                ids = {line.strip() for line in out.splitlines() if line.strip()}
            self._flatpak_cache = {"ts": time.time(), "ids": ids}
        return self._flatpak_cache["ids"]

    def available(self, app):
        """Ist die App sofort startbar (ohne Installation)?"""
        typ = app.get("type")
        if typ == "flatpak":
            return app.get("flatpak") in self.flatpak_installed() or bool(app.get("fallback_url"))
        if typ == "native":
            return bool(which(app.get("cmd", [""])[0]))
        if typ in ("web", "browser"):
            return bool(chrome_binary()) or config.DEV
        if typ == "android":
            return self.android.status()["installed"]
        return True

    def describe(self, app):
        key = self.run_key(app)
        icon_url, banner = icons.url_for(app)
        return {
            "id": app["id"], "name": app.get("name", app["id"]), "type": app.get("type"),
            "color": app.get("color", "#1e3a5f"), "color2": app.get("color2", app.get("color", "#0b1a2e")),
            "glyph": app.get("glyph", "app"), "desc": app.get("desc", {}), "category": app.get("category", "other"),
            "icon": icon_url, "banner": banner, "running": key in self.running,
            "installable": app.get("type") == "flatpak" and app.get("flatpak") not in self.flatpak_installed(),
            "available": self.available(app), "hidden": bool(app.get("hidden")),
        }

    def home_apps(self):
        order = self.settings.get("apps") or []
        result = []
        for app_id in order:
            app = self.get(app_id)
            if app:
                result.append(self.describe(app))
        return result

    def all_apps(self):
        return [self.describe(a) for a in self.catalog if not a.get("hidden")]

    # ------------------------------------------------------------------ Starten
    @staticmethod
    def run_key(app):
        typ = app.get("type")
        if typ in ("kodi", "kodi_addon"):
            return "kodi"
        if typ == "android":
            return "android"
        return app["id"]

    def _log(self, key):
        return os.path.join(config.LOG_DIR, f"{key}.log")

    def _chrome_cmd(self, app, kiosk=True):
        chrome = chrome_binary()
        if not chrome:
            raise RuntimeError("Google Chrome ist nicht installiert")
        profile = os.path.join(config.CONF_DIR, "profiles", app["id"])
        cmd = [chrome, f"--user-data-dir={profile}", f"--lang={self.settings.get('lang')}", *CHROME_FLAGS]
        if app.get("ua"):
            cmd.append(f"--user-agent={app['ua']}")
        url = app.get("url") or app.get("fallback_url")
        if kiosk:
            cmd += ["--kiosk", "--start-fullscreen", f"--app={url}"]
        else:
            cmd += ["--start-maximized", url]
        return cmd

    def launch(self, app_id, **opts):
        app = self.get(app_id)
        if not app:
            return {"ok": False, "message": "unknown_app"}
        typ = app.get("type")
        if typ == "internal":
            return {"ok": True, "internal": app.get("target", app_id)}
        key = self.run_key(app)
        ws = f"app-{key}"
        with self._lock:
            entry = self.running.get(key)
            alive = bool(entry) and ((entry["proc"] is not None and entry["proc"].poll() is None) or
                                     (entry["proc"] is None and entry.get("profile") is not None))
        try:
            if typ in ("kodi", "kodi_addon"):
                return self._launch_kodi(app, ws, alive, opts)
            if typ == "android":
                return self._launch_android(app, ws)
            if alive:
                sway.go(ws)
                return {"ok": True, "resumed": True}
            if typ == "web":
                cmd = self._chrome_cmd(app, kiosk=True)
            elif typ == "browser":
                cmd = self._chrome_cmd(app, kiosk=False)
            elif typ == "flatpak":
                if app["flatpak"] in self.flatpak_installed():
                    cmd = ["flatpak", "run", app["flatpak"], *app.get("args", [])]
                elif app.get("fallback_url"):
                    cmd = self._chrome_cmd(app, kiosk=True)
                else:
                    return {"ok": False, "message": "not_installed", "installable": True}
            elif typ == "native":
                cmd = list(app["cmd"])
                if not which(cmd[0]):
                    return {"ok": False, "message": "not_installed"}
            else:
                return {"ok": False, "message": "unsupported"}
        except RuntimeError as exc:
            return {"ok": False, "message": str(exc)}
        sway.go(ws)
        self._start(key, ws, cmd, app)
        return {"ok": True}

    def _start(self, key, ws, cmd, app):
        if config.DEV:
            log.info("DEV start %s: %s", key, " ".join(cmd))
            self.running[key] = {"proc": None, "ws": ws, "started": time.time(), "app": app["id"]}
            bus.emit("running")
            return
        env = dict(os.environ)
        env.update(app.get("env", {}))
        profile = next((c.split("=", 1)[1] for c in cmd if c.startswith("--user-data-dir=")), None)
        proc = spawn(cmd, logfile=self._log(key), env=env)
        with self._lock:
            self.running[key] = {"proc": proc, "ws": ws, "started": time.time(), "app": app["id"], "profile": profile}
        bus.emit("running")

    def _launch_kodi(self, app, ws, alive, opts):
        view = opts.get("view") or app.get("view") or "home"
        if app.get("type") == "kodi_addon":
            view = f"addon:{app['addon']}"
        channel = opts.get("channel")
        sway.go(ws)
        if alive and self.kodi.ready():
            threading.Thread(target=self._kodi_navigate, args=(view, channel), daemon=True).start()
            return {"ok": True, "resumed": True}
        self.kodi.prepare()
        cmd = ["kodi"]
        windowing = os.environ.get("WTV_KODI_WINDOWING", "wayland")
        if windowing:
            cmd.append(f"--windowing={windowing}")
        if not which("kodi") and not config.DEV:
            return {"ok": False, "message": "not_installed"}
        app = dict(app, env=dict(app.get("env", {})))
        # Kodi gibt Ton über PipeWire aus – auch Dolby/DTS/Atmos-Durchreichen (iec958.codecs am HDMI-Ausgang,
        # siehe /usr/share/wireplumber/wireplumber.conf.d/90-wangerttv-hdmi.conf). Andere Apps bleiben dabei hörbar.
        app["env"]["KODI_AE_SINK"] = "PIPEWIRE"
        self._start("kodi", ws, cmd, app)

        def after():
            if self.kodi.post_start():
                self._kodi_navigate(view, channel)
        threading.Thread(target=after, daemon=True).start()
        return {"ok": True}

    def _kodi_navigate(self, view, channel):
        if channel:
            res = self.kodi.play_channel(channel)
            if not res.get("ok"):
                # PVR braucht beim allerersten Start etwas, bis die Senderliste geladen ist
                for _ in range(10):
                    time.sleep(3)
                    if self.kodi.play_channel(channel).get("ok"):
                        return
                self.kodi.open_view("tvchannels")
            return
        self.kodi.open_view(view)

    def _launch_android(self, app, ws):
        pkg = app.get("package")
        sway.go(ws)
        with self._lock:
            self.running.setdefault("android", {"proc": None, "ws": ws, "started": time.time(), "app": app["id"]})
        bus.emit("running")

        def go():
            ok = self.android.launch(pkg)
            if not ok:
                bus.emit("toast", key="android_failed")
                self.home()
        threading.Thread(target=go, daemon=True).start()
        return {"ok": True}

    def launch_android_package(self, package, name=""):
        app = {"id": f"android:{package}", "type": "android", "package": package, "name": name}
        return self._launch_android(app, "app-android")

    # ------------------------------------------------------------------ Beenden / Überwachen
    def close(self, key):
        with self._lock:
            entry = self.running.pop(key, None)
        if not entry:
            return False
        if key == "kodi":
            self.kodi.quit()
            time.sleep(1.5)
        if key == "android":
            self.android.stop()
        proc = entry.get("proc")
        if entry.get("profile") and (proc is None or proc.poll() is not None):
            run(["pkill", "-u", str(os.getuid()), "-f", f"--user-data-dir={entry['profile']}( |$)"], timeout=5)
        if proc and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=5)
            except (ProcessLookupError, PermissionError, OSError):
                pass
            except Exception:  # noqa: BLE001 – z. B. TimeoutExpired
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    pass
        if sway.focused == entry["ws"]:
            self.home()
        bus.emit("running")
        return True

    def running_list(self):
        with self._lock:
            items = []
            for key, entry in self.running.items():
                app = self.get(entry["app"]) or {"id": entry["app"], "name": entry["app"]}
                d = self.describe(app) if "type" in app else {"id": entry["app"], "name": entry["app"]}
                d["key"] = key
                d["since"] = entry["started"]
                if key == "kodi":
                    d["name"] = "Kodi"
                if key == "android":
                    d["name"] = "Android"
                items.append(d)
            return items

    def home(self):
        sway.go_home()
        bus.emit("home")

    def watch(self):
        threading.Thread(target=self._watch_loop, daemon=True, name="app-watch").start()

    def _watch_loop(self):
        while not self._stopping:
            time.sleep(1)
            ended = []
            with self._lock:
                for key, entry in list(self.running.items()):
                    proc = entry.get("proc")
                    if proc is not None and proc.poll() is not None:
                        if entry.get("profile") and profile_alive(entry["profile"]):
                            entry["proc"] = None          # an eine laufende Chrome-Instanz übergeben
                            continue
                        ended.append((key, entry))
                        del self.running[key]
                    elif proc is None and entry.get("profile") and time.time() - entry.get("checked", 0) > 3:
                        entry["checked"] = time.time()
                        if not profile_alive(entry["profile"]):
                            ended.append((key, entry))
                            del self.running[key]
            for key, entry in ended:
                log.info("App %s wurde beendet", key)
                if sway.focused == entry["ws"]:
                    self.home()
                bus.emit("running")
            self._supervise_launcher()

    # ------------------------------------------------------------------ Start-Bildschirm
    def start_launcher(self):
        sway.go(HOME_WS)
        self._supervise_launcher(force=True)

    def _supervise_launcher(self, force=False):
        if config.DEV or self._stopping:
            return
        if self._launcher and self._launcher.poll() is None:
            return
        profile = os.path.join(config.CONF_DIR, "profiles", "launcher")
        if time.time() - getattr(self, "_launcher_check", 0) < 5:
            return
        self._launcher_check = time.time()
        if profile_alive(profile):
            return      # Start-Bildschirm läuft bereits (z. B. nach Neustart des Hintergrunddienstes)
        if self._launcher is not None and not force:
            log.warning("Start-Bildschirm wurde beendet – starte neu")
            sway.go(HOME_WS)
        chrome = chrome_binary()
        if not chrome:
            log.error("Kein Chrome/Chromium gefunden – Start-Bildschirm kann nicht laufen")
            return
        cmd = [chrome, f"--user-data-dir={profile}", *CHROME_FLAGS, "--kiosk", "--start-fullscreen",
               "--disable-pinch", "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
               f"--app=http://{config.HOST}:{config.PORT}/"]
        self._launcher = spawn(cmd, logfile=self._log("launcher"))

    def stop(self):
        self._stopping = True

    # ------------------------------------------------------------------ Flatpak-Installation
    def install(self, app_id):
        app = self.get(app_id)
        if not app or app.get("type") != "flatpak":
            return False
        return jobs.start(f"install:{app_id}", app.get("name", app_id), self._install_flatpak, app["flatpak"])

    def _install_flatpak(self, ref):
        if config.DEV:
            time.sleep(3)
            return "ok"
        run(["flatpak", "remote-add", "--user", "--if-not-exists", "flathub",
             "https://dl.flathub.org/repo/flathub.flatpakrepo"], timeout=60)
        rc, out, err = run(["flatpak", "install", "--user", "-y", "--noninteractive", "flathub", ref], timeout=3600)
        self.flatpak_installed(refresh=True)
        bus.emit("apps")
        if rc != 0:
            raise RuntimeError((err or out).strip()[-300:])
        return "ok"

    def ensure_defaults_installed(self):
        """Installiert nach der Einrichtung fehlende Flatpak-Apps der Startseite im Hintergrund."""
        for app_id in self.settings.get("apps") or []:
            app = self.get(app_id)
            if app and app.get("type") == "flatpak" and app.get("flatpak") not in self.flatpak_installed(refresh=True):
                self.install(app_id)
