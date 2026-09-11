"""
Direkter USB-Zugriff auf iPod touch / iPhone - ohne iTunes, ohne CopyTrans.

Der Weg zum Geraet ist derselbe, den Apples eigene Software geht, nur eben
selbst gesprochen:

    USB -> usbmux -> lockdownd (Port 62078) -> AFC

* **usbmux** multiplext TCP-Verbindungen ueber das USB-Kabel.
  macOS bringt den Dienst ab Werk mit, Linux ueber das Paket ``usbmuxd``,
  unter Windows liefert ihn der Apple-Mobile-Device-Treiber.
* **lockdownd** ist der Dienst-Broker auf dem iPod. Beim ersten Kontakt wird
  ein Schluesselpaar ausgetauscht (Pairing) - ab iOS 7 mit "Vertrauen?"-Dialog,
  auf iOS 6 laeuft das still durch.
* **AFC** (Apple File Conduit) ist das Dateiprotokoll. ``com.apple.afc`` gibt
  die Medien-Partition ``/var/mobile/Media`` frei, ``com.apple.afc2`` (nur mit
  Jailbreak) das komplette Wurzelverzeichnis, und ``house_arrest`` die Sandbox
  einer einzelnen App.

pymobiledevice3 ist vollstaendig asynchron. Damit Flask synchron bleiben kann,
laeuft hier genau ein Event-Loop in einem Hintergrund-Thread; alle Aufrufe
werden darauf marshallt. Das serialisiert die Geraete-I/O ganz nebenbei - was
ohnehin noetig ist, weil eine AFC-Verbindung keine parallelen Nutzer mag.
"""

from __future__ import annotations

import asyncio
import os
import posixpath
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

MEDIA_ROOT = "media"
JAILBREAK_ROOT = "root"
APP_PREFIX = "app:"

#: Player-Apps, die Musik per Dateifreigabe entgegennehmen. Reihenfolge =
#: Empfehlung. Alle spielen MP3 und gruppieren nach Album-Interpret.
KNOWN_PLAYERS = {
    "org.videolan.vlc-ios": "VLC for Mobile",
    "org.videolan.vlc": "VLC",
    "com.foobar2000.foobar2000": "foobar2000",
    "com.jetheaddev.flacplayer": "FLAC Player",
    "com.evgeniycollab.cesium": "Cesium Music Player",
    "com.golden-ear.ios.gplayer": "GoldenEar / GPlayer",
    "de.k-nut.oPlayer": "oPlayer",
    "com.olimsoft.oplayer.lite": "OPlayer Lite",
}

CHUNK = 256 * 1024


class DeviceError(RuntimeError):
    """Alles, was beim Geraetezugriff schiefgehen kann - mit Klartext."""

    def __init__(self, message: str, hint: str = "", kind: str = "error"):
        super().__init__(message)
        self.hint = hint
        self.kind = kind

    def to_dict(self) -> dict:
        return {"error": str(self), "hint": self.hint, "kind": self.kind}


@dataclass
class RemoteEntry:
    name: str
    path: str
    is_dir: bool
    size: int = 0
    mtime: float = 0.0
    is_link: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name, "path": self.path, "is_dir": self.is_dir,
            "size": self.size, "mtime": self.mtime, "is_link": self.is_link,
        }


