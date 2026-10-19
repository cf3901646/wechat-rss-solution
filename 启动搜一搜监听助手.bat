@echo off
title PC WeChat Search Sniffer
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Python virtual environment not found in .venv!
    pause
    exit /b 1
)

".venv\Scripts\python.exe" "scripts\search_sniffer.py" --port 8080 --auto-proxy
pause
