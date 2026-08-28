"""Production entrypoint: optional Tor bootstrap, then gunicorn-less Flask.

Flask's threaded server is enough here because the heavy lifting happens in the
Playwright event-loop thread; keeping one process means one browser pool.
"""
import os
import subprocess
import threading
import time

from core import config


def boot_tor():
    if not config.USE_TOR:
        return
    os.makedirs("/tmp/tor-data", exist_ok=True)
    with open("/tmp/torrc", "w") as fh:
        for i in range(10):
            fh.write(f"SocksPort {config.TOR_BASE_PORT + i} SessionGroup={i}\n")
        fh.write("ControlPort 9060\nCookieAuthentication 1\n")
        fh.write("CookieAuthFile /tmp/tor-data/control_auth_cookie\n")
        fh.write("DataDirectory /tmp/tor-data\nRunAsDaemon 0\n")
    log = open("/tmp/tor.log", "w")
    subprocess.Popen(["tor", "-f", "/tmp/torrc"], stdout=log, stderr=log)
    print("[TOR] started", flush=True)
    for _ in range(120):
        try:
            if "Bootstrapped 100%" in open("/tmp/tor.log").read():
                print("[TOR] bootstrapped", flush=True)
                return
        except FileNotFoundError:
            pass
        time.sleep(1)


if __name__ == "__main__":
    threading.Thread(target=boot_tor, daemon=True).start()
    from app import app
    app.run(host="0.0.0.0", port=config.PORT, threaded=True, use_reloader=False)
