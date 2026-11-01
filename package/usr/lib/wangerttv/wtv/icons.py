"""App-Symbole: nutzt die offiziellen Symbole, die Apps und Dienste selbst bereitstellen.

Reihenfolge (wie bei TV-Launchern üblich):
  1. eigene Bilder in /etc/wangerttv/icons/<id>.png (bzw. <id>-banner.png für 16:9-Kacheln)
  2. Symbol aus der installierten App (Flatpak-Export, Debian-Paket, Android/Waydroid)
  3. App-Symbol der Webseite (Web-App-Manifest / apple-touch-icon), einmalig geladen und zwischengespeichert
"""
import glob
import html.parser
import json
import os
import threading
import urllib.parse

from . import config
from .util import http_get, log

CACHE = os.path.join(config.CACHE_DIR, "icons")
SYSTEM_ICONS = {
    "kodi": ["/usr/share/icons/hicolor/256x256/apps/kodi.png", "/usr/share/kodi/media/icon256x256.png"],
    "browser": ["/usr/share/icons/hicolor/256x256/apps/google-chrome.png", "/opt/google/chrome/product_logo_256.png"],
}
KODI_LIKE = {"livetv", "tvguide", "mediathek"}   # nutzen das Kodi-Symbol nur, wenn kein eigenes vorhanden ist
_pending = set()
_lock = threading.Lock()


def _largest(paths):
    def size(p):
        for part in p.split("/"):
            if "x" in part and part.split("x")[0].isdigit():
                return int(part.split("x")[0])
        return 999 if p.endswith(".svg") else 0
    paths = [p for p in paths if os.path.isfile(p)]
    return max(paths, key=size) if paths else None


def flatpak_icon(app_id):
    roots = ["/var/lib/flatpak/exports/share/icons/hicolor",
             os.path.join(config.HOME, ".local/share/flatpak/exports/share/icons/hicolor")]
    found = []
    for root in roots:
        found += glob.glob(os.path.join(root, "*", "apps", f"{app_id}.png"))
        found += glob.glob(os.path.join(root, "*", "apps", f"{app_id}.svg"))
    return _largest(found)


def android_icon(package):
    path = os.path.join(config.HOME, ".local/share/waydroid/data/icons", f"{package}.png")
    return path if os.path.isfile(path) else None


class _LinkParser(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.manifest = None
        self.touch = []

    def handle_starttag(self, tag, attrs):
        if tag != "link":
            return
        a = dict(attrs)
        rel = (a.get("rel") or "").lower()
        if "manifest" in rel and a.get("href"):
            self.manifest = a["href"]
        elif "apple-touch-icon" in rel and a.get("href"):
            self.touch.append((a.get("sizes", ""), a["href"]))
        elif rel in ("icon", "shortcut icon") and a.get("href") and ("192" in a.get("sizes", "") or "512" in a.get("sizes", "")):
            self.touch.append((a.get("sizes", ""), a["href"]))


def _fetch_web_icon(app_id, url):
    """Lädt das offizielle App-Symbol einer Webseite (wie ein Browser für Startbildschirm-Verknüpfungen)."""
    try:
        page = http_get(url, timeout=12, max_bytes=3 * 1024 * 1024,
                        headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/142 Safari/537.36"}).decode("utf-8", "replace")
        parser = _LinkParser()
        parser.feed(page)
        candidates = []
        if parser.manifest:
            murl = urllib.parse.urljoin(url, parser.manifest)
            manifest = json.loads(http_get(murl, timeout=10, max_bytes=512 * 1024).decode("utf-8", "replace"))
            for ic in manifest.get("icons", []):
                sizes = ic.get("sizes", "0x0").split()[0]
                w = int(sizes.split("x")[0]) if sizes.split("x")[0].isdigit() else 0
                if "monochrome" in (ic.get("purpose") or ""):
                    continue
                candidates.append((w, urllib.parse.urljoin(murl, ic.get("src", ""))))
        for sizes, href in parser.touch:
            w = int(sizes.split("x")[0]) if sizes and sizes.split("x")[0].isdigit() else 180
            candidates.append((w, urllib.parse.urljoin(url, href)))
        if not candidates:
            return
        candidates.sort(reverse=True)
        best = next((c for c in candidates if c[0] <= 512), candidates[0])[1]
        data = http_get(best, timeout=12, max_bytes=2 * 1024 * 1024)
        ext = ".svg" if data.lstrip()[:5] in (b"<?xml", b"<svg ") else ".png" if data[:8] == b"\x89PNG\r\n\x1a\n" else \
            ".webp" if data[8:12] == b"WEBP" else ".jpg" if data[:2] == b"\xff\xd8" else None
        if not ext:
            return
        os.makedirs(CACHE, exist_ok=True)
        with open(os.path.join(CACHE, f"{app_id}{ext}"), "wb") as fh:
            fh.write(data)
        from .util import bus
        bus.emit("apps")
    except Exception as exc:  # noqa: BLE001
        log.info("Kein Web-Symbol für %s: %s", app_id, exc)
    finally:
        with _lock:
            _pending.discard(app_id)


def resolve(app):
    """Liefert (Dateipfad, ist_banner) oder (None, False)."""
    app_id = app["id"]
    for folder in config.ICON_DIRS:
        for name, banner in ((f"{app_id}-banner", True), (app_id, False)):
            for ext in ("png", "svg", "webp", "jpg"):
                path = os.path.join(folder, f"{name}.{ext}")
                if os.path.isfile(path):
                    return path, banner
    typ = app.get("type")
    if typ == "flatpak" and app.get("flatpak"):
        path = flatpak_icon(app["flatpak"])
        if path:
            return path, False
    if typ == "android" and app.get("package"):
        path = android_icon(app["package"])
        if path:
            return path, False
    for path in SYSTEM_ICONS.get(app_id, []):
        if os.path.isfile(path):
            return path, False
    if typ in ("web", "flatpak") and (app.get("url") or app.get("fallback_url")) and not config.DEV:
        for ext in (".png", ".svg", ".webp", ".jpg"):
            path = os.path.join(CACHE, f"{app_id}{ext}")
            if os.path.isfile(path):
                return path, False
        with _lock:
            if app_id not in _pending:
                _pending.add(app_id)
                site = app.get("icon_site") or app.get("url") or app.get("fallback_url")
                threading.Thread(target=_fetch_web_icon, args=(app_id, site), daemon=True).start()
    return None, False


def url_for(app):
    path, banner = resolve(app)
    if not path:
        return "", False
    mtime = int(os.path.getmtime(path))
    return f"/appicon/{urllib.parse.quote(app['id'])}?v={mtime}", banner
