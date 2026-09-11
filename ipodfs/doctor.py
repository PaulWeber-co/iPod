"""
Selbstdiagnose: "Warum sehe ich meinen iPod nicht?"

Prueft die Kette Python -> pymobiledevice3 -> usbmux -> Geraet und sagt fuer
jeden Schritt, was konkret zu tun ist. Die Windows-Hinweise sind bewusst so
formuliert, dass *kein* iTunes installiert werden muss - es geht nur um den
USB-Treiber, der auch einzeln zu haben ist.
"""

from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import sys

WINDOWS_HELP = (
    "Windows braucht einmalig Apples USB-Treiber (Apple Mobile Device Support). "
    "Das ist NICHT iTunes und laeuft auch nicht im Hintergrund mit. "
    "Zwei Wege ohne iTunes: "
    "(1) 'Apple Devices' aus dem Microsoft Store installieren und danach nie wieder oeffnen, oder "
    "(2) iTunes64Setup.exe herunterladen, mit 7-Zip entpacken und nur "
    "AppleMobileDeviceSupport64.msi per Doppelklick installieren "
    "(siehe tools/windows_treiber.md)."
)
LINUX_HELP = "Unter Linux: 'sudo apt install usbmuxd libimobiledevice6' und den iPod neu anstecken."
MAC_HELP = "macOS bringt usbmux ab Werk mit - hier ist normalerweise nichts zu tun."


def _ok(name, detail="", fix=""):
    return {"name": name, "status": "ok", "detail": detail, "fix": fix}


def _warn(name, detail="", fix=""):
    return {"name": name, "status": "warn", "detail": detail, "fix": fix}


def _fail(name, detail="", fix=""):
    return {"name": name, "status": "fail", "detail": detail, "fix": fix}


def _platform_help() -> str:
    if sys.platform == "win32":
        return WINDOWS_HELP
    if sys.platform == "darwin":
        return MAC_HELP
    return LINUX_HELP


def _usbmux_reachable() -> tuple[bool, str]:
    """Laesst sich der usbmux-Dienst ueberhaupt ansprechen?"""
    address = os.environ.get("USBMUXD_SOCKET_ADDRESS")
    if address and ":" in address:
        host, _, port = address.rpartition(":")
        try:
            with socket.create_connection((host, int(port)), timeout=3):
                return True, f"TCP {address}"
        except OSError as exc:
            return False, f"TCP {address}: {exc}"

    if sys.platform == "win32":
        try:
            with socket.create_connection(("127.0.0.1", 27015), timeout=3):
                return True, "127.0.0.1:27015 (Apple Mobile Device Service)"
        except OSError as exc:
            return False, str(exc)

    path = address or "/var/run/usbmuxd"
    if not os.path.exists(path):
        return False, f"{path} existiert nicht"
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(3)
        sock.connect(path)
        sock.close()
        return True, path
    except OSError as exc:
        return False, f"{path}: {exc}"


def _windows_service_state() -> str:
    if sys.platform != "win32":
        return ""
    sc = shutil.which("sc") or r"C:\Windows\System32\sc.exe"
    try:
        output = subprocess.run(
            [sc, "query", "Apple Mobile Device Service"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except Exception:
        return ""
    if "RUNNING" in output:
        return "laeuft"
    if "STOPPED" in output:
        return "installiert, aber gestoppt"
    return "nicht installiert"


def run() -> dict:
    checks = []

    version = platform.python_version()
    if sys.version_info >= (3, 9):
        checks.append(_ok("Python", f"{version} auf {platform.system()} {platform.release()}"))
    else:
        checks.append(_fail("Python", f"{version} ist zu alt", "Python 3.9 oder neuer installieren."))

    try:
        import pymobiledevice3

        checks.append(_ok("pymobiledevice3", getattr(pymobiledevice3, "__version__", "installiert")))
    except ImportError:
        checks.append(_fail("pymobiledevice3", "fehlt", "pip install -r requirements.txt"))

    try:
        import mutagen

        checks.append(_ok("mutagen", mutagen.version_string))
    except ImportError:
        checks.append(_fail("mutagen", "fehlt", "pip install -r requirements.txt"))

    reachable, detail = _usbmux_reachable()
    if reachable:
        checks.append(_ok("usbmux-Dienst", detail))
    else:
        checks.append(_fail("usbmux-Dienst", detail, _platform_help()))

    state = _windows_service_state()
    if state:
        (checks.append(_ok("Apple Mobile Device Service", state))
         if state == "laeuft" else
         checks.append(_warn("Apple Mobile Device Service", state, WINDOWS_HELP)))

    devices = []
    if reachable:
        try:
            from .devicelink import link

            devices = link.list_devices()
            if devices:
                checks.append(_ok("Geraet am USB", ", ".join(d["udid"] for d in devices)))
            else:
                checks.append(_warn(
                    "Geraet am USB", "keins gefunden",
                    "iPod anstecken, Bildschirm entsperren, anderes Kabel oder anderen "
                    "USB-Port probieren (Ladekabel ohne Datenleitung sind haeufig die Ursache).",
                ))
        except Exception as exc:
            checks.append(_fail("Geraet am USB", str(exc), getattr(exc, "hint", "")))

    worst = "ok"
    for check in checks:
        if check["status"] == "fail":
            worst = "fail"
            break
        if check["status"] == "warn":
            worst = "warn"

    return {
        "status": worst,
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "checks": checks,
        "devices": devices,
        "help": _platform_help(),
    }
