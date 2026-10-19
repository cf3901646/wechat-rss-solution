@echo off
chcp 65001 > nul
title 微信公众号文章 RSS 同步工具
cd /d "%~dp0.."
echo ====================================================
echo        微信公众号文章自动同步与 Markdown 归档
echo ====================================================
echo.
if not exist ".venv\Scripts\python.exe" (
    echo [错误] 未找到项目虚拟环境: .venv\Scripts\python.exe
    echo.
    echo 请先在项目根目录执行以下两条命令安装依赖：
    echo     python -m venv .venv
    echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" "scripts\run_sync.py"
echo.
echo ====================================================
echo 同步任务已结束，日志见 logs\sync.log，按任意键退出窗口...
pause
