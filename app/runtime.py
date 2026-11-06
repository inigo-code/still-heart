from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from pathlib import Path

from app.paths import ROOT, is_frozen, resource_dir

_report_lock = threading.Lock()
_report_cache: dict | None = None
LOCAL_GDL = ROOT / "gallery-dl.exe"


def which(name: str) -> str | None:
    found = shutil.which(name)
    return str(Path(found)) if found else None


def find_ffmpeg() -> str | None:
    extras = [
        resource_dir() / "ffmpeg.exe",
        ROOT / "ffmpeg.exe",
        ROOT / "bin" / "ffmpeg.exe",
        Path(sys.executable).resolve().parent / "ffmpeg.exe",
        Path.home() / ".forgeneo" / "bin" / "ffmpeg.exe",
        Path(r"C:\ffmpeg\bin\ffmpeg.exe"),
    ]
    for candidate in extras:
        if candidate.is_file():
            return str(candidate)
    return which("ffmpeg")


def ensure_local_gallery_dl() -> Path | None:
    """Keep a copy next to StillHeart.exe so --update survives relaunch."""
    if LOCAL_GDL.is_file():
        return LOCAL_GDL
    bundled = resource_dir() / "gallery-dl.exe"
    if not bundled.is_file():
        bundled = ROOT / "bin" / "gallery-dl.exe"
    if bundled.is_file() and bundled.resolve() != LOCAL_GDL.resolve():
        try:
            ROOT.mkdir(parents=True, exist_ok=True)
            shutil.copy2(bundled, LOCAL_GDL)
            return LOCAL_GDL
        except OSError:
            return bundled if bundled.is_file() else None
    return bundled if bundled.is_file() else None


def find_gallery_dl() -> list[str]:
    extras = [
        LOCAL_GDL,
        ROOT / "bin" / "gallery-dl.exe",
        resource_dir() / "gallery-dl.exe",
        Path(sys.executable).resolve().parent / "gallery-dl.exe",
    ]
    for candidate in extras:
        if candidate.is_file():
            return [str(candidate)]
    found = which("gallery-dl")
    if found:
        return [found]
    # Never re-exec StillHeart.exe to run gallery-dl: a onefile unpack per
    # --version would hang the health check and spawn nested copies.
    if not is_frozen():
        return [sys.executable, "-u", "-m", "gallery_dl"]
    return []


def _version(cmd: list[str]) -> str:
    try:
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        proc = subprocess.run(
            cmd,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            creationflags=flags,
        )
        text = (proc.stdout or proc.stderr or "").strip().splitlines()
        return text[0].strip() if text else ""
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return ""


def gallery_dl_version() -> str:
    cmd = find_gallery_dl()
    if not cmd:
        return ""
    return _version(cmd + ["--version"])


def ffmpeg_version() -> str:
    path = find_ffmpeg()
    if not path:
        return ""
    line = _version([path, "-version"])
    return line.replace("ffmpeg version ", "").split(" ")[0] if line else ""


def runtime_report(*, quick: bool = False) -> dict:
    global _report_cache
    with _report_lock:
        if _report_cache is not None:
            return dict(_report_cache)
    gdl = find_gallery_dl()
    ffmpeg = find_ffmpeg()
    report = {
        "gallery_dl": " ".join(gdl) if gdl else "",
        "gallery_dl_version": "" if quick else (gallery_dl_version() if gdl else ""),
        "ffmpeg": ffmpeg or "",
        "ffmpeg_version": "" if quick else (ffmpeg_version() if ffmpeg else ""),
        "ok": bool(gdl),
    }
    if not quick:
        with _report_lock:
            _report_cache = dict(report)
    return report


def invalidate_runtime() -> None:
    global _report_cache
    with _report_lock:
        _report_cache = None


def can_self_update() -> bool:
    cmd = find_gallery_dl()
    if not cmd:
        return False
    name = Path(cmd[0]).name.lower()
    return name.endswith(".exe") and "python" not in name


def warm_runtime() -> None:
    try:
        ensure_local_gallery_dl()
        invalidate_runtime()
        runtime_report(quick=False)
    except Exception:
        pass
