"""Kodi-Anbindung: Erstkonfiguration, IPTV-Einrichtung und Steuerung per JSON-RPC (TCP 9090)."""
import json
import os
import socket
import threading
import time
from xml.sax.saxutils import escape

from . import config
from .util import log, run, which

KODI_HOME = os.path.join(config.HOME, ".kodi")
USERDATA = os.path.join(KODI_HOME, "userdata")
RPC_PORT = 9090

# Pulse-Eight USB-CEC-Adapter: Kodi darf ihn nicht selbst öffnen – das übernimmt wtv-cec systemweit.
CEC_PERIPHERALS = ["usb_2548_1001_CEC_Adapter.xml", "usb_2548_1002_CEC_Adapter.xml"]

LANG_ADDON = {"de": "resource.language.de_de", "en": "resource.language.en_gb"}

KEYMAP = """<?xml version="1.0" encoding="UTF-8"?>
<!-- WangertTV: Tasten der TV-/CEC-Fernbedienung (über wtv-cec) in Kodi -->
<keymap>
  <global>
    <keyboard>
      <f1>Red</f1>
      <f2>Green</f2>
      <f3>Yellow</f3>
      <f4>Blue</f4>
      <menu>ContextMenu</menu>
      <e>ActivateWindow(TVGuide)</e>
      <play_pause>PlayPause</play_pause>
      <stop>Stop</stop>
      <next_track>SkipNext</next_track>
      <prev_track>SkipPrevious</prev_track>
      <browser_back>Back</browser_back>
    </keyboard>
  </global>
  <FullscreenLiveTV>
    <keyboard>
      <pageup>ChannelUp</pageup>
      <pagedown>ChannelDown</pagedown>
      <e>ActivateWindow(TVGuide)</e>
    </keyboard>
  </FullscreenLiveTV>
</keymap>
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
        os.makedirs(os.path.join(USERDATA, "keymaps"), exist_ok=True)
        os.makedirs(os.path.join(USERDATA, "peripheral_data"), exist_ok=True)
        guisettings = os.path.join(USERDATA, "guisettings.xml")
        if not os.path.exists(guisettings):
            seeded = {
                "services.esenabled": "true",          # JSON-RPC (TCP 9090) für Programme auf diesem Gerät
                "services.esallinterfaces": "false",
                "services.webserver": "false",
                "lookandfeel.startupwindow": "10000",
                "videoplayer.adjustrefreshrate": "2",   # Bildwiederholrate beim Start/Stopp anpassen
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
            fh.write(KEYMAP)
        self.write_iptv()

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
        return "result" in self.rpc("JSONRPC.Ping", timeout=2)

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
        iptv = self.settings.get("iptv") or {}
        if iptv.get("m3u"):
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
        self._set("audiooutput.channels", self.CHANNELS.get(speakers, 1))
        passthrough = bool(kodi_cfg.get("passthrough"))
        # Dolby Digital (AC3), Dolby Digital Plus (E-AC3, auch Atmos), DTS, Dolby TrueHD (auch Atmos), DTS-HD MA (auch DTS:X)
        for setting in ("audiooutput.passthrough", "audiooutput.ac3passthrough", "audiooutput.eac3passthrough",
                        "audiooutput.dtspassthrough", "audiooutput.truehdpassthrough", "audiooutput.dtshdpassthrough"):
            self._set(setting, passthrough)
        self._set("audiooutput.ac3transcode", False)
        if passthrough:
            device = self._passthrough_device()
            if device:
                self._set("audiooutput.passthroughdevice", device)
        # Hardware-Dekodierung per VA-API (Intel Iris Xe: H.264, HEVC 8/10-bit, VP9, AV1, MPEG-2, VC-1)
        for setting in ("videoplayer.usevaapi", "videoplayer.usevaapimpeg2", "videoplayer.usevaapimpeg4",
                        "videoplayer.usevaapivc1", "videoplayer.usevaapivp8", "videoplayer.usevaapivp9",
                        "videoplayer.usevaapihevc", "videoplayer.usevaapiav1", "videoplayer.prefervaapirender"):
            self._set(setting, True)

    def _passthrough_device(self):
        """Wählt in Kodi den HDMI-Ausgang für das Durchreichen (PipeWire-Gerät mit IEC958-Formaten)."""
        res = self.rpc("Settings.GetSettings", {"filter": {"section": "system", "category": "audio"}, "level": "expert"})
        settings = (res.get("result") or {}).get("settings") or []
        entry = next((s for s in settings if s.get("id") == "audiooutput.passthroughdevice"), None)
        options = [o.get("value", "") for o in (entry or {}).get("options", [])]
        if not options:
            return None
        default_sink = ""
        if not config.DEV:
            default_sink = run(["pactl", "get-default-sink"], timeout=5)[1].strip()
        for want in (default_sink, "hdmi", "HDMI"):
            hit = next((o for o in options if want and want in o), None)
            if hit:
                return hit
        return None

    def apply_passthrough(self):
        if self.ready():
            threading.Thread(target=self.apply_audio_video, daemon=True).start()

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
        else:
            res = self.rpc("GUI.ActivateWindow", {"window": view or "home"})
        return {"ok": "result" in res}

    def play_channel(self, name):
        res = self.rpc("PVR.GetChannels", {"channelgroupid": "alltv", "properties": ["channelnumber"]})
        channels = (res.get("result") or {}).get("channels") or []
        wanted = (name or "").casefold().strip()
        match = next((c for c in channels if c.get("label", "").casefold().strip() == wanted), None)
        if not match:
            match = next((c for c in channels if wanted and wanted in c.get("label", "").casefold()), None)
        if not match:
            return {"ok": False, "message": "channel_not_found"}
        res = self.rpc("Player.Open", {"item": {"channelid": match["channelid"]}})
        return {"ok": "result" in res}

    def quit(self):
        return "result" in self.rpc("Application.Quit")

    def is_playing(self):
        res = self.rpc("Player.GetActivePlayers", timeout=2)
        return bool(res.get("result"))
