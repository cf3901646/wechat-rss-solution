@echo off
cd /d "%~dp0.."

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "scripts\reset_keyboard.py"
) else (
    python "scripts\reset_keyboard.py"
)

pause
