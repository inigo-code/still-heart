# Build a single portable StillHeart.exe (gallery-dl + optional ffmpeg baked in).
Set-Location $PSScriptRoot
$ErrorActionPreference = "Stop"

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
  py -3.11 -m venv .venv
}
$py = ".\.venv\Scripts\python.exe"
& $py -m pip install -r requirements.txt
& $py -m pip install pyinstaller

# Stop the WebView2 white flash on minimize/maximize (edits pywebview's launch args).
& $py tools\patch_pywebview.py

& $py tools\make_logo.py

$bin = Join-Path $PSScriptRoot "bin"
New-Item -ItemType Directory -Force -Path $bin | Out-Null

$gdl = Join-Path $bin "gallery-dl.exe"
if (-not (Test-Path $gdl) -or (Get-Item $gdl).Length -lt 1000000) {
  $urls = @(
    "https://codeberg.org/mikf/gallery-dl/releases/download/v1.32.10/gallery-dl.exe",
    "https://codeberg.org/mikf/gallery-dl/releases/download/v1.32.9/gallery-dl.exe",
    "https://github.com/gdl-org/builds/releases/latest/download/gallery-dl_windows.exe"
  )
  foreach ($u in $urls) {
    Write-Host "Downloading gallery-dl.exe from $u"
    try {
      & curl.exe -L --fail -o $gdl $u
      if ((Test-Path $gdl) -and (Get-Item $gdl).Length -gt 1000000) { break }
    } catch {
      Write-Host "Download failed: $_"
    }
  }
}
if (-not (Test-Path $gdl) -or (Get-Item $gdl).Length -lt 1000000) {
  Write-Host "No standalone gallery-dl.exe — the Python package will be bundled instead."
}

$ffmpeg = $null
foreach ($c in @(
    (Join-Path $bin "ffmpeg.exe"),
    (Join-Path $env:USERPROFILE ".forgeneo\bin\ffmpeg.exe"),
    "C:\ffmpeg\bin\ffmpeg.exe",
    (Get-Command ffmpeg -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
  )) {
  if ($c -and (Test-Path $c)) { $ffmpeg = $c; break }
}
if ($ffmpeg -and $ffmpeg -ne (Join-Path $bin "ffmpeg.exe")) {
  Copy-Item $ffmpeg (Join-Path $bin "ffmpeg.exe") -Force
}
if (-not $ffmpeg) {
  Write-Host "ffmpeg.exe not found — Pixiv ugoira conversion will be skipped. Images still download."
}

Write-Host "Building single-file StillHeart.exe..."
& $py -m PyInstaller --noconfirm --clean stills.spec

$exe = Join-Path $PSScriptRoot "dist\StillHeart.exe"
if (-not (Test-Path $exe)) {
  Write-Error "Build finished but dist\StillHeart.exe is missing"
}

Write-Host ""
Write-Host "Portable app: $exe"
Write-Host "Copy that one file anywhere and double-click it. First launch can take 15-30 seconds while it unpacks."
