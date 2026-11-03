"""Kodi-Anbindung: Erstkonfiguration, IPTV-Einrichtung und Steuerung per JSON-RPC (TCP 9090)."""
import hashlib
import json
import os
import shutil
import socket
import threading
import time
from xml.sax.saxutils import escape

from . import config, logos, system
from .config import atomic_write_json
from .util import log, run, which

KODI_HOME = os.path.join(config.HOME, ".kodi")
USERDATA = os.path.join(KODI_HOME, "userdata")
RPC_PORT = 9090

# Pulse-Eight USB-CEC-Adapter: Kodi darf ihn nicht selbst öffnen – das übernimmt wtv-cec systemweit.
CEC_PERIPHERALS = ["usb_2548_1001_CEC_Adapter.xml", "usb_2548_1002_CEC_Adapter.xml"]

LANG_ADDON = {"de": "resource.language.de_de", "en": "resource.language.en_gb"}

# WangertTV-Add-on in Kodi (Info-Leiste, Umschalten in WangertTV-Reihenfolge, Rückkehr zu WangertTV)
ADDON_ID = "service.wangerttv"
ADDON_SRC = os.path.join(config.LIB, "kodi", ADDON_ID)
ACCENT_HEX = {"cyan": "22E1FF", "mint": "3EF0B0", "gold": "FFD24A", "coral": "FF8A65", "red": "FF5D6C", "pink": "FF8AD8",
              "violet": "C58BFF", "blue": "8FB8FF", "lime": "C6F24E", "white": "FFFFFF"}

NOTIFY = f"NotifyAll({ADDON_ID},%s)"
DIGITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]


def addon_dst():
    return os.path.join(os.path.dirname(USERDATA), "addons", ADDON_ID)


def addon_state_file():
    return os.path.join(USERDATA, "addon_data", ADDON_ID, "state.json")


def keymap(own_ui=True):
    """Tastenbelegung für Kodi. own_ui: Live-TV mit WangertTV-Info-Leiste statt Kodis eigener Menüs."""
    guide = NOTIFY % "wtv_guide" if own_ui else "ActivateWindow(TVGuide)"
    live = ""
    if own_ui:
        keys = {"up": "wtv_up", "down": "wtv_down", "pageup": "wtv_up", "pagedown": "wtv_down", "return": "wtv_info",
                "enter": "wtv_info", "i": "wtv_info", "backspace": "wtv_back", "escape": "wtv_back",
                "browser_back": "wtv_back", "e": "wtv_guide", "stop": "wtv_back"}
        for i, name in enumerate(DIGITS):
            keys[name] = keys[f"numpad{name}"] = f"wtv_num{i}"
        live = "\n".join(f"      <{k}>{NOTIFY % v}</{k}>" for k, v in keys.items())
    else:
        live = "      <pageup>ChannelUp</pageup>\n      <pagedown>ChannelDown</pagedown>\n      <e>ActivateWindow(TVGuide)</e>"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!-- WangertTV: Tasten der Fernbedienung in Kodi. Datei wird von WangertTV bei jedem Kodi-Start neu geschrieben. -->
<keymap>
  <global>
    <keyboard>
      <f1>Red</f1>
      <f2>Green</f2>
      <f3>Yellow</f3>
      <f4>Blue</f4>
      <menu>ContextMenu</menu>
      <e>{guide}</e>
      <play_pause>PlayPause</play_pause>
      <stop>Stop</stop>
      <next_track>SkipNext</next_track>
      <prev_track>SkipPrevious</prev_track>
      <browser_back>Back</browser_back>
    </keyboard>
  </global>
  <FullscreenLiveTV>
    <keyboard>
{live}
    </keyboard>
  </FullscreenLiveTV>
</keymap>
"""


KEYMAP = keymap(True)


# Puffer und Netzwerk für stabile Wiedergabe von Streams (waipu.tv, IPTV, Mediatheken)
ADVANCED = """<advancedsettings version="1.0">
  <!-- WangertTV: stabile Wiedergabe. Datei wird von WangertTV gepflegt (Kennzeichen: wangerttv-managed). -->
  <cache>
    <buffermode>1</buffermode>            <!-- alles puffern, auch Internet-Streams -->
    <memorysize>157286400</memorysize>    <!-- 150 MB Puffer (Kodi nutzt bis zum Dreifachen an RAM) -->
    <readfactor>8</readfactor>            <!-- Puffer zügig füllen -->
  </cache>
  <splash>false</splash>                  <!-- kein Kodi-Startbild: WangertTV bleibt sichtbar, bis das Bild läuft -->
  <network>
    <curlclienttimeout>30</curlclienttimeout>
    <curllowspeedtime>30</curllowspeedtime>
    <curlretries>3</curlretries>
  </network>
