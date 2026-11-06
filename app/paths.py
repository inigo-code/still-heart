from __future__ import annotations

import shutil
import socket
import sys
import tempfile
import time
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_dir() -> Path:
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


# Portable apps that write next to their exe. Sharing one folder mixes data\.
_FAMILY = frozenset({"stills.exe", "rushes.exe", "plates.exe"})
_OWN = {
    "rushes.exe": ("rushes.log", "rushes.log.old", "captures"),
    "stills.exe": ("stills.log", "stills.log.old", "downloads", "gallery-dl.exe"),
    "plates.exe": ("remover.log", "models", "tools"),
}
_DATA = {
    "rushes.exe": ("schedule.json", "diag.txt"),
    "stills.exe": ("archive.sqlite3", "extractors.json", "scans.json"),
    "plates.exe": (),
}
_BIN = {
    "rushes.exe": ("yt-dlp.exe", "yt-dlp.exe.old", "yt-dlp-download.exe"),
    "stills.exe": ("gallery-dl.exe", "gallery-dl.exe.old"),
    "plates.exe": (),
}


def _move_into(src: Path, dest: Path) -> None:
    if not src.exists() or dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        src.replace(dest)
    except OSError:
        pass


def _claim(folder: Path, home: Path, exe_name: str) -> None:
    """Move this app's files into its own folder the first time that folder is made."""
    if home.exists():
        return
    foreign = False
    data = folder / "data"
    for other, markers in _OWN.items():
        if other == exe_name:
            continue
        if any((folder / marker).exists() for marker in markers):
            foreign = True
        if any((data / marker).exists() for marker in _DATA[other]):
            foreign = True
    own = any((folder / marker).exists() for marker in _OWN[exe_name]) or any(
        (data / marker).exists() for marker in _DATA[exe_name]
    )
    for marker in _OWN[exe_name]:
        _move_into(folder / marker, home / marker)
    if own and not foreign:
        _move_into(data, home / "data")
    for name in _BIN[exe_name]:
        _move_into(folder / "bin" / name, home / "bin" / name)


def portable_home(exe: Path) -> Path:
    """Folder this portable exe should write into.

    Stills.exe always uses a Stills folder. The same rule applies to Rushes and
    Plates, whether or not the other apps are nearby. An exe that already sits
    inside a folder of its own name uses that folder.
    """
    exe = exe.resolve()
    folder = exe.parent
    if exe.name.lower() not in _FAMILY or folder.name.lower() == exe.stem.lower():
        return folder
    home = folder / exe.stem
    if home.exists() and not home.is_dir():
        return folder
    _claim(folder, home, exe.name.lower())
    try:
        home.mkdir(parents=True, exist_ok=True)
    except OSError:
        return folder
    return home


def user_dir() -> Path:
    if is_frozen():
        return portable_home(Path(sys.executable))
    return Path(__file__).resolve().parent.parent


ROOT = user_dir()
WEB_DIR = resource_dir() / "web"
DATA_DIR = ROOT / "data"
DEFAULT_DOWNLOADS = ROOT / "downloads"
SETTINGS_PATH = DATA_DIR / "settings.json"
HISTORY_PATH = DATA_DIR / "history.json"
ARCHIVE_PATH = DATA_DIR / "archive.sqlite3"
EXTRACTORS_PATH = DATA_DIR / "extractors.json"
SCANS_PATH = DATA_DIR / "scans.json"
LOG_PATH = ROOT / "stills.log"


def ensure_dirs(downloads: Path | None = None) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    target = downloads or DEFAULT_DOWNLOADS
    target.mkdir(parents=True, exist_ok=True)


def free_port(preferred: tuple[int, ...] = (8775, 8776, 8777, 18775)) -> int:
    for port in preferred:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", port))
            sock.close()
            return port
        except OSError:
            sock.close()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _is_stills_unpack(d: Path) -> bool:
    return (d / "web" / "stills.ico").is_file() and (d / "web" / "app.js").is_file()


def prune_stale_unpacks(temp: Path, current: str | None, cutoff: float) -> int:
    """Delete old Stills ``_MEI*`` unpacks in ``temp`` that no process is using.

    Other PyInstaller apps share the ``_MEI`` prefix, so only folders holding
    Stills' own files are touched. A folder that a running copy still has files
    open in can't be renamed on Windows; renaming first means a live unpack is
    skipped instead of half-deleted.
    """
    removed = 0
    try:
        candidates = list(temp.glob("_MEI*"))
    except OSError:
        return 0
    for d in candidates:
        try:
            if not d.is_dir() or d.name.endswith("-stale"):
                continue
            if current and str(d.resolve()) == current:
                continue
            if d.stat().st_mtime >= cutoff or not _is_stills_unpack(d):
                continue
            doomed = d.with_name(d.name + "-stale")
            d.rename(doomed)
        except OSError:
            continue
        shutil.rmtree(doomed, ignore_errors=True)
        removed += 1
    return removed


def prune_temp_extractions() -> int:
    """Reclaim disk leaked by stale PyInstaller onefile unpacks of Stills."""
    if not getattr(sys, "frozen", False):
        return 0
    current = getattr(sys, "_MEIPASS", None)
    if current:
        current = str(Path(current).resolve())
    return prune_stale_unpacks(Path(tempfile.gettempdir()), current, time.time() - 24 * 3600)
