# WangertTV für Kodi: gemeinsame Hilfen für den Dienst (service.py) und Tastenbefehle (default.py)
import calendar
import json
import os
import time
import urllib.request

import xbmc
import xbmcaddon
import xbmcvfs

ADDON = xbmcaddon.Addon("service.wangerttv")
ADDON_PATH = ADDON.getAddonInfo("path")
PROFILE = xbmcvfs.translatePath("special://profile/addon_data/service.wangerttv/")
STATE_FILE = os.path.join(PROFILE, "state.json")

AUDIO_NAMES = {"ac3": "Dolby Digital", "eac3": "Dolby Digital Plus", "truehd": "Dolby TrueHD", "dts": "DTS", "dca": "DTS",
               "dtshd_ma": "DTS-HD MA", "dtshd_hra": "DTS-HD HRA", "dts-hd": "DTS-HD", "aac": "AAC", "aac_latm": "AAC",
               "mp2": "MPEG", "mp3": "MP3", "mp1": "MPEG", "opus": "Opus", "flac": "FLAC", "vorbis": "Vorbis", "alac": "ALAC"}
CHANNEL_NAMES = {"1": "Mono", "2": "Stereo", "3": "2.1", "6": "5.1", "7": "6.1", "8": "7.1"}
HDR_NAMES = {"hdr10": "HDR10", "hdr10plus": "HDR10+", "dolbyvision": "Dolby Vision", "hlg": "HLG"}
VIDEO_CODECS = {"h264": "H.264", "avc1": "H.264", "hevc": "HEVC", "h265": "HEVC", "av1": "AV1", "vp9": "VP9",
                "mpeg2video": "MPEG-2", "mpeg4": "MPEG-4", "vc1": "VC-1"}
RESOLUTIONS = {"8k": "8K", "4k": "UHD", "2160": "UHD", "1440": "QHD", "1080": "Full HD", "720": "HD",
               "576": "SD", "540": "SD", "480": "SD"}

_state = {"mtime": None, "data": {}}


def state():
    """Einstellungen, die WangertTV für Kodi ablegt (Reihenfolge, Nummern, Logos, Dauer der Info-Leiste …)."""
    try:
        mtime = os.path.getmtime(STATE_FILE)
        if mtime != _state["mtime"]:
            with open(STATE_FILE, encoding="utf-8") as fh:
                _state.update(mtime=mtime, data=json.load(fh))
    except (OSError, ValueError):
        pass
    return _state["data"]


def t(key):
    """Wenige Texte der Info-Leiste – Sprache wie in WangertTV."""
    en = state().get("lang") == "en"
    return {"next": ("Next", "Danach"), "now": ("Now", "Jetzt"), "no_epg": ("No programme information", "Keine Programminformationen"),
            "until": ("until", "bis"), "min_left": ("min left", "Min. übrig"), "ok_hint": ("OK: switch", "OK: umschalten"),
            "loading": ("Loading …", "Wird geladen …")}[key][0 if en else 1]


def rpc(method, params=None):
    req = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        req["params"] = params
    try:
        return json.loads(xbmc.executeJSONRPC(json.dumps(req)))
    except (ValueError, TypeError):
        return {}


def api(path, body=None, timeout=3):
    """An den WangertTV-Dienst melden (lokal, Port aus state.json)."""
    port = int(state().get("port") or 8765)
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body or {}).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-WTV": "1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read() or b"{}")
    except Exception as exc:  # noqa: BLE001 – WangertTV nicht erreichbar: Kodi läuft trotzdem weiter
        xbmc.log(f"[WangertTV] {path}: {exc}", xbmc.LOGINFO)
        return None


def key(name):
    return (name or "").casefold().strip()


def current_channel():
    """(channelid, Name) des laufenden Senders oder (None, "")."""
    res = rpc("Player.GetItem", {"playerid": 1, "properties": ["channel"]})
    item = (res.get("result") or {}).get("item") or {}
    if item.get("type") == "channel" and item.get("id") is not None:
        return item["id"], item.get("channel") or item.get("label") or ""
    return None, xbmc.getInfoLabel("VideoPlayer.ChannelName")


