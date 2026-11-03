"""Systemfunktionen: Netzwerk, Bluetooth, Ton, Bildschirm, Region, Info, Energie, Datenträger."""
import glob
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
# Audioformate aus der HDMI-ELD (CTA-861 "Short Audio Descriptor"-Codierung)
ELD_FORMATS = {1: "pcm", 2: "ac3", 6: "aac", 7: "dts", 10: "eac3", 11: "dtshd", 12: "truehd"}


def parse_eld(text):
    """Wertet /proc/asound/cardX/eld#Y.Z aus: Gerätename, Lautsprecher und unterstützte Tonformate."""
    info, sads = {}, {}
    for line in text.splitlines():
        key, _, val = line.partition("\t")
        key, val = key.strip(), val.strip()
        m = re.fullmatch(r"sad(\d+)_(\w+)", key)
        if m:
            sads.setdefault(int(m.group(1)), {})[m.group(2)] = val
        elif key:
            info[key] = val
    if info.get("monitor_present") != "1" or info.get("eld_valid") != "1":
        return None
    formats, pcm_channels = [], 2
    for sad in sads.values():
        m = re.match(r"\[0x([0-9a-fA-F]+)\]", sad.get("coding_type", ""))
        if not m:
            continue
        fmt = ELD_FORMATS.get(int(m.group(1), 16))
        try:
            channels = int(sad.get("channels", "2"))
        except ValueError:
            channels = 2
        if fmt == "pcm":
            pcm_channels = max(pcm_channels, channels)
        elif fmt and fmt not in formats:
            formats.append(fmt)
    speakers = re.sub(r"^\[0x[0-9a-fA-F]+\]\s*", "", info.get("speakers", ""))
    layout = "7.1" if pcm_channels >= 8 else "5.1" if pcm_channels >= 6 else "2.0"
    return {"name": info.get("monitor_name", ""), "pcm_channels": pcm_channels, "formats": formats,
            "speakers": speakers, "layout": layout,
            "passthrough": any(f in formats for f in ("ac3", "dts", "eac3", "truehd", "dtshd"))}


def hdmi_receiver():
    """Am HDMI-Ausgang angeschlossenes Gerät (Fernseher oder AV-Receiver) mit seinen Tonfähigkeiten."""
    if DEV:
        return {"name": "AVR-X1600H", "pcm_channels": 8, "formats": ["ac3", "dts", "eac3", "truehd", "dtshd"],
                "speakers": "FL/FR LFE FC RL/RR RLC/RRC", "layout": "7.1", "passthrough": True}
    best = None
    for path in sorted(glob.glob("/proc/asound/card*/eld#*")):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                eld = parse_eld(fh.read())
        except OSError:
            continue
        if eld and (best is None or len(eld["formats"]) + eld["pcm_channels"] > len(best["formats"]) + best["pcm_channels"]):
            best = eld
    return best


def audio_status():
    if DEV:
        return dict(mock.audio_status(), receiver=hdmi_receiver())
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
    return {"sinks": sinks, "default": default, "volume": int(m.group(1)) if m else 0, "muted": mute,
            "receiver": hdmi_receiver()}


AUDIO_DRIVER_CONF = "/etc/modprobe.d/wangerttv-audio.conf"


def audio_driver():
    """Welcher Ton-Treiber läuft? sof = Intel-DSP (HDMI nur Stereo-PCM), legacy = klassisches Intel-HDA (bis 8 Kanäle)."""
    if DEV:
        return {"current": "sof", "configured": "auto", "switchable": True}
    try:
        with open("/proc/asound/cards", encoding="utf-8", errors="replace") as fh:
            cards = fh.read()
    except OSError:
        cards = ""
    current = "sof" if "sof-" in cards else "legacy" if "HDA-Intel" in cards else "other"
    configured = "legacy" if os.path.exists(AUDIO_DRIVER_CONF) else "auto"
    # Umschalten nur auf Intel-HDA-Systemen (SoundWire-Geräte brauchen den DSP-Treiber zwingend)
    switchable = "sof-hda-dsp" in cards or (current == "legacy" and configured == "legacy")
    return {"current": current, "configured": configured, "switchable": switchable}


def set_audio_driver(driver):
    if driver not in ("legacy", "sof"):
        return False
    return systemctl_start(f"wtv-audiodriver@{driver}.service")


def sink_names():
    rc, out, _ = run(["pactl", "list", "short", "sinks"], timeout=5)
    return [line.split("\t")[1] for line in out.splitlines() if "\t" in line] if rc == 0 else []


def default_sink():
    rc, out, _ = run(["pactl", "get-default-sink"], timeout=5)
    return out.strip() if rc == 0 else ""


