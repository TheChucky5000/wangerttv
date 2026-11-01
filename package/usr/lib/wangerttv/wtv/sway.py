"""Steuerung des Sway-Compositors: Arbeitsflächen pro App, Fokus, Ereignisse."""
import json
import subprocess
import threading
import time

from . import config
from .util import log, run

HOME_WS = "home"


class Sway:
    def __init__(self):
        self.focused = HOME_WS
        self._listeners = []
        self._lock = threading.Lock()

    def on_focus(self, callback):
        self._listeners.append(callback)

    def cmd(self, command):
        if config.DEV:
            log.debug("DEV swaymsg %s", command)
            return True
        rc, out, err = run(["swaymsg", command], timeout=10)
        if rc != 0:
            log.warning("swaymsg %r fehlgeschlagen: %s", command, (err or out).strip())
        return rc == 0

    def go(self, workspace):
        safe = workspace.replace('"', "")
        ok = self.cmd(f'workspace "{safe}"')
        if config.DEV:
            self._set_focused(workspace)
        return ok

    def go_home(self):
        return self.go(HOME_WS)

    def _set_focused(self, name):
        with self._lock:
            changed = name != self.focused
            self.focused = name
        if changed:
            for cb in self._listeners:
                try:
                    cb(name)
                except Exception:  # noqa: BLE001
                    log.exception("Fokus-Listener fehlgeschlagen")

    def workspace_has_windows(self, name):
        if config.DEV:
            return True
        rc, out, _ = run(["swaymsg", "-t", "get_tree", "-r"], timeout=10)
        if rc != 0:
            return True
        try:
            tree = json.loads(out)
        except ValueError:
            return True

        def find(node):
            if node.get("type") == "workspace" and node.get("name") == name:
                return node
            for child in node.get("nodes", []) + node.get("floating_nodes", []):
                hit = find(child)
                if hit:
                    return hit
            return None
        ws = find(tree)
        if not ws:
            return False
        return bool(ws.get("nodes") or ws.get("floating_nodes"))

    def _check_empty(self):
        name = self.focused
        if name != HOME_WS and not self.workspace_has_windows(name):
            log.info("Arbeitsfläche %s ist leer – zurück zum Startbildschirm", name)
            self.go_home()

    def watch(self):
        """Hört auf Sway-Ereignisse (Fensterwechsel) und kehrt bei leeren Flächen heim."""
        if config.DEV:
            return
        threading.Thread(target=self._watch_loop, daemon=True, name="sway-watch").start()

    def _watch_loop(self):
        decoder = json.JSONDecoder()
        while True:
            try:
                proc = subprocess.Popen(
                    ["swaymsg", "-t", "subscribe", "-m", '["window","workspace"]'],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                )
                buf = ""
                for chunk in iter(lambda: proc.stdout.read(1), ""):
                    buf += chunk
                    if chunk not in "}\n":
                        continue
                    stripped = buf.lstrip()
                    try:
                        event, end = decoder.raw_decode(stripped)
                    except ValueError:
                        continue
                    buf = stripped[end:]
                    self._handle(event)
            except Exception:  # noqa: BLE001
                log.exception("Sway-Ereignisse unterbrochen")
            time.sleep(2)

    def _handle(self, event):
        change = event.get("change")
        if "current" in event and change in ("focus", "init"):
            cur = event.get("current") or {}
            if cur.get("name"):
                self._set_focused(cur["name"])
        elif "container" in event and change in ("close", "move"):
            # kurz warten, damit Sway den Baum aktualisiert hat
            threading.Timer(0.4, self._check_empty).start()


sway = Sway()
