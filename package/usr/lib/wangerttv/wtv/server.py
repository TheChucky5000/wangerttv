"""HTTP-Server für Oberfläche und API (nur 127.0.0.1)."""
import json
import mimetypes
import os
import queue
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config, files, history, icons, iptv, phoneinput, player, radio, remote, share, system, tools, updates, weather
from .webadmin import admin as webadmin
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
            "/api/channels": lambda q: {"channels": c.channels.channels(), "configured": bool(s.get("iptv", "m3u")) or bool(s.get("waipu", "username") and s.get("waipu", "password")),
                                        "waipu": bool(s.get("waipu", "username"))},
            "/api/weather": lambda q: weather.get(s.get("city"), s.get("lang")),
            "/api/network": lambda q: system.network_status(),
            "/api/wifi/scan": lambda q: {"networks": system.wifi_scan()},
            "/api/bt": lambda q: {"devices": system.bt_devices()},
            "/api/audio": lambda q: system.audio_status(),
            "/api/display": lambda q: system.display_status(),
            "/api/timezones": lambda q: {"zones": system.timezones()},
            "/api/sysinfo": lambda q: system.sysinfo(),
            "/api/health": lambda q: tools.health(),
            "/api/usb": lambda q: {"drives": c.usb.drives()},
            "/api/radio": lambda q: {**c.radio.state(), "favorites": s.get("radio", "favorites") or [],
                                     "recent": s.get("radio", "recent") or [], "genres": radio.GENRES,
                                     "states": radio.states(), "db": radio.db_info()},
            "/api/radio/list": lambda q: self._radio_list(q),
            "/api/files": lambda q: (files.listing(q["path"][0], q.get("hidden", ["0"])[0] == "1") if q.get("path", [""])[0]
                                     else {"ok": True, "roots": files.roots(c.usb.drives())}),
            "/api/files/job": lambda q: files.job(),
            "/api/share": lambda q: c.share_info(),
            "/api/epg": lambda q: {"start": int(q.get("start", ["0"])[0] or 0),
                                   "channels": c.channels.guide(int(q.get("start", ["0"])[0] or 0), min(12, max(1, int(q.get("hours", ["4"])[0] or 4))))},
            "/api/channels/all": lambda q: {"channels": c.channels.all_channels()},
            "/api/foryou": lambda q: c.foryou(),
            "/api/history": lambda q: history.summary(),
            "/api/schedule": lambda q: {"at": int(q.get("at", ["0"])[0] or 0), "items": c.channels.schedule(int(q.get("at", ["0"])[0] or 0)),
                                        "reminders": s.get("reminders") or []},
            "/api/player/sources": lambda q: {"sources": player.sources(c.usb.drives()), "recent": player.recent()},
            "/api/player/browse": lambda q: player.browse(q.get("path", [""])[0]),
            "/api/audiodriver": lambda q: system.audio_driver(),
            "/api/diag": lambda q: {"text": tools.diag_report(c)},
            "/api/cleanup": lambda q: tools.cleanup_preview("kodi" in c.apps.running),
            "/api/updates": lambda q: updates.status(),
            "/api/android": lambda q: c.android.status(),
            "/api/android/apps": lambda q: {"apps": c.android.apps()},
            "/api/remote": lambda q: remote.status(),
            "/api/disks": lambda q: {"disks": system.install_disks()},
            "/api/install": lambda q: c.install_status(),
            "/api/jobs": lambda q: {"jobs": jobs.list()},
            "/api/photos": lambda q: {"photos": self._photos()},
            "/api/phoneremote": lambda q: c.phone_remote_info(),
            "/api/webadmin": lambda q: webadmin.info(),
            "/api/ir": lambda q: remote.send("ir_status", timeout=3),
            "/api/ssh": lambda q: system.ssh_status(),
            "/api/qr": lambda q: {"svg": phoneinput.qr_svg(q.get("text", [""])[0][:500])},
        }
        self.post = {
            "/api/settings": lambda b: {"changed": c.patch_settings(b), "settings": s.snapshot()},
            "/api/setup/finish": lambda b: {"ok": c.finish_setup()},
            "/api/apps/launch": lambda b: c.apps.launch(b.get("id", ""), view=b.get("view"), channel=b.get("channel"),
                                                        path=b.get("path")),
            "/api/apps/close": lambda b: {"ok": c.apps.close(b.get("key", ""))},
            "/api/apps/install": lambda b: {"ok": c.apps.install(b.get("id", ""))},
            "/api/android/launch": lambda b: c.apps.launch_android_package(b.get("package") or "", b.get("name", "")),
            "/api/android/restart": lambda b: (threading.Thread(target=c.android.restart, daemon=True).start(), {"ok": True})[1],
            "/api/android/init": lambda b: {"ok": c.android.init()},
            "/api/android/id": lambda b: {"ok": c.android.request_id()},
            "/api/home": lambda b: (c.apps.home(), {"ok": True})[1],
            "/api/power": lambda b: {"ok": c.power(b.get("action", ""), b.get("source", "ui"))},
            "/api/speedtest": lambda b: tools.speedtest(),
            "/api/usb/eject": lambda b: c.usb.eject(str(b.get("path", ""))),
            "/api/reminders": lambda b: c.reminder(str(b.get("action", "")), b.get("item") or {}, bool(b.get("auto"))),
            "/api/share/renew": lambda b: c.share_info(renew=True),
            "/api/share/play": lambda b: c.apps.play_media(str(b.get("path", "")), True)
            if str(b.get("path", "")).startswith(share.FOLDER + "/") else {"ok": False},
            "/api/files/op": lambda b: files.operate(str(b.get("op", "")), str(b.get("path", "")), str(b.get("name", "")),
                                                      [p for p in (b.get("paths") or []) if isinstance(p, str)]),
            "/api/files/transfer": lambda b: files.transfer(str(b.get("mode", "")), b.get("paths") or [], str(b.get("dest", ""))),
            "/api/radio/play": lambda b: c.radio.play(b.get("station")),
            "/api/radio/stop": lambda b: c.radio.stop(),
            "/api/radio/fav": lambda b: c.radio.favorite(b.get("station") or {}, bool(b.get("on"))),
            "/api/player/play": lambda b: c.apps.play_media(b.get("path"), bool(b.get("start_over"))),
            "/api/player/forget": lambda b: {"ok": player.forget(str(b.get("path", "")))},
            "/api/audiodriver": lambda b: {"ok": system.set_audio_driver(str(b.get("driver", "")))},
            "/api/cleanup": lambda b: tools.cleanup("kodi" in c.apps.running),
            "/api/powerkey": lambda b: (c.power_key(), {"ok": True})[1],
            "/api/resumed": lambda b: (c.resumed(), {"ok": True})[1],
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
            "/api/remote/volume_osd": lambda b: (c.volume_osd(b.get("level"), b.get("mute"), str(b.get("source", "avr"))),
                                                 {"ok": True})[1],
            "/api/install": lambda b: c.start_install(b.get("disk", "")),
            "/api/reset": lambda b: {"ok": c.factory_reset()},
            "/api/librespot": lambda b: (c.receivers.librespot_event(b), {"ok": True})[1],
            "/api/kodi/event": lambda b: c.kodi_event(b),
            "/api/ui/ready": lambda b: (threading.Timer(0.15, c.apps.splash_done).start(), {"ok": True})[1],
            "/api/history": lambda b: c.history_tick(b),
            "/api/history/clear": lambda b: (history.clear(), {"ok": True})[1],
            "/api/phoneremote/renew": lambda b: c.phone_remote_info(renew=True),
            "/api/webadmin/renew": lambda b: webadmin.info(renew=True),
            "/api/ir": lambda b: remote.send(f"ir_{b.get('action', '')}", timeout=32, slot=b.get("slot", ""))
            if b.get("action") in ("learn", "send", "clear", "cancel") else {"ok": False},
            "/api/ssh": lambda b: {"ok": system.ssh_set(b.get("action", "")), **system.ssh_status()},
            "/api/phone/start": lambda b: phoneinput.new_session(b.get("label", "WangertTV"), b.get("secret", False)),
            "/api/phone/stop": lambda b: (phoneinput.close_session(b.get("token", "")), {"ok": True})[1],
        }

    def _radio_list(self, q):
        kind, arg = q.get("kind", ["top"])[0], q.get("q", [""])[0]
        country = (self.core.settings.get("radio", "country") or "DE")[:2]
        try:
            if kind == "search":
                stations = radio.search(arg)
            elif kind == "state":
                stations = radio.by_state(arg[:40])
            elif kind == "genre":
                stations = radio.genre(arg[:40], country)
            else:
                stations = radio.top(country)
        except RuntimeError:
            return {"ok": False, "message": "offline", "stations": []}
        return {"ok": True, "stations": stations}

    def _photos(self):
        if self.core.settings.get("ambient", "source") == "handy":
            # Fotos, die vom Handy geschickt wurden (alle Tage), neueste zuerst
            out = []
            for root, _, names in os.walk(share.FOLDER):
                for n in names:
                    if os.path.splitext(n)[1].lower() in IMAGE_EXT:
                        rel = os.path.relpath(os.path.join(root, n), share.FOLDER)
                        out.append(("/handy/" + "/".join(urllib.parse.quote(x) for x in rel.split(os.sep)), os.path.getmtime(os.path.join(root, n))))
            return [u for u, _ in sorted(out, key=lambda x: -x[1])[:500]]
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

        def _file(self, path, cache=False):
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
            self.send_header("Cache-Control", "max-age=604800" if cache else "no-cache")
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
            elif path.startswith("/chlogo/"):
                from . import logos
                target = self._safe_join(logos.CACHE, path[len("/chlogo/"):]) if os.path.isdir(logos.CACHE) else None
                if target and target.endswith(".png") and os.path.isfile(target):
                    self._file(target, cache=True)
                else:
                    self._json({"error": "not_found"}, 404)
            elif path.startswith("/handy/"):
                # Vorschaubilder für "Vom Handy" (nur Bilder aus ~/Handy)
                target = self._safe_join(share.FOLDER, path[len("/handy/"):]) if os.path.isdir(share.FOLDER) else None
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
    webadmin.configure(api)
    phoneinput.configure_remote(lambda: core.settings.get("phone_remote", "token"), core.phone_remote)
    phoneinput.configure_share(lambda: core.settings.get("share", "token"), core.share_action)
    phoneinput.serve()
    server.serve_forever()
