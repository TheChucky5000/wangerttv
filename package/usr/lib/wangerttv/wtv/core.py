"""Zentrale Logik: Einstellungen anwenden, Einrichtung, Standby/Aufwachen, Sleep-Timer, Fernbedienungsaktionen."""
import os
import threading
import time

from . import config, remote, system, updates
from .android import Android
from .apps import AppManager
from .iptv import ChannelStore
from .kodi import Kodi
from .media import Receivers
from .sway import HOME_WS, sway
from .util import bus, log, run, which


class Core:
    def __init__(self):
        config.ensure_dirs()
        self.settings = config.Settings()
        self.kodi = Kodi(self.settings)
        self.android = Android()
        self.apps = AppManager(self.settings, self.kodi, self.android)
        self.channels = ChannelStore(self.settings)
        self.receivers = Receivers(self.settings)
        self.standby = False
        self.sleep_timer = None      # {"ends": ts, "timer": Timer}
        self._power_lock = threading.Lock()
        sway.on_focus(self._on_focus)

    # ------------------------------------------------------------------ Start
    STANDBY_FLAG = os.path.join(config.CACHE_DIR, "standby")        # Box ist im Standby (liest der Updater)
    BOOT_STANDBY = os.path.join(config.CACHE_DIR, "boot-standby")   # Updater hat nachts neu gestartet

    def _set_standby_flag(self, on):
        try:
            if on:
                open(self.STANDBY_FLAG, "w").close()
            elif os.path.exists(self.STANDBY_FLAG):
                os.remove(self.STANDBY_FLAG)
        except OSError:
            pass

    def start(self):
        self._set_standby_flag(False)
        quiet_boot = os.path.exists(self.BOOT_STANDBY)
        if quiet_boot:
            try:
                os.remove(self.BOOT_STANDBY)
            except OSError:
                pass
        system.display_apply(self.settings.get("display") or {})
        if self.settings.get("audio", "sink"):
            system.audio_set(sink=self.settings.get("audio", "sink"))
        system.audio_set_speakers(self.settings.get("audio", "speakers") or "2.0")
        self.apps.start_launcher()
        self.apps.watch()
        sway.watch()
        updates.watch()
        self.receivers.apply()
        self.receivers.supervise()
        if self.settings.get("setup_done"):
            threading.Thread(target=self._after_setup_tasks, daemon=True).start()
            if quiet_boot:
                # Nach einem automatischen Update-Neustart in der Nacht: im Standby bleiben, Fernseher bleibt aus
                log.info("Neustart nach automatischem Update – bleibe im Standby")
                threading.Timer(3, self.enter_standby, args=("auto",)).start()
            elif self.settings.get("cec", "tv_on_with_box"):
                # Box (neu) gestartet -> Fernseher einschalten und Eingang umschalten
                threading.Timer(4, lambda: remote.send("tv_on")).start()

    def _on_focus(self, workspace):
        remote.send("ctx", value="kodi" if workspace == "app-kodi" else "default", timeout=1)
        bus.emit("focus", home=workspace == HOME_WS, workspace=workspace)

    # ------------------------------------------------------------------ Einstellungen
    def patch_settings(self, patch):
        changed = self.settings.patch(patch)
        self.apply(changed)
        return changed

    def apply(self, changed):
        s = self.settings
        if "device_name" in changed:
            system.set_hostname(s.get("device_name"))
            self.receivers.restart_all()
        if "timezone" in changed:
            system.set_timezone(s.get("timezone"))
        if "lang" in changed:
            system.set_locale(s.get("lang"))
            s.patch({"kodi": {"language_done": False}})
        if "display" in changed:
            system.display_apply(s.get("display") or {})
        if "audio" in changed:
            if s.get("audio", "sink"):
                system.audio_set(sink=s.get("audio", "sink"))
            system.audio_set_speakers(s.get("audio", "speakers") or "2.0")
            self.kodi.apply_passthrough()
        if "waipu" in changed:
            self.kodi.write_waipu()
            if self.kodi.ready():
                threading.Thread(target=self.kodi.post_start, daemon=True).start()
        if "iptv" in changed:
            self.kodi.write_iptv()
            self.channels.channels(force=True)
            if self.kodi.ready():
                threading.Thread(target=self.kodi.post_start, daemon=True).start()
        if "kodi" in changed:
            self.kodi.apply_passthrough()
        if "airplay" in changed or "spotify_connect" in changed:
            self.receivers.apply()
        if "remote" in changed or "cec" in changed:
            remote.send("reload")
        if "apps" in changed:
            bus.emit("apps")

    def finish_setup(self):
        s = self.settings
        s.patch({"setup_done": True})
        system.set_hostname(s.get("device_name"))
        system.set_timezone(s.get("timezone"))
        system.set_locale(s.get("lang"))
        try:
            self.kodi.prepare()
        except OSError as exc:
            log.warning("Kodi-Vorbereitung: %s", exc)
        self.receivers.apply()
        remote.send("reload")
        threading.Thread(target=self._after_setup_tasks, daemon=True).start()
        if (s.get("android") or {}).get("wanted") and not self.android.status()["initialized"]:
            self.android.init()
        return True

    def _after_setup_tasks(self):
        time.sleep(5)
        self.apps.ensure_defaults_installed()
        self.channels.channels()

    def factory_reset(self):
        return system.systemctl_start("wtv-reset.service")

    # ------------------------------------------------------------------ Standby / Aufwachen
    def enter_standby(self, source="key"):
        with self._power_lock:
            if self.standby:
                return
            self.standby = True
        self._set_standby_flag(True)
        log.info("Standby (%s)", source)
        bus.emit("standby", on=True)
        # Wiedergabe überall anhalten
        if "kodi" in self.apps.running:
            self.kodi.rpc("Player.Stop", {"playerid": 1}, timeout=2)
        if which("playerctl"):
            run(["playerctl", "-a", "pause"], timeout=5)
        self.receivers.restart_all()   # beendet laufende AirPlay-/Spotify-Wiedergabe
        self.apps.home()
        if source not in ("cec", "auto") and self.settings.get("cec", "tv_off_on_standby"):
            remote.send("tv_off")
        time.sleep(0.5)
        system.display_power(False)
        system.start_wake_watch(os.path.join(config.LIB, "bin", "wtvctl") + " wake")
        self.cancel_sleep_timer()
        if self.settings.get("sleep_action") == "suspend" and source == "timer":
            system.power("suspend")

    def wake(self, source="key"):
        with self._power_lock:
            if not self.standby:
                system.display_power(True)
                return
            self.standby = False
        self._set_standby_flag(False)
        log.info("Aufwachen (%s)", source)
        system.stop_wake_watch()
        system.display_power(True)
        if source != "cec" and self.settings.get("cec", "tv_on_with_box"):
            remote.send("tv_on")
        bus.emit("standby", on=False)

    def power_key(self):
        """Aus-Taste der Fernbedienung: Standby an/aus (oder Menü, je nach Einstellung)."""
        if self.standby:
            self.wake("key")
        elif self.settings.get("power_key") == "menu":
            self.apps.home()
            bus.emit("power_menu")
        else:
            threading.Thread(target=self.enter_standby, args=("key",), daemon=True).start()

    def power(self, action, source="ui"):
        if action == "standby":
            threading.Thread(target=self.enter_standby, args=(source,), daemon=True).start()
            return True
        if action == "wake":
            self.wake(source)
            return True
        if action in ("reboot", "poweroff"):
            if self.settings.get("cec", "tv_off_on_standby") and action == "poweroff":
                remote.send("tv_off")
            return system.power(action)
        if action == "suspend":
            return system.power("suspend")
        return False

    # ------------------------------------------------------------------ Sleep-Timer
    def set_sleep_timer(self, minutes):
        self.cancel_sleep_timer()
        minutes = int(minutes or 0)
        if minutes <= 0:
            bus.emit("sleeptimer", ends=0)
            return 0
        action = self.settings.get("sleep_action")

        def fire():
            self.sleep_timer = None
            bus.emit("sleeptimer", ends=0)
            if action == "poweroff":
                self.power("poweroff")
            else:
                self.enter_standby("timer")
        timer = threading.Timer(minutes * 60, fire)
        timer.daemon = True
        timer.start()
        ends = time.time() + minutes * 60
        self.sleep_timer = {"ends": ends, "timer": timer}
        bus.emit("sleeptimer", ends=ends)
        return ends

    def cancel_sleep_timer(self):
        if self.sleep_timer:
            self.sleep_timer["timer"].cancel()
            self.sleep_timer = None

    def sleep_timer_ends(self):
        return self.sleep_timer["ends"] if self.sleep_timer else 0

    # ------------------------------------------------------------------ Fernbedienung
    def remote_action(self, action, source="remote"):
        """Aktion von einer zugewiesenen Fernbedienungstaste oder vom CEC-Dienst."""
        if self.standby and action not in ("standby", "wake"):
            self.wake(source)
            return True
        if action.startswith("app:"):
            res = self.apps.launch(action[4:])
            if res.get("internal"):
                self.apps.home()
                bus.emit("open", target=res["internal"])
            return res.get("ok", False)
        if action == "home":
            self.apps.home()
        elif action == "settings":
            self.apps.home()
            bus.emit("open", target="settings")
        elif action == "power_menu":
            self.apps.home()
            bus.emit("power_menu")
        elif action == "standby":
            self.power("standby", source)
        elif action == "wake":
            self.wake(source)
        elif action == "power_key":
            self.power_key()
        elif action in ("volume_up", "volume_down"):
            system.audio_set(step=5 if action == "volume_up" else -5)
        elif action == "mute":
            system.audio_set(mute_toggle=True)
        elif action in ("livetv", "tvguide"):
            self.apps.launch(action)
        else:
            return False
        return True

    # ------------------------------------------------------------------ Installer (Live-System)
    def start_install(self, disk):
        disks = {d["path"]: d for d in system.install_disks()}
        if disk not in disks or disks[disk]["too_small"]:
            return {"ok": False, "message": "invalid_disk"}
        config.atomic_write_json(config.INSTALL_REQUEST, {"disk": disk, "ts": time.time()}, 0o644)
        if config.DEV:
            threading.Thread(target=_fake_install, daemon=True).start()
            return {"ok": True}
        return {"ok": system.systemctl_start("wtv-install.service")}

    @staticmethod
    def install_status():
        return config.read_json(config.INSTALL_STATUS, {"state": "idle", "progress": 0, "message": ""})


def _fake_install():
    for p, msg in ((5, "partition"), (15, "format"), (30, "copy"), (60, "copy"), (85, "bootloader"), (100, "done")):
        config.atomic_write_json(config.INSTALL_STATUS, {"state": "running" if p < 100 else "done",
                                                         "progress": p, "message": msg})
        time.sleep(1.2)

