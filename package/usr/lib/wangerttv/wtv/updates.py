"""Updater-Steuerung: Status lesen, Prüfung/Aktualisierung über System-Units anstoßen."""
import threading
import time

from . import config
from .config import atomic_write_json, read_json
from .system import systemctl_start
from .util import bus

EMPTY = {"state": "idle", "progress": 0, "message": "", "last_check": 0, "last_update": 0,
         "available": {}, "packages": [], "total": 0, "reboot_required": False, "log": []}


def status():
    st = read_json(config.UPDATE_STATUS, {}) or {}
    return {**EMPTY, **st}


def check():
    if config.DEV:
        threading.Thread(target=_fake, args=(True,), daemon=True).start()
        return True
    return systemctl_start("wtv-update-check.service")


def run_all():
    if config.DEV:
        threading.Thread(target=_fake, args=(False,), daemon=True).start()
        return True
    return systemctl_start("wtv-update.service")


def watch():
    """Meldet Statusänderungen per Ereignis an die Oberfläche."""
    def loop():
        last = None
        while True:
            st = status()
            key = (st["state"], st["progress"], st["message"], st["total"])
            if key != last:
                last = key
                bus.emit("update", status=st)
            time.sleep(1.5)
    threading.Thread(target=loop, daemon=True, name="update-watch").start()


def _fake(check_only):
    base = status()
    steps = [("checking", 5, "Paketquellen werden geprüft …")]
    if not check_only:
        steps += [("running", 20, "System-Pakete werden geladen …"), ("running", 45, "kodi wird installiert …"),
                  ("running", 65, "google-chrome-stable wird installiert …"), ("running", 80, "Flatpak-Apps …"),
                  ("running", 92, "Android wird geprüft …")]
    for state, prog, msg in steps:
        base.update(state=state, progress=prog, message=msg)
        atomic_write_json(config.UPDATE_STATUS, base)
        time.sleep(1.2)
    now = time.time()
    if check_only:
        base.update(state="idle", progress=0, message="", last_check=now, total=7,
                    available={"apt": 5, "flatpak": 2, "android": 0, "wangerttv": 0},
                    packages=["kodi", "kodi-data", "google-chrome-stable", "linux-image-amd64", "libcec7",
                              "com.spotify.Client", "tv.plex.PlexHTPC"])
    else:
        base.update(state="done", progress=100, message="", last_update=now, last_check=now, total=0,
                    available={"apt": 0, "flatpak": 0, "android": 0, "wangerttv": 0}, packages=[],
                    reboot_required=True)
    atomic_write_json(config.UPDATE_STATUS, base)
