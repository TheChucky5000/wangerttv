"""Tests für den WangertTV-Hintergrunddienst. Aufruf: python3 -m unittest discover tests"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "package/usr/lib/wangerttv")
TMP = tempfile.mkdtemp(prefix="wtv-test-")
os.environ.update(WTV_DEV="1", WTV_LIB=LIB, WTV_CONF=os.path.join(TMP, "conf"), WTV_CACHE=os.path.join(TMP, "cache"),
                  WTV_STATE=os.path.join(TMP, "state"), WTV_RUN=os.path.join(TMP, "run"))
sys.path.insert(0, LIB)

from wtv import config, iptv, util  # noqa: E402


class SettingsTest(unittest.TestCase):
    def test_defaults_and_patch(self):
        s = config.Settings(os.path.join(TMP, "s1.json"))
        self.assertEqual(s.get("device_name"), "WangertTV Box")
        changed = s.patch({"iptv": {"m3u": "http://x/list.m3u"}, "unknown_key": 1})
        self.assertEqual(changed, ["iptv"])
        self.assertEqual(s.get("iptv", "m3u"), "http://x/list.m3u")
        self.assertEqual(s.get("iptv", "refresh_hours"), 24)      # Rest bleibt erhalten
        self.assertNotIn("unknown_key", s.snapshot())
        s2 = config.Settings(os.path.join(TMP, "s1.json"))       # gespeichert?
        self.assertEqual(s2.get("iptv", "m3u"), "http://x/list.m3u")

    def test_patch_without_change(self):
        s = config.Settings(os.path.join(TMP, "s2.json"))
        self.assertEqual(s.patch({"lang": "de"}), [])


class M3UTest(unittest.TestCase):
    SAMPLE = """#EXTM3U url-tvg="http://epg"
