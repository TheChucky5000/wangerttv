"""Handy-Funktionen: kleiner Webserver im Heimnetz.

* Texteingabe (/i/<token>): Einmal-Token; der Text landet im gerade offenen Eingabefeld.
* Fernbedienung (/r/<token>): dauerhafter Token aus den Einstellungen; Steuerkreuz, OK, Zurück,
  Home, Lautstärke, Ein/Aus und Text direkt in Netflix & Co. senden.
"""
import hmac
import html
import io
import json
import os
import secrets
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config
from .util import bus, log
from .webadmin import COOKIE, SESSION_DAYS, admin, read_page, to_json

PORT = config.PORT + 1
TOKEN_TTL = 10 * 60
_sessions = {}   # token -> {"label", "secret", "expires"}
_lock = threading.Lock()
_remote = {"token": None, "act": None}   # token: Funktion -> gültiger Token, act: Funktion(kind, value)
_share = {"token": None, "act": None}    # Vom Handy teilen: token-Funktion, act(kind, value)


def configure_remote(token_func, act_func):
    _remote["token"] = token_func
    _remote["act"] = act_func


def configure_share(token_func, act_func):
    _share["token"] = token_func
    _share["act"] = act_func


def share_url(token):
    return f"http://{lan_ip()}:{PORT}/s/{token}"

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


