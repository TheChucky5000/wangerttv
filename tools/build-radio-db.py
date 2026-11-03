#!/usr/bin/env python3
"""Baut die mitgelieferte Radiosender-Datenbank neu: python3 tools/build-radio-db.py [rohdaten.json]

Ohne Argument werden alle deutschen Sender von radio-browser.info geladen (seitenweise, mit Wiederholungen)."""
import gzip
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "package/usr/lib/wangerttv"))
from wtv import radiodb  # noqa: E402


def fetch():
    out, off = [], 0
    while True:
        for _ in range(6):
            r = subprocess.run(["curl", "-sS", "-m", "90", "-A", "WangertTV/1.0",
                                "https://all.api.radio-browser.info/json/stations/search?countrycode=DE&hidebroken=true"
                                f"&order=clickcount&reverse=true&limit=1000&offset={off}"], capture_output=True)
            if r.returncode == 0 and r.stdout.startswith(b"["):
                break
            time.sleep(3)
        else:
            sys.exit(f"Abbruch bei Seite {off}")
        page = json.loads(r.stdout)
        out += page
        if len(page) < 1000:
            return out
        off += 1000


raw = json.load(open(sys.argv[1])) if len(sys.argv) > 1 else fetch()
stations = radiodb.build(raw)
target = os.path.join(ROOT, "package/usr/lib/wangerttv/radio/stations-de.json.gz")
with gzip.open(target, "wt", encoding="utf-8") as fh:
    json.dump({"ts": int(time.time()), "country": "DE", "stations": stations}, fh, ensure_ascii=False, separators=(",", ":"))
with_state = sum(1 for s in stations if s["state"])
print(f"{len(raw)} Einträge -> {len(stations)} Sender ({with_state} mit Bundesland), {os.path.getsize(target) // 1024} KB")
