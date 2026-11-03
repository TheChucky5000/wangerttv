"""Intelligente Startseite: lernt lokal, was zu welcher Tageszeit gesehen und gehört wird.

Alles bleibt auf der Box (~/.config/wangerttv/history.json) – nichts geht ins Internet. Gespeichert wird je Sender,
App und Radiosender ein Gewicht pro Stunde (getrennt nach Werktag und Wochenende) und je Sendung, wie lange und an
wie vielen Tagen sie lief, dazu die zuletzt gesehenen Folgen (für "Neue Folge"). Alte Gewohnheiten verblassen
langsam (täglich ~3 %), damit sich die Vorschläge anpassen. Abschaltbar und löschbar in den Einstellungen.
"""
import atexit
import os
import threading
import time

from . import config
from .config import atomic_write_json, read_json
from .util import log

FILE = os.path.join(config.CONF_DIR, "history.json")
KINDS = ("channel", "app", "radio")
DECAY = 0.97
MAX_ITEMS = 400
MAX_SHOWS = 300
SKIP_APPS = {"settings", "files", "share", "tvplaner", "livetv", "tvguide"}

_lock = threading.RLock()
_data = None
_dirty = 0.0
_saved = 0.0


def _empty():
    return {"v": 1, "day": time.strftime("%Y-%m-%d"), "items": {}, "shows": {}}


def _load():
    global _data
    if _data is None:
        data = read_json(FILE, None)
        _data = data if isinstance(data, dict) and data.get("v") == 1 else _empty()
        _decay()
    return _data


def _decay():
    today = time.strftime("%Y-%m-%d")
    if _data.get("day") == today:
        return
    try:
        days = max(1, (time.mktime(time.strptime(today, "%Y-%m-%d")) - time.mktime(time.strptime(_data["day"], "%Y-%m-%d"))) // 86400)
    except (KeyError, ValueError):
        days = 1
    f = DECAY ** min(days, 60)
    for key in list(_data["items"]):
        it = _data["items"][key]
        it["h"] = [round(v * f, 2) for v in it["h"]]
        it["total"] = round(it.get("total", 0) * f, 2)
        if it["total"] < 0.3:
            del _data["items"][key]
    for title in list(_data["shows"]):
        sh = _data["shows"][title]
        sh["minutes"] = round(sh.get("minutes", 0) * f, 1)
        if sh["minutes"] < 2 and time.time() - sh.get("last", 0) > 30 * 86400:
            del _data["shows"][title]
    _data["day"] = today


def bucket(ts=None):
    """Stunde 0–23, Wochenende ab 24."""
    lt = time.localtime(ts or time.time())
    return lt.tm_hour + (24 if lt.tm_wday >= 5 else 0)


def record(kind, name, weight=1.0, title="", episode="", ts=None, meta=None):
    """Nutzung merken. weight: Minuten (Fernsehen, Ticks des Kodi-Add-ons) bzw. 3 für einen Start."""
    if kind not in KINDS or not name or not isinstance(name, str):
        return
    if kind == "app" and name in SKIP_APPS:
        return
    name = name.strip()[:120]
    with _lock:
        data = _load()
        _decay()
        key = f"{kind}:{name.casefold()}"
        it = data["items"].setdefault(key, {"kind": kind, "name": name, "h": [0.0] * 48, "total": 0.0})
        b = bucket(ts)
        it["h"][b] = round(it["h"][b] + weight, 2)
        it["total"] = round(it.get("total", 0) + weight, 2)
        it["last"] = int(ts or time.time())
        if meta:
            it["meta"] = meta
        title = (title or "").strip()[:120]
        if kind == "channel" and title:
            sh = data["shows"].setdefault(title.casefold(), {"title": title, "minutes": 0, "days": [], "channels": {}, "eps": []})
            sh["minutes"] = round(sh["minutes"] + weight, 1)
            day = time.strftime("%Y-%m-%d", time.localtime(ts or time.time()))
            if day not in sh["days"]:
                sh["days"] = (sh["days"] + [day])[-12:]
            sh["channels"][name] = sh["channels"].get(name, 0) + 1
            if episode and episode not in sh["eps"]:
                sh["eps"] = (sh["eps"] + [episode.strip()[:120]])[-30:]
            sh["last"] = int(ts or time.time())
        _prune(data)
        _mark()


def _prune(data):
    if len(data["items"]) > MAX_ITEMS:
        keep = sorted(data["items"].items(), key=lambda kv: kv[1].get("total", 0), reverse=True)[:MAX_ITEMS]
        data["items"] = dict(keep)
    if len(data["shows"]) > MAX_SHOWS:
        keep = sorted(data["shows"].items(), key=lambda kv: kv[1].get("minutes", 0), reverse=True)[:MAX_SHOWS]
        data["shows"] = dict(keep)


def _mark():
    global _dirty
    _dirty = _dirty or time.time()
    if time.time() - _saved > 120:
        save()


def save(force=False):
    """Höchstens alle 2 Minuten schreiben (schont die SSD); beim Beenden mit force=True."""
    global _dirty, _saved
    with _lock:
        if _data is None or (not _dirty and not force):
            return
        try:
            atomic_write_json(FILE, _data, mode=0o600, indent=None)
            _dirty, _saved = 0.0, time.time()
        except OSError as exc:
            log.warning("Verlauf nicht gespeichert: %s", exc)


def clear():
    global _data
    with _lock:
        _data = _empty()
        save(force=True)


def score(it, ts=None):
    """Wie gut passt ein Eintrag zur jetzigen Tageszeit? Nachbarstunden zählen mit, der andere Tagestyp wenig."""
    b = bucket(ts)
    base, hour = (24 if b >= 24 else 0), b % 24
    other = 24 - base
    h = it["h"]
    s = 0.0
    for d, w in ((0, 1.0), (-1, 0.55), (1, 0.55), (-2, 0.2), (2, 0.2)):
        hh = (hour + d) % 24
        s += h[base + hh] * w + h[other + hh] * w * 0.25
    s += it.get("total", 0) * 0.02                    # allgemeine Vorliebe als Rückfall
    age_days = (time.time() - it.get("last", 0)) / 86400
    return s * (1.0 if age_days < 3 else 0.85 if age_days < 14 else 0.6)


def suggest(kind=None, limit=12, ts=None, min_score=0.6):
    with _lock:
        data = _load()
        items = [it for it in data["items"].values() if kind is None or it["kind"] == kind]
        scored = sorted(((score(it, ts), it) for it in items), key=lambda x: x[0], reverse=True)
    return [{"kind": it["kind"], "name": it["name"], "score": round(s, 2), "meta": it.get("meta")}
            for s, it in scored if s >= min_score][:limit]


def favorite_shows(limit=30):
    """Sendungen, die an mindestens zwei Tagen zusammen mindestens 20 Minuten liefen."""
    with _lock:
        shows = [dict(sh) for sh in _load()["shows"].values() if len(sh.get("days", [])) >= 2 and sh.get("minutes", 0) >= 20]
    shows.sort(key=lambda sh: (len(sh["days"]), sh["minutes"]), reverse=True)
    return shows[:limit]


def seen_episode(title, episode):
    with _lock:
        sh = _load()["shows"].get((title or "").casefold())
        return bool(sh and episode and episode in sh.get("eps", []))


def summary():
    with _lock:
        data = _load()
        counts = {k: sum(1 for it in data["items"].values() if it["kind"] == k) for k in KINDS}
        return {"items": counts, "shows": len(data["shows"]), "favorites": len(favorite_shows(100)),
                "size": os.path.getsize(FILE) if os.path.exists(FILE) else 0}


atexit.register(save)
