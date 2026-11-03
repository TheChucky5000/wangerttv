"""IPTV: M3U-Senderliste laden, zwischenspeichern und für die Startseite aufbereiten."""
import os
import re
import threading
import time
import urllib.parse

from . import config, logos, mock
from .config import atomic_write_json, read_json
from .util import http_get, log

CACHE = os.path.join(config.CACHE_DIR, "channels.json")
PVR_CACHE = os.path.join(config.CACHE_DIR, "pvr-channels.json")   # Sender aus Kodi (z. B. waipu.tv)
EPG_CACHE = os.path.join(config.CACHE_DIR, "epg.json")             # TV-Programm aus Kodi (nächste Stunden)
EPG_HOURS = 30          # reicht vom Morgen bis zum Abend des nächsten Tages (TV-Planer, 20:15)
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


def _utc_ts(text):
    """Kodi liefert Sendezeiten als "YYYY-MM-DD HH:MM:SS" in UTC."""
    import calendar
    try:
        return calendar.timegm(time.strptime(text, "%Y-%m-%d %H:%M:%S"))
    except (TypeError, ValueError):
        return None


_epg_mem = {"mtime": None, "data": {}}
_epg_lock = threading.Lock()


def epg_data():
    """TV-Programm aus dem Zwischenspeicher – nur neu einlesen, wenn sich die Datei geändert hat."""
    with _epg_lock:
        try:
            mtime = os.path.getmtime(EPG_CACHE)
        except OSError:
            return {}
        if mtime != _epg_mem["mtime"]:
            _epg_mem.update(mtime=mtime, data=(read_json(EPG_CACHE, {}) or {}).get("channels") or {})
        return _epg_mem["data"]


def now_next(entries, now=None):
    """Aus einer Liste [start, ende, titel] die laufende und die nächste Sendung wählen."""
    now = now or time.time()
    cur = nxt = None
    for e in entries:
        start, end, title = e[0], e[1], e[2]
        item = {"title": title, "start": start, "end": end}
        if len(e) > 3 and e[3]:
            item["desc"] = e[3]
        if start <= now < end:
            cur = item
        elif start >= now:
            if cur is not None or nxt is None:
                nxt = item
            break
    return cur, nxt


def kodi_image(url):
    """Kodi-Bildadresse ("image://https%3a%2f%2f…/") in eine normale Web-Adresse umwandeln."""
    url = url or ""
    if url.startswith("image://"):
        url = urllib.parse.unquote(url[len("image://"):]).rstrip("/")
    return url if url.startswith(("http://", "https://")) else ""


