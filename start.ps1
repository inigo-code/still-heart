Set-Location $PSScriptRoot
$Host.UI.RawUI.WindowTitle = "Stills"
if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
  py -3.11 -m venv .venv
}
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (Test-Path ".\brand\logo.jpg") {
  & .\.venv\Scripts\python.exe tools\make_logo.py
}
& .\.venv\Scripts\python.exe -m app
