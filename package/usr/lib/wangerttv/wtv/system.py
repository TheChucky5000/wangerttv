"""Systemfunktionen: Netzwerk, Bluetooth, Ton, Bildschirm, Region, Info, Energie, Datenträger."""
import json
import os
import platform
import re
import shutil
import socket
import threading
import math
import struct
import time
import wave

from . import config, mock
from .util import log, run, split_nmcli, which, slugify, spawn

DEV = config.DEV


# --------------------------------------------------------------------------- Netzwerk
def network_status():
    if DEV:
        return mock.network_status()
    rc, out, _ = run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"])
    result = {"ethernet": None, "wifi": None, "online": False}
    if rc != 0:
        return result
    for line in out.splitlines():
        dev, typ, state, conn = (split_nmcli(line) + ["", "", "", ""])[:4]
        if typ not in ("ethernet", "wifi") or result.get(typ):
            continue
        connected = state.startswith("connected") or state.startswith("verbunden")
        info = {"device": dev, "connected": connected, "connection": conn if connected else "", "ip": ""}
        if connected:
            _, ipout, _ = run(["nmcli", "-g", "IP4.ADDRESS", "device", "show", dev])
            info["ip"] = ipout.strip().split("|")[0].split("/")[0]
            result["online"] = True
        if typ == "wifi":
            _, radio, _ = run(["nmcli", "radio", "wifi"])
            info["enabled"] = radio.strip() in ("enabled", "aktiviert")
        result[typ] = info
    return result


