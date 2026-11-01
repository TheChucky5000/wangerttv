"""WangertTV – Pfade, Einstellungen und gemeinsame Konstanten."""
import copy
import json
import os
import threading

DEV = os.environ.get("WTV_DEV") == "1"
LIB = os.environ.get("WTV_LIB", "/usr/lib/wangerttv")
HOME = os.path.expanduser("~")
CONF_DIR = os.environ.get("WTV_CONF", os.path.join(HOME, ".config", "wangerttv"))
CACHE_DIR = os.environ.get("WTV_CACHE", os.path.join(HOME, ".cache", "wangerttv"))
LOG_DIR = os.path.join(CACHE_DIR, "logs")
SETTINGS_FILE = os.path.join(CONF_DIR, "settings.json")
INSTALL_REQUEST = os.path.join(CONF_DIR, "install-request.json")
STATE_DIR = os.environ.get("WTV_STATE", "/var/lib/wangerttv")
UPDATE_STATUS = os.path.join(STATE_DIR, "update", "status.json")
INSTALL_STATUS = os.path.join(STATE_DIR, "update", "install.json")
ANDROID_STATUS = os.path.join(STATE_DIR, "update", "android.json")
RUN_DIR = os.environ.get("WTV_RUN", "/run/wangerttv")
CEC_SOCKET = os.path.join(RUN_DIR, "cec.sock")
CEC_STATUS = os.path.join(RUN_DIR, "cec.json")
UI_DIR = os.path.join(LIB, "ui")
ICON_DIRS = ["/etc/wangerttv/icons", os.path.join(LIB, "icons")]
PORT = int(os.environ.get("WTV_PORT", "8765"))
HOST = "127.0.0.1"


def version():
    for path in (os.path.join(LIB, "VERSION"), os.path.join(os.path.dirname(__file__), "..", "VERSION")):
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError:
            continue
    return "dev"


def is_live():
    if DEV:
        return os.environ.get("WTV_DEV_LIVE", "1") == "1"
    return os.path.isdir("/run/live/medium")


DEFAULTS = {
    "setup_done": False,
    "lang": "de",
    "theme": "wangert",            # Design der Oberfläche
    "device_name": "WangertTV Box",
    "timezone": "Europe/Berlin",
    "city": "",
    "apps": ["livetv", "youtube", "netflix", "spotify", "playstore", "mediathek", "browser"],
    "iptv": {"m3u": "", "epg": "", "refresh_hours": 24, "timeshift": False},
    "ambient": {
        "enabled": True,
        "minutes": 5,
        "source": "art",          # art | picsum | folder
        "folder": os.path.join(HOME, "Bilder"),
        "show_weather": True,
        "display_off_hours": 3,   # 0 = nie
    },
    "airplay": True,
    "spotify_connect": True,
    "cec": {
        "enabled": True,
        "tv_on_with_box": True,     # Box startet / wacht auf -> TV an
        "standby_with_tv": True,    # TV aus -> Box in Standby
        "tv_off_on_standby": True,  # Box Standby -> TV aus
        "hdmi_port": 0,             # 0 = automatisch
    },
    "autoupdate": {"enabled": True},
    "kodi": {"passthrough": False, "language_done": False},
    "display": {"mode": "auto", "scale": "auto"},
    "audio": {"sink": "", "speakers": "2.0"},   # Lautsprecher: 2.0 | 5.1 | 7.1
    "sleep_action": "standby",      # standby | suspend | poweroff
    "power_key": "standby",         # Aus-Taste: standby (an/aus umschalten) | menu
    "android": {"wanted": False},
    # Sondertasten der Fernbedienung (z. B. waipu.tv: Netflix, YouTube, TV, Programm).
    # match: "key:KEY_NAME" oder "scan:<hex>" (für Tasten ohne Linux-Tastencode)
    "remote": {"mappings": [
        {"match": "key:KEY_TV", "action": "app:livetv", "label": "TV"},
        {"match": "key:KEY_EPG", "action": "app:tvguide", "label": "EPG"},
        {"match": "key:KEY_PROGRAM", "action": "app:tvguide", "label": "Programm"},
        {"match": "key:KEY_PVR", "action": "app:livetv", "label": "Live-TV"},
    ]},
}


def _deep_merge(base, patch):
    out = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def atomic_write_json(path, data, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return copy.deepcopy(default)


class Settings:
    """Persistente Benutzereinstellungen (~/.config/wangerttv/settings.json)."""

    def __init__(self, path=SETTINGS_FILE):
        self.path = path
        self._lock = threading.RLock()
        self.data = _deep_merge(DEFAULTS, read_json(path, {}) or {})

    def get(self, *keys, default=None):
        with self._lock:
            node = self.data
            for key in keys:
                if not isinstance(node, dict) or key not in node:
                    return default
                node = node[key]
            return copy.deepcopy(node)

    def snapshot(self):
        with self._lock:
            return copy.deepcopy(self.data)

    def patch(self, patch):
        """Übernimmt Änderungen, speichert und liefert die geänderten Top-Level-Schlüssel."""
        if not isinstance(patch, dict):
            raise ValueError("patch must be an object")
        with self._lock:
            before = copy.deepcopy(self.data)
            clean = {k: v for k, v in patch.items() if k in DEFAULTS}
            self.data = _deep_merge(self.data, clean)
            changed = [k for k in clean if before.get(k) != self.data.get(k)]
            self.save()
            return changed

    def reset(self):
        with self._lock:
            self.data = copy.deepcopy(DEFAULTS)
            self.save()

    def save(self):
        with self._lock:
            atomic_write_json(self.path, self.data, 0o600)


def ensure_dirs():
    for path in (CONF_DIR, CACHE_DIR, LOG_DIR):
        os.makedirs(path, exist_ok=True)
