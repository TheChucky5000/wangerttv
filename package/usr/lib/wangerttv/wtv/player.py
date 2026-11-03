"""WangertTV Player: Mediathek (Ordner, USB-Sticks), Wiedergabe mit mpv und "Weiterschauen".

Wiedergabe-Kern ist mpv (FFmpeg): spielt praktisch jedes Video-, Ton-, Bild- und Untertitelformat, nutzt die
Hardware-Dekodierung und reicht Dolby/DTS an den Receiver durch. Die Bedienoberfläche für die Fernbedienung liegt
in /usr/lib/wangerttv/player/scripts/wangerttv.lua.
"""
import os
import tempfile

from . import config, system
from .config import atomic_write_json, read_json
from .util import which

PLAYER_DIR = os.path.join(config.LIB, "player")
PROGRESS = os.path.join(config.CACHE_DIR, "player-progress.json")
WATCH_LATER = os.path.join(config.CACHE_DIR, "player", "watch_later")
HOME = os.path.expanduser("~")
IPC = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or config.CACHE_DIR, "wangerttv-player.sock")

VIDEO = {"mkv", "mp4", "m4v", "avi", "mov", "wmv", "flv", "webm", "mpg", "mpeg", "m2ts", "mts", "ts", "vob", "ogv",
         "3gp", "divx", "xvid", "rm", "rmvb", "asf", "f4v", "y4m", "dv", "mxf", "iso", "m2v", "hevc", "h264", "av1", "ivf"}
AUDIO = {"mp3", "flac", "m4a", "aac", "ogg", "oga", "opus", "wav", "wma", "alac", "ape", "aiff", "aif", "dts", "ac3",
         "eac3", "mka", "wv", "tta", "mpc", "dsf", "dff", "mid", "midi", "amr", "caf", "m4b", "spx", "thd", "mlp"}
IMAGE = {"jpg", "jpeg", "png", "gif", "webp", "bmp", "tif", "tiff", "heic", "heif", "avif", "jxl", "svg"}
PLAYLIST = {"m3u", "m3u8", "pls", "cue", "xspf"}
SUBS = {"srt", "ass", "ssa", "sub", "idx", "vtt", "sup"}


def kind(name):
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in VIDEO:
        return "video"
    if ext in AUDIO:
        return "audio"
    if ext in IMAGE:
        return "image"
    if ext in PLAYLIST:
        return "playlist"
    return None


def allowed(path):
    """Nur eigene Ordner und eingesteckte Laufwerke – nichts aus dem System."""
    real = os.path.realpath(path)
    return any(real == r or real.startswith(r + os.sep) for r in (HOME, "/media", "/mnt", "/run/media"))


def sources(usb_drives=()):
    """Startpunkte der Mediathek: USB-Laufwerke und die üblichen Ordner im persönlichen Verzeichnis."""
    out = []
    for d in usb_drives:
        out.append({"id": "usb", "name": d.get("label") or "USB", "path": d.get("mount"), "icon": "usb"})
    names = [("Videos", "film"), ("Filme", "film"), ("Musik", "music"), ("Music", "music"), ("Bilder", "image"),
             ("Pictures", "image"), ("Downloads", "download")]
    home = "/home/tv" if config.DEV else HOME
    for name, ic in names:
        p = os.path.join(home, name)
        if os.path.isdir(p) or (config.DEV and name in ("Videos", "Musik", "Bilder", "Downloads")):
            out.append({"id": name.lower(), "name": name, "path": p, "icon": ic})
    out.append({"id": "home", "name": "", "path": home, "icon": "home"})
    return out


def _disc(path):
    """Blu-ray- oder DVD-Ordnerstruktur erkennen."""
    if os.path.isdir(os.path.join(path, "BDMV")):
        return "bluray"
    if os.path.isdir(os.path.join(path, "VIDEO_TS")):
        return "dvd"
    return None


