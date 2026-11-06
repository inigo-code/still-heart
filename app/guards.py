from __future__ import annotations

import shutil
import threading
import time
from copy import deepcopy
from pathlib import Path

from app import settings

_lock = threading.Lock()
_state = {
    "free": 0,
    "total": 0,
    "used": 0,
    "free_gb": 0.0,
    "total_gb": 0.0,
    "percent_used": 0.0,
    "level": "ok",
    "blocked": False,
    "alerts": [],
}
_started = False
GB = 1024 ** 3
_start_lock = threading.Lock()
_out_dir: Path | None = None


def disk_for(path: Path) -> dict:
    usage = shutil.disk_usage(path)
    free_gb = usage.free / GB
    total_gb = usage.total / GB
    used = usage.total - usage.free
    return {
        "free": usage.free,
        "total": usage.total,
        "used": used,
        "free_gb": round(free_gb, 2),
        "total_gb": round(total_gb, 2) if usage.total else 0,
        "percent_used": round(100 * used / usage.total, 1) if usage.total else 0,
    }


def snapshot() -> dict:
    with _lock:
        return deepcopy(_state)


def _set_state(**fields) -> None:
    with _lock:
        _state.update(fields)


def _output_dir() -> Path:
    global _out_dir
    cfg_dir = str(settings.load().get("output_dir") or "")
    if _out_dir is None or str(_out_dir) != cfg_dir:
        path = Path(cfg_dir).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        _out_dir = path.resolve()
    return _out_dir


def _tick() -> None:
    cfg = settings.load()
    try:
        out = _output_dir()
        disk = disk_for(out)
    except OSError:
        # An unplugged drive must not stop the app opening. Hold the queue instead.
        _set_state(
            level="critical",
            blocked=True,
            alerts=["The download folder isn't available. Plug the drive back in, or choose another folder in Settings."],
        )
        return
    min_free = float(cfg.get("min_free_gb") or 0)
    warn_free = float(cfg.get("warn_free_gb") or 0)
    alerts: list[str] = []
    level = "ok"
    blocked = False
    if min_free and disk["free_gb"] < min_free:
        level = "critical"
        blocked = True
        alerts.append(f"Only {disk['free_gb']} GB free — downloads paused until you free space.")
    elif warn_free and disk["free_gb"] < warn_free:
        level = "warn"
        alerts.append(f"{disk['free_gb']} GB free on the download drive.")
    _set_state(**disk, level=level, blocked=blocked, alerts=alerts)


def start() -> None:
    global _started
    with _start_lock:
        if _started:
            return
        _started = True

    def loop() -> None:
        while True:
            try:
                _tick()
            except Exception:
                pass
            time.sleep(4)

    _tick()
    threading.Thread(target=loop, daemon=True).start()
