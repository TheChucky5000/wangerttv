"""Empfangsdienste: AirPlay (UxPlay) und Spotify Connect (librespot) – laufen in der TV-Sitzung."""
import os
import threading
import time

from . import config, system
from .util import bus, log, run, spawn, which

LIBRESPOT_EVENT = os.path.join(config.LIB, "bin", "wtv-librespot-event")


class Receivers:
    def __init__(self, settings):
        self.settings = settings
        self.procs = {}
        self.nowplaying = {}
        self._lock = threading.Lock()
        self._restarts = {}
        self._started = {}
        self._name_suffix = 0       # AirPlay-Name schon vergeben (NameConflict) -> "WangertTV Box (2)" usw.
        self._install_requested = False
        self.on_play = None          # Core: Spotify Connect spielt -> Radio aus

    def _cmd(self, name):
        dev = (self.settings.get("device_name") or "WangertTV Box").strip()[:40]
        if name == "airplay":
            if not which("uxplay"):
                return None
            if self._name_suffix:
                dev = f"{dev} ({self._name_suffix + 1})"
            return ["uxplay", "-n", dev, "-nh", "-fs", "-nohold"]
        if name == "spotify":
            if not which("librespot"):
                return None
            cache = os.path.join(config.CACHE_DIR, "librespot")
            return ["librespot", "--name", dev, "--device-type", "tv", "--bitrate", "320",
                    "--backend", "pulseaudio", "--initial-volume", "70", "--enable-volume-normalisation",
                    "--cache", cache, "--disable-audio-cache", "--onevent", LIBRESPOT_EVENT]
        return None

    def wanted(self, name):
        key = {"airplay": "airplay", "spotify": "spotify_connect"}[name]
        if name == "spotify" and self.settings.get("spotify_mode") == "app":
            return False        # Connect läuft über die offizielle Spotify-App (Core.spotify_app_loop)
        return bool(self.settings.get(key)) and bool(self.settings.get("setup_done"))

    def spotify_app_wanted(self):
        return (self.settings.get("spotify_mode") == "app" and bool(self.settings.get("spotify_connect"))
                and bool(self.settings.get("setup_done")))

    def start(self, name):
        if config.DEV:
            return
        cmd = self._cmd(name)
        if not cmd:
            return
        with self._lock:
            proc = self.procs.get(name)
            if proc and proc.poll() is None:
                return
            env = dict(os.environ, WTV_PORT=str(config.PORT))
            self.procs[name] = spawn(cmd, logfile=os.path.join(config.LOG_DIR, f"{name}.log"), env=env)
            self._started[name] = time.time()
            log.info("%s gestartet", name)

    def stop(self, name):
        with self._lock:
            proc = self.procs.pop(name, None)
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                proc.kill()

    def restart_all(self):
        for name in ("airplay", "spotify"):
            self.stop(name)
        self.apply()

    def apply(self):
        self.ensure_installed()
        for name in ("airplay", "spotify"):
            if self.wanted(name):
                self.start(name)
            else:
                self.stop(name)

    def ensure_installed(self):
        """Spotify Connect eingeschaltet, aber librespot fehlt (nicht in Debian) -> einmal nachinstallieren lassen."""
        if config.DEV or not self.wanted("spotify") or which("librespot") or self._install_requested:
            return False
        self._install_requested = True
        log.info("Spotify Connect: librespot fehlt – wird nachinstalliert (wtv-extras)")
        if system.systemctl_start("wtv-extras.service"):
            bus.emit("toast", key="spotify_installing")
        return True

    def supervise(self):
        def loop():
            while True:
                time.sleep(5)
                for name in ("airplay", "spotify"):
                    if self.wanted(name) and name not in self.procs and self._cmd(name):
                        log.info("%s ist jetzt verfügbar – starte", name)
                        self.start(name)
                        continue
                    proc = self.procs.get(name)
                    if proc is not None and proc.poll() is None and time.time() - self._started.get(name, 0) > 600:
                        self._restarts[name] = 0          # läuft seit 10 min stabil: Zähler zurücksetzen
                    if self.wanted(name) and proc is not None and proc.poll() is not None:
                        if name == "airplay" and self._name_conflict() and self._name_suffix < 5:
                            # Name im Netz schon vergeben (z. B. alte Anmeldung hängt noch) -> anderer Name
                            self._name_suffix += 1
                            log.warning("AirPlay-Name ist im Netz schon vergeben – verwende %s", self._cmd("airplay")[2])
                        count = self._restarts.get(name, 0) + 1
                        self._restarts[name] = count
                        if count <= 20:
                            log.warning("%s abgestürzt – Neustart %d", name, count)
                            time.sleep(min(60, 2 * count))
                            self.procs.pop(name, None)
                            self.start(name)
        threading.Thread(target=loop, daemon=True, name="receivers").start()

    @staticmethod
    def _name_conflict():
        try:
            with open(os.path.join(config.LOG_DIR, "airplay.log"), "rb") as fh:
                fh.seek(0, os.SEEK_END)
                fh.seek(max(0, fh.tell() - 1500))
                tail = fh.read().decode("utf-8", "replace")
        except OSError:
            return False
        last = tail.rsplit("UxPlay ", 1)[-1]          # nur der letzte Start zählt
        return "NameConflict" in last

    def librespot_event(self, data):
        event = data.get("PLAYER_EVENT", "")
        if event in ("playing", "started", "changed", "track_changed", "paused", "loading"):
            name = data.get("NAME", "")
            artists = data.get("ARTISTS", "").replace("\n", ", ")
            covers = [c for c in (data.get("COVERS", "") or "").split("\n") if c.startswith("https://")]
            self.nowplaying = {
                "source": "spotify", "playing": event != "paused",
                "title": name or self.nowplaying.get("title", ""),
                "artist": artists or self.nowplaying.get("artist", ""),
                "cover": covers[0] if covers else self.nowplaying.get("cover", ""), "ts": time.time(),
            }
        elif event in ("stopped", "session_disconnected", "unavailable"):
            self.nowplaying = {}
        if event in ("playing", "started") and self.on_play:
            try:
                self.on_play("spotify")
            except Exception:  # noqa: BLE001
                log.exception("on_play")
        bus.emit("nowplaying", nowplaying=self.nowplaying)

    def pause_spotify(self):
        """Spotify Connect (librespot) anhalten, z. B. wenn das Radio startet. librespot hat keinen Pause-Befehl –
        ein Neustart beendet die Wiedergabe; die Box bleibt im Handy als Gerät verfügbar."""
        if self.nowplaying.get("source") == "spotify" and self.nowplaying.get("via") != "spotify_app" \
                and self.nowplaying.get("playing") and "spotify" in self.procs:
            log.info("Spotify Connect angehalten (Radio startet)")
            self.stop("spotify")
            self.nowplaying = {}
            bus.emit("nowplaying", nowplaying={})
            threading.Timer(1.5, self.start, args=("spotify",)).start()


