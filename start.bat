@echo off
setlocal
cd /d "%~dp0"
echo [reelgrab] %date% %time% > start.log

where docker >nul 2>nul
if errorlevel 1 (
  echo [reelgrab] docker not found on PATH. Install Docker Desktop first. >> start.log
  echo docker not found on PATH. Install Docker Desktop first.
  pause
  exit /b 1
)

docker info >nul 2>nul
if not errorlevel 1 goto build
echo [reelgrab] starting Docker Desktop... >> start.log
echo Starting Docker Desktop...
start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
set /a tries=0
:waitdocker
timeout /t 3 /nobreak >nul
docker info >nul 2>nul
if not errorlevel 1 goto build
set /a tries+=1
if %tries% lss 60 goto waitdocker
echo [reelgrab] docker engine never came up >> start.log
echo Docker engine never came up. Open Docker Desktop manually and rerun.
pause
exit /b 1

:build
echo [reelgrab] docker compose up -d --build >> start.log
echo Building and starting reelgrab (first build takes a minute or two)...
docker compose up -d --build >> start.log 2>&1
if errorlevel 1 (
  echo [reelgrab] compose failed >> start.log
  type start.log
  pause
  exit /b 1
)

set /a tries=0
:waitweb
curl -fs http://localhost:8080/healthz >nul 2>nul
if not errorlevel 1 goto up
set /a tries+=1
if %tries% geq 30 goto up
timeout /t 1 /nobreak >nul
goto waitweb

:up
echo [reelgrab] up at http://localhost:8080 >> start.log
echo reelgrab is running at http://localhost:8080
start "" http://localhost:8080
timeout /t 5 >nul
