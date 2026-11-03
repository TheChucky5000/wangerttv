"""Werkzeuge: Internet-Geschwindigkeitstest, Systemzustand (Temperatur/Auslastung) und Speicher aufräumen."""
import glob
import os
import shutil
import threading
import time
import urllib.request

from . import config
from .util import USER_AGENT, log

SPEED_HOST = "https://speed.cloudflare.com"


# --------------------------------------------------------------------------- Geschwindigkeitstest
def _open(url, data=None, timeout=10):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 – feste https-Adresse


def _latency(samples=6):
    times = []
    for i in range(samples):
        t0 = time.perf_counter()
        try:
            with _open(f"{SPEED_HOST}/__down?bytes=0", timeout=5) as resp:
                resp.read()
        except OSError:
            continue
        if i:                               # erste Anfrage = Verbindungsaufbau (DNS/TLS), nicht mitzählen
            times.append((time.perf_counter() - t0) * 1000)
    if not times:
        return None, None
    jitter = sum(abs(a - b) for a, b in zip(times, times[1:])) / max(1, len(times) - 1)
    return round(min(times)), round(jitter)


def _download(seconds=8.0, chunk=1 << 16):
    """Lädt parallel (4 Verbindungen) so viel wie möglich in `seconds` – Ergebnis in Mbit/s."""
    total = [0]
    lock = threading.Lock()
    stop = time.perf_counter() + seconds
    t0 = time.perf_counter()

    def worker():
        while time.perf_counter() < stop:
            try:
                with _open(f"{SPEED_HOST}/__down?bytes=50000000", timeout=10) as resp:
                    while time.perf_counter() < stop:
                        data = resp.read(chunk)
                        if not data:
                            break
                        with lock:
                            total[0] += len(data)
            except OSError:
                time.sleep(0.3)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(seconds + 12)
    elapsed = max(0.1, time.perf_counter() - t0)
    return round(total[0] * 8 / elapsed / 1e6, 1) if total[0] else None


def _upload(seconds=5.0, size=4 * 1024 * 1024):
    payload = os.urandom(size)
    sent = 0
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        try:
            with _open(f"{SPEED_HOST}/__up", data=payload, timeout=15) as resp:
                resp.read()
            sent += size
        except OSError:
            break
    elapsed = max(0.1, time.perf_counter() - t0)
    return round(sent * 8 / elapsed / 1e6, 1) if sent else None


def speedtest():
    """Misst Ping, Download und Upload. Bewertet, wofür die Leitung reicht."""
    if config.DEV:
        time.sleep(1.5)
        return {"ok": True, "ping": 14, "jitter": 2, "down": 92.4, "up": 38.1, "rating": "uhd"}
    ping, jitter = _latency()
    if ping is None:
        return {"ok": False, "message": "no_internet"}
    down = _download()
    up = _upload()
    rating = "none"
    if down:
        rating = "uhd" if down >= 25 else "hd" if down >= 8 else "sd" if down >= 3 else "slow"
    log.info("Geschwindigkeitstest: Ping %s ms, Download %s Mbit/s, Upload %s Mbit/s", ping, down, up)
    return {"ok": bool(down), "ping": ping, "jitter": jitter, "down": down, "up": up, "rating": rating}


# --------------------------------------------------------------------------- Systemzustand
def cpu_temp():
    """CPU-Temperatur in °C (Intel: coretemp/x86_pkg_temp), sonst None."""
    for hw in glob.glob("/sys/class/hwmon/hwmon*"):
        try:
            with open(os.path.join(hw, "name"), encoding="utf-8") as fh:
                name = fh.read().strip()
        except OSError:
            continue
        if name in ("coretemp", "k10temp", "zenpower"):
            try:
                with open(os.path.join(hw, "temp1_input"), encoding="utf-8") as fh:
                    return round(int(fh.read().strip()) / 1000)
            except (OSError, ValueError):
                pass
    best = None
    for zone in glob.glob("/sys/class/thermal/thermal_zone*"):
        try:
            with open(os.path.join(zone, "type"), encoding="utf-8") as fh:
                kind = fh.read().strip()
            with open(os.path.join(zone, "temp"), encoding="utf-8") as fh:
                value = int(fh.read().strip()) / 1000
        except (OSError, ValueError):
            continue
        if kind == "x86_pkg_temp":
            return round(value)
        if 0 < value < 120 and (best is None or value > best):
            best = value
    return round(best) if best is not None else None


_cpu_last = None


