from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from app import jobs, settings
from app.paths import DATA_DIR, EXTRACTORS_PATH, ensure_dirs
from app.runtime import (
    LOCAL_GDL,
    can_self_update,
    ensure_local_gallery_dl,
    find_ffmpeg,
    find_gallery_dl,
    gallery_dl_version,
    invalidate_runtime,
)

IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "gif", "bmp", "avif", "jxl", "tif", "tiff", "heic"}
VIDEO_EXTS = {"mp4", "webm", "mkv", "mov", "m4v", "avi"}
GIF_EXTS = {"gif"}

RE_PROGRESS_LINE = re.compile(r"^(None|\d+)/(None|\d+)$")
RE_PREPARE = re.compile(r"^(\d+)/(\d+)$")
RE_HASH_PATH = re.compile(r"^#\s+(.+)$")
RE_EXTRACTOR = re.compile(r"^\[([^\]]+)\]")

_procs: dict[str, subprocess.Popen] = {}
_proc_lock = threading.Lock()

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


_ERROR_HINTS = (
    ("401", "Instagram/login wall — set a cookies.txt or your browser in Settings"),
    ("unauthorized", "Login wall — set a cookies.txt or your browser in Settings"),
    ("redirect to home", "The site rejected your session — log out and log back in inside your browser, then retry. If it keeps failing, your IP may be flagged: try a proxy in Settings."),
    ("403 forbidden", "The site refused the request (cookies expired or bot check) — re-login in your browser, or use a proxy in Settings"),
    ("challenge", "Site sent a bot challenge (Cloudflare) — retry later, or use cookies from your browser"),
    ("unsupported url", "No extractor for this URL — check the Sites tab for the correct URL format"),
    ("rate-limit", "Rate limited by the site — wait a bit, raise 'Pause between requests' in Settings"),
    ("429", "Rate limited by the site — wait a bit, raise 'Pause between requests' in Settings"),
    ("login required", "Login wall — set a cookies.txt or your browser in Settings"),
)


def _friendly_error(text: str) -> str:
    t = (text or "").strip()
    low = t.lower()
    for needle, hint in _ERROR_HINTS:
        if needle in low:
            return f"{t[:280]} — {hint}"
    return t


def range_start(range_spec: str) -> int:
    text = (range_spec or "").strip()
    if not text:
        return 1
    first = text.split(",")[0].split("-")[0].strip()
    try:
        return max(1, int(first))
    except ValueError:
        return 1


def compress_range(nums: list[int]) -> str:
    if not nums:
        return ""
    ordered = sorted({int(n) for n in nums if int(n) > 0})
    if not ordered:
        return ""
    parts: list[str] = []
    start = prev = ordered[0]
    for n in ordered[1:]:
        if n == prev + 1:
            prev = n
            continue
        parts.append(f"{start}-{prev}" if start != prev else str(start))
        start = prev = n
    parts.append(f"{start}-{prev}" if start != prev else str(start))
    return ",".join(parts)


def _first(*values: Any) -> Any:
    for value in values:
        if value is None or value == "":
            continue
        return value
    return None


