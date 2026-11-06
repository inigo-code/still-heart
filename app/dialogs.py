"""Native Windows folder and file pickers for the settings screen.

The dialog runs in PowerShell so it can sit above the WebView2 window.
The chosen path is written to a temp file as UTF-8. It is never interpolated
into the script, so a path cannot change what the script does.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

_SCRIPT = r"""
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.Opacity = 0
$owner.Width = 1
$owner.Height = 1
$initial = $env:STILLS_PICK_INITIAL
$out = $env:STILLS_PICK_OUT
$owner.Show()
try {
  $chosen = ''
  if ($env:STILLS_PICK_KIND -eq 'folder') {
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description = 'Choose the download folder'
    if ($initial -and (Test-Path -LiteralPath $initial)) { $dialog.SelectedPath = $initial }
    if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { $chosen = $dialog.SelectedPath }
  } else {
    $dialog = New-Object System.Windows.Forms.OpenFileDialog
    $dialog.Title = 'Choose a Netscape cookies.txt'
    $dialog.Filter = 'Cookie files (*.txt)|*.txt|All files (*.*)|*.*'
    if ($initial -and (Test-Path -LiteralPath $initial)) { $dialog.InitialDirectory = $initial }
    if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { $chosen = $dialog.FileName }
  }
  if ($chosen) {
    [System.IO.File]::WriteAllText($out, $chosen, (New-Object System.Text.UTF8Encoding $false))
  }
} finally {
  $owner.Close()
  $owner.Dispose()
}
"""


def _initial_dir(initial: str) -> str:
    text = (initial or "").strip()
    if not text:
        return ""
    path = Path(text).expanduser()
    try:
        if path.is_file():
            path = path.parent
        if path.is_dir():
            return str(path)
    except OSError:
        return ""
    return ""


def pick_path(kind: str, initial: str = "") -> str:
    """Open a folder or cookies-file dialog. Returns '' when the user cancels."""
    if kind not in {"folder", "file"}:
        raise ValueError("Unknown picker")
    if sys.platform != "win32":
        raise RuntimeError("The folder picker is only available on Windows")
    handle = tempfile.NamedTemporaryFile(prefix="stills-pick-", suffix=".txt", delete=False)
    out = Path(handle.name)
    handle.close()
    env = os.environ.copy()
    env["STILLS_PICK_KIND"] = kind
    env["STILLS_PICK_INITIAL"] = _initial_dir(initial)
    env["STILLS_PICK_OUT"] = str(out)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", _SCRIPT],
            capture_output=True,
            env=env,
            timeout=600,
            creationflags=flags,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or b"").decode("utf-8", "replace").strip()
            raise RuntimeError(detail.splitlines()[-1][:240] if detail else "Could not open the file dialog")
        if not out.is_file() or out.stat().st_size == 0:
            return ""
        return out.read_text(encoding="utf-8").strip()
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("The file dialog was left open too long") from exc
    finally:
        try:
            out.unlink(missing_ok=True)
        except OSError:
            pass
