# WangertTV-Dienst in Kodi: eigene Info-Leiste mit Bild-/Tonformat, Umschalten in WangertTV-Reihenfolge
# (mit Vorschau, damit schnelles Durchschalten nicht jeden Stream anlaufen lässt), Zifferneingabe mit
# WangertTV-Nummern, Rückkehr zur WangertTV-Oberfläche und Sehverlauf für die intelligente Startseite.
import json
import os
import sys
import threading
import time

import xbmc
import xbmcaddon
import xbmcgui

sys.path.insert(0, os.path.join(xbmcaddon.Addon("service.wangerttv").getAddonInfo("path"), "resources", "lib"))
import wtvkodi as W  # noqa: E402

A_LEFT, A_RIGHT, A_UP, A_DOWN, A_PGUP, A_PGDN, A_SELECT = 1, 2, 3, 4, 5, 6, 7
A_PREVMENU, A_INFO, A_BACK, A_CONTEXT, A_CH_UP, A_CH_DOWN = 10, 11, 92, 117, 184, 185
A_NUMBERS = {58 + i: str(i) for i in range(10)}          # REMOTE_0 … REMOTE_9
A_MOUSE = set(range(100, 110))                            # Mausaktionen ignorieren
SENDER = "service.wangerttv"


def bg(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True).start()


class Bar(xbmcgui.WindowXMLDialog):
    svc = None

    def onInit(self):
        if self.svc:
            self.svc.refresh(force=True)

    def onAction(self, action):
        if self.svc:
            self.svc.bar_action(action.getId())


class Backdrop(xbmcgui.WindowXML):
    """WangertTV-Hintergrund hinter dem Fernsehbild – statt Kodis Startseite (z. B. kurz beim Umschalten)."""
    svc = None

    def onAction(self, action):
        if self.svc:
            self.svc.backdrop_action(action.getId())


class Player(xbmc.Player):
    svc = None

    def onPlayBackStarted(self):
        # Wiedergabe wird geöffnet (Bild kommt erst mit onAVStarted) – solange nicht erneut umschalten
        if self.svc and time.time() >= self.svc.opening_until:
            self.svc.opening_until = time.time() + 15

    def onAVStarted(self):
        if self.svc:
            self.svc.av_started()

    def onPlayBackStopped(self):
        if self.svc:
            self.svc.stopped()

    def onPlayBackEnded(self):
        if self.svc:
            self.svc.stopped()

    def onPlayBackError(self):
        if self.svc:
            self.svc.stopped(error=True)


