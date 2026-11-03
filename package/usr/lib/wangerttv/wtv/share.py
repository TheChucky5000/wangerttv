"""Vom Handy teilen: Fotos, Videos, Musik und Links per QR-Code vom Handy auf den Fernseher schicken.

Das Handy öffnet http://<box>:8766/s/<token> (Token aus den Einstellungen, neu erzeugbar). Dateien landen in
~/Handy/<Datum>/ und werden auf Wunsch sofort im WangertTV Player gezeigt; Links öffnen YouTube bzw. den Browser.
"""
import os
import re
import secrets
import shutil
import time

from . import config, player
from .util import bus, log

FOLDER = os.path.join(player.HOME, "Handy")
MAX_FILE = 16 * 1024 ** 3          # 16 GB pro Datei
KEEP_FREE = 1024 ** 3              # mindestens 1 GB frei lassen
CHUNK = 1024 * 1024


def token(settings, renew=False):
    tok = settings.get("share", "token")
    if renew or not tok:
        tok = secrets.token_urlsafe(12)
        settings.patch({"share": {"token": tok}})
    return tok


def clean_name(name):
    name = os.path.basename((name or "").replace("\\", "/")).strip()
    name = re.sub(r"[\x00-\x1f<>:\"|?*]", "_", name)[:180].lstrip(".")
    return name or f"Datei-{time.strftime('%H%M%S')}"


def receive(name, length, stream):
    """Speichert eine hochgeladene Datei (Rohdaten, bekannte Länge). Liefert (ok, pfad oder Fehler)."""
    if length <= 0 or length > MAX_FILE:
        return False, "too_large"
    day = os.path.join(FOLDER, time.strftime("%Y-%m-%d"))
    os.makedirs(day, exist_ok=True)
    if shutil.disk_usage(day).free - length < KEEP_FREE:
        return False, "no_space"
    name = clean_name(name)
    base, ext = os.path.splitext(name)
    target, n = os.path.join(day, name), 2
    while os.path.exists(target):
        target = os.path.join(day, f"{base} ({n}){ext}")
        n += 1
    part = target + ".part"
    left = length
    try:
        with open(part, "wb") as fh:
            while left > 0:
                buf = stream.read(min(CHUNK, left))
                if not buf:
                    raise OSError("Verbindung abgebrochen")
                fh.write(buf)
                left -= len(buf)
        os.rename(part, target)
    except OSError as exc:
        try:
            os.remove(part)
        except OSError:
            pass
        log.warning("Vom Handy: Übertragung abgebrochen (%s): %s", name, exc)
        return False, "aborted"
    log.info("Vom Handy empfangen: %s (%d KB)", os.path.relpath(target, FOLDER), length // 1024)
    bus.emit("share", what="file", path=target, name=os.path.basename(target), ftype=player.kind(target) or "file")
    return True, target


def recent(limit=40):
    """Zuletzt empfangene Dateien (neueste zuerst)."""
    if config.DEV:
        return [{"name": f"IMG_20261103_{i:04d}.jpg", "path": f"{FOLDER}/2026-11-03/IMG_{i}.jpg", "type": "image",
                 "size": 2_400_000, "mtime": int(time.time()) - i * 600} for i in range(6)] + \
               [{"name": "VID_20261103.mp4", "path": f"{FOLDER}/2026-11-03/VID.mp4", "type": "video", "size": 84_000_000, "mtime": int(time.time()) - 4000}]
    out = []
    for root, _, files in os.walk(FOLDER):
        for f in files:
            if f.endswith(".part"):
                continue
            p = os.path.join(root, f)
            try:
                st = os.stat(p)
            except OSError:
                continue
            out.append({"name": f, "path": p, "type": player.kind(f) or "file", "size": st.st_size, "mtime": int(st.st_mtime)})
    out.sort(key=lambda e: -e["mtime"])
    return out[:limit]


def youtube_id(url):
    m = re.search(r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/|embed/)|youtu\.be/)([\w-]{11})", url or "")
    return m.group(1) if m else None