def browse(path):
    if config.DEV:
        return _mock_browse(path)
    if not path or not allowed(path) or not os.path.isdir(path):
        return {"ok": False, "message": "not_found"}
    prog = {e.get("path"): e for e in (read_json(PROGRESS, []) or []) if isinstance(e, dict)}
    dirs, files = [], []
    try:
        entries = list(os.scandir(path))
    except OSError as exc:
        return {"ok": False, "message": str(exc)[:200]}
    for e in entries:
        if e.name.startswith("."):
            continue
        try:
            if e.is_dir():
                dirs.append({"name": e.name, "path": e.path, "type": _disc(e.path) or "dir"})
                continue
            k = kind(e.name)
            if not k:
                continue
            st = e.stat()
            item = {"name": e.name, "path": e.path, "type": k, "size": st.st_size}
            p = prog.get(e.path)
            if p and p.get("duration"):
                item["progress"] = 1.0 if p.get("done") else round(min(1.0, p.get("pos", 0) / p["duration"]), 3)
            files.append(item)
        except OSError:
            continue
    key = lambda x: x["name"].casefold()  # noqa: E731
    dirs.sort(key=key)
    files.sort(key=key)
    parent = os.path.dirname(path.rstrip("/"))
    return {"ok": True, "path": path, "name": os.path.basename(path.rstrip("/")) or path,
            "parent": parent if allowed(parent) and parent != path else None, "disc": _disc(path),
            "items": dirs + files}


def recent():
    """Angefangene Videos/Musik ("Weiterschauen"), neueste zuerst – nur was es noch gibt."""
    if config.DEV:
        return [{"path": "/media/tv/INTENSO/Filme/Inception (2010).mkv", "title": "Inception (2010)", "pos": 3120,
                 "duration": 8880, "progress": 0.35}]
    out = []
    for e in read_json(PROGRESS, []) or []:
        if not isinstance(e, dict) or e.get("done") or not e.get("duration"):
            continue
        if not os.path.exists(e.get("path", "")):
            continue
        out.append({**e, "progress": round(min(1.0, e.get("pos", 0) / e["duration"]), 3)})
        if len(out) >= 12:
            break
    return out


def forget(path):
    """Eintrag aus "Weiterschauen" entfernen (die Datei selbst bleibt)."""
    entries = [e for e in (read_json(PROGRESS, []) or []) if isinstance(e, dict) and e.get("path") != path]
    atomic_write_json(PROGRESS, entries)
    return True


def _spdif(settings):
    """Dolby/DTS an den Receiver durchreichen – dieselben Formate wie in Kodi."""
    kodi = settings.get("kodi") or {}
    if not kodi.get("passthrough"):
        return []
    rcv = system.hdmi_receiver() or {}
    known = rcv.get("formats") if rcv else None   # leere Liste = Gerät kann nur PCM
    fmts = ["ac3", "dts"] + (["eac3", "truehd", "dts-hd"] if kodi.get("hd_passthrough") else [])
    return [f for f in fmts if known is None or f.replace("-", "") in known]


