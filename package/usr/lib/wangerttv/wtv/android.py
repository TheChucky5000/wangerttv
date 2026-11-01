"""Android-Apps & Google Play Store über Waydroid."""
import os
import re
import time

from . import config, icons, mock
from .config import read_json
from .system import systemctl_start
from .util import log, run, spawn, which

HIDDEN = {
    "com.android.documentsui", "com.android.camera2", "org.lineageos.jelly", "org.lineageos.etar",
    "com.android.gallery3d", "com.android.contacts", "com.android.deskclock", "org.lineageos.recorder",
    "com.android.calculator2", "org.lineageos.eleven", "com.android.inputmethod.latin",
}


def binder_available():
    """Prüft, ob der Kernel den Android-Treiber (binder) hat – geladen, eingebaut oder als Modul.

    Ohne Root-Rechte und ohne modinfo (liegt in /usr/sbin, für den Benutzer tv nicht im PATH).
    Debian 13 liefert binder als Modul binder_linux (ohne binderfs).
    """
    if any(os.path.exists(p) for p in ("/dev/binder", "/dev/binderfs", "/sys/module/binder_linux")):
        return True
    try:
        with open("/proc/filesystems", encoding="utf-8") as fh:
            if re.search(r"\sbinder$", fh.read(), re.M):
                return True
    except OSError:
        pass
    moddir = os.path.join("/lib/modules", os.uname().release)
    for name in ("modules.dep", "modules.builtin"):
        try:
            with open(os.path.join(moddir, name), encoding="utf-8", errors="replace") as fh:
                if "drivers/android/binder" in fh.read():
                    return True
        except OSError:
            continue
    return False


class Android:
    def __init__(self):
        self._session_proc = None
        self._cache = (0, None)

    def status(self):
        """Status mit kurzem Zwischenspeicher (wird für jede App-Kachel abgefragt)."""
        ts, cached = self._cache
        if cached is not None and time.time() - ts < 5:
            return dict(cached)
        st = self._status()
        self._cache = (time.time(), st)
        return dict(st)

    def _status(self):
        if config.DEV:
            st = read_json(config.ANDROID_STATUS, {}) or {}
            return {"installed": True, "initialized": st.get("state") == "done", "running": False,
                    "state": st.get("state", "none"), "progress": st.get("progress", 0),
                    "message": st.get("message", ""), "android_id": st.get("android_id", ""), "binder": True}
        installed = bool(which("waydroid"))
        initialized = os.path.isdir("/var/lib/waydroid/images") or os.path.exists("/var/lib/waydroid/waydroid.cfg")
        st = read_json(config.ANDROID_STATUS, {}) or {}
        running = False
        if installed and initialized:
            out = run(["waydroid", "status"], timeout=8)[1]
            running = bool(re.search(r"Session:\s*RUNNING", out))
        binder = binder_available()
        return {
            "installed": installed, "initialized": initialized, "running": running,
            "state": st.get("state", "done" if initialized else "none"), "progress": st.get("progress", 0),
            "message": st.get("message", ""), "android_id": st.get("android_id", ""), "binder": binder,
        }

    def init(self):
        self._cache = (0, None)
        if config.DEV:
            from .config import atomic_write_json
            import threading

            def fake():
                for p in range(0, 101, 10):
                    atomic_write_json(config.ANDROID_STATUS, {"state": "running" if p < 100 else "done", "progress": p,
                                                              "message": "Android wird heruntergeladen …",
                                                              "android_id": "" if p < 100 else "3f8a2b91c04d77e5"})
                    time.sleep(0.6)
            threading.Thread(target=fake, daemon=True).start()
            return True
        return systemctl_start("wtv-android@init.service")

    def request_id(self):
        return True if config.DEV else systemctl_start("wtv-android@id.service")

    def ensure_session(self, timeout=60):
        if config.DEV:
            return True
        if self.status()["running"]:
            return True
        if not (self._session_proc and self._session_proc.poll() is None):
            self._session_proc = spawn(["waydroid", "session", "start"],
                                       logfile=os.path.join(config.LOG_DIR, "waydroid-session.log"))
        end = time.time() + timeout
        while time.time() < end:
            if re.search(r"Session:\s*RUNNING", run(["waydroid", "status"], timeout=8)[1]):
                return True
            time.sleep(1.5)
        log.warning("Waydroid-Sitzung startet nicht")
        return False

    def apps(self):
        if config.DEV:
            return mock.android_apps() if self.status()["initialized"] else []
        st = self.status()
        if not (st["initialized"] and st["running"]):
            return []
        out = run(["waydroid", "app", "list"], timeout=20)[1]
        apps, cur = [], {}
        for line in out.splitlines() + [""]:
            line = line.strip()
            if line.startswith("Name:"):
                cur = {"name": line.split(":", 1)[1].strip()}
            elif line.startswith("packageName:"):
                cur["package"] = line.split(":", 1)[1].strip()
            elif not line and cur.get("package"):
                if cur["package"] not in HIDDEN:
                    if icons.android_icon(cur["package"]):
                        cur["icon"] = f"/appicon/android:{cur['package']}"
                    apps.append(cur)
                cur = {}
        return sorted(apps, key=lambda a: a["name"].lower())

    def launch(self, package=None):
        if config.DEV:
            return True
        if not self.ensure_session():
            return False
        if package:
            if not re.fullmatch(r"[A-Za-z0-9_.]+", package):
                return False
            return run(["waydroid", "app", "launch", package], timeout=20)[0] == 0
        spawn(["waydroid", "show-full-ui"], logfile=os.path.join(config.LOG_DIR, "waydroid-ui.log"))
        return True

    def stop(self):
        if config.DEV:
            return True
        run(["waydroid", "session", "stop"], timeout=20)
        return True
