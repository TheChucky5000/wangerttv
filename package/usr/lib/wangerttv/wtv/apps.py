"""App-Katalog, Starten/Beenden von Apps und der Start-Bildschirm (Launcher)."""
import glob
import json
import os
import signal
import threading
import time

from . import config, icons, player, tvnav
from .sway import HOME_WS, sway
from .util import bus, jobs, log, run, spawn, which

CHROME_FLAGS = [
    "--no-first-run", "--no-default-browser-check", "--noerrdialogs", "--disable-infobars",
    "--disable-session-crashed-bubble", "--disable-translate", "--password-store=basic",
    "--autoplay-policy=no-user-gesture-required", "--overscroll-history-navigation=0",
    "--check-for-update-interval=31536000", "--disable-features=Translate,MediaRouter,OptimizationHints",
    # schont SSD und Speicher: kein Sync/Absturzbericht, Zwischenspeicher je App höchstens 256 MB
    "--disable-sync", "--disable-breakpad", "--disk-cache-size=268435456",
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


def flatpak_running(ref):
    """Läuft eine Instanz dieser Flatpak-App? (Spotify & Co. übergeben einen zweiten Start an die laufende Instanz
    und beenden "flatpak run" sofort – der gestartete Prozess sagt dann nichts mehr aus.)"""
    if not ref:
        return False
    with _flatpak_ps["lock"]:
        # "flatpak ps" ist teuer und wird von mehreren Schleifen gefragt – Ergebnis 4 s lang teilen
        if time.time() - _flatpak_ps["ts"] > 4:
            rc, out, _ = run(["flatpak", "ps", "--columns=application"], timeout=5)
            _flatpak_ps.update(ts=time.time(), ids={line.strip() for line in out.splitlines()} if rc == 0 else set())
        return ref in _flatpak_ps["ids"]


_flatpak_ps = {"ts": 0.0, "ids": set(), "lock": threading.Lock()}


def kodi_process_running():
    rc, out, _ = run(["pgrep", "-u", str(os.getuid()), "-x", "kodi.bin"], timeout=3)
    return rc == 0 and bool(out.strip())


def instance_alive(entry):
    if entry.get("profile"):
        return profile_alive(entry["profile"])
    if entry.get("flatpak"):
        return flatpak_running(entry["flatpak"])
    return False


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
        self.on_running_change = None   # Core: z. B. Surround-Upmix an Kodi anpassen
        self.before_kodi = None         # Core: wird direkt vor dem Kodi-Start aufgerufen
        self.before_launch = None       # Core: vor dem Start einer App/Wiedergabe (z. B. Radio aus)
        self.kodi_focus_until = 0.0     # Live-TV: erst zu Kodi wechseln, wenn das Bild läuft (WangertTV bleibt sichtbar)

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
        port = self.tvnav_port(app)
        if port:
            # nur lokal erreichbar; darüber spielt WangertTV die Fernbedienungs-Steuerung ein
            cmd += [f"--remote-debugging-port={port}", "--remote-debugging-address=127.0.0.1"]
        if kiosk:
            cmd += ["--kiosk", "--start-fullscreen", f"--app={url}"]
        else:
            cmd += ["--start-maximized", url]
        return cmd

    def tvnav_port(self, app):
        """DevTools-Port für die TV-Steuerung (None = abgeschaltet, z. B. YouTube mit eigener TV-Oberfläche)."""
        # Achtung: Settings.get() nimmt nur Schlüssel (get("tvnav", True) hieße Pfad tvnav→True und lieferte immer None)
        if app.get("tvnav") is False or self.settings.get("tvnav") is False:
            return None
        ids = [a["id"] for a in self.catalog]
        idx = ids.index(app["id"]) if app["id"] in ids else len(ids)
        return tvnav.port_for(app["id"], idx)

    def own_tv_ui(self):
        return not (self.settings.get("tv") or {}).get("kodi_menus")

    def _tv_route(self, app_id, opts):
        """Live-TV/TV-Programm ohne bestimmten Sender: WangertTV-Ansicht statt Kodi-Menü (außer "Kodi-Menüs" ist an)."""
        if app_id == "tvguide":
            return "epg"
        if self.is_running("kodi") and not config.DEV and self.kodi.is_playing():
            opts["resume"] = True              # läuft schon: einfach zurück zum Bild
            return None
        tv = self.settings.get("tv") or {}
        recent = (self.settings.get("channel_prefs") or {}).get("recent") or []
        if tv.get("start_with", "last") == "last" and recent:
            opts["channel"] = recent[0]
            return None
        return "live"

    def launch(self, app_id, **opts):
        app = self.get(app_id)
        if not app:
            return {"ok": False, "message": "unknown_app"}
        typ = app.get("type")
        self.kodi_focus_until = 0.0
        if typ == "kodi" and app_id in ("livetv", "tvguide") and not opts.get("channel") and not opts.get("view") \
                and self.own_tv_ui():
            target = self._tv_route(app_id, opts)
            if target:
                return {"ok": True, "internal": target}
        if typ != "internal" and self.before_launch:
            try:
                self.before_launch(app)
            except Exception:  # noqa: BLE001
                log.exception("before_launch")
        if typ == "internal":
            if app_id == "player" and self.is_running("player"):
                sway.go("app-player")          # Player läuft schon: zurück zur Wiedergabe
                return {"ok": True, "resumed": True}
            return {"ok": True, "internal": app.get("target", app_id)}
        key = self.run_key(app)
        ws = f"app-{key}"
        with self._lock:
            entry = self.running.get(key)
            alive = bool(entry) and ((entry["proc"] is not None and entry["proc"].poll() is None) or
                                     (entry["proc"] is None and (entry.get("profile") or entry.get("flatpak")) is not None))
        url = opts.get("url")
        if url and typ in ("web", "browser") and str(url).startswith(("https://", "http://")):
            # mit bestimmter Adresse öffnen (z. B. Link vom Handy): laufende Instanz neu starten
            if alive:
                self.close(key)
                time.sleep(0.8)
                alive = False
            app = {**app, "url": url}
        try:
            if typ in ("kodi", "kodi_addon"):
                return self._launch_kodi(app, ws, alive, opts)
            if typ == "android":
                return self._launch_android(app, ws)
            if alive:
                self._claim_window(app, ws)
                sway.go(ws)
                # Läuft die Web-App schon (z. B. noch vom letzten Start der Oberfläche), die TV-Steuerung
                # trotzdem sicherstellen – attach() tut nichts, wenn sie schon verbunden ist
                port = self.tvnav_port(app) if typ == "web" else None
                if port and not config.DEV:
                    tvnav.attach(port, self.settings.get("lang"), lambda: key in self.running)
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
                path = opts.get("path")
                if path and isinstance(path, str) and path.startswith("/media/") and os.path.isdir(path):
                    cmd.append(path)        # z. B. ganzen USB-Stick an eine Medien-App übergeben
            else:
                return {"ok": False, "message": "unsupported"}
        except RuntimeError as exc:
            return {"ok": False, "message": str(exc)}
        sway.go(ws)
        self._start(key, ws, cmd, app)
        port = next((int(c.split("=", 1)[1]) for c in cmd if c.startswith("--remote-debugging-port=")), None)
        if port and not config.DEV:
            tvnav.attach(port, self.settings.get("lang"), lambda: key in self.running)
        elif typ in ("web", "browser") and not config.DEV:
            log.info("TV-Steuerung für %s ist aus (App hat eigene TV-Oberfläche oder in den Einstellungen abgeschaltet)", key)
        return {"ok": True}

    @staticmethod
    def _claim_window(app, ws):
        """Fenster einer App (z. B. Spotify, das sich an die laufende Instanz übergibt) auf ihre Arbeitsfläche holen."""
        pattern = app.get("window")
        if pattern and not config.DEV:
            safe_ws = ws.replace('"', "")
            for crit in ("app_id", "class"):
                run(["swaymsg", f'[{crit}="(?i){pattern}"] move container to workspace "{safe_ws}"'], timeout=5)
            if sway.focused != ws:
                # Fokus (Tastatur) dort lassen, wo man gerade ist – nicht beim verschobenen Fenster
                sway.cmd(f'workspace "{sway.focused.replace(chr(34), "")}"')

    def apply_window_rules(self):
        """Sway-Regeln für App-Fenster sofort setzen (die Datei sway/config gilt erst nach einem Neustart von Sway)."""
        if config.DEV:
            return
        for app in self.catalog:
            if app.get("window"):
                ws = f"app-{self.run_key(app)}"
                for crit in ("app_id", "class"):
                    sway.cmd(f'for_window [{crit}="(?i){app["window"]}"] move container to workspace {ws}')

    def is_running(self, key):
        with self._lock:
            entry = self.running.get(key)
            return bool(entry) and (entry["proc"] is None or entry["proc"].poll() is None)

    def start_background(self, app_id):
        """Flatpak-App starten, ohne sie anzuzeigen (z. B. Spotify als Connect-Ziel). Sway verschiebt das Fenster
        per Regel auf seine Arbeitsfläche; geöffnet wird es erst, wenn man die App auswählt."""
        app = self.get(app_id)
        if not app or app.get("type") != "flatpak" or app["flatpak"] not in self.flatpak_installed():
            return False
        key = self.run_key(app)
        if self.is_running(key):
            return True
        cmd = ["flatpak", "run", app["flatpak"], *app.get("args", [])]
        self._start(key, f"app-{key}", cmd, app)
        # falls das Fenster doch auf der aktuellen Fläche landet: nach dem Start auf seine eigene verschieben
        for delay in (4, 10):
            threading.Timer(delay, self._claim_window, args=(app, f"app-{key}")).start()
        return True

    def play_media(self, path, start_over=False):
        """Datei/Ordner/Blu-ray-Ordner im WangertTV Player (mpv) abspielen – ersetzt eine laufende Wiedergabe."""
        if not isinstance(path, str) or not path:
            return {"ok": False, "message": "not_found"}
        try:
            cmd = player.command(self.settings, path, bool(start_over))
        except RuntimeError as exc:
            return {"ok": False, "message": str(exc)}
        if self.before_launch:
            try:
                self.before_launch({"id": "player"})
            except Exception:  # noqa: BLE001
                log.exception("before_launch")
        if self.is_running("player"):
            self.close("player")
            time.sleep(0.4)
        app = dict(self.get("player") or {"id": "player", "name": "WangertTV Player"})
        app["env"] = player.env(self.settings, bool(start_over))
        sway.go("app-player")
        self._start("player", "app-player", cmd, app)
        return {"ok": True}

    def _changed(self, key):
        if key == "kodi" and self.on_running_change:
            try:
                self.on_running_change()
            except Exception:  # noqa: BLE001
                log.exception("on_running_change")

    def _start(self, key, ws, cmd, app):
        if config.DEV:
            log.info("DEV start %s: %s", key, " ".join(cmd))
            self.running[key] = {"proc": None, "ws": ws, "started": time.time(), "app": app["id"]}
            bus.emit("running")
            return
        env = dict(os.environ)
        env.update(app.get("env", {}))
        profile = next((c.split("=", 1)[1] for c in cmd if c.startswith("--user-data-dir=")), None)
        log.info("App %s wird gestartet: %s", key, " ".join(c for c in cmd if not c.startswith("--user-agent"))[:400])
        proc = spawn(cmd, logfile=self._log(key), env=env)
        with self._lock:
            self.running[key] = {"proc": proc, "ws": ws, "started": time.time(), "app": app["id"], "profile": profile,
                                 "flatpak": cmd[2] if cmd[:2] == ["flatpak", "run"] and len(cmd) > 2 else None}
        self._changed(key)
        bus.emit("running")

    def _launch_kodi(self, app, ws, alive, opts):
        view = opts.get("view") or app.get("view") or "home"
        if app.get("type") == "kodi_addon":
            view = f"addon:{app['addon']}"
        channel = opts.get("channel")
        if opts.get("resume") and alive:
            sway.go(ws)
            return {"ok": True, "resumed": True}
        hidden = bool(channel) and self.own_tv_ui() and not config.DEV and sway.focused != ws
        if not alive and not config.DEV and kodi_process_running():
            alive = True                       # Kodi läuft schon (z. B. nach Neustart des Dienstes) – nie ein zweites starten
        if alive:
            if hidden:
                # Umschalten aus WangertTV: Oberfläche bleibt stehen, bis Kodi das Bild zeigt (kein Kodi-Menü zu sehen)
                self._pend_kodi_focus(12, channel)
            else:
                sway.go(ws)

            def navigate():
                # Kodi kann beim Laden der Senderliste kurz nicht antworten – warten statt ein zweites Kodi zu starten
                if self.kodi.wait_ready(timeout=25):
                    self._kodi_navigate(view, channel)
                elif hidden:
                    self._kodi_focus_fallback(force=True)
            threading.Thread(target=navigate, daemon=True).start()
            return {"ok": True, "resumed": True, "pending": hidden}
        if hidden:
            self._pend_kodi_focus(30, channel)  # Kaltstart: Kodi lädt unsichtbar auf seiner Arbeitsfläche
        else:
            sway.go(ws)
        if self.before_kodi:
            try:
                self.before_kodi()     # z. B. Stereo-Upmix vom HDMI-Ausgang nehmen, damit Kodi ihn sauber erkennt
            except Exception:  # noqa: BLE001
                log.exception("before_kodi")
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
        if hidden:
            # Kodi-Fenster sicher auf seine Arbeitsfläche (falls die Sway-Regel noch nicht greift)
            for delay in (0.6, 1.5, 3.0):
                threading.Timer(delay, self._claim_window, args=(self.get("kodi") or app, ws)).start()

        def after():
            if self.kodi.post_start():
                self._kodi_navigate(view, channel)
            elif hidden:
                self._kodi_focus_fallback(force=True)
        threading.Thread(target=after, daemon=True).start()
        return {"ok": True, "pending": hidden}

    def _kodi_navigate(self, view, channel):
        if channel:
            if self.own_tv_ui():
                # Add-on legt den WangertTV-Hintergrund hinter das Bild – beim Start nie Kodis Menü
                self.kodi.rpc("JSONRPC.NotifyAll", {"sender": "service.wangerttv", "message": "wtv_prepare", "data": channel},
                              timeout=2)
            res = self.kodi.play_channel(channel)
            if not res.get("ok"):
                # PVR braucht beim allerersten Start etwas, bis die Senderliste geladen ist
                for _ in range(10):
                    time.sleep(3)
                    if self.kodi.play_channel(channel).get("ok"):
                        return
                if self.kodi_focus_until or self.own_tv_ui():
                    self._kodi_focus_fallback(force=True)   # nicht in Kodis Menü landen
                else:
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
            if not self.android.status()["running"]:
                bus.emit("toast", key="android_starting")
            ok = self.android.launch(pkg)
            if not ok:
                bus.emit("toast", key="android_failed")
                with self._lock:
                    self.running.pop("android", None)
                bus.emit("running")
                if sway.focused == ws:
                    self.home()
                return
            # Android-Fenster auf die eigene Arbeitsfläche holen (falls es woanders aufgegangen ist)
            sway.cmd(f'[app_id="(?i)waydroid"] move container to workspace "{ws}"')
            sway.go(ws)
        threading.Thread(target=go, daemon=True).start()
        return {"ok": True}

    def launch_android_package(self, package, name=""):
        if not package:
            return self._launch_android({"id": "android", "type": "android", "name": "Android"}, "app-android")
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
        if entry.get("flatpak") and (proc is None or proc.poll() is not None):
            run(["flatpak", "kill", entry["flatpak"]], timeout=10)
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
        self._changed(key)
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
        if self.kodi_focus_until:
            self.kodi_focus_until = 0.0
            bus.emit("tv_loading", on=False)
        sway.go_home()
        bus.emit("home")

    def kodi_focus(self):
        """Kodi meldet: das Bild läuft. Jetzt zu Kodi wechseln, falls darauf gewartet wird."""
        if self.kodi_focus_until and time.time() < self.kodi_focus_until + 1:
            self.kodi_focus_until = 0.0
            sway.go("app-kodi")
            bus.emit("tv_loading", on=False)
            return True
        return False

    def _pend_kodi_focus(self, seconds, channel):
        until = self.kodi_focus_until = time.time() + seconds
        bus.emit("tv_loading", name=channel or "", on=True)
        threading.Timer(seconds + 0.5, self._kodi_focus_fallback).start()
        threading.Thread(target=self._watch_playback, args=(until, channel), daemon=True).start()

    def _watch_playback(self, until, channel):
        """Zweiter Weg neben der Meldung des Kodi-Add-ons: läuft der gewünschte Sender schon? Dann zu Kodi wechseln."""
        want = (channel or "").casefold().strip()
        while self.kodi_focus_until == until and time.time() < until:
            time.sleep(0.7)
            if not self.kodi.ready():
                continue
            res = self.kodi.rpc("Player.GetItem", {"playerid": 1, "properties": ["channel"]}, timeout=2).get("result")
            item = (res.get("item") if isinstance(res, dict) else None) or {}
            label = (item.get("channel") or item.get("label") or "").casefold().strip()
            if want and label != want:
                continue
            props = self.kodi.rpc("Player.GetProperties", {"playerid": 1, "properties": ["time", "speed"]}, timeout=2).get("result")
            props = props if isinstance(props, dict) else {}
            t = props.get("time") or {}
            secs = t.get("hours", 0) * 3600 + t.get("minutes", 0) * 60 + t.get("seconds", 0) + t.get("milliseconds", 0) / 1000
            if props.get("speed") == 1 and secs >= 0.5:
                time.sleep(0.4)                 # erstes Bild ist da – kurz warten, damit kein Ladebalken zu sehen ist
                if self.kodi_focus_until == until:
                    self.kodi_focus()
                return

    def _kodi_focus_fallback(self, force=False):
        if self.kodi_focus_until and (force or time.time() >= self.kodi_focus_until):
            self.kodi_focus_until = 0.0
            bus.emit("tv_loading", on=False)
            if not force and self.kodi.is_playing():
                sway.go("app-kodi")
            else:
                bus.emit("toast", key="channel_failed", icon="alert")

    def watch(self):
        threading.Thread(target=self._watch_loop, daemon=True, name="app-watch").start()
        threading.Thread(target=self._kodi_watchdog, daemon=True, name="kodi-watchdog").start()

    def _kodi_watchdog(self):
        """Hängt Kodi (antwortet 45 s nicht mehr), wird es beendet und neu gestartet."""
        fails = 0
        while not self._stopping:
            time.sleep(15)
            if config.DEV:
                continue
            with self._lock:
                entry = self.running.get("kodi")
            proc = entry.get("proc") if entry else None
            if not proc or proc.poll() is not None or time.time() - entry["started"] < 90:
                fails = 0
                continue
            if self.kodi.ping(timeout=8):
                fails = 0
                continue
            fails += 1
            log.warning("Kodi antwortet nicht (%d/3)", fails)
            if fails >= 3:
                fails = 0
                self.kodi.save_crash_log("Kodi hing und wurde neu gestartet")
                entry["hung"] = True
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    pass

    def _watch_loop(self):
        while not self._stopping:
            time.sleep(1)
            ended = []
            with self._lock:
                for key, entry in list(self.running.items()):
                    proc = entry.get("proc")
                    if proc is not None and proc.poll() is not None:
                        if instance_alive(entry):
                            entry["proc"] = None          # an eine laufende Instanz (Chrome-Profil, Flatpak) übergeben
                            entry["checked"] = time.time()
                            continue
                        ended.append((key, entry))
                        del self.running[key]
                    elif proc is None and (entry.get("profile") or entry.get("flatpak")) and time.time() - entry.get("checked", 0) > 3:
                        entry["checked"] = time.time()
                        if not instance_alive(entry):
                            ended.append((key, entry))
                            del self.running[key]
            for key, entry in ended:
                proc = entry.get("proc")
                code = proc.returncode if proc is not None else 0
                log.info("App %s wurde beendet (Code %s, lief %d s)", key, code, time.time() - entry.get("started", time.time()))
                self._changed(key)
                crashed = key == "kodi" and (entry.get("hung") or (code not in (0, None)))
                if crashed and not entry.get("hung"):
                    self.kodi.save_crash_log(f"Kodi abgestürzt (Code {code})")
                if crashed and time.time() - entry.get("started", 0) > 20 and sway.focused == entry["ws"]:
                    # Kodi abgestürzt/gehangen, während man es benutzt hat: Protokoll sichern und neu starten
                    log.warning("Kodi beendet mit Code %s – starte neu", code)
                    bus.emit("toast", key="kodi_restarted")
                    threading.Thread(target=self.launch, args=(entry["app"],), daemon=True).start()
                    bus.emit("running")
                    continue
                if sway.focused == entry["ws"]:
                    self.home()
                bus.emit("running")
            self._supervise_launcher()

    # ------------------------------------------------------------------ Start-Bildschirm
    SPLASH_WS = "splash"

    def start_launcher(self):
        profile = os.path.join(config.CONF_DIR, "profiles", "launcher")
        if not config.DEV and not profile_alive(profile):
            # Systemstart: leere Arbeitsfläche zeigt das Bild der Bootanimation (Sway-Hintergrund), während die
            # Oberfläche unsichtbar lädt. Erst wenn sie ihr erstes Bild gemalt hat, wird umgeschaltet – kein Flackern.
            self._splash_until = time.time() + 12
            sway.go(self.SPLASH_WS)
            threading.Timer(12.5, self.splash_done).start()
        else:
            sway.go(HOME_WS)
        self._supervise_launcher(force=True)

    def splash_done(self):
        """Oberfläche meldet: Startbild ist gemalt -> sichtbar machen."""
        if getattr(self, "_splash_until", 0):
            self._splash_until = 0
            if sway.focused in (self.SPLASH_WS, "", None) or not sway.focused.startswith("app-"):
                sway.go(HOME_WS)

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
        # Fenster der Oberfläche immer auf "home" – auch wenn gerade die Start-Arbeitsfläche zu sehen ist
        sway.cmd(f'for_window [pid={self._launcher.pid}] move container to workspace "{HOME_WS}"')

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
