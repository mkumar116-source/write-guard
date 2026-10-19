@echo off
title Write Guard
REM Write Guard launcher. Creates venv on first run, installs requirements, starts the app.
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv venv || goto :error
)

echo Installing / checking requirements...
"venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
"venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt || goto :error

echo Starting Write Guard...
echo Open http://localhost:8511 in your browser.
start "" powershell -NoProfile -WindowStyle Hidden -File "D:\Flask Tools\common\open_when_ready.ps1" -Port 8511
"venv\Scripts\python.exe" app.py
goto :eof

:error
echo.
echo Something went wrong. See the message above.
pause
