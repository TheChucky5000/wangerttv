"""Zentrale Logik: Einstellungen anwenden, Einrichtung, Standby/Aufwachen, Sleep-Timer, Fernbedienungsaktionen."""
import os
import secrets
import threading
import time

from . import config, history, phoneinput, player, remote, share, system, tvnav, updates
from . import iptv as iptvmod
from .android import Android
from .apps import AppManager
from .iptv import ChannelStore
from .kodi import Kodi
from .media import Receivers, Surround
from . import radio as radiomod
from .radio import Radio
from .usb import UsbManager
from .sway import HOME_WS, sway
from .util import bus, log, run, which


TV_KEY_ACTIONS = ("channel_up", "channel_down", "channel_last", "channel_list", "tv_info", "playpause", "stop")


class Core:
    def __init__(self):
        config.ensure_dirs()
        self.settings = config.Settings()
        self.kodi = Kodi(self.settings)
        self.android = Android()
        self.apps = AppManager(self.settings, self.kodi, self.android)
        self.channels = ChannelStore(self.settings)
        self.receivers = Receivers(self.settings)
        self.usb = UsbManager()
        self.surround = Surround(self.settings)
        self.radio = Radio(self.settings, self._radio_nowplaying)
        self.surround.kodi_active = lambda: "kodi" in self.apps.running
        self.surround.pcm_layout = self.kodi.pcm_layout
        self.apps.before_launch = self._before_app
        # Nie zwei Musikquellen gleichzeitig: Spotify startet -> Radio aus; Radio startet -> Spotify pausieren
        self.receivers.on_play = lambda src: self.radio_stop()
        self.radio.before_play = self._pause_other_music
        self.channels.kodi = self.kodi
        self.channels.kodi_running = lambda: "kodi" in self.apps.running
        self.apps.on_running_change = self._kodi_running_changed
        self.apps.before_kodi = self._before_kodi
        self.standby = False
        self._resumed_at = 0
        self.sleep_timer = None      # {"ends": ts, "timer": Timer}
        self._power_lock = threading.Lock()
        self._radio_last = None
        self._kodi_state_ts = 0.0
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
        self._migrate()
        self._migrate_channel_keys()
        system.display_apply(self.settings.get("display") or {})
        # Oberfläche so früh wie möglich starten – die Startanimation läuft nahtlos weiter
        self.apps.start_launcher()
        system.set_power_profile(self.settings.get("box", "power_profile") or "balanced")
        system.audio_set_speakers(self.kodi.pcm_layout())
        if self.settings.get("audio", "sink"):
            system.audio_set(sink=self.settings.get("audio", "sink"))
        # Reste eines vorherigen Hintergrunddienstes (z. B. nach einem Update) beenden, bevor alles neu startet
        for name in ("uxplay", "librespot"):
            run(["pkill", "-u", str(os.getuid()), "-x", name], timeout=5)
        def audio_start():
            # HDMI-Kanäle/Durchreichen einrichten (kann WirePlumber neu starten), danach den Stereo-Upmix starten
            if "kodi" not in self.apps.running:
                self.kodi.setup_hdmi()
                # Ein WirePlumber-Neustart kann das Kartenprofil zurücksetzen – Stereo/Mehrkanal erneut setzen
                system.audio_set_speakers(self.kodi.pcm_layout())
            user_sink = self.settings.get("audio", "sink") or ""
            if user_sink and user_sink not in system.sink_names() and not user_sink.startswith("wangerttv"):
                # Gespeicherter Ausgang existiert nicht mehr (z. B. alter Name vor der Raumklang-Einrichtung)
                self.settings.patch({"audio": {"sink": ""}})
                user_sink = ""
            system.ensure_default_sink(user_sink)
            self.surround.apply()
            system.ensure_default_sink(self.settings.get("audio", "sink") or "")
        threading.Thread(target=audio_start, daemon=True).start()
        self.surround.supervise()
        threading.Thread(target=self._auto_standby_loop, daemon=True, name="auto-standby").start()
        threading.Thread(target=self._maintenance_loop, daemon=True, name="maintenance").start()
        self.usb.watch()
        self.apps.apply_window_rules()
        radiomod.start_refresh()          # Radiosender-Liste wöchentlich aktualisieren
        threading.Thread(target=self._spotify_app_loop, daemon=True, name="spotify-app").start()
        threading.Thread(target=self._reminder_loop, daemon=True, name="reminders").start()
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
                threading.Timer(4, lambda: remote.send("tv_on", timeout=8, source="boot")).start()

    def _on_focus(self, workspace):
        # Fernsehen läuft unsichtbar weiter, wenn man mit "Home" weggeht – Kodi dann anhalten (spart Leistung,
        # und Kodi verträgt es schlecht, Video zu dekodieren, ohne es anzeigen zu dürfen)
        prev, self._last_ws = getattr(self, "_last_ws", None), workspace
        if prev == "app-kodi" and workspace != "app-kodi" and self.apps.own_tv_ui() and "kodi" in self.apps.running \
                and not self.apps.kodi_focus_until:
            threading.Thread(target=self._stop_hidden_tv, daemon=True).start()
        # WangertTV Player: eigene Tastensteuerung (kein Mausmodus per langem OK)
        ctx = ("home" if workspace == HOME_WS else "kodi" if workspace == "app-kodi"
               else "android" if workspace == "app-android" else "default" if workspace == "app-player"
               else "web" if workspace.startswith("app-") else "default")
        remote.send("ctx", value=ctx, timeout=1)
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
            if "kodi" not in self.apps.running:
                system.audio_set_speakers(self.kodi.pcm_layout())
            if s.get("audio", "sink"):
                system.audio_set(sink=s.get("audio", "sink"))
            self.surround.apply()
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
        if "airplay" in changed or "spotify_connect" in changed or "spotify_mode" in changed:
            self.receivers.apply()
            if "spotify_mode" in changed and s.get("spotify_mode") == "app" and not config.DEV:
                if "com.spotify.Client" not in self.apps.flatpak_installed(refresh=True):
                    self.apps.install("spotify_desktop")
                    bus.emit("toast", key="spotify_app_installing")
        if "remote" in changed or "cec" in changed:
            remote.send("reload")
        if "apps" in changed:
            bus.emit("apps")
        if "box" in changed:
            system.set_power_profile(s.get("box", "power_profile") or "balanced")
        if "tv" in changed or "accent" in changed or "lang" in changed:
            bus.emit("channels")
            def kodi_tv():
                self.kodi_state()
                if "tv" in changed and self.kodi.ready():
                    self.kodi.apply_tv_ui()
            threading.Thread(target=kodi_tv, daemon=True).start()

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

    # ------------------------------------------------------------------ TV-Planer: Erinnerungen
    def reminder(self, action, item, auto=False, smart=False):
        """Erinnerung an eine Sendung setzen/entfernen. item: {"name" (Sender), "title", "start", "end"}."""
        try:
            key = (str(item.get("name", ""))[:120], int(item.get("start") or 0))
        except (TypeError, ValueError):
            return {"ok": False}
        rems = [r for r in (self.settings.get("reminders") or []) if isinstance(r, dict) and (r.get("name"), r.get("start")) != key
                and r.get("start", 0) > time.time() - 600]
        if action == "add" and key[0] and key[1] > time.time() - 300:
            rems.append({"name": key[0], "title": str(item.get("title", ""))[:160], "start": key[1],
                         "end": int(item.get("end") or key[1] + 3600), "auto": bool(auto), "smart": bool(smart)})
            rems.sort(key=lambda r: r["start"])
        self.settings.patch({"reminders": rems[:200]})
        bus.emit("reminders", reminders=rems)
        return {"ok": True, "reminders": rems}

    def _reminder_loop(self):
        last_smart = 0.0
        while True:
            time.sleep(15)
            try:
                now = time.time()
                if now - last_smart > 600:
                    last_smart = now
                    self._smart_reminders()
                rems = list(self.settings.get("reminders") or [])
                due = [r for r in rems if isinstance(r, dict) and r.get("start", 0) - 75 <= now]
                if not due:
                    continue
                self.settings.patch({"reminders": [r for r in rems if r not in due]})
                for r in due:
                    if now - r.get("start", 0) > 600:
                        continue                 # verpasst (Box war aus) – nicht mehr melden
                    self._fire_reminder(r)
            except Exception:  # noqa: BLE001
                log.exception("Erinnerungen")

    def _fire_reminder(self, r):
        log.info("Erinnerung: %s auf %s%s", r.get("title"), r.get("name"), " – schalte um" if r.get("auto") else "")
        if r.get("auto"):
            if self.standby:
                self.wake("auto")
                time.sleep(3)
            self.radio_stop()
            self.apps.launch("livetv", channel=r.get("name"))
            bus.emit("reminder", reminder=r, switched=True)
            return
        if self.standby:
            return
        bus.emit("reminder", reminder=r, switched=False)
        text = f"{'Gleich' if self.settings.get('lang') != 'en' else 'Starting soon'}: {r.get('title')} ({r.get('name')})"
        ws = sway.focused
        if ws == "app-kodi" and "kodi" in self.apps.running:
            self.kodi.rpc("GUI.ShowNotification", {"title": "WangertTV", "message": text, "displaytime": 12000}, timeout=2)
        elif ws == "app-player":
            player.ipc(["show-text", text, 8000])
        elif ws != HOME_WS:
            self.apps.home()             # Web-App o. Ä.: zur Startseite, dort erscheint die Erinnerung

    def radio_stop(self):
        if self.radio.state()["playing"]:
            self.radio.stop()

    def _radio_nowplaying(self, st):
        """Radio als "Läuft gerade" (Startseite, Bildschirmschoner)."""
        rec = self.receivers
        if st.get("playing") and st.get("station"):
            stn = st["station"]
            if stn.get("id") != self._radio_last and self.learning():
                self._radio_last = stn.get("id")
                history.record("radio", stn.get("name", ""), 3, meta={k: stn.get(k) for k in ("id", "name", "url", "favicon", "tags")})
            rec.nowplaying = {"source": "radio", "playing": True, "title": st.get("title") or stn.get("name", ""),
                              "artist": stn.get("name", "") if st.get("title") else (stn.get("tags") or ""),
                              "cover": st.get("cover") or stn.get("favicon") or "", "ts": time.time()}
        elif rec.nowplaying.get("source") == "radio":
            rec.nowplaying = {}
            self._radio_last = None
        bus.emit("nowplaying", nowplaying=rec.nowplaying)

    def _pause_other_music(self):
        self.receivers.pause_spotify()                     # Spotify Connect (librespot)
        if which("playerctl"):
            run(["playerctl", "-p", "spotify", "pause"], timeout=4)   # offizielle Spotify-App
        player.ipc(["set_property", "pause", True])        # WangertTV Player

    def _before_app(self, app):
        """Vor dem Start einer App/eines Videos: Radio aus (sonst laufen zwei Tonquellen gleichzeitig)."""
        if (app or {}).get("id") not in ("radio", "settings", "files", "share"):
            self.radio_stop()
        if app and app.get("type") not in ("kodi",) and self.learning():
            history.record("app", app.get("id", ""), 3)

    def learning(self):
        return (self.settings.get("personal") or {}).get("enabled", True) is not False

    def _spotify_app_loop(self):
        """Offizielle Spotify-App als Connect-Ziel: läuft unsichtbar im Hintergrund, wird bei Absturz neu gestartet;
        "Läuft gerade" kommt über MPRIS (playerctl)."""
        fails, last_start = 0, 0.0
        while True:
            # schnell nachsehen, wenn das Radio läuft (Spotify startet -> Radio aus), sonst seltener (spart CPU)
            time.sleep(2 if self.radio.state()["playing"] else 5)
            try:
                if config.DEV or not self.receivers.spotify_app_wanted() or self.standby:
                    continue
                if not self.apps.is_running("spotify_desktop"):
                    if time.time() - last_start > min(600, 20 * (fails + 1)):
                        if self.apps.start_background("spotify_desktop"):
                            log.info("Spotify-App als Connect-Ziel im Hintergrund gestartet")
                            fails = fails + 1 if time.time() - last_start < 120 else 0
                            last_start = time.time()
                    continue
                self._spotify_nowplaying()
            except Exception:  # noqa: BLE001
                log.exception("Spotify-App")

    def _spotify_nowplaying(self):
        if not which("playerctl"):
            return
        rc, out, _ = run(["playerctl", "-p", "spotify", "metadata", "--format",
                          "{{status}}\t{{title}}\t{{artist}}\t{{mpris:artUrl}}"], timeout=4)
        rec = self.receivers
        if rc != 0 or "\t" not in out:
            if rec.nowplaying.get("via") == "spotify_app":
                rec.nowplaying = {}
                bus.emit("nowplaying", nowplaying={})
            return
        status, title, artist, art = (out.strip().split("\t") + ["", "", "", ""])[:4]
        if status not in ("Playing", "Paused") or not title:
            return
        art = art.replace("https://open.spotify.com/image/", "https://i.scdn.co/image/")
        np = {"source": "spotify", "via": "spotify_app", "playing": status == "Playing", "title": title,
              "artist": artist, "cover": art if art.startswith("https://") else ""}
        if np["playing"] and self.radio.state()["playing"]:
            log.info("Spotify spielt – Radio aus")
            self.radio_stop()
        if rec.nowplaying.get("source") == "radio":
            return                          # Spotify pausiert, Radio läuft: Anzeige gehört dem Radio
        if {k: rec.nowplaying.get(k) for k in np} != np:
            rec.nowplaying = {**np, "ts": time.time()}
            bus.emit("nowplaying", nowplaying=rec.nowplaying)

    def _migrate(self):
        """Einmalige Umstellungen nach Updates."""
        done = list(self.settings.get("migrations") or [])
        if "player" not in done:
            # VLC (nicht mit der Fernbedienung bedienbar) wird durch den WangertTV Player ersetzt
            apps = [a for a in (self.settings.get("apps") or []) if a != "vlc"]
            if "player" not in apps:
                idx = apps.index("mediathek") + 1 if "mediathek" in apps else len(apps)
                apps.insert(idx, "player")
            self.settings.patch({"apps": apps, "migrations": done + ["player"]})
            done = list(self.settings.get("migrations") or [])
        if "radio" not in done:
            apps = list(self.settings.get("apps") or [])
            if "radio" not in apps:
                apps.insert(apps.index("player") + 1 if "player" in apps else len(apps), "radio")
            self.settings.patch({"apps": apps, "migrations": done + ["radio"]})
            done = list(self.settings.get("migrations") or [])
        if "tvplaner" not in done:
            apps = list(self.settings.get("apps") or [])
            if "tvplaner" not in apps:
                apps.insert(apps.index("livetv") + 1 if "livetv" in apps else 0, "tvplaner")
            self.settings.patch({"apps": apps, "migrations": done + ["tvplaner"]})

    def _migrate_channel_keys(self):
        """Programmtasten P+/P− der Fernbedienungen (z. B. waipu.tv) belegen – Kodi kennt diese Tastencodes nicht."""
        done = list(self.settings.get("migrations") or [])
        if "chkeys" in done:
            return
        maps = list((self.settings.get("remote") or {}).get("mappings") or [])
        have = {m.get("match") for m in maps if isinstance(m, dict)}
        for key, action, label in (("KEY_CHANNELUP", "channel_up", "P+"), ("KEY_CHANNELDOWN", "channel_down", "P−"),
                                   ("KEY_LAST", "channel_last", "Last")):
            if f"key:{key}" not in have:
                maps.append({"match": f"key:{key}", "action": action, "label": label})
        self.settings.patch({"remote": {"mappings": maps}, "migrations": done + ["chkeys"]})

    def _before_kodi(self):
        """Vor dem Kodi-Start: Stereo-Upmix vom HDMI-Ausgang nehmen. Sonst ist der Ausgang belegt, während Kodi
        die Tonausgänge abfragt – Kodi übersieht ihn dann und nimmt "Standard" (kein Durchreichen, nur Stereo)."""
        self.surround.kodi_starting = time.time()
        self.radio_stop()
        self.kodi_state()
        if self.surround.active():
            self.surround.apply()
            time.sleep(0.8)
        # HDMI-Profil (Stereo-PCM im Automatik-Modus) muss stehen, bevor Kodi die Ausgänge einliest
        if system.audio_set_speakers(self.kodi.pcm_layout()):
            time.sleep(1.5)

    def _kodi_running_changed(self):
        self.surround.kodi_starting = 0.0
        threading.Thread(target=self.surround.apply, daemon=True).start()
        if "kodi" in self.apps.running:
            # Sobald Kodi läuft, die waipu.tv-Sender für die Startseite übernehmen (Kodi braucht etwas zum Laden)
            threading.Thread(target=self._kodi_lists, daemon=True).start()
        else:
            # Kodi beendet: geänderte Ton-Einstellungen (Kanäle/Durchreichen) jetzt am HDMI-Ausgang übernehmen
            def after_kodi():
                self.kodi.setup_hdmi()
                system.audio_set_speakers(self.kodi.pcm_layout())
            threading.Thread(target=after_kodi, daemon=True).start()

    def _kodi_lists(self):
        if self.channels.waipu_configured():
            self.channels.refresh_pvr(wait=150)
        self.kodi_state()
        self.channels.refresh_epg(wait=150)

    def kodi_state(self):
        """Senderreihenfolge/-nummern, Logos und TV-Einstellungen für das WangertTV-Add-on in Kodi ablegen."""
        try:
            self.kodi.write_state(self.channels.channels())
            self._kodi_state_ts = time.time()
        except Exception:  # noqa: BLE001
            log.exception("Kodi-Add-on-Zustand")

    # ------------------------------------------------------------------ Live-TV (Kodi als unsichtbare Wiedergabe)
    def kodi_event(self, body):
        """Meldungen des WangertTV-Add-ons in Kodi."""
        ev = body.get("event")
        if ev == "backdrop":
            self.apps.kodi_focus()                # WangertTV-Hintergrund steht in Kodi – jetzt kann Kodi sichtbar werden
        elif ev == "av_started":
            self.apps.kodi_focus()
            if body.get("tv") and body.get("name"):
                self._tv_watching(str(body["name"]))
            if time.time() - self._kodi_state_ts > 600:
                threading.Thread(target=self.kodi_state, daemon=True).start()
        elif ev == "zap":
            self._tv_watching(str(body.get("name") or ""))
        elif ev == "leave":
            self.leave_tv(body.get("how") or "back")
        elif ev == "tv_stopped":
            # Stopp-Taste, Stream zu Ende oder abgebrochen: nicht in Kodis Menü stehen bleiben
            if self.apps.own_tv_ui() and sway.focused == "app-kodi" and not self.apps.kodi_focus_until:
                def back_if_idle():
                    time.sleep(0.4)
                    if sway.focused == "app-kodi" and not self.kodi.is_playing():
                        self.leave_tv("stopped")
                threading.Thread(target=back_if_idle, daemon=True).start()
        elif ev == "error":
            if self.apps.kodi_focus_until:
                self.apps.kodi_focus_until = 0.0
                bus.emit("toast", key="channel_failed", icon="alert")
        else:
            return {"ok": False}
        return {"ok": True}

    def _tv_watching(self, name):
        if not name:
            return
        prefs = self.settings.get("channel_prefs") or {}
        recent = [name] + [n for n in prefs.get("recent") or [] if n != name]
        if (prefs.get("recent") or [])[:1] != [name]:
            self.settings.patch({"channel_prefs": {"favorites": prefs.get("favorites") or [], "recent": recent[:10]}})
        if self.learning():
            history.record("channel", name, 2)
        bus.emit("watching", name=name)

    def _stop_hidden_tv(self):
        time.sleep(1.0)
        if sway.focused != "app-kodi" and not self.apps.kodi_focus_until and self.kodi.is_playing():
            res = self.kodi.rpc("Player.GetItem", {"playerid": 1}, timeout=2).get("result")
            if isinstance(res, dict) and (res.get("item") or {}).get("type") == "channel":
                log.info("Fernsehen im Hintergrund beendet")
                self.kodi.rpc("Player.Stop", {"playerid": 1}, timeout=2)

    def leave_tv(self, how="back"):
        """Aus dem laufenden Fernsehbild zurück zur WangertTV-Oberfläche; Kodi hört auf zu streamen."""
        back_to = (self.settings.get("tv") or {}).get("back_to") or "live"
        target = "epg" if how == "guide" else back_to if back_to in ("live", "epg", "home") else "live"
        self.apps.home()
        if target != "home":
            bus.emit("open", target=target, from_kodi=True)
        threading.Thread(target=self.kodi.rpc, args=("Player.Stop", {"playerid": 1}), daemon=True).start()
        return True

    def history_tick(self, body):
        """Sehverlauf vom Kodi-Add-on (jede Minute, solange Fernsehen läuft)."""
        if not self.learning() or self.standby:
            return {"ok": True}
        try:
            minutes = max(0.0, min(5.0, float(body.get("minutes") or 1)))
        except (TypeError, ValueError):
            minutes = 1.0
        history.record(str(body.get("kind") or ""), str(body.get("name") or ""), minutes,
                       title=str(body.get("title") or ""), episode=str(body.get("episode") or ""))
        return {"ok": True}

    # ------------------------------------------------------------------ Intelligente Startseite
    def foryou(self):
        p = self.settings.get("personal") or {}
        hour = time.localtime().tm_hour
        daypart = "morning" if 5 <= hour < 11 else "day" if hour < 17 else "evening" if hour < 22 else "night"
        out = {"enabled": p.get("enabled", True) is not False, "name": p.get("name") or "", "daypart": daypart,
               "morning": daypart == "morning" and p.get("morning", True) is not False, "picks": [], "shows": [], "today": []}
        if not out["enabled"]:
            return out
        chans = self.channels.channels()
        by_name = {c["name"].casefold(): c for c in chans}
        catalog = {a["id"]: a for a in self.apps.catalog}
        for sug in history.suggest(limit=24):
            if sug["kind"] == "channel" and sug["name"].casefold() in by_name:
                out["picks"].append({"kind": "channel", "name": by_name[sug["name"].casefold()]["name"]})
            elif sug["kind"] == "app" and sug["name"] in catalog:
                out["picks"].append({"kind": "app", "id": sug["name"]})
            elif sug["kind"] == "radio" and (sug.get("meta") or {}).get("url"):
                out["picks"].append({"kind": "radio", "station": sug["meta"]})
        out["picks"] = out["picks"][:14]
        out["shows"] = self._fav_airings(chans)
        out["today"] = self._today_for_you(chans, out["picks"], out["shows"])
        return out

    def _fav_airings(self, chans, hours=30):
        fav = {sh["title"].casefold(): sh for sh in history.favorite_shows(40)}
        if not fav:
            return []
        epg = iptvmod.epg_data()
        now, limit = time.time(), time.time() + hours * 3600
        out, per_show = [], {}
        for c in chans:
            for e in epg.get(c["name"].casefold().strip()) or []:
                if e[1] <= now or e[0] > limit:
                    continue
                sh = fav.get((e[2] or "").casefold())
                if not sh:
                    continue
                ep = e[4] if len(e) > 4 else ""
                out.append({"name": c["name"], "number": c.get("number"), "logo": c.get("logo", ""), "quality": c.get("quality", ""),
                            "title": e[2], "start": e[0], "end": e[1], "desc": e[3] if len(e) > 3 else "", "episode": ep,
                            "new": bool(ep) and not history.seen_episode(e[2], ep)})
        out.sort(key=lambda x: x["start"])
        res = []
        for it in out:
            k = it["title"].casefold()
            if per_show.get(k, 0) < 2:
                per_show[k] = per_show.get(k, 0) + 1
                res.append(it)
        return res[:20]

    def _today_for_you(self, chans, picks, shows):
        """"Heute für dich im TV": deine Sendungen von heute, dazu das Abendprogramm deiner Lieblingssender."""
        lt = time.localtime()
        end_of_day = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 23, 59, 59, 0, 0, -1))
        items = [s for s in shows if s["start"] < end_of_day]
        seen = {(i["name"], i["start"]) for i in items}
        prime = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 20, 15, 0, 0, 0, -1))
        at = prime if time.time() < prime + 3600 else time.time()
        favs = (self.settings.get("channel_prefs") or {}).get("favorites") or []
        top = [p["name"] for p in picks if p["kind"] == "channel"][:6] + [f for f in favs][:6]
        if top:
            sched = {i["name"]: i for i in self.channels.schedule(at)}
            for name in dict.fromkeys(top):
                it = sched.get(name)
                if it and (it["name"], it["start"]) not in seen:
                    seen.add((it["name"], it["start"]))
                    items.append(it)
        items.sort(key=lambda x: x["start"])
        return items[:16]

    def _smart_reminders(self):
        """Neue Folgen deiner Lieblingssendungen: automatisch erinnern (Einstellung "Automatisch erinnern")."""
        if not (self.settings.get("tv") or {}).get("smart_reminders") or not self.learning():
            return
        have = {(r.get("name"), r.get("start")) for r in self.settings.get("reminders") or [] if isinstance(r, dict)}
        soon = time.time() + 3 * 3600
        for it in self._fav_airings(self.channels.channels(), hours=4):
            if it["start"] > time.time() + 120 and it["start"] < soon and (it["new"] or not it["episode"]) \
                    and (it["name"], it["start"]) not in have:
                self.reminder("add", it, smart=True)

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
        player.ipc(["set_property", "pause", True])      # WangertTV Player (mpv) merkt sich die Stelle
        self.radio_stop()
        if which("playerctl"):
            run(["playerctl", "-a", "pause"], timeout=5)
        self.receivers.restart_all()   # beendet laufende AirPlay-/Spotify-Wiedergabe
        self.apps.home()
        if source not in ("cec", "auto") and self.settings.get("cec", "tv_off_on_standby"):
            remote.send("tv_off", timeout=8, source=source)
        time.sleep(0.5)
        system.display_power(False)
        system.start_wake_watch(os.path.join(config.LIB, "bin", "wtvctl") + " wake")
        self.cancel_sleep_timer()
        if (self.settings.get("sleep_action") == "suspend" and source == "timer") or \
                (self.settings.get("power_key") == "suspend" and source in ("key", "cec", "phone", "ir")):
            # Echter Ruhezustand: Mini-PC geht schlafen (Lüfter/LED aus). Aufwecken per Bluetooth-Fernbedienung
            # klappt nur, wenn das BIOS "Wake on USB/Bluetooth" erlaubt (siehe 90-wangerttv-wakeup.rules).
            time.sleep(2)
            if self.standby:
                log.info("Ruhezustand wird eingeleitet (%s)", source)
                if not system.power("suspend"):
                    bus.emit("toast", key="suspend_failed")

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
            threading.Thread(target=remote.send, args=("tv_on",), kwargs={"timeout": 8, "source": source},
                             daemon=True).start()
        bus.emit("standby", on=False)
        if source not in ("auto", "phone", "boot") and self.settings.get("box", "wake_to") == "tv":
            # "Beim Einschalten: Fernsehen" – direkt der zuletzt gesehene Sender
            threading.Timer(1.5, self._wake_to_tv).start()

    def _wake_to_tv(self):
        if self.standby:
            return
        res = self.apps.launch("livetv")
        if res.get("internal"):
            bus.emit("open", target=res["internal"])

    def resumed(self):
        """Nach dem Aufwachen aus dem Ruhezustand (systemd-sleep-Hook ruft »wtvctl resume«)."""
        self._resumed_at = time.time()
        # Diagnose der IR-Box (BOX-3) ins Protokoll holen: wie lief das Aufwecken?
        threading.Thread(target=remote.send, args=("ir_diag",), kwargs={"timeout": 4}, daemon=True).start()
        if self.standby:
            self.wake("resume")
        else:
            system.display_power(True)

    def power_key(self, source="key"):
        """Aus-Taste der Fernbedienung: Standby an/aus (oder Menü, je nach Einstellung)."""
        if not self.standby and time.time() - self._resumed_at < 6:
            return      # die Taste, die die Box gerade aufgeweckt hat, nicht gleich wieder als "Aus" werten
        if self.standby:
            self.wake(source)
        elif self.settings.get("power_key") == "menu" and source != "ir":
            self.apps.home()
            bus.emit("power_menu")
        else:
            threading.Thread(target=self.enter_standby, args=(source,), daemon=True).start()

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

    # ------------------------------------------------------------------ Automatischer Standby
    def touch(self):
        """Bedienung über Handy, Web-Oberfläche o. Ä. zählt als Aktivität."""
        self._last_activity = time.time()

    def _auto_standby_loop(self):
        """Wie beim Fernseher: nach X Stunden ohne Tastendruck in den Standby (5 Minuten vorher Hinweis)."""
        warned = False
        while True:
            time.sleep(60)
            hours = self.settings.get("auto_standby_hours") or 0
            if not hours or self.standby or not self.settings.get("setup_done"):
                warned = False
                continue
            res = remote.send("idle", timeout=2)
            idle = res.get("seconds") if res.get("ok") else None
            if idle is None:
                continue
            idle = min(idle, time.time() - getattr(self, "_last_activity", 0))
            limit = float(hours) * 3600
            if idle < limit - 300:
                warned = False
                continue
            if not warned:
                warned = True
                log.info("Automatischer Standby in 5 Minuten")
                bus.emit("toast", key="auto_standby_warn")
                if "kodi" in self.apps.running:
                    self.kodi.rpc("GUI.ShowNotification", {"title": "WangertTV",
                                  "message": "Standby in 5 Minuten – eine Taste drücken zum Abbrechen",
                                  "displaytime": 20000}, timeout=3)
                continue
            if idle >= limit:
                log.info("Automatischer Standby nach %.1f Stunden ohne Bedienung", idle / 3600)
                warned = False
                self.enter_standby("timer")

    def _maintenance_loop(self):
        """Nächtlicher Wartungsneustart: zwischen 4 und 5 Uhr, nur wenn niemand die Box benutzt.

        Hält Speicher, Browser und Kodi über Wochen frisch (wie es viele TV-Boxen nachts machen).
        Nur nach mindestens 12 Stunden Laufzeit – so gibt es nie zwei Neustarts in einer Nacht."""
        while True:
            time.sleep(300)
            history.save()
            try:
                if not self.settings.get("maintenance_reboot") or not self.settings.get("setup_done"):
                    continue
                if time.localtime().tm_hour != 4:
                    continue
                with open("/proc/uptime", encoding="utf-8") as fh:
                    if float(fh.read().split()[0]) < 12 * 3600:
                        continue
                if not self.standby:
                    res = remote.send("idle", timeout=2)
                    idle = res.get("seconds") if res.get("ok") else None
                    if idle is None or min(idle, time.time() - getattr(self, "_last_activity", 0)) < 2 * 3600:
                        continue
                if "kodi" in self.apps.running and self.kodi.is_playing():
                    continue
                if self.receivers.nowplaying and (self.receivers.nowplaying or {}).get("playing"):
                    continue
                log.info("Nächtlicher Wartungsneustart")
                system.power("reboot")
            except Exception:  # noqa: BLE001
                log.exception("Wartungsneustart")

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
    def pause_all(self):
        """Alle Wiedergaben anhalten (z. B. Fernseher hat auf einen anderen HDMI-Eingang umgeschaltet)."""
        if self.standby:
            return
        log.info("Wiedergabe angehalten (anderer Eingang am Fernseher)")
        if "kodi" in self.apps.running:
            self.kodi.rpc("Player.PlayPause", {"playerid": 1, "play": False}, timeout=2)
        player.ipc(["set_property", "pause", True])
        if which("playerctl"):
            run(["playerctl", "-a", "pause"], timeout=5)

    def volume(self, action):
        """Lauter/Leiser/Stumm: am AV-Receiver per HDMI-CEC (wirkt auch bei Dolby/DTS) oder an der Box."""
        mode = self.settings.get("cec", "volume") or "auto"
        cec_cmd = {"volume_up": "volup", "volume_down": "voldown", "mute": "mute"}[action]
        if mode != "box" and self.settings.get("cec", "enabled") is not False:
            st = (remote.status().get("cec") or {})
            if st.get("adapter") and (st.get("avr") or mode == "avr"):
                if remote.send(cec_cmd, timeout=2).get("ok"):
                    return True                    # Anzeige kommt mit der Rückmeldung des Receivers
        if action == "mute":
            system.audio_set(mute_toggle=True)
        else:
            system.audio_set(step=5 if action == "volume_up" else -5)
        a = system.audio_status()
        self.volume_osd(a.get("volume"), a.get("muted"), "box")
        return True

    def volume_osd(self, level, mute, source="box"):
        """Lautstärke kurz anzeigen – auf der Startseite, in Kodi und im WangertTV Player."""
        bus.emit("volume", level=level, mute=bool(mute), source=source)
        de = self.settings.get("lang") != "en"
        name = "Receiver" if source == "avr" else ""
        if mute:
            text = ("Stumm" if de else "Muted")
        else:
            text = f"{'Lautstärke' if de else 'Volume'} {level if level is not None else '–'}"
        text = f"{name}: {text}" if name else text
        ws = sway.focused
        if ws == "app-kodi" and "kodi" in self.apps.running:
            self.kodi.rpc("GUI.ShowNotification", {"title": "WangertTV", "message": text, "displaytime": 1500}, timeout=1)
        elif ws == "app-player":
            player.ipc(["show-text", text, 1500])

    def remote_action(self, action, source="remote"):
        """Aktion von einer zugewiesenen Fernbedienungstaste oder vom CEC-Dienst."""
        if action == "pause_all":
            self.pause_all()
            self.radio_stop()           # nie aufwecken: Fernseher zeigt gerade etwas anderes
            return True
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
            self.power_key(source if source in ("ir", "phone", "web") else "key")
        elif action in ("volume_up", "volume_down", "mute"):
            self.volume(action)
        elif action in ("livetv", "tvguide"):
            res = self.apps.launch(action)
            if res.get("internal"):
                self.apps.home()
                bus.emit("open", target=res["internal"])
        elif action in TV_KEY_ACTIONS:
            return self.tv_key(action)
        else:
            return False
        return True

    # ------------------------------------------------------------------ Programmtasten (P+/P−, Info, Stopp …)
    def _kodi_tv_front(self):
        return sway.focused == "app-kodi" and "kodi" in self.apps.running

    def _kodi_notify(self, message):
        return "result" in self.kodi.rpc("JSONRPC.NotifyAll", {"sender": "service.wangerttv", "message": message}, timeout=2)

    def _channel_step(self, step):
        """Sender vor/zurück ab dem zuletzt gesehenen (in WangertTV-Reihenfolge)."""
        chans = [c["name"] for c in self.channels.channels()]
        if not chans:
            return None
        recent = (self.settings.get("channel_prefs") or {}).get("recent") or []
        idx = chans.index(recent[0]) if recent and recent[0] in chans else -1
        return chans[(idx + step) % len(chans)] if idx >= 0 else chans[0]

    def tv_key(self, action):
        """Tasten zum Fernsehen – passen sich an, je nachdem, was gerade zu sehen ist."""
        kodi_front = self._kodi_tv_front()
        if action in ("channel_up", "channel_down"):
            step = 1 if action == "channel_up" else -1
            if kodi_front and self.apps.own_tv_ui():
                return self._kodi_notify("wtv_up" if step > 0 else "wtv_down")
            if kodi_front:
                self.kodi.rpc("Input.ExecuteAction", {"action": "channelup" if step > 0 else "channeldown"}, timeout=2)
                return True
            if sway.focused == HOME_WS:
                bus.emit("channel_key", step=step)          # Oberfläche: im TV-Programm blättern, sonst umschalten
                return True
            name = self._channel_step(step)
            return bool(name) and bool(self.apps.launch("livetv", channel=name).get("ok"))
        if action == "channel_last":
            recent = (self.settings.get("channel_prefs") or {}).get("recent") or []
            if len(recent) < 2:
                return False
            return bool(self.apps.launch("livetv", channel=recent[1]).get("ok"))
        if action == "channel_list":
            self.apps.home()
            bus.emit("open", target="live")
            return True
        if action == "tv_info":
            if kodi_front:
                return self._kodi_notify("wtv_info") if self.apps.own_tv_ui() else \
                    "result" in self.kodi.rpc("Input.ExecuteAction", {"action": "info"}, timeout=2)
            if sway.focused == HOME_WS:
                bus.emit("open", target="live")
            return True
        if action == "playpause":
            if kodi_front:
                self.kodi.rpc("Player.PlayPause", {"playerid": 1}, timeout=2)
            elif sway.focused == "app-player":
                player.ipc(["cycle", "pause"])
            elif self.radio.state()["playing"]:
                self.radio_stop()
            elif which("playerctl"):
                run(["playerctl", "play-pause"], timeout=4)
            return True
        if action == "stop":
            if kodi_front and self.kodi.is_playing():
                if self.apps.own_tv_ui():
                    return self.leave_tv("back")
                self.kodi.rpc("Player.Stop", {"playerid": 1}, timeout=2)
            elif sway.focused == "app-player":
                player.ipc(["quit"])
            elif self.radio.state()["playing"]:
                self.radio_stop()
            elif which("playerctl"):
                run(["playerctl", "stop"], timeout=4)
            return True
        return False

    # ------------------------------------------------------------------ Handy als Fernbedienung
    PHONE_KEYS = {"up": "KEY_UP", "down": "KEY_DOWN", "left": "KEY_LEFT", "right": "KEY_RIGHT", "ok": "KEY_ENTER",
                  "back": "BACK", "playpause": "KEY_PLAYPAUSE"}

    def phone_remote_info(self, renew=False):
        token = self.settings.get("phone_remote", "token")
        if renew or not token:
            token = secrets.token_urlsafe(12)
            self.settings.patch({"phone_remote": {"token": token}})
        url = phoneinput.remote_url(token)
        return {"url": url, "qr": phoneinput.qr_svg(url)}

    def share_info(self, renew=False):
        url = phoneinput.share_url(share.token(self.settings, renew))
        return {"url": url, "qr": phoneinput.qr_svg(url), "recent": share.recent(), "autoshow": self.settings.get("share", "autoshow") is not False}

    def share_action(self, kind, body):
        """Vom Handy: "done" (Dateien fertig hochgeladen) oder "link"."""
        self.touch()
        if kind == "link":
            url = str(body.get("url") or "").strip()
            if not url.startswith(("https://", "http://")) or len(url) > 2000:
                return False
            if self.standby:
                self.wake("phone")
            yid = share.youtube_id(url)
            log.info("Vom Handy: Link %s", "YouTube " + yid if yid else url[:80])
            if yid:
                res = self.apps.launch("youtube", url=f"https://www.youtube.com/tv#/watch?v={yid}")
            else:
                res = self.apps.launch("browser", url=url)
            return bool(res.get("ok"))
        if kind == "done":
            names = [share.clean_name(n) for n in (body.get("files") or []) if isinstance(n, str)][:500]
            bus.emit("share_done", count=len(names))
            if not names or self.standby or self.settings.get("share", "autoshow") is False:
                return True
            first = os.path.join(share.FOLDER, time.strftime("%Y-%m-%d"), names[0])
            if os.path.exists(first) and player.kind(first) in ("video", "audio", "image"):
                self.apps.play_media(first, start_over=True)
            return True
        return False

    def phone_remote(self, kind, value):
        self.touch()
        if kind == "pointer":
            if self.standby:
                self.wake("phone")
                return True
            dx, dy, scroll, click = value
            return bool(remote.send("pointer", dx=dx, dy=dy, scroll=scroll, click=click, timeout=2).get("ok"))
        if kind == "text":
            return self.send_text(value)
        if value == "power":
            self.power_key("phone")
            return True
        if self.standby:
            self.wake("phone")
            return True
        if value == "home":
            self.apps.home()
        elif value == "settings":
            self.remote_action("settings", "phone")
        elif value in ("volup", "voldown", "mute"):
            self.volume({"volup": "volume_up", "voldown": "volume_down", "mute": "mute"}[value])
        elif value == "mouse":
            remote.send("mouse", value="toggle", timeout=2)
        elif value in self.PHONE_KEYS:
            return bool(remote.send("key", key=self.PHONE_KEYS[value], timeout=2).get("ok"))
        else:
            return False
        return True

    def send_text(self, text):
        """Text in die gerade sichtbare App schreiben: Web-App (DevTools), Kodi-Tastatur oder WangertTV-Eingabe."""
        ws = sway.focused or ""
        key = ws[4:] if ws.startswith("app-") else None
        entry = self.apps.running.get(key) if key else None
        if key == "android":
            # Android (z. B. Google-Anmeldung im Play Store): Text direkt in das ausgewählte Feld
            return bool(remote.send("android_text", timeout=16, text=text).get("ok"))
        if key == "kodi" and self.kodi.ready():
            res = self.kodi.rpc("Input.SendText", {"text": text, "done": False}, timeout=3)
            return "error" not in res
        if entry:
            app = self.apps.get(entry["app"])
            port = self.apps.tvnav_port(app) if app and app.get("type") in ("web", "browser", "flatpak") else None
            if port and tvnav.insert_text(port, text):
                return True
        bus.emit("phone_text", token="remote", text=text)
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

