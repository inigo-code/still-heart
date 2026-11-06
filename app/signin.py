"""Sign in to sites from inside the app.

The site's own login page opens in a window here. The person types their
password into the site, never into this app. Cookies, and the tokens gallery-dl
trades for them, stay in this app's data folder. Keys some sites hand out are
kept with Windows' per-user encryption.
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

from app.paths import DATA_DIR

SIGNIN_DIR = DATA_DIR / "sign-in"
SESSIONS_DIR = SIGNIN_DIR / "sessions"
ACCOUNTS_PATH = SIGNIN_DIR / "accounts.json"
CACHE_PATH = SIGNIN_DIR / "gallery-dl-cache.sqlite3"
OAUTH_CONFIG = SIGNIN_DIR / "oauth.json"

POLL_SECONDS = 1.2
SETTLE_SECONDS = 2.0
OAUTH_URL_WAIT = 30.0
CREATE_NO_WINDOW = 0x08000000
NEEDS_SIGN_IN = re.compile(
    r"refresh-token|login required|log in|sign in|logged in|login wall|unauthorized|authenticat"
    r"|\b401\b|cookies expired|nsfw|age.?restricted|members.?only",
    re.I,
)


@dataclass(frozen=True)
class Site:
    id: str
    name: str
    how: str  # "browser", "oauth" or "key"
    hosts: tuple[str, ...]
    url: str = ""
    done: tuple[str, ...] = ()
    fields: tuple[tuple[str, str], ...] = ()
    token: str = ""


def _browser(id: str, name: str, url: str, hosts: tuple[str, ...], done: tuple[str, ...] = ()) -> Site:
    return Site(id, name, "browser", hosts, url, done)


def _oauth(id: str, name: str, hosts: tuple[str, ...], token: str = "") -> Site:
    return Site(id, name, "oauth", hosts, token=token)


def _key(id: str, name: str, hosts: tuple[str, ...], *fields: tuple[str, str]) -> Site:
    return Site(id, name, "key", hosts, fields=fields)


SITES: tuple[Site, ...] = (
    _oauth("pixiv", "Pixiv", ("pixiv.net",), token="refresh-token"),
    _browser("instagram", "Instagram", "https://www.instagram.com/accounts/login/", ("instagram.com",), ("sessionid",)),
    _browser("twitter", "X (Twitter)", "https://x.com/i/flow/login", ("x.com", "twitter.com"), ("auth_token",)),
    _browser("facebook", "Facebook", "https://www.facebook.com/login/", ("facebook.com",), ("c_user",)),
    _browser("tiktok", "TikTok", "https://www.tiktok.com/login", ("tiktok.com",), ("sessionid",)),
    _oauth("deviantart", "DeviantArt", ("deviantart.com",)),
    _oauth("reddit", "Reddit", ("reddit.com",)),
    _oauth("tumblr", "Tumblr", ("tumblr.com",)),
    _oauth("flickr", "Flickr", ("flickr.com",)),
    _oauth("smugmug", "SmugMug", ("smugmug.com",)),
    _browser("patreon", "Patreon", "https://www.patreon.com/login", ("patreon.com",), ("session_id",)),
    _browser("fanbox", "Pixiv Fanbox", "https://www.fanbox.cc/login", ("fanbox.cc", "pixiv.net"), ("FANBOXSESSID",)),
    _browser("fantia", "Fantia", "https://fantia.jp/sessions/signin", ("fantia.jp",)),
    _browser("boosty", "Boosty", "https://boosty.to/app/auth", ("boosty.to",), ("auth",)),
    _browser("subscribestar", "SubscribeStar", "https://www.subscribestar.com/login", ("subscribestar.com",)),
    _browser("weibo", "Weibo", "https://passport.weibo.com/sso/signin", ("weibo.com", "weibo.cn"), ("SUB",)),
    _browser("vk", "VK", "https://vk.com/login", ("vk.com",)),
    _browser("newgrounds", "Newgrounds", "https://www.newgrounds.com/passport", ("newgrounds.com",), ("ng_session",)),
    _browser("furaffinity", "Fur Affinity", "https://www.furaffinity.net/login", ("furaffinity.net",), ("a",)),
    _browser("exhentai", "E-Hentai", "https://forums.e-hentai.org/index.php?act=Login", ("e-hentai.org", "exhentai.org"), ("ipb_member_id",)),
    _browser("nijie", "Nijie", "https://nijie.info/login.php", ("nijie.info",), ("nijie_tok",)),
    _browser("kemono", "Kemono", "https://kemono.cr/authentication/login", ("kemono.cr", "kemono.su")),
    _browser("poipiku", "Poipiku", "https://poipiku.com/LoginFormEmailPcV.jsp", ("poipiku.com",), ("POIPIKU_LK",)),
    _browser("pillowfort", "Pillowfort", "https://www.pillowfort.social/users/sign_in", ("pillowfort.social",)),
    _browser("tapas", "Tapas", "https://tapas.io/account/signin", ("tapas.io",), ("_cpc_",)),
    _browser("ao3", "Archive of Our Own", "https://archiveofourown.org/users/login", ("archiveofourown.org",), ("remember_user_token",)),
    _browser("seiga", "Niconico Seiga", "https://account.nicovideo.jp/login", ("nicovideo.jp",), ("user_session",)),
    _browser("behance", "Behance", "https://www.behance.net/signin", ("behance.net",)),
    _browser("500px", "500px", "https://500px.com/login", ("500px.com",)),
    _browser("iwara", "Iwara", "https://www.iwara.tv/login", ("iwara.tv",)),
    _browser("zerochan", "Zerochan", "https://www.zerochan.net/login", ("zerochan.net",), ("z_id",)),
    _browser("imgbb", "ImgBB", "https://imgbb.com/login", ("imgbb.com", "ibb.co"), ("LID",)),
    _key("bluesky", "Bluesky", ("bsky.app",), ("username", "Handle"), ("password", "App password")),
    _key("inkbunny", "Inkbunny", ("inkbunny.net",), ("username", "Username"), ("password", "Password")),
    _key("sankaku", "Sankaku", ("sankaku.app", "sankakucomplex.com"), ("username", "Username"), ("password", "Password")),
    _key("danbooru", "Danbooru", ("donmai.us",), ("username", "Username"), ("password", "API key")),
    _key("gelbooru", "Gelbooru", ("gelbooru.com",), ("user-id", "User ID"), ("api-key", "API key")),
    _key("civitai", "Civitai", ("civitai.com",), ("api-key", "API key")),
    _key("gofile", "Gofile", ("gofile.io",), ("api-token", "Account token")),
    _key("discord", "Discord", ("discord.com",), ("token", "Account token")),
)
BY_ID = {site.id: site for site in SITES}
OAUTH_LOGIN = {"pixiv": "pixiv"}

_lock = threading.Lock()
_active: dict | None = None
_last: dict | None = None


def _host(url: str) -> str:
    try:
        return (urlparse(url.strip()).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def _matches(host: str, hosts: tuple[str, ...]) -> bool:
    host = host.lstrip(".").lower()
    return any(host == item or host.endswith("." + item) for item in hosts)


def _home_domain(host: str) -> str:
    """example.co.uk from www.example.co.uk. An address stays whole."""
    parts = host.split(".")
    if len(parts) <= 2 or all(part.isdigit() for part in parts):
        return host
    two_part = parts[-2] in {"co", "com", "org", "net", "ac", "gov", "ne", "or"} and len(parts[-1]) == 2
    return ".".join(parts[-3:] if two_part else parts[-2:])


def _custom_id(domain: str) -> str:
    return "site-" + re.sub(r"[^a-z0-9.-]", "", domain.lower())


# ---------------------------------------------------------------- stored accounts


def _read_accounts() -> dict:
    try:
        data = json.loads(ACCOUNTS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_accounts(data: dict) -> None:
    SIGNIN_DIR.mkdir(parents=True, exist_ok=True)
    temp = ACCOUNTS_PATH.with_suffix(".json.tmp")
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temp.replace(ACCOUNTS_PATH)


def _remember(site_id: str, **fields) -> None:
    with _lock:
        data = _read_accounts()
        row = {"when": time.time(), **fields}
        data[site_id] = row
        _write_accounts(data)


def _forget(site_id: str) -> None:
    with _lock:
        data = _read_accounts()
        if data.pop(site_id, None) is not None:
            _write_accounts(data)


def _dpapi(data: bytes, protect: bool) -> bytes:
    """Windows encryption tied to this user on this PC."""
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_char))]

    crypt = ctypes.windll.crypt32
    kernel = ctypes.windll.kernel32
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    held = ctypes.create_string_buffer(data, len(data))
    source = Blob(len(data), ctypes.cast(held, ctypes.POINTER(ctypes.c_char)))
    result = Blob()
    call = crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    if not call(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(result)):
        raise OSError("Windows could not read the saved sign-in")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(result.data)


def _seal(text: str) -> str:
    return base64.b64encode(_dpapi(text.encode("utf-8"), True)).decode("ascii")


def _unseal(text: str) -> str:
    return _dpapi(base64.b64decode(text), False).decode("utf-8")


def save_keys(site_id: str, values: dict) -> None:
    site = BY_ID.get(site_id)
    allowed = {name for name, _label in site.fields} if site else set()
    if site and site.token:
        allowed.add(site.token)
    clean = {k: str(v).strip() for k, v in values.items() if k in allowed and str(v).strip()}
    if site is None or not clean:
        raise ValueError("Fill in the fields first.")
    if site.how == "key" and len(clean) < len(site.fields):
        raise ValueError("Fill in every field.")
    _remember(site_id, how="key", sealed={k: _seal(v) for k, v in clean.items()})


def keys_for(site_id: str) -> dict:
    row = _read_accounts().get(site_id) or {}
    out = {}
    for name, sealed in (row.get("sealed") or {}).items():
        try:
            out[name] = _unseal(sealed)
        except (OSError, ValueError):
            return {}
    return out


# ---------------------------------------------------------------- cookies


def session_path(site_id: str) -> Path:
    return SESSIONS_DIR / f"{re.sub(r'[^a-zA-Z0-9.-]', '_', site_id)}.txt"


def write_session(site_id: str, cookies: list[dict]) -> Path:
    """Save cookies as a Netscape file, which gallery-dl and yt-dlp both read."""
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    lines = ["# Netscape HTTP Cookie File"]
    for item in cookies:
        domain = item["domain"]
        lead = "#HttpOnly_" if item.get("httponly") else ""
        whole = "TRUE" if domain.startswith(".") else "FALSE"
        secure = "TRUE" if item.get("secure") else "FALSE"
        value = str(item["value"]).replace("\t", " ").replace("\n", " ")
        lines.append(f"{lead}{domain}\t{whole}\t{item.get('path') or '/'}\t{secure}\t{int(item.get('expires') or 0)}\t{item['name']}\t{value}")
    path = session_path(site_id)
    temp = path.with_suffix(".tmp")
    temp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temp.replace(path)
    return path


def site_for(url: str) -> Site | None:
    host = _host(url)
    if not host:
        return None
    for site in SITES:
        if _matches(host, site.hosts):
            return site
    return None


def _session_for(url: str) -> str:
    host = _host(url)
    if not host:
        return ""
    site = site_for(url)
    ids = [site.id] if site else []
    if site is not None and site.id == "pixiv":
        ids.append("fanbox")
    ids.append(_custom_id(_home_domain(host)))
    accounts = _read_accounts()
    for site_id in ids:
        if site_id in accounts and session_path(site_id).is_file():
            return site_id
    return ""


def cookie_file_for(url: str) -> str:
    """The saved session that belongs to this link, if there is one."""
    site_id = _session_for(url)
    return str(session_path(site_id)) if site_id else ""


def needs(job: dict) -> str:
    """Which site to offer sign-in for, when a job failed for want of an account."""
    if job.get("status") not in {"error", "partial"}:
        return ""
    if not NEEDS_SIGN_IN.search(str(job.get("error") or "")):
        return ""
    site = site_for(str(job.get("url") or ""))
    return site.id if site else ""


# ---------------------------------------------------------------- gallery-dl tokens


def apply_to_config(payload: dict, url: str) -> None:
    """Point a gallery-dl config at the tokens and keys saved for this link."""
    payload["cache"] = {"file": str(CACHE_PATH).replace("\\", "/")}
    site = site_for(url)
    if site is None:
        return
    keys = keys_for(site.id)
    if keys:
        section = payload.setdefault("extractor", {}).setdefault(site.id, {})
        section.update(keys)


def _clear_cached_token(site_id: str) -> None:
    import sqlite3

    if not CACHE_PATH.is_file():
        return
    try:
        db = sqlite3.connect(CACHE_PATH)
        try:
            db.execute("DELETE FROM data WHERE key LIKE ?", (f"%{site_id}%",))
            db.commit()
        finally:
            db.close()
    except sqlite3.Error:
        pass


def sign_out(site_id: str) -> None:
    session_path(site_id).unlink(missing_ok=True)
    _clear_cached_token(site_id)
    _forget(site_id)


# ---------------------------------------------------------------- status


def status() -> dict:
    accounts = _read_accounts()
    rows = []
    for site in SITES:
        row = accounts.get(site.id)
        rows.append(
            {
                "id": site.id,
                "name": site.name,
                "how": site.how,
                "signed_in": bool(row),
                "when": (row or {}).get("when") or 0,
                "fields": [{"name": n, "label": label} for n, label in site.fields],
                "token": site.token,
            }
        )
    for site_id, row in accounts.items():
        if site_id.startswith("site-"):
            rows.append(
                {
                    "id": site_id,
                    "name": site_id[5:],
                    "how": "browser",
                    "signed_in": True,
                    "when": row.get("when") or 0,
                    "fields": [],
                    "token": "",
                }
            )
    return {"sites": rows, "active": _active, "last": _last, "available": available()}


def available() -> bool:
    return bool(os.environ.get("STILLS_DESKTOP")) and sys.platform == "win32"


def _finish(site_id: str, name: str, ok: bool, message: str) -> None:
    global _active, _last
    _last = {"site": site_id, "name": name, "ok": ok, "message": message, "at": time.time()}
    _active = None


# ---------------------------------------------------------------- the sign-in window


def _webview2(window):
    from webview.platforms.winforms import BrowserView

    return BrowserView.instances[window.uid].browser.webview


def _read_cookies(window) -> list[dict]:
    """Every cookie in the window, HttpOnly ones too. The page itself can't see those."""
    from System import Action, Func, Type  # type: ignore[import-not-found]
    from System.Collections.Generic import List  # type: ignore[import-not-found]
    from System.Globalization import CultureInfo  # type: ignore[import-not-found]
    from System.Threading.Tasks import Task  # type: ignore[import-not-found]
    from Microsoft.Web.WebView2.Core import CoreWebView2Cookie  # type: ignore[import-not-found]
    from webview.platforms.winforms import BrowserView

    browser = BrowserView.instances[window.uid].browser
    done = threading.Event()
    found: list[dict] = []

    def parse(task) -> None:
        # WebView2 objects can only be touched on the window's own thread.
        try:
            for item in task.Result:
                expires = 0
                if not item.IsSession:
                    text = item.Expires.ToString("r", CultureInfo.GetCultureInfo("en-US"))
                    expires = int(parsedate_to_datetime(text).timestamp())
                found.append(
                    {
                        "name": item.Name,
                        "value": item.Value,
                        "domain": item.Domain,
                        "path": item.Path,
                        "secure": bool(item.IsSecure),
                        "httponly": bool(item.IsHttpOnly),
                        "expires": expires,
                    }
                )
        finally:
            done.set()

    def begin():
        # An empty address asks for every cookie in the window, not just this page's.
        browser.webview.CoreWebView2.CookieManager.GetCookiesAsync("").ContinueWith(
            Action[Task[List[CoreWebView2Cookie]]](parse), browser.syncContextTaskScheduler
        )

    browser.webview.BeginInvoke(Func[Type](begin))
    done.wait(6.0)
    return found


