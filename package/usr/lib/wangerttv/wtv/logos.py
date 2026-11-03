"""Offizielle Senderlogos (Projekt "tv-logos", für dunkle Hintergründe gemacht).

Die Liste der verfügbaren Logos wird mitgeliefert (logos/germany.json); das Bild selbst wird erst geladen, wenn ein
Sender es braucht, und dann in ~/.cache/wangerttv/logos gespeichert. Bis dahin bleibt das Logo des Anbieters.
"""
import functools
import json
import os
import re
import threading
import unicodedata

from . import config
from .util import http_get, log

INDEX = os.path.join(config.LIB, "logos", "germany.json")
CACHE = os.path.join(config.CACHE_DIR, "logos")
_index = {"files": None, "source": ""}
_pending = set()
_failed = set()
_lock = threading.Lock()

# Schreibweisen der Anbieter -> Name im Logo-Projekt
ALIASES = {
    "prosieben": "pro-sieben", "pro7": "pro-sieben", "prosieben-maxx": "pro-sieben-maxx", "pro7-maxx": "pro-sieben-maxx",
    "prosieben-fun": "pro-sieben-fun", "sat1": "sat-1", "sat-1-gold": "sat-1-gold", "sat1-gold": "sat-1-gold",
    "sat1-emotions": "sat-1-emotions", "rtlzwei": "rtl-zwei", "rtl2": "rtl-zwei", "rtl-ii": "rtl-zwei", "rtlup": "rtl-up",
    "rtl-plus": "rtl-up", "super-rtl": "rtl-super", "superrtl": "rtl-super", "kabel1": "kabel-eins", "kabeleins": "kabel-eins",
    "kabel-1": "kabel-eins", "kabel-eins-doku": "kabel-eins-doku", "n-tv": "ntv", "zdfneo": "zdf-neo", "zdfinfo": "zdf-info",
    "zdf-neo": "zdf-neo", "ard": "das-erste", "erste": "das-erste", "ard-das-erste": "das-erste", "br-fernsehen": "br-fernsehen",
    "bayerisches-fernsehen": "br-fernsehen", "br-sued": "br-sud", "hr-fernsehen": "hr", "hessischer-rundfunk": "hr",
    "swr-fernsehen": "swr", "swr-bw": "swr-bw", "swr-rp": "swr", "swr-fernsehen-bw": "swr-bw", "swr-fernsehen-rp": "swr",
    "mdr-fernsehen": "mdr", "mdr-sachsen": "mdr", "rbb-fernsehen": "rbb-fernsehen", "sr-fernsehen": "sr-fernsehen",
    "sr": "sr-fernsehen", "ndr-fernsehen": "ndr", "wdr-fernsehen": "wdr", "welt-nachrichtensender": "welt", "n24": "welt",
    "n24-doku": "n24-doku", "eurosport": "eurosport-1", "eurosport1": "eurosport-1", "eurosport2": "eurosport-2",
    "sport-1": "sport1", "dazn-1": "dazn1", "nickelodeon": "nick", "nick-jr": "nick-jr", "toggo": "toggo-plus",
    "tele5": "tele-5", "comedy-central": "comedy-central", "deluxe": "deluxe-music", "3-sat": "3sat", "drei-sat": "3sat",
    "servus-tv": "servus-tv-deutchland", "servustv": "servus-tv-deutchland", "bibel-tv": "bibel-tv", "kika": "kika",
    "ard-alpha": "ard-alpha", "dw": "dw", "deutsche-welle": "dw", "radio-bremen-tv": "radio-bremen", "euronews": "euronews-deutsch",
    "rtl-nitro": "nitro", "rtlnitro": "nitro", "voxup": "vox-up", "vox-up": "vox-up", "ntv": "ntv", "ntv-de": "ntv",
}
STRIP = {"hd", "uhd", "fhd", "sd", "4k", "de", "deutschland", "germany", "tv", "(de)", "live"}


def _files():
    with _lock:
        if _index["files"] is None:
            try:
                with open(INDEX, encoding="utf-8") as fh:
                    data = json.load(fh)
                _index.update(files=set(data.get("files") or []), source=data.get("source", ""))
            except (OSError, ValueError):
                _index.update(files=set(), source="")
        return _index["files"]


def slug(name):
    s = unicodedata.normalize("NFKD", (name or "").lower().replace("ß", "ss"))
    s = "".join(c for c in s if not unicodedata.combining(c))          # ä -> a (wie im Logo-Projekt: "munchen")
    s = s.replace("+", " plus ").replace("&", " and ").replace(".", " ")
    return "-".join(re.findall(r"[a-z0-9]+", s))


@functools.lru_cache(maxsize=4096)
def match(name):
    """Dateiname des passenden Logos oder None (Ergebnis wird je Sendername gemerkt)."""
    files = _files()
    if not files or not name:
        return None
    tries = []
    base_words = slug(name).split("-")
    for strip in ({"hd", "uhd", "fhd", "sd", "4k", "de", "live"}, STRIP):   # erst "TV" behalten (Bibel TV), dann weglassen
        words = list(base_words)
        while words and words[-1] in strip:
            words.pop()
        words = [w for w in words if w not in ("hd", "uhd", "fhd")] if len(words) > 1 else words   # "WDR HD Köln"
        for n in range(len(words), 0, -1):            # "zdf neo hd" -> zdf-neo, zdf
            base = "-".join(words[:n])
            joined = "".join(words[:n])
            for cand in (base, ALIASES.get(base), ALIASES.get(joined), joined):
                if cand and cand not in tries:
                    tries.append(cand)
    for cand in tries:
        fname = f"{cand}-de.png"
        if fname in files:
            return fname
    return None


def cached_path(fname):
    path = os.path.join(CACHE, fname)
    return path if os.path.isfile(path) else None


def logo_for(name):
    """URL für die Oberfläche (/chlogo/<datei>), sobald das Logo geladen ist – sonst None (Laden läuft an)."""
    fname = match(name)
    if not fname:
        return None
    if cached_path(fname):
        return f"/chlogo/{fname}"
    _fetch_later(fname)
    return None


def _fetch_later(fname):
    if config.DEV:
        return
    with _lock:
        if fname in _pending or fname in _failed:
            return
        _pending.add(fname)
    threading.Thread(target=_fetch, args=(fname,), daemon=True).start()


def _fetch(fname):
    try:
        data = http_get(_index["source"] + fname, timeout=15, max_bytes=2 * 1024 * 1024)
        if data[:8] != b"\x89PNG\r\n\x1a\n":
            raise ValueError("kein PNG")
        os.makedirs(CACHE, exist_ok=True)
        tmp = os.path.join(CACHE, fname + ".tmp")
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, os.path.join(CACHE, fname))
    except Exception as exc:  # noqa: BLE001
        log.info("Senderlogo %s nicht geladen: %s", fname, exc)
        with _lock:
            _failed.add(fname)
    finally:
        with _lock:
            _pending.discard(fname)
        if not _pending:
            from .util import bus
            bus.emit("channels")          # Oberfläche lädt die Senderliste mit den neuen Logos


def quality(name):
    """Bildqualität laut Sendername (vor der Wiedergabe): UHD, HD oder SD."""
    words = set(re.findall(r"[a-z0-9]+", (name or "").lower()))
    if words & {"uhd", "4k"}:
        return "UHD"
    if words & {"hd", "fhd"}:
        return "HD"
    return ""