def cpu_load():
    """CPU-Auslastung in Prozent seit dem letzten Aufruf (erster Aufruf: kurze Messung)."""
    global _cpu_last

    def read():
        with open("/proc/stat", encoding="utf-8") as fh:
            parts = [int(x) for x in fh.readline().split()[1:]]
        idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
        return sum(parts), idle

    try:
        if _cpu_last is None:
            _cpu_last = read()
            time.sleep(0.25)
        total, idle = read()
    except (OSError, ValueError, IndexError):
        return None
    dt, di = total - _cpu_last[0], idle - _cpu_last[1]
    _cpu_last = (total, idle)
    return round(100 * (1 - di / dt)) if dt > 0 else None


def swap_info():
    total = free = 0
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("SwapTotal"):
                    total = int(line.split()[1]) * 1024
                elif line.startswith("SwapFree"):
                    free = int(line.split()[1]) * 1024
    except OSError:
        pass
    zram = any(os.path.exists(f"/sys/block/{d}") for d in ("zram0",))
    return {"swap_total": total, "swap_used": total - free, "zram": zram}


def health():
    if config.DEV:
        return {"temp": 47, "load": 12, "swap_total": 4 * 1024 ** 3, "swap_used": 0, "zram": True}
    return {"temp": cpu_temp(), "load": cpu_load(), **swap_info()}


# --------------------------------------------------------------------------- Speicher aufräumen
HOME = os.path.expanduser("~")
# Nur echte Zwischenspeicher – keine Anmeldedaten, Einstellungen oder Downloads
CACHE_DIR_NAMES = {"Cache", "Code Cache", "GPUCache", "GrShaderCache", "ShaderCache", "DawnGraphiteCache",
                   "DawnWebGPUCache", "CacheStorage", "ScriptCache"}


def _dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path, onerror=lambda e: None):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def _profile_in_use(path):
    """Läuft gerade ein Chrome mit diesem Profil? (Chrome legt dann "SingletonLock" im Profilordner an)"""
    cur = os.path.dirname(path)
    while cur and cur != os.path.dirname(cur) and cur.startswith(HOME):
        if os.path.lexists(os.path.join(cur, "SingletonLock")):
            return True
        cur = os.path.dirname(cur)
    return False


def _cache_targets(kodi_running=False):
    targets = []
    # Browser-Profile der Web-Apps und der Oberfläche (Chrome legt die Caches in Unterordnern an)
    roots = [os.path.join(HOME, ".cache"), os.path.join(HOME, ".config"), config.CACHE_DIR]
    for root in roots:
        for dirpath, dirnames, _files in os.walk(root, onerror=lambda e: None):
            depth = dirpath[len(root):].count(os.sep)
            if depth > 6:
                dirnames[:] = []
                continue
            for d in list(dirnames):
                if d in CACHE_DIR_NAMES and ("chrom" in dirpath.lower() or "wangerttv" in dirpath.lower()
                                             or "google-chrome" in dirpath.lower()):
                    if not _profile_in_use(os.path.join(dirpath, d)):
                        targets.append(os.path.join(dirpath, d))
                    dirnames.remove(d)
    # Kodi: Vorschaubilder (werden bei Bedarf neu geladen) und Temp-Ordner (ohne Protokolle)
    kodi = os.path.join(HOME, ".kodi")
    if not kodi_running:
        targets.append(os.path.join(kodi, "userdata", "Thumbnails"))
        for p in glob.glob(os.path.join(kodi, "temp", "*")):
            if not p.endswith(".log"):
                targets.append(p)
    targets.append(os.path.join(kodi, "addons", "packages"))
    return [t for t in targets if os.path.exists(t)]


def cleanup_preview(kodi_running=False):
    if config.DEV:
        return {"bytes": 734 * 1024 ** 2, "items": 12}
    targets = _cache_targets(kodi_running)
    return {"bytes": sum(_dir_size(t) if os.path.isdir(t) else os.path.getsize(t) for t in targets),
            "items": len(targets)}


def cleanup(kodi_running=False):
    """Leert die Zwischenspeicher. Kodi-Vorschaubilder nur, solange Kodi nicht läuft (Datenbank-Bezug);
    Browser-Caches nur von Apps, die gerade nicht laufen."""
    if config.DEV:
        return {"ok": True, "freed": 734 * 1024 ** 2}
    freed = 0
    for t in _cache_targets(kodi_running):
        try:
            if os.path.isdir(t) and not os.path.islink(t):
                size = _dir_size(t)
                shutil.rmtree(t, ignore_errors=True)
                if t.endswith(os.path.join("userdata", "Thumbnails")):
                    os.makedirs(t, exist_ok=True)
                    # Kodi merkt sich Vorschaubilder in Textures13.db – ohne Bilder dort neu aufbauen lassen
                    for db in glob.glob(os.path.join(HOME, ".kodi", "userdata", "Database", "Textures*.db")):
                        try:
                            os.remove(db)
                        except OSError:
                            pass
            else:
                size = os.path.getsize(t)
                os.remove(t)
            freed += size
        except OSError:
            continue
    log.info("Speicher aufgeräumt: %.1f MB frei", freed / 1e6)
    return {"ok": True, "freed": freed}


