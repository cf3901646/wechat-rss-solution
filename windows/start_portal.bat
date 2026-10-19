@echo off
cd /d "%~dp0.."
set AUTO_OPEN_BROWSER=1

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Python environment not found: .venv\Scripts\python.exe
    pause
    exit /b 1
)

".venv\Scripts\python.exe" "scripts\web_portal.py" 8002
pause
