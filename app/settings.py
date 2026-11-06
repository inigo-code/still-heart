from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from threading import Lock
from urllib.parse import urlparse

from app.paths import ARCHIVE_PATH, DEFAULT_DOWNLOADS, SETTINGS_PATH, ensure_dirs

DEFAULTS = {
    "accepted_terms": False,
    "output_dir": str(DEFAULT_DOWNLOADS),
    "max_active": 3,
    "min_free_gb": 1.0,
    "warn_free_gb": 3.0,
    "cookies_file": "",
    "cookies_browser": "",
    "proxy": "",
    "filename_preset": "original",
    "filename_custom": "{filename}.{extension}",
    "directory_preset": "site_author",
    "directory_custom": "{category}/{author}",
    "use_archive": True,
    "write_metadata": False,
    "write_tags": False,
    "filter_mode": "all",
    "min_width": 0,
    "sleep_request": 0.8,
    "rate": "",
    "retries": 4,
    "scan_limit": 240,
    "convert_ugoira": "mp4",
    "skip_existing": True,
}

FILENAME_PRESETS = {
    "original": "{filename}.{extension}",
    "numbered": "{num:>04}_{filename}.{extension}",
    "titled": "{title}_{num:>03}.{extension}",
    "id": "{id}_{num:>03}.{extension}",
    "custom": None,
}

DIRECTORY_PRESETS = {
    "site_author": ["{category}", "{author|uploader|user[name]|user|artist}"],
    "site_album": ["{category}", "{title|album|chapter|search_tags|id}"],
    "site_date": ["{category}", "{date:%Y-%m}", "{author|uploader|user[name]|user}"],
    "flat": ["{category}"],
    "custom": None,
}

BROWSERS = ("", "chrome", "chromium", "edge", "firefox", "brave", "opera", "vivaldi")
FILTER_MODES = ("all", "images", "videos")
UGOIRA = ("none", "mp4", "webm", "gif")

_lock = Lock()
_cache: dict | None = None
_RE_RATE = re.compile(r"^\d+(\.\d+)?[kmg]?$", re.I)


def _clean_proxy(value) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https", "socks4", "socks5", "socks5h"} or not parsed.hostname:
        raise ValueError("Proxy must look like http://127.0.0.1:7890 or socks5://127.0.0.1:1080")
    return text


def _clean_rate(value) -> str:
    text = "" if value is None else re.sub(r"\s+", "", str(value))
    if not text or text.lower() in {"off", "none", "0"}:
        return ""
    if not _RE_RATE.match(text):
        raise ValueError("Speed limit must look like 500K or 2M, or be left blank")
    return text


def _read() -> tuple[dict, bool]:
    if not SETTINGS_PATH.exists():
        return deepcopy(DEFAULTS), True
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return deepcopy(DEFAULTS), True
    merged = deepcopy(DEFAULTS)
    merged.update({k: v for k, v in data.items() if k in DEFAULTS})
    persist = any(key not in data for key in DEFAULTS)
    return merged, persist


def load() -> dict:
    global _cache
    with _lock:
        if _cache is None:
            ensure_dirs()
            current, persist = _read()
            _cache = current
            if persist:
                _write(current)
        return deepcopy(_cache)


def _write(current: dict) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(current, indent=2), encoding="utf-8")


def _clamp(value, lo: int | float, hi: int | float, cast, name: str):
    try:
        return max(lo, min(hi, cast(value)))
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number") from None


def save(updates: dict) -> dict:
    global _cache
    with _lock:
        current = deepcopy(_cache) if _cache is not None else _read()[0]
        for key, value in updates.items():
            if key not in DEFAULTS:
                continue
            if key == "output_dir":
                path = Path(str(value)).expanduser()
                try:
                    path.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    raise ValueError(f"Cannot use that folder: {exc}") from exc
                current[key] = str(path.resolve())
            elif key == "max_active":
                current[key] = _clamp(value, 1, 8, int, "Concurrent albums")
            elif key in {"min_free_gb", "warn_free_gb"}:
                current[key] = _clamp(value, 0.0, 100000.0, float, "Free space limit")
            elif key == "min_width":
                current[key] = _clamp(value, 0, 20000, int, "Minimum width")
            elif key == "sleep_request":
                current[key] = _clamp(value, 0.0, 30.0, float, "Pause between requests")
            elif key == "retries":
                current[key] = _clamp(value, 0, 20, int, "Retries")
            elif key == "scan_limit":
                current[key] = _clamp(value, 20, 2000, int, "Scan preview size")
            elif key == "accepted_terms":
                current[key] = bool(value)
            elif key in {"use_archive", "write_metadata", "write_tags", "skip_existing"}:
                current[key] = bool(value)
            elif key == "filename_preset":
                current[key] = value if value in FILENAME_PRESETS else "original"
            elif key == "directory_preset":
                current[key] = value if value in DIRECTORY_PRESETS else "site_author"
            elif key == "filter_mode":
                current[key] = value if value in FILTER_MODES else "all"
            elif key == "convert_ugoira":
                current[key] = value if value in UGOIRA else "mp4"
            elif key == "cookies_browser":
                current[key] = value if value in BROWSERS else ""
            elif key == "proxy":
                current[key] = _clean_proxy(value)
            elif key == "rate":
                current[key] = _clean_rate(value)
            else:
                current[key] = "" if value is None else str(value).strip()
        _write(current)
        _cache = current
        return deepcopy(current)


def output_dir() -> Path:
    path = Path(load().get("output_dir") or DEFAULT_DOWNLOADS).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def archive_path() -> Path | None:
    cfg = load()
    if not cfg.get("use_archive"):
        return None
    ARCHIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
    return ARCHIVE_PATH


def filename_format(cfg: dict | None = None) -> str:
    cfg = cfg or load()
    preset = cfg.get("filename_preset") or "original"
    if preset == "custom":
        return (cfg.get("filename_custom") or "").strip() or FILENAME_PRESETS["original"]
    return FILENAME_PRESETS.get(preset) or FILENAME_PRESETS["original"]


def directory_format(cfg: dict | None = None) -> list[str]:
    cfg = cfg or load()
    preset = cfg.get("directory_preset") or "site_author"
    if preset == "custom":
        raw = (cfg.get("directory_custom") or "").replace("\\", "/").strip("/")
        parts = [p for p in raw.split("/") if p]
        return parts or ["{category}"]
    return list(DIRECTORY_PRESETS.get(preset) or DIRECTORY_PRESETS["site_author"])