REMOTE_PAGE = """<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<meta name="theme-color" content="#07111f"><title>WangertTV Fernbedienung</title>
<style>
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;font-family:system-ui,sans-serif;background:#07111f;color:#e8f1ff;user-select:none;-webkit-user-select:none}
main{max-width:420px;margin:0 auto;padding:18px 18px 28px}
h1{font-size:1.05rem;margin:4px 0 16px;color:#8fb3d9;font-weight:600;text-align:center}
.row{display:flex;gap:12px;justify-content:center;margin:12px 0}
button{border:0;border-radius:18px;background:#14243d;color:#e8f1ff;font-size:1rem;font-weight:600;padding:16px 0;flex:1;
 touch-action:manipulation}
button:active{background:#00a8e8;color:#04101f}
.pad{position:relative;width:290px;height:290px;margin:18px auto;border-radius:50%;background:#0f1d33;box-shadow:inset 0 0 0 2px #1d3355}
.pad button{position:absolute;width:86px;height:86px;border-radius:50%;padding:0;font-size:1.6rem;background:transparent}
.pad button:active{background:#00a8e8}
.pad .up{top:6px;left:102px}.pad .down{bottom:6px;left:102px}.pad .left{left:6px;top:102px}.pad .right{right:6px;top:102px}
.pad .ok{top:87px;left:87px;width:116px;height:116px;background:#1b3a63;font-size:1.25rem}
.power{background:#3a1420}.power:active{background:#ff4d6d}
form{display:flex;gap:10px;margin-top:20px}
input{flex:1;min-width:0;font-size:1rem;padding:14px;border-radius:14px;border:1px solid #2a4a70;background:#0d1d33;color:#fff}
form button{flex:0 0 96px;background:linear-gradient(90deg,#00c8ff,#0057b8)}
.hint{color:#6f8fb3;font-size:.85rem;text-align:center;margin-top:14px}
.tp{margin-top:18px;height:230px;border-radius:22px;background:radial-gradient(circle at 50% 40%,#16365c,#0b1a2d);border:1px solid #2a4a70;
display:flex;align-items:flex-end;justify-content:center;touch-action:none;user-select:none;-webkit-user-select:none}
.tp span{color:#6f8fb3;font-size:.8rem;text-align:center;padding:12px;pointer-events:none}.tp.on{border-color:#00c8ff}
</style></head><body><main>
<h1>__NAME__</h1>
<div class="row"><button class="power" data-k="power">⏻ Ein/Aus</button><button data-k="home">⌂ Start</button><button data-k="settings">⚙</button></div>
<div class="pad"><button class="up" data-k="up">▲</button><button class="left" data-k="left">◀</button>
<button class="ok" data-k="ok">OK</button><button class="right" data-k="right">▶</button><button class="down" data-k="down">▼</button></div>
<div class="row"><button data-k="back">↩ Zurück</button><button data-k="playpause">⏯ Play/Pause</button><button data-k="mouse">🖱 Maus</button></div>
<div id="tp" class="tp"><span>Touchpad – wischen bewegt den Mauszeiger, tippen klickt,<br>mit zwei Fingern scrollen</span></div>
<div class="row"><button id="tpr">Rechtsklick</button></div>
<div class="row"><button data-k="voldown">🔉 −</button><button data-k="mute">🔇</button><button data-k="volup">🔊 +</button></div>
<form id="f"><input id="t" placeholder="Text an den Fernseher senden" autocomplete="off" autocapitalize="off"><button>Senden</button></form>
<p class="hint">Text landet im gerade ausgewählten Eingabefeld (z. B. Netflix-Anmeldung oder Suche).</p>
</main><script>
const base = location.pathname.replace(/\\/$/, "");
function send(kind, value) {
  fetch(base + "/" + kind, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({v: value})})
    .then(r => { if (r.status === 403) document.body.innerHTML = "<main><h1>Code ungültig – bitte am Fernseher neu scannen.</h1></main>"; })
    .catch(() => {});
}
let rep = null;
document.querySelectorAll("button[data-k]").forEach(b => {
  const k = b.dataset.k;
  const start = (e) => { e.preventDefault(); if (navigator.vibrate) navigator.vibrate(12); send("key", k);
    if (["up","down","left","right","volup","voldown"].includes(k)) { clearInterval(rep); rep = setTimeout(() => { rep = setInterval(() => send("key", k), 140); }, 450); } };
  const stop = () => { clearTimeout(rep); clearInterval(rep); rep = null; };
  b.addEventListener("pointerdown", start); ["pointerup","pointerleave","pointercancel"].forEach(ev => b.addEventListener(ev, stop));
});
// Touchpad: Bewegungen sammeln und höchstens alle 30 ms schicken (flüssig, ohne das Netz zu fluten)
const tp = document.getElementById("tp");
let acc = [0, 0, 0], last = null, downAt = 0, moved = 0, fingers = 0, timer = null;
function flush(click) {
  timer = null;
  const v = [Math.round(acc[0]), Math.round(acc[1]), Math.round(acc[2]), click || 0];
  acc = [acc[0] - v[0], acc[1] - v[1], acc[2] - v[2]];
  if (v[0] || v[1] || v[2] || v[3]) send("pointer", v);
}
function queue() { if (!timer) timer = setTimeout(flush, 30); }
const pts = new Map();
tp.addEventListener("pointerdown", e => { e.preventDefault(); tp.setPointerCapture(e.pointerId); pts.set(e.pointerId, [e.clientX, e.clientY]);
  fingers = Math.max(fingers, pts.size); if (pts.size === 1) { downAt = Date.now(); moved = 0; } tp.classList.add("on"); });
tp.addEventListener("pointermove", e => { const p = pts.get(e.pointerId); if (!p) return; e.preventDefault();
  const dx = e.clientX - p[0], dy = e.clientY - p[1]; pts.set(e.pointerId, [e.clientX, e.clientY]); moved += Math.abs(dx) + Math.abs(dy);
  if (pts.size >= 2) { acc[2] += -dy / 12; } else { const f = 1.6 + Math.min(2.4, Math.hypot(dx, dy) / 10); acc[0] += dx * f; acc[1] += dy * f; }
  queue(); });
function up(e) { if (!pts.has(e.pointerId)) return; pts.delete(e.pointerId);
  if (!pts.size) { tp.classList.remove("on");
    if (moved < 8 && Date.now() - downAt < 300) { if (navigator.vibrate) navigator.vibrate(10); clearTimeout(timer); flush(fingers >= 2 ? 2 : 1); }
    fingers = 0; } }
["pointerup", "pointercancel"].forEach(ev => tp.addEventListener(ev, up));
document.getElementById("tpr").addEventListener("click", () => { flush(2); });
document.getElementById("f").addEventListener("submit", e => { e.preventDefault(); const t = document.getElementById("t");
  if (t.value) { send("text", t.value); t.value = ""; } });
</script></body></html>"""


