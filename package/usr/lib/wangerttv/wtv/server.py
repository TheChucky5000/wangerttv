"""HTTP-Server für Oberfläche und API (nur 127.0.0.1)."""
import json
import mimetypes
import os
import queue
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config, icons, iptv, phoneinput, remote, system, updates, weather
from .util import bus, jobs, log

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
ALLOWED_HOSTS = {f"127.0.0.1:{config.PORT}", f"localhost:{config.PORT}"}


class Api:
    """Bildet Pfade auf Funktionen ab. GET liefert Daten, POST führt Aktionen aus."""

    def __init__(self, core):
        self.core = core
        c = core
        s = core.settings
        self.get = {
            "/api/state": lambda q: {
                "settings": s.snapshot(), "live": config.is_live(), "version": config.version(),
                "standby": c.standby, "sleep_timer": c.sleep_timer_ends(), "dev": config.DEV,
                "nowplaying": c.receivers.nowplaying,
            },
            "/api/apps": lambda q: {"home": c.apps.home_apps(), "all": c.apps.all_apps(),
                                    "running": c.apps.running_list()},
            "/api/channels": lambda q: {"channels": c.channels.channels(), "configured": bool(s.get("iptv", "m3u"))},
            "/api/weather": lambda q: weather.get(s.get("city"), s.get("lang")),
            "/api/network": lambda q: system.network_status(),
            "/api/wifi/scan": lambda q: {"networks": system.wifi_scan()},
            "/api/bt": lambda q: {"devices": system.bt_devices()},
            "/api/audio": lambda q: system.audio_status(),
            "/api/display": lambda q: system.display_status(),
            "/api/timezones": lambda q: {"zones": system.timezones()},
            "/api/sysinfo": lambda q: system.sysinfo(),
            "/api/updates": lambda q: updates.status(),
            "/api/android": lambda q: c.android.status(),
            "/api/android/apps": lambda q: {"apps": c.android.apps()},
            "/api/remote": lambda q: remote.status(),
            "/api/disks": lambda q: {"disks": system.install_disks()},
            "/api/install": lambda q: c.install_status(),
            "/api/jobs": lambda q: {"jobs": jobs.list()},
            "/api/photos": lambda q: {"photos": self._photos()},
            "/api/qr": lambda q: {"svg": phoneinput.qr_svg(q.get("text", [""])[0][:500])},
        }
        self.post = {
            "/api/settings": lambda b: {"changed": c.patch_settings(b), "settings": s.snapshot()},
            "/api/setup/finish": lambda b: {"ok": c.finish_setup()},
            "/api/apps/launch": lambda b: c.apps.launch(b.get("id", ""), view=b.get("view"), channel=b.get("channel")),
            "/api/apps/close": lambda b: {"ok": c.apps.close(b.get("key", ""))},
            "/api/apps/install": lambda b: {"ok": c.apps.install(b.get("id", ""))},
            "/api/android/launch": lambda b: c.apps.launch_android_package(b["package"], b.get("name", ""))
            if b.get("package") else {"ok": c.android.launch(None)},
            "/api/android/init": lambda b: {"ok": c.android.init()},
            "/api/android/id": lambda b: {"ok": c.android.request_id()},
            "/api/home": lambda b: (c.apps.home(), {"ok": True})[1],
            "/api/power": lambda b: {"ok": c.power(b.get("action", ""), b.get("source", "ui"))},
            "/api/powerkey": lambda b: (c.power_key(), {"ok": True})[1],
            "/api/sleeptimer": lambda b: {"ends": c.set_sleep_timer(b.get("minutes", 0))},
            "/api/wifi/connect": lambda b: system.wifi_connect(b.get("ssid", ""), b.get("password", "")),
            "/api/wifi/forget": lambda b: {"ok": system.wifi_forget(b.get("ssid", ""))},
            "/api/wifi/enable": lambda b: {"ok": system.wifi_set_enabled(bool(b.get("on")))},
            "/api/bt/scan": lambda b: {"ok": system.bt_scan()},
            "/api/bt/pair": lambda b: system.bt_pair(b.get("mac", "")),
            "/api/bt/action": lambda b: {"ok": system.bt_action(b.get("action", ""), b.get("mac", ""))},
            "/api/audio": lambda b: {"ok": system.audio_set(sink=b.get("sink"), volume=b.get("volume"),
                                                            step=b.get("step"), mute_toggle=bool(b.get("mute")))},
            "/api/audio/test": lambda b: {"ok": system.audio_test()},
            "/api/iptv/test": lambda b: iptv.test(b.get("url", "")),
            "/api/channels/refresh": lambda b: {"ok": bool(c.channels.channels(force=True) is not None)},
            "/api/updates/check": lambda b: {"ok": updates.check()},
            "/api/updates/run": lambda b: {"ok": updates.run_all()},
            "/api/remote/learn": lambda b: remote.send("learn", timeout=16, seconds=15),
            "/api/remote/action": lambda b: {"ok": c.remote_action(b.get("action", ""), b.get("source", "remote"))},
            "/api/remote/cec": lambda b: remote.send(b.get("cmd", "status")),
            "/api/install": lambda b: c.start_install(b.get("disk", "")),
            "/api/reset": lambda b: {"ok": c.factory_reset()},
            "/api/librespot": lambda b: (c.receivers.librespot_event(b), {"ok": True})[1],
            "/api/phone/start": lambda b: phoneinput.new_session(b.get("label", "WangertTV"), b.get("secret", False)),
            "/api/phone/stop": lambda b: (phoneinput.close_session(b.get("token", "")), {"ok": True})[1],
        }

    def _photos(self):
        folder = os.path.expanduser(self.core.settings.get("ambient", "folder") or "")
        if not os.path.isdir(folder):
            return []
        files = sorted(f for f in os.listdir(folder) if os.path.splitext(f)[1].lower() in IMAGE_EXT)
        return [f"/photos/{urllib.parse.quote(f)}" for f in files[:500]]


