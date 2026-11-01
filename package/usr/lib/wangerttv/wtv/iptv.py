"""IPTV: M3U-Senderliste laden, zwischenspeichern und für die Startseite aufbereiten."""
import os
import re
import threading
import time

from . import config, mock
from .config import atomic_write_json, read_json
from .util import http_get, log

CACHE = os.path.join(config.CACHE_DIR, "channels.json")
ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')


def parse_m3u(text):
    if not text.lstrip("﻿").lstrip().startswith("#EXTM3U"):
        raise ValueError("not_m3u")
    channels = []
    current = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#EXTINF"):
            head, _, title = line.partition(",")
            attrs = dict(ATTR_RE.findall(head))
            current = {
                "name": title.strip() or attrs.get("tvg-name", "").strip(),
                "logo": attrs.get("tvg-logo", ""),
                "group": attrs.get("group-title", ""),
                "tvg_id": attrs.get("tvg-id", ""),
                "radio": attrs.get("radio", "").lower() == "true",
            }
            chno = attrs.get("tvg-chno", "")
            if chno.isdigit():
                current["number"] = int(chno)
        elif line.startswith("#EXTGRP:") and current is not None and not current["group"]:
            current["group"] = line.split(":", 1)[1].strip()
        elif line and not line.startswith("#") and current is not None:
            if current["name"]:
                channels.append(current)
            current = None
    for i, ch in enumerate(channels):
        ch.setdefault("number", i + 1)
        if not ch["logo"].startswith(("http://", "https://")):
            ch["logo"] = ""  # nur Web-Logos – lokale Pfade kann die Oberfläche nicht laden
    return channels


def fetch(url):
    if url.startswith("/"):
        with open(url, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    return http_get(url, timeout=25).decode("utf-8", "replace")


def test(url):
    if config.DEV:
        chans = mock.channels()
        return {"ok": True, "count": len(chans), "sample": [c["name"] for c in chans[:5]],
                "groups": sorted({c["group"] for c in chans})}
    try:
        chans = parse_m3u(fetch(url.strip()))
    except ValueError as exc:
        return {"ok": False, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": str(exc)[:200]}
    groups = []
    for c in chans:
        if c["group"] and c["group"] not in groups:
            groups.append(c["group"])
    return {"ok": bool(chans), "count": len(chans), "sample": [c["name"] for c in chans[:5]], "groups": groups[:30],
            "message": "" if chans else "empty"}


class ChannelStore:
    def __init__(self, settings):
        self.settings = settings
        self._lock = threading.Lock()
        self._loading = False

    def channels(self, force=False):
        if config.DEV:
            return mock.channels()
        iptv = self.settings.get("iptv") or {}
        url = (iptv.get("m3u") or "").strip()
        if not url:
            return []
        cache = read_json(CACHE, {}) or {}
        max_age = max(1, int(iptv.get("refresh_hours") or 24)) * 3600
        fresh = cache.get("url") == url and time.time() - cache.get("ts", 0) < max_age
        if fresh and not force:
            return cache.get("channels", [])
        if not self._loading:
            threading.Thread(target=self._refresh, args=(url,), daemon=True).start()
        return cache.get("channels", []) if cache.get("url") == url else []

    def _refresh(self, url):
        with self._lock:
            self._loading = True
            try:
                chans = parse_m3u(fetch(url))
                atomic_write_json(CACHE, {"url": url, "ts": time.time(), "channels": chans[:3000]})
                from .util import bus
                bus.emit("channels")
            except Exception as exc:  # noqa: BLE001
                log.warning("Senderliste konnte nicht geladen werden: %s", exc)
            finally:
                self._loading = False
