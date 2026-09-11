"""
Lokaler Web-Server - die Oberflaeche laeuft im Browser.

Warum Browser und nicht Tk? Weil Drag & Drop aus dem Explorer/Finder dort
einfach funktioniert, ganze Ordner inklusive, auf Windows, macOS und Linux
gleichermassen, ohne zusaetzliche GUI-Bibliothek.

Der Server hoert ausschliesslich auf 127.0.0.1 und verlangt bei jedem
API-Aufruf einen Token-Header. Damit kann keine beliebige Webseite im
Hintergrund auf die lokale Dateiablage oder den iPod zugreifen: ein
Custom-Header erzwingt einen CORS-Preflight, den der Browser fuer fremde
Herkunft blockiert.
"""

from __future__ import annotations

import io
import mimetypes
import os
import posixpath
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

from flask import Flask, Response, jsonify, request, send_file, send_from_directory

from . import doctor as doctor_mod
from . import library as libmod
from . import tags as tagmod
from .devicelink import KNOWN_PLAYERS, DeviceError, link
from .jobs import manager
from .normalize import ISSUE_LABELS, FixOptions
from .syncer import LAYOUTS, SyncTarget, build_plan, run_plan

STATIC_DIR = Path(__file__).parent / "static"
TOKEN = secrets.token_urlsafe(24)

app = Flask(__name__, static_folder=None)
# Flask sortiert JSON-Schluessel sonst alphabetisch - dann steht in der
# Layout-Auswahl nicht mehr die empfohlene Variante oben.
app.json.sort_keys = False
library = libmod.Library()

_last_sync_plan: dict[str, Any] = {}


# ------------------------------------------------------------------ Schutz
@app.before_request
def _guard() -> Optional[Response]:
    if request.method == "OPTIONS":
        return None
    if request.path.startswith("/api/"):
        token = request.headers.get("X-IPodFS-Token") or request.args.get("token")
        if token != TOKEN:
            return jsonify({"error": "Ungueltiger Token. Seite neu laden."}), 403
    host = (request.host or "").split(":")[0]
    if host not in ("127.0.0.1", "localhost", "[::1]", "::1"):
        return jsonify({"error": "Nur lokaler Zugriff erlaubt."}), 403
    return None


def _fail(exc: BaseException, status: int = 400):
    if isinstance(exc, DeviceError):
        return jsonify(exc.to_dict()), status
    return jsonify({"error": str(exc) or type(exc).__name__, "hint": ""}), status


# ---------------------------------------------------------------- Oberflaeche
@app.get("/")
def index() -> Response:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    return Response(html.replace("{{TOKEN}}", TOKEN), mimetype="text/html")


@app.get("/static/<path:filename>")
def static_files(filename: str):
    return send_from_directory(STATIC_DIR, filename)


# --------------------------------------------------------------------- Status
@app.get("/api/state")
def state():
    try:
        device = link.info()
    except DeviceError as exc:
        device = {"connected": False, "error": exc.to_dict()}
    return jsonify({
        "device": device,
        "library": library.stats(),
        "options": vars(library.options),
        "job": manager.active,
        "layouts": LAYOUTS,
        "issue_labels": ISSUE_LABELS,
        "known_players": KNOWN_PLAYERS,
        "platform": sys.platform,
    })


@app.get("/api/doctor")
def api_doctor():
    return jsonify(doctor_mod.run())


# --------------------------------------------------------------------- Geraet
@app.get("/api/device/list")
def device_list():
    try:
        return jsonify({"devices": link.list_devices()})
    except DeviceError as exc:
        return _fail(exc)


@app.post("/api/device/connect")
def device_connect():
    udid = (request.json or {}).get("udid")
    try:
        return jsonify(link.connect(udid))
    except DeviceError as exc:
        return _fail(exc)


@app.post("/api/device/disconnect")
def device_disconnect():
    link.disconnect()
    return jsonify({"connected": False})


