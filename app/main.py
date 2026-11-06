from __future__ import annotations

import asyncio
import io
import ipaddress
import json
import mimetypes
import os
import re
import socket
import sys
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import APP_AUTHOR, APP_CREDIT, APP_NAME, APP_TAGLINE, __version__, dialogs, engine, guards, jobs, recycle, scheduler, settings, signin
from app.paths import DATA_DIR, WEB_DIR, ensure_dirs, free_port
from app.runtime import ensure_local_gallery_dl, invalidate_runtime, runtime_report, warm_runtime

IMAGE_EXTS = engine.IMAGE_EXTS
VIDEO_EXTS = engine.VIDEO_EXTS
MAX_BATCH = 80


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_dirs()
    ensure_local_gallery_dl()
    jobs.load_history()
    guards.start()
    scheduler.start()
    yield
    engine.kill_all()


app = FastAPI(title=APP_NAME, version=__version__, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


LOCAL_HOSTS = {"127.0.0.1", "localhost"}


@app.middleware("http")
async def _origin_guard(request, call_next):
    # DNS rebinding: a hostile page can point its own domain at 127.0.0.1, and
    # then Origin and Host both name that domain. Only answer to local names.
    host_name = urlparse(f"//{request.headers.get('host') or ''}").hostname or ""
    if host_name.lower() not in LOCAL_HOSTS:
        return JSONResponse({"detail": "Unknown host"}, status_code=403)
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin") or ""
        if origin:
            host = request.headers.get("host") or ""
            if urlparse(origin).netloc != host:
                return JSONResponse({"detail": "Cross-origin request blocked"}, status_code=403)
    return await call_next(request)


class UrlsIn(BaseModel):
    url: str = Field(min_length=4)
    range: str | None = None


class SelectedIn(BaseModel):
    indices: list[int] = Field(min_length=1)


class SettingsIn(BaseModel):
    accepted_terms: bool | None = None
    output_dir: str | None = None
    max_active: int | None = None
    min_free_gb: float | None = None
    warn_free_gb: float | None = None
    cookies_file: str | None = None
    cookies_browser: str | None = None
    proxy: str | None = None
    filename_preset: str | None = None
    filename_custom: str | None = None
    directory_preset: str | None = None
    directory_custom: str | None = None
    use_archive: bool | None = None
    write_metadata: bool | None = None
    write_tags: bool | None = None
    filter_mode: str | None = None
    min_width: int | None = None
    sleep_request: float | None = None
    rate: str | None = None
    retries: int | None = None
    scan_limit: int | None = None
    convert_ugoira: str | None = None
    skip_existing: bool | None = None


class OpenIn(BaseModel):
    path: str = ""


class SignInIn(BaseModel):
    site: str = ""
    url: str = ""


class KeysIn(BaseModel):
    site: str
    values: dict[str, str]


class PickIn(BaseModel):
    kind: str
    initial: str = ""


def _valid_http(url: str) -> bool:
    parsed = urlparse(url.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _split_urls(raw: str) -> list[str]:
    """Split pasted text into URL candidates.

    Commas and semicolons are legal inside URLs (``?tags=a,b``), so they only
    separate entries when another http(s):// URL follows them.
    """
    parts: list[str] = []
    for token in raw.split():
        for piece in re.split(r"[,;]+(?=https?://)", token, flags=re.IGNORECASE):
            piece = piece.strip().rstrip(",;")
            if piece:
                parts.append(piece)
    return parts


def parse_urls_report(raw: str) -> tuple[list[str], int, list[str]]:
    """URLs to queue, how many entries weren't URLs, and the URLs past the batch cap."""
    urls: list[str] = []
    rest: list[str] = []
    seen: set[str] = set()
    invalid = 0
    for part in _split_urls(raw or ""):
        if not _valid_http(part):
            invalid += 1
            continue
        if part in seen:
            continue
        seen.add(part)
        (urls if len(urls) < MAX_BATCH else rest).append(part)
    return urls, invalid, rest


def parse_urls(raw: str) -> list[str]:
    return parse_urls_report(raw)[0]


RE_RANGE = re.compile(r"^\s*(\d*\s*[-:]?\s*\d*(\s*:\s*\d+)?)(\s*,\s*\d*\s*[-:]?\s*\d*(\s*:\s*\d+)?)*\s*$")


def clean_range(raw: str | None) -> str:
    """Normalize the Range box: blank/"all" means everything; anything else must be a gallery-dl range."""
    text = (raw or "").strip()
    if text.lower() in {"", "all", "*"}:
        return ""
    if not RE_RANGE.match(text) or not re.search(r"\d", text):
        raise HTTPException(400, "Range must look like 1-80, 5, or 1-10,25-30 (or leave it blank for all)")
    return re.sub(r"\s+", "", text)


def _require_terms() -> dict:
    cfg = settings.load()
    if not cfg.get("accepted_terms"):
        raise HTTPException(403, "Accept the terms before downloading")
    if guards.snapshot().get("blocked"):
        raise HTTPException(409, "Not enough free disk space")
    if not runtime_report().get("ok"):
        raise HTTPException(500, "gallery-dl was not found")
    return cfg


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/favicon.ico")
def favicon() -> FileResponse:
    ico = WEB_DIR / "stills.ico"
    png = WEB_DIR / "logo.png"
    if ico.is_file():
        return FileResponse(ico)
    if png.is_file():
        return FileResponse(png)
    raise HTTPException(404)


@app.get("/api/ready")
def ready() -> dict:
    return {"ok": True, "name": APP_NAME, "version": __version__}


@app.get("/api/health")
def health() -> dict:
    rt = runtime_report(quick=True)
    return {
        "engine": engine.engine_status(),
        "ok": bool(rt.get("ok")),
        "runtime": rt,
        "settings": settings.load(),
        "jobs": len(jobs.all_jobs()),
        "active": jobs.active_count(),
        "disk": guards.snapshot(),
        "name": APP_NAME,
        "tagline": APP_TAGLINE,
        "version": __version__,
        "author": APP_AUTHOR,
        "credit": APP_CREDIT,
    }


@app.get("/api/accounts")
def get_accounts() -> dict:
    return signin.status()


@app.post("/api/accounts/sign-in")
def post_sign_in(body: SignInIn) -> dict:
    try:
        if body.url.strip():
            signin.start_other(body.url)
        else:
            engine.start_sign_in(body.site)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return signin.status()


@app.post("/api/accounts/keys")
def post_keys(body: KeysIn) -> dict:
    try:
        signin.save_keys(body.site, body.values)
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return signin.status()


@app.post("/api/accounts/sign-out")
def post_sign_out(body: SignInIn) -> dict:
    signin.sign_out(body.site)
    return signin.status()


@app.get("/api/settings")
def get_settings() -> dict:
    return settings.load()


@app.put("/api/settings")
def put_settings(body: SettingsIn) -> dict:
    payload = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        return settings.save(payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _enqueue(body: UrlsIn, *, kind: str) -> dict:
    urls, invalid, rest = parse_urls_report(body.url)
    if not urls:
        raise HTTPException(400, "Paste one or more http(s) gallery, album, or image URLs")
    range_spec = clean_range(body.range)
    cfg = _require_terms()
    if kind == "scan" and not range_spec:
        limit = int(cfg.get("scan_limit") or 240)
        range_spec = f"1-{limit}"
    refused: list[str] = []
    notes: list[str] = []
    allowed: list[str] = []
    for url in urls:
        reason, note = engine.link_scope(url)
        if reason:
            refused.append(reason)
            continue
        allowed.append(url)
        if note and not range_spec and note not in notes:
            notes.append(note)
    if not allowed:
        raise HTTPException(400, refused[0])
    if kind == "download" and any(jobs.finished_before(url) for url in allowed):
        notes.append("Already in History. Only files you don't have yet are saved.")
    created = [jobs.create_job(url, kind=kind, range_spec=range_spec) for url in allowed]
    # Always the same shape, so the UI can report what was queued vs. skipped.
    return {"jobs": created, "count": len(created), "invalid": invalid, "rest": rest, "notes": refused + notes}


@app.post("/api/grab")
def grab(body: UrlsIn) -> dict:
    return _enqueue(body, kind="download")


@app.post("/api/scan")
def scan(body: UrlsIn) -> dict:
    return _enqueue(body, kind="scan")


@app.get("/api/jobs")
def list_jobs() -> dict:
    return {"jobs": jobs.all_jobs(), "active": jobs.active_count()}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@app.get("/api/jobs/{job_id}/items")
def job_items(job_id: str, after: int = -1) -> dict:
    items = jobs.get_items(job_id)
    if items is None:
        raise HTTPException(404, "Job not found")
    if after >= 0:
        items = [it for it in items if int(it.get("index") or 0) > after]
    return {"items": items, "count": len(items)}


@app.get("/api/jobs/{job_id}/log")
def job_log(job_id: str) -> dict:
    if not jobs.get_job(job_id):
        raise HTTPException(404, "Job not found")
    return {"log": jobs.get_log(job_id)}


@app.post("/api/jobs/{job_id}/stop")
def stop_job(job_id: str) -> dict:
    current = jobs.get_job(job_id)
    if not current:
        raise HTTPException(404, "Job not found")
    engine.kill_job(job_id)
    return jobs.get_job(job_id) or current


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: str) -> dict:
    current = jobs.get_job(job_id)
    if not current:
        raise HTTPException(404, "Job not found")
    if current["status"] not in jobs.TERMINAL:
        raise HTTPException(409, "Stop it before retrying")
    retried = jobs.requeue_job(job_id)
    if not retried:
        raise HTTPException(409, "Could not retry this item")
    return retried


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str, files: bool = False) -> dict:
    current = jobs.get_job(job_id)
    if not current:
        raise HTTPException(404, "Job not found")
    if current.get("status") in jobs.ACTIVE and current.get("status") != "queued":
        raise HTTPException(409, "Stop it before removing")
    paths = jobs.files_of(job_id) if files else []
    jobs.remove_job(job_id)
    return {"ok": True, "recycled": recycle.to_recycle_bin(paths)}


@app.post("/api/jobs/stop-all")
def stop_all() -> dict:
    for job in jobs.all_jobs():
        if job.get("status") in jobs.ACTIVE:
            engine.kill_job(job["id"])
    return {"ok": True}


@app.post("/api/history/clear")
def clear_history(files: bool = False) -> dict:
    paths = jobs.finished_files() if files else []
    removed = jobs.clear_history()
    return {"removed": removed, "recycled": recycle.to_recycle_bin(paths)}


@app.post("/api/scans/{job_id}/more")
def scan_more(job_id: str) -> dict:
    current = jobs.get_job(job_id)
    if not current or current.get("kind") != "scan":
        raise HTTPException(404, "Scan not found")
    if current.get("status") not in jobs.TERMINAL:
        raise HTTPException(409, "Wait for the scan to finish")
    cfg = _require_terms()
    positions = [p for p in map(engine.item_pos, jobs.get_items(job_id) or []) if p]
    start = (max(positions) if positions else 0) + 1
    limit = int(cfg.get("scan_limit") or 240)
    extended = jobs.extend_scan(job_id, f"{start}-{start + limit - 1}")
    if not extended:
        raise HTTPException(409, "Could not load more for this scan")
    return extended


def selection_jobs(items: list[dict], indices: list[int]) -> tuple[str, list[str]]:
    """Split a preview selection into a gallery-dl file range plus child URLs.

    Files are picked by their gallery-dl position, not the preview index; child
    albums aren't counted by --range, so each one is grabbed by its own URL.
    """
    wanted = set(indices)
    positions: list[int] = []
    children: list[str] = []
    for item in items:
        if int(item.get("index") or 0) not in wanted:
            continue
        pos = engine.item_pos(item)
        if pos:
            positions.append(pos)
        elif item.get("child") and _valid_http(str(item.get("url") or "")):
            children.append(str(item["url"]))
    return engine.compress_range(positions), list(dict.fromkeys(children))


@app.post("/api/scans/{job_id}/download")
def download_selected(job_id: str, body: SelectedIn) -> dict:
    current = jobs.get_job(job_id)
    if not current or current.get("kind") != "scan":
        raise HTTPException(404, "Scan not found")
    _require_terms()
    indices = sorted({int(i) for i in body.indices if int(i) > 0})
    range_spec, children = selection_jobs(jobs.get_items(job_id) or [], indices)
    if not range_spec and not children:
        raise HTTPException(400, "Select at least one file")
    created: list[dict] = []
    if range_spec:
        created.append(
            jobs.create_job(
                current["url"],
                kind="download",
                range_spec=range_spec,
                title=current.get("title") or current["url"],
                extra={"from_scan": job_id},
            )
        )
    for url in children:
        created.append(jobs.create_job(url, kind="download", extra={"from_scan": job_id}))
    if len(created) == 1:
        return created[0]
    return {"jobs": created, "count": len(created)}


@app.get("/api/sites")
def sites(q: str = "") -> dict:
    items = engine.load_extractors(False)
    needle = q.strip().lower()
    if needle:
        items = [
            it
            for it in items
            if needle in (it.get("label") or "").lower()
            or needle in (it.get("category") or "").lower()
            or needle in (it.get("example") or "").lower()
        ]
    return {"count": len(items), "sites": items[:400]}


@app.get("/api/engine")
def get_engine() -> dict:
    return engine.engine_status()


@app.post("/api/engine/check")
def check_engine() -> dict:
    try:
        return engine.check_engine()
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/engine/update")
def update_engine() -> dict:
    try:
        status = engine.update_engine()
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    invalidate_runtime()
    return {"engine": status, "runtime": runtime_report(quick=False), "log": status.get("log") or ""}


def _reveal(path: Path) -> None:
    """Open a folder, or select a file in Explorer."""
    if sys.platform == "win32" and path.is_file():
        # As one quoted argument Explorer can't read this, and opens Documents.
        subprocess.Popen(f'explorer /select,"{path}"')
        return
    os.startfile(str(path))  # type: ignore[attr-defined]


@app.post("/api/open")
def open_path(body: OpenIn) -> dict:
    target = (body.path or "").strip()
    if not target:
        target = str(settings.output_dir())
    path = Path(target).expanduser()
    # A file saved before the download folder changed is still this app's own.
    known = jobs.knows_file(str(path))
    if not any(_safe_under(root, path) for root in (settings.output_dir(), DATA_DIR)) and not known:
        raise HTTPException(403, "That folder is outside your downloads")
    if not path.exists():
        # The file was moved or deleted. Its folder is still the useful place.
        if not path.parent.is_dir():
            raise HTTPException(404, "That file and its folder are no longer there.")
        path = path.parent
    try:
        _reveal(path)
    except OSError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "path": str(path)}


