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
    "accent": "",                  # eigene Akzentfarbe (leer = passend zum Design)
    "motion": "full",              # full | calm (weniger Animationen, z. B. für langsame Geräte)
    "device_name": "WangertTV Box",
    "timezone": "Europe/Berlin",
    "city": "",
    "apps": ["livetv", "tvplaner", "youtube", "netflix", "spotify", "playstore", "mediathek", "player", "radio", "browser"],
    "migrations": [],               # einmalige Umstellungen nach Updates (z. B. VLC -> WangertTV Player)
    "livetv_source": "iptv",        # iptv | waipu
    "iptv": {"m3u": "", "epg": "", "refresh_hours": 24, "timeshift": False},
    "channel_prefs": {"favorites": [], "recent": []},   # Lieblingssender und zuletzt gesehene Sender (Namen)
    "waipu": {"provider": 0, "username": "", "password": ""},   # provider: 0 = waipu.tv, 1 = O2 TV
    "ambient": {
        "enabled": True,
        "minutes": 5,
        "source": "art",          # art | picsum | folder | handy
        "folder": os.path.join(HOME, "Bilder"),
        "show_weather": True,
        "display_off_hours": 3,   # 0 = nie
        "style": "classic",       # classic | center | info | night
        "interval": 15,           # Sekunden pro Bild
    },
    "airplay": True,
    "tvnav": True,
    "phone_remote": {"token": ""},
    "share": {"token": "", "autoshow": True},   # Vom Handy teilen: Token im QR-Code, empfangene Fotos/Videos sofort zeigen
    "web_admin": {"enabled": True, "pin": ""},   # Web-Oberfläche für den PC (Port 8766, /admin)  # Handy als Fernbedienung (Token im QR-Code)                  # Fernbedienungs-Steuerung (Pfeiltasten + Bildschirmtastatur) in Web-Apps
    "spotify_connect": True,
    "radio": {"favorites": [], "recent": [], "last": None, "country": "DE"},   # Internetradio
    "tv": {                          # Live-TV
        "order": [], "hidden": [],     # eigene Senderreihenfolge / ausgeblendete Sender (Namen)
        "logos": "official",           # official (offizielle Logos) | provider (Logos des Anbieters)
        "renumber": True,              # Sendernummern folgen der eigenen Reihenfolge
        "infobar_secs": 6,             # Info-Leiste beim Umschalten (Sekunden)
        "start_with": "last",          # Live-TV öffnet mit: last (letzter Sender) | guide (Programmübersicht)
        "back_to": "live",             # Zurück im Fernsehbild: live (Live-TV-Seite) | home | guide
        "kodi_menus": False,           # Kodi-eigene Menüs statt WangertTV-Oberfläche
        "smart_reminders": False,      # an Lieblingssendungen automatisch erinnern
    },
    "personal": {"enabled": True, "name": "", "morning": True},
    "box": {"power_profile": "balanced", "wake_to": "home"},     # Leistungsprofil der CPU, Ansicht nach dem Aufwachen   # intelligente Startseite (lernt lokal), Name für die Begrüßung
    "reminders": [],                # TV-Planer: [{"name", "title", "start", "end", "auto"}]
    "spotify_mode": "librespot",    # Spotify Connect über librespot (ohne Anmeldung) oder die offizielle App ("app")
    "cec": {
        "enabled": True,
        "tv_on_with_box": True,     # Box startet / wacht auf -> TV an
        "standby_with_tv": True,    # TV aus -> Box in Standby
        "tv_off_on_standby": True,  # Box Standby -> TV aus
        "hdmi_port": 0,             # 0 = automatisch (Adresse aus den EDID-Daten)
        "via": "tv",                # Box hängt direkt am Fernseher ("tv") oder am AV-Receiver ("avr") – nur bei festem Port
        "volume": "auto",           # Lautstärketasten: auto (Receiver per CEC, falls vorhanden) | avr | box
        "pause_on_switch": True,    # Fernseher wechselt auf anderen Eingang -> Wiedergabe anhalten
    },
    "autoupdate": {"enabled": True},
    "update_repo": "",             # GitHub-Repository für WangertTV-Updates (leer = aus update.conf)
    "kodi": {"passthrough": False, "hd_passthrough": False, "language_done": False},
    "display": {"mode": "auto", "scale": "auto"},
    "audio": {"sink": "", "speakers": "2.0", "upmix": True, "pcm_mode": "auto"},   # Lautsprecher: 2.0 | 5.1 | 7.1; upmix: Stereo auf alle
    "sleep_action": "standby",      # standby | suspend | poweroff
    "maintenance_reboot": False,    # nachts (4 Uhr) neu starten, wenn die Box in Standby/unbenutzt ist – hält das System frisch
    "auto_standby_hours": 4,        # nach so vielen Stunden ohne Tastendruck Standby (0 = aus), wie beim Fernseher
    "power_key": "standby",         # Aus-Taste: standby (an/aus umschalten) | menu
    "android": {"wanted": False},
    # Sondertasten der Fernbedienung (z. B. waipu.tv: Netflix, YouTube, TV, Programm).
    # match: "key:KEY_NAME" oder "scan:<hex>" (für Tasten ohne Linux-Tastencode)
    "remote": {"mouse_android": True, "mappings": [
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


def atomic_write_json(path, data, mode=0o644, indent=2):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        # große Dateien (TV-Programm, Verlauf) kompakt schreiben – spart Platz und Ladezeit
        json.dump(data, fh, ensure_ascii=False, indent=indent, separators=None if indent else (",", ":"))
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
