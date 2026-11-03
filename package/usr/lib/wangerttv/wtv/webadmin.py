"""Web-Oberfläche für den PC: Einstellungen von einem anderen Gerät im Heimnetz ändern.

Erreichbar unter http://<IP der Box>:8766/admin – Anmeldung mit der 6-stelligen PIN, die der Fernseher unter
Einstellungen → Gerät → Web-Oberfläche anzeigt. Nach der Anmeldung gilt ein Sitzungs-Cookie (30 Tage).

Sicherheit:
* Nur freigegebene API-Funktionen (kein Werksreset, keine Installation auf Festplatte).
* Passwörter (waipu.tv) werden nie an den Browser zurückgegeben.
* Cookie: HttpOnly + SameSite=Strict, zusätzlich Pflicht-Header X-WTV-Admin (blockiert fremde Webseiten).
* Nach 5 falschen PINs ist die Anmeldung von dieser Adresse 5 Minuten gesperrt.
"""
import copy
import hmac
import json
import os
import secrets
import threading
import time

from . import config
from .util import log

COOKIE = "wtvadmin"
SESSION_DAYS = 30
SESSIONS_FILE = os.path.join(config.CACHE_DIR, "webadmin-sessions.json")
PAGE = os.path.join(config.UI_DIR, "admin", "index.html")

GET_ALLOWED = {
    "/api/state", "/api/apps", "/api/sysinfo", "/api/updates", "/api/network", "/api/audio", "/api/display",
    "/api/remote", "/api/android", "/api/channels", "/api/weather", "/api/phoneremote", "/api/timezones", "/api/jobs",
    "/api/ssh", "/api/diag", "/api/health", "/api/usb", "/api/channels/all", "/api/epg", "/api/schedule", "/api/foryou",
    "/api/history", "/api/radio", "/api/radio/list",
}
POST_ALLOWED = {
    "/api/settings", "/api/updates/check", "/api/updates/run", "/api/power", "/api/home", "/api/apps/launch",
    "/api/apps/close", "/api/apps/install", "/api/channels/refresh", "/api/iptv/test", "/api/audio", "/api/audio/test",
    "/api/phoneremote/renew", "/api/android/restart", "/api/sleeptimer", "/api/ssh", "/api/speedtest", "/api/usb/eject",
    "/api/radio/stop", "/api/radio/play", "/api/radio/fav", "/api/reminders", "/api/history/clear", "/api/remote/action",
}
# Fernbedienungs-Aktionen, die die Web-Oberfläche auslösen darf
REMOTE_ACTIONS = {"home", "livetv", "tvguide", "volume_up", "volume_down", "mute", "standby", "wake"}
# Einstellungen, die nur am Fernseher geändert werden dürfen
PROTECTED_KEYS = {"web_admin", "phone_remote", "setup_done"}