def ensure_default_sink(user_sink=""):
    """Standard-Ausgang prüfen: gibt es den gewählten Ausgang nicht (mehr), läuft der Ton über HDMI.

    Sonst landet Ton von Spotify Connect, AirPlay oder Web-Apps z. B. am Kopfhörer-Ausgang – und am Fernseher
    ist nichts zu hören. Gibt den tatsächlich gesetzten Ausgang zurück."""
    if DEV:
        return user_sink
    names = sink_names()
    if user_sink and user_sink in names:
        target = user_sink
    else:
        if user_sink:
            log.warning("Tonausgang %s gibt es nicht mehr – nehme HDMI", user_sink)
        current = default_sink()
        if current.startswith("wangerttv") and current in names:
            return current                       # Stereo-Upmix ist der Standard – richtig so
        target = hdmi_sink() or current
    if target and target != default_sink():
        set_default_sink(target)
        log.info("Standard-Tonausgang: %s", target)
    return target


def audio_set(sink=None, volume=None, step=None, mute_toggle=False):
    if DEV:
        return True
    if sink:
        if sink not in sink_names():
            log.warning("Tonausgang %s nicht gefunden – nehme HDMI", sink)
            sink = hdmi_sink()
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
            ok = run(["pactl", "set-card-profile", card.get("name", ""), wanted])[0] == 0
            log.info("HDMI-Profil %s -> %s%s", active, wanted, "" if ok else " (fehlgeschlagen)")
            changed |= ok
    return changed


def hdmi_sink():
    """Name des HDMI-Ausgangs in PipeWire (bevorzugt der Anschluss, an dem wirklich ein Gerät hängt)."""
    if DEV:
        return "alsa_output.pci-0000_00_1f.3.hdmi-surround"
    rc, out, _ = run(["pactl", "-f", "json", "list", "sinks"])
    if rc != 0:
        return ""
    try:
        sinks = json.loads(out)
    except ValueError:
        return ""
    best, best_score = "", -1
    for sink in sinks:
        name = sink.get("name") or ""
        if name.startswith("wangerttv") or "hdmi" not in (name + (sink.get("description") or "")).lower():
            continue
        ports = sink.get("ports") or []
        active = next((p for p in ports if p.get("name") == sink.get("active_port")), {})
        avail = active.get("availability", "")
        score = 2 if avail == "available" else 1 if "unknown" in avail or not avail else 0
        score += min(int((sink.get("channel_map") or "").count(",")), 7) / 10
        if score > best_score:
            best, best_score = name, score
    return best


PULSE_IEC958 = {"ac3": "ac3-iec61937", "eac3": "eac3-iec61937", "dts": "dts-iec61937",
                "truehd": "truehd-iec61937", "dtshd": "dtshd-iec61937"}


def set_passthrough_formats(formats):
    """Schaltet an den HDMI-Ausgängen das Durchreichen (IEC958) frei – wie in pavucontrol unter "Erweitert".

    Ohne das meldet PipeWire Kodi "No passthrough capabilities" und Kodi wandelt alles in PCM um.
    (Die WirePlumber-Regel iec958.codecs allein reicht nicht: der gespeicherte Anschluss-Zustand überschreibt sie.)
    Gibt True zurück, wenn sich etwas geändert hat."""
    if DEV:
        return False
    want = ["pcm"] + [PULSE_IEC958[f] for f in formats if f in PULSE_IEC958]
    rc, out, _ = run(["pactl", "-f", "json", "list", "sinks"])
    if rc != 0:
        return False
    try:
        sinks = json.loads(out)
    except ValueError:
        return False
    changed = False
    for sink in sinks:
        name = sink.get("name") or ""
        if name.startswith("wangerttv") or "hdmi" not in (name + (sink.get("description") or "")).lower():
            continue
        have = [str(f).split(",")[0].strip() for f in sink.get("formats") or []]
        if sorted(have) == sorted(want):
            continue
        cmd = ["pactl", "set-sink-formats", str(sink.get("index")), ";".join(want)]
        rc, _, err = run(cmd, timeout=5)
        if rc != 0:
            # pavucontrol & pactl brauchen dafür das Modul "device-restore" im PipeWire-Pulse-Server
            run(["pactl", "load-module", "module-device-restore"], timeout=5)
            rc, _, err = run(cmd, timeout=5)
        if rc == 0:
            changed = True
            log.info("HDMI-Durchreichen für %s: %s", name, ", ".join(want))
        else:
            log.warning("HDMI-Durchreichen für %s nicht gesetzt: %s", name, (err or "").strip())
    return changed


