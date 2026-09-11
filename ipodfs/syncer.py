"""
Synchronisation auf den iPod.

Der Ablauf ist bewusst schlicht und nachvollziehbar:

1. Fuer jeden Song wird aus den (reparierten) Tags ein Zielpfad gebaut -
   ``Album-Interpret/Album/01 Titel.mp3``. Weil der Ordnername aus dem
   *Album-Interpret* kommt, landen "Katy Perry" und
   "Katy Perry feat. Snoop Dogg" garantiert im selben Ordner.
2. Ein Manifest auf dem Geraet merkt sich, was schon oben liegt. Beim
   naechsten Lauf wandern nur geaenderte oder neue Dateien ueber das Kabel.
3. Optional werden Dateien geloescht, die iPodFS frueher hochgeladen hat,
   die es in der Sammlung aber nicht mehr gibt. Fremde Dateien bleiben
   grundsaetzlich unangetastet.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .devicelink import APP_PREFIX, DeviceError, DeviceLink
from .jobs import Job
from .tags import IOS_NATIVE_EXTENSIONS, Track

MANIFEST_NAME = ".ipodfs-manifest.json"

LAYOUTS = {
    "albumartist_album": "Album-Interpret / Album / Titel  (empfohlen)",
    "artist_album": "Interpret / Album / Titel",
    "album": "Album / Titel",
    "flat": "Alles in einen Ordner",
}

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_component(name: str, limit: int = 80) -> str:
    """Einen Namen in etwas verwandeln, das jedes Dateisystem akzeptiert."""
    text = unicodedata.normalize("NFC", (name or "").strip())
    text = _ILLEGAL.sub("_", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    if len(text) > limit:
        text = text[:limit].rstrip(" .")
    return text or "Unbenannt"


@dataclass
class SyncTarget:
    root: str = "media"
    base: str = "/iPodFS"
    layout: str = "albumartist_album"

    @property
    def label(self) -> str:
        if self.root.startswith(APP_PREFIX):
            return f"App {self.root[len(APP_PREFIX):]}{self.base}"
        if self.root == "root":
            return f"Wurzel-Dateisystem {self.base}"
        return f"Medien-Partition {self.base}"

    def to_dict(self) -> dict:
        return {"root": self.root, "base": self.base, "layout": self.layout, "label": self.label}

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "SyncTarget":
        data = data or {}
        base = (data.get("base") or "/").replace("\\", "/")
        if not base.startswith("/"):
            base = "/" + base
        base = posixpath.normpath(base)
        layout = data.get("layout") or "albumartist_album"
        if layout not in LAYOUTS:
            layout = "albumartist_album"
        return cls(root=data.get("root") or "media", base=base, layout=layout)


@dataclass
class SyncAction:
    kind: str            # upload | replace | skip | delete
    dst: str
    src: str = ""
    size: int = 0
    reason: str = ""

    def to_dict(self) -> dict:
        return {"kind": self.kind, "dst": self.dst, "src": self.src,
                "size": self.size, "reason": self.reason}


@dataclass
class SyncPlan:
    target: SyncTarget
    actions: list[SyncAction] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    free_bytes: int = 0

    @property
    def uploads(self) -> list[SyncAction]:
        return [a for a in self.actions if a.kind in ("upload", "replace")]

    @property
    def deletions(self) -> list[SyncAction]:
        return [a for a in self.actions if a.kind == "delete"]

    @property
    def upload_bytes(self) -> int:
        return sum(a.size for a in self.uploads)

    def to_dict(self, limit: int = 500) -> dict:
        skipped = [a for a in self.actions if a.kind == "skip"]
        return {
            "target": self.target.to_dict(),
            "upload_count": len(self.uploads),
            "replace_count": len([a for a in self.actions if a.kind == "replace"]),
            "skip_count": len(skipped),
            "delete_count": len(self.deletions),
            "upload_bytes": self.upload_bytes,
            "free_bytes": self.free_bytes,
            "fits": self.free_bytes == 0 or self.upload_bytes < self.free_bytes,
            "warnings": self.warnings,
            "actions": [a.to_dict() for a in (self.uploads + self.deletions)[:limit]],
            "truncated": len(self.uploads) + len(self.deletions) > limit,
        }


# ------------------------------------------------------------------ Zielpfade
def remote_path_for(track: Track, target: SyncTarget) -> str:
    album_artist = safe_component(track.effective_album_artist or "Unbekannter Interpret")
    artist = safe_component(track.artist or track.effective_album_artist or "Unbekannter Interpret")
    album = safe_component(track.album or "Unbekanntes Album")

    number = ""
    if track.track:
        number = f"{track.track:02d} "
        if track.disc and (track.disc_total or 0) > 1:
            number = f"{track.disc}-{track.track:02d} "

    filename = safe_component(f"{number}{track.title or 'Unbenannt'}", limit=110) + track.ext.lower()

    if target.layout == "flat":
        parts = [filename]
    elif target.layout == "album":
        parts = [album, filename]
    elif target.layout == "artist_album":
        parts = [artist, album, filename]
    else:
        parts = [album_artist, album, filename]

    return posixpath.join(target.base, *parts)


def _signature(track: Track) -> str:
    payload = "|".join(str(x) for x in (
        track.size, int(track.mtime), track.title, track.artist,
        track.albumartist, track.album, track.track, track.disc, track.art_hash,
    ))
    return hashlib.sha1(payload.encode("utf-8", "replace")).hexdigest()[:16]


# ------------------------------------------------------------------- Manifest
def manifest_path(target: SyncTarget) -> str:
    return posixpath.join(target.base, MANIFEST_NAME)


def load_manifest(link: DeviceLink, target: SyncTarget) -> dict:
    try:
        raw = link.read_file(target.root, manifest_path(target))
    except DeviceError:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return {}
    return data.get("files", {}) if isinstance(data, dict) else {}


def save_manifest(link: DeviceLink, target: SyncTarget, files: dict) -> None:
    payload = json.dumps(
        {"version": 1, "updated": time.time(), "layout": target.layout, "files": files},
        ensure_ascii=False,
    ).encode("utf-8")
    link.write_file(target.root, manifest_path(target), payload)


# ----------------------------------------------------------------- Planen
def build_plan(
    link: DeviceLink,
    tracks: Iterable[Track],
    target: SyncTarget,
    delete_orphans: bool = False,
    job: Optional[Job] = None,
) -> SyncPlan:
    plan = SyncPlan(target=target)
    tracks = [t for t in tracks if not t.error]

    if job:
        job.say("Manifest vom iPod lesen ...")
    manifest = load_manifest(link, target)

    try:
        plan.free_bytes = int(link.info().get("free_bytes") or 0)
    except Exception:
        plan.free_bytes = 0

    wanted: dict[str, Track] = {}
    collisions = 0
    for track in tracks:
        dst = remote_path_for(track, target)
        if dst in wanted:
            # Gleicher Zielname (z.B. zwei Dateien, gleiche Tags) -> entzerren.
            stem, dot, ext = dst.rpartition(".")
            suffix = 2
            while f"{stem} ({suffix}){dot}{ext}" in wanted:
                suffix += 1
            dst = f"{stem} ({suffix}){dot}{ext}"
            collisions += 1
        wanted[dst] = track

    if collisions:
        plan.warnings.append(
            f"{collisions} Songs hatten denselben Zielnamen und wurden durchnummeriert. "
            "Meist fehlen dort Titel- oder Track-Nummern."
        )

    non_native = sorted({t.ext for t in tracks if t.ext not in IOS_NATIVE_EXTENSIONS})
    if non_native:
        plan.warnings.append(
            "Formate, die die iOS-Musik-App nicht kennt: " + ", ".join(non_native)
            + ". VLC & Co. spielen sie problemlos."
        )

    total = len(wanted)
    for index, (dst, track) in enumerate(sorted(wanted.items()), 1):
        signature = _signature(track)
        known = manifest.get(dst)
        if known and known.get("sig") == signature:
            plan.actions.append(SyncAction(kind="skip", dst=dst, src=track.path,
                                           size=track.size, reason="unveraendert"))
        elif known:
            plan.actions.append(SyncAction(kind="replace", dst=dst, src=track.path,
                                           size=track.size, reason="Tags oder Datei geaendert"))
        else:
            plan.actions.append(SyncAction(kind="upload", dst=dst, src=track.path,
                                           size=track.size, reason="neu"))
        if job and index % 100 == 0:
            job.progress(index, total, "Abgleich ...")

    if delete_orphans:
        for dst, meta in manifest.items():
            if dst not in wanted:
                plan.actions.append(SyncAction(kind="delete", dst=dst,
                                               size=int(meta.get("size", 0)),
                                               reason="nicht mehr in der Sammlung"))

    return plan


# ----------------------------------------------------------------- Ausfuehren
def run_plan(link: DeviceLink, plan: SyncPlan, job: Optional[Job] = None) -> dict:
    target = plan.target
    manifest = load_manifest(link, target)

    uploads = plan.uploads
    deletions = plan.deletions
    total_bytes = plan.upload_bytes or 1
    done_bytes = 0
    uploaded = 0
    deleted = 0
    errors: list[str] = []

    link.makedirs(target.root, target.base)

    for index, action in enumerate(uploads, 1):
        if job:
            job.check_cancel()
            job.say(f"[{index}/{len(uploads)}] {posixpath.basename(action.dst)}")

        base_done = done_bytes

        def on_chunk(sent: int, size: int) -> None:
            if job:
                job.progress(base_done + sent, total_bytes)

        try:
            link.upload(target.root, action.src, action.dst, progress=on_chunk)
            manifest[action.dst] = {
                "src": action.src,
                "size": action.size,
                "sig": _signature_from_action(action),
                "uploaded": time.time(),
            }
            uploaded += 1
        except DeviceError as exc:
            errors.append(f"{posixpath.basename(action.dst)}: {exc}")
            if exc.kind == "full":
                job and job.say("Kein Speicherplatz mehr - Abbruch.")
                break
        except Exception as exc:
            errors.append(f"{posixpath.basename(action.dst)}: {exc}")

        done_bytes += action.size
        if job:
            job.progress(done_bytes, total_bytes)
        # Regelmaessig sichern, damit ein Abbruch nicht alles zunichtemacht.
        if uploaded and uploaded % 25 == 0:
            _safe_save(link, target, manifest, errors)

    for action in deletions:
        if job:
            job.check_cancel()
            job.say(f"Loesche {posixpath.basename(action.dst)}")
        try:
            link.remove(target.root, action.dst, recursive=False)
            deleted += 1
        except DeviceError as exc:
            if exc.kind != "not_found":
                errors.append(f"{posixpath.basename(action.dst)}: {exc}")
        manifest.pop(action.dst, None)

    _safe_save(link, target, manifest, errors)

    if job:
        job.say(f"Fertig: {uploaded} uebertragen, {deleted} geloescht.")

    return {
        "uploaded": uploaded,
        "deleted": deleted,
        "skipped": len([a for a in plan.actions if a.kind == "skip"]),
        "bytes": done_bytes,
        "errors": errors,
    }


def _signature_from_action(action: SyncAction) -> str:
    """Signatur nach dem Upload aus der Quelldatei neu bilden."""
    from .tags import read_track

    try:
        return _signature(read_track(action.src))
    except Exception:
        return ""


def _safe_save(link: DeviceLink, target: SyncTarget, manifest: dict, errors: list[str]) -> None:
    try:
        save_manifest(link, target, manifest)
    except Exception as exc:
        errors.append(f"Manifest konnte nicht geschrieben werden: {exc}")
