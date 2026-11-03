"""Hilfsfunktionen: Prozesse, HTTP, Ereignis-Bus."""
import gzip
import json
import logging
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import unicodedata
import urllib.parse
import urllib.request

log = logging.getLogger("wtv")

USER_AGENT = "WangertTV/1.0 (+https://github.com/)"


def which(name):
    return shutil.which(name)


def run(cmd, timeout=20, input_text=None, env=None):
    """Führt einen Befehl aus. Liefert (returncode, stdout, stderr) – wirft nie."""
    try:
        proc = subprocess.run(
            cmd, input=input_text, capture_output=True, text=True,
            timeout=timeout, env=env, check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", f"{cmd[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"{cmd[0]}: timeout"
    except OSError as exc:
        return 126, "", str(exc)


def spawn(cmd, logfile=None, env=None, cwd=None):
    """Startet einen langlebigen Prozess in eigener Session."""
    out = subprocess.DEVNULL
    if logfile:
        os.makedirs(os.path.dirname(logfile), exist_ok=True)
        out = open(logfile, "ab", buffering=0)  # noqa: SIM115 – bleibt offen solange der Prozess läuft
    try:
        return subprocess.Popen(
            cmd, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            env=env, cwd=cwd, start_new_session=True,
        )
    finally:
        if out is not subprocess.DEVNULL:
            out.close()


def slugify(text, fallback="wangerttv", maxlen=40):
    norm = unicodedata.normalize("NFKD", text or "")
    norm = norm.replace("ß", "ss")
    ascii_text = norm.encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9-]+", "-", ascii_text).strip("-").lower()[:maxlen].strip("-")
    return slug or fallback


def http_get(url, timeout=15, max_bytes=60 * 1024 * 1024, headers=None):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Nur http(s)-Adressen sind erlaubt")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 – Schema oben geprüft
        data = resp.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError("Antwort zu groß")
        if resp.headers.get("Content-Encoding") == "gzip" or data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        return data


def http_get_json(url, timeout=10):
    return json.loads(http_get(url, timeout=timeout, max_bytes=5 * 1024 * 1024).decode("utf-8"))


def split_nmcli(line):
    """Zerlegt eine `nmcli -t`-Zeile (Doppelpunkt-getrennt, \\: maskiert)."""
    fields, cur, esc = [], [], False
    for ch in line:
        if esc:
            cur.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            fields.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    fields.append("".join(cur))
    return fields


class EventBus:
    """Einfacher Publish/Subscribe-Bus für Server-Sent-Events an die Oberfläche."""

    def __init__(self):
        self._subs = []
        self._lock = threading.Lock()

    def subscribe(self):
        q = queue.Queue(maxsize=200)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def emit(self, kind, **data):
        msg = {**data, "type": kind, "ts": time.time()}   # Ereignistyp kann nicht überschrieben werden
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(msg)
            except queue.Full:
                pass


bus = EventBus()


class Jobs:
    """Hintergrund-Aufgaben (z. B. App-Installationen) mit Status für die Oberfläche."""

    def __init__(self):
        self._jobs = {}
        self._lock = threading.Lock()

    def start(self, job_id, label, func, *args):
        with self._lock:
            job = self._jobs.get(job_id)
            if job and job["state"] == "running":
                return False
            self._jobs[job_id] = {"id": job_id, "label": label, "state": "running", "message": "", "ts": time.time()}

        def runner():
            try:
                result = func(*args)
                self.update(job_id, state="done", message=result or "")
            except Exception as exc:  # noqa: BLE001 – Fehler wird an die Oberfläche gemeldet
                log.exception("Job %s fehlgeschlagen", job_id)
                self.update(job_id, state="error", message=str(exc))
        threading.Thread(target=runner, daemon=True, name=f"job-{job_id}").start()
        return True

    def update(self, job_id, **fields):
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].update(fields)
                job = dict(self._jobs[job_id])
            else:
                return
        bus.emit("job", job=job)

    def list(self):
        with self._lock:
            return [dict(j) for j in self._jobs.values()]


jobs = Jobs()
