"""
Lokale Musiksammlung: einlesen, analysieren, reparieren, zurueckrollen.

Jede Reparatur legt vorher ein vollstaendiges Backup der alten Tag-Werte an
(inklusive Cover), sodass sich ein Lauf jederzeit rueckgaengig machen laesst.
"""

from __future__ import annotations

import base64
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

from . import tags as tagmod
from .normalize import AlbumPlan, FixOptions, plan_library
from .tags import AUDIO_EXTENSIONS, Track

ProgressFn = Optional[Callable[[int, int, str], None]]

CONFIG_DIR = Path(os.environ.get("IPODFS_HOME", Path.home() / ".ipodfs"))
BACKUP_DIR = CONFIG_DIR / "backups"
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".Trash", "$RECYCLE.BIN",
             "System Volume Information", ".Spotlight-V100", ".fseventsd"}


def _ensure_dirs() -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)


# ------------------------------------------------------------------ scannen
def iter_audio_files(root: str) -> Iterable[str]:
    root_path = Path(root)
    if root_path.is_file():
        if root_path.suffix.lower() in AUDIO_EXTENSIONS:
            yield str(root_path)
        return
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("._"))
        for filename in sorted(filenames):
            if filename.startswith("._"):
                continue
            if Path(filename).suffix.lower() in AUDIO_EXTENSIONS:
                yield os.path.join(dirpath, filename)


def scan(roots: Iterable[str], progress: ProgressFn = None) -> list[Track]:
    """Alle Audiodateien unter den angegebenen Ordnern einlesen."""
    files: list[str] = []
    seen: set[str] = set()
    for root in roots:
        for path in iter_audio_files(root):
            real = os.path.normcase(os.path.abspath(path))
            if real not in seen:
                seen.add(real)
                files.append(path)

    total = len(files)
    tracks: list[Track] = []
    for index, path in enumerate(files, 1):
        tracks.append(tagmod.read_track(path))
        if progress and (index % 25 == 0 or index == total):
            progress(index, total, os.path.basename(path))
    return tracks


# ---------------------------------------------------------------- reparieren
@dataclass
class FixResult:
    backup_id: str
    files_changed: int = 0
    fields_changed: int = 0
    artwork_written: int = 0
    errors: list[str] = None

    def to_dict(self) -> dict:
        return {
            "backup_id": self.backup_id,
            "files_changed": self.files_changed,
            "fields_changed": self.fields_changed,
            "artwork_written": self.artwork_written,
            "errors": self.errors or [],
        }


def apply_plans(
    plans: list[AlbumPlan],
    opts: Optional[FixOptions] = None,
    progress: ProgressFn = None,
) -> FixResult:
    """
    Die Reparaturvorschlaege schreiben.

    Vor der ersten Aenderung an einer Datei werden ihre alten Werte im Backup
    festgehalten - erst danach wird geschrieben.
    """
    _ensure_dirs()
    opts = opts or FixOptions()
    backup_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    entries: list[dict] = []
    result = FixResult(backup_id=backup_id, errors=[])

    artwork_cache: dict[str, tuple[bytes, str]] = {}
    pending = [(plan, change) for plan in plans for change in plan.changes if not change.empty]
    total = len(pending)

    for index, (plan, change) in enumerate(pending, 1):
        try:
            track = next((t for t in plan.tracks if t.path == change.path), None)
            if track is None:
                continue

            artwork = None
            if change.set_artwork and change.artwork_source:
                if change.artwork_source not in artwork_cache:
                    data, mime = tagmod.read_artwork(change.artwork_source)
                    if data:
                        artwork_cache[change.artwork_source] = (data, mime)
                artwork = artwork_cache.get(change.artwork_source)

            old_art, old_mime = (None, "")
            if artwork is not None:
                old_art, old_mime = tagmod.read_artwork(change.path)

            entries.append({
                "path": change.path,
                "fields": {k: v[0] for k, v in change.fields.items()},
                "artwork": base64.b64encode(old_art).decode() if old_art else None,
                "artwork_mime": old_mime,
                "artwork_replaced": artwork is not None,
            })

            new_values = {k: v[1] for k, v in change.fields.items()}
            tagmod.write_tags(change.path, new_values, artwork)

            result.files_changed += 1
            result.fields_changed += len(new_values)
            if artwork is not None:
                result.artwork_written += 1

            # Datei neu einlesen statt die Werte nur zu raten: durch das
            # Schreiben aendern sich auch Groesse, mtime und Cover-Pruefsumme -
            # und genau die braucht der Sync-Abgleich spaeter.
            _refresh(track)
        except Exception as exc:
            result.errors.append(f"{os.path.basename(change.path)}: {exc}")

        if progress and (index % 5 == 0 or index == total):
            progress(index, total, os.path.basename(change.path))

    if entries:
        backup_path = BACKUP_DIR / f"{backup_id}.json"
        backup_path.write_text(
            json.dumps({
                "id": backup_id,
                "created": time.time(),
                "entries": entries,
            }, ensure_ascii=False),
            encoding="utf-8",
        )
    return result


