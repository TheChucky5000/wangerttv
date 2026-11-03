"""Albumcover zum laufenden Titel (Radio: ICY-Titel "Interpret - Titel").

Suche über die freie iTunes-Suche (ohne Schlüssel), ersatzweise Deezer. Ergebnisse werden zwischengespeichert;
Nachrichten, Werbung und Senderkennungen werden gar nicht erst gesucht.
"""
import json
import re
import threading
import urllib.parse

from . import config
from .util import http_get, log

_cache = {}
_lock = threading.Lock()
SKIP = re.compile(r"(?i)\b(werbung|nachrichten|news|verkehr|wetter|jingle|commercial|advert|station ?id|live ?stream|"
                  r"am mikrofon|im studio|moderation|podcast|interview|sendung)\b")
UA = {"User-Agent": "WangertTV/1.0 (TV-Box)"}


def split_title(text):
    """"Interpret - Titel" -> (Interpret, Titel). Manche Sender schicken "Titel von Interpret"."""
    text = re.sub(r"\s+", " ", (text or "").replace("|", " - ")).strip(" -")
    for sep in (" - ", " – ", " — ", " / "):
        if sep in text:
            a, b = text.split(sep, 1)
            return a.strip(), b.strip()
    m = re.match(r"(.+?) von (.+)$", text)
    if m:
        return m.group(2).strip(), m.group(1).strip()
    return "", text


def _clean(s):
    s = re.sub(r"\((?:feat|ft|featuring|radio edit|single version|remaster)[^)]*\)", "", s, flags=re.I)
    s = re.sub(r"\[[^\]]*\]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _itunes(artist, title):
    q = urllib.parse.urlencode({"term": f"{artist} {title}".strip(), "entity": "song", "limit": 5, "country": "DE"})
    data = json.loads(http_get(f"https://itunes.apple.com/search?{q}", timeout=8, max_bytes=512 * 1024, headers=UA).decode())
    results = data.get("results") or []
    a = artist.casefold()
    pick = next((r for r in results if a and (a in r.get("artistName", "").casefold() or r.get("artistName", "").casefold() in a)), None)
    pick = pick or (results[0] if results and not artist else None)
    url = (pick or {}).get("artworkUrl100", "")
    return url.replace("100x100bb", "600x600bb") if url.startswith("https://") else ""


def _deezer(artist, title):
    q = urllib.parse.urlencode({"q": f"{artist} {title}".strip(), "limit": 3})
    data = json.loads(http_get(f"https://api.deezer.com/search?{q}", timeout=8, max_bytes=512 * 1024, headers=UA).decode())
    a = artist.casefold()
    for r in data.get("data") or []:
        name = ((r.get("artist") or {}).get("name") or "").casefold()
        if not a or a in name or name in a:
            url = (r.get("album") or {}).get("cover_xl") or ""
            if url.startswith("https://"):
                return url
    return ""


def lookup(text, station=""):
    """Cover-URL zum Titel oder "" (nichts gefunden, Werbung/Nachrichten, offline)."""
    artist, title = split_title(text)
    if not title or len(title) < 2 or SKIP.search(text) or (station and station.casefold() in text.casefold()):
        return ""
    if config.DEV:
        return ""
    key = f"{artist}|{title}".casefold()
    with _lock:
        if key in _cache:
            return _cache[key]
    url = ""
    for fn in (_itunes, _deezer):
        try:
            url = fn(_clean(artist), _clean(title))
        except Exception as exc:  # noqa: BLE001
            log.debug("Cover-Suche (%s): %s", fn.__name__, exc)
        if url:
            break
    with _lock:
        if len(_cache) > 800:
            _cache.clear()
        _cache[key] = url
    return url