class WebAdmin:
    def __init__(self):
        self.api = None
        self.settings = None
        self._lock = threading.Lock()
        self._sessions = self._load()
        self._fails = {}      # IP -> [Anzahl, gesperrt_bis]

    # ------------------------------------------------------------------ Einrichtung
    def configure(self, api):
        self.api = api
        self.settings = api.core.settings

    def enabled(self):
        return bool(self.settings) and bool((self.settings.get("web_admin") or {}).get("enabled", True))

    def info(self, renew=False):
        """Adresse + PIN für die Anzeige am Fernseher."""
        from .phoneinput import PORT, lan_ip, qr_svg
        pin = (self.settings.get("web_admin") or {}).get("pin") or ""
        if renew or not pin:
            pin = f"{secrets.randbelow(1000000):06d}"
            self.settings.patch({"web_admin": {"pin": pin}})
            with self._lock:
                self._sessions = {}
                self._save()
        url = f"http://{lan_ip()}:{PORT}/admin"
        return {"url": url, "pin": pin, "qr": qr_svg(url), "enabled": self.enabled()}

    # ------------------------------------------------------------------ Sitzungen
    def _load(self):
        data = config.read_json(SESSIONS_FILE, {}) or {}
        now = time.time()
        return {k: v for k, v in data.items() if isinstance(v, (int, float)) and v > now}

    def _save(self):
        try:
            config.atomic_write_json(SESSIONS_FILE, self._sessions, 0o600)
        except OSError as exc:
            log.warning("Web-Oberfläche: Sitzungen nicht gespeichert: %s", exc)

    def session_valid(self, cookie_header):
        sid = ""
        for part in (cookie_header or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE:
                sid = value
        if not sid:
            return False
        with self._lock:
            exp = self._sessions.get(sid)
            return bool(exp and exp > time.time())

    def login(self, ip, pin):
        """Liefert (ok, sid_oder_meldung)."""
        now = time.time()
        with self._lock:
            count, until = self._fails.get(ip, [0, 0])
            if until > now:
                return False, "locked"
        expected = (self.settings.get("web_admin") or {}).get("pin") or ""
        if expected and isinstance(pin, str) and hmac.compare_digest(pin.strip(), expected):
            sid = secrets.token_urlsafe(32)
            with self._lock:
                self._fails.pop(ip, None)
                self._sessions[sid] = now + SESSION_DAYS * 86400
                self._save()
            log.info("Web-Oberfläche: Anmeldung von %s", ip)
            return True, sid
        with self._lock:
            count += 1
            self._fails[ip] = [0, now + 300] if count >= 5 else [count, 0]
        log.warning("Web-Oberfläche: falsche PIN von %s", ip)
        return False, "wrong_pin"

    def logout(self, cookie_header):
        for part in (cookie_header or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE:
                with self._lock:
                    self._sessions.pop(value, None)
                    self._save()

    # ------------------------------------------------------------------ API
    @staticmethod
    def _sanitize_state(data):
        data = copy.deepcopy(data)
        st = data.get("settings") or {}
        waipu = st.get("waipu") or {}
        waipu["has_password"] = bool(waipu.get("password"))
        waipu["password"] = ""
        st.pop("web_admin", None)
        st.pop("phone_remote", None)
        return data

    def call(self, method, path, body=None, query=None):
        if method == "GET":
            if path not in GET_ALLOWED:
                return 404, {"error": "not_found"}
            res = self.api.get[path](query or {})
            if path == "/api/state":
                res = self._sanitize_state(res)
            if path == "/api/ssh":
                res = {k: v for k, v in res.items() if k != "password"}   # Passwort nur am Fernseher
            return 200, res
        if path not in POST_ALLOWED:
            return 404, {"error": "not_found"}
        try:
            self.api.core.touch()          # Bedienung über die Web-Oberfläche verhindert den Auto-Standby
        except AttributeError:
            pass
        body = dict(body or {})
        if path == "/api/settings":
            body = {k: v for k, v in body.items() if k not in PROTECTED_KEYS}
            waipu = body.get("waipu")
            if isinstance(waipu, dict) and not waipu.get("password"):
                waipu.pop("password", None)
            res = self.api.post[path](body)
            return 200, {"changed": res.get("changed", []), **self._sanitize_state({"settings": res.get("settings", {})})}
        if path == "/api/power" and body.get("action") not in ("standby", "wake", "reboot", "poweroff"):
            return 400, {"error": "bad_action"}
        if path == "/api/remote/action":
            if body.get("action") not in REMOTE_ACTIONS:
                return 400, {"error": "bad_action"}
            body["source"] = "web"
        res = self.api.post[path](body)
        if path == "/api/apps/launch" and isinstance(res, dict) and res.get("internal"):
            from .util import bus
            self.api.core.apps.home()              # z. B. Live-TV: Senderliste am Fernseher öffnen
            bus.emit("open", target=res["internal"])
        if path == "/api/ssh":
            res = {k: v for k, v in res.items() if k != "password"}
        return 200, res


admin = WebAdmin()


def read_page():
    with open(PAGE, "rb") as fh:
        return fh.read()


def to_json(data):
    return json.dumps(data, ensure_ascii=False).encode()
