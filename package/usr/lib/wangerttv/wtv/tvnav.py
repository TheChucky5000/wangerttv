"""Fernbedienungs-Steuerung für Web-Apps.

Web-Apps (Netflix, Spotify, Prime Video, …) laufen in Chrome mit lokalem DevTools-Port (nur 127.0.0.1).
WangertTV verbindet sich darüber und spielt tvnav.js in jede Seite ein: Pfeiltasten springen zwischen
Schaltflächen/Feldern, OK klickt bzw. öffnet die Bildschirmtastatur.

Bewusst ohne Zusatzpakete: minimaler WebSocket-Client mit der Python-Standardbibliothek.
"""
import base64
import json
import os
import socket
import struct
import threading
import time
import urllib.parse
import urllib.request

from . import config
from .util import log

SCRIPT_PATH = os.path.join(config.LIB, "tvnav", "tvnav.js")
BASE_PORT = 9300


class WebSocket:
    """Kleiner WebSocket-Client (RFC 6455) – reicht für das DevTools-Protokoll auf 127.0.0.1."""

    def __init__(self, url, timeout=10):
        u = urllib.parse.urlparse(url)
        if u.hostname not in ("127.0.0.1", "localhost"):
            raise OSError("nur lokale DevTools-Verbindungen erlaubt")
        self.lock = threading.Lock()
        self.sock = socket.create_connection((u.hostname, u.port or 80), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise OSError("Verbindung geschlossen")
            resp += chunk
            if len(resp) > 65536:
                raise OSError("ungültige Antwort")
        head, _, self.buf = resp.partition(b"\r\n\r\n")
        if b" 101 " not in head.split(b"\r\n", 1)[0] + b" ":
            raise OSError("WebSocket-Handshake abgelehnt")

    def _frame(self, opcode, data):
        hdr = bytearray([0x80 | opcode])
        n = len(data)
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126)
            hdr += struct.pack(">H", n)
        else:
            hdr.append(0x80 | 127)
            hdr += struct.pack(">Q", n)
        mask = os.urandom(4)
        hdr += mask
        mask_long = (mask * (n // 4 + 1))[:n]
        masked = (int.from_bytes(data, "big") ^ int.from_bytes(mask_long, "big")).to_bytes(n, "big") if n else b""
        with self.lock:
            self.sock.sendall(bytes(hdr) + masked)

    def send(self, text):
        self._frame(0x1, text.encode("utf-8"))

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise OSError("Verbindung geschlossen")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def recv(self):
        b1, b2 = self._read(2)
        opcode, n = b1 & 0x0F, b2 & 0x7F
        if n == 126:
            n = struct.unpack(">H", self._read(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", self._read(8))[0]
        mask = self._read(4) if b2 & 0x80 else None
        payload = self._read(n)
        if mask:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        if opcode == 0x9:
            self._frame(0xA, payload)
        return opcode, payload

    def close(self):
        try:
            self._frame(0x8, b"")
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


_live = {}            # Port -> offene DevTools-Sitzungen (neueste zuletzt)
_live_lock = threading.Lock()


def insert_text(port, text):
    """Text in das ausgewählte Eingabefeld der Web-App schreiben (z. B. vom Handy)."""
    with _live_lock:
        conns = list(_live.get(port, []))
    for ws in reversed(conns):
        try:
            ws.send(json.dumps({"id": 99, "method": "Input.insertText", "params": {"text": text}}))
            return True
        except OSError:
            continue
    return False


def port_for(app_id, catalog_index):
    return BASE_PORT + (catalog_index % 500)


def _script(lang):
    with open(SCRIPT_PATH, encoding="utf-8") as fh:
        return f"window.__WTV_LANG={json.dumps(lang or 'de')};\n" + fh.read()


def _targets(port):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/json/list")
    with urllib.request.urlopen(req, timeout=3) as resp:  # noqa: S310 – feste lokale Adresse
        return json.load(resp)


def _session(port, ws_url, script, alive):
    """Hält die DevTools-Sitzung offen – das eingespielte Skript gilt nur, solange sie besteht."""
    try:
        ws = WebSocket(ws_url)
    except OSError as exc:
        log.warning("TV-Steuerung: DevTools-Verbindung fehlgeschlagen: %s", exc)
        return
    log.info("TV-Steuerung aktiv (Port %s)", port)
    with _live_lock:
        _live.setdefault(port, []).append(ws)
    try:
        ws.send(json.dumps({"id": 1, "method": "Page.enable"}))
        ws.send(json.dumps({"id": 2, "method": "Page.addScriptToEvaluateOnNewDocument", "params": {"source": script}}))
        ws.send(json.dumps({"id": 3, "method": "Runtime.evaluate", "params": {"expression": script}}))
        ws.sock.settimeout(5)
        while alive():
            try:
                opcode, _ = ws.recv()
            except socket.timeout:
                continue
            if opcode == 0x8:
                break
    except OSError:
        pass
    finally:
        with _live_lock:
            if ws in _live.get(port, []):
                _live[port].remove(ws)
        ws.close()


_watchers = {}


def status():
    """Für die Diagnose: welche Web-Apps werden überwacht, wie viele Seiten sind verbunden."""
    with _live_lock:
        live = {p: len(v) for p, v in _live.items()}
    return {str(p): {"watcher": t.is_alive(), "pages": live.get(p, 0)} for p, t in _watchers.items()}


def attach(port, lang, alive):
    """Überwacht die Chrome-Instanz einer Web-App und versorgt jede Seite mit der TV-Steuerung."""
    th = _watchers.get(port)
    if th and th.is_alive():
        return
    log.info("TV-Steuerung: verbinde mit Web-App (Port %s)", port)

    def loop():
        try:
            script = _script(lang)
        except OSError as exc:
            log.warning("TV-Steuerung nicht verfügbar: %s", exc)
            return
        sessions = {}
        started = time.time()
        warned = False
        while alive():
            try:
                targets = _targets(port)
            except (OSError, ValueError):
                targets = []
                if not warned and time.time() - started > 20:
                    warned = True
                    log.warning("TV-Steuerung: Chrome antwortet nicht auf Port %s (läuft die App schon ohne Steuerung?)", port)
            for t in targets:
                url = t.get("url", "")
                if t.get("type") not in ("page", "app") or not t.get("webSocketDebuggerUrl") or url.startswith(("chrome", "devtools")):
                    continue
                th = sessions.get(t["id"])
                if th and th.is_alive():
                    continue
                th = threading.Thread(target=_session, args=(port, t["webSocketDebuggerUrl"], script, alive),
                                      daemon=True, name=f"tvnav-{port}")
                th.start()
                sessions[t["id"]] = th
            time.sleep(2)
    th = threading.Thread(target=loop, daemon=True, name=f"tvnav-watch-{port}")
    _watchers[port] = th
    th.start()
