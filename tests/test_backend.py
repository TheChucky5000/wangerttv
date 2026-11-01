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
