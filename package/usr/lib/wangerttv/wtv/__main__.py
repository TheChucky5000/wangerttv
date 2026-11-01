"""Startpunkt: python3 -m wtv"""
import logging
import os
import signal
import socket
import sys
import threading
import time

from . import config


def main():
    config.ensure_dirs()
    logging.basicConfig(
        level=logging.DEBUG if os.environ.get("WTV_DEBUG") else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    from .core import Core
    from .server import serve

    core = Core()
    signal.signal(signal.SIGTERM, lambda *_: (core.apps.stop(), sys.exit(0)))
    # Erst den Server starten, damit der Start-Bildschirm sofort Daten bekommt
    threading.Thread(target=serve, args=(core,), daemon=True, name="http").start()
    for _ in range(50):
        try:
            socket.create_connection((config.HOST, config.PORT), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    core.start()
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