#EXTINF:-1 tvg-id="ard.de" tvg-name="Das Erste" tvg-logo="https://logo/ard.png" group-title="ÖR" tvg-chno="1",Das Erste HD
http://stream/ard
#EXTINF:-1 tvg-logo="/local/zdf.png",ZDF HD
#EXTGRP:Öffentlich
http://stream/zdf
#EXTINF:-1 radio="true" group-title="Radio",WDR 2
http://stream/wdr2
"""

    def test_parse(self):
        ch = iptv.parse_m3u(self.SAMPLE)
        self.assertEqual([c["name"] for c in ch], ["Das Erste HD", "ZDF HD", "WDR 2"])
        self.assertEqual(ch[0]["logo"], "https://logo/ard.png")
        self.assertEqual(ch[0]["number"], 1)
        self.assertEqual(ch[1]["logo"], "")                 # lokale Pfade werden verworfen
        self.assertEqual(ch[1]["group"], "Öffentlich")
        self.assertTrue(ch[2]["radio"])

    def test_not_m3u(self):
        with self.assertRaises(ValueError):
            iptv.parse_m3u("<html>Fehler</html>")


class UtilTest(unittest.TestCase):
    def test_nmcli_split(self):
        self.assertEqual(util.split_nmcli(r"*:Mein\:WLAN:80:WPA2"), ["*", "Mein:WLAN", "80", "WPA2"])

    def test_slugify(self):
        self.assertEqual(util.slugify("WangertTV Box"), "wangerttv-box")
        self.assertEqual(util.slugify("Wohnzimmer – Fernseher"), "wohnzimmer-fernseher")
        self.assertEqual(util.slugify("Größe"), "grosse")
        self.assertEqual(util.slugify("!!!"), "wangerttv")

    def test_http_scheme_check(self):
        with self.assertRaises(ValueError):
            util.http_get("file:///etc/passwd")


class KodiConfigTest(unittest.TestCase):
    def test_iptv_instance_written(self):
        from wtv import kodi
        home = tempfile.mkdtemp()
        kodi.USERDATA = os.path.join(home, "userdata")
        s = config.Settings(os.path.join(TMP, "k.json"))
        s.patch({"iptv": {"m3u": "https://example.org/a&b.m3u", "epg": "https://example.org/epg.xml.gz"}})
        k = kodi.Kodi(s)
        k.prepare()
        inst = open(os.path.join(kodi.USERDATA, "addon_data/pvr.iptvsimple/instance-settings-1.xml"), encoding="utf-8").read()
        self.assertIn('<setting id="m3uUrl">https://example.org/a&amp;b.m3u</setting>', inst)
        self.assertIn('<setting id="epgPathType">1</setting>', inst)
        gui = open(os.path.join(kodi.USERDATA, "guisettings.xml"), encoding="utf-8").read()
        self.assertIn('services.esenabled', gui)
        self.assertIn('WangertTV Box', gui)
        self.assertTrue(os.path.exists(os.path.join(kodi.USERDATA, "peripheral_data/usb_2548_1002_CEC_Adapter.xml")))
        self.assertTrue(k.pending_iptv)


class CatalogTest(unittest.TestCase):
    def test_catalog_valid(self):
        with open(os.path.join(LIB, "apps.json"), encoding="utf-8") as fh:
            apps = json.load(fh)
        ids = [a["id"] for a in apps]
        self.assertEqual(len(ids), len(set(ids)))
        for a in apps:
            self.assertIn(a["type"], {"web", "browser", "kodi", "kodi_addon", "flatpak", "native", "android", "internal"})
            if a["type"] in ("web", "browser"):
                self.assertTrue(a["url"].startswith("https://"))
        for default in config.DEFAULTS["apps"]:
            self.assertIn(default, ids)


class ServerSecurityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import ThreadingHTTPServer
        from wtv.core import Core
        from wtv.server import Api, make_handler
        cls.core = Core()
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Api(cls.core)))
        cls.port = cls.srv.server_address[1]
        import wtv.server as srvmod
        srvmod.ALLOWED_HOSTS.add(f"127.0.0.1:{cls.port}")
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def req(self, path, body=None, headers=None):
        import urllib.error
        import urllib.request
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, headers=headers or {},
                                   method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(r, timeout=5) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, {}

    def test_post_requires_header(self):
        self.assertEqual(self.req("/api/home", {})[0], 403)
        self.assertEqual(self.req("/api/home", {}, {"X-WTV": "1"})[0], 200)

    def test_foreign_host_rejected(self):
        self.assertEqual(self.req("/api/state", headers={"Host": "evil.example:8765"})[0], 403)

    def test_path_traversal(self):
        self.assertIn(self.req("/assets/../../../../etc/passwd")[0], (403, 404))

    def test_state(self):
        code, data = self.req("/api/state")
        self.assertEqual(code, 200)
        self.assertIn("settings", data)


if __name__ == "__main__":
    unittest.main()


class TvNavTest(unittest.TestCase):
    """Web-Apps bekommen den DevTools-Port für die Fernbedienungs-Steuerung (Bug bis 1.0.30: immer aus)."""

    def test_port_enabled_by_default(self):
        from wtv.apps import AppManager
        s = config.Settings(os.path.join(TMP, "tv1.json"))
        am = AppManager(s, None, None)
        netflix = am.get("netflix")
        self.assertIsNotNone(am.tvnav_port(netflix))
        cmd = am._chrome_cmd(netflix) if util.which("google-chrome-stable") or util.which("google-chrome") else None
        if cmd:
            self.assertTrue(any(c.startswith("--remote-debugging-port=") for c in cmd))
        self.assertIsNone(am.tvnav_port(am.get("youtube")))          # YouTube hat eigene TV-Oberfläche
        s.patch({"tvnav": False})
        self.assertIsNone(am.tvnav_port(netflix))


class _FakeKodi:
    pass


class SurroundModeTest(unittest.TestCase):
    def _kodi(self, audio, kodi_cfg, formats=("ac3", "dts", "eac3")):
        from wtv import kodi as kodimod, system
        s = config.Settings(os.path.join(TMP, "pcm.json"))
        s.patch({"audio": audio, "kodi": kodi_cfg})
        k = kodimod.Kodi(s)
        system_hdmi_receiver = system.hdmi_receiver
        self.addCleanup(setattr, system, "hdmi_receiver", system_hdmi_receiver)
        system.hdmi_receiver = lambda: {"pcm_channels": 8, "formats": list(formats)}
        return k

    def test_auto_uses_stereo_pcm_with_passthrough(self):
        k = self._kodi({"speakers": "5.1", "pcm_mode": "auto"}, {"passthrough": True})
        self.assertEqual(k.pcm_layout(), "2.0")

    def test_multi_keeps_channels(self):
        k = self._kodi({"speakers": "7.1", "pcm_mode": "multi"}, {"passthrough": True})
        self.assertEqual(k.pcm_layout(), "7.1")

    def test_auto_without_passthrough_needs_pcm(self):
        k = self._kodi({"speakers": "5.1", "pcm_mode": "auto"}, {"passthrough": False})
        self.assertEqual(k.pcm_layout(), "5.1")

    def test_auto_without_dolby_receiver(self):
        k = self._kodi({"speakers": "5.1", "pcm_mode": "auto"}, {"passthrough": True}, formats=())
        self.assertEqual(k.pcm_layout(), "5.1")

    def test_kodi_settings_auto(self):
        """Automatik: Kodi-Lautsprecher 2.0 + Umrechnung in Dolby Digital, kein Hochmischen (sonst immer 5.1)."""
        from wtv import system
        k = self._kodi({"speakers": "5.1", "pcm_mode": "auto"}, {"passthrough": True})
        sent = {}
        k._set = lambda key, val: sent.__setitem__(key, val)
        k._device_option = lambda *a: "PIPEWIRE:hdmi"
        k._passthrough_device = lambda: "PIPEWIRE:hdmi"
        orig = (system.hdmi_sink, system._sink_channels, system._node_offers_iec958, system.set_passthrough_formats)
        self.addCleanup(lambda: (setattr(system, "hdmi_sink", orig[0]), setattr(system, "_sink_channels", orig[1]),
                                 setattr(system, "_node_offers_iec958", orig[2]), setattr(system, "set_passthrough_formats", orig[3])))
        system.hdmi_sink = lambda: "alsa_output.hdmi-stereo"
        system._sink_channels = lambda n: 2
        system._node_offers_iec958 = lambda n: True
        system.set_passthrough_formats = lambda f: True
        k.apply_audio_video()
        self.assertEqual(sent["audiooutput.channels"], 1)          # 2.0 – Voraussetzung für "Transcode to AC3"
        self.assertTrue(sent["audiooutput.ac3transcode"])
        self.assertFalse(sent["audiooutput.stereoupmix"])
        self.assertTrue(sent["audiooutput.passthrough"])

    def test_kodi_settings_multi(self):
        from wtv import system
        k = self._kodi({"speakers": "5.1", "pcm_mode": "multi", "upmix": True}, {"passthrough": True})
        sent = {}
        k._set = lambda key, val: sent.__setitem__(key, val)
        k._device_option = lambda *a: "PIPEWIRE:hdmi"
        k._passthrough_device = lambda: "PIPEWIRE:hdmi"
        orig = (system.hdmi_sink, system._sink_channels, system._node_offers_iec958, system.set_passthrough_formats)
        self.addCleanup(lambda: (setattr(system, "hdmi_sink", orig[0]), setattr(system, "_sink_channels", orig[1]),
                                 setattr(system, "_node_offers_iec958", orig[2]), setattr(system, "set_passthrough_formats", orig[3])))
        system.hdmi_sink = lambda: "alsa_output.hdmi-surround71"
        system._sink_channels = lambda n: 8
        system._node_offers_iec958 = lambda n: True
        system.set_passthrough_formats = lambda f: True
        k.apply_audio_video()
        self.assertEqual(sent["audiooutput.channels"], 8)          # 5.1 als PCM
        self.assertFalse(sent["audiooutput.ac3transcode"])
        self.assertTrue(sent["audiooutput.stereoupmix"])


class PlayerTest(unittest.TestCase):
    def test_kinds(self):
        from wtv import player
        self.assertEqual(player.kind("Film.MKV"), "video")
        self.assertEqual(player.kind("song.flac"), "audio")
        self.assertEqual(player.kind("foto.HEIC"), "image")
        self.assertEqual(player.kind("liste.m3u"), "playlist")
        self.assertIsNone(player.kind("notes.txt"))

    def test_allowed(self):
        from wtv import player
        self.assertTrue(player.allowed("/media/tv/STICK/Film.mkv"))
        self.assertFalse(player.allowed("/etc/shadow"))
        self.assertFalse(player.allowed("/media/../etc/passwd"))

    def test_browse_and_forget(self):
        from wtv import player
        d = player.browse("/media/tv/INTENSO")
        self.assertTrue(d["ok"])
        self.assertTrue(any(i["type"] == "dir" for i in d["items"]))
        config.atomic_write_json(player.PROGRESS, [{"path": "/a.mkv", "pos": 10, "duration": 100}, {"path": "/b.mkv"}])
        player.forget("/a.mkv")
        self.assertEqual([e["path"] for e in config.read_json(player.PROGRESS, [])], ["/b.mkv"])

    def test_play_media(self):
        from wtv.apps import AppManager
        s = config.Settings(os.path.join(TMP, "pl.json"))
        am = AppManager(s, None, None)
        res = am.play_media("/media/tv/INTENSO/Filme/x.mkv")
        self.assertTrue(res["ok"])
        self.assertIn("player", am.running)
        self.assertEqual(am.launch("player"), {"ok": True, "resumed": True})
        self.assertFalse(am.play_media("")["ok"])

    def test_migration_replaces_vlc(self):
        s = config.Settings(os.path.join(TMP, "mig.json"))
        s.patch({"apps": ["livetv", "vlc", "mediathek", "browser"]})
        from wtv.core import Core
        core = Core.__new__(Core)
        core.settings = s
        core._migrate()
        want = ["livetv", "tvplaner", "mediathek", "player", "radio", "browser"]
        self.assertEqual(s.get("apps"), want)
        core._migrate()                                            # nur einmal
        self.assertEqual(s.get("apps"), want)


class _NoDev:
    """Funktionen wie auf der echten Box ausführen (ohne DEV-Attrappen)."""

    def __enter__(self):
        config.DEV = False
        return self

    def __exit__(self, *a):
        config.DEV = True


class FilesTest(unittest.TestCase):
    def setUp(self):
        from wtv import files
        self.files = files
        self.root = tempfile.mkdtemp(prefix="wtv-files-")
        self.orig = files.ROOTS
        files.ROOTS = (self.root,)
        os.makedirs(os.path.join(self.root, "a/sub"))
        with open(os.path.join(self.root, "a/film.mkv"), "wb") as fh:
            fh.write(b"x" * 5000)

    def tearDown(self):
        self.files.ROOTS = self.orig

    def test_listing_and_ops(self):
        f = self.files
        with _NoDev():
            d = f.listing(os.path.join(self.root, "a"))
            self.assertTrue(d["ok"])
            self.assertEqual([i["name"] for i in d["items"]], ["sub", "film.mkv"])     # Ordner zuerst
            self.assertEqual(d["items"][1]["kind"], "video")
            self.assertTrue(f.operate("mkdir", os.path.join(self.root, "a"), "Neu")["ok"])
            self.assertEqual(f.operate("mkdir", os.path.join(self.root, "a"), "Neu")["message"], "exists")
            self.assertEqual(f.operate("mkdir", os.path.join(self.root, "a"), "../raus")["ok"], True)  # "/" wird ersetzt
            self.assertFalse(os.path.exists(os.path.join(self.root, "raus")))
            r = f.operate("rename", os.path.join(self.root, "a/film.mkv"), "Film 2.mkv")
            self.assertTrue(r["ok"])
            self.assertFalse(f.operate("delete", paths=["/etc/passwd"])["ok"])           # außerhalb: verboten
            self.assertFalse(f.listing("/etc")["ok"])

    def test_copy_and_move(self):
        f = self.files
        with _NoDev():
            src, dst = os.path.join(self.root, "a"), os.path.join(self.root, "ziel")
            os.mkdir(dst)
            self.assertEqual(f.transfer("copy", [src], src)["message"], "into_itself")
            self.assertTrue(f.transfer("copy", [src], dst)["ok"])
            for _ in range(100):
                if f.job()["state"] != "running":
                    break
                import time
                time.sleep(0.05)
            self.assertEqual(f.job()["state"], "done")
            self.assertTrue(os.path.isfile(os.path.join(dst, "a/film.mkv")))
            self.assertTrue(f.transfer("copy", [src], dst)["ok"])                     # zweites Mal: "a (2)"
            for _ in range(100):
                if f.job()["state"] != "running":
                    break
                import time
                time.sleep(0.05)
            self.assertTrue(os.path.isdir(os.path.join(dst, "a (2)")))


class ShareTest(unittest.TestCase):
    def test_receive_and_names(self):
        import io
        from wtv import share
        share.FOLDER = tempfile.mkdtemp(prefix="wtv-handy-")
        ok, path = share.receive("../../etc/IMG 1.jpg", 4, io.BytesIO(b"abcd"))
        self.assertTrue(ok)
        self.assertTrue(path.startswith(share.FOLDER))
        self.assertEqual(os.path.basename(path), "IMG 1.jpg")
        ok2, path2 = share.receive("IMG 1.jpg", 4, io.BytesIO(b"abcd"))
        self.assertTrue(path2.endswith("IMG 1 (2).jpg"))
        ok3, err = share.receive("kaputt.jpg", 10, io.BytesIO(b"abc"))       # Verbindung abgebrochen
        self.assertFalse(ok3)
        self.assertFalse(any(n.endswith(".part") for _, _, fs in os.walk(share.FOLDER) for n in fs))
        self.assertEqual(share.youtube_id("https://youtu.be/dQw4w9WgXcQ?t=3"), "dQw4w9WgXcQ")
        self.assertEqual(share.youtube_id("https://www.youtube.com/watch?app=desktop&v=dQw4w9WgXcQ"), "dQw4w9WgXcQ")
        self.assertIsNone(share.youtube_id("https://example.com/"))


class ScheduleTest(unittest.TestCase):
    def test_primetime(self):
        from wtv import iptv
        s = config.Settings(os.path.join(TMP, "sched.json"))
        store = iptv.ChannelStore(s)
        at = 1_800_000_000
        config.atomic_write_json(iptv.EPG_CACHE, {"ts": at, "channels": {
            "das erste": [[at - 3600, at - 60, "Vorher"], [at - 60, at + 5400, "Tatort"]],
            "zdf": [[at - 7200, at + 600, "Lang"], [at + 600, at + 4000, "Krimi"]],
            "rtl": [[at - 7200, at + 3600, "Film läuft schon"]]}})
        store.channels = lambda force=False: [{"name": "Das Erste"}, {"name": "ZDF"}, {"name": "RTL"}, {"name": "Ohne EPG"}]
        with _NoDev():
            items = {i["name"]: i["title"] for i in store.schedule(at)}
        self.assertEqual(items, {"Das Erste": "Tatort", "ZDF": "Krimi", "RTL": "Film läuft schon"})


class RemindersTest(unittest.TestCase):
    def test_add_remove(self):
        import time
        from wtv.core import Core
        core = Core.__new__(Core)
        core.settings = config.Settings(os.path.join(TMP, "rem.json"))
        it = {"name": "ZDF", "title": "Krimi", "start": int(time.time()) + 3600, "end": int(time.time()) + 7200}
        self.assertEqual(len(core.reminder("add", it, auto=True)["reminders"]), 1)
        self.assertTrue(core.settings.get("reminders")[0]["auto"])
        self.assertEqual(core.reminder("add", it)["reminders"][0]["auto"], False)       # ersetzt, nicht doppelt
        self.assertEqual(core.reminder("del", it)["reminders"], [])


class CecParseTest(unittest.TestCase):
    def test_cec_client_output(self):
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("wtvremote", os.path.join(LIB, "bin", "wtv-remote"))
        spec = importlib.util.spec_from_loader("wtvremote", loader)
        m = importlib.util.module_from_spec(spec)
        loader.exec_module(m)
        calls = []
        m.api = lambda path, body: calls.append((path, body))
        r = m.Remote.__new__(m.Remote)
        r.settings = {"cec": {}}
        r.cec_state = {"adapter": True, "devices": [], "logical": "", "avr": False, "active": None, "port": "", "tv_power": "", "tv_vendor": ""}
        r.cec_scan_block, r.phys_addr, r.ctx, r.last_input = None, None, "default", 0
        taps, sent = [], []
        r.tap, r.handle_learned, r.write_status = taps.append, (lambda *a: False), (lambda: None)
        r.cec_send = lambda line: sent.append(line) or True
        for line in ["NOTICE:   [  301]\tCEC client registered: libCEC version = 6.0.2, logical address(es) = Playback 1 (4) , physical address: 1.1.0.0",
                     "device #0: TV", "vendor:        Philips", "power status:  on", "device #5: Audio", "osd string:    AVR-X1600H",
                     "currently active source: Playback 1 (4)",
                     "TRAFFIC: [ 4012]\t>> 04:44:01", "TRAFFIC: [ 4100]\t>> 50:7a:2d", "TRAFFIC: [ 4200]\t>> 04:8d:01",
                     "TRAFFIC: [ 5000]\t>> 8f:82:12:00"]:
            r.cec_line(line)
        self.assertEqual(r.cec_state["port"], "1.1.0.0")
        self.assertEqual(r.cec_state["tv_vendor"], "Philips")
        self.assertTrue(r.cec_state["avr"])
        self.assertEqual(r.cec_state["avr_volume"], 45)
        self.assertEqual(taps, ["KEY_UP"])
        self.assertIn("tx 40:8e:00", sent)
        self.assertIn(("/api/remote/action", {"action": "pause_all", "source": "cec"}), calls)
        edid = bytearray(256)
        edid[:8] = b"\x00\xff\xff\xff\xff\xff\xff\x00"
        edid[128:131] = bytes([2, 3, 12])
        edid[132:138] = bytes([0x65, 0x03, 0x0c, 0x00, 0x21, 0x00])
        self.assertEqual(m.fmt_pa(m.parse_edid_physical_address(bytes(edid))), "2.1.0.0")


class FlatpakHandoffTest(unittest.TestCase):
    def test_handoff_keeps_app_running(self):
        """Spotify übergibt einen zweiten Start an die laufende Instanz – die App darf nicht als beendet gelten."""
        from wtv import apps as appsmod
        s = config.Settings(os.path.join(TMP, "fp.json"))
        am = appsmod.AppManager(s, None, None)

        class Done:
            returncode = 0

            def poll(self):
                return 0
        am.running["spotify_desktop"] = {"proc": Done(), "ws": "app-spotify_desktop", "started": 0, "app": "spotify_desktop",
                                         "profile": None, "flatpak": "com.spotify.Client"}
        orig = appsmod.flatpak_running
        appsmod.flatpak_running = lambda ref: ref == "com.spotify.Client"
        try:
            entry = am.running["spotify_desktop"]
            self.assertTrue(appsmod.instance_alive(entry))
        finally:
            appsmod.flatpak_running = orig


class RadioDbTest(unittest.TestCase):
    def test_bundled_db(self):
        from wtv import radio
        info = radio.db_info()
        self.assertGreater(info["count"], 3000)                   # alle deutschen Sender mitgeliefert
        self.assertTrue(any(s["name"] == "SWR3" for s in radio.top()))
        self.assertTrue(radio.by_state("Bayern"))
        self.assertTrue(radio.genre("schlager"))
        self.assertTrue(radio.search("antenne bayern")[0]["name"].lower().startswith("antenne bayern"))
        self.assertEqual({x["name"] for x in radio.states()} - set(__import__("wtv.radiodb", fromlist=["x"]).BUNDESLAENDER), set())

    def test_build_and_states(self):
        from wtv import radiodb
        raw = [{"stationuuid": "1", "name": "Radio X", "url_resolved": "https://a/1", "state": "Bavaria", "clickcount": 5, "tags": "pop"},
               {"stationuuid": "2", "name": "radio x", "url_resolved": "https://a/2", "state": "", "clickcount": 50, "tags": "pop"},
               {"stationuuid": "3", "name": "Kölncampus", "url_resolved": "https://a/3", "state": "Köln", "clickcount": 1},
               {"stationuuid": "4", "name": "Kaputt", "url_resolved": "", "clickcount": 1}]
        out = radiodb.build(raw)
        self.assertEqual([s["id"] for s in out], ["2", "3"])              # Doppelte zusammengefasst, beliebteste bleibt
        self.assertEqual(radiodb.normalize_state({"state": "Sachsen-Anhalt", "name": "x"}), "Sachsen-Anhalt")
        self.assertEqual(radiodb.normalize_state({"state": "", "name": "WDR 2"}), "Nordrhein-Westfalen")

    def test_cover_title_split(self):
        from wtv import coverart
        self.assertEqual(coverart.split_title("The Killers - Mr. Brightside"), ("The Killers", "Mr. Brightside"))
        self.assertEqual(coverart.split_title("Atemlos von Helene Fischer"), ("Helene Fischer", "Atemlos"))
        self.assertEqual(coverart.lookup("SWR3 Nachrichten", "SWR3"), "")


class SwayFocusTest(unittest.TestCase):
    def test_background_workspace_init_is_not_focus(self):
        """Eine im Hintergrund entstehende Arbeitsfläche (Spotify) darf den Fokus nicht "stehlen"."""
        from wtv.sway import Sway
        sw = Sway()
        seen = []
        sw.on_focus(seen.append)
        sw._handle({"change": "init", "current": {"name": "app-spotify_desktop", "focused": False}})
        self.assertEqual(sw.focused, "home")
        sw._handle({"change": "focus", "current": {"name": "app-kodi", "focused": True}})
        self.assertEqual(sw.focused, "app-kodi")
        self.assertEqual(seen, ["app-kodi"])


class WeatherOfflineTest(unittest.TestCase):
    def test_stale_weather_when_offline(self):
        import time
        from wtv import weather
        good = {"ok": True, "place": "Berlin", "temp": 7, "code": 3, "is_day": 1, "daily": []}
        weather._cache.update(key=("berlin", "de"), ts=time.time() - 3600, data=good)
        orig = weather._geocode

        def boom(*a):
            raise OSError("Temporary failure in name resolution")
        weather._geocode = boom
        config.DEV = False
        try:
            weather._cache["ts"] = time.time() - 3600       # Cache abgelaufen (älter als 20 min), Netz weg
            res = weather.get("Berlin", "de")
        finally:
            config.DEV = True
            weather._geocode = orig
        self.assertTrue(res["ok"])
        self.assertTrue(res["stale"])
        self.assertEqual(res["temp"], 7)


class MusicExclusiveTest(unittest.TestCase):
    def test_spotify_stops_radio_and_radio_pauses_spotify(self):
        from wtv.media import Receivers
        s = config.Settings(os.path.join(TMP, "mx.json"))
        rec = Receivers(s)
        calls = []
        rec.on_play = lambda src: calls.append(("radio_stop", src))
        rec.librespot_event({"PLAYER_EVENT": "playing", "NAME": "Song", "ARTISTS": "Band"})
        self.assertEqual(calls, [("radio_stop", "spotify")])          # Spotify Connect startet -> Radio aus
        stopped = []
        rec.procs["spotify"] = object()
        rec.stop = lambda name: (stopped.append(name), rec.procs.pop(name, None))
        rec.start = lambda name: None
        rec.pause_spotify()                                          # Radio startet -> Spotify anhalten
        self.assertEqual(stopped, ["spotify"])
        self.assertEqual(rec.nowplaying, {})


class LogosTest(unittest.TestCase):
    def test_match_and_quality(self):
        from wtv import logos
        self.assertEqual(logos.match("Das Erste HD"), "das-erste-de.png")
        self.assertEqual(logos.match("n-tv HD"), "ntv-de.png")
        self.assertEqual(logos.match("ZDFneo HD"), "zdf-neo-de.png")
        self.assertIsNone(logos.match("Gibt es nicht TV 123"))
        self.assertEqual(logos.quality("RTL UHD"), "UHD")
        self.assertEqual(logos.quality("ProSieben HD"), "HD")
        self.assertEqual(logos.quality("Bibel TV"), "")


class ArrangeTest(unittest.TestCase):
    def test_order_hidden_renumber(self):
        s = config.Settings(os.path.join(TMP, "s_arr.json"))
        s.patch({"tv": {"order": ["ZDF HD", "Das Erste HD"], "hidden": ["RTL"], "renumber": True}})
        store = iptv.ChannelStore(s)
        chans = [{"name": n, "number": i + 1} for i, n in enumerate(["Das Erste HD", "RTL", "ZDF HD", "VOX"])]
        out = store.arrange(chans)
        self.assertEqual([c["name"] for c in out], ["ZDF HD", "Das Erste HD", "VOX"])
        self.assertEqual([c["number"] for c in out], [1, 2, 3])
        self.assertEqual(out[0]["quality"], "HD")
        full = store.arrange(chans, include_hidden=True)
        self.assertTrue(any(c.get("hidden") and c["name"] == "RTL" for c in full))


class HistoryTest(unittest.TestCase):
    def test_learn_suggest_shows(self):
        import time as _t
        from wtv import history
        history.clear()
        now = _t.time()
        for day in range(3):
            ts = now - day * 86400
            history.record("channel", "ZDF HD", 15, title="heute journal", episode=f"Folge {day}", ts=ts)
            history.record("app", "youtube", 3, ts=ts)
        history.record("channel", "RTL", 1, ts=now - 12 * 3600)          # andere Tageszeit, wenig
        sug = history.suggest(limit=5, ts=now)
        self.assertEqual(sug[0]["name"], "ZDF HD")
        self.assertIn("youtube", [x["name"] for x in sug])
        fav = history.favorite_shows()
        self.assertEqual(fav[0]["title"], "heute journal")
        self.assertTrue(history.seen_episode("heute journal", "Folge 1"))
        self.assertFalse(history.seen_episode("heute journal", "Folge 9"))
        history.record("app", "settings", 3)                                # Systemansichten zählen nicht
        self.assertNotIn("settings", [x["name"] for x in history.suggest(limit=50, min_score=0)])
        history.save(force=True)
        self.assertTrue(os.path.exists(history.FILE))
        history.clear()
        self.assertEqual(history.suggest(), [])


class KodiAddonTest(unittest.TestCase):
    def test_keymap_and_state(self):
        import xml.dom.minidom
        from wtv import kodi
        own = kodi.keymap(True)
        xml.dom.minidom.parseString(own)
        self.assertIn("NotifyAll(service.wangerttv,wtv_up)", own)
        self.assertIn("<numpadfive>NotifyAll(service.wangerttv,wtv_num5)</numpadfive>", own)
        legacy = kodi.keymap(False)
        self.assertNotIn("wtv_up", legacy)
        xml.dom.minidom.parse(os.path.join(LIB, "kodi/service.wangerttv/addon.xml"))
        xml.dom.minidom.parse(os.path.join(LIB, "kodi/service.wangerttv/resources/skins/Default/1080i/script-wangerttv-infobar.xml"))

    def test_addon_install(self):
        from wtv import kodi
        old = kodi.USERDATA
        kodi.USERDATA = os.path.join(tempfile.mkdtemp(prefix="kodi-"), "userdata")
        try:
            k = kodi.Kodi(config.Settings(os.path.join(TMP, "s_k.json")))
            self.assertTrue(k.install_addon())
            self.assertTrue(os.path.isfile(os.path.join(kodi.addon_dst(), "service.py")))
            self.assertTrue(k.install_addon())                 # zweiter Aufruf: unverändert, nichts zu tun
        finally:
            kodi.USERDATA = old