@app.post("/api/pick")
def pick(body: PickIn) -> dict:
    try:
        path = dialogs.pick_path(body.kind, body.initial)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"path": path}


def _safe_under(root: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


@app.get("/api/library")
def library(limit: int = 240, q: str = "", kind: str = "all") -> dict:
    root = settings.output_dir()
    files: list[dict] = []
    cap = max(24, min(800, int(limit)))
    needle = q.strip().lower()
    wanted = kind if kind in {"image", "gif", "video"} else "all"
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in filenames:
                ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                if ext not in IMAGE_EXTS | VIDEO_EXTS:
                    continue
                path = Path(dirpath) / name
                try:
                    stat = path.stat()
                except OSError:
                    continue
                rel = str(path.relative_to(root)).replace("\\", "/")
                item_kind = "video" if ext in VIDEO_EXTS else "gif" if ext == "gif" else "image"
                if wanted != "all" and item_kind != wanted:
                    continue
                if needle and needle not in rel.lower() and needle not in name.lower():
                    continue
                files.append(
                    {
                        "name": name,
                        "path": str(path),
                        "rel": rel,
                        "ext": ext,
                        "kind": item_kind,
                        "size": stat.st_size,
                        "mtime": stat.st_mtime,
                    }
                )
    except OSError:
        pass
    files.sort(key=lambda f: f["mtime"], reverse=True)
    return {"root": str(root), "files": files[:cap], "total": len(files)}


@app.get("/api/library/file")
def library_file(p: str = Query(min_length=1)) -> FileResponse:
    root = settings.output_dir()
    target = Path(p)
    if not target.is_absolute():
        target = root / p
    if not _safe_under(root, target) or not target.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(target)


def _safe_thumb_url(url: str) -> bool:
    if not _valid_http(url):
        return False
    parsed = urlparse(url)
    host = parsed.hostname or ""
    try:
        if parsed.port not in (None, 80, 443):
            return False
    except ValueError:
        return False
    if host.lower() in {"localhost", "127.0.0.1", "::1"}:
        return False
    try:
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
                return False
    except OSError:
        return False
    return True


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _safe_thumb_url(newurl):
            raise urllib.error.HTTPError(newurl, code, "blocked redirect", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# A bare SSLContext trusts nothing, so every site fell through to the loose one.
_THUMB_SSL = ssl.create_default_context()
_THUMB_SSL_LOOSE = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
_THUMB_SSL_LOOSE.check_hostname = False
_THUMB_SSL_LOOSE.verify_mode = ssl.CERT_NONE
THUMB_EDGE = 360
THUMB_CACHE_BYTES = 64 * 1024 * 1024
THUMB_ENTRY_MAX = 2 * 1024 * 1024
FULL_IMAGE_MAX = 64 * 1024 * 1024
THUMB_SOURCE_MAX = 24 * 1024 * 1024
_THUMB_CACHE: OrderedDict[str, tuple[bytes, str]] = OrderedDict()
_thumb_cache_bytes = 0
_thumb_lock = threading.Lock()


def shrink_thumb(data: bytes, ctype: str, edge: int = THUMB_EDGE) -> tuple[bytes, str]:
    """Downscale an image to a small WebP; returns the input unchanged if it can't."""
    try:
        from PIL import Image
    except ImportError:
        return data, ctype
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.width <= edge and im.height <= edge and len(data) <= 200_000:
                return data, ctype
            im.draft("RGB", (edge * 2, edge * 2))
            im.thumbnail((edge, edge))
            small = im if im.mode in ("RGB", "RGBA") else im.convert("RGBA")
            out = io.BytesIO()
            small.save(out, "WEBP", quality=80)
            return out.getvalue(), "image/webp"
    except Exception:
        return data, ctype


def _thumb_cache_get(key: str) -> tuple[bytes, str] | None:
    with _thumb_lock:
        hit = _THUMB_CACHE.get(key)
        if hit is not None:
            _THUMB_CACHE.move_to_end(key)
        return hit


def _thumb_cache_put(key: str, value: tuple[bytes, str]) -> None:
    """LRU capped by bytes, so full-size images can't pile up in memory."""
    global _thumb_cache_bytes
    size = len(value[0])
    if size > THUMB_ENTRY_MAX:
        return
    with _thumb_lock:
        old = _THUMB_CACHE.pop(key, None)
        if old is not None:
            _thumb_cache_bytes -= len(old[0])
        _THUMB_CACHE[key] = value
        _thumb_cache_bytes += size
        while _thumb_cache_bytes > THUMB_CACHE_BYTES and _THUMB_CACHE:
            _, dropped = _THUMB_CACHE.popitem(last=False)
            _thumb_cache_bytes -= len(dropped[0])


def _fetch_thumb(url: str, limit: int = THUMB_SOURCE_MAX) -> tuple[bytes, str]:
    parsed = urlparse(url)
    referer = f"{parsed.scheme}://{parsed.netloc}/"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 StillHeart/1.0",
            "Referer": referer,
            "Accept": "image/*,*/*;q=0.8",
        },
    )
    cert_error: Exception | None = None
    for ctx in (_THUMB_SSL, _THUMB_SSL_LOOSE):
        opener = urllib.request.build_opener(_SafeRedirectHandler(), urllib.request.HTTPSHandler(context=ctx))
        try:
            with opener.open(req, timeout=12) as resp:
                return resp.read(limit), resp.headers.get("Content-Type") or "image/jpeg"
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", None)
            if isinstance(reason, ssl.SSLCertVerificationError):
                cert_error = exc
                continue
            raise
    raise cert_error or OSError("thumbnail fetch failed")