@app.get("/api/device/apps")
def device_apps():
    try:
        return jsonify({"apps": link.apps()})
    except DeviceError as exc:
        return _fail(exc)


@app.post("/api/device/install")
def device_install():
    """Eine hochgeladene .ipa ueber USB aufs Geraet bringen."""
    upload = request.files.get("ipa")
    if upload is None or not (upload.filename or "").lower().endswith(".ipa"):
        return jsonify({"error": "Bitte eine .ipa-Datei auswaehlen."}), 400

    staging = Path(tempfile.mkdtemp(prefix="ipodfs-ipa-"))
    local = staging / Path(upload.filename).name
    upload.save(local)

    def work(job):
        job.say(f"Installiere {local.name} ...")
        try:
            return link.install_app(str(local),
                                    progress=lambda done, total: job.progress(done, total))
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    job = manager.submit("install", f"App installieren: {local.name}", work)
    return jsonify({"job": job.id})


# ------------------------------------------------------------- Dateisystem
@app.get("/api/fs/list")
def fs_list():
    root = request.args.get("root", "media")
    path = request.args.get("path", "/")
    try:
        entries = link.listdir(root, path)
    except DeviceError as exc:
        return _fail(exc)
    return jsonify({
        "root": root,
        "path": path,
        "parent": posixpath.dirname(path.rstrip("/")) or "/",
        "entries": [e.to_dict() for e in entries],
    })


@app.post("/api/fs/mkdir")
def fs_mkdir():
    data = request.json or {}
    try:
        link.makedirs(data.get("root", "media"), data["path"])
        return jsonify({"ok": True})
    except (DeviceError, KeyError) as exc:
        return _fail(exc)


@app.post("/api/fs/delete")
def fs_delete():
    data = request.json or {}
    root = data.get("root", "media")
    paths = data.get("paths") or []
    errors = []
    for path in paths:
        try:
            link.remove(root, path, recursive=True)
        except DeviceError as exc:
            errors.append(f"{posixpath.basename(path)}: {exc}")
    return jsonify({"deleted": len(paths) - len(errors), "errors": errors})


@app.post("/api/fs/rename")
def fs_rename():
    data = request.json or {}
    try:
        link.rename(data.get("root", "media"), data["src"], data["dst"])
        return jsonify({"ok": True})
    except (DeviceError, KeyError) as exc:
        return _fail(exc)


@app.get("/api/fs/download")
def fs_download():
    root = request.args.get("root", "media")
    path = request.args.get("path", "")
    try:
        data = link.read_file(root, path)
    except DeviceError as exc:
        return _fail(exc)
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return send_file(io.BytesIO(data), mimetype=mime, as_attachment=True,
                     download_name=posixpath.basename(path))


@app.post("/api/fs/upload")
def fs_upload():
    """
    Drag & Drop aus dem Explorer/Finder.

    Die Dateien landen erst in einem temporaeren Ordner (der Browser liefert
    Inhalte, keine Pfade) und wandern danach im Hintergrund-Job aufs Geraet -
    so bleibt die Oberflaeche waehrend grosser Uploads bedienbar.
    """
    root = request.form.get("root", "media")
    base = request.form.get("path", "/")
    files = request.files.getlist("files")
    relatives = request.form.getlist("relative")

    if not files:
        return jsonify({"error": "Keine Dateien empfangen."}), 400

    staging = Path(tempfile.mkdtemp(prefix="ipodfs-upload-"))
    staged: list[tuple[str, str]] = []
    for index, storage in enumerate(files):
        relative = relatives[index] if index < len(relatives) else ""
        relative = (relative or storage.filename or f"datei-{index}").replace("\\", "/")
        relative = "/".join(part for part in relative.split("/") if part not in ("", ".", ".."))
        local = staging / relative
        local.parent.mkdir(parents=True, exist_ok=True)
        storage.save(local)
        staged.append((str(local), posixpath.join(base, relative)))

    def work(job):
        total = sum(os.path.getsize(src) for src, _ in staged) or 1
        done = 0
        errors = []
        try:
            for index, (src, dst) in enumerate(staged, 1):
                job.check_cancel()
                job.say(f"[{index}/{len(staged)}] {posixpath.basename(dst)}")
                base_done = done
                try:
                    link.upload(root, src, dst,
                                progress=lambda sent, size: job.progress(base_done + sent, total))
                except DeviceError as exc:
                    errors.append(f"{posixpath.basename(dst)}: {exc}")
                done += os.path.getsize(src)
                job.progress(done, total)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        return {"uploaded": len(staged) - len(errors), "errors": errors}

    job = manager.submit("upload", f"{len(staged)} Datei(en) auf den iPod", work)
    return jsonify({"job": job.id})