def make_handler(api):
    class Handler(BaseHTTPRequestHandler):
        server_version = "WangertTV"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            log.debug("http: " + fmt, *args)

        # -------------------------------------------------------------- Hilfen
        def _host_ok(self):
            return self.headers.get("Host", "") in ALLOWED_HOSTS

        def _json(self, data, code=200):
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _file(self, path):
            if not os.path.isfile(path):
                self._json({"error": "not_found"}, 404)
                return
            ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript", "image/svg+xml"):
                ctype += "; charset=utf-8"
            with open(path, "rb") as fh:
                data = fh.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(data)

        @staticmethod
        def _safe_join(base, name):
            name = urllib.parse.unquote(name)
            path = os.path.realpath(os.path.join(base, name))
            if not path.startswith(os.path.realpath(base) + os.sep):
                return None
            return path

        # -------------------------------------------------------------- GET
        def do_GET(self):
            if not self._host_ok():
                self._json({"error": "forbidden"}, 403)
                return
            parsed = urllib.parse.urlparse(self.path)
            path, query = parsed.path, urllib.parse.parse_qs(parsed.query)
            if path in ("/", "/index.html"):
                self._file(os.path.join(config.UI_DIR, "index.html"))
            elif path.startswith("/assets/"):
                target = self._safe_join(os.path.join(config.UI_DIR, "assets"), path[len("/assets/"):])
                self._file(target) if target else self._json({"error": "forbidden"}, 403)
            elif path.startswith("/icons/"):
                for folder in config.ICON_DIRS:
                    target = self._safe_join(folder, path[len("/icons/"):])
                    if target and os.path.isfile(target):
                        self._file(target)
                        return
                self._json({"error": "not_found"}, 404)
            elif path.startswith("/appicon/"):
                app_id = urllib.parse.unquote(path[len("/appicon/"):])
                if app_id.startswith("android:"):
                    target = icons.android_icon(app_id.split(":", 1)[1])
                else:
                    app = api.core.apps.get(app_id)
                    target = icons.resolve(app)[0] if app else None
                self._file(target) if target else self._json({"error": "not_found"}, 404)
            elif path.startswith("/photos/"):
                folder = os.path.expanduser(api.core.settings.get("ambient", "folder") or "")
                target = self._safe_join(folder, path[len("/photos/"):]) if os.path.isdir(folder) else None
                if target and os.path.splitext(target)[1].lower() in IMAGE_EXT:
                    self._file(target)
                else:
                    self._json({"error": "not_found"}, 404)
            elif path == "/api/events":
                self._events()
            elif path in api.get:
                try:
                    self._json(api.get[path](query))
                except Exception as exc:  # noqa: BLE001
                    log.exception("GET %s", path)
                    self._json({"error": str(exc)}, 500)
            else:
                self._json({"error": "not_found"}, 404)

        def _events(self):
            q = bus.subscribe()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                self.wfile.write(b"retry: 2000\n\n")
                self.wfile.flush()
                while True:
                    try:
                        msg = q.get(timeout=20)
                        self.wfile.write(f"data: {json.dumps(msg, ensure_ascii=False)}\n\n".encode())
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                bus.unsubscribe(q)
                self.close_connection = True

        # -------------------------------------------------------------- POST
        def do_POST(self):
            # Schutz gegen Webseiten (z. B. im Browser-App), die 127.0.0.1 ansprechen wollen:
            # eigener Header erzwingt einen CORS-Preflight, den wir nie erlauben.
            if not self._host_ok() or self.headers.get("X-WTV") != "1":
                self._json({"error": "forbidden"}, 403)
                return
            path = urllib.parse.urlparse(self.path).path
            length = min(int(self.headers.get("Content-Length") or 0), 1024 * 1024)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("object expected")
            except ValueError:
                self._json({"error": "bad_json"}, 400)
                return
            handler = api.post.get(path)
            if not handler:
                self._json({"error": "not_found"}, 404)
                return
            try:
                self._json(handler(body))
            except Exception as exc:  # noqa: BLE001
                log.exception("POST %s", path)
                self._json({"error": str(exc)}, 500)

        def do_OPTIONS(self):
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()

    return Handler


def serve(core):
    api = Api(core)
    server = ThreadingHTTPServer((config.HOST, config.PORT), make_handler(api))
    server.daemon_threads = True
    log.info("WangertTV-Dienst läuft auf http://%s:%d", config.HOST, config.PORT)
    phoneinput.serve()
    server.serve_forever()