class Surround:
    """Stereo-Ton (YouTube, Netflix, Spotify …) auf alle Lautsprecher verteilen.

    Browser liefern unter Linux nur Stereo. Chrome würde Stereo an einen 5.1-Ausgang unverändert vorne ausgeben –
    hinten bliebe es still und der Receiver schaltet nicht auf Surround. Darum gibt es einen virtuellen
    Stereo-Ausgang "WangertTV Surround": PipeWire rechnet ihn (Upmix, Verfahren PSD) auf 5.1/7.1 hoch und gibt ihn
    als Mehrkanal-PCM am HDMI aus. Kodi spielt weiter direkt auf HDMI (echtes 5.1/7.1 und Dolby/DTS-Durchreichen).
    """
    NAME = "wangerttv_surround"
    POSITIONS = {"5.1": "[FL FR FC LFE RL RR]", "7.1": "[FL FR FC LFE RL RR SL SR]"}

    def __init__(self, settings):
        self.settings = settings
        self.proc = None
        self.key = None             # (Ziel, Layout) der laufenden Instanz
        self._lock = threading.Lock()

    def layout(self):
        # Kanäle des HDMI-Ausgangs (im Automatik-Modus Stereo, wenn 5.1 als Dolby Digital kommt – dann kein Upmix)
        return self.pcm_layout()

    kodi_active = staticmethod(lambda: False)   # wird von Core gesetzt
    pcm_layout = staticmethod(lambda: "2.0")    # wird von Core gesetzt (Kodi.pcm_layout)
    kodi_starting = 0.0          # Zeitpunkt des Kodi-Starts (Upmix bleibt dann ~60 s aus)

    def wanted(self):
        # Während Kodi läuft, ist der HDMI-Ausgang für Kodi reserviert: Dolby/DTS-Durchreichen braucht den Ausgang
        # für sich allein – ein zweiter Datenstrom (der Upmix) würde den Ton stören oder abreißen lassen.
        if time.time() - self.kodi_starting < 60 or self.kodi_active():
            return False
        sink = self.settings.get("audio", "sink") or ""
        user_sink_ok = not sink or "hdmi" in sink.lower() or sink == self.NAME
        upmix = self.settings.get("audio", "upmix")
        return self.layout() in self.POSITIONS and (upmix is None or bool(upmix)) and user_sink_ok

    def active(self):
        return bool(self.proc and self.proc.poll() is None)

    def apply(self):
        with self._lock:
            self._apply()

    def _apply(self):
        if config.DEV:
            return
        if not which("pw-loopback"):
            log.warning("pw-loopback fehlt – kein Surround-Upmix")
            return
        if not self.wanted():
            self._stop()
            return
        target = system.hdmi_sink()
        if not target:
            self._stop()
            return
        key = (target, self.layout())
        if self.active() and self.key == key:
            system.set_default_sink(self.NAME)
            return
        self._stop(restore=False)
        run(["pkill", "-u", str(os.getuid()), "-f", "pw-loopback.*wangerttv-surround"], timeout=5)
        playback = (f"node.name={self.NAME}.output target.object={target} audio.position={self.POSITIONS[key[1]]} "
                    "node.passive=true stream.dont-remix=false channelmix.upmix=true channelmix.upmix-method=psd "
                    "channelmix.lfe-cutoff=150 channelmix.fc-cutoff=12000 channelmix.rear-delay=12.0")
        capture = (f"media.class=Audio/Sink node.name={self.NAME} node.description=\"WangertTV Surround\" "
                   "audio.position=[FL FR]")
        self.proc = spawn(["pw-loopback", "-n", "wangerttv-surround", "--capture-props", capture,
                           "--playback-props", playback], logfile=os.path.join(config.LOG_DIR, "surround.log"))
        self.key = key
        for _ in range(25):
            time.sleep(0.2)
            if self.NAME in run(["pactl", "list", "short", "sinks"], timeout=5)[1]:
                break
        system.set_default_sink(self.NAME)
        log.info("Surround-Upmix aktiv: Stereo -> %s auf %s", key[1], target)

    def _stop(self, restore=True):
        proc, self.proc, self.key = self.proc, None, None
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except Exception:  # noqa: BLE001
                proc.kill()
        if restore and not config.DEV:
            sink = self.settings.get("audio", "sink") or ""
            if not sink or sink == self.NAME:
                system.set_default_sink(system.hdmi_sink())

    def supervise(self):
        def loop():
            n = 0
            while True:
                time.sleep(4)
                n += 1
                try:
                    if config.DEV:
                        continue
                    if n % 8 == 0 and not self.kodi_active() and time.time() - self.kodi_starting > 60:
                        # alle ~30 s: HDMI-Profil (Stereo/Mehrkanal) prüfen – WirePlumber setzt es nach Neustarts
                        # oder Ein-/Ausschalten des Receivers gern auf 7.1 zurück –, dann den Standard-Ausgang:
                        # muss HDMI (oder der Upmix) sein, sonst kein Ton bei Spotify & Co.
                        if system.audio_set_speakers(self.layout()):
                            log.info("HDMI-Ausgang wieder auf %s gestellt", self.layout())
                            self.apply()
                        system.ensure_default_sink(self.settings.get("audio", "sink") or "")
                    want, act = self.wanted(), self.active()
                    if want and not act:
                        log.warning("Surround-Upmix lief nicht – starte neu")
                        self.apply()
                    elif act and not want:
                        # z. B. Kodi läuft: HDMI-Ausgang freigeben, sonst stört der Upmix das Dolby/DTS-Durchreichen
                        log.info("Surround-Upmix angehalten (HDMI wird gerade anders gebraucht)")
                        self.apply()
                except Exception:  # noqa: BLE001
                    log.exception("Surround-Überwachung")
        threading.Thread(target=loop, daemon=True, name="surround").start()
