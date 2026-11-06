from __future__ import annotations

import json
import os
import time
import uuid
from copy import deepcopy
from threading import Lock
from typing import Any

from app.paths import HISTORY_PATH, SCANS_PATH, ensure_dirs

TERMINAL = {"done", "error", "stopped", "partial"}
ACTIVE = {"queued", "starting", "scanning", "downloading", "stopping"}
# "Load more" appends to one scan, so keep enough for several pages.
SCAN_ITEMS_KEPT = 2000

_lock = Lock()
_jobs: dict[str, dict[str, Any]] = {}
_listeners: list = []
_revision = 0
# job id -> (files counted, finished?, total bytes)
_size_cache: dict[str, tuple[int, bool, int]] = {}
_speed_state: dict[str, tuple[float, int, float]] = {}


def revision() -> int:
    return _revision


def _now() -> float:
    return time.time()


def new_id() -> str:
    return uuid.uuid4().hex[:10]


def _card_note(job: dict[str, Any], log: list) -> str:
    kind = job.get("kind")
    progress = job.get("progress") or {}
    if kind == "scan":
        found = len(job.get("items") or [])
        if job.get("status") == "scanning":
            return f"Found {found} file{'s' if found != 1 else ''}…"
        if found:
            return f"{found} files in preview"
    saved = int(progress.get("saved") or 0)
    skipped = int(progress.get("skipped") or 0)
    total = progress.get("total")
    if total:
        return f"{saved} of {total} saved"
    if saved or skipped:
        extra = f", {skipped} skipped" if skipped else ""
        return f"{saved} saved{extra}"
    if job.get("status") in {"downloading", "starting"}:
        return "Downloading…"
    last = str(log[-1]).strip() if log else ""
    if last.startswith("#") or last.startswith("--") or "gallery-dl" in last:
        return ""
    return last[:120]


def snapshot(job: dict[str, Any]) -> dict[str, Any]:
    items = job.get("items") or []
    thumbs = [it.get("thumb") for it in items[:8] if isinstance(it, dict) and it.get("thumb")]
    log = job.get("log") or []
    return {
        "id": job.get("id"),
        "url": job.get("url"),
        "kind": job.get("kind"),
        "title": job.get("title"),
        "extractor": job.get("extractor") or "",
        "author": job.get("author") or "",
        "thumbnail": job.get("thumbnail") or (thumbs[0] if thumbs else ""),
        "preview_thumbs": thumbs[:6],
        "range": job.get("range") or "",
        "status": job.get("status"),
        "progress": dict(job.get("progress") or {}),
        "output_path": job.get("output_path") or "",
        "files_count": len(job.get("files") or []),
        "item_count": len(items),
        "error": job.get("error") or "",
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "ended_at": job.get("ended_at"),
        "pid": job.get("pid"),
        "from_scan": job.get("from_scan"),
        "parent": job.get("parent"),
        "status_line": _card_note(job, log),
    }


def _stat_total(paths: list[str]) -> int:
    total = 0
    for p in paths:
        try:
            total += os.stat(p).st_size
        except OSError:
            pass
    return total


def _files_size(job_id: str, paths: list[str], status: str) -> int:
    """Bytes on disk for a job's files.

    ``files`` only grows while a job runs, so only new paths are stat'ed; a full
    recount happens once when the job finishes or if the list shrank (retry).
    """
    finished = status in TERMINAL
    cached = _size_cache.get(job_id)
    if cached and cached[0] == len(paths) and cached[1] == finished:
        return cached[2]
    if cached and not finished and not cached[1] and cached[0] < len(paths):
        total = cached[2] + _stat_total(paths[cached[0]:])
    else:
        total = _stat_total(paths)
    _size_cache[job_id] = (len(paths), finished, total)
    return total


def _live_speed(job_id: str, size: int, status: str) -> float:
    """Smoothed bytes/sec from growth of the job's files on disk."""
    if status not in ACTIVE:
        _speed_state.pop(job_id, None)
        return 0.0
    now = time.monotonic()
    prev = _speed_state.get(job_id)
    if prev is None:
        _speed_state[job_id] = (now, size, 0.0)
        return 0.0
    t0, s0, smoothed = prev
    dt = now - t0
    if dt < 0.8:
        return smoothed
    inst = max(0, size - s0) / dt
    smoothed = inst if smoothed <= 0 else 0.6 * inst + 0.4 * smoothed
    _speed_state[job_id] = (now, size, smoothed)
    return smoothed


