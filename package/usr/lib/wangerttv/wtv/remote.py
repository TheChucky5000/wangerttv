"""Verbindung zum System-Dienst wtv-remote (HDMI-CEC + Fernbedienungs-Tasten)."""
import json
import socket
import time

from . import config
from .config import read_json

REMOTE_SOCKET = config.CEC_SOCKET.replace("cec.sock", "remote.sock")
REMOTE_STATUS = config.CEC_STATUS.replace("cec.json", "remote.json")

ACTIONS = [
    "home", "settings", "assistant", "standby", "power_menu", "livetv", "tvguide", "volume_up", "volume_down",
    "mute",
]


def send(cmd, timeout=3.0, **params):
    if config.DEV:
        if cmd == "learn":
            time.sleep(1.5)
            return {"ok": True, "key": "KEY_UNKNOWN", "scan": "c0221", "match": "scan:c0221",
                    "device": "waipu.tv RCU"}
        return {"ok": True}
    payload = json.dumps({"cmd": cmd, **params}) + "\n"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(REMOTE_SOCKET)
            sock.sendall(payload.encode())
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
        return json.loads(buf.decode() or "{}")
    except (OSError, ValueError) as exc:
        return {"ok": False, "message": str(exc)}


def status():
    if config.DEV:
        return {"running": True, "cec": {"adapter": False, "tv_power": "", "tv_vendor": ""},
                "devices": ["waipu.tv RCU", "AT Translated Set 2 keyboard"]}
    st = read_json(REMOTE_STATUS, {}) or {}
    st["running"] = bool(st) and time.time() - st.get("ts", 0) < 120
    return st