WP_PASSTHROUGH = os.path.expanduser("~/.config/wireplumber/wireplumber.conf.d/91-wangerttv-passthrough.conf")


def _hdmi_alsa_nodes():
    """HDMI-Ausgänge mit ALSA-Daten: [(node_name, card_id, pcm_device, path)]."""
    rc, out, _ = run(["pactl", "-f", "json", "list", "sinks"])
    if rc != 0:
        return []
    try:
        sinks = json.loads(out)
    except ValueError:
        return []
    nodes = []
    for sink in sinks:
        name = sink.get("name") or ""
        props = sink.get("properties") or {}
        path = props.get("api.alsa.path") or ""
        if name.startswith("wangerttv") or "hdmi" not in (name + (sink.get("description") or "")).lower():
            continue
        m = (re.match(r"^(?:_ucm\w*\.)?hw:([^,]+),(\d+)$", path)
             or re.match(r"^hdmiwtv:CARD=([^,]+),DEV=(\d+)", path))
        if not m:
            nodes.append((name, None, None, path))      # z. B. schon "hdmi:..." (klassischer HDA-Treiber)
            continue
        card, dev = m.group(1), int(m.group(2))
        if card.isdigit():
            try:
                with open(f"/proc/asound/card{card}/id", encoding="utf-8") as fh:
                    card = fh.read().strip() or card
            except OSError:
                pass
        nodes.append((name, card, dev, path))
    return nodes


def _iec958_ctl_indexes(card):
    rc, out, _ = run(["amixer", "-c", card, "controls"], timeout=5)
    if rc != 0:
        return []
    return sorted(int(m.group(1)) for m in re.finditer(r"name='IEC958 Playback Default',index=(\d+)", out))


def _restart_wireplumber():
    run(["systemctl", "--user", "restart", "wireplumber.service"], timeout=15)
    for _ in range(40):
        time.sleep(0.25)
        if hdmi_sink():
            return True
    return False


def _node_offers_iec958(node_name):
    rc, out, _ = run(["pw-dump"], timeout=10)
    if rc != 0:
        return False
    try:
        objs = json.loads(out)
    except ValueError:
        return False
    for obj in objs:
        info = obj.get("info") or {}
        if (info.get("props") or {}).get("node.name") != node_name:
            continue
        for fmt in (info.get("params") or {}).get("EnumFormat") or []:
            if fmt.get("mediaSubtype") == "iec958":
                return True
    return False


# Kanalreihenfolge der ALSA-HDMI-Geräte (Standard-Reihenfolge von ALSA)
HDMI_POSITIONS = {"5.1": "[ FL FR RL RR FC LFE ]", "7.1": "[ FL FR RL RR FC LFE SL SR ]"}


def _sink_channels(node_name):
    rc, out, _ = run(["pactl", "list", "short", "sinks"], timeout=5)
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) > 3 and parts[1] == node_name:
            m = re.search(r"(\d+)ch", parts[3])
            return int(m.group(1)) if m else None
    return None


def _hdmi_rules(sof, passthrough, layout):
    rules = []
    by_card = {}
    for name, card, dev, _path in sof:
        by_card.setdefault(card, []).append((dev, name))
    for card, items in by_card.items():
        items.sort()
        ctls = _iec958_ctl_indexes(card) if passthrough else []
        for i, (dev, name) in enumerate(items):
            props = []
            if passthrough:
                # IEC958-Regler: i-ter HDMI-Ausgang = i-ter Regler (fehlt er, überspringt ALSA das Setzen nur –
                # siehe /etc/alsa/conf.d/50-wangerttv-hdmi.conf, "optional true")
                m = re.search(r"HDMI(\d+)", name)
                ctl = ctls[i] if len(ctls) == len(items) else (int(m.group(1)) - 1 if m else i)
                props.append(f'api.alsa.path = "hdmiwtv:CARD={card},DEV={dev},CTL={ctl}" api.alsa.open.ucm = false')
            # Kanalzahl NICHT erzwingen: Der Intel-DSP-Treiber (SOF) erlaubt an HDMI nur 2 Kanäle. Mit
            # audio.channels = 6 meldet PipeWire dann "6 bis 2 Kanäle" – Kodi verwirft den Ausgang komplett und
            # weicht auf "Standard" aus (gesehen in 1.0.26–1.0.29). Mehrkanal-PCM gibt es nur mit dem klassischen
            # Intel-HDA-Treiber (Einstellungen -> Bild & Ton -> Ton-Treiber); 5.1 kommt hier als Dolby Digital an.
            if props:
                rules.append("  {\n"
                             f"    matches = [ {{ node.name = \"{name}\" }} ]\n"
                             f"    actions = {{ update-props = {{ {' '.join(props)} }} }}\n"
                             "  }")
    if not rules:
        return ""
    return ("# Von WangertTV erzeugt (Raumklang: Kanäle und Durchreichen) – wird automatisch verwaltet\n"
            "monitor.alsa.rules = [\n" + "\n".join(rules) + "\n]\n")