def _author(item: dict) -> str:
    for key in ("author", "uploader", "artist", "username", "user_name"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            name = _first(value.get("name"), value.get("username"), value.get("nick"), value.get("display_name"))
            if name:
                return str(name)
    user = item.get("user")
    if isinstance(user, dict):
        name = _first(user.get("name"), user.get("username"), user.get("nick"), user.get("display_name"))
        if name:
            return str(name)
    if isinstance(user, str) and user.strip():
        return user.strip()
    return ""


def _kind(ext: str) -> str:
    if ext in VIDEO_EXTS:
        return "video"
    if ext in GIF_EXTS:
        return "gif"
    if ext in IMAGE_EXTS:
        return "image"
    return "file"


def simplify_item(raw: Any, index: int) -> dict[str, Any] | None:
    item = raw
    if isinstance(raw, (list, tuple)) and len(raw) >= 2 and isinstance(raw[1], dict):
        item = dict(raw[1])
        item.setdefault("url", raw[0])
    if not isinstance(item, dict):
        return None
    ext = str(item.get("extension") or item.get("ext") or "").lower().lstrip(".")
    url = str(_first(item.get("url"), item.get("file"), item.get("image"), item.get("display_url")) or "")
    thumb = str(
        _first(
            item.get("thumbnail"),
            item.get("preview"),
            item.get("display_url"),
            url if ext in IMAGE_EXTS | GIF_EXTS else "",
        )
        or ""
    )
    width = item.get("width")
    height = item.get("height")
    if isinstance(item.get("file"), dict):
        width = width or item["file"].get("width")
        height = height or item["file"].get("height")
        ext = ext or str(item["file"].get("extension") or "").lower().lstrip(".")
        url = url or str(item["file"].get("url") or "")
    filename = str(_first(item.get("filename"), item.get("id"), item.get("name"), f"file-{index}") or f"file-{index}")
    return {
        "index": index,
        "url": url,
        "thumb": thumb,
        "filename": filename,
        "extension": ext,
        "title": str(_first(item.get("title"), item.get("search_tags"), item.get("description"), "") or ""),
        "author": _author(item),
        "category": str(item.get("category") or ""),
        "subcategory": str(item.get("subcategory") or ""),
        "width": int(width) if isinstance(width, (int, float)) else None,
        "height": int(height) if isinstance(height, (int, float)) else None,
        "filesize": item.get("filesize") or item.get("size"),
        "kind": _kind(ext),
    }


def _load_json_payload(text: str) -> Any:
    blob = (text or "").strip()
    if not blob:
        return []
    start_arr = blob.find("[")
    start_obj = blob.find("{")
    payload = blob
    if start_arr != -1 and (start_obj == -1 or start_arr <= start_obj):
        payload = blob[start_arr:]
    elif start_obj != -1:
        payload = blob[start_obj:]
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        rows = []
        for line in blob.splitlines():
            line = line.strip()
            if not line.startswith("{") and not line.startswith("["):
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows


def parse_dump_errors(text: str) -> list[str]:
    data = _load_json_payload(text)
    if not isinstance(data, list):
        return []
    errors = []
    for raw in data:
        if isinstance(raw, (list, tuple)) and raw and raw[0] == -1:
            info = raw[1] if len(raw) > 1 and isinstance(raw[1], dict) else {}
            msg = str(info.get("message") or info.get("error") or raw[1])
            if msg:
                errors.append(msg[:400])
    return errors


def item_from_message(raw: Any, index: int) -> dict[str, Any] | None:
    payload: Any = raw
    child = False
    if isinstance(raw, (list, tuple)) and raw:
        kind = raw[0]
        if kind == -1:
            return None
        if kind in {3, 6} and len(raw) >= 3:
            payload = raw[2] if isinstance(raw[2], dict) else {}
            payload = dict(payload)
            payload.setdefault("url", raw[1])
            child = kind == 6
        elif kind == 2:
            return None
        elif len(raw) >= 2 and isinstance(raw[1], dict):
            payload = dict(raw[1])
            if not payload.get("url") and isinstance(raw[0], str):
                payload["url"] = raw[0]
        else:
            return None
    item = simplify_item(payload, index)
    if item and child:
        # A queued child URL (sub-album, external post). gallery-dl's file
        # range does not count these, so they are downloaded by URL instead.
        item["child"] = True
        item["kind"] = "album"
    return item


def number_items(items: list[dict[str, Any]], start_index: int, start_pos: int) -> int:
    """Give each item a unique preview index and each file its gallery-dl position.

    ``pos`` is what ``--range`` counts: files only, after the filter. Returns the
    next free position.
    """
    pos = start_pos
    for offset, item in enumerate(items):
        item["index"] = start_index + offset
        if item.get("child"):
            item["pos"] = None
        else:
            item["pos"] = pos
            pos += 1
    return pos


def parse_dump(text: str) -> list[dict[str, Any]]:
    data = _load_json_payload(text)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    items: list[dict[str, Any]] = []
    for raw in data:
        simple = item_from_message(raw, len(items) + 1)
        if simple and (simple.get("url") or simple.get("filename")):
            items.append(simple)
    return items


def item_pos(item: dict[str, Any]) -> int | None:
    """gallery-dl file position of a scanned item (older scans lack ``pos``)."""
    if item.get("child"):
        return None
    pos = item.get("pos", item.get("index"))
    return int(pos) if isinstance(pos, (int, float)) and pos > 0 else None


def _filter_expression(cfg: dict) -> str:
    conds: list[str] = []
    mode = cfg.get("filter_mode") or "all"
    if mode == "images":
        conds.append(
            "extension in ('jpg','jpeg','png','webp','gif','bmp','avif','jxl','tif','tiff','heic')"
        )
    elif mode == "videos":
        conds.append("extension in ('mp4','webm','mkv','mov','m4v','avi')")
    min_width = int(cfg.get("min_width") or 0)
    if min_width > 0:
        # Keep files whose width the site doesn't report; a bare `width >= N`
        # would make gallery-dl drop them all.
        conds.append(f"(not locals().get('width') or width >= {min_width})")
    return " and ".join(conds)


def write_config(path: Path, cfg: dict, *, scan: bool = False, range_spec: str = "") -> None:
    extractor: dict[str, Any] = {
        "base-directory": str(settings.output_dir()).replace("\\", "/"),
        "filename": settings.filename_format(cfg),
        "directory": settings.directory_format(cfg),
        "retries": int(cfg.get("retries") or 4),
        "timeout": 30,
        "sleep-request": float(cfg.get("sleep_request") or 0) or None,
        "archive": str(settings.archive_path() or "").replace("\\", "/") or None,
        "skip": bool(cfg.get("skip_existing", True)),
        "input": False,
    }
    cookies_file = (cfg.get("cookies_file") or "").strip()
    cookies_browser = (cfg.get("cookies_browser") or "").strip()
    if cookies_file and Path(cookies_file).is_file():
        extractor["cookies"] = cookies_file.replace("\\", "/")
    elif cookies_browser:
        extractor["cookies"] = [cookies_browser]
    proxy = (cfg.get("proxy") or "").strip()
    if proxy:
        extractor["proxy"] = proxy
    if range_spec:
        extractor["image-range"] = range_spec
    # gallery-dl applies the filter before the range, so scans must use the
    # same filter or their positions won't match "Download selected" ranges.
    # ("filter" is not a gallery-dl option; "image-filter" is.)
    filt = _filter_expression(cfg)
    if filt:
        extractor["image-filter"] = filt
    if not extractor.get("archive"):
        extractor.pop("archive", None)
    if not extractor.get("sleep-request"):
        extractor.pop("sleep-request", None)

    downloader: dict[str, Any] = {
        "mtime": True,
        "retries": int(cfg.get("retries") or 4),
        "timeout": 30,
        "progress": not scan,
    }
    rate = (cfg.get("rate") or "").strip()
    if rate:
        downloader["rate"] = rate

    payload = {
        "extractor": extractor,
        "downloader": downloader,
        "output": {
            "mode": "pipe",
            "ansi": False,
            "progress": not scan,
            "skip": True,
            "short": False,
            "jsonl": bool(scan),
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _cmd_base(cfg: dict, config_path: Path, *, scan: bool) -> list[str]:
    gdl = find_gallery_dl()
    if not gdl:
        raise RuntimeError("gallery-dl was not found. Place gallery-dl.exe next to Stills or in bin\\.")
    cmd = list(gdl) + [
        "--ignore-config",
        "-c",
        str(config_path),
        "--windows-filenames",
    ]
    ugoira = cfg.get("convert_ugoira") or "none"
    if find_ffmpeg() and ugoira != "none" and not scan:
        cmd += ["--ugoira", ugoira]
    if cfg.get("write_metadata") and not scan:
        cmd.append("--write-metadata")
    if cfg.get("write_tags") and not scan:
        cmd.append("--write-tags")
    if scan:
        cmd.append("--dump-json")
    else:
        cmd += ["--Print", "prepare:{num}/{count}"]
    return cmd


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        folder = str(Path(ffmpeg).parent)
        env["PATH"] = folder + os.pathsep + env.get("PATH", "")
    return env


def _spawn(cmd: list[str]) -> subprocess.Popen:
    kwargs: dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "stdin": subprocess.DEVNULL,
        "env": _env(),
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": 1,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = CREATE_NO_WINDOW
    return subprocess.Popen(cmd, **kwargs)


def kill_job(job_id: str) -> None:
    jobs.request_stop(job_id)
    with _proc_lock:
        proc = _procs.get(job_id)
    if not proc or proc.poll() is not None:
        return
    pid = proc.pid
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                creationflags=CREATE_NO_WINDOW,
            )
        else:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
    except OSError:
        pass


def kill_all() -> None:
    with _proc_lock:
        ids = list(_procs)
    for job_id in ids:
        kill_job(job_id)


def _register(job_id: str, proc: subprocess.Popen) -> None:
    with _proc_lock:
        _procs[job_id] = proc
    jobs.update_job(job_id, pid=proc.pid)


def _unregister(job_id: str) -> None:
    with _proc_lock:
        _procs.pop(job_id, None)


def _under_base(base: Path, candidate: Path) -> bool:
    b = os.path.normcase(str(base))
    c = os.path.normcase(str(candidate))
    return c == b or c.startswith(b + os.sep)


def _consume_download_line(job_id: str, line: str, started: float, base: Path) -> None:
    text = line.strip()
    if not text:
        return
    fields: dict[str, Any] = {"progress": {"elapsed": time.time() - started}}
    # "--Print prepare:{num}/{count}" progress lines ("3/40", "None/None")
    if RE_PROGRESS_LINE.match(text):
        prep = RE_PREPARE.match(text)
        if prep:
            cur, tot = int(prep.group(1)), int(prep.group(2))
            if tot > 0:
                fields["progress"]["current"] = cur
                fields["progress"]["total"] = tot
                fields["progress"]["percent"] = round(100 * cur / tot, 1)
                jobs.update_job(job_id, **fields)
        return
    # "# path" = skipped, plain absolute path = downloaded (gallery-dl pipe output)
    path_match = RE_HASH_PATH.match(text)
    is_skip = bool(path_match)
    raw_path = path_match.group(1).strip() if path_match else text
    candidate = Path(raw_path)
    if not (candidate.is_absolute() and _under_base(base, candidate)):
        jobs.append_log(job_id, text[:500])
        jobs.update_job(job_id, quiet=True, **fields)
        return
    path = str(candidate)
    fields["add_file"] = path
    current = jobs.peek(job_id) or {}
    if is_skip:
        fields["progress"]["skipped"] = int((current.get("progress") or {}).get("skipped") or 0) + 1
    else:
        saved = int((current.get("progress") or {}).get("saved") or 0) + 1
        fields["progress"]["saved"] = saved
        fields["progress"]["current"] = saved
        if not current.get("output_path"):
            fields["output_path"] = path
        if not current.get("thumbnail") and candidate.suffix.lower().lstrip(".") in IMAGE_EXTS | GIF_EXTS:
            fields["thumbnail"] = path
        title = current.get("title") or ""
        if not title or title == current.get("url"):
            fields["title"] = candidate.stem[:80]
    ext_match = RE_EXTRACTOR.match(text)
    if ext_match and ext_match.group(1) not in {"download", "downloader.http", "gallery-dl", "urllib3"}:
        if not current.get("extractor"):
            fields["extractor"] = ext_match.group(1)
    jobs.update_job(job_id, **fields)


def download_status(code: int | None, saved: int, skipped: int = 0) -> str | None:
    """Final status for a finished download, or None if it failed outright.

    gallery-dl exit codes are bit flags (4 HTTP, 8 not found, 16 auth, ...), so
    any non-zero code after some files went through is a partial success.
    """
    if code in (0, None):
        return "done"
    if saved or skipped:
        return "partial"
    return None


def run_job(job_id: str) -> None:
    job = jobs.get_job(job_id)
    if not job:
        return
    cfg = settings.load()
    kind = job.get("kind") or "download"
    ensure_dirs()
    config_path = DATA_DIR / "jobs" / f"{job_id}.json"
    range_spec = (job.get("range") or "").strip()
    started = time.time()
    base = settings.output_dir()
    try:
        write_config(config_path, cfg, scan=(kind == "scan"), range_spec=range_spec)
        cmd = _cmd_base(cfg, config_path, scan=(kind == "scan"))
        if range_spec:
            cmd += ["--range", range_spec]
        cmd.append(job["url"])
        jobs.update_job(job_id, status="scanning" if kind == "scan" else "downloading")
        jobs.append_log(job_id, " ".join(cmd[-6:]))
        proc = _spawn(cmd)
        _register(job_id, proc)

        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []
        # "Load more" re-runs a scan with the next range and appends to it.
        existing = (jobs.get_items(job_id) or []) if kind == "scan" else []
        scan_index = max(int(it.get("index") or 0) for it in existing) + 1 if existing else range_start(range_spec)
        file_pos = range_start(range_spec)
        # --range doesn't limit child URLs, so every page lists them again.
        seen_children = {it.get("url") for it in existing if it.get("child")}
        last_flush = time.time()
        found = 0

        def is_repeat_child(item: dict) -> bool:
            if not item.get("child"):
                return False
            if item.get("url") in seen_children:
                return True
            seen_children.add(item.get("url"))
            return False

        def flush_scan() -> None:
            nonlocal last_flush
            last_flush = time.time()
            jobs.update_job(
                job_id,
                progress={
                    "current": found,
                    "saved": found,
                    "elapsed": time.time() - started,
                },
            )

        def read_out() -> None:
            nonlocal scan_index, file_pos, found, last_flush
            if proc.stdout is None:
                return
            for line in proc.stdout:
                stdout_chunks.append(line)
                if kind == "scan":
                    text = line.strip()
                    if text:
                        try:
                            raw = json.loads(text)
                        except json.JSONDecodeError:
                            raw = None
                        if raw is not None:
                            item = item_from_message(raw, scan_index)
                            if item and (item.get("url") or item.get("filename")) and not is_repeat_child(item):
                                file_pos = number_items([item], scan_index, file_pos)
                                jobs.update_job(job_id, add_item=item, quiet=True)
                                if found == 0 and not existing:
                                    jobs.update_job(
                                        job_id,
                                        quiet=True,
                                        thumbnail=item.get("thumb") or "",
                                        author=item.get("author") or "",
                                        extractor=item.get("category") or "",
                                        title=item.get("title") or item.get("author") or job.get("title"),
                                    )
                                scan_index += 1
                                found += 1
                                if found % 8 == 0 or time.time() - last_flush > 0.25:
                                    flush_scan()
                else:
                    _consume_download_line(job_id, line, started, base)
                if jobs.should_stop(job_id):
                    break

        def read_err() -> None:
            if proc.stderr is None:
                return
            n = 0
            for line in proc.stderr:
                stderr_chunks.append(line)
                text = line.rstrip("\n")
                if text:
                    low = text.lower()
                    interesting = "[error]" in low or "[warning]" in low or "extracting" in low
                    n += 1
                    if interesting or n % 6 == 0:
                        jobs.update_job(job_id, log_line=text[:240], quiet=not interesting)
                    ext_match = RE_EXTRACTOR.match(text.strip())
                    if ext_match and not (jobs.peek(job_id) or {}).get("extractor"):
                        name = ext_match.group(1)
                        if name not in {"download", "downloader.http", "gallery-dl"}:
                            jobs.update_job(job_id, extractor=name)
                if jobs.should_stop(job_id):
                    break

        t_out = threading.Thread(target=read_out, daemon=True)
        t_err = threading.Thread(target=read_err, daemon=True)
        t_out.start()
        t_err.start()
        last_tick = time.time()
        while proc.poll() is None:
            if jobs.should_stop(job_id):
                kill_job(job_id)
                break
            now = time.time()
            if now - last_tick >= 0.8:
                jobs.update_job(job_id, progress={"elapsed": now - started})
                last_tick = now
            time.sleep(0.2)
        t_out.join(timeout=4)
        t_err.join(timeout=4)
        code = proc.poll()
        stdout = "".join(stdout_chunks)
        stderr = "".join(stderr_chunks)

        if jobs.should_stop(job_id):
            jobs.update_job(job_id, status="stopped", ended_at=time.time(), pid=None)
            return

        if kind == "scan":
            items = jobs.get_items(job_id) or []
            if len(items) == len(existing):
                fresh = [it for it in (parse_dump(stdout) or parse_dump(stderr)) if not is_repeat_child(it)]
                number_items(fresh, scan_index, file_pos)
                for item in fresh:
                    jobs.update_job(job_id, add_item=item, quiet=True)
                items = jobs.get_items(job_id) or []
                if existing and not fresh:
                    jobs.append_log(job_id, "No more files in this album.")
            errors = parse_dump_errors(stdout) or parse_dump_errors(stderr)
            if not items:
                err = errors[0] if errors else ((stderr or stdout).strip().splitlines() or ["Scan returned no files"])[-1][:400]
                jobs.update_job(job_id, status="error", error=_friendly_error(err), ended_at=time.time(), pid=None)
                return
            partial = bool(errors) or code not in (0, None)
            title = job.get("title") or job.get("url")
            author = ""
            thumb = ""
            extractor = (jobs.peek(job_id) or {}).get("extractor") or ""
            if items:
                author = items[0].get("author") or ""
                thumb = items[0].get("thumb") or ""
                extractor = extractor or items[0].get("category") or ""
                title = items[0].get("title") or author or extractor or title
            jobs.update_job(
                job_id,
                status="partial" if partial else "done",
                error=errors[0] if errors else "",
                items=items,
                title=title,
                author=author,
                thumbnail=thumb,
                extractor=extractor,
                ended_at=time.time(),
                pid=None,
                progress={
                    "current": len(items),
                    "total": len(items),
                    "saved": len(items),
                    "percent": 100,
                    "elapsed": time.time() - started,
                },
            )
            return

        current = jobs.peek(job_id) or job
        progress = current.get("progress") or {}
        status = download_status(code, int(progress.get("saved") or 0), int(progress.get("skipped") or 0))
        if status:
            jobs.update_job(
                job_id,
                status=status,
                ended_at=time.time(),
                pid=None,
                progress={"percent": 100 if status == "done" else (current.get("progress") or {}).get("percent")},
            )
            return
        err = ""
        for line in reversed((stderr + "\n" + stdout).splitlines()):
            if line.strip() and ("error" in line.lower() or "exception" in line.lower()):
                err = line.strip()[:400]
                break
        if not err:
            err = (stderr or stdout).strip().splitlines()[-1][:400] if (stderr or stdout).strip() else f"gallery-dl exited {code}"
        jobs.update_job(job_id, status="error", error=_friendly_error(err), ended_at=time.time(), pid=None)
    except Exception as exc:
        jobs.update_job(job_id, status="error", error=str(exc)[:400], ended_at=time.time(), pid=None)
    finally:
        _unregister(job_id)
        try:
            config_path.unlink(missing_ok=True)
        except OSError:
            pass


def parse_extractors(text: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    current = {"category": "", "subcategory": "", "example": "", "label": ""}

    def flush() -> None:
        nonlocal current
        if current.get("category"):
            items.append(current)
        current = {"category": "", "subcategory": "", "example": "", "label": ""}

    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            flush()
            continue
        if line.lower().startswith("category:"):
            cat = re.search(r"Category:\s*([^\s,]+)", line, re.I)
            sub = re.search(r"Subcategory:\s*([^\s,]+)", line, re.I)
            current["category"] = cat.group(1) if cat else ""
            current["subcategory"] = sub.group(1) if sub else ""
            current["label"] = (
                f"{current['category']}:{current['subcategory']}" if current["subcategory"] else current["category"]
            )
            continue
        if line.lower().startswith("example"):
            current["example"] = line.split(":", 1)[-1].strip() if ":" in line else ""
    flush()
    seen: set[tuple[str, str]] = set()
    unique = []
    for item in items:
        key = (item.get("category") or "", item.get("subcategory") or "")
        if not key[0] or key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def load_extractors(force: bool = False) -> list[dict[str, str]]:
    ensure_dirs()
    if not force and EXTRACTORS_PATH.exists():
        try:
            age = time.time() - EXTRACTORS_PATH.stat().st_mtime
            data = json.loads(EXTRACTORS_PATH.read_text(encoding="utf-8"))
            if age < 7 * 24 * 3600 and isinstance(data, list) and data:
                return data
        except (OSError, json.JSONDecodeError):
            pass
    gdl = find_gallery_dl()
    if not gdl:
        return []
    try:
        proc = subprocess.run(
            gdl + ["--list-extractors"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            env=_env(),
        )
        items = parse_extractors(proc.stdout or proc.stderr or "")
        if items:
            EXTRACTORS_PATH.write_text(json.dumps(items), encoding="utf-8")
        return items
    except (OSError, subprocess.TimeoutExpired):
        return []


_update_state = {
    "current": "",
    "latest": "",
    "available": False,
    "checked": False,
    "can_update": False,
    "log": "",
}


def engine_status() -> dict:
    return {
        **_update_state,
        "can_update": can_self_update(),
    }


def check_engine() -> dict:
    ensure_local_gallery_dl()
    gdl = find_gallery_dl()
    if not gdl:
        raise RuntimeError("gallery-dl was not found")
    current = gallery_dl_version()
    proc = subprocess.run(
        gdl + ["--update-check"],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        env=_env(),
    )
    log = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    latest = current
    available = False
    match = re.search(r"available:\s*(\S+)\s*->\s*(\S+)", log, re.I)
    if match:
        current = match.group(1).strip()
        latest = match.group(2).strip().rstrip(".")
        available = current != latest
    elif re.search(r"up to date", log, re.I):
        available = False
        latest = current
    _update_state.update(
        {
            "current": current,
            "latest": latest,
            "available": available,
            "checked": True,
            "can_update": can_self_update(),
            "log": log,
        }
    )
    return engine_status()


def update_engine() -> dict:
    ensure_local_gallery_dl()
    if not can_self_update():
        raise RuntimeError(
            "This copy of gallery-dl cannot self-update. "
            "The portable app uses gallery-dl.exe next to Stills.exe."
        )
    gdl = find_gallery_dl()
    if not gdl:
        raise RuntimeError("gallery-dl was not found")
    # Update the persistent exe, not a temp unpack.
    target = LOCAL_GDL if LOCAL_GDL.is_file() else Path(gdl[0])
    cmd = [str(target), "--update"]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        env=_env(),
    )
    log = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    invalidate_runtime()
    current = gallery_dl_version()
    _update_state.update(
        {
            "current": current,
            "latest": current,
            "available": False,
            "checked": True,
            "can_update": True,
            "log": log,
        }
    )
    return engine_status()
