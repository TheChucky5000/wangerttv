"""USB-Sticks und -Festplatten: automatisch einbinden (wie bei Windows), anzeigen und sicher auswerfen.

Eingebunden wird über udisks2 (wie bei jedem Linux-Desktop) unter /media/<benutzer>/<Name> – ohne Root-Rechte,
weil die TV-Sitzung die aktive lokale Sitzung ist.
"""
import json
import os
import re
import threading
import time

from . import config
from .util import bus, log, run, which

SUPPORTED = {"vfat", "exfat", "ntfs", "ntfs3", "ext2", "ext3", "ext4", "btrfs", "xfs", "iso9660", "udf", "hfsplus", "f2fs"}


def _lsblk():
    rc, out, _ = run(["lsblk", "-J", "-b", "-o",
                      "NAME,PATH,TYPE,FSTYPE,LABEL,SIZE,HOTPLUG,RM,TRAN,MOUNTPOINTS,PKNAME,MODEL,VENDOR"], timeout=10)
    if rc != 0:
        return []
    try:
        data = json.loads(out)
    except ValueError:
        return []
    flat = []

    def walk(devs, parent=None):
        for d in devs or []:
            d["_parent"] = parent
            flat.append(d)
            walk(d.get("children"), d)
    walk(data.get("blockdevices"))
    return flat


def _is_usb(d):
    top = d
    while top.get("_parent") is not None:
        top = top["_parent"]
    return (top.get("tran") == "usb" or bool(top.get("hotplug")) or bool(top.get("rm"))) and top.get("type") == "disk"


def _top(d):
    while d.get("_parent") is not None:
        d = d["_parent"]
    return d


def _mounts(d):
    return [m for m in (d.get("mountpoints") or []) if m]


class UsbManager:
    def __init__(self):
        self._known = {}          # Pfad -> Laufwerk (nur eingebundene)
        self._failed = set()      # Partitionen, die sich nicht einbinden ließen (nicht dauernd neu versuchen)
        self._ejected = set()     # ausgeworfene Laufwerke: nicht wieder einbinden, bis sie abgezogen wurden
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ Abfrage
    def drives(self):
        if config.DEV:
            return [{"path": "/dev/sdb1", "disk": "/dev/sdb", "label": "INTENSO", "mount": "/media/tv/INTENSO",
                     "size": 64 * 1000 ** 3, "free": 41 * 1000 ** 3, "fstype": "exfat", "model": "Intenso Speed Line"}]
        with self._lock:
            return list(self._known.values())

    def _scan(self):
        found, to_mount = {}, []
        for d in _lsblk():
            if not _is_usb(d) or d.get("type") not in ("part", "disk", "rom"):
                continue
            fs = (d.get("fstype") or "").lower()
            if fs not in SUPPORTED:
                continue
            mounts = _mounts(d)
            # Live-System (WangertTV vom Stick gestartet) und alles außerhalb von /media nicht anfassen
            if any(m.startswith("/run/live") or m in ("/", "/boot", "/boot/efi") for m in mounts):
                continue
            if not mounts:
                if _top(d).get("path") not in self._ejected:
                    to_mount.append(d)
                continue
            mount = next((m for m in mounts if m.startswith("/media/")), None)
            if mount:
                top = _top(d)
                try:
                    st = os.statvfs(mount)
                    free = st.f_bavail * st.f_frsize
                except OSError:
                    free = None
                name = d.get("label") or " ".join(x for x in ((top.get("vendor") or "").strip(),
                                                               (top.get("model") or "").strip()) if x) or "USB-Stick"
                found[d["path"]] = {"path": d["path"], "disk": top.get("path"), "label": name, "mount": mount,
                                    "size": int(d.get("size") or 0), "free": free, "fstype": fs,
                                    "model": (top.get("model") or "").strip()}
        return found, to_mount

    # ------------------------------------------------------------------ Einbinden / Auswerfen
    def _mount(self, d):
        path = d["path"]
        if path in self._failed:
            return
        rc, out, err = run(["udisksctl", "mount", "-b", path, "--no-user-interaction"], timeout=30)
        if rc == 0:
            log.info("USB eingebunden: %s", out.strip())
        else:
            self._failed.add(path)
            log.warning("USB %s ließ sich nicht einbinden: %s", path, (err or out).strip()[-200:])

    def eject(self, path):
        """Alle Partitionen des Laufwerks aushängen und den Stick abschalten (danach gefahrlos abziehen)."""
        if config.DEV:
            return {"ok": True}
        disk = None
        with self._lock:
            info = self._known.get(path)
        disk = (info or {}).get("disk") or re.sub(r"p?\d+$", "", path)
        ok = True
        for d in _lsblk():
            if _top(d).get("path") == disk and any(m.startswith("/media/") for m in _mounts(d)):
                rc, _, err = run(["udisksctl", "unmount", "-b", d["path"], "--no-user-interaction"], timeout=60)
                if rc != 0:
                    ok = False
                    log.warning("Aushängen von %s fehlgeschlagen: %s", d["path"], err.strip()[-200:])
        if ok:
            self._ejected.add(disk)
            run(["udisksctl", "power-off", "-b", disk, "--no-user-interaction"], timeout=30)
            log.info("USB-Laufwerk %s ausgeworfen", disk)
        self.refresh()
        return {"ok": ok, "message": "" if ok else "busy"}

    # ------------------------------------------------------------------ Überwachung
    def refresh(self):
        found, to_mount = self._scan()
        for d in to_mount:
            self._mount(d)
        if to_mount:
            found, _ = self._scan()
        with self._lock:
            added = [v for k, v in found.items() if k not in self._known]
            removed = [k for k in self._known if k not in found]
            self._known = found
        # Abgezogene Sticks dürfen beim nächsten Einstecken wieder versucht werden
        present = {d["path"] for d in _lsblk()}
        self._failed &= present
        self._ejected &= present
        if added or removed:
            bus.emit("usb", drives=list(found.values()), added=[a["label"] for a in added])

    def watch(self):
        if config.DEV or not which("udisksctl"):
            if not config.DEV:
                log.warning("udisksctl fehlt – USB-Sticks werden nicht automatisch eingebunden")
            return

        def loop():
            while True:
                try:
                    self.refresh()
                except Exception:  # noqa: BLE001
                    log.exception("USB-Überwachung")
                time.sleep(3)
        threading.Thread(target=loop, daemon=True, name="usb").start()
