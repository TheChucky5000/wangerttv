"""Internetradio: tausende Sender aus der freien Radio-Browser-Datenbank (radio-browser.info).

Wiedergabe im Hintergrund mit mpv (ohne Bild) – das Radio läuft weiter, während man auf der Startseite oder in
anderen Apps ist. Titel/Interpret kommen aus den ICY-Metadaten des Senders ("Läuft gerade").
"""
import gzip
import json
import os
import random
import socket
import threading
import time
import urllib.parse

from . import config, coverart, radiodb
from .config import atomic_write_json
from .util import bus, http_get, log, spawn, which

IPC = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or config.CACHE_DIR, "wangerttv-radio.sock")
UA = {"User-Agent": "WangertTV/1.0 (TV-Box)"}
GENRES = ["pop", "rock", "news", "schlager", "80s", "90s", "oldies", "dance", "electronic", "hip hop", "jazz", "classical",
          "chillout", "metal", "country", "kids", "sport", "christmas"]
BUNDLED = os.path.join(config.LIB, "radio", "stations-de.json.gz")
CACHE = os.path.join(config.CACHE_DIR, "radio-de.json")
REFRESH_DAYS = 7
FALLBACK_SERVERS = ["de1.api.radio-browser.info", "de2.api.radio-browser.info"]

_db = {"ts": 0, "stations": [], "loaded": False}
_db_lock = threading.Lock()
_servers = {"list": [], "ts": 0}
_cache = {}
_cache_lock = threading.Lock()


# ------------------------------------------------------------------ lokale Datenbank (alle Sender in Deutschland)
def _load_db():
    with _db_lock:
        if _db["loaded"]:
            return _db
        best = {"ts": 0, "stations": []}
        for path, opener in ((BUNDLED, lambda p: gzip.open(p, "rt", encoding="utf-8")), (CACHE, lambda p: open(p, encoding="utf-8"))):
            try:
                with opener(path) as fh:
                    data = json.load(fh)
                if data.get("ts", 0) > best["ts"] and len(data.get("stations") or []) > 100:
                    best = data
            except (OSError, ValueError):
                continue
        _db.update(ts=best["ts"], stations=best["stations"], loaded=True)
        return _db


def db_info():
    d = _load_db()
    return {"count": len(d["stations"]), "ts": d["ts"]}


def _local():
    return _load_db()["stations"]


def _match_genre(st, tag):
    words = radiodb.GENRES.get(tag, [tag])
    tags = [t.strip() for t in (st.get("tags") or "").split(",")]
    return any(w in tags for w in words) or any(w in st.get("name", "").lower() for w in words if len(w) > 4)


def states():
    counts = {}
    for st in _local():
        if st.get("state"):
            counts[st["state"]] = counts.get(st["state"], 0) + 1
    return [{"name": n, "count": counts.get(n, 0)} for n in radiodb.BUNDESLAENDER if counts.get(n)]


# ------------------------------------------------------------------ Online-Abfrage (mit Wiederholungen)
def _server_list():
    """Aktuelle Server von radio-browser.info (die Liste ändert sich; früher gab es mehrere, zuletzt nur de1)."""
    if _servers["list"] and time.time() - _servers["ts"] < 6 * 3600:
        return _servers["list"]
    names = []
    for host in ("all.api.radio-browser.info",) + tuple(FALLBACK_SERVERS):
        try:
            data = json.loads(http_get(f"https://{host}/json/servers", timeout=8, headers=UA).decode())
            names = sorted({d.get("name") for d in data if d.get("name")})
            if names:
                break
        except Exception:  # noqa: BLE001
            continue
    _servers.update(list=names or FALLBACK_SERVERS, ts=time.time() if names else 0)
    return _servers["list"]


def _api(path, params=None, ttl=1800, tries=4, max_bytes=8 * 1024 * 1024):
    if config.DEV:
        return _mock(path, params)
    query = urllib.parse.urlencode(params or {})
    key = f"{path}?{query}"
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    servers = _server_list()[:]
    random.shuffle(servers)
    last = None
    for attempt in range(tries):
        host = servers[attempt % len(servers)]
        try:
            data = json.loads(http_get(f"https://{host}/json/{path}?{query}", timeout=15, max_bytes=max_bytes,
                                       headers=UA).decode("utf-8", "replace"))
            with _cache_lock:
                _cache[key] = (time.time(), data)
            return data
        except Exception as exc:  # noqa: BLE001 – der Server bricht gelegentlich Verbindungen ab: kurz warten, nochmal
            last = exc
            time.sleep(0.6 * (attempt + 1))
    log.info("Radio-Datenbank nicht erreichbar: %s", last)
    raise RuntimeError("offline")