# -------------------------------------------------------------- Sammlung
_PICKER = """
import sys
import tkinter as tk
from tkinter import filedialog

root = tk.Tk()
root.withdraw()
root.attributes('-topmost', True)
print(filedialog.askdirectory(title='Musikordner waehlen') or '')
"""


@app.post("/api/library/pick")
def library_pick():
    """Nativer Ordner-Dialog - im Unterprozess, damit Tk den Server nicht stoert."""
    try:
        result = subprocess.run([sys.executable, "-c", _PICKER],
                                capture_output=True, text=True, timeout=300)
        path = (result.stdout or "").strip().splitlines()
        return jsonify({"path": path[-1] if path else ""})
    except Exception as exc:
        return jsonify({"path": "", "error": f"Dialog nicht verfuegbar: {exc}"})


@app.post("/api/library/scan")
def library_scan():
    roots = (request.json or {}).get("roots") or []
    roots = [r for r in roots if r and Path(r).expanduser().exists()]
    if not roots:
        return jsonify({"error": "Kein gueltiger Ordner angegeben."}), 400

    def work(job):
        job.say("Dateien einlesen ...")
        library.rescan(roots, progress=lambda c, t, name: job.progress(c, t, name))
        job.say("Alben analysieren ...")
        return library.stats()

    job = manager.submit("scan", "Sammlung einlesen", work)
    return jsonify({"job": job.id})


@app.get("/api/library/albums")
def library_albums():
    only_issues = request.args.get("issues") == "1"
    needle = (request.args.get("q") or "").casefold()
    plans = library.plans
    if only_issues:
        plans = [p for p in plans if p.needs_fix]
    if needle:
        plans = [p for p in plans
                 if needle in p.album.casefold() or needle in p.album_artist.casefold()]
    return jsonify({
        "albums": [p.to_dict(include_tracks=False) for p in plans[:400]],
        "total": len(plans),
        "stats": library.stats(),
    })


@app.get("/api/library/album/<key>")
def library_album(key: str):
    plan = library.plan_by_key(key)
    if plan is None:
        return jsonify({"error": "Album nicht gefunden."}), 404
    return jsonify(plan.to_dict(include_tracks=True))


@app.post("/api/library/options")
def library_options():
    library.replan(FixOptions.from_dict(request.json or {}))
    return jsonify({"options": vars(library.options), "stats": library.stats()})


@app.post("/api/library/fix")
def library_fix():
    data = request.json or {}
    keys = data.get("keys")
    plans = library.plans if not keys else [p for p in library.plans if p.key in keys]
    plans = [p for p in plans if p.needs_fix]
    if not plans:
        return jsonify({"error": "Nichts zu reparieren."}), 400

    def work(job):
        job.say(f"{len(plans)} Album/Alben reparieren ...")
        result = libmod.apply_plans(plans, library.options,
                                    progress=lambda c, t, name: job.progress(c, t, name))
        job.say("Neu analysieren ...")
        library.replan()
        return {**result.to_dict(), "stats": library.stats()}

    job = manager.submit("fix", "Tags reparieren", work)
    return jsonify({"job": job.id})