def all_jobs() -> list[dict[str, Any]]:
    with _lock:
        rows = [(snapshot(j), list(j.get("files") or [])) for j in _jobs.values()]
    for snap, paths in rows:
        snap["size"] = _files_size(snap["id"], paths, snap.get("status") or "")
        snap["size_speed"] = round(_live_speed(snap["id"], snap["size"], snap.get("status") or ""))
    rows.sort(key=lambda r: r[0].get("created_at") or 0, reverse=True)
    return [r[0] for r in rows]


def get_job(job_id: str) -> dict[str, Any] | None:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return None
        snap = snapshot(job)
        paths = list(job.get("files") or [])
    snap["size"] = _files_size(job_id, paths, snap.get("status") or "")
    snap["size_speed"] = round(_live_speed(job_id, snap["size"], snap.get("status") or ""))
    return snap


def peek(job_id: str) -> dict[str, Any] | None:
    """Cheap read of a job's scalar fields: no snapshot, no disk stats.

    For per-line hot paths in the engine, where get_job would stat files.
    """
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return None
        copied = {k: v for k, v in job.items() if k not in {"log", "files", "items"}}
        copied["progress"] = dict(job.get("progress") or {})
        return copied


def get_log(job_id: str) -> list[str]:
    with _lock:
        job = _jobs.get(job_id)
        return list(job.get("log") or []) if job else []


def get_items(job_id: str) -> list[dict[str, Any]] | None:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return None
        return list(job.get("items") or [])


def active_count() -> int:
    with _lock:
        return sum(1 for j in _jobs.values() if j.get("status") in ACTIVE and j.get("status") != "queued")


def create_job(
    url: str,
    *,
    kind: str = "download",
    range_spec: str = "",
    title: str = "",
    extra: dict | None = None,
) -> dict[str, Any]:
    clean_url = url.strip()
    with _lock:
        if kind in {"download", "scan"}:
            for existing in _jobs.values():
                if (
                    existing.get("url") == clean_url
                    and existing.get("kind") == kind
                    and (existing.get("range") or "") == (range_spec or "")
                    and existing.get("status") in ACTIVE
                ):
                    copied = snapshot(existing)
                    copied["already"] = True
                    return copied
        job = {
            "id": new_id(),
            "url": clean_url,
            "kind": kind,
            "title": title or clean_url,
            "extractor": "",
            "author": "",
            "thumbnail": "",
            "range": range_spec,
            "status": "queued",
            "progress": {
                "percent": None,
                "current": 0,
                "total": None,
                "saved": 0,
                "skipped": 0,
                "speed": None,
                "eta": None,
                "elapsed": 0,
            },
            "output_path": "",
            "files": [],
            "items": [],
            "error": "",
            "created_at": _now(),
            "started_at": None,
            "ended_at": None,
            "log": [],
            "stop_requested": False,
            "pid": None,
            "stop_reason": "",
        }
        if extra:
            job.update(extra)
        _jobs[job["id"]] = job
        copied = snapshot(job)
    _persist()
    _notify()
    return copied


def take_queued() -> dict[str, Any] | None:
    with _lock:
        waiting = [
            j
            for j in _jobs.values()
            if j.get("status") == "queued"
        ]
        waiting.sort(key=lambda j: j.get("created_at") or 0)
        if not waiting:
            return None
        job = waiting[0]
        job["status"] = "starting"
        job["started_at"] = _now()
        copied = snapshot(job)
    _notify()
    return copied


def update_job(job_id: str, **fields: Any) -> dict[str, Any] | None:
    quiet = bool(fields.pop("quiet", False))
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return None
        progress = fields.pop("progress", None)
        if progress:
            job["progress"].update(progress)
        log_line = fields.pop("log_line", None)
        if log_line:
            job["log"].append(str(log_line)[:2000])
            if len(job["log"]) > 120:
                job["log"] = job["log"][-80:]
        add_file = fields.pop("add_file", None)
        if add_file:
            path = str(add_file)
            if path not in job["files"]:
                job["files"].append(path)
                if not job.get("output_path"):
                    job["output_path"] = path
        add_item = fields.pop("add_item", None)
        if add_item:
            job["items"].append(add_item)
        job.update(fields)
        copied = snapshot(job)
    if not quiet:
        _notify()
    if copied.get("status") in TERMINAL:
        _persist()
    return copied


def touch() -> None:
    _notify()


def request_stop(job_id: str) -> dict[str, Any] | None:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return None
        if job["status"] in TERMINAL:
            return snapshot(job)
        job["stop_requested"] = True
        if job["status"] == "queued":
            job["status"] = "stopped"
            job["ended_at"] = _now()
        else:
            job["status"] = "stopping"
        copied = snapshot(job)
    _notify()
    return copied


