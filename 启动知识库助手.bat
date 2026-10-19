@echo off
title WeChat Knowledge Base Portal
chcp 65001 >nul
cd /d "%~dp0"
set AUTO_OPEN_BROWSER=1

echo ============================================================
echo   WeChat Knowledge Base Portal (Version 1 - No Search)
echo ============================================================
echo.

set "PY_CMD="
if exist ".venv\Scripts\python.exe" (
    set "PY_CMD=.venv\Scripts\python.exe"
) else (
    where python >nul 2>nul
    if %errorlevel% equ 0 set "PY_CMD=python"
)

if "%PY_CMD%"=="" (
    echo [ERROR] Python environment not found!
    echo Please install Python 3.10+ or check .venv
    pause
    exit /b 1
)

echo [*] Starting Web Portal on http://127.0.0.1:8002 ...
echo [*] Browser will open automatically. Press Ctrl+C to stop.
echo.

"%PY_CMD%" "scripts\web_portal.py" 8002

echo.
echo Portal stopped.
pause
