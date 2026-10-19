@echo off
title Disable System Proxy
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
    reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings" /v ProxyEnable /t REG_DWORD /d 0 /f >nul
) else (
    ".venv\Scripts\python.exe" "scripts\search_sniffer.py" --disable-proxy
)
pause