@app.post("/api/library/track")
def library_track():
    data = request.json or {}
    path = data.get("path")
    track = library.track_by_path(path or "")
    if track is None:
        return jsonify({"error": "Song nicht in der Sammlung."}), 404

    fields = {k: v for k, v in (data.get("fields") or {}).items()
              if k in tagmod.TEXT_FIELDS + tagmod.NUM_FIELDS + ("compilation",)}
    for key in tagmod.NUM_FIELDS:
        if key in fields:
            fields[key] = int(fields[key]) if str(fields[key]).strip() else None
    try:
        tagmod.write_tags(path, fields)
    except Exception as exc:
        return _fail(exc)

    for key, value in fields.items():
        setattr(track, key, value)
    library.replan()
    return jsonify({"ok": True, "track": track.to_dict(), "stats": library.stats()})


def _inside_library(path: str) -> bool:
    """Nur Dateien aus den eingelesenen Ordnern ausliefern."""
    try:
        resolved = Path(path).expanduser().resolve()
    except OSError:
        return False
    for root in library.roots:
        try:
            resolved.relative_to(Path(root).expanduser().resolve())
            return True
        except ValueError:
            continue
    return False


@app.get("/api/library/art")
def library_art():
    path = request.args.get("path", "")
    if not _inside_library(path):
        return Response(status=404)
    data, mime = tagmod.read_artwork(path)
    if not data:
        return Response(status=404)
    return Response(data, mimetype=mime or "image/jpeg",
                    headers={"Cache-Control": "private, max-age=300"})


@app.get("/api/library/backups")
def library_backups():
    return jsonify({"backups": libmod.list_backups()})


@app.post("/api/library/undo")
def library_undo():
    backup_id = (request.json or {}).get("id")

    def work(job):
        job.say("Alte Tags zuruecksichern ...")
        result = libmod.undo(backup_id, progress=lambda c, t, n: job.progress(c, t, n))
        if library.roots:
            library.rescan(library.roots)
        return result

    job = manager.submit("undo", "Reparatur zuruecknehmen", work)
    return jsonify({"job": job.id})


# ------------------------------------------------------------------ Sync
@app.post("/api/sync/plan")
def sync_plan():
    data = request.json or {}
    target = SyncTarget.from_dict(data.get("target"))
    delete_orphans = bool(data.get("delete_orphans"))

    def work(job):
        job.say("Vergleiche Sammlung mit iPod ...")
        plan = build_plan(link, library.tracks, target, delete_orphans, job)
        _last_sync_plan[target.root + target.base] = plan
        return plan.to_dict()

    job = manager.submit("sync-plan", "Sync vorbereiten", work)
    return jsonify({"job": job.id})


@app.post("/api/sync/run")
def sync_run():
    data = request.json or {}
    target = SyncTarget.from_dict(data.get("target"))
    delete_orphans = bool(data.get("delete_orphans"))

    def work(job):
        plan = _last_sync_plan.get(target.root + target.base)
        if plan is None or plan.target.layout != target.layout:
            job.say("Plan erstellen ...")
            plan = build_plan(link, library.tracks, target, delete_orphans, job)
        job.say(f"{len(plan.uploads)} Datei(en) uebertragen ...")
        result = run_plan(link, plan, job)
        _last_sync_plan.pop(target.root + target.base, None)
        return result

    job = manager.submit("sync", "Auf den iPod synchronisieren", work)
    return jsonify({"job": job.id})


# ------------------------------------------------------------------- Jobs
@app.get("/api/jobs")
def jobs_list():
    return jsonify({"jobs": manager.recent()})


@app.get("/api/jobs/<job_id>")
def job_get(job_id: str):
    job = manager.get(job_id)
    if job is None:
        return jsonify({"error": "Job unbekannt."}), 404
    return jsonify(job.to_dict())


@app.post("/api/jobs/<job_id>/cancel")
def job_cancel(job_id: str):
    return jsonify({"cancelled": manager.cancel(job_id)})