class Service(xbmc.Monitor):
    def __init__(self):
        super().__init__()
        self.player = Player()
        self.player.svc = self
        self.bar = None
        self.bar_until = 0.0
        self.expanded = False
        self.preview = None            # Index in self.chans, solange nur ausgewählt (noch nicht umgeschaltet)
        self.preview_at = 0.0
        self.digits = ""
        self.digits_at = 0.0
        self.chans = []
        self.chans_ts = 0.0
        self.info_cache = {}
        self.last_refresh = 0.0
        self.guard_until = 0.0         # nach eigenem Umschalten: Stopp-Meldungen von Kodi ignorieren
        self.tv_active = False
        self.watch_ts = time.time()
        self.stop_check = 0.0
        self.opening_until = 0.0       # Sender startet gerade – bis dahin nicht erneut umschalten (Kodi verträgt das schlecht)
        self.target = None             # Index des Senders, der gerade startet
        self.backdrop = None
        self.started = time.time()

    # ------------------------------------------------------------------ Hauptschleife
    def run(self):
        xbmc.log("[WangertTV] Dienst gestartet", xbmc.LOGINFO)
        while not self.abortRequested():
            if self.waitForAbort(0.15):
                break
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 – Dienst darf nie aussteigen
                xbmc.log(f"[WangertTV] Fehler: {exc}", xbmc.LOGWARNING)
        self.close_bar()

    def tick(self):
        now = time.time()
        if self.backdrop is None and now - self.started > 1.5 and W.state().get("own_ui", True) \
                and not xbmc.getCondVisibility("Player.HasVideo"):
            self.show_backdrop()
        if self.preview is not None and now >= self.preview_at and now >= self.opening_until:
            self.commit()
        if self.digits and now >= self.digits_at:
            self.dial()
        if self.bar:
            if now >= self.bar_until and self.preview is None and not self.digits and not self.expanded:
                self.close_bar()
            elif now - self.last_refresh >= 1.0:
                self.refresh()
        if self.stop_check and now >= self.stop_check:
            self.stop_check = 0.0
            if not self.player.isPlaying() and self.tv_active and now >= self.guard_until:
                self.tv_active = False
                bg(W.api, "/api/kodi/event", {"event": "tv_stopped"})
                if W.state().get("own_ui", True):
                    self.show_backdrop()                                # nie in Kodis TV-Menüs stehen bleiben
        if now - self.watch_ts >= 60:
            self.watch_ts = now
            if xbmc.getCondVisibility("Pvr.IsPlayingTv") and not xbmc.getCondVisibility("Player.Paused"):
                bg(W.api, "/api/history", {"kind": "channel", "name": xbmc.getInfoLabel("VideoPlayer.ChannelName"),
                                           "title": xbmc.getInfoLabel("VideoPlayer.Title"),
                                           "episode": xbmc.getInfoLabel("VideoPlayer.EpisodeName"), "minutes": 1})

    # ------------------------------------------------------------------ Befehle aus der Tastenbelegung
    def onNotification(self, sender, method, data):
        if sender != SENDER:
            return
        cmd = method.split(".", 1)[-1]
        if cmd == "wtv_up":
            self.zap(1)
        elif cmd == "wtv_down":
            self.zap(-1)
        elif cmd == "wtv_info":
            if self.bar:
                self.toggle_expanded()
            else:
                self.show_bar()
        elif cmd == "wtv_back":
            if self.bar:
                self.close_bar()
            else:
                self.leave("back")
        elif cmd == "wtv_guide":
            self.leave("guide")
        elif cmd.startswith("wtv_num") and cmd[7:].isdigit():
            self.digit(cmd[7:])
        elif cmd == "wtv_prepare":
            # WangertTV schaltet gleich einen Sender ein: Hintergrund statt Kodi-Menü dahinter
            try:
                name = json.loads(data) if data else ""
            except ValueError:
                name = ""
            self.show_backdrop(force=True, busy=name if isinstance(name, str) and name else W.t("loading"))

    def backdrop_action(self, aid):
        if aid in (A_BACK, A_PREVMENU):
            self.leave("back")
        elif aid in (A_UP, A_PGUP, A_CH_UP, A_DOWN, A_PGDN, A_CH_DOWN) and self.tv_active:
            self.zap(1 if aid in (A_UP, A_PGUP, A_CH_UP) else -1)

    def show_backdrop(self, force=False, busy=""):
        if not W.state().get("own_ui", True):
            return
        try:
            if self.backdrop is None:
                self.backdrop = Backdrop("script-wangerttv-backdrop.xml", W.ADDON_PATH, "Default", "1080i")
                self.backdrop.svc = self
            self.backdrop.setProperty("busy", busy)
            active = xbmc.getCondVisibility("Window.IsActive(script-wangerttv-backdrop.xml)")
            video = xbmc.getCondVisibility("Window.IsActive(fullscreenvideo) | Window.IsActive(fullscreenlivetv)")
            if not active and (force or not video):
                self.backdrop.show()
            if force or not active:
                # WangertTV kann jetzt zu Kodi wechseln: zu sehen ist der WangertTV-Hintergrund, nicht Kodi
                bg(W.api, "/api/kodi/event", {"event": "backdrop"})
        except RuntimeError as exc:
            xbmc.log(f"[WangertTV] Hintergrund: {exc}", xbmc.LOGWARNING)

    def bar_action(self, aid):
        if aid in (A_UP, A_PGUP, A_CH_UP):
            self.zap(1)
        elif aid in (A_DOWN, A_PGDN, A_CH_DOWN):
            self.zap(-1)
        elif aid == A_SELECT:
            if self.preview is not None:
                self.commit()
            elif self.digits:
                self.dial()
            else:
                self.toggle_expanded()
        elif aid == A_INFO:
            self.toggle_expanded()
        elif aid in (A_BACK, A_PREVMENU):
            self.preview = None
            self.digits = ""
            self.close_bar()
        elif aid in A_NUMBERS:
            self.digit(A_NUMBERS[aid])
        elif aid == A_CONTEXT:
            self.close_bar()
            xbmc.executebuiltin("ActivateWindow(videoosd)")
        elif aid in (A_LEFT, A_RIGHT):
            self.close_bar()
            xbmc.executebuiltin("Action(StepBack)" if aid == A_LEFT else "Action(StepForward)")
        elif aid not in A_MOUSE:
            self.bar_until = time.time() + self.secs()

    # ------------------------------------------------------------------ Umschalten
    def secs(self):
        try:
            return max(2, int(W.state().get("infobar_secs") or 6))
        except (TypeError, ValueError):
            return 6

    def channels(self):
        if not self.chans or time.time() - self.chans_ts > 120:
            self.chans = W.ordered_channels()
            self.chans_ts = time.time()
        return self.chans

    def opening(self):
        return time.time() < self.opening_until

    def current_index(self):
        if self.opening() and self.target is not None:
            return self.target              # Sender startet noch – Kodi nicht nach dem laufenden fragen
        chans = self.channels()
        cid, name = W.current_channel()
        for i, (c, n, _) in enumerate(chans):
            if c == cid or (cid is None and W.key(n) == W.key(name)):
                return i
        return -1

    def zap(self, step):
        chans = self.channels()
        if not chans:
            xbmc.executebuiltin("Action(ChannelUp)" if step > 0 else "Action(ChannelDown)")
            return
        base = self.preview if self.preview is not None else self.current_index()
        self.preview = (base + step) % len(chans) if base >= 0 else 0
        self.preview_at = time.time() + 0.75     # erst umschalten, wenn nicht weitergedrückt wird
        self.digits = ""
        self.expanded = False
        self.show_bar()

    def commit(self, index=None):
        idx = self.preview if index is None else index
        self.preview = None
        chans = self.channels()
        if idx is None or not (0 <= idx < len(chans)):
            return
        cid, name, _ = chans[idx]
        if not self.opening() and cid == W.current_channel()[0]:
            self.refresh(force=True)
            return
        self.guard_until = time.time() + 10
        self.opening_until = time.time() + 15
        self.target = idx
        if self.backdrop is not None:
            self.backdrop.setProperty("busy", name)
        self.close_bar()
        W.stop_and_wait()                    # sauberer Wechsel (Kodi stürzte beim fliegenden Wechsel ab)
        W.rpc("Player.Open", {"item": {"channelid": cid}})
        self.show_bar()
        bg(W.api, "/api/kodi/event", {"event": "zap", "name": name})
        self.bar_until = time.time() + self.secs()
        self.refresh(force=True)

    def digit(self, d):
        self.digits = (self.digits + d)[-4:]
        self.digits_at = time.time() + 1.6
        self.preview = None
        self.show_bar()

    def dial(self):
        want, self.digits = self.digits, ""
        for i, (_, _, num) in enumerate(self.channels()):
            if str(num) == want.lstrip("0") or str(num) == want:
                self.commit(i)
                return
        self.refresh(force=True)

    def leave(self, how):
        """Zurück zur WangertTV-Oberfläche (Senderliste/Startseite oder TV-Programm)."""
        self.close_bar()
        self.guard_until = time.time() + 10
        res = W.api("/api/kodi/event", {"event": "leave", "how": how})
        if res is None:                      # WangertTV nicht erreichbar: wenigstens Kodis eigene Ansicht zeigen
            xbmc.executebuiltin("ActivateWindow(TVGuide)" if how == "guide" else "ActivateWindow(TVChannels)")

    # ------------------------------------------------------------------ Info-Leiste
    def toggle_expanded(self):
        self.expanded = not self.expanded
        self.bar_until = time.time() + max(self.secs(), 12)
        self.refresh(force=True)

    def show_bar(self):
        self.bar_until = time.time() + self.secs()
        if self.bar is None:
            self.bar = Bar("script-wangerttv-infobar.xml", W.ADDON_PATH, "Default", "1080i")
            self.bar.svc = self
            self.bar.show()
        self.refresh(force=True)

    def close_bar(self):
        bar, self.bar = self.bar, None
        self.expanded = False
        if bar is not None:
            bar.svc = None
            try:
                bar.close()
            except RuntimeError:
                pass
            del bar

    def info(self, cid):
        hit = self.info_cache.get(cid)
        if hit and time.time() - hit[0] < 20:
            return hit[1]
        data = W.channel_info(cid)
        self.info_cache[cid] = (time.time(), data)
        if len(self.info_cache) > 60:
            self.info_cache.pop(next(iter(self.info_cache)))
        return data

    def refresh(self, force=False):
        bar = self.bar
        if bar is None:
            return
        self.last_refresh = time.time()
        chans = self.channels()
        preview = self.preview is not None
        if preview:
            cid, name, num = chans[self.preview]
        else:
            idx = self.current_index()
            cid, name, num = chans[idx] if idx >= 0 else (W.current_channel()[0], xbmc.getInfoLabel("VideoPlayer.ChannelName"), "")
        info = self.info(cid) if cid is not None else {}
        name = info.get("name") or name
        now, nxt = info.get("now"), info.get("next")
        t = time.time()
        props = {"name": name, "number": str(num or ""), "logo": W.logo_for(name, info.get("icon", "")),
                 "preview": "1" if preview else "", "hint": W.t("ok_hint") if preview else "",
                 "digits": (self.digits + "_") if self.digits else "", "expanded": "1" if self.expanded else "",
                 "accent": W.state().get("accent") or "FF3FA9FF"}
        if now and now.get("title"):
            left = max(1, round((now["end"] - t) / 60)) if now["end"] else 0
            props.update(title=now["title"], plot=now.get("plot") or W.t("no_epg"), episode=now.get("episode") or now["title"],
                         times=f"{W.hm(now['start'])} – {W.hm(now['end'])}" + (f"  ·  {left} {W.t('min_left')}" if left else ""))
            pct = (t - now["start"]) / (now["end"] - now["start"]) * 100 if now["end"] > now["start"] else 0
        else:
            props.update(title=W.t("no_epg"), plot="", episode="", times="")
            pct = 0
        props["next"] = f"{W.t('next')} {W.hm(nxt['start'])}  {nxt['title']}" if nxt and nxt.get("title") else ""
        if preview or self.opening():
            props["hint"] = W.t("ok_hint") if preview else W.t("loading")
        if not preview and not self.opening() and xbmc.getCondVisibility("Player.HasVideo"):
            quality, hdr, vcodec = W.video_badges()
            audio = W.audio_badges()
            props.update(b_quality=quality, b_hdr=hdr, b_vcodec=vcodec, b_audio=audio[0] if audio else "",
                         b_channels=audio[1] if len(audio) > 1 and audio[1] != "Bitstream" else "",
                         b_bitstream="Bitstream" if "Bitstream" in audio else "")
        else:
            props.update(b_quality="", b_hdr="", b_vcodec="", b_audio="", b_channels="", b_bitstream="")
        try:
            for k, v in props.items():
                bar.setProperty(k, v)
            bar.getControl(20).setPercent(max(0.0, min(100.0, pct)))
        except (RuntimeError, TypeError):
            pass

    # ------------------------------------------------------------------ Wiedergabe-Ereignisse
    def av_started(self):
        tv = xbmc.getCondVisibility("Pvr.IsPlayingTv")
        self.tv_active = tv
        self.opening_until = 0.0
        self.target = None
        if self.backdrop is not None:
            self.backdrop.setProperty("busy", "")
        self.stop_check = 0.0
        self.info_cache.clear()
        name = xbmc.getInfoLabel("VideoPlayer.ChannelName") if tv else ""
        bg(W.api, "/api/kodi/event", {"event": "av_started", "tv": tv, "name": name})
        if tv and W.state().get("infobar_secs", 6) != 0:
            self.show_bar()

    def stopped(self, error=False):
        if error:
            self.opening_until = 0.0
            self.target = None
            bg(W.api, "/api/kodi/event", {"event": "error"})
        self.close_bar()
        if self.tv_active:
            self.stop_check = time.time() + 0.6      # kurz warten: beim Umschalten startet gleich der nächste Sender


if __name__ == "__main__":
    Service().run()