def _open_window(title: str, url: str):
    import webview

    window = webview.create_window(title, url, width=1040, height=780, min_size=(560, 520), background_color="#ffffff")
    closed = threading.Event()
    window.events.closed += lambda *_a: closed.set()
    window.events.shown.wait(20)
    return window, closed


def _close(window, closed: threading.Event) -> None:
    if not closed.is_set():
        try:
            window.destroy()
        except Exception:
            pass


def _keep_cookies(cookies: list[dict], hosts: tuple[str, ...]) -> list[dict]:
    return [item for item in cookies if _matches(item["domain"], hosts)]


def _browser_flow(site_id: str, name: str, url: str, hosts: tuple[str, ...], done_names: tuple[str, ...]) -> None:
    window, closed = _open_window(f"Sign in to {name}. Close this window when you are done.", url)
    latest: list[dict] = []
    signed = False
    try:
        while not closed.wait(POLL_SECONDS):
            mine = _keep_cookies(_read_cookies(window), hosts)
            if mine:
                latest = mine
            if done_names and any(item["name"] in done_names for item in mine):
                signed = True
                time.sleep(SETTLE_SECONDS)
                latest = _keep_cookies(_read_cookies(window), hosts) or latest
                break
    finally:
        _close(window, closed)
    if done_names and not signed:
        _finish(site_id, name, False, f"{name} sign-in was not finished.")
        return
    if not latest:
        _finish(site_id, name, False, f"{name} did not save a session.")
        return
    write_session(site_id, latest)
    _remember(site_id, how="browser")
    _finish(site_id, name, True, f"Signed in to {name}.")