def hdmi_setup(passthrough, layout="2.0"):
    """Richtet die HDMI-Ausgänge von Intel "Smart Sound Technology" (SOF) für Raumklang ein.

    1. Kanäle: SOF meldet HDMI nur als Stereo (2 Kanäle) – dann rechnet PipeWire jeden 5.1-Ton auf Stereo herunter
       und Kodi bietet keine Kanalwahl an. Hier wird der Ausgang auf 6 (5.1) bzw. 8 (7.1) Kanäle gestellt.
    2. Durchreichen (Dolby/DTS): Dafür braucht PipeWire ein ALSA-Gerät, dessen Name mit "hdmi" beginnt
       (sonst "No passthrough capabilities") – siehe /etc/alsa/conf.d/50-wangerttv-hdmi.conf.
    Prüft danach selbst, ob es geklappt hat; Durchreichen wird bei Problemen wieder abgeschaltet, damit der Ton
    nie wegen dieser Funktion ausfällt. Startet WirePlumber nur neu, wenn sich etwas ändert.
    Gibt True zurück, wenn ein HDMI-Ausgang Durchreichen anbietet."""
    if DEV:
        return False
    nodes = _hdmi_alsa_nodes()
    sof = [n for n in nodes if n[1] is not None]
    native = [n for n in nodes if n[1] is None and n[3].startswith(("hdmi", "iec958"))]
    current = ""
    try:
        with open(WP_PASSTHROUGH, encoding="utf-8") as fh:
            current = fh.read()
    except OSError:
        pass
    failed = ""
    try:
        with open(WP_PASSTHROUGH + ".fehlgeschlagen", encoding="utf-8") as fh:
            failed = fh.read()
    except OSError:
        pass
    wanted = _hdmi_rules(sof, passthrough, layout) if sof else ""
    if wanted and wanted == failed and passthrough:
        # Durchreichen ist auf diesem Gerät schon einmal gescheitert -> nur die Kanäle einrichten
        wanted = _hdmi_rules(sof, False, layout)
        passthrough = False
    if wanted == current:
        if native and not sof:
            return passthrough
        return bool(passthrough and wanted) and any(_node_offers_iec958(n[0]) for n in sof)
    if wanted:
        os.makedirs(os.path.dirname(WP_PASSTHROUGH), exist_ok=True)
        with open(WP_PASSTHROUGH, "w", encoding="utf-8") as fh:
            fh.write(wanted)
    else:
        try:
            os.remove(WP_PASSTHROUGH)
        except OSError:
            pass
    log.info("HDMI-Raumklang einrichten (%s, Durchreichen %s) – starte WirePlumber neu", layout, "an" if passthrough else "aus")
    ok = _restart_wireplumber()
    if not wanted:
        return False
    time.sleep(1.0)
    names = [n[0] for n in sof]
    if layout in HDMI_POSITIONS:
        chans = {n: _sink_channels(n) for n in names}
        log.info("HDMI-Kanäle: %s", ", ".join(f"{n.rsplit('.', 1)[-1]}={c}" for n, c in chans.items()))
    if not passthrough:
        return False
    if ok and any(_node_offers_iec958(n) for n in names):
        log.info("HDMI-Durchreichen aktiv")
        return True
    # Durchreichen hat nicht geklappt -> merken und nur die Kanäle behalten, damit der normale Ton sicher läuft
    log.warning("HDMI-Durchreichen ließ sich nicht einrichten – weiter ohne Durchreichen")
    with open(WP_PASSTHROUGH + ".fehlgeschlagen", "w", encoding="utf-8") as fh:
        fh.write(wanted)
    rest = _hdmi_rules(sof, False, layout)
    if rest:
        with open(WP_PASSTHROUGH, "w", encoding="utf-8") as fh:
            fh.write(rest)
    else:
        try:
            os.remove(WP_PASSTHROUGH)
        except OSError:
            pass
    _restart_wireplumber()
    return False


SSH_STATE = "/var/lib/wangerttv/ssh.json"