def _refresh(track: Track) -> None:
    """Ein Track-Objekt an den Stand auf der Platte angleichen."""
    fresh = tagmod.read_track(track.path)
    if fresh.error:
        return
    for name in track.__dataclass_fields__:
        if name != "path":
            setattr(track, name, getattr(fresh, name))


def list_backups() -> list[dict]:
    _ensure_dirs()
    out = []
    for path in sorted(BACKUP_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.append({
            "id": data.get("id", path.stem),
            "created": data.get("created", path.stat().st_mtime),
            "files": len(data.get("entries", [])),
        })
    return out


def undo(backup_id: str, progress: ProgressFn = None) -> dict:
    """Einen Reparaturlauf vollstaendig zuruecknehmen."""
    backup_path = BACKUP_DIR / f"{backup_id}.json"
    if not backup_path.exists():
        raise FileNotFoundError(f"Backup {backup_id} nicht gefunden")

    data = json.loads(backup_path.read_text(encoding="utf-8"))
    entries = data.get("entries", [])
    restored, errors = 0, []

    for index, entry in enumerate(entries, 1):
        try:
            artwork = None
            if entry.get("artwork_replaced"):
                blob = entry.get("artwork")
                artwork = (base64.b64decode(blob) if blob else b"", entry.get("artwork_mime") or "")
            tagmod.write_tags(entry["path"], entry.get("fields", {}), artwork)
            restored += 1
        except Exception as exc:
            errors.append(f"{os.path.basename(entry['path'])}: {exc}")
        if progress and (index % 5 == 0 or index == len(entries)):
            progress(index, len(entries), os.path.basename(entry["path"]))

    if not errors:
        backup_path.unlink(missing_ok=True)
    return {"restored": restored, "errors": errors}


# --------------------------------------------------------------- Zustand
class Library:
    """Haelt den zuletzt gescannten Stand fuer die Oberflaeche fest."""

    def __init__(self) -> None:
        self.roots: list[str] = []
        self.tracks: list[Track] = []
        self.plans: list[AlbumPlan] = []
        self.options = FixOptions()
        self.scanned_at: float = 0.0

    def rescan(self, roots: Iterable[str], progress: ProgressFn = None) -> None:
        self.roots = [str(Path(r).expanduser()) for r in roots]
        self.tracks = scan(self.roots, progress)
        self.scanned_at = time.time()
        self.replan()

    def replan(self, options: Optional[FixOptions] = None) -> None:
        if options is not None:
            self.options = options
        self.plans = plan_library(self.tracks, self.options)

    def plan_by_key(self, key: str) -> Optional[AlbumPlan]:
        return next((p for p in self.plans if p.key == key), None)

    def track_by_path(self, path: str) -> Optional[Track]:
        return next((t for t in self.tracks if t.path == path), None)

    def stats(self) -> dict:
        readable = [t for t in self.tracks if not t.error]
        problem_plans = [p for p in self.plans if p.needs_fix]
        tiles_before = sum(p.tiles_before for p in self.plans)
        tiles_after = sum(p.tiles_after for p in self.plans)
        return {
            "roots": self.roots,
            "scanned_at": self.scanned_at,
            "tracks": len(readable),
            "unreadable": len(self.tracks) - len(readable),
            "albums": len(self.plans),
            "albums_with_issues": len(problem_plans),
            "tiles_before": tiles_before,
            "tiles_after": tiles_after,
            "duplicate_tiles": max(0, tiles_before - tiles_after),
            "total_bytes": sum(t.size for t in readable),
            "total_duration": sum(t.duration for t in readable),
        }