def ordered_channels():
    """Kodi-Sender in WangertTV-Reihenfolge, ohne ausgeblendete -> [(channelid, Name, Nummer)]."""
    st = state()
    res = rpc("PVR.GetChannels", {"channelgroupid": "alltv", "properties": ["hidden", "channelnumber"]})
    kodi = [c for c in (res.get("result") or {}).get("channels") or [] if not c.get("hidden")]
    by_name = {key(c.get("label")): c for c in kodi}
    hidden = {key(n) for n in st.get("hidden") or []}
    ordered, seen = [], set()
    for n in st.get("order") or []:
        c = by_name.get(key(n))
        if c and key(n) not in hidden and c["channelid"] not in seen:
            ordered.append(c)
            seen.add(c["channelid"])
    ordered += [c for c in kodi if c["channelid"] not in seen and key(c.get("label")) not in hidden]
    numbers = st.get("numbers") or {}
    return [(c["channelid"], c.get("label", ""), numbers.get(key(c.get("label"))) or c.get("channelnumber") or i + 1)
            for i, c in enumerate(ordered)]


def local_ts(utc_text):
    """Zeit aus Kodis EPG ("2026-11-03 19:15:00", UTC) -> Unix-Zeit."""
    try:
        return calendar.timegm(time.strptime(utc_text, "%Y-%m-%d %H:%M:%S"))
    except (TypeError, ValueError):
        return 0


def hm(ts):
    return time.strftime("%H:%M", time.localtime(ts)) if ts else ""


def channel_info(channelid):
    """Name, Logo, laufende und nächste Sendung eines Senders (für die Vorschau beim Umschalten)."""
    res = rpc("PVR.GetChannelDetails", {"channelid": channelid,
                                        "properties": ["icon", "channelnumber", "broadcastnow", "broadcastnext"]})
    ch = (res.get("result") or {}).get("channeldetails") or {}

    def show(b):
        if not b:
            return None
        return {"title": b.get("title") or b.get("label") or "", "start": local_ts(b.get("starttime")),
                "end": local_ts(b.get("endtime")), "plot": b.get("plot") or b.get("plotoutline") or "",
                "episode": b.get("episodename") or ""}
    return {"name": ch.get("label", ""), "icon": ch.get("icon", ""), "now": show(ch.get("broadcastnow")),
            "next": show(ch.get("broadcastnext"))}


def logo_for(name, fallback=""):
    path = (state().get("logos") or {}).get(key(name))
    return path if path and os.path.isfile(path) else fallback


def audio_badges():
    """["Dolby Digital", "5.1", "Bitstream"] o. ä. für den laufenden Ton."""
    codec = (xbmc.getInfoLabel("VideoPlayer.AudioCodec") or "").lower()
    if not codec:
        return []
    out = [AUDIO_NAMES.get(codec, codec.upper().replace("_", " "))]
    ch = CHANNEL_NAMES.get(xbmc.getInfoLabel("VideoPlayer.AudioChannels"), "")
    if ch:
        out.append(ch)
    decoder = (xbmc.getInfoLabel("Player.Process(audiodecoder)") or "").lower()
    if decoder.startswith("pt") or "passthrough" in decoder:
        out.append("Bitstream")          # Dolby/DTS geht unverändert an den Receiver
    return out


def video_badges():
    """(Qualität, HDR, Codec) – z. B. ("UHD", "HDR10", "HEVC")."""
    res = (xbmc.getInfoLabel("VideoPlayer.VideoResolution") or "").lower()
    quality = RESOLUTIONS.get(res, "")
    if not quality:
        try:
            h = int(xbmc.getInfoLabel("Player.Process(videoheight)") or 0)
        except ValueError:
            h = 0
        quality = "UHD" if h >= 2000 else "Full HD" if h >= 1000 else "HD" if h >= 700 else "SD" if h else ""
    hdr = HDR_NAMES.get((xbmc.getInfoLabel("VideoPlayer.HdrType") or "").lower(), "")
    codec = (xbmc.getInfoLabel("VideoPlayer.VideoCodec") or "").lower()
    codec = VIDEO_CODECS.get(codec, codec.upper())
    return quality, hdr, codec


def stop_and_wait(timeout=3.0):
    """Wiedergabe beenden und warten, bis Kodi wirklich gestoppt hat."""
    if not xbmc.Player().isPlaying():
        return
    rpc("Player.Stop", {"playerid": 1})
    end = time.time() + timeout
    while time.time() < end and xbmc.Player().isPlaying():
        xbmc.sleep(100)