SHARE_PAGE = """<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#07111f">
<title>An __NAME__ senden</title>
<style>
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;font-family:system-ui,sans-serif;background:#07111f;color:#e8f1ff}
main{max-width:520px;margin:0 auto;padding:22px 18px 40px}
h1{font-size:1.3rem;margin:6px 0 4px}p{color:#8fb3d9;margin:0 0 18px;line-height:1.4}
.pick{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;height:190px;border-radius:22px;
 border:2px dashed #2f5a8a;background:#0d1d33;font-size:1.1rem;font-weight:600;text-align:center;cursor:pointer}
.pick span{font-size:2.6rem}.pick small{color:#8fb3d9;font-weight:400}
input[type=file]{display:none}
.f{display:flex;align-items:center;gap:12px;margin-top:12px;padding:12px 14px;border-radius:14px;background:#0f2038}
.f b{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:600}
.bar{width:90px;height:8px;border-radius:4px;background:#1d3355;overflow:hidden}.bar i{display:block;height:100%;width:0;background:#00c8ff}
.st{width:24px;text-align:center}
h2{font-size:1.05rem;margin:28px 0 10px}
form{display:flex;gap:10px}
input[type=url]{flex:1;min-width:0;font-size:1rem;padding:14px;border-radius:14px;border:1px solid #2a4a70;background:#0d1d33;color:#fff}
button{border:0;border-radius:14px;padding:0 18px;font-size:1rem;font-weight:600;color:#fff;background:linear-gradient(90deg,#00c8ff,#0057b8)}
.msg{margin-top:14px;color:#3ddc97;min-height:1.2em}
</style></head><body><main>
<h1>📲 An „__NAME__“ senden</h1>
<p>Fotos, Videos und Musik landen sofort auf dem Fernseher (und bleiben im Ordner „Handy“ gespeichert).</p>
<label class="pick" for="files"><span>＋</span>Fotos &amp; Videos auswählen<small>mehrere gleichzeitig möglich</small></label>
<input id="files" type="file" multiple accept="image/*,video/*,audio/*">
<div id="list"></div>
<h2>🔗 Link senden</h2>
<form id="lf"><input id="url" type="url" placeholder="YouTube- oder Web-Link einfügen" required><button>Senden</button></form>
<div class="msg" id="msg"></div>
</main><script>
const base = location.pathname.replace(/\\/$/, "");
const list = document.getElementById("list"), msg = document.getElementById("msg");
function row(name) {
  const el = document.createElement("div"); el.className = "f";
  el.innerHTML = '<b></b><span class="bar"><i></i></span><span class="st">⏳</span>';
  el.querySelector("b").textContent = name; list.prepend(el); return el;
}
function upload(file, el) {
  return new Promise((resolve) => {
    const x = new XMLHttpRequest();
    x.open("PUT", base + "/upload?name=" + encodeURIComponent(file.name));
    x.upload.onprogress = (e) => { if (e.lengthComputable) el.querySelector("i").style.width = (e.loaded / e.total * 100) + "%"; };
    x.onload = () => {
      let r = {}; try { r = JSON.parse(x.responseText); } catch (e) {}
      el.querySelector(".st").textContent = r.ok ? "✅" : "⚠️";
      if (!r.ok) el.querySelector("b").textContent += r.error === "no_space" ? " – kein Platz" : r.error === "token" ? " – Code ungültig" : " – Fehler";
      resolve(r.ok ? r.name : null);
    };
    x.onerror = () => { el.querySelector(".st").textContent = "⚠️"; resolve(null); };
    x.send(file);
  });
}
document.getElementById("files").addEventListener("change", async (e) => {
  const files = [...e.target.files]; e.target.value = "";
  const done = [];
  for (const f of files) { const n = await upload(f, row(f.name)); if (n) done.push(n); }
  if (done.length) {
    fetch(base + "/done", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({files: done})});
    msg.textContent = "✓ " + done.length + (done.length === 1 ? " Datei" : " Dateien") + " gesendet";
  }
});
document.getElementById("lf").addEventListener("submit", (e) => {
  e.preventDefault(); const u = document.getElementById("url");
  fetch(base + "/link", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({url: u.value})})
    .then(r => r.json()).then(r => { msg.textContent = r.ok ? "✓ Link wird auf dem Fernseher geöffnet" : "Link konnte nicht geöffnet werden"; if (r.ok) u.value = ""; });
});
</script></body></html>"""


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


