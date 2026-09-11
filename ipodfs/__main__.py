"""
Startpunkt: ``python -m ipodfs``

Startet den lokalen Server und oeffnet den Browser. Nichts wird ins Netz
gestellt - es hoert ausschliesslich 127.0.0.1 zu.
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
import webbrowser


def _free_port(preferred: int) -> int:
    for port in (preferred, 0):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return sock.getsockname()[1]
            except OSError:
                continue
    return preferred


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ipodfs",
        description="iPod touch per USB verwalten - ohne iTunes.",
    )
    parser.add_argument("--port", type=int, default=8731, help="Port (Standard: 8731)")
    parser.add_argument("--no-browser", action="store_true", help="Browser nicht automatisch oeffnen")
    parser.add_argument("--doctor", action="store_true", help="Nur die Verbindungsdiagnose ausgeben")
    args = parser.parse_args(argv)

    if args.doctor:
        from . import doctor

        report = doctor.run()
        symbols = {"ok": "OK  ", "warn": "!   ", "fail": "FEHL"}
        print(f"\niPodFS Diagnose - {report['platform']}\n")
        for check in report["checks"]:
            print(f"  [{symbols.get(check['status'], '?')}] {check['name']}: {check['detail']}")
            if check["fix"] and check["status"] != "ok":
                print(f"         -> {check['fix']}")
        print()
        return 0 if report["status"] != "fail" else 1

    from .server import TOKEN, app

    port = _free_port(args.port)
    url = f"http://127.0.0.1:{port}/"

    print("\n  iPodFS laeuft.")
    print(f"  Oberflaeche: {url}")
    print("  Beenden mit Strg+C\n")

    if not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(1.0), webbrowser.open(url)),
                         daemon=True).start()

    try:
        app.run(host="127.0.0.1", port=port, threaded=True, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        pass
    finally:
        from .devicelink import link

        link.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
