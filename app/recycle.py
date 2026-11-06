"""Send files to the Recycle Bin, so a removal can be undone."""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

FO_DELETE = 3
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOERRORUI = 0x0400


class _Operation(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", ctypes.c_uint16),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


def to_recycle_bin(paths: list[str]) -> int:
    """Move existing files to the Recycle Bin and return how many went."""
    if sys.platform != "win32":
        return 0
    moved = 0
    seen: set[str] = set()
    for text in paths:
        path = Path(text)
        key = str(path).lower()
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        operation = _Operation()
        operation.wFunc = FO_DELETE
        # The list of paths ends with two NUL characters.
        operation.pFrom = str(path.resolve()) + "\0\0"
        operation.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
        if ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation)) == 0 and not path.exists():
            moved += 1
    return moved