def remote_url(token):
    return f"http://{lan_ip()}:{PORT}/r/{token}"


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

    def _remote_token(self):
        """Prüft /r/<token>[/aktion] gegen den Fernbedienungs-Token. Liefert die Aktion oder None."""
        parts = self.path.split("?")[0].strip("/").split("/")
        if len(parts) not in (2, 3) or parts[0] != "r" or not _remote["token"]:
            return None
        valid = _remote["token"]() or ""
        if not valid or not hmac.compare_digest(parts[1], valid):
            return False
        return parts[2] if len(parts) == 3 else ""

    def _share_token(self):
        """/s/<token>[/aktion] gegen den Teilen-Token prüfen. Liefert Aktion ("" = Seite), False (falsch) oder None."""
        parts = self.path.split("?")[0].strip("/").split("/")
        if len(parts) not in (2, 3) or parts[0] != "s" or not _share["token"]:
            return None
        valid = _share["token"]() or ""
        if not valid or not hmac.compare_digest(parts[1], valid):
            return False
        return parts[2] if len(parts) == 3 else ""

    def _length(self, limit):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return -1
        return n if 0 <= n <= limit else -1

    def do_PUT(self):
        """Datei vom Handy (Rohdaten, Name im Query-Parameter "name")."""
        from . import share
        action = self._share_token()
        if action is False or action is None:
            self._raw(b'{"ok":false,"error":"token"}', "application/json", 403)
            return
        if action != "upload":
            self._raw(b'{"ok":false}', "application/json", 404)
            return
        name = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("name", [""])[0]
        length = self._length(share.MAX_FILE)
        if length <= 0:
            self._raw(b'{"ok":false,"error":"too_large"}', "application/json", 413)
            return
        ok, res = share.receive(name, length, self.rfile)
        self._raw(json.dumps({"ok": ok, "error": "" if ok else res, "name": os.path.basename(res) if ok else ""}).encode(),
                  "application/json", 200 if ok else 507 if res == "no_space" else 400)
        if not ok:
            self.close_connection = True

    def _raw(self, data, ctype, code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(data)

    # ------------------------------------------------------------------ Web-Oberfläche (PC)
    def _admin_get(self, path):
        if not admin.enabled():
            self._send("<p class='ok'>Die Web-Oberfläche ist ausgeschaltet (am Fernseher: Einstellungen → Gerät).</p>", 404)
            return
        if path in ("/admin", "/admin/"):
            try:
                self._raw(read_page(), "text/html; charset=utf-8")
            except OSError:
                self._send("<p class='ok'>Seite nicht gefunden.</p>", 404)
            return
        if not admin.session_valid(self.headers.get("Cookie")):
            self._raw(to_json({"error": "login"}), "application/json", 401)
            return
        if path.startswith("/admin/appicon/"):
            from . import icons
            app = admin.api.core.apps.get(urllib.parse.unquote(path[len("/admin/appicon/"):]))
            target = icons.resolve(app)[0] if app else None
            if not target:
                self._raw(b"", "text/plain", 404)
                return
            import mimetypes
            with open(target, "rb") as fh:
                self._raw(fh.read(), mimetypes.guess_type(target)[0] or "application/octet-stream")
            return
        if path.startswith("/admin/chlogo/"):
            from . import logos
            name = os.path.basename(urllib.parse.unquote(path[len("/admin/chlogo/"):]))
            target = os.path.join(logos.CACHE, name)
            if not name.endswith(".png") or not os.path.isfile(target):
                self._raw(b"", "text/plain", 404)
                return
            with open(target, "rb") as fh:
                self._raw(fh.read(), "image/png")
            return
        if path.startswith("/admin/api/"):
            if self.headers.get("X-WTV-Admin") != "1":
                self._raw(to_json({"error": "forbidden"}), "application/json", 403)
                return
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            try:
                code, data = admin.call("GET", path[len("/admin"):], query=query)
            except Exception as exc:  # noqa: BLE001
                log.exception("Web-Oberfläche GET %s", path)
                code, data = 500, {"error": str(exc)}
            self._raw(to_json(data), "application/json", code)
            return
        self._raw(to_json({"error": "not_found"}), "application/json", 404)

    def _admin_post(self, path):
        if not admin.enabled():
            self._raw(to_json({"error": "disabled"}), "application/json", 404)
            return
        if self.headers.get("X-WTV-Admin") != "1":
            self._raw(to_json({"error": "forbidden"}), "application/json", 403)
            return
        length = min(int(self.headers.get("Content-Length") or 0), 256 * 1024)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            self._raw(to_json({"error": "bad_json"}), "application/json", 400)
            return
        if path == "/admin/login":
            ok, res = admin.login(self.client_address[0], body.get("pin", ""))
            if not ok:
                self._raw(to_json({"ok": False, "error": res}), "application/json", 403)
                return
            data = to_json({"ok": True})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Set-Cookie", f"{COOKIE}={res}; Path=/admin; Max-Age={SESSION_DAYS * 86400}; HttpOnly; SameSite=Strict")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/admin/logout":
            admin.logout(self.headers.get("Cookie"))
            data = to_json({"ok": True})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Set-Cookie", f"{COOKIE}=; Path=/admin; Max-Age=0; HttpOnly; SameSite=Strict")
            self.end_headers()
            self.wfile.write(data)
            return
        if not admin.session_valid(self.headers.get("Cookie")):
            self._raw(to_json({"error": "login"}), "application/json", 401)
            return
        if path.startswith("/admin/api/"):
            try:
                code, data = admin.call("POST", path[len("/admin"):], body=body)
            except Exception as exc:  # noqa: BLE001
                log.exception("Web-Oberfläche POST %s", path)
                code, data = 500, {"error": str(exc)}
            self._raw(to_json(data), "application/json", code)
            return
        self._raw(to_json({"error": "not_found"}), "application/json", 404)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/admin" or path.startswith("/admin/"):
            self._admin_get(path)
            return
        share_action = self._share_token()
        if share_action is False:
            self._send("<p class='ok'>Dieser Code ist ungültig. Bitte am Fernseher neu scannen.</p>", 403)
            return
        if share_action == "":
            name = html.escape(config.Settings().get("device_name") or "WangertTV Box")
            self._raw(SHARE_PAGE.replace("__NAME__", name).encode(), "text/html; charset=utf-8")
            return
        action = self._remote_token()
        if action is False:
            self._send("<p class='ok'>Dieser Code ist ungültig. Bitte am Fernseher neu scannen.</p>", 403)
            return
        if action == "":
            name = html.escape(config.Settings().get("device_name") or "WangertTV Box")
            self._raw(REMOTE_PAGE.replace("__NAME__", name).encode(), "text/html; charset=utf-8")
            return
        _, sess = self._session()
        if not sess:
            self._send("<p class='ok'>Dieser Code ist abgelaufen. Bitte am Fernseher neu öffnen.</p>", 404)
            return
        field = ('<input type="password" name="text" autofocus autocomplete="off">' if sess["secret"]
                 else '<textarea name="text" rows="4" autofocus autocapitalize="off" spellcheck="false"></textarea>')
        self._send(f"<h1>{html.escape(sess['label'])}</h1><p>Text eingeben und senden – er erscheint auf dem Fernseher.</p>"
                   f"<form method='post'>{field}<button type='submit'>An WangertTV senden</button></form>")

    def do_POST(self):
        path = self.path.split("?")[0]
        if path.startswith("/admin/"):
            self._admin_post(path)
            return
        share_action = self._share_token()
        if share_action is False:
            self._raw(b'{"ok":false}', "application/json", 403)
            return
        if share_action in ("link", "done"):
            length = self._length(16384)
            try:
                body = json.loads(self.rfile.read(max(0, length)) or b"{}") if length >= 0 else {}
            except ValueError:
                body = {}
            ok = False
            if _share["act"] and isinstance(body, dict):
                try:
                    ok = bool(_share["act"](share_action, body))
                except Exception:  # noqa: BLE001
                    log.exception("Vom Handy teilen")
            self._raw(json.dumps({"ok": ok}).encode(), "application/json")
            return
        action = self._remote_token()
        if action is False:
            self._raw(b'{"ok":false}', "application/json", 403)
            return
        if action == "pointer":
            # Touchpad: [dx, dy, scroll, click] – click: 0 = nichts, 1 = links, 2 = rechts
            length = min(int(self.headers.get("Content-Length") or 0), 1024)
            try:
                value = json.loads(self.rfile.read(length) or b"{}").get("v")
                value = [max(-400, min(400, int(x))) for x in value][:4]
            except (ValueError, AttributeError, TypeError):
                value = None
            ok = False
            if value and len(value) == 4 and _remote["act"]:
                try:
                    ok = bool(_remote["act"]("pointer", value))
                except Exception:  # noqa: BLE001
                    log.exception("Handy-Touchpad")
            self._raw(json.dumps({"ok": ok}).encode(), "application/json")
            return
        if action in ("key", "text"):
            length = min(int(self.headers.get("Content-Length") or 0), 8192)
            try:
                value = json.loads(self.rfile.read(length) or b"{}").get("v", "")
            except (ValueError, AttributeError):
                value = ""
            ok = False
            if isinstance(value, str) and value and _remote["act"]:
                try:
                    ok = bool(_remote["act"](action, value[:2000]))
                except Exception:  # noqa: BLE001
                    log.exception("Handy-Fernbedienung")
            self._raw(json.dumps({"ok": ok}).encode(), "application/json")
            return
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