# --------------------------------------------------------------------------- Diagnose-Bericht
_SECRET_RE = None


def _tail(path, lines=200):
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 400_000))
            data = fh.read().decode("utf-8", "replace").replace("\x00", "")
        return "\n".join(data.splitlines()[-lines:])
    except OSError:
        return "(nicht vorhanden)"


def _clean(text):
    """Passwörter, Tokens und PINs aus dem Bericht entfernen."""
    import re
    global _SECRET_RE
    if _SECRET_RE is None:
        _SECRET_RE = re.compile(r'(?i)("?(?:password|passwort|token|pin|secret|key)"?\s*[:=]\s*)("[^"]*"|\S+)')
    return _SECRET_RE.sub(r"\1***", text)


def diag_report(core):
    """Textbericht für die Fehlersuche (Web-Oberfläche -> Diagnose herunterladen). Enthält keine Passwörter."""
    from . import remote
    from .util import run
    out = [f"WangertTV {config.version()} – Diagnose vom {time.strftime('%Y-%m-%d %H:%M:%S')}", ""]

    def section(title, body):
        out.extend([f"===== {title} =====", body.rstrip() or "(leer)", ""])

    s = dict(core.settings.data)
    for k in ("waipu", "web_admin", "phone_remote"):
        s.pop(k, None)
    s["iptv"] = {k: v for k, v in (s.get("iptv") or {}).items() if k != "m3u"}
    tv = dict(s.get("tv") or {})
    tv["order"] = f"{len(tv.get('order') or [])} Sender eigene Reihenfolge"
    s["tv"] = tv
    s.pop("reminders", None)
    import json as _json
    section("Einstellungen (ohne Zugangsdaten)", _json.dumps(s, ensure_ascii=False, indent=1)[:6000])
    section("Laufende Apps", ", ".join(core.apps.running) or "keine")
    from . import history, kodi as _kodi
    addon_xml = os.path.join(_kodi.addon_dst(), "addon.xml")
    kodi_log = os.path.join(_kodi.KODI_HOME, "temp", "kodi.log")
    try:
        with open(kodi_log, encoding="utf-8", errors="replace") as fh:
            wtv_lines = [ln.rstrip() for ln in fh if "WangertTV" in ln or "service.wangerttv" in ln][-30:]
    except OSError:
        wtv_lines = []
    section("Kodi-Add-on WangertTV", f"installiert: {os.path.exists(addon_xml)} · Verlauf: {_json.dumps(history.summary())}\n" +
            "\n".join(wtv_lines))
    # Abstürze von Kodi: Absturzprotokoll (vom kodi-Startskript) und das Protokoll des vorherigen Laufs
    crashes = sorted(glob.glob(os.path.join(config.HOME, "kodi_crashlog-*.log")), key=os.path.getmtime)
    crash_txt = ""
    if crashes:
        try:
            with open(crashes[-1], encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
            keep = [ln for ln in lines if any(k in ln for k in ("Thread", "#", "signal", "SIG", "Crash", "error", "ERROR"))]
            crash_txt = f"{os.path.basename(crashes[-1])} ({len(crashes)} insgesamt)\n" + "\n".join((keep or lines)[-120:])
        except OSError:
            pass
    saved = sorted(glob.glob(os.path.join(config.LOG_DIR, "kodi-crash-*.log")))
    if saved:
        try:
            with open(saved[-1], encoding="utf-8", errors="replace") as fh:
                crash_txt += f"\n--- {os.path.basename(saved[-1])} ---\n" + "\n".join(ln.rstrip() for ln in fh.readlines()[-60:])
        except OSError:
            pass
    old_log = os.path.join(_kodi.KODI_HOME, "temp", "kodi.old.log")
    try:
        with open(old_log, encoding="utf-8", errors="replace") as fh:
            tail = [ln.rstrip() for ln in fh][-60:]
    except OSError:
        tail = []
    section("Kodi-Abstürze", (crash_txt or "kein Absturzprotokoll") + "\n--- Ende des vorherigen Kodi-Laufs ---\n" + "\n".join(tail))
    from . import tvnav
    section("TV-Steuerung in Web-Apps", _json.dumps(tvnav.status(), ensure_ascii=False) + "\n" +
            run(["sh", "-c", "google-chrome --version 2>/dev/null || chromium --version 2>/dev/null"], timeout=10)[1].strip())
    for key in list(core.apps.running):
        if key not in ("kodi", "android"):
            section(f"App-Protokoll {key}", _tail(os.path.join(config.LOG_DIR, f"{key}.log"), 40))
    section("Fernbedienungsdienst (Status)", _json.dumps(remote.send("status", timeout=2), ensure_ascii=False)[:3000])
    rlog = remote.send("log", n=250, timeout=3)
    section("Fernbedienungsdienst (Protokoll & Tasten)", "\n".join(rlog.get("lines") or []) or "(nicht verfügbar)")
    section("Eingabegeräte (sway)", run(["swaymsg", "-t", "get_inputs", "-r"], timeout=5)[1][:6000])
    section("Tonausgänge", run(["pactl", "list", "short", "sinks"], timeout=5)[1])
    try:
        cards = _json.loads(run(["pactl", "-f", "json", "list", "cards"], timeout=5)[1] or "[]")
        profiles = "\n".join(f"{c.get('name')}: {c.get('active_profile')}" for c in cards)
    except ValueError:
        profiles = ""
    section("Ton-Kartenprofile & Raumklang-Modus", (profiles or "(nicht verfügbar)") +
            f"\nRaumklang-Modus: {(core.settings.get('audio', 'pcm_mode') or 'auto')}, HDMI-PCM: {core.kodi.pcm_layout()}")
    import glob as _glob
    eld = []
    for path in sorted(_glob.glob("/proc/asound/card*/eld#*")):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        if "monitor_present\t\t1" in text or "eld_valid\t\t1" in text:
            eld.append(f"--- {path}\n{text.strip()}")
    section("HDMI-Gegenstelle (ELD: was Receiver/Fernseher können)", "\n".join(eld) or "(kein Gerät meldet sich)")
    rc, dump, _ = run(["pw-dump"], timeout=10)
    nodes = []
    try:
        for obj in _json.loads(dump) if rc == 0 else []:
            props = (obj.get("info") or {}).get("props") or {}
            if "HDMI" in str(props.get("node.name", "")):
                fmts = (obj.get("info") or {}).get("params", {}).get("EnumFormat") or []
                keep = {k: v for k, v in props.items() if k.startswith(("node.name", "audio.", "api.alsa.path", "api.alsa.open",
                                                                         "iec958", "device.profile"))}
                chans = sorted({str(f.get("channels")) for f in fmts if isinstance(f, dict) and f.get("channels")})
                subs = sorted({str(f.get("mediaSubtype")) for f in fmts if isinstance(f, dict)})
                nodes.append(f"{_json.dumps(keep, ensure_ascii=False)}\n   Formate: {', '.join(subs)} · Kanäle: {', '.join(chans)}")
    except ValueError:
        pass
    section("HDMI-Ausgänge in PipeWire", "\n".join(nodes) or "(nicht verfügbar)")
    section("Laufwerke", run(["lsblk", "-o", "NAME,SIZE,TYPE,FSTYPE,TRAN,MOUNTPOINTS"], timeout=5)[1])
    section("Systemzustand", _json.dumps(health(), ensure_ascii=False))
    section("WangertTV-Protokoll (letzte 300 Zeilen)", _tail(os.path.join(config.LOG_DIR, "wtvd.log"), 300))
    for name in ("spotify.log", "spotify_desktop.log", "airplay.log", "radio.log", "surround.log", "android-launch.log"):
        section(name, _tail(os.path.join(config.LOG_DIR, name), 60))
    kodi_log = os.path.join(os.path.expanduser("~"), ".kodi", "temp", "kodi.log")
    lines = [ln for ln in _tail(kodi_log, 3000).splitlines()
             if any(w in ln for w in ("ERROR", "error", "ActiveAE", "PIPEWIRE", "inputstream", "Creating audio", "OpenSink"))]
    section("Kodi (Fehler & Ton, gefiltert)", "\n".join(lines[-120:]))
    section("Update-Protokoll (letzte 150 Zeilen)", _tail("/var/log/wangerttv-update.log", 150))
    for unit in ("wtv-remote", "wtv-update", "wtv-extras"):
        section(f"Systemdienst {unit}", run(["journalctl", "-u", unit, "-b", "-n", "60", "--no-pager"], timeout=10)[1]
                or "(kein Zugriff – nur mit Root lesbar)")
    return _clean("\n".join(out))
