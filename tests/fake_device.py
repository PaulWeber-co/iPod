"""
Ein iPod-Ersatz auf der Festplatte.

Bildet genau die Methoden von :class:`ipodfs.devicelink.DeviceLink` nach, die
der Sync-Motor benutzt. Damit laesst sich der komplette Ablauf - Manifest,
inkrementeller Abgleich, Verwaiste loeschen - ohne echtes Geraet testen.
"""

from __future__ import annotations

import os
import posixpath
import shutil
from pathlib import Path

from ipodfs.devicelink import DeviceError


class FakeLink:
    def __init__(self, root: Path, free_bytes: int = 8 * 1024**3):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.free_bytes = free_bytes
        self.uploads: list[str] = []

    # ------------------------------------------------------------- Umrechnung
    def _local(self, remote: str) -> Path:
        return self.root / remote.lstrip("/")

    # ---------------------------------------------------------------- API
    def info(self) -> dict:
        return {"connected": True, "free_bytes": self.free_bytes,
                "total_bytes": self.free_bytes * 2, "name": "FakePod"}

    def makedirs(self, root: str, path: str) -> None:
        self._local(path).mkdir(parents=True, exist_ok=True)

    def read_file(self, root: str, path: str) -> bytes:
        target = self._local(path)
        if not target.is_file():
            raise DeviceError("Datei oder Ordner existiert nicht.", kind="not_found")
        return target.read_bytes()

    def write_file(self, root: str, path: str, data: bytes) -> None:
        target = self._local(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def upload(self, root: str, local_path: str, remote_path: str, progress=None) -> int:
        target = self._local(remote_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        size = os.path.getsize(local_path)
        if size > self.free_bytes:
            raise DeviceError("Kein Speicherplatz mehr auf dem iPod.", kind="full")
        shutil.copyfile(local_path, target)
        self.free_bytes -= size
        self.uploads.append(remote_path)
        if progress:
            progress(size, size)
        return size

    def remove(self, root: str, path: str, recursive: bool = True) -> None:
        target = self._local(path)
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            self.free_bytes += target.stat().st_size
            target.unlink()
        else:
            raise DeviceError("Datei oder Ordner existiert nicht.", kind="not_found")

    def listdir(self, root: str, path: str = "/"):
        from ipodfs.devicelink import RemoteEntry

        base = self._local(path)
        out = []
        for child in sorted(base.iterdir()):
            out.append(RemoteEntry(
                name=child.name, path=posixpath.join(path, child.name),
                is_dir=child.is_dir(),
                size=child.stat().st_size if child.is_file() else 0,
                mtime=child.stat().st_mtime,
            ))
        return out

    def stat(self, root: str, path: str) -> dict:
        target = self._local(path)
        if not target.exists():
            raise DeviceError("Datei oder Ordner existiert nicht.", kind="not_found")
        return {"path": path, "is_dir": target.is_dir(),
                "size": target.stat().st_size if target.is_file() else 0,
                "mtime": target.stat().st_mtime}

    # ------------------------------------------------------------- Hilfsmittel
    def tree(self) -> list[str]:
        out = []
        for dirpath, _, filenames in os.walk(self.root):
            for name in filenames:
                full = Path(dirpath) / name
                out.append("/" + str(full.relative_to(self.root)).replace(os.sep, "/"))
        return sorted(out)
