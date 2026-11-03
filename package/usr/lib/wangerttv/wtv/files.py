"""Dateimanager: Dateien auf USB-Sticks/Festplatten und im persönlichen Ordner verwalten.

Sicherheit: nur innerhalb des persönlichen Ordners und eingesteckter Laufwerke (wie der Player); Pfade werden
aufgelöst (keine Ausflüge über Symlinks oder ".."), Stammordner selbst lassen sich weder löschen noch umbenennen.
Kopieren/Verschieben läuft im Hintergrund mit Fortschritt (Ereignis "files_job").
"""
import os
import shutil
import threading
import time

from . import config, player
from .util import bus, log, run

HOME = player.HOME
ROOTS = (HOME, "/media", "/mnt", "/run/media")
HIDDEN_HOME = {"snap", "go"}
CHUNK = 4 * 1024 * 1024
_job = {"state": "idle"}
_job_lock = threading.Lock()


def _real(path):
    return os.path.realpath(path) if isinstance(path, str) and path else ""


def allowed(path):
    real = _real(path)
    return bool(real) and any(real == r or real.startswith(r + os.sep) for r in ROOTS)


def _protected(path):
    """Stammordner (Home, /media, Laufwerks-Wurzel) und versteckte Systemordner nicht anfassen."""
    real = _real(path)
    if real in ROOTS or real in ("/media/" + os.path.basename(HOME), "/run/media/" + os.path.basename(HOME)):
        return True
    if os.path.ismount(real):
        return True
    rel = os.path.relpath(real, HOME) if real.startswith(HOME + os.sep) else ""
    return bool(rel) and rel.split(os.sep)[0].startswith(".")


def _free(path):
    try:
        du = shutil.disk_usage(path)
        return {"free": du.free, "total": du.total}
    except OSError:
        return {}


def roots(drives=()):
    out = []
    for d in drives:
        if d.get("mount"):
            out.append({"name": d.get("label") or "USB", "path": d["mount"], "type": "usb", **_free(d["mount"])})
    for name in ("Videos", "Filme", "Musik", "Music", "Bilder", "Pictures", "Downloads", "Handy", "Dokumente", "Documents"):
        p = os.path.join(HOME, name)
        if os.path.isdir(p):
            out.append({"name": name, "path": p, "type": "folder"})
    out.append({"name": "", "path": HOME, "type": "home", **_free(HOME)})
    return out


def listing(path, hidden=False):
    if config.DEV:
        return _mock(path)
    if not allowed(path) or not os.path.isdir(path):
        return {"ok": False, "message": "not_found"}
    items = []
    try:
        entries = list(os.scandir(path))
    except OSError as exc:
        return {"ok": False, "message": str(exc)[:200]}
    for e in entries:
        if (e.name.startswith(".") and not hidden) or (os.path.realpath(path) == HOME and e.name in HIDDEN_HOME):
            continue
        try:
            st = e.stat(follow_symlinks=False)
            is_dir = e.is_dir(follow_symlinks=False)
        except OSError:
            continue
        items.append({"name": e.name, "path": e.path, "dir": is_dir, "size": 0 if is_dir else st.st_size,
                      "mtime": int(st.st_mtime), "kind": "dir" if is_dir else (player.kind(e.name) or "file")})
    items.sort(key=lambda i: (not i["dir"], i["name"].casefold()))
    parent = os.path.dirname(path.rstrip("/"))
    return {"ok": True, "path": path, "name": os.path.basename(path.rstrip("/")) or path,
            "parent": parent if allowed(parent) and parent != path.rstrip("/") else None,
            "writable": os.access(path, os.W_OK), "items": items[:3000], "more": max(0, len(items) - 3000), **_free(path)}


def _unique(dest_dir, name):
    base, ext = os.path.splitext(name)
    cand, n = os.path.join(dest_dir, name), 2
    while os.path.lexists(cand):
        cand = os.path.join(dest_dir, f"{base} ({n}){ext}")
        n += 1
    return cand


def _clean_name(name):
    name = (name or "").strip().replace("/", "-").replace("\0", "")
    if name in ("", ".", "..") or len(name.encode()) > 240:
        raise ValueError("bad_name")
    return name


def operate(op, path="", name="", paths=()):
    if config.DEV:
        return {"ok": True}
    try:
        if op == "mkdir":
            if not allowed(path) or not os.path.isdir(path):
                return {"ok": False, "message": "not_found"}
            target = os.path.join(path, _clean_name(name))
            if os.path.lexists(target):
                return {"ok": False, "message": "exists"}
            os.mkdir(target)
            return {"ok": True, "path": target}
        if op == "rename":
            if not allowed(path) or _protected(path) or not os.path.lexists(path):
                return {"ok": False, "message": "protected"}
            target = os.path.join(os.path.dirname(path), _clean_name(name))
            if os.path.lexists(target):
                return {"ok": False, "message": "exists"}
            os.rename(path, target)
            return {"ok": True, "path": target}
        if op == "delete":
            done = 0
            for p in list(paths)[:500]:
                if not allowed(p) or _protected(p) or not os.path.lexists(p):
                    continue
                if os.path.isdir(p) and not os.path.islink(p):
                    shutil.rmtree(p)
                else:
                    os.remove(p)
                done += 1
            os.sync()
            log.info("Dateimanager: %d Einträge gelöscht", done)
            return {"ok": done > 0, "count": done}
    except ValueError as exc:
        return {"ok": False, "message": str(exc)}
    except OSError as exc:
        return {"ok": False, "message": exc.strerror or str(exc)}
    return {"ok": False, "message": "unknown_op"}