def ssh_status():
    """SSH-Zugang: an/aus, Passwort (nur für die Anzeige am Fernseher) und Anmeldebefehl."""
    if DEV:
        st = config.read_json(os.path.join(config.STATE_DIR, "ssh.json"), {}) or {}
    else:
        st = config.read_json(SSH_STATE, {}) or {}
    active = bool(st.get("enabled"))
    if not DEV:
        active = active and run(["systemctl", "is-active", "--quiet", "ssh.service"], timeout=5)[0] == 0
    net = network_status()
    ip = (net.get("ethernet") or {}).get("ip") or (net.get("wifi") or {}).get("ip") or ""
    return {"enabled": bool(st.get("enabled")), "active": active, "password": st.get("password", "") if st.get("enabled") else "",
            "user": "tv", "ip": ip, "command": f"ssh tv@{ip}" if ip else "ssh tv@<IP der Box>"}


def set_power_profile(profile):
    """CPU-Leistungsprofil (balanced | performance | quiet) über wtv-power@.service setzen."""
    if profile not in ("balanced", "performance", "quiet"):
        return False
    if DEV:
        return True
    return run(["systemctl", "start", "--no-block", f"wtv-power@{profile}.service"], timeout=20)[0] == 0


def ssh_set(action):
    if action not in ("on", "off", "renew"):
        return False
    if DEV:
        import secrets as _s
        path = os.path.join(config.STATE_DIR, "ssh.json")
        st = config.read_json(path, {}) or {}
        if action in ("on", "renew"):
            st["password"] = "-".join(_s.token_hex(2) for _ in range(3))
        if action != "renew":
            st["enabled"] = action == "on"
        config.atomic_write_json(path, st, 0o600)
        return True
    rc = run(["systemctl", "start", f"wtv-ssh@{action}.service"], timeout=60)[0]
    return rc == 0


def set_default_sink(name):
    if DEV or not name:
        return True
    return run(["pactl", "set-default-sink", name])[0] == 0


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
    lines, boot_video = [], None
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
        if chosen and out.get("active"):
            lines.append(f'output {out["name"]} mode {chosen["width"]}x{chosen["height"]}@{chosen["refresh"]}Hz scale {scale}')
            boot_video = boot_video or f'{out["name"]}_{chosen["width"]}x{chosen["height"]}_{chosen["refresh"]}'
    _persist_display(lines, boot_video)
    return True


SWAY_OUTPUT_CONF = os.path.join(config.CONF_DIR, "sway-output.conf")


def _persist_display(lines, boot_video):
    """Bildmodus für den nächsten Start merken: Sway startet direkt darin, der Kernel stellt ihn schon beim Booten
    ein (video=…). So wechselt der Fernseher den Modus nur einmal – kein Flackern zwischen Animation und Oberfläche."""
    text = "# WangertTV: Bildmodus beim Start (automatisch geschrieben)\n" + "\n".join(lines) + "\n"
    try:
        with open(SWAY_OUTPUT_CONF, encoding="utf-8") as fh:
            same = fh.read() == text
    except OSError:
        same = False
    if not same and lines:
        try:
            os.makedirs(os.path.dirname(SWAY_OUTPUT_CONF), exist_ok=True)
            with open(SWAY_OUTPUT_CONF, "w", encoding="utf-8") as fh:
                fh.write(text)
        except OSError as exc:
            log.warning("Bildmodus nicht gespeichert: %s", exc)
    stamp = os.path.join(config.CACHE_DIR, "bootvideo")
    try:
        with open(stamp, encoding="utf-8") as fh:
            if fh.read().strip() == (boot_video or ""):
                return
    except OSError:
        pass
    if not boot_video or not re.fullmatch(r"[A-Za-z]+(-[A-Za-z]+)?-\d{1,2}_\d{3,4}x\d{3,4}_\d{2,3}", boot_video):
        return
    rc, unit, _ = run(["systemd-escape", "--template=wtv-bootvideo@.service", boot_video], timeout=5)
    if rc == 0 and run(["systemctl", "start", "--no-block", unit.strip()], timeout=20)[0] == 0:
        try:
            with open(stamp, "w", encoding="utf-8") as fh:
                fh.write(boot_video)
        except OSError:
            pass
        log.info("Bildmodus beim Start: %s", boot_video)


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
        # Laufende Wiedergabe-Sperren (Chrome/Kodi) sollen den Ruhezustand nicht verhindern
        "suspend": ["systemctl", "suspend", "--check-inhibitors=no"],
    }
    if action not in cmds:
        return False
    rc, out, err = run(cmds[action], timeout=30)
    if rc != 0:
        log.warning("%s fehlgeschlagen (Code %s): %s", action, rc, (err or out).strip()[-300:])
    else:
        log.info("%s ausgelöst", action)
    return rc == 0


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