@app.get("/api/thumb")
def thumb(request: Request, u: str = Query(min_length=8), full: bool = False) -> Response:
    fetch_site = (request.headers.get("sec-fetch-site") or "").lower()
    if fetch_site and fetch_site not in {"same-origin", "same-site", "none"}:
        raise HTTPException(403, "Cross-site thumbnail request blocked")
    local = Path(u)
    if local.is_file() and _safe_under(settings.output_dir(), local):
        ext = local.suffix.lower().lstrip(".")
        if full or ext in VIDEO_EXTS:
            return FileResponse(local)
        cache_key = str(local.resolve())
        cached_local = _thumb_cache_get(cache_key)
        headers = {"Cache-Control": "public, max-age=86400"}
        if cached_local is not None:
            data, ctype = cached_local
            return Response(content=data, media_type=ctype, headers=headers)
        try:
            if local.stat().st_size > THUMB_SOURCE_MAX:
                return FileResponse(local)
            raw = local.read_bytes()
        except OSError as exc:
            raise HTTPException(404, "Thumbnail unavailable") from exc
        # A cut-off file can't be decoded, so a large picture never got a thumbnail.
        guess = mimetypes.guess_type(local.name)[0] or "application/octet-stream"
        data, ctype = shrink_thumb(raw, guess)
        _thumb_cache_put(cache_key, (data, ctype))
        return Response(content=data, media_type=ctype, headers=headers)
    headers = {"Cache-Control": "public, max-age=3600"}
    cached = None if full else _thumb_cache_get(u)
    if cached is not None:
        data, ctype = cached
        return Response(content=data, media_type=ctype, headers=headers)
    if not _safe_thumb_url(u):
        raise HTTPException(400, "Bad thumbnail URL")
    try:
        data, ctype = _fetch_thumb(u, FULL_IMAGE_MAX if full else THUMB_SOURCE_MAX)
    except Exception as exc:
        raise HTTPException(404, "Thumbnail unavailable") from exc
    if full:
        return Response(content=data, media_type=ctype, headers=headers)
    data, ctype = shrink_thumb(data, ctype)
    _thumb_cache_put(u, (data, ctype))
    return Response(content=data, media_type=ctype, headers=headers)


