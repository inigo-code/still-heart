"""Patch pywebview's WebView2 launch arguments to stop window flicker.

pywebview hardcodes ``AdditionalBrowserArguments = '--disable-features=ElasticOverscroll'``
in its edgechromium backend. That makes Chromium throw away and repaint the
render surface (a white flash) on minimize / maximize / occlusion. We merge in
the switches that keep the surface painted and stop renderer backgrounding.

This runs at build time (from build.ps1) so the fix is baked into the bundled
copy of pywebview and survives a fresh virtualenv. It is idempotent.
"""

from __future__ import annotations

import sys
from pathlib import Path

ORIGINAL = "props.AdditionalBrowserArguments = '--disable-features=ElasticOverscroll'"
PATCHED = (
    "props.AdditionalBrowserArguments = "
    "'--disable-features=ElasticOverscroll,CalculateNativeWinOcclusion"
    " --disable-backgrounding-occluded-windows"
    " --disable-renderer-backgrounding"
    " --disable-background-timer-throttling'"
)
MARKER = "CalculateNativeWinOcclusion"


def _target() -> Path | None:
    try:
        import webview.platforms.edgechromium as ec
    except Exception as exc:  # pywebview not installed / non-Windows
        print(f"patch_pywebview: skipped ({exc})")
        return None
    return Path(ec.__file__)


def main() -> int:
    path = _target()
    if path is None or not path.is_file():
        return 0
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        print("patch_pywebview: already patched")
        return 0
    if ORIGINAL not in text:
        print("patch_pywebview: anchor line not found — pywebview changed; skipping")
        return 0
    path.write_text(text.replace(ORIGINAL, PATCHED, 1), encoding="utf-8")
    print(f"patch_pywebview: patched {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