def wifi_scan():
    if DEV:
        return mock.wifi_scan()
    rc, out, _ = run(["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "yes"], timeout=30)
    if rc != 0:
        return []
    best = {}
    for line in out.splitlines():
        inuse, ssid, signal, security = (split_nmcli(line) + ["", "", "", ""])[:4]
        if not ssid:
            continue
        sig = int(signal) if signal.isdigit() else 0
        entry = {"ssid": ssid, "signal": sig, "secure": bool(security and security != "--"), "active": inuse == "*"}
        if ssid not in best or sig > best[ssid]["signal"] or entry["active"]:
            best[ssid] = entry
    return sorted(best.values(), key=lambda e: (not e["active"], -e["signal"]))


def wifi_connect(ssid, password=""):
    if DEV:
        time.sleep(1.5)
        return {"ok": password != "falsch", "message": "" if password != "falsch" else "Falsches Passwort"}
    cmd = ["nmcli", "--wait", "40", "device", "wifi", "connect", ssid]
    if password:
        cmd += ["password", password]
    rc, out, err = run(cmd, timeout=50)
    if rc != 0:
        # fehlgeschlagene Verbindung nicht als Profil liegen lassen
        run(["nmcli", "connection", "delete", "id", ssid])
        msg = (err or out).strip()
        if "Secrets were required" in msg or "802-1x" in msg or "psk" in msg.lower():
            msg = "wrong_password"
        return {"ok": False, "message": msg}
    return {"ok": True, "message": ""}


def wifi_set_enabled(on):
    if DEV:
        return True
    return run(["nmcli", "radio", "wifi", "on" if on else "off"])[0] == 0


def wifi_forget(ssid):
    if DEV:
        return True
    return run(["nmcli", "connection", "delete", "id", ssid])[0] == 0


# --------------------------------------------------------------------------- Bluetooth
_bt_scan_proc = None


def bt_devices():
    if DEV:
        return mock.bt_devices()

    def parse(out):
        devs = {}
        for line in out.splitlines():
            m = re.match(r"Device ([0-9A-F:]{17}) (.*)", line.strip())
            if m:
                devs[m.group(1)] = m.group(2)
        return devs
    all_devs = parse(run(["bluetoothctl", "devices"])[1])
    paired = parse(run(["bluetoothctl", "devices", "Paired"])[1])
    connected = parse(run(["bluetoothctl", "devices", "Connected"])[1])
    result = []
    for mac, name in all_devs.items():
        if name.replace("-", ":") == mac:
            continue  # Geräte ohne Namen ausblenden
        _, info, _ = run(["bluetoothctl", "info", mac], timeout=5)
        icon = re.search(r"Icon: (\S+)", info)
        result.append({
            "mac": mac, "name": name, "paired": mac in paired, "connected": mac in connected,
            "icon": icon.group(1) if icon else "",
        })
    return sorted(result, key=lambda d: (not d["connected"], not d["paired"], d["name"].lower()))


def bt_scan(seconds=25):
    global _bt_scan_proc
    if DEV:
        return True
    run(["rfkill", "unblock", "bluetooth"])
    run(["bluetoothctl", "power", "on"])
    if _bt_scan_proc and _bt_scan_proc.poll() is None:
        return True
    _bt_scan_proc = spawn(["bluetoothctl", "--timeout", str(seconds), "scan", "on"])
    return True


def bt_pair(mac):
    if DEV:
        time.sleep(2)
        return {"ok": True}
    if not re.fullmatch(r"[0-9A-F:]{17}", mac or ""):
        return {"ok": False, "message": "invalid"}
    script = f"agent NoInputNoOutput\ndefault-agent\npair {mac}\n"
    run(["bluetoothctl"], input_text=script, timeout=25)
    run(["bluetoothctl", "trust", mac])
    rc, out, err = run(["bluetoothctl", "connect", mac], timeout=20)
    ok = rc == 0 or "Connection successful" in out
    return {"ok": ok, "message": "" if ok else (err or out).strip()[-200:]}


def bt_action(action, mac):
    if DEV:
        return True
    if action not in ("connect", "disconnect", "remove") or not re.fullmatch(r"[0-9A-F:]{17}", mac or ""):
        return False
    return run(["bluetoothctl", action, mac], timeout=20)[0] == 0


# --------------------------------------------------------------------------- Ton
def audio_status():
    if DEV:
        return mock.audio_status()
    rc, out, _ = run(["pactl", "-f", "json", "list", "sinks"])
    sinks = []
    if rc == 0:
        try:
            for s in json.loads(out):
                sinks.append({"name": s.get("name"), "label": s.get("description") or s.get("name")})
        except ValueError:
            pass
    default = run(["pactl", "get-default-sink"])[1].strip()
    vol_out = run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"])[1]
    m = re.search(r"(\d+)%", vol_out)
    mute = "yes" in run(["pactl", "get-sink-mute", "@DEFAULT_SINK@"])[1]
    return {"sinks": sinks, "default": default, "volume": int(m.group(1)) if m else 0, "muted": mute}


def audio_set(sink=None, volume=None, step=None, mute_toggle=False):
    if DEV:
        return True
    if sink:
        run(["pactl", "set-default-sink", sink])
    if volume is not None:
        run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{max(0, min(150, int(volume)))}%"])
    if step:
        run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"])
        current = audio_status()["volume"]
        target = max(0, min(100, current + int(step)))
        run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{target}%"])
    if mute_toggle:
        run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"])
    return True


def audio_set_speakers(layout):
    """Schaltet den HDMI-Ausgang auf Stereo, 5.1 oder 7.1 (Kartenprofil hdmi-stereo/-surround/-surround71)."""
    if DEV:
        return True
    target = {"2.0": "stereo", "5.1": "surround", "7.1": "surround71"}.get(layout, "stereo")
    rc, out, _ = run(["pactl", "-f", "json", "list", "cards"])
    if rc != 0:
        return False
    try:
        cards = json.loads(out)
    except ValueError:
        return False
    changed = False
    for card in cards:
        active = card.get("active_profile") or ""
        m = re.fullmatch(r"output:hdmi-(stereo|surround|surround71)(-extra\d+)?(\+.*)?", active)
        if not m:
            continue   # kein klassisches HDMI-Profil (z. B. SOF/UCM) – Kanäle regelt dann nur Kodi
        wanted = f"output:hdmi-{target}{m.group(2) or ''}{m.group(3) or ''}"
        profiles = card.get("profiles") or {}
        if wanted in profiles and wanted != active and profiles[wanted].get("available", True):
            changed |= run(["pactl", "set-card-profile", card.get("name", ""), wanted])[0] == 0
    return changed


def audio_test():
    """Spielt einen kurzen Zweiklang über das Standard-Ausgabegerät."""
    if DEV:
        return True
    path = os.path.join(config.CACHE_DIR, "testtone.wav")
    if not os.path.exists(path):
        rate = 44100
        with wave.open(path, "wb") as wav:
            wav.setnchannels(2)
            wav.setsampwidth(2)
            wav.setframerate(rate)
            frames = bytearray()
            for freq, dur in ((660, 0.25), (880, 0.35)):
                for i in range(int(rate * dur)):
                    env = min(1.0, i / 800, (rate * dur - i) / 800)
                    val = int(9000 * env * math.sin(2 * math.pi * freq * i / rate))
                    frames += struct.pack("<hh", val, val)
            wav.writeframes(bytes(frames))
    spawn(["paplay", path])
    return True


# --------------------------------------------------------------------------- Bildschirm (Sway)
def _swaymsg(*args, timeout=10):
    return run(["swaymsg", *args], timeout=timeout)


def display_status():
    if DEV:
        return mock.display_status()
    rc, out, _ = _swaymsg("-t", "get_outputs", "-r")
    outputs = []
    if rc == 0:
        try:
            for o in json.loads(out):
                modes = []
                seen = set()
                for m in o.get("modes", []):
                    key = (m["width"], m["height"], round(m["refresh"] / 1000))
                    if key in seen:
                        continue
                    seen.add(key)
                    modes.append({"width": key[0], "height": key[1], "refresh": key[2]})
                modes.sort(key=lambda m: (m["width"] * m["height"], m["refresh"]), reverse=True)
                cur = o.get("current_mode") or {}
                outputs.append({
                    "name": o.get("name"), "label": f'{o.get("make", "")} {o.get("model", "")}'.strip(),
                    "active": o.get("active", False), "scale": o.get("scale", 1),
                    "current": {"width": cur.get("width"), "height": cur.get("height"),
                                "refresh": round((cur.get("refresh") or 0) / 1000)},
                    "modes": modes,
                })
        except ValueError:
            pass
    return {"outputs": outputs}


def display_apply(display_settings):
    """Setzt Auflösung/Skalierung. mode: 'auto' oder 'BxH@Hz'; scale: 'auto' oder Zahl."""
    if DEV:
        return True
    status = display_status()
    for out in status["outputs"]:
        if not out["active"] and not out["modes"]:
            continue
        mode = display_settings.get("mode", "auto")
        chosen = None
        if mode != "auto":
            m = re.fullmatch(r"(\d+)x(\d+)@(\d+)", mode)
            if m:
                w, h, r = map(int, m.groups())
                chosen = next((x for x in out["modes"] if (x["width"], x["height"], x["refresh"]) == (w, h, r)), None)
        explicit = chosen is not None
        if not chosen:
            # automatisch: die vom Fernseher empfohlene Auflösung (EDID) behalten, die Sway beim Start wählt
            cur = out.get("current") or {}
            if cur.get("width"):
                chosen = {"width": cur["width"], "height": cur["height"], "refresh": cur["refresh"]}
            elif out["modes"]:
                pref = [x for x in out["modes"] if 49 <= x["refresh"] <= 61 and x["width"] <= 3840]
                chosen = (pref or out["modes"])[0]
                explicit = True
        scale = display_settings.get("scale", "auto")
        if scale == "auto":
            scale = min(2.0, round(chosen["width"] / 1920, 2)) if chosen else 1
        try:
            scale = max(0.5, min(4.0, float(scale)))
        except (TypeError, ValueError):
            scale = 1.0
        args = ["output", out["name"], "enable", "scale", str(scale)]
        if chosen and explicit:
            args[3:3] = ["mode", f'{chosen["width"]}x{chosen["height"]}@{chosen["refresh"]}Hz']
        _swaymsg(*args)
    return True


def display_power(on):
    if DEV:
        return True
    return _swaymsg("output", "*", "power", "on" if on else "off")[0] == 0


# --------------------------------------------------------------------------- Region / Gerät
def timezones():
    if DEV:
        return ["Europe/Berlin", "Europe/Vienna", "Europe/Zurich", "Europe/London", "America/New_York", "UTC"]
    rc, out, _ = run(["timedatectl", "list-timezones"])
    zones = [z for z in out.split() if "/" in z or z == "UTC"] if rc == 0 else []
    return zones or ["Europe/Berlin"]


def set_timezone(tz):
    if DEV:
        return True
    if not re.fullmatch(r"[A-Za-z0-9_+\-/]+", tz or ""):
        return False
    return run(["timedatectl", "set-timezone", tz])[0] == 0


def set_locale(lang):
    if DEV:
        return True
    loc = {"de": "de_DE.UTF-8", "en": "en_US.UTF-8"}.get(lang, "de_DE.UTF-8")
    run(["localectl", "set-locale", f"LANG={loc}"])
    return True


def set_hostname(device_name):
    """Setzt Netzwerkname (wangerttv-box) und Anzeigename ("WangertTV Box", u. a. für Bluetooth)."""
    if DEV:
        return True
    pretty = (device_name or "WangertTV Box").strip()[:60]
    ok = run(["hostnamectl", "set-hostname", slugify(pretty, "wangerttv-box", 30)])[0] == 0
    run(["hostnamectl", "set-hostname", "--pretty", pretty])
    run(["bluetoothctl", "system-alias", pretty], timeout=5)
    return ok


# --------------------------------------------------------------------------- Systeminfo
def _pkg_version(pkg):
    rc, out, _ = run(["dpkg-query", "-W", "-f", "${Version}", pkg], timeout=5)
    return out.strip() if rc == 0 else ""


def sysinfo():
    if DEV:
        return mock.sysinfo()
    cpu = ""
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("model name"):
                    cpu = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass
    mem_total = mem_avail = 0
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal"):
                    mem_total = int(line.split()[1]) * 1024
                elif line.startswith("MemAvailable"):
                    mem_avail = int(line.split()[1]) * 1024
    except OSError:
        pass
    disk = shutil.disk_usage("/")
    try:
        with open("/proc/uptime", encoding="utf-8") as fh:
            uptime = int(float(fh.read().split()[0]))
    except OSError:
        uptime = 0
    try:
        with open("/etc/debian_version", encoding="utf-8") as fh:
            debian = fh.read().strip()
    except OSError:
        debian = ""
    net = network_status()
    ip = (net.get("ethernet") or {}).get("ip") or (net.get("wifi") or {}).get("ip") or ""
    gpu = ""
    rc, out, _ = run(["lspci"], timeout=5)
    for line in out.splitlines():
        if "VGA" in line or "Display controller" in line:
            gpu = line.split(":", 2)[-1].strip()
            break
    return {
        "version": config.version(), "debian": debian, "kernel": platform.release(),
        "hostname": socket.gethostname(), "cpu": cpu, "gpu": gpu,
        "mem_total": mem_total, "mem_used": mem_total - mem_avail,
        "disk_total": disk.total, "disk_used": disk.used, "uptime": uptime, "ip": ip,
        "kodi": _pkg_version("kodi"), "chrome": _pkg_version("google-chrome-stable"),
        "live": config.is_live(),
    }


# --------------------------------------------------------------------------- Energie
def power(action):
    if DEV:
        log.info("DEV power %s", action)
        return True
    cmds = {
        "reboot": ["systemctl", "reboot"],
        "poweroff": ["systemctl", "poweroff"],
        "suspend": ["systemctl", "suspend"],
    }
    if action not in cmds:
        return False
    return run(cmds[action])[0] == 0


_wake_proc = None


def start_wake_watch(callback_cmd):
    """Startet swayidle, das beim nächsten Tastendruck den Bildschirm wieder einschaltet."""
    global _wake_proc
    if DEV or not which("swayidle"):
        return
    if _wake_proc and _wake_proc.poll() is None:
        return
    _wake_proc = spawn(["swayidle", "-w", "timeout", "1", "true", "resume", callback_cmd])


def stop_wake_watch():
    global _wake_proc
    if _wake_proc and _wake_proc.poll() is None:
        _wake_proc.terminate()
    _wake_proc = None


# --------------------------------------------------------------------------- Datenträger (Installer)
def _live_disk():
    rc, src, _ = run(["findmnt", "-n", "-o", "SOURCE", "/run/live/medium"])
    if rc != 0 or not src.strip():
        return ""
    rc, parent, _ = run(["lsblk", "-no", "PKNAME", src.strip()])
    name = parent.strip().splitlines()[0] if parent.strip() else os.path.basename(src.strip())
    return "/dev/" + name


def install_disks():
    if DEV:
        return mock.disks()
    rc, out, _ = run(["lsblk", "-J", "-b", "-d", "-o", "NAME,PATH,SIZE,MODEL,TRAN,TYPE,RM,SERIAL"])
    if rc != 0:
        return []
    live = _live_disk()
    disks = []
    for d in json.loads(out).get("blockdevices", []):
        if d.get("type") != "disk" or d.get("path") == live:
            continue
        if re.match(r"(loop|zram|sr|ram)", d.get("name", "")):
            continue
        size = int(d.get("size") or 0)
        disks.append({
            "path": d["path"], "name": d["name"], "size": size, "model": (d.get("model") or "").strip(),
            "tran": d.get("tran") or "", "removable": bool(d.get("rm")), "serial": (d.get("serial") or "").strip(),
            "too_small": size < 16 * 1000 ** 3,
        })
    return disks


def systemctl_start(unit):
    """Startet eine (per Polkit freigegebene) System-Unit ohne zu blockieren."""
    if DEV:
        log.info("DEV systemctl start %s", unit)
        return True
    rc, _, err = run(["systemctl", "start", "--no-block", unit])
    if rc != 0:
        log.warning("systemctl start %s: %s", unit, err.strip())
    return rc == 0


def background(func, *args):
    threading.Thread(target=func, args=args, daemon=True).start()


def subprocess_alive(proc):
    return proc is not None and proc.poll() is None