def _write_oauth_config() -> None:
    SIGNIN_DIR.mkdir(parents=True, exist_ok=True)
    OAUTH_CONFIG.write_text(
        json.dumps(
            {
                "extractor": {"input": True, "oauth": {"browser": False, "cache": True}},
                "cache": {"file": str(CACHE_PATH).replace("\\", "/")},
                "output": {"mode": "pipe", "ansi": False},
            }
        ),
        encoding="utf-8",
    )


def _oauth_flow(site: Site, gallery_dl: list[str], env: dict) -> None:
    """gallery-dl does the token exchange. This supplies the window it would
    have opened in a browser, and for Pixiv the code a browser would hide."""
    _write_oauth_config()
    flags = CREATE_NO_WINDOW if sys.platform == "win32" else 0
    proc = subprocess.Popen(
        [*gallery_dl, "--ignore-config", "-c", str(OAUTH_CONFIG), f"oauth:{site.id}"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=flags,
    )
    lines: list[str] = []
    found = threading.Event()
    box: dict = {}

    def read() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.append(line)
            match = re.search(r"https://\S+", line)
            if match and "url" not in box:
                box["url"] = match.group(0)
                found.set()
        found.set()

    threading.Thread(target=read, daemon=True).start()
    found.wait(OAUTH_URL_WAIT)
    if "url" not in box:
        proc.kill()
        _finish(site.id, site.name, False, f"Could not start {site.name} sign-in.")
        return

    window, closed = _open_window(f"Sign in to {site.name}", box["url"])
    sent = threading.Event()

    def on_navigation(_sender, args) -> None:
        uri = str(args.Uri)
        if uri.lower().startswith("pixiv://") and not sent.is_set():
            args.Cancel = True
            code = re.search(r"[?&]code=([^&#]+)", uri)
            if code and proc.stdin is not None:
                sent.set()
                proc.stdin.write(code.group(1) + "\n")
                proc.stdin.flush()

    if site.id in OAUTH_LOGIN:
        _webview2(window).NavigationStarting += on_navigation
    try:
        while not closed.wait(0.5):
            if proc.poll() is not None:
                break
    finally:
        _close(window, closed)
    if proc.poll() is None:
        try:
            proc.wait(20 if sent.is_set() else 0.1)
        except subprocess.TimeoutExpired:
            pass
    if proc.poll() is None:
        proc.kill()
        _finish(site.id, site.name, False, f"{site.name} sign-in was cancelled.")
        return
    text = "".join(lines)
    if proc.returncode == 0 and "been cached" in text:
        _remember(site.id, how="oauth")
        _finish(site.id, site.name, True, f"Signed in to {site.name}.")
        return
    _finish(site.id, site.name, False, f"{site.name} did not finish the sign-in. Try again.")


def _guarded(job, site_id: str, name: str) -> None:
    try:
        job()
    except Exception as exc:  # A failed window must not leave sign-in stuck.
        print(f"sign-in failed for {site_id}: {type(exc).__name__}", flush=True)
        _finish(site_id, name, False, f"Could not open the sign-in window for {name}.")


def _claim(site_id: str, name: str) -> None:
    global _active
    with _lock:
        if _active is not None:
            raise ValueError(f"Finish signing in to {_active['name']} first.")
        _active = {"site": site_id, "name": name, "at": time.time()}


def start(site_id: str, gallery_dl: list[str], env: dict) -> None:
    site = BY_ID.get(site_id)
    if site is None or site.how == "key":
        raise ValueError("That site has no sign-in page.")
    if not available():
        raise ValueError("Sign-in opens inside the Still Heart window.")
    if site.how == "oauth" and not gallery_dl:
        raise ValueError("gallery-dl was not found.")
    _claim(site.id, site.name)
    if site.how == "oauth":
        job = lambda: _oauth_flow(site, gallery_dl, env)
    else:
        job = lambda: _browser_flow(site.id, site.name, site.url, site.hosts, site.done)
    threading.Thread(target=_guarded, args=(job, site.id, site.name), daemon=True).start()


def start_other(url: str) -> None:
    text = url.strip()
    if "://" not in text:
        text = "https://" + text
    parsed = urlparse(text)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not host:
        raise ValueError("Enter the site's address, like example.com")
    if not available():
        raise ValueError("Sign-in opens inside the Still Heart window.")
    domain = _home_domain(host)
    site_id = _custom_id(domain)
    _claim(site_id, domain)
    job = lambda: _browser_flow(site_id, domain, text, (domain,), ())
    threading.Thread(target=_guarded, args=(job, site_id, domain), daemon=True).start()