class ChannelStore:
    PVR_MAX_AGE = 6 * 3600

    def __init__(self, settings):
        self.settings = settings
        self._lock = threading.Lock()
        self._loading = False
        self._pvr_lock = threading.Lock()
        self._epg_lock = threading.Lock()
        self.kodi = None                              # wird von Core gesetzt
        self.kodi_running = staticmethod(lambda: False)

    def refresh_epg(self, wait=0):
        """TV-Programm (laufende und kommende Sendungen) aus Kodi holen und zwischenspeichern.

        Kodi kennt das Programm von waipu.tv und IPTV (XMLTV). Gespeichert werden die nächsten Stunden – so
        stimmt "Jetzt läuft" auf der Startseite auch, wenn Kodi gerade nicht läuft."""
        if self.kodi is None or not self._epg_lock.acquire(blocking=False):
            return False
        try:
            kodi = self.kodi
            end_wait = time.time() + wait
            chans = []
            while True:
                if kodi.ready():
                    res = kodi.rpc("PVR.GetChannels", {"channelgroupid": "alltv", "properties": ["hidden"]}, timeout=15)
                    chans = [c for c in (res.get("result") or {}).get("channels") or [] if not c.get("hidden")]
                if chans or time.time() >= end_wait or not self.kodi_running():
                    break
                time.sleep(5)
            if not chans:
                return False
            now = time.time()
            horizon = now + EPG_HOURS * 3600
            data = {}
            for ch in chans[:600]:
                if not self.kodi_running():
                    break
                res = kodi.rpc("PVR.GetBroadcasts", {"channelid": ch["channelid"], "properties": ["title", "starttime", "endtime", "plot", "episodename"],
                                                    "limits": {"start": 0, "end": 400}}, timeout=10)
                items = []
                for b in (res.get("result") or {}).get("broadcasts") or []:
                    start, end = _utc_ts(b.get("starttime")), _utc_ts(b.get("endtime"))
                    if start is None or end is None or end <= now or start > horizon:
                        continue
                    plot = (b.get("plot") or "").strip().replace("\n", " ")
                    ep = (b.get("episodename") or "").strip()
                    if ep and ep.lower() not in plot.lower():
                        plot = f"{ep} – {plot}" if plot else ep
                    items.append([start, end, (b.get("title") or b.get("label") or "").strip()[:120], plot[:280], ep[:120]])
                if items:
                    items.sort()
                    data[(ch.get("label") or "").casefold().strip()] = items
            if data:
                atomic_write_json(EPG_CACHE, {"ts": now, "channels": data}, indent=None)
                _epg_mem.update(mtime=None)
                log.info("TV-Programm für %d Sender übernommen", len(data))
                from .util import bus
                bus.emit("channels")
            return bool(data)
        finally:
            self._epg_lock.release()

    def _with_epg(self, chans):
        epg = epg_data()
        if not epg:
            return chans
        now = time.time()
        out = []
        for c in chans:
            entries = epg.get(c["name"].casefold().strip())
            if entries:
                cur, nxt = now_next(entries, now)
                c = dict(c, now=cur, next=nxt)
            out.append(c)
        return out

    def schedule(self, at, window=15 * 60):
        """Was läuft um <at> (Unix-Zeit) auf allen Sendern – Sendung, die dann läuft oder bis zu 15 min später beginnt.
        Für den TV-Planer ("Heute um 20:15") und die Erinnerungen."""
        chans = self.channels()
        if config.DEV:
            return _mock_schedule(chans, at)
        epg = epg_data()
        out = []
        for c in chans:
            entries = epg.get(c["name"].casefold().strip()) or []
            hit = None
            for e in entries:
                start, end = e[0], e[1]
                if start <= at < end and at - start < 20 * 60 or at <= start <= at + window:
                    hit = e
                    break
                if start <= at < end:
                    hit = e                        # läuft schon länger – nehmen, falls nichts Passenderes kommt
            if hit:
                out.append({"name": c["name"], "number": c.get("number"), "logo": c.get("logo", ""), "group": c.get("group", ""),
                            "quality": c.get("quality", ""), "title": hit[2], "start": hit[0], "end": hit[1],
                            "desc": hit[3] if len(hit) > 3 else ""})
        return out

    def guide(self, start, hours=4):
        """TV-Programm für die Programmübersicht: alle (sichtbaren) Sender mit ihren Sendungen im Zeitfenster."""
        end = start + hours * 3600
        chans = self.channels()
        if config.DEV:
            import random
            rnd = random.Random(7)
            titles = ["Tagesschau", "Tatort", "Wer weiß denn sowas?", "Terra X", "Die Simpsons", "Navy CIS", "Bares für Rares",
                      "Fußball: Bundesliga", "Spielfilm: Inception", "Nachrichten", "Dokumentation", "Quiz-Show", "Kochshow"]
            out = []
            for c in chans:
                t, entries = start - rnd.randint(0, 50) * 60, []
                while t < end:
                    d = rnd.choice([15, 30, 45, 60, 90, 105])
                    entries.append([t, t + d * 60, rnd.choice(titles), "Beschreibung der Sendung – Handlung, Gäste und Themen."])
                    t += d * 60
                out.append({"name": c["name"], "number": c.get("number"), "logo": c.get("logo", ""), "quality": c.get("quality", ""),
                            "entries": entries})
            return out
        epg = epg_data()
        out = []
        for c in chans:
            entries = [e for e in epg.get(c["name"].casefold().strip()) or [] if e[1] > start and e[0] < end]
            out.append({"name": c["name"], "number": c.get("number"), "logo": c.get("logo", ""), "quality": c.get("quality", ""),
                        "entries": entries})
        return out

    def broadcasts(self, name, hours=30):
        """Alle Sendungen eines Senders der nächsten Stunden."""
        if config.DEV:
            base = int(time.time()) // 1800 * 1800
            return [[base + i * 3600, base + (i + 1) * 3600, f"Sendung {i + 1}"] for i in range(hours)]
        entries = epg_data().get(name.casefold().strip()) or []
        limit = time.time() + hours * 3600
        return [e for e in entries if e[1] > time.time() and e[0] < limit]

    def waipu_configured(self):
        w = self.settings.get("waipu") or {}
        return bool(w.get("username") and w.get("password"))

    def channels(self, force=False):
        if config.DEV:
            return self.arrange(mock.channels())
        chans = self._iptv_channels(force)
        if self.waipu_configured():
            pvr = read_json(PVR_CACHE, {}) or {}
            if self.kodi_running() and (force or time.time() - pvr.get("ts", 0) > self.PVR_MAX_AGE):
                threading.Thread(target=self.refresh_pvr, daemon=True).start()
            known = {c["name"].casefold() for c in chans}
            chans = chans + [c for c in pvr.get("channels", []) if c["name"].casefold() not in known]
        if self.kodi_running():
            try:
                epg_ts = os.path.getmtime(EPG_CACHE)
            except OSError:
                epg_ts = 0
            if force or time.time() - epg_ts > 1800:
                threading.Thread(target=self.refresh_epg, daemon=True).start()
        return self._with_epg(self.arrange(chans))

    def all_channels(self):
        """Alle Sender inkl. ausgeblendeter (für "Sender sortieren")."""
        return self.arrange(mock.channels() if config.DEV else self._raw_channels(), include_hidden=True)

    def _raw_channels(self):
        chans = self._iptv_channels(False)
        if self.waipu_configured():
            known = {c["name"].casefold() for c in chans}
            chans = chans + [c for c in (read_json(PVR_CACHE, {}) or {}).get("channels", []) if c["name"].casefold() not in known]
        return chans

    def arrange(self, chans, include_hidden=False):
        """Eigene Reihenfolge, ausgeblendete Sender, offizielle Logos und HD/UHD-Kennzeichen anwenden."""
        tv = self.settings.get("tv") or {}
        order = [n.casefold() for n in tv.get("order") or []]
        hidden = {n.casefold() for n in tv.get("hidden") or []}
        pos = {n: i for i, n in enumerate(order)}
        chans = sorted(chans, key=lambda c: (pos.get(c["name"].casefold(), len(pos)),))   # stabil: Rest bleibt wie geliefert
        out = []
        for c in chans:
            is_hidden = c["name"].casefold() in hidden
            if is_hidden and not include_hidden:
                continue
            c = dict(c, quality=c.get("quality") or logos.quality(c["name"]))
            if is_hidden:
                c["hidden"] = True
            if tv.get("logos", "official") == "official":
                official = logos.logo_for(c["name"])
                if official:
                    c["provider_logo"] = c.get("logo", "")
                    c["logo"] = official
            out.append(c)
        if order and tv.get("renumber", True):
            n = 0
            for c in out:
                if not c.get("hidden"):
                    n += 1
                    c["number"] = n            # Nummern folgen der eigenen Reihenfolge (Direktwahl per Zifferntasten)
        return out

    def refresh_pvr(self, wait=0):
        """Senderliste aus Kodi holen (waipu.tv & Co.) und für die Startseite merken.

        Geht nur, solange Kodi läuft – die waipu.tv-Sender kennt nur das Kodi-Addon. wait: so lange (Sekunden)
        warten, bis Kodi die Sender geladen hat (direkt nach dem Start von Kodi dauert das etwas)."""
        if self.kodi is None or not self._pvr_lock.acquire(blocking=False):
            return False
        try:
            end = time.time() + wait
            while True:
                chans = self._fetch_pvr()
                if chans or time.time() >= end or not self.kodi_running():
                    break
                time.sleep(5)
            if not chans:
                return False
            old = (read_json(PVR_CACHE, {}) or {}).get("channels")
            atomic_write_json(PVR_CACHE, {"ts": time.time(), "channels": chans})
            if old != chans:
                log.info("%d Sender aus Kodi für die Startseite übernommen", len(chans))
                from .util import bus
                bus.emit("channels")
            return True
        finally:
            self._pvr_lock.release()

    def _fetch_pvr(self):
        kodi = self.kodi
        if not kodi.ready():
            return []
        # Welche PVR-Clients gibt es? IPTV-Simple-Sender kommen schon aus der M3U-Liste – nur den Rest übernehmen.
        clients = (kodi.rpc("PVR.GetClients").get("result") or {}).get("clients") or []
        skip = {c.get("clientid") for c in clients if c.get("addonid") == "pvr.iptvsimple"}
        names = {c.get("clientid"): c.get("label") or c.get("addonid", "") for c in clients}
        res = kodi.rpc("PVR.GetChannels", {"channelgroupid": "alltv",
                                           "properties": ["channelnumber", "icon", "thumbnail", "hidden", "clientid"]},
                       timeout=15)
        out = []
        for ch in (res.get("result") or {}).get("channels") or []:
            if ch.get("hidden") or ch.get("clientid") in skip:
                continue
            name = (ch.get("label") or "").strip()
            if not name:
                continue
            client = names.get(ch.get("clientid"), "")
            out.append({
                "name": name,
                "logo": kodi_image(ch.get("icon") or ch.get("thumbnail")),
                "group": "waipu.tv" if "waipu" in client.lower() else (client or "Live-TV"),
                "number": ch.get("channelnumber") or len(out) + 1,
                "tvg_id": "", "radio": False, "source": "kodi",
            })
        return out

    def _iptv_channels(self, force=False):
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


def _mock_schedule(chans, at):
    films = ["Tatort", "Der Bergdoktor", "Wer wird Millionär?", "Jurassic World", "Bares für Rares – Abendshow", "Terra X",
             "Harry Potter und der Stein der Weisen", "Navy CIS", "Das große Backen", "Fast & Furious 7", "Doku: Planet Erde",
             "Quizduell", "Die Höhle der Löwen", "Grey's Anatomy", "The Masked Singer", "ZDF-Krimi", "Star Trek", "Die Simpsons",
             "Asphalt-Cowboys", "Mord mit Aussicht"]
    return [{"name": c["name"], "number": c.get("number"), "logo": c.get("logo", ""), "group": c.get("group", ""),
             "title": films[i % len(films)], "start": int(at), "end": int(at) + (90 + i % 3 * 15) * 60}
            for i, c in enumerate(chans)]