def _size(path):
    if os.path.isdir(path) and not os.path.islink(path):
        total = 0
        for root, _, files in os.walk(path):
            for f in files:
                try:
                    total += os.lstat(os.path.join(root, f)).st_size
                except OSError:
                    pass
        return total
    try:
        return os.lstat(path).st_size
    except OSError:
        return 0


def job():
    with _job_lock:
        return dict(_job)


def _progress(**kw):
    with _job_lock:
        _job.update(kw)
        snap = dict(_job)
    bus.emit("files_job", job=snap)


def transfer(mode, paths, dest):
    """Kopieren/Verschieben im Hintergrund. Liefert sofort; Fortschritt kommt als Ereignis."""
    paths = [p for p in list(paths or [])[:500] if isinstance(p, str)]
    if mode not in ("copy", "move") or not paths:
        return {"ok": False, "message": "bad_request"}
    if config.DEV:
        with _job_lock:
            _job.clear()
            _job.update(state="running", mode=mode, done=0, total=0, files=0, current="", dest=dest, started=time.time())
        threading.Thread(target=_dev_job, daemon=True).start()
        return {"ok": True}
    if not allowed(dest) or not os.path.isdir(dest):
        return {"ok": False, "message": "not_found"}
    for p in paths:
        if not allowed(p) or not os.path.lexists(p) or (mode == "move" and _protected(p)):
            return {"ok": False, "message": "protected"}
        rp = _real(p)
        if _real(dest) == rp or _real(dest).startswith(rp + os.sep):
            return {"ok": False, "message": "into_itself"}
    with _job_lock:
        if _job.get("state") == "running":
            return {"ok": False, "message": "busy"}
        _job.clear()
        _job.update(state="running", mode=mode, done=0, total=0, files=0, current="", dest=dest, started=time.time())
    threading.Thread(target=_run_transfer, args=(mode, paths, dest), daemon=True, name="files").start()
    return {"ok": True}


def _copy_file(src, dst):
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        while True:
            buf = fi.read(CHUNK)
            if not buf:
                break
            fo.write(buf)
            with _job_lock:
                _job["done"] += len(buf)
            if time.time() - _job.get("_ts", 0) > 0.5:
                _job["_ts"] = time.time()
                _progress(current=os.path.basename(src))
    shutil.copystat(src, dst, follow_symlinks=False)


def _copy_tree(src, dst):
    if os.path.islink(src):
        os.symlink(os.readlink(src), dst)
    elif os.path.isdir(src):
        os.mkdir(dst)
        for e in sorted(os.listdir(src)):
            _copy_tree(os.path.join(src, e), os.path.join(dst, e))
        shutil.copystat(src, dst)
    else:
        _copy_file(src, dst)
        with _job_lock:
            _job["files"] += 1


def _run_transfer(mode, paths, dest):
    try:
        total = sum(_size(p) for p in paths)
        _progress(total=total)
        free = shutil.disk_usage(dest).free
        same_fs = all(os.stat(p).st_dev == os.stat(dest).st_dev for p in paths)
        if total > free and not (mode == "move" and same_fs):
            raise OSError(28, "no_space")
        for p in paths:
            target = _unique(dest, os.path.basename(p.rstrip("/")))
            if mode == "move" and same_fs:
                os.rename(p, target)
                with _job_lock:
                    _job["done"] += _size(target)
                continue
            _copy_tree(p, target)
            if mode == "move":
                shutil.rmtree(p) if os.path.isdir(p) and not os.path.islink(p) else os.remove(p)
        run(["sync"], timeout=600)       # erst dann ist der USB-Stick wirklich fertig beschrieben
        _progress(state="done", done=total, current="")
        log.info("Dateimanager: %s fertig (%d Dateien, %d MB)", mode, _job.get("files", 0), total // 1048576)
    except OSError as exc:
        msg = "no_space" if exc.errno == 28 else (exc.strerror or str(exc))
        log.warning("Dateimanager: %s fehlgeschlagen: %s", mode, exc)
        _progress(state="error", message=msg[:200])


def _dev_job():
    _progress(total=300_000_000)
    for i in range(1, 6):
        time.sleep(0.6)
        _progress(done=i * 60_000_000, current=f"Datei {i}.mkv")
    _progress(state="done")


def _mock(path):
    base = path or "/media/tv/INTENSO"
    items = [{"name": n, "path": f"{base}/{n}", "dir": d, "size": sz, "mtime": 1767000000 + i * 86400, "kind": k}
             for i, (n, d, sz, k) in enumerate([("Filme", True, 0, "dir"), ("Musik", True, 0, "dir"), ("Urlaub 2025", True, 0, "dir"),
                                                ("Konzert.mkv", False, 2_400_000_000, "video"), ("Strand.jpg", False, 3_200_000, "image"),
                                                ("Rechnung.pdf", False, 180_000, "file")])]
    return {"ok": True, "path": base, "name": os.path.basename(base), "parent": os.path.dirname(base) if base.count("/") > 3 else None,
            "writable": True, "items": items, "more": 0, "free": 41_000_000_000, "total": 64_000_000_000}
