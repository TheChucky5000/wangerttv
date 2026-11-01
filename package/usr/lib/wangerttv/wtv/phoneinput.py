"""Texteingabe per Handy: kleiner Webserver im Heimnetz, abgesichert mit Einmal-Token.

Der Fernseher zeigt einen QR-Code; das Handy öffnet die Seite und schickt den Text
(z. B. WLAN-Passwort oder IPTV-Link) direkt in das gerade offene Eingabefeld.
"""
import html
import io
import secrets
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config
from .util import bus, log

PORT = config.PORT + 1
TOKEN_TTL = 10 * 60
_sessions = {}   # token -> {"label", "secret", "expires"}
_lock = threading.Lock()

PAGE = """<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WangertTV – Eingabe</title>
<style>
body{{margin:0;font-family:system-ui,sans-serif;background:#07111f;color:#e8f1ff;display:flex;min-height:100vh;align-items:center;justify-content:center}}
main{{width:min(92vw,520px)}}h1{{font-size:1.3rem;margin:0 0 .3rem}}p{{color:#8fb3d9;margin:0 0 1rem}}
textarea,input{{width:100%;box-sizing:border-box;font-size:1.1rem;padding:.9rem;border-radius:12px;border:1px solid #2a4a70;background:#0d1d33;color:#fff}}
button{{margin-top:1rem;width:100%;padding:1rem;font-size:1.1rem;border:0;border-radius:12px;background:linear-gradient(90deg,#00c8ff,#0057b8);color:#fff;font-weight:600}}
.ok{{text-align:center;font-size:1.2rem}}
</style></head><body><main>{body}</main></body></html>"""


def lan_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))   # keine echte Verbindung – ermittelt nur die ausgehende Adresse
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def qr_svg(text):
    try:
        import qrcode
        import qrcode.image.svg
    except ImportError:
        return ""
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode()


def new_session(label, secret=False):
    token = secrets.token_urlsafe(9)
    now = time.time()
    with _lock:
        for t in [t for t, s in _sessions.items() if s["expires"] < now]:
            del _sessions[t]
        _sessions[token] = {"label": label[:80], "secret": bool(secret), "expires": now + TOKEN_TTL}
    url = f"http://{lan_ip()}:{PORT}/i/{token}"
    return {"token": token, "url": url, "qr": qr_svg(url)}


def close_session(token):
    with _lock:
        _sessions.pop(token, None)


class Handler(BaseHTTPRequestHandler):
    server_version = "WangertTV"

    def log_message(self, fmt, *args):
        log.debug("phone: " + fmt, *args)

    def _session(self):
        parts = self.path.split("?")[0].strip("/").split("/")
        if len(parts) != 2 or parts[0] != "i":
            return None, None
        with _lock:
            sess = _sessions.get(parts[1])
        if not sess or sess["expires"] < time.time():
            return parts[1], None
        return parts[1], sess

    def _send(self, body, code=200):
        data = PAGE.format(body=body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        _, sess = self._session()
        if not sess:
            self._send("<p class='ok'>Dieser Code ist abgelaufen. Bitte am Fernseher neu öffnen.</p>", 404)
            return
        field = ('<input type="password" name="text" autofocus autocomplete="off">' if sess["secret"]
                 else '<textarea name="text" rows="4" autofocus autocapitalize="off" spellcheck="false"></textarea>')
        self._send(f"<h1>{html.escape(sess['label'])}</h1><p>Text eingeben und senden – er erscheint auf dem Fernseher.</p>"
                   f"<form method='post'>{field}<button type='submit'>An WangertTV senden</button></form>")

    def do_POST(self):
        token, sess = self._session()
        if not sess:
            self._send("<p class='ok'>Dieser Code ist abgelaufen.</p>", 404)
            return
        length = min(int(self.headers.get("Content-Length") or 0), 16384)
        form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
        text = (form.get("text") or [""])[0]
        bus.emit("phone_text", token=token, text=text)
        close_session(token)
        self._send("<p class='ok'>✓ Gesendet! Du kannst diese Seite schließen.</p>")


def serve():
    try:
        srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)  # noqa: S104 – bewusst im Heimnetz erreichbar, Token-geschützt
    except OSError as exc:
        log.warning("Handy-Eingabe nicht verfügbar: %s", exc)
        return
    threading.Thread(target=srv.serve_forever, daemon=True, name="phone-input").start()