</advancedsettings>
"""


class Kodi:
    def __init__(self, settings):
        self.settings = settings
        self._rpc_id = 0
        self._lock = threading.Lock()
        self.pending_iptv = False

    # ------------------------------------------------------------------ Konfigurationsdateien
    def prepare(self):
        """Legt Kodi-Konfiguration an, bevor Kodi startet (idempotent)."""
        # Kodi fragt die Durchreich-Fähigkeiten nur beim Start ab – deshalb hier vorher freischalten
        self.setup_hdmi()
        os.makedirs(os.path.join(USERDATA, "keymaps"), exist_ok=True)
        os.makedirs(os.path.join(USERDATA, "peripheral_data"), exist_ok=True)
        guisettings = os.path.join(USERDATA, "guisettings.xml")
        if not os.path.exists(guisettings):
            seeded = {
                "services.esenabled": "true",          # JSON-RPC (TCP 9090) für Programme auf diesem Gerät
                "services.esallinterfaces": "false",
                "services.webserver": "false",
                "lookandfeel.startupwindow": "10000",
                "videoplayer.adjustrefreshrate": "0",   # unter Wayland ohnehin nicht möglich – vermeidet Aussetzer
                "services.devicename": self.settings.get("device_name") or "WangertTV Box",
            }
            body = "\n".join(f'    <setting id="{k}">{escape(v)}</setting>' for k, v in seeded.items() if v)
            with open(guisettings, "w", encoding="utf-8") as fh:
                fh.write(f'<settings version="2">\n{body}\n</settings>\n')
        for name in CEC_PERIPHERALS:
            path = os.path.join(USERDATA, "peripheral_data", name)
            if not os.path.exists(path):
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write('<settings>\n    <setting id="enabled" value="0" />\n</settings>\n')
        with open(os.path.join(USERDATA, "keymaps", "wangerttv.xml"), "w", encoding="utf-8") as fh:
            fh.write(keymap(self.own_ui()))
        self.install_addon()
        adv = os.path.join(USERDATA, "advancedsettings.xml")
        try:
            with open(adv, encoding="utf-8") as fh:
                current = fh.read()
        except OSError:
            current = None
        if current is None or ("wangerttv-managed" in current and current != ADVANCED):
            with open(adv, "w", encoding="utf-8") as fh:   # eigene Datei des Nutzers nie überschreiben
                fh.write(ADVANCED)
        self.write_iptv()
        self.write_waipu()
        self.link_widevine()

    def own_ui(self):
        return not (self.settings.get("tv") or {}).get("kodi_menus")

    @staticmethod
    def _tree_hash(path):
        h = hashlib.sha1()
        for root, dirs, files in os.walk(path):
            dirs.sort()
            for f in sorted(files):
                if f.endswith((".pyc", ".pyo")):
                    continue
                full = os.path.join(root, f)
                h.update(os.path.relpath(full, path).encode())
                try:
                    with open(full, "rb") as fh:
                        h.update(fh.read())
                except OSError:
                    pass
        return h.hexdigest()

    def install_addon(self):
        """WangertTV-Add-on nach ~/.kodi/addons kopieren (nur wenn neu oder geändert)."""
        if not os.path.isdir(ADDON_SRC):
            return False
        try:
            dst = addon_dst()
            if os.path.isdir(dst) and self._tree_hash(ADDON_SRC) == self._tree_hash(dst):
                return True
            shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(ADDON_SRC, dst)
            log.info("Kodi-Add-on %s installiert/aktualisiert", ADDON_ID)
            return True
        except OSError as exc:
            log.warning("Kodi-Add-on nicht installiert: %s", exc)
            return False

    def write_state(self, channels):
        """Senderreihenfolge, Nummern, Logos und Einstellungen für das Kodi-Add-on ablegen."""
        if config.DEV:
            return
        tv = self.settings.get("tv") or {}
        numbers, logo_paths, order = {}, {}, []
        for c in channels or []:
            key = c["name"].casefold().strip()
            order.append(c["name"])
            if c.get("number"):
                numbers[key] = c["number"]
            logo = c.get("logo") or ""
            if logo.startswith("/chlogo/"):
                logo_paths[key] = os.path.join(logos.CACHE, os.path.basename(logo))
        accent = ACCENT_HEX.get(self.settings.get("accent") or "", "3FA9FF")
        data = {"order": order, "hidden": list(tv.get("hidden") or []), "numbers": numbers, "logos": logo_paths,
                "infobar_secs": int(tv.get("infobar_secs", 6) or 0), "back_to": tv.get("back_to") or "live",
                "accent": "FF" + accent, "lang": self.settings.get("lang") or "de", "port": config.PORT,
                "own_ui": self.own_ui()}
        try:
            atomic_write_json(addon_state_file(), data)
        except OSError as exc:
            log.warning("Kodi-Add-on-Zustand nicht geschrieben: %s", exc)

    def apply_tv_ui(self):
        """Umschalten zwischen WangertTV-Info-Leiste und Kodis eigenen Menüs bei laufendem Kodi."""
        own = self.own_ui()
        try:
            with open(os.path.join(USERDATA, "keymaps", "wangerttv.xml"), "w", encoding="utf-8") as fh:
                fh.write(keymap(own))
        except OSError:
            return
        if "error" in self.rpc("Input.ExecuteAction", {"action": "reloadkeymaps"}):
            self.builtin("ReloadKeymaps")
        self._set("pvrmenu.displaychannelinfo", 0 if own else 5)
        self._set("pvrplayback.confirmchannelswitch", not own)

    def write_waipu(self):
        """waipu.tv-PVR-Client (pvr.waipu) mit den Zugangsdaten aus WangertTV einrichten."""
        w = self.settings.get("waipu") or {}
        if not (w.get("username") and w.get("password")):
            return False
        values = {
            "kodi_addon_instance_name": "waipu.tv", "kodi_addon_instance_enabled": "true",
            "provider_select": str(int(w.get("provider") or 0)), "username": w["username"], "password": w["password"],
            # Alle Sender über inputstream.adaptive: schneller Start, und kein Wechsel zwischen zwei Abspiel-Modulen
            # beim Umschalten (ffmpegdirect <-> adaptive brachte Kodi beim Umschalten zum Absturz)
            "protocol": "auto", "streaming_use_ffmpegdirect": "false",
        }
        folder = os.path.join(USERDATA, "addon_data", "pvr.waipu")
        os.makedirs(folder, exist_ok=True)
        body = "\n".join(f'    <setting id="{k}">{escape(v)}</setting>' for k, v in values.items())
        new = f'<settings version="2">\n{body}\n</settings>\n'
        changed = False
        # Kodi 21 nutzt je nach Add-on settings.xml oder instance-settings-1.xml – beide schreiben
        for name in ("instance-settings-1.xml", "settings.xml"):
            target = os.path.join(folder, name)
            try:
                with open(target, encoding="utf-8") as fh:
                    if fh.read() == new:
                        continue
            except OSError:
                pass
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(new)
            os.chmod(target, 0o600)
            changed = True
        if changed:
            self.pending_iptv = True
        return True

    @staticmethod
    def link_widevine():
        """Widevine (Kopierschutz für waipu.tv, Netflix-Add-ons …) aus Google Chrome für Kodi bereitstellen."""
        cdm_dir = os.path.join(KODI_HOME, "cdm")
        target = os.path.join(cdm_dir, "libwidevinecdm.so")
        if os.path.exists(target) and not os.path.islink(target):
            return   # vom InputStream Helper installiert – nicht anfassen
        for src in ("/opt/google/chrome/WidevineCdm/_platform_specific/linux_x64/libwidevinecdm.so",
                    "/usr/lib/chromium/WidevineCdm/_platform_specific/linux_x64/libwidevinecdm.so"):
            if os.path.exists(src):
                os.makedirs(cdm_dir, exist_ok=True)
                if os.path.islink(target) and os.readlink(target) == src:
                    return
                if os.path.lexists(target):
                    os.remove(target)
                os.symlink(src, target)
                manifest = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(src))), "manifest.json")
                if os.path.exists(manifest) and not os.path.lexists(os.path.join(cdm_dir, "manifest.json")):
                    os.symlink(manifest, os.path.join(cdm_dir, "manifest.json"))
                return

    def write_iptv(self):
        iptv = self.settings.get("iptv") or {}
        m3u = (iptv.get("m3u") or "").strip()
        if not m3u:
            return False
        epg = (iptv.get("epg") or "").strip()
        values = {
            "kodi_addon_instance_name": "WangertTV IPTV",
            "kodi_addon_instance_enabled": "true",
            "m3uPathType": "1", "m3uUrl": m3u, "m3uCache": "true",
            "m3uRefreshMode": "2", "m3uRefreshHour": "4",
            "epgPathType": "1", "epgUrl": epg, "epgCache": "true",
            "logoPathType": "1", "logoFromEpg": "1",
            "timeshiftEnabled": "true" if iptv.get("timeshift") else "false",
            # HLS-Sender (.m3u8) über inputstream.adaptive statt über Kodis eingebauten Demuxer abspielen.
            # Viele Sender (z. B. Deluxe Music) liefern den Ton als eigene Spur (EXT-X-MEDIA TYPE=AUDIO); damit
            # kommt Kodis eingebauter Demuxer nicht klar – Bild läuft, Ton kommt kurz und ist dann weg.
            "useInputstreamAdaptiveforHls": "true",
        }
        if m3u.startswith("/"):
            values.update({"m3uPathType": "0", "m3uPath": m3u, "m3uUrl": ""})
        if epg.startswith("/"):
            values.update({"epgPathType": "0", "epgPath": epg, "epgUrl": ""})
        folder = os.path.join(USERDATA, "addon_data", "pvr.iptvsimple")
        os.makedirs(folder, exist_ok=True)
        body = "\n".join(f'    <setting id="{k}">{escape(v)}</setting>' for k, v in values.items())
        target = os.path.join(folder, "instance-settings-1.xml")
        new = f'<settings version="2">\n{body}\n</settings>\n'
        try:
            with open(target, encoding="utf-8") as fh:
                if fh.read() == new:
                    return True
        except OSError:
            pass
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(new)
        self.pending_iptv = True
        return True

    # ------------------------------------------------------------------ JSON-RPC
    def rpc(self, method, params=None, timeout=6):
        if config.DEV:
            log.info("DEV kodi rpc %s %s", method, params)
            return {"result": "OK"}
        with self._lock:
            self._rpc_id += 1
            req_id = self._rpc_id
        payload = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            payload["params"] = params
        try:
            with socket.create_connection(("127.0.0.1", RPC_PORT), timeout=timeout) as sock:
                sock.sendall(json.dumps(payload).encode())
                decoder = json.JSONDecoder()
                buf = ""
                deadline = time.time() + timeout
                while time.time() < deadline:
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    buf += chunk.decode("utf-8", "replace")
                    while buf:
                        buf = buf.lstrip()
                        try:
                            obj, end = decoder.raw_decode(buf)
                        except ValueError:
                            break
                        buf = buf[end:]
                        if obj.get("id") == req_id:
                            return obj
        except OSError as exc:
            return {"error": {"message": str(exc)}}
        return {"error": {"message": "timeout"}}

    def ready(self):
        return "result" in self.rpc("JSONRPC.Ping", timeout=4)

    def wait_ready(self, timeout=45):
        end = time.time() + timeout
        while time.time() < end:
            if self.ready():
                return True
            time.sleep(1)
        return False

    def builtin(self, action):
        """Führt eine Kodi-Builtin-Aktion über kodi-send (EventServer) aus."""
        if config.DEV:
            log.info("DEV kodi-send %s", action)
            return True
        if not which("kodi-send"):
            log.warning("kodi-send fehlt – Aktion %s übersprungen", action)
            return False
        return run(["kodi-send", "--host=127.0.0.1", f"--action={action}"], timeout=10)[0] == 0

    # ------------------------------------------------------------------ Nach dem Start
    def post_start(self):
        """Wird nach jedem Kodi-Start ausgeführt: IPTV aktivieren, Sprache, Audio."""
        if not self.wait_ready():
            log.warning("Kodi antwortet nicht auf JSON-RPC")
            return False
        w = self.settings.get("waipu") or {}
        if w.get("username") and w.get("password"):
            res = self.rpc("Addons.SetAddonEnabled", {"addonid": "pvr.waipu", "enabled": True})
            if "error" in res:
                log.warning("waipu.tv-Client konnte nicht aktiviert werden: %s", res["error"])
        iptv = self.settings.get("iptv") or {}
        if iptv.get("m3u"):
            self.rpc("Addons.SetAddonEnabled", {"addonid": "inputstream.adaptive", "enabled": True})
            res = self.rpc("Addons.SetAddonEnabled", {"addonid": "pvr.iptvsimple", "enabled": True})
            if "error" in res:
                log.warning("IPTV Simple konnte nicht aktiviert werden: %s", res["error"])
            elif self.pending_iptv:
                self.pending_iptv = False
        kodi_cfg = self.settings.get("kodi") or {}
        lang = self.settings.get("lang")
        if not kodi_cfg.get("language_done"):
            addon = LANG_ADDON.get(lang, LANG_ADDON["de"])
            if lang != "en":
                self.builtin(f"InstallAddon({addon})")
                for _ in range(30):
                    if "result" in self.rpc("Addons.GetAddonDetails", {"addonid": addon}):
                        break
                    time.sleep(2)
            self.rpc("Settings.SetSettingValue", {"setting": "locale.language", "value": addon})
            self.settings.patch({"kodi": {"language_done": True}})
        self.rpc("Settings.SetSettingValue", {"setting": "services.devicename",
                                              "value": self.settings.get("device_name") or "WangertTV Box"})
        self.apply_audio_video()
        own = self.own_ui()
        if os.path.isdir(addon_dst()):
            res = self.rpc("Addons.SetAddonEnabled", {"addonid": ADDON_ID, "enabled": True})
            if "error" in res:
                log.warning("WangertTV-Add-on in Kodi nicht aktiviert: %s", res["error"])
        # Kodis eigene Sender-Info beim Umschalten aus – die WangertTV-Info-Leiste übernimmt
        self._set("pvrmenu.displaychannelinfo", 0 if own else 5)
        self._set("pvrplayback.confirmchannelswitch", not own)
        return True

    # Kodi-Werte für audiooutput.channels (AEStdChLayout): 2.0 = 1, 5.1 = 8, 7.1 = 10
    CHANNELS = {"2.0": 1, "5.1": 8, "7.1": 10}

    def _set(self, setting, value):
        res = self.rpc("Settings.SetSettingValue", {"setting": setting, "value": value})
        if "error" in res:
            log.info("Kodi-Einstellung %s nicht gesetzt: %s", setting, res["error"].get("message"))

    def apply_audio_video(self):
        """Ton (Lautsprecher, Dolby/DTS-Durchreichen) und Hardware-Videodekodierung in Kodi einstellen."""
        kodi_cfg = self.settings.get("kodi") or {}
        speakers = self.settings.get("audio", "speakers") or "2.0"
        passthrough = bool(kodi_cfg.get("passthrough"))
        allowed = self.passthrough_formats()
        if passthrough and allowed:
            # Zuerst "Beste Übereinstimmung": Bei "Fest" blendet Kodi alle Durchreich-Einstellungen aus und
            # lehnt sie dann ab ("Invalid params")
            self._set("audiooutput.config", 2)
        # Kann die HDMI-Strecke nur Stereo-PCM (SOF-Treiber, Receiver meldet 2 Kanäle) oder läuft HDMI im
        # Automatik-Modus als Stereo, kommt 5.1 nur als Dolby Digital/DTS am Receiver an:
        #  - Dolby Digital/DTS-Spuren reicht Kodi unverändert durch,
        #  - Mehrkanal-Ton, den es nicht durchreichen kann (AAC 5.1, Dolby Digital Plus, TrueHD …), rechnet Kodi in
        #    Dolby Digital 5.1 um ("Transcode to AC3"),
        #  - Stereo bleibt Stereo-PCM – der Receiver zeigt 2.0 und schaltet bei Raumklang selbst auf 5.1.
        # Kodi rechnet nur um, wenn seine Lautsprecher-Einstellung 2.0 ist (ActiveAE: "no multichannel pcm") –
        # deshalb steht sie dann auf 2.0, obwohl 5.1/7.1-Lautsprecher dranhängen.
        hdmi_name = system.hdmi_sink()
        rcv = system.hdmi_receiver() or {}
        node_ch = system._sink_channels(hdmi_name) if hdmi_name else None
        stereo_link = (node_ch or 2) <= 2 or int(rcv.get("pcm_channels") or 2) <= 2
        pcm = "2.0" if stereo_link else self.pcm_layout()
        transcode = bool(passthrough and "ac3" in allowed and speakers != "2.0" and pcm == "2.0")
        self._set("audiooutput.channels", self.CHANNELS.get(pcm, 1))
        # Dolby Digital (AC3), Dolby Digital Plus (E-AC3, auch Atmos), DTS, Dolby TrueHD (auch Atmos), DTS-HD MA (auch DTS:X)
        # Nur Formate durchreichen, die der Receiver/Fernseher laut HDMI auch kann – der Rest wird von Kodi
        # in PCM umgewandelt (sonst bliebe es bei nicht unterstützten Formaten stumm).
        system.set_passthrough_formats(allowed)
        self._set("audiooutput.passthrough", passthrough)
        for fmt in ("ac3", "eac3", "dts", "truehd", "dtshd"):
            self._set(f"audiooutput.{fmt}passthrough", fmt in allowed)
        # Tonverbindung zum Receiver 1 Minute offen halten: sonst schneidet der Receiver nach Pausen/Senderwechsel
        # die ersten Sekunden ab oder synchronisiert neu (Aussetzer)
        self._set("audiooutput.streamsilence", 1)
        # Bildwiederholrate umschalten geht unter Wayland nicht – ausgeschaltet vermeidet schwarze Bilder/Aussetzer
        self._set("videoplayer.adjustrefreshrate", 0)
        # Stereo auf alle Lautsprecher hochrechnen nur bei Mehrkanal-PCM ("multi"). Im Automatik-Modus bleibt Stereo
        # Stereo – mit Umrechnung würde Kodi sonst auch Stereo als Dolby Digital 5.1 schicken (Receiver immer auf 5.1).
        upmix = (self.settings.get("audio", "upmix") is not False) and pcm != "2.0"
        self._set("audiooutput.stereoupmix", upmix)
        # Kodi spielt immer direkt auf den HDMI-Ausgang – nie über den virtuellen Stereo-Upmix "WangertTV Surround"
        # (über den ginge weder echtes 5.1 noch Dolby/DTS-Durchreichen).
        hdmi = system.hdmi_sink()
        device = None
        for _ in range(6 if hdmi else 0):
            # direkt nach dem Start kennt Kodi den HDMI-Ausgang manchmal noch nicht – kurz warten statt "Standard"
            device = self._device_option("audiooutput.audiodevice", hdmi)
            if device:
                break
            time.sleep(1)
        if device:
            self._set("audiooutput.audiodevice", device)
        pt_device = None
        if passthrough and allowed:
            for _ in range(4):
                pt_device = self._passthrough_device()
                if pt_device:
                    break
                time.sleep(0.5)
        if pt_device:
            self._set("audiooutput.passthroughdevice", pt_device)
        # erst jetzt (Ausgang + Durchreich-Gerät stehen) zeigt Kodi die Umrechnung an und nimmt sie an
        self._set("audiooutput.ac3transcode", transcode and bool(pt_device))
        # Ausgabeformat: Ohne Durchreichen "Fest" (immer gleiche Kanalzahl und 48 kHz). Sonst öffnet Kodi den
        # HDMI-Ausgang bei jedem Wechsel Stereo <-> 5.1 (Senderwechsel, Werbung, Sendungswechsel) neu und der
        # Receiver schaltet jedes Mal kurz stumm. Mit Durchreichen muss es "Beste Übereinstimmung" sein.
        self._set("audiooutput.config", 2 if pt_device else 1)
        self._set("audiooutput.samplerate", 48000)
        pw_iec = system._node_offers_iec958(hdmi) if (passthrough and hdmi) else None
        if transcode and pt_device:
            log.info("Kodi-Ton: HDMI als Stereo-PCM – Mehrkanal-Ton kommt als Dolby Digital/DTS (umgerechnet, falls nötig)")
        log.info("Kodi-Ton: Ausgang %s, Lautsprecher %s (PCM %s), Durchreichen: %s%s", device or "Standard", speakers, pcm,
                 (", ".join(allowed) or "aus") if pt_device else ("kein fähiger Ausgang" if passthrough else "aus"),
                 "" if pw_iec is None else f" (PipeWire bietet IEC958 an: {'ja' if pw_iec else 'nein'})")
        # Hardware-Dekodierung per VA-API (Intel Iris Xe: H.264, HEVC 8/10-bit, VP9, AV1, MPEG-2, VC-1)
        for setting in ("videoplayer.usevaapi", "videoplayer.usevaapimpeg2", "videoplayer.usevaapimpeg4",
                        "videoplayer.usevaapivc1", "videoplayer.usevaapivp8", "videoplayer.usevaapivp9",
                        "videoplayer.usevaapihevc", "videoplayer.usevaapiav1", "videoplayer.prefervaapirender"):
            self._set(setting, True)

    PT_FORMATS = ("ac3", "eac3", "dts", "truehd", "dtshd")

    def passthrough_formats(self):
        """Formate, die Kodi an den Receiver durchreichen darf (leer = alles als PCM)."""
        kodi_cfg = self.settings.get("kodi") or {}
        if not kodi_cfg.get("passthrough"):
            return []
        # Nur Formate, die der Receiver/Fernseher laut HDMI auch kann – sonst bliebe es stumm.
        rcv = system.hdmi_receiver() or {}
        known = rcv.get("formats") if rcv else None   # leere Liste = Gerät kann nur PCM
        # Dolby Digital Plus (E-AC3), TrueHD und DTS-HD brauchen HDMI mit hoher Bitrate (HBR). Über PipeWire bricht
        # das bei vielen Receivern nach wenigen Sekunden ab ("Ton kommt kurz und ist wieder weg") – deshalb
        # standardmäßig aus: Kodi wandelt diese Spuren dann selbst in mehrkanaliges PCM um (5.1 bleibt 5.1).
        hd = bool(kodi_cfg.get("hd_passthrough"))
        return [f for f in self.PT_FORMATS if (known is None or f in known) and (hd or f in ("ac3", "dts"))]

    def pcm_layout(self):
        """Kanäle, mit denen der HDMI-Ausgang als PCM läuft.

        "auto" (Standard): HDMI läuft als Stereo-PCM, 5.1/7.1 kommt als Dolby Digital/DTS-Datenstrom (Durchreichen
        bzw. Umrechnen in Dolby Digital) – der Receiver zeigt dann 2.0 bei Stereo und 5.1 bei Raumklang und schaltet
        selbst um. Ein fester 8-Kanal-PCM-Ausgang ließe ihn dagegen immer auf 5.1/7.1 stehen.
        "multi": HDMI läuft immer mit allen Kanälen als PCM (für Stereo-Hochmischen auf alle Lautsprecher).
        Ohne Durchreichen (oder kann der Receiver kein Dolby Digital) bleibt nur Mehrkanal-PCM für echtes 5.1."""
        audio = self.settings.get("audio") or {}
        speakers = audio.get("speakers") or "2.0"
        if speakers == "2.0" or audio.get("pcm_mode") == "multi":
            return speakers
        return "2.0" if "ac3" in self.passthrough_formats() else speakers

    def _device_option(self, setting_id, want):
        """Sucht in Kodis Geräteliste den Eintrag, der zum PipeWire-Ausgang passt."""
        if not want:
            return None
        res = self.rpc("Settings.GetSettings", {"filter": {"section": "system", "category": "audio"}, "level": "expert"})
        result = res.get("result")
        settings = (result.get("settings") if isinstance(result, dict) else None) or []
        entry = next((s for s in settings if s.get("id") == setting_id), None)
        options = [o.get("value", "") for o in (entry or {}).get("options", [])]
        return next((o for o in options if want.lower() in o.lower()), None)

    def _passthrough_device(self):
        """Wählt in Kodi den HDMI-Ausgang für das Durchreichen (PipeWire-Gerät mit IEC958-Formaten)."""
        res = self.rpc("Settings.GetSettings", {"filter": {"section": "system", "category": "audio"}, "level": "expert"})
        result = res.get("result")
        settings = (result.get("settings") if isinstance(result, dict) else None) or []
        entry = next((s for s in settings if s.get("id") == "audiooutput.passthroughdevice"), None)
        options = [o.get("value", "") for o in (entry or {}).get("options", [])]
        if not options:
            return None
        # Immer der echte HDMI-Ausgang – nie der virtuelle Upmix (der kann kein Dolby/DTS durchreichen)
        options = [o for o in options if "wangerttv" not in o.lower()]
        for want in (system.hdmi_sink(), "hdmi", "HDMI"):
            hit = next((o for o in options if want and want in o), None)
            if hit:
                return hit
        return None

    _hdmi_lock = threading.Lock()

    def setup_hdmi(self):
        """HDMI-Ausgang fürs Durchreichen vorbereiten (nur solange Kodi nicht läuft – startet ggf. WirePlumber neu)."""
        with self._hdmi_lock:
            try:
                formats = self.passthrough_formats()
                system.hdmi_setup(bool(formats), self.pcm_layout())
                system.set_passthrough_formats(formats)
            except Exception:  # noqa: BLE001
                log.exception("HDMI-Durchreichen")

    def apply_passthrough(self):
        if self.ready():
            threading.Thread(target=self.apply_audio_video, daemon=True).start()
        else:
            threading.Thread(target=self.setup_hdmi, daemon=True).start()

    # ------------------------------------------------------------------ Navigation
    def open_view(self, view):
        if view.startswith("addon:"):
            addon = view.split(":", 1)[1]
            details = self.rpc("Addons.GetAddonDetails", {"addonid": addon})
            if "error" in details:
                self.builtin(f"InstallAddon({addon})")
                return {"ok": True, "installing": True}
            self.rpc("Addons.SetAddonEnabled", {"addonid": addon, "enabled": True})
            res = self.rpc("GUI.ActivateWindow", {"window": "videos", "parameters": [f"plugin://{addon}/", "return"]})
        elif view.startswith("path:"):
            # Ordner (z. B. USB-Stick) in Kodis Videos-Ansicht öffnen
            res = self.rpc("GUI.ActivateWindow", {"window": "videos", "parameters": [view[5:].rstrip("/") + "/", "return"]})
        else:
            res = self.rpc("GUI.ActivateWindow", {"window": view or "home"})
        return {"ok": "result" in res}

    def play_channel(self, name):
        res = self.rpc("PVR.GetChannels", {"channelgroupid": "alltv", "properties": ["channelnumber"]})
        result = res.get("result")
        channels = (result.get("channels") if isinstance(result, dict) else None) or []
        wanted = (name or "").casefold().strip()
        match = next((c for c in channels if c.get("label", "").casefold().strip() == wanted), None)
        if not match:
            match = next((c for c in channels if wanted and wanted in c.get("label", "").casefold()), None)
        if not match:
            return {"ok": False, "message": "channel_not_found"}
        self.stop_playback()
        res = self.rpc("Player.Open", {"item": {"channelid": match["channelid"]}})
        return {"ok": "result" in res}

    def stop_playback(self, timeout=3.0):
        """Laufende Wiedergabe sauber beenden, bevor ein anderer Sender startet. Ein Senderwechsel mitten in der
        laufenden Wiedergabe (Videodecoder wird weiterverwendet) ließ Kodi mit Hardware-Dekodierung abstürzen."""
        if not self.is_playing():
            return
        self.rpc("Player.Stop", {"playerid": 1}, timeout=3)
        end = time.time() + timeout
        while time.time() < end and self.is_playing():
            time.sleep(0.1)

    def ping(self, timeout=5):
        return "result" in self.rpc("JSONRPC.Ping", timeout=timeout)

    def save_crash_log(self, reason):
        """Kodi-Protokoll für die Fehlersuche sichern (~/.cache/wangerttv/logs/kodi-crash-*.log)."""
        src = os.path.join(KODI_HOME, "temp", "kodi.log")
        dst = os.path.join(config.LOG_DIR, f"kodi-crash-{time.strftime('%Y%m%d-%H%M%S')}.log")
        try:
            with open(src, "rb") as fh:
                data = fh.read()[-400000:]
            with open(dst, "wb") as fh:
                fh.write(f"# WangertTV: {reason}\n".encode() + data)
            logs = sorted(f for f in os.listdir(config.LOG_DIR) if f.startswith("kodi-crash-"))
            for old in logs[:-5]:
                os.remove(os.path.join(config.LOG_DIR, old))
        except OSError:
            pass
        return dst

    def quit(self):
        return "result" in self.rpc("Application.Quit")

    def is_playing(self):
        res = self.rpc("Player.GetActivePlayers", timeout=2)
        return bool(res.get("result"))
