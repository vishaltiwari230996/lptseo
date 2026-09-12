"""Background jobs and chunked storage for the deep audit.

Two small things every long-running diagnostic here needs, kept in one place so
there is one implementation of each.

**Jobs.** A full crawl of a 1,000-page site takes minutes; measuring every page
in a real browser under mobile throttling takes over an hour. Neither can be an
HTTP request — the client would time out long before the work finished, and a
dropped connection would kill it. So a job runs on a daemon thread and writes
its progress to state as it goes; the endpoint that starts it returns at once,
and the panel polls the progress document. One job per (kind, brand) at a time:
starting a second while the first runs returns the running one rather than
racing it.

**Chunked lists.** Per-page results for a large site do not fit in one document
— Firestore refuses anything over 1MB, and a thousand page records with their
findings is several times that. ``save_list`` splits a list across numbered
documents under one prefix, plus a manifest; ``load_list`` reassembles it. The
local JSON backend has no such limit, but the code path is the same either way
so the cloud backend is not a separate, untested branch.
"""
from __future__ import annotations

import threading
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Callable

from . import state

#: Records per chunk. Sized well under Firestore's 1MB document limit for the
#: largest record shape here (a landing page with ~40 findings).
CHUNK = 120

#: Guards the registry of running threads. Re-entrant as a safety net, but no
#: code path should need that: see `start`, which must never call `status`
#: while holding it.
_LOCK = threading.RLock()
_RUNNING: dict[str, threading.Thread] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Chunked lists
# --------------------------------------------------------------------------- #

def save_list(prefix: str, items: list[dict], *, meta: dict | None = None) -> None:
    """Persist ``items`` as ``{prefix}-0``, ``{prefix}-1``… plus ``{prefix}-meta``.

    The manifest is written LAST, after every chunk: a reader that finds a
    manifest therefore always finds all the chunks it names. Chunks left over
    from a previous, longer save are deleted so a shrinking list cannot come
    back with stale tail entries.
    """
    old = state.load(f"{prefix}-meta") or {}
    chunks = [items[i:i + CHUNK] for i in range(0, len(items), CHUNK)] or [[]]
    for n, part in enumerate(chunks):
        state.save(f"{prefix}-{n}", {"items": part})
    for n in range(len(chunks), int(old.get("chunks", 0))):
        state.delete(f"{prefix}-{n}")
    state.save(f"{prefix}-meta", {
        **(meta or {}),
        "chunks": len(chunks),
        "count": len(items),
        "saved_at": _now(),
    })


def load_list(prefix: str) -> tuple[list[dict], dict | None]:
    """``(items, manifest)``; ``([], None)`` when nothing has been saved."""
    meta = state.load(f"{prefix}-meta")
    if not meta:
        return [], None
    items: list[dict] = []
    for n in range(int(meta.get("chunks", 0))):
        items.extend((state.load(f"{prefix}-{n}") or {}).get("items") or [])
    return items, meta


# --------------------------------------------------------------------------- #
# Jobs
# --------------------------------------------------------------------------- #

def _key(kind: str, brand_id: str) -> str:
    return f"job-{kind}-{brand_id}"


def status(kind: str, brand_id: str) -> dict | None:
    """The job's progress document, with ``alive`` added from this process.

    ``state`` outlives the process; the thread does not. A document that says
    ``running`` but has no live thread belongs to a server that was restarted
    mid-job, so it is reported as ``interrupted`` rather than left spinning
    forever in the panel.
    """
    doc = state.load(_key(kind, brand_id))
    if not doc:
        return None
    with _LOCK:
        thread = _RUNNING.get(_key(kind, brand_id))
    alive = bool(thread and thread.is_alive())
    if doc.get("status") == "running" and not alive:
        doc = {**doc, "status": "interrupted"}
    return {**doc, "alive": alive}


class Progress:
    """Handed to a job body; every call persists, so a poll always sees current state.

    Writes are throttled to one per ``every`` seconds for high-frequency updates
    (one per crawled page would be a thousand writes), but a phase change or the
    final state is always written immediately.
    """

    def __init__(self, kind: str, brand_id: str, *, every: float = 1.5) -> None:
        self.key = _key(kind, brand_id)
        self.every = every
        self._last = 0.0
        self.doc: dict[str, Any] = {
            "kind": kind,
            "brand_id": brand_id,
            "status": "running",
            "phase": "starting",
            "done": 0,
            "total": 0,
            "started_at": _now(),
            "finished_at": None,
            "error": None,
            "log": [],
        }
        self._flush(force=True)

    def _flush(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - self._last >= self.every:
            state.save(self.key, self.doc)
            self._last = now

    def phase(self, name: str, *, total: int = 0) -> None:
        self.doc.update(phase=name, done=0, total=total)
        self.note(name)
        self._flush(force=True)

    def step(self, n: int = 1) -> None:
        self.doc["done"] = self.doc.get("done", 0) + n
        self._flush()

    def note(self, line: str) -> None:
        # A short trail of what happened, newest last. Capped — this is for a
        # human glancing at a stalled job, not an archive.
        self.doc["log"] = (self.doc.get("log") or [])[-19:] + [f"{_now()[11:19]} {line}"]
        self._flush()

    def set(self, **fields: Any) -> None:
        self.doc.update(fields)
        self._flush(force=True)

    def finish(self, *, error: str | None = None) -> None:
        self.doc.update(
            status="failed" if error else "done",
            error=error,
            finished_at=_now(),
            phase="failed" if error else "done",
        )
        self._flush(force=True)


def start(kind: str, brand_id: str, body: Callable[[Progress], None]) -> dict:
    """Run ``body`` on a daemon thread unless one is already running for this key.

    ``body`` receives a :class:`Progress`. Anything it raises is caught, logged
    into the progress document, and ends the job as ``failed`` — a crashed job
    must say so, not vanish.

    The new run's "running" document is written HERE, synchronously, before the
    thread starts. It used to be written by the thread, and this function then
    polled for "a document with a start time" — which the PREVIOUS run's
    finished document already had, so the response to "start" reported the old
    run as done. Writing it first leaves no window in which a stale document can
    be read as the new one.
    """
    key = _key(kind, brand_id)
    with _LOCK:
        existing = _RUNNING.get(key)
        already_running = bool(existing and existing.is_alive())
    # Decided under the lock, reported outside it. `status` takes the same lock,
    # and calling it from inside this block used to deadlock the SECOND start of
    # a running job — and with it every later status poll and the job's own
    # cleanup, freezing the panel until the server was restarted.
    if already_running:
        return status(kind, brand_id) or {"status": "running"}

    with _LOCK:
        existing = _RUNNING.get(key)
        if existing and existing.is_alive():  # lost a race to another starter
            return {"status": "running", "kind": kind, "brand_id": brand_id}

        progress = Progress(kind, brand_id)   # writes the fresh "running" doc now

        def run() -> None:
            try:
                body(progress)
                progress.finish()
            except Exception as exc:  # noqa: BLE001 — see docstring
                progress.note(traceback.format_exc(limit=3).strip().splitlines()[-1])
                progress.finish(error=f"{type(exc).__name__}: {exc}"[:500])
            finally:
                with _LOCK:
                    _RUNNING.pop(key, None)

        thread = threading.Thread(target=run, name=key, daemon=True)
        _RUNNING[key] = thread
        thread.start()
    return status(kind, brand_id) or {"status": "running"}
