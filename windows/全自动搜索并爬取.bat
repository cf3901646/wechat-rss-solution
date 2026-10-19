@echo off
title WeChat Auto Crawler
cd /d "%~dp0.."

set "PY_CMD=.venv\Scripts\python.exe"
if not exist "%PY_CMD%" (
    where python >nul 2>nul
    if %errorlevel% equ 0 (
        set "PY_CMD=python"
    ) else (
        echo [ERROR] Python not found in .venv or PATH!
        pause
        exit /b 1
    )
)

"%PY_CMD%" "scripts\auto_wechat_crawler.py" %*

if exist "scripts\reset_keyboard.py" (
    "%PY_CMD%" "scripts\reset_keyboard.py"
)

pause
