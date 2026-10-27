# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

hidden = []
for pkg in ("uvicorn", "anyio", "starlette", "fastapi", "webview", "app"):
    try:
        hidden += collect_submodules(pkg)
    except Exception:
        pass

hidden += [
    "uvicorn.logging",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "pydantic",
    "app.main",
    "app.desktop",
    "app.engine",
    "app.jobs",
    "app.settings",
    "app.runtime",
    "app.paths",
    "app.guards",
    "app.scheduler",
]

bins = []
root = Path(SPECPATH)
for name in ("ffmpeg.exe", "gallery-dl.exe"):
    candidate = root / "bin" / name
    if candidate.is_file():
        bins.append((str(candidate), "."))

a = Analysis(
    ["app/desktop.py"],
    pathex=[],
    binaries=bins,
    datas=[("web", "web")],
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

ico = (root / "web" / "stills.ico").resolve()
version = (root / "stills.version").resolve()
if not ico.is_file():
    raise SystemExit(f"Missing Windows icon: {ico}")
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="Stills",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    icon=str(ico),
    version=str(version) if version.is_file() else None,
)
