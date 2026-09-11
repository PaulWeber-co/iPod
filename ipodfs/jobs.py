"""
Hintergrund-Jobs mit Fortschritt.

Alles, was laenger als einen Wimpernschlag dauert (scannen, reparieren,
synchronisieren), laeuft hier - in *einem* Worker-Thread. Das serialisiert
die Geraetezugriffe ganz nebenbei: eine AFC-Verbindung vertraegt keine zwei
gleichzeitigen Nutzer.
"""

from __future__ import annotations

import queue
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


class JobCancelled(Exception):
    """Wird geworfen, wenn der Nutzer abbricht."""


@dataclass
class Job:
    id: str
    kind: str
    title: str
    status: str = "pending"          # pending | running | done | error | cancelled
    current: int = 0
    total: int = 0
    message: str = ""
    result: Any = None
    error: str = ""
    hint: str = ""
    log: list[str] = field(default_factory=list)
    created: float = field(default_factory=time.time)
    started: float = 0.0
    finished: float = 0.0
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    # ------------------------------------------------------- vom Job benutzt
    def progress(self, current: int, total: int, message: str = "") -> None:
        self.current, self.total = current, total
        if message:
            self.message = message
        self.check_cancel()

    def say(self, message: str) -> None:
        self.message = message
        self.log.append(message)
        del self.log[:-200]

    def check_cancel(self) -> None:
        if self._cancel.is_set():
            raise JobCancelled()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def to_dict(self) -> dict:
        return {
            "id": self.id, "kind": self.kind, "title": self.title,
            "status": self.status, "current": self.current, "total": self.total,
            "percent": round(100 * self.current / self.total, 1) if self.total else 0.0,
            "message": self.message, "result": self.result, "error": self.error,
            "hint": self.hint, "log": self.log[-40:],
            "created": self.created, "started": self.started, "finished": self.finished,
            "running": self.status in ("pending", "running"),
        }


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._queue: "queue.Queue[tuple[Job, Callable[[Job], Any]]]" = queue.Queue()
        self._lock = threading.RLock()
        self._worker = threading.Thread(target=self._run, name="ipodfs-jobs", daemon=True)
        self._worker.start()

    def submit(self, kind: str, title: str, fn: Callable[[Job], Any]) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, title=title)
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
            # Alte, abgeschlossene Jobs aufraeumen.
            while len(self._order) > 40:
                old = self._order.pop(0)
                if self._jobs.get(old) and not self._jobs[old].to_dict()["running"]:
                    self._jobs.pop(old, None)
                else:
                    self._order.insert(0, old)
                    break
        self._queue.put((job, fn))
        return job

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None or not job.to_dict()["running"]:
            return False
        job._cancel.set()
        if job.status == "pending":
            job.status = "cancelled"
            job.finished = time.time()
        return True

    def recent(self, limit: int = 12) -> list[dict]:
        with self._lock:
            ids = self._order[-limit:]
        return [self._jobs[i].to_dict() for i in reversed(ids) if i in self._jobs]

    @property
    def active(self) -> Optional[dict]:
        for job in self._jobs.values():
            if job.status == "running":
                return job.to_dict()
        return None

    def _run(self) -> None:
        while True:
            job, fn = self._queue.get()
            if job.cancelled:
                job.status = "cancelled"
                job.finished = time.time()
                continue
            job.status = "running"
            job.started = time.time()
            try:
                job.result = fn(job)
                job.status = "cancelled" if job.cancelled else "done"
            except JobCancelled:
                job.status = "cancelled"
                job.say("Abgebrochen.")
            except Exception as exc:
                job.status = "error"
                job.error = str(exc) or type(exc).__name__
                job.hint = getattr(exc, "hint", "")
                job.log.append(traceback.format_exc(limit=3))
            finally:
                job.finished = time.time()


manager = JobManager()