def command(settings, path, start_over=False):
    """mpv-Befehl für eine Datei (Ordner = Wiedergabeliste ab dieser Datei) oder einen ganzen Ordner."""
    if not which("mpv") and not config.DEV:
        raise RuntimeError("not_installed")
    if config.DEV:
        return ["mpv", f"--config-dir={PLAYER_DIR}", path]
    if not allowed(path) or not os.path.exists(path):
        raise RuntimeError("not_found")
    os.makedirs(WATCH_LATER, exist_ok=True)
    cmd = ["mpv", f"--config-dir={PLAYER_DIR}", f"--watch-later-dir={WATCH_LATER}", "--title=WangertTV Player",
           "--wayland-app-id=wangerttv-player", f"--input-ipc-server={IPC}"]
    spdif = _spdif(settings)
    if spdif:
        cmd.append("--audio-spdif=" + ",".join(spdif))
        speakers = (settings.get("audio") or {}).get("speakers") or "2.0"
        rcv = system.hdmi_receiver() or {}
        stereo_link = int(rcv.get("pcm_channels") or 2) <= 2 or _hdmi_stereo_only()
        if speakers != "2.0" and "ac3" in spdif and stereo_link:
            # HDMI kann nur Stereo-PCM: Mehrkanal-Ton (AAC 5.1, Opus 5.1 …) in Dolby Digital 5.1 umrechnen
            cmd.append("--af-append=lavcac3enc=tospdif=yes:minch=3")
    if start_over:
        cmd.append("--no-resume-playback")
    disc = _disc(path) if os.path.isdir(path) else None
    if disc == "bluray":
        return cmd + [f"--bluray-device={path}", "bd://"]
    if disc == "dvd":
        return cmd + [f"--dvd-device={path}", "dvd://"]
    if os.path.isdir(path):
        return cmd + [path]
    k = kind(os.path.basename(path))
    if k == "playlist":
        return cmd + [path]
    # Datei: alle Dateien derselben Art im Ordner als Wiedergabeliste, Start bei dieser Datei
    folder = os.path.dirname(path)
    try:
        siblings = sorted((e.path for e in os.scandir(folder)
                           if e.is_file() and not e.name.startswith(".") and kind(e.name) == k),
                          key=lambda p: os.path.basename(p).casefold())
    except OSError:
        siblings = [path]
    if len(siblings) <= 1 or path not in siblings:
        return cmd + [path]
    fd, plist = tempfile.mkstemp(prefix="wtv-playlist-", suffix=".m3u", dir=config.CACHE_DIR)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("#EXTM3U\n" + "\n".join(siblings) + "\n")
    return cmd + [f"--playlist={plist}", f"--playlist-start={siblings.index(path)}"]


def _hdmi_stereo_only():
    name = system.hdmi_sink()
    ch = system._sink_channels(name) if name else None
    return bool(ch) and ch <= 2


def env(settings, start_over=False):
    return {"WTV_LANG": settings.get("lang") or "de", "WTV_PLAYER_PROGRESS": PROGRESS,
            "WTV_START_OVER": "1" if start_over else "0"}


def _mock_browse(path):
    base = path or "/media/tv/INTENSO"
    if base.endswith("Filme"):
        items = [{"name": n, "path": f"{base}/{n}", "type": "video", "size": s, **({"progress": p} if p else {})}
                 for n, s, p in [("Inception (2010).mkv", 14_200_000_000, 0.35), ("Interstellar (2014).mkv", 19_800_000_000, 1.0),
                                 ("Der Pate (1972).mp4", 4_100_000_000, None), ("Avatar – The Way of Water (2022).mkv", 32_000_000_000, None)]]
    elif base.endswith("Musik"):
        items = [{"name": n, "path": f"{base}/{n}", "type": "audio", "size": 9_000_000} for n in
                 ("01 – Intro.flac", "02 – Nachtfahrt.flac", "03 – Neonlicht.mp3")]
    else:
        items = [{"name": "Filme", "path": f"{base}/Filme", "type": "dir"},
                 {"name": "Musik", "path": f"{base}/Musik", "type": "dir"},
                 {"name": "Urlaub 2025", "path": f"{base}/Urlaub 2025", "type": "dir"},
                 {"name": "Konzert.mkv", "path": f"{base}/Konzert.mkv", "type": "video", "size": 2_400_000_000},
                 {"name": "Strand.jpg", "path": f"{base}/Strand.jpg", "type": "image", "size": 3_200_000}]
    parent = os.path.dirname(base) if base.count("/") > 3 else None
    return {"ok": True, "path": base, "name": os.path.basename(base), "parent": parent, "disc": None, "items": items}


def ipc(command, timeout=1.0):
    """Befehl an den laufenden Player (mpv-JSON-IPC), z. B. ["set_property", "pause", True]."""
    if config.DEV or not os.path.exists(IPC):
        return None
    import json
    import socket
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(IPC)
            sock.sendall((json.dumps({"command": command}) + "\n").encode())
            data = sock.recv(4096)
        return json.loads(data.split(b"\n", 1)[0] or b"{}")
    except (OSError, ValueError):
        return None