def should_stop(job_id: str) -> bool:
    with _lock:
        job = _jobs.get(job_id)
        return bool(job and job.get("stop_requested"))


def requeue_job(job_id: str) -> dict[str, Any] | None:
    with _lock:
        job = _jobs.get(job_id)
        if not job or job.get("status") not in TERMINAL:
            return None
        job["status"] = "queued"
        job["error"] = ""
        job["stop_reason"] = ""
        job["stop_requested"] = False
        job["ended_at"] = None
        job["started_at"] = None
        job["pid"] = None
        if job.get("kind") == "scan":
            # The re-run lists the album again; keeping old items would double them.
            # Start again from the first page, or Load more's range loses the start.
            job["items"] = []
            job["range"] = job.pop("first_range", job.get("range") or "")
        else:
            job["files"] = []
            job["output_path"] = ""
            job["thumbnail"] = ""
        job["progress"] = {
            "percent": None,
            "current": 0,
            "total": None,
            "saved": 0,
            "skipped": 0,
            "speed": None,
            "eta": None,
            "elapsed": 0,
        }
        copied = snapshot(job)
    _persist()
    _notify()
    return copied


def extend_scan(job_id: str, range_spec: str) -> dict[str, Any] | None:
    """Re-queue a finished scan for its next range; new items append to it."""
    with _lock:
        job = _jobs.get(job_id)
        if not job or job.get("kind") != "scan" or job.get("status") not in TERMINAL:
            return None
        job.setdefault("first_range", job.get("range") or "")
        job["status"] = "queued"
        job["range"] = range_spec
        job["error"] = ""
        job["stop_reason"] = ""
        job["stop_requested"] = False
        job["ended_at"] = None
        job["started_at"] = None
        job["pid"] = None
        copied = snapshot(job)
    _persist()
    _notify()
    return copied


def append_log(job_id: str, line: str) -> None:
    update_job(job_id, log_line=line)


def clear_history() -> int:
    with _lock:
        ids = [jid for jid, job in _jobs.items() if job.get("status") in TERMINAL]
        for jid in ids:
            _jobs.pop(jid, None)
            _size_cache.pop(jid, None)
            _speed_state.pop(jid, None)
        count = len(ids)
    _persist()
    _notify()
    return count


def remove_job(job_id: str) -> bool:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return False
        if job.get("status") in ACTIVE and job.get("status") != "queued":
            return False
        _jobs.pop(job_id, None)
        _size_cache.pop(job_id, None)
        _speed_state.pop(job_id, None)
    _persist()
    _notify()
    return True


def subscribe(callback) -> None:
    with _lock:
        _listeners.append(callback)


def _notify() -> None:
    global _revision
    _revision += 1
    with _lock:
        if not _listeners:
            return
        listeners = list(_listeners)
        payload = [snapshot(j) for j in _jobs.values()]
    for cb in listeners:
        try:
            cb(payload)
        except Exception:
            pass


def _persist() -> None:
    ensure_dirs()
    with _lock:
        finished = sorted(
            (j for j in _jobs.values() if j.get("status") not in ACTIVE),
            key=lambda j: j.get("created_at") or 0,
        )
        for old in finished[: max(0, len(finished) - 120)]:
            _jobs.pop(old["id"], None)
            _size_cache.pop(old["id"], None)
            _speed_state.pop(old["id"], None)
        serializable = []
        for job in _jobs.values():
            item = deepcopy(job)
            item.pop("log", None)
            if item.get("kind") != "scan":
                item["items"] = []
            elif isinstance(item.get("items"), list) and len(item["items"]) > SCAN_ITEMS_KEPT:
                item["items"] = item["items"][:SCAN_ITEMS_KEPT]
            serializable.append(item)
    tmp = HISTORY_PATH.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(serializable[-120:], indent=2), encoding="utf-8")
        os.replace(tmp, HISTORY_PATH)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    try:
        scans = [j for j in serializable if j.get("kind") == "scan"]
        SCANS_PATH.write_text(json.dumps(scans[-8:], indent=2), encoding="utf-8")
    except OSError:
        pass


def load_history() -> None:
    ensure_dirs()
    with _lock:
        if _jobs:
            return
        for path in (HISTORY_PATH, SCANS_PATH):
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, list):
                continue
            for item in data:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                if item["id"] in _jobs:
                    continue
                if item.get("status") in ACTIVE:
                    item["status"] = "stopped"
                    item["error"] = item.get("error") or "Interrupted when Stills closed."
                    item["ended_at"] = item.get("ended_at") or _now()
                item.setdefault("log", [])
                item.setdefault("files", [])
                item.setdefault("items", [])
                item.setdefault("progress", {})
                _jobs[item["id"]] = item