def refresh_db(force=False):
    """Alle deutschen Sender neu laden (seitenweise) und lokal speichern. Läuft wöchentlich im Hintergrund."""
    if config.DEV or (not force and time.time() - db_info()["ts"] < REFRESH_DAYS * 86400):
        return False
    raw, off = [], 0
    try:
        while True:
            page = _api("stations/search", {"countrycode": "DE", "hidebroken": "true", "order": "clickcount", "reverse": "true",
                                            "limit": 1000, "offset": off}, ttl=0, tries=6)
            raw += page
            if len(page) < 1000 or off > 30000:
                break
            off += 1000
    except RuntimeError:
        log.info("Radiosender-Liste: Aktualisierung verschoben (Server nicht erreichbar)")
        return False
    stations = radiodb.build(raw)
    if len(stations) < 500:
        return False
    data = {"ts": int(time.time()), "country": "DE", "stations": stations}
    atomic_write_json(CACHE, data)
    with _db_lock:
        _db.update(ts=data["ts"], stations=stations, loaded=True)
    log.info("Radiosender-Liste aktualisiert: %d Sender in Deutschland", len(stations))
    return True


def start_refresh():
    def loop():
        time.sleep(180)
        while True:
            try:
                refresh_db()
            except Exception:  # noqa: BLE001
                log.exception("Radiosender-Liste")
            time.sleep(12 * 3600)
    threading.Thread(target=loop, daemon=True, name="radio-db").start()


# ------------------------------------------------------------------ Listen für die Oberfläche
def top(country="DE", limit=80):
    return _local()[:limit]


def genre(tag, country="DE"):
    tag = (tag or "").lower()
    return [s for s in _local() if _match_genre(s, tag)][:120]


def by_state(state):
    return [s for s in _local() if s.get("state") == state][:200]


def search(text):
    text = (text or "").strip().lower()[:80]
    if len(text) < 2:
        return []
    words = text.split()
    local = [s for s in _local() if all(w in f"{s['name']} {s.get('tags', '')} {s.get('state', '')}".lower() for w in words)]
    local.sort(key=lambda s: (not s["name"].lower().startswith(text), -s.get("pop", 0)))
    if len(local) >= 12:
        return local[:120]
    # wenig gefunden: zusätzlich weltweit online suchen (z. B. ausländische Sender)
    try:
        known = {s["id"] for s in local}
        online = [_slim(st) for st in _api("stations/search", {"name": text, "hidebroken": "true", "order": "clickcount",
                                                                "reverse": "true", "limit": 60})]
        local += [s for s in online if s["id"] not in known and s["url"]]
    except RuntimeError:
        pass
    return local[:120]


def _slim(st):
    s = radiodb.slim(st)
    s["country"] = st.get("country") or ""
    return s


