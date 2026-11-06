from __future__ import annotations

import os
import sys
import threading
import time
import traceback
import urllib.request
from pathlib import Path

os.environ["STILLS_DESKTOP"] = "1"

if "--run-gallery-dl" in sys.argv:
    idx = sys.argv.index("--run-gallery-dl")
    sys.argv = ["gallery-dl", *sys.argv[idx + 1 :]]
    import gallery_dl

    raise SystemExit(gallery_dl.main())

from app.paths import free_port, prune_temp_extractions, user_dir

prune_temp_extractions()

WEBVIEW2_DOWNLOAD = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"


def _log_path() -> Path:
    if getattr(sys, "frozen", False):
        return user_dir() / "stills.log"
    return Path(__file__).resolve().parent.parent / "stills.log"


_STDIO_ATTACHED = False


def _attach_stdio() -> None:
    global _STDIO_ATTACHED
    if _STDIO_ATTACHED:
        return
    _STDIO_ATTACHED = True
    log_file = _log_path()
    try:
        if log_file.exists() and log_file.stat().st_size > 5_000_000:
            log_file.replace(log_file.with_suffix(".log.old"))
    except OSError:
        pass
    handle = open(log_file, "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = handle
    if sys.stderr is None:
        sys.stderr = handle


if getattr(sys, "frozen", False):
    _attach_stdio()

from app import APP_NAME
from app.main import app
from app.paths import WEB_DIR, ensure_dirs


def _message_box(text: str, title: str = "Stills", yes_no: bool = False) -> bool:
    if sys.platform != "win32":
        print(text, flush=True)
        return False
    import ctypes

    mb_ok = 0x00000000
    mb_yesno = 0x00000004
    mb_icon = 0x00000010
    idyes = 6
    flags = mb_icon | (mb_yesno if yes_no else mb_ok)
    result = ctypes.windll.user32.MessageBoxW(None, text, title, flags)
    return result == idyes


def _open_url(url: str) -> None:
    try:
        os.startfile(url)  # type: ignore[attr-defined]
    except OSError:
        pass


def _webview2_installed() -> bool:
    if sys.platform != "win32":
        return True
    import winreg

    guid = r"{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    paths = [
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{guid}"),
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{guid}"),
        (winreg.HKEY_CURRENT_USER, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{guid}"),
    ]
    for hive, key_path in paths:
        try:
            key = winreg.OpenKey(hive, key_path)
            winreg.CloseKey(key)
            return True
        except OSError:
            continue
    for folder in (
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Microsoft" / "EdgeWebView" / "Application",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Microsoft" / "EdgeWebView" / "Application",
    ):
        if folder.is_dir():
            return True
    return False


def _fail(kind: str, detail: str = "") -> None:
    log = _log_path()
    extra = f"\n\nDetails:\n{detail}" if detail else ""
    if kind == "webview2":
        msg = (
            "Stills needs Microsoft Edge WebView2 to open its window.\n\n"
            "This is already on Windows 11 and most Windows 10 PCs.\n"
            "If you are on an old or stripped-down Windows 10, install the "
            "WebView2 Runtime (Evergreen).\n\n"
            "Click Yes to open the Microsoft download page."
        )
        if _message_box(msg, yes_no=True):
            _open_url(WEBVIEW2_DOWNLOAD)
        return
    if kind == "server":
        msg = (
            "Stills could not start its downloader.\n\n"
            "First launch can take 15–30 seconds. Try once more.\n"
            "Windows Defender may have blocked the unpack — allow the app if asked.\n\n"
            f"A log was written to:\n{log}"
            f"{extra}"
        )
        _message_box(msg)
        return
    msg = (
        "Stills could not open.\n\n"
        f"{detail or 'Unknown error.'}\n\n"
        f"A log was written to:\n{log}"
    )
    _message_box(msg)


def _wait(url: str, tries: int = 120) -> None:
    last = ""
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=2.0) as resp:
                if resp.status == 200:
                    print(f"ready after {attempt} probe(s)", flush=True)
                    return
        except Exception as exc:
            last = str(exc)
            time.sleep(0.1)
    raise RuntimeError(f"Stills server did not start ({url}): {last}")


def main() -> None:
    if getattr(sys, "frozen", False):
        import multiprocessing

        multiprocessing.freeze_support()
    _attach_stdio()

    if not _webview2_installed():
        print("WebView2 runtime not found", flush=True)
        _fail("webview2")
        return

    print(f"WEB_DIR={WEB_DIR} exists={WEB_DIR.is_dir()}", flush=True)
    if getattr(sys, "frozen", False):
        mei = Path(getattr(sys, "_MEIPASS", ""))
        print(f"MEIPASS={mei}", flush=True)
        try:
            names = sorted(p.name for p in mei.iterdir())[:30]
            print(f"bundle={names}", flush=True)
        except OSError as exc:
            print(f"bundle list failed: {exc}", flush=True)
    ensure_dirs()
    host = "127.0.0.1"
    port = free_port()
    page = f"http://{host}:{port}"
    print(f"starting {page}", flush=True)

    def _serve() -> None:
        try:
            import asyncio
            import uvicorn

            config = uvicorn.Config(
                app,
                host=host,
                port=port,
                log_level="info",
                access_log=False,
            )
            server = uvicorn.Server(config)
            server.install_signal_handlers = False
            asyncio.run(server.serve())
        except Exception:
            traceback.print_exc()

    threading.Thread(target=_serve, daemon=True).start()
    try:
        _wait(f"{page}/api/ready")
    except Exception as ext:
        traceback.print_exc()
        _fail("server", str(ext))
        return
    print("server ready", flush=True)

    os.environ.setdefault("PYWEBVIEW_GUI", "edgechromium")
    # Chromium anti-flash switches. `CalculateNativeWinOcclusion` makes Chromium
    # throw away the render surface (repainting white) on minimize / maximize /
    # occlusion; the backgrounding switches make it keep rendering while the
    # window is minimized or in the background. The env var is a fallback — it is
    # ignored by WebView2 when browser args are set programmatically, which
    # pywebview does, so we also inject the switches into pywebview's args below.
    _ANTI_FLASH = (
        "--disable-features=CalculateNativeWinOcclusion "
        "--disable-backgrounding-occluded-windows "
        "--disable-renderer-backgrounding "
        "--disable-background-timer-throttling"
    )
    os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", _ANTI_FLASH)
    try:
        import webview

        if sys.platform == "win32":
            try:
                from webview.platforms.edgechromium import EdgeChrome

                _orig_ready = EdgeChrome.on_webview_ready

                def _enable_ctx_menu(self, sender, args):
                    _orig_ready(self, sender, args)
                    try:
                        sender.CoreWebView2.Settings.AreDefaultContextMenusEnabled = True
                    except Exception:
                        pass

                EdgeChrome.on_webview_ready = _enable_ctx_menu
            except Exception:
                pass

        print("opening window", flush=True)
        webview.create_window(
            APP_NAME,
            page,
            width=1320,
            height=900,
            min_size=(1020, 700),
            confirm_close=True,
            background_color="#100f0e",
        )
        icon = WEB_DIR / "stills.ico"
        print(f"icon={icon} exists={icon.is_file()}", flush=True)
        webview.start(gui="edgechromium", icon=str(icon) if icon.is_file() else None)
        print("window closed", flush=True)
    except Exception as exc:
        traceback.print_exc()
        text = str(exc).lower()
        if "webview" in text or "edge" in text or "runtime" in text:
            _fail("webview2")
        else:
            _fail("window", str(exc))
    finally:
        from app import engine

        engine.kill_all()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        traceback.print_exc()
        _fail("window", str(exc))