class _Loop:
    """Ein dauerhafter Event-Loop in einem eigenen Thread."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, name="ipodfs-device", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run(self, coro, timeout: Optional[float] = 300.0):
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return future.result(timeout)


def _translate(exc: BaseException) -> DeviceError:
    """pymobiledevice3-Ausnahmen in verstaendliche Meldungen uebersetzen."""
    name = type(exc).__name__
    text = str(exc) or name

    if name in ("NoDeviceConnectedError", "DeviceNotFoundError", "ConnectionFailedError"):
        return DeviceError(
            "Kein Geraet gefunden.",
            "iPod per USB anstecken, entsperren und - falls gefragt - "
            "'Diesem Computer vertrauen' bestaetigen.",
            kind="no_device",
        )
    if name in ("ConnectionFailedToUsbmuxdError", "MuxException", "MuxVersionError") \
            or "usbmux" in text.lower() or "usbmux" in name.lower():
        return DeviceError(
            "Der usbmux-Dienst ist nicht erreichbar.",
            "macOS bringt ihn mit. Unter Linux: 'sudo apt install usbmuxd'. "
            "Unter Windows fehlt der Apple-Mobile-Device-Treiber - siehe README.",
            kind="no_usbmux",
        )
    if name == "PasswordRequiredError":
        return DeviceError("Der iPod ist gesperrt.", "Code eingeben und erneut verbinden.", kind="locked")
    if name in ("UserDeniedPairingError", "PairingDialogResponsePendingError", "InvalidHostIDError"):
        return DeviceError(
            "Das Pairing wurde nicht bestaetigt.",
            "Auf dem iPod 'Vertrauen' antippen und noch einmal verbinden.",
            kind="pairing",
        )
    if name == "AppNotInstalledError":
        return DeviceError("Diese App ist nicht installiert.", kind="no_app")
    if name == "AfcException" or "AfcError" in text:
        if "OBJECT_NOT_FOUND" in text:
            return DeviceError("Datei oder Ordner existiert nicht.", kind="not_found")
        if "PERM_DENIED" in text:
            return DeviceError(
                "Zugriff verweigert.",
                "Ausserhalb von /var/mobile/Media geht es nur mit Jailbreak (afc2).",
                kind="denied",
            )
        if "OBJECT_EXISTS" in text:
            return DeviceError("Existiert bereits.", kind="exists")
        if "NO_SPACE" in text.upper() or "NOSPACE" in text.upper():
            return DeviceError("Kein Speicherplatz mehr auf dem iPod.", kind="full")
    if name == "StartServiceError":
        return DeviceError(f"Dienst konnte nicht gestartet werden: {text}", kind="service")
    return DeviceError(text, kind=name)


class DeviceLink:
    """Synchrone Fassade auf das asynchrone pymobiledevice3."""

    def __init__(self) -> None:
        self._loop = _Loop()
        self._lock = threading.RLock()
        self._lockdown = None
        self._afc: dict[str, Any] = {}
        self._udid: Optional[str] = None
        self._values: dict = {}
        self._afc2_available: Optional[bool] = None
        self._last_error: Optional[DeviceError] = None

    # ------------------------------------------------------------- Verbindung
    def list_devices(self) -> list[dict]:
        from pymobiledevice3.usbmux import list_devices as mux_list

        async def _go():
            return await mux_list()

        try:
            devices = self._loop.run(_go(), timeout=20)
        except Exception as exc:
            raise _translate(exc) from exc
        return [{"udid": d.serial, "connection": d.connection_type} for d in devices]

    def connect(self, udid: Optional[str] = None) -> dict:
        """Mit dem Geraet verbinden (Pairing passiert bei Bedarf automatisch)."""
        with self._lock:
            self._teardown()
            try:
                self._lockdown = self._loop.run(self._connect(udid), timeout=90)
                self._values = self._lockdown.all_values or {}
                self._udid = self._values.get("UniqueDeviceID") or udid
                self._afc2_available = None
                self._last_error = None
            except Exception as exc:
                self._lockdown = None
                error = _translate(exc)
                self._last_error = error
                raise error from exc
        return self.info()

    async def _connect(self, udid: Optional[str]):
        from pymobiledevice3.lockdown import create_using_usbmux

        return await create_using_usbmux(serial=udid, autopair=True, pair_timeout=60)

    @property
    def connected(self) -> bool:
        return self._lockdown is not None

    def disconnect(self) -> None:
        with self._lock:
            self._teardown()

    def _teardown(self) -> None:
        for service in list(self._afc.values()):
            try:
                self._loop.run(service.close(), timeout=10)
            except Exception:
                pass
        self._afc.clear()
        if self._lockdown is not None:
            try:
                self._loop.run(self._lockdown.close(), timeout=10)
            except Exception:
                pass
        self._lockdown = None
        self._values = {}
        self._udid = None

    def _require(self):
        if self._lockdown is None:
            raise DeviceError(
                "Nicht verbunden.",
                "Erst auf 'Verbinden' klicken.",
                kind="not_connected",
            )
        return self._lockdown

    # ------------------------------------------------------------------- Infos
    def info(self) -> dict:
        if self._lockdown is None:
            return {"connected": False, "error": self._last_error.to_dict() if self._last_error else None}

        values = self._values or {}
        data = {
            "connected": True,
            "udid": self._udid,
            "name": values.get("DeviceName") or "iPod",
            "product": values.get("ProductType") or "",
            "model": _pretty_model(values.get("ProductType", "")),
            "ios": values.get("ProductVersion") or "",
            "build": values.get("BuildVersion") or "",
            "serial": values.get("SerialNumber") or "",
            "class": values.get("DeviceClass") or "",
            "color": values.get("DeviceColor") or "",
        }
        try:
            devinfo = self._afc_call(MEDIA_ROOT, lambda afc: afc.get_device_info())
            total = int(devinfo.get("FSTotalBytes", 0))
            free = int(devinfo.get("FSFreeBytes", 0))
            data.update({"total_bytes": total, "free_bytes": free, "used_bytes": max(0, total - free)})
        except Exception:
            data.update({"total_bytes": 0, "free_bytes": 0, "used_bytes": 0})

        data["jailbroken"] = self.has_afc2()
        return data

    def has_afc2(self) -> bool:
        """``com.apple.afc2`` existiert nur auf gejailbreakten Geraeten."""
        if self._afc2_available is None:
            if self._lockdown is None:
                return False
            try:
                self._get_afc(JAILBREAK_ROOT)
                self._afc2_available = True
            except Exception:
                self._afc2_available = False
        return bool(self._afc2_available)

    def apps(self) -> list[dict]:
        """Installierte Apps - mit Markierung, welche Dateifreigabe koennen."""
        lockdown = self._require()

        async def _go():
            from pymobiledevice3.services.installation_proxy import InstallationProxyService

            async with InstallationProxyService(lockdown) as proxy:
                return await proxy.get_apps(application_type="User")

        try:
            apps = self._loop.run(_go(), timeout=120)
        except Exception as exc:
            raise _translate(exc) from exc

        out = []
        for bundle_id, meta in (apps or {}).items():
            shares_files = bool(meta.get("UIFileSharingEnabled") or meta.get("LSSupportsOpeningDocumentsInPlace"))
            out.append({
                "bundle_id": bundle_id,
                "name": meta.get("CFBundleDisplayName") or meta.get("CFBundleName") or bundle_id,
                "version": str(meta.get("CFBundleShortVersionString") or meta.get("CFBundleVersion") or ""),
                "file_sharing": shares_files,
                "known_player": bundle_id in KNOWN_PLAYERS,
            })
        out.sort(key=lambda a: (not a["known_player"], not a["file_sharing"], a["name"].casefold()))
        return out

    def install_app(self, ipa_path: str, progress: Optional[Callable[[int, int], None]] = None) -> dict:
        """
        Eine .ipa ueber USB installieren - der Weg, auf dem auch iTunes Apps
        aufspielt (``com.apple.mobile.installation_proxy``).

        Auf einem Geraet ohne Jailbreak muss die Datei gueltig signiert sein
        (App-Store-Kauf oder eigenes Entwicklerzertifikat); mit Jailbreak und
        AppSync laeuft jede .ipa durch.
        """
        lockdown = self._require()
        from pathlib import Path as _Path

        async def _go():
            from pymobiledevice3.services.installation_proxy import InstallationProxyService

            def on_status(status):
                if not progress:
                    return
                percent = status.get("PercentComplete") if isinstance(status, dict) else None
                if percent is not None:
                    progress(int(percent), 100)

            async with InstallationProxyService(lockdown) as proxy:
                await proxy.install_from_local(_Path(ipa_path), handler=on_status)

        try:
            self._loop.run(_go(), timeout=1800)
        except Exception as exc:
            raise _translate(exc) from exc
        # Sandbox-Verbindungen verwerfen: die App-Liste hat sich geaendert.
        for key in [k for k in self._afc if k.startswith(APP_PREFIX)]:
            service = self._afc.pop(key)
            try:
                self._loop.run(service.close(), timeout=5)
            except Exception:
                pass
        return {"installed": os.path.basename(ipa_path)}

    # -------------------------------------------------------------- AFC-Wurzel
    def _get_afc(self, root: str):
        """AFC-Dienst fuer eine Wurzel holen (und offen halten)."""
        service = self._afc.get(root)
        if service is not None:
            return service
        lockdown = self._require()

        async def _open():
            if root.startswith(APP_PREFIX):
                from pymobiledevice3.services.house_arrest import HouseArrestService

                return await HouseArrestService.create(lockdown, root[len(APP_PREFIX):])
            from pymobiledevice3.services.afc import AfcService

            name = "com.apple.afc2" if root == JAILBREAK_ROOT else "com.apple.afc"
            svc = AfcService(lockdown, service_name=name)
            await svc.connect()
            return svc

        try:
            service = self._loop.run(_open(), timeout=60)
        except Exception as exc:
            raise _translate(exc) from exc
        self._afc[root] = service
        return service

    def _afc_call(self, root: str, fn: Callable[[Any], Any], timeout: float = 300.0, retry: bool = True):
        """
        Eine AFC-Operation ausfuehren.

        Bricht die Verbindung weg (Kabel raus, Geraet gesperrt, Dienst neu
        gestartet), wird der AFC-Dienst einmal neu aufgebaut und der Aufruf
        wiederholt - das erspart dem Nutzer das manuelle Neuverbinden.
        """
        with self._lock:
            service = self._get_afc(root)
            try:
                return self._loop.run(fn(service), timeout=timeout)
            except Exception as exc:
                if not retry or isinstance(exc, DeviceError):
                    raise _translate(exc) from exc
                if not _is_connection_loss(exc):
                    raise _translate(exc) from exc
                self._afc.pop(root, None)
                try:
                    self._loop.run(service.close(), timeout=5)
                except Exception:
                    pass
                service = self._get_afc(root)
                try:
                    return self._loop.run(fn(service), timeout=timeout)
                except Exception as exc2:
                    raise _translate(exc2) from exc2

    # -------------------------------------------------------- Dateioperationen
    def listdir(self, root: str, path: str = "/") -> list[RemoteEntry]:
        path = _norm(path)

        async def _go(afc):
            names = await afc.listdir(path)
            entries = []
            for name in names:
                full = posixpath.join(path, name)
                try:
                    info = await afc.stat(full)
                except Exception:
                    entries.append(RemoteEntry(name=name, path=full, is_dir=False))
                    continue
                link_type = str(info.get("st_ifmt", ""))
                entries.append(RemoteEntry(
                    name=name,
                    path=full,
                    is_dir=link_type == "S_IFDIR",
                    is_link=link_type == "S_IFLNK",
                    size=int(info.get("st_size", 0) or 0),
                    mtime=_as_epoch(info.get("st_mtime")),
                ))
            return entries

        entries = self._afc_call(root, _go)
        entries.sort(key=lambda e: (not e.is_dir, e.name.casefold()))
        return entries

    def stat(self, root: str, path: str) -> dict:
        path = _norm(path)

        async def _go(afc):
            return await afc.stat(path)

        info = self._afc_call(root, _go)
        return {
            "path": path,
            "is_dir": str(info.get("st_ifmt", "")) == "S_IFDIR",
            "size": int(info.get("st_size", 0) or 0),
            "mtime": _as_epoch(info.get("st_mtime")),
        }

    def exists(self, root: str, path: str) -> bool:
        try:
            self.stat(root, path)
            return True
        except DeviceError:
            return False

    def makedirs(self, root: str, path: str) -> None:
        path = _norm(path)
        if path in ("/", ""):
            return

        async def _go(afc):
            return await afc.makedirs(path)

        self._afc_call(root, _go)

    def remove(self, root: str, path: str, recursive: bool = True) -> None:
        path = _norm(path)

        async def _go(afc):
            if recursive:
                return await afc.rm(path, force=True)
            return await afc.rm_single(path, force=True)

        self._afc_call(root, _go)

    def rename(self, root: str, source: str, target: str) -> None:
        async def _go(afc):
            return await afc.rename(_norm(source), _norm(target))

        self._afc_call(root, _go)

    def read_file(self, root: str, path: str) -> bytes:
        async def _go(afc):
            return await afc.get_file_contents(_norm(path))

        return self._afc_call(root, _go)

    def write_file(self, root: str, path: str, data: bytes) -> None:
        path = _norm(path)
        parent = posixpath.dirname(path)
        if parent not in ("", "/"):
            self.makedirs(root, parent)

        async def _go(afc):
            return await afc.set_file_contents(path, data)

        self._afc_call(root, _go)

    def upload(
        self,
        root: str,
        local_path: str,
        remote_path: str,
        progress: Optional[Callable[[int, int], None]] = None,
    ) -> int:
        """Datei hochladen - in Haeppchen, damit der Fortschritt sichtbar bleibt."""
        remote_path = _norm(remote_path)
        parent = posixpath.dirname(remote_path)
        if parent not in ("", "/"):
            self.makedirs(root, parent)

        total = os.path.getsize(local_path)

        async def _go(afc):
            handle = await afc.fopen(remote_path, "w")
            sent = 0
            try:
                with open(local_path, "rb") as source:
                    while True:
                        chunk = source.read(CHUNK)
                        if not chunk:
                            break
                        await afc.fwrite(handle, chunk)
                        sent += len(chunk)
                        if progress:
                            progress(sent, total)
            finally:
                await afc.fclose(handle)
            return sent

        return self._afc_call(root, _go, timeout=3600)

    def download(
        self,
        root: str,
        remote_path: str,
        local_path: str,
        progress: Optional[Callable[[int, int], None]] = None,
    ) -> int:
        remote_path = _norm(remote_path)
        total = self.stat(root, remote_path)["size"]
        os.makedirs(os.path.dirname(os.path.abspath(local_path)), exist_ok=True)

        async def _go(afc):
            handle = await afc.fopen(remote_path, "r")
            got = 0
            try:
                with open(local_path, "wb") as target:
                    while True:
                        chunk = await afc.fread(handle, CHUNK)
                        if not chunk:
                            break
                        target.write(chunk)
                        got += len(chunk)
                        if progress:
                            progress(got, total)
            finally:
                await afc.fclose(handle)
            return got

        return self._afc_call(root, _go, timeout=3600)

    def walk_files(self, root: str, path: str = "/") -> list[RemoteEntry]:
        """Rekursiv alle Dateien unter ``path`` einsammeln."""
        found: list[RemoteEntry] = []
        stack = [_norm(path)]
        while stack:
            current = stack.pop()
            try:
                entries = self.listdir(root, current)
            except DeviceError:
                continue
            for entry in entries:
                if entry.is_dir and not entry.is_link:
                    stack.append(entry.path)
                elif not entry.is_dir:
                    found.append(entry)
        return found


# ------------------------------------------------------------------- Helfer
def _norm(path: str) -> str:
    path = (path or "/").replace("\\", "/")
    if not path.startswith("/"):
        path = "/" + path
    path = posixpath.normpath(path)
    return "/" if path == "." else path


def _as_epoch(value: Any) -> float:
    if value is None:
        return 0.0
    if hasattr(value, "timestamp"):
        try:
            return float(value.timestamp())
        except Exception:
            return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    # AFC liefert Nanosekunden seit Epoche.
    return number / 1e9 if number > 1e12 else number


def _is_connection_loss(exc: BaseException) -> bool:
    name = type(exc).__name__
    if name in ("ConnectionAbortedError", "ConnectionResetError", "BrokenPipeError",
                "ConnectionTerminatedError", "ConnectionFailedError", "TimeoutError"):
        return True
    text = str(exc).lower()
    return any(token in text for token in ("broken pipe", "not connected", "connection reset",
                                           "connection aborted", "closed"))


_MODELS = {
    "iPod1,1": "iPod touch (1. Gen.)", "iPod2,1": "iPod touch (2. Gen.)",
    "iPod3,1": "iPod touch (3. Gen.)", "iPod4,1": "iPod touch (4. Gen.)",
    "iPod5,1": "iPod touch (5. Gen.)", "iPod7,1": "iPod touch (6. Gen.)",
    "iPod9,1": "iPod touch (7. Gen.)",
}


def _pretty_model(product_type: str) -> str:
    if product_type in _MODELS:
        return _MODELS[product_type]
    if product_type.startswith("iPhone"):
        return f"iPhone ({product_type})"
    if product_type.startswith("iPad"):
        return f"iPad ({product_type})"
    return product_type or "Unbekannt"


#: Prozessweite Instanz - eine Verbindung reicht, mehr vertraegt AFC nicht.
link = DeviceLink()