class Radio:
    """Spielt einen Sender im Hintergrund (mpv ohne Bild) und meldet Titel/Interpret."""

    def __init__(self, settings, nowplaying_cb=None):
        self.settings = settings
        self.proc = None
        self.station = None
        self.title = ""
        self.cover = ""                          # Albumcover zum laufenden Titel (iTunes/Deezer)
        self._lock = threading.Lock()
        self.nowplaying_cb = nowplaying_cb      # Core: "Läuft gerade" setzen
        self.before_play = None                 # Core: andere Musik (Spotify) anhalten

    def state(self):
        playing = bool(self.proc and self.proc.poll() is None)
        return {"playing": playing, "station": self.station if playing else None, "title": self.title if playing else "",
                "cover": self.cover if playing else ""}

    def play(self, station):
        if not isinstance(station, dict) or not str(station.get("url", "")).startswith(("http://", "https://")):
            return {"ok": False, "message": "bad_station"}
        st = {k: station.get(k) for k in ("id", "name", "url", "favicon", "tags", "country", "codec", "bitrate")}
        if not which("mpv") and not config.DEV:
            return {"ok": False, "message": "not_installed"}
        self.stop(emit=False)
        if self.before_play:
            try:
                self.before_play()
            except Exception:  # noqa: BLE001
                log.exception("before_play")
        with self._lock:
            self.station, self.title, self.cover = st, "", ""
            self.proc = self._spawn(st)
        self._remember(st)
        if st.get("id") and not config.DEV:
            threading.Thread(target=self._click, args=(st["id"],), daemon=True).start()
        threading.Thread(target=self._watch, args=(self.proc,), daemon=True, name="radio").start()
        self._emit()
        log.info("Radio: %s", st.get("name"))
        return {"ok": True}

    def stop(self, emit=True):
        with self._lock:
            proc, self.proc = self.proc, None
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except Exception:  # noqa: BLE001
                proc.kill()
        self.title, self.cover = "", ""
        if emit:
            self._emit()
        return {"ok": True}

    def favorite(self, station, on):
        favs = [f for f in (self.settings.get("radio", "favorites") or []) if isinstance(f, dict) and f.get("id") != station.get("id")]
        if on:
            favs.append({k: station.get(k) for k in ("id", "name", "url", "favicon", "tags", "country", "codec", "bitrate")})
        self.settings.patch({"radio": {"favorites": favs[:100]}})
        return {"ok": True, "favorites": favs}

    def _remember(self, st):
        recent = [s for s in (self.settings.get("radio", "recent") or []) if isinstance(s, dict) and s.get("id") != st.get("id")]
        self.settings.patch({"radio": {"recent": [st] + recent[:11], "last": st}})

    @staticmethod
    def _click(uuid):
        """Klick zählen (so wird die Senderliste für alle besser) – Fehler egal."""
        try:
            http_get(f"https://{random.choice(_server_list())}/json/url/{urllib.parse.quote(uuid)}", timeout=6, headers=UA)
        except Exception:  # noqa: BLE001
            pass

    def _ipc(self, command):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(1.5)
                sock.connect(IPC)
                sock.sendall((json.dumps({"command": command}) + "\n").encode())
                return json.loads(sock.recv(65536).split(b"\n", 1)[0] or b"{}")
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _spawn(st):
        if config.DEV:
            return _FakeProc()
        # Stream-Abbrüche abfangen: mpv verbindet sich bei kurzen Aussetzern selbst neu (stream-lavf-o reconnect)
        cmd = ["mpv", "--no-config", "--no-video", "--no-terminal", "--idle=no", "--ao=pipewire",
               "--volume=100", "--cache=yes", "--cache-secs=20", "--demuxer-max-bytes=8MiB", "--network-timeout=20",
               "--stream-lavf-o=reconnect=1,reconnect_streamed=1,reconnect_delay_max=10",
               f"--input-ipc-server={IPC}", "--title=WangertTV Radio", st["url"]]
        return spawn(cmd, logfile=os.path.join(config.LOG_DIR, "radio.log"))

    def _watch(self, proc, retries=0):
        """Titel aus den Sender-Metadaten (ICY) abfragen, bis der Sender gewechselt/gestoppt wird."""
        started = time.time()
        while proc is self.proc and proc.poll() is None:
            time.sleep(4)
            if config.DEV:
                continue
            meta = (self._ipc(["get_property", "metadata"]) or {}).get("data") or {}
            title = str(meta.get("icy-title") or meta.get("StreamTitle") or meta.get("title") or "").strip()
            if title and title != self.title and title.lower() != (self.station or {}).get("name", "").lower():
                self.title, self.cover = title[:200], ""
                self._emit()
                threading.Thread(target=self._find_cover, args=(self.title,), daemon=True).start()
        if proc is self.proc and proc is not None:
            # Sender abgebrochen (Netz weg, Server-Aussetzer): automatisch neu verbinden, solange er vorher lief
            if retries < 5 and (time.time() - started > 15 or retries == 0) and self.station:
                log.info("Radio-Stream unterbrochen (%s) – verbinde neu (%d)", self.station.get("name"), retries + 1)
                time.sleep(min(10, 2 * (retries + 1)))
                with self._lock:
                    if proc is not self.proc:
                        return                       # inzwischen gestoppt/anderer Sender
                    self.proc = self._spawn(self.station)
                    new_proc = self.proc
                self._watch(new_proc, retries + 1 if time.time() - started < 60 else 0)
                return
            log.warning("Radio-Stream beendet (%s)", (self.station or {}).get("name"))
            with self._lock:
                self.proc = None
            self._emit()
            bus.emit("toast", key="radio_failed")

    def _find_cover(self, title):
        url = coverart.lookup(title, (self.station or {}).get("name", ""))
        if url and title == self.title:              # Titel inzwischen gewechselt? Dann nicht mehr setzen
            self.cover = url
            self._emit()

    def _emit(self):
        st = self.state()
        if self.nowplaying_cb:
            self.nowplaying_cb(st)
        bus.emit("radio", **st)


class _FakeProc:
    """DEV: tut so, als liefe ein Sender."""
    returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.returncode = -9


def _mock(path, params):
    names = [("SWR3", "pop, news"), ("Bayern 3", "pop, hits"), ("1LIVE", "pop, youth"), ("Antenne Bayern", "hits"),
             ("radioeins", "alternative"), ("Deutschlandfunk", "news, talk"), ("Radio Bob!", "rock"), ("Klassik Radio", "classical"),
             ("sunshine live", "electronic, dance"), ("Schlager Radio", "schlager"), ("FFH", "pop"), ("NDR 2", "pop, rock")]
    q = (params or {}).get("name", "").lower()
    return [{"stationuuid": f"mock-{i}", "name": n, "url_resolved": f"https://example.invalid/{i}.mp3", "favicon": "",
             "tags": tg, "country": "Germany", "countrycode": "DE", "codec": "MP3", "bitrate": 128}
            for i, (n, tg) in enumerate(names) if not q or q in n.lower()]
