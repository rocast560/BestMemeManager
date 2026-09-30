@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" goto deps

echo [reelgrab] first run: creating .venv ...
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -m venv .venv
) else (
  python -m venv .venv
)
if not exist ".venv\Scripts\python.exe" (
  echo Python 3.10+ not found. Install it from python.org or: winget install Python.Python.3.12
  pause
  exit /b 1
)

:deps
".venv\Scripts\python.exe" -c "import PySide6, curl_cffi" >nul 2>nul
if errorlevel 1 (
  echo [reelgrab] installing dependencies, this takes a minute ...
  ".venv\Scripts\python.exe" -m pip install -q -e .[archive]
  if errorlevel 1 (
    echo Dependency install failed.
    pause
    exit /b 1
  )
)

where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo WARNING: ffmpeg not found on PATH - thumbnails, shrinking and mobile conversion will be off.
  echo          Install it with: winget install Gyan.FFmpeg
  timeout /t 5 >nul
)

start "" ".venv\Scripts\pythonw.exe" -m reelgrab.archive.app
