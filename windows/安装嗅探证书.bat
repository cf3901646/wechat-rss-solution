@echo off
title Install Sniffer Certificate
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Python virtual environment not found in .venv!
    pause
    exit /b 1
)

".venv\Scripts\python.exe" "scripts\search_sniffer.py" --install-cert
pause