@app.get("/api/events")
async def events() -> StreamingResponse:
    async def gen():
        last_rev = -1
        idle = 0
        while True:
            rev = jobs.revision()
            if rev != last_rev:
                last_rev = rev
                # all_jobs() stats files on disk; keep that off the event loop.
                all_jobs = await asyncio.to_thread(jobs.all_jobs)
                payload = json.dumps(
                    {
                        "jobs": all_jobs,
                        "active": jobs.active_count(),
                        "disk": guards.snapshot(),
                    }
                )
                yield f"data: {payload}\n\n"
                idle = 0
            else:
                idle += 1
                if idle >= 8:
                    # Disk space changes without a job event. A named event keeps
                    # the stream alive and lets the window show a pause in time.
                    yield f"event: disk\ndata: {json.dumps(guards.snapshot())}\n\n"
                    idle = 0
            await asyncio.sleep(0.4)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


def run() -> None:
    import uvicorn

    from app.paths import prune_temp_extractions

    prune_temp_extractions()
    host = "127.0.0.1"
    port = free_port()
    url = f"http://{host}:{port}"
    if os.environ.get("STILLS_DESKTOP") != "1":
        threading.Thread(target=lambda: (time.sleep(0.8), webbrowser.open(url)), daemon=True).start()
    print(f"{APP_NAME}  {url}")
    uvicorn.run(app, host=host, port=port, log_level="info")
