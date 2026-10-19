@echo off
chcp 65001 > nul
title 微信公众号单文件 HTML 批量导出工具
cd /d "%~dp0.."
echo ====================================================
echo    微信公众号文章批量导出为单文件 HTML (图文一体)
echo ====================================================
echo.
if not exist ".venv\Scripts\python.exe" (
    echo [错误] 未找到项目虚拟环境: .venv\Scripts\python.exe
    pause
    exit /b 1
)
".venv\Scripts\python.exe" "scripts\html_exporter.py"
echo.
echo ====================================================
echo 批量导出任务已结束！
echo 所有单文件 HTML 已生成在 output\articles 目录。
echo 您可直接在文件管理器中双击打开任意 .html 查看与复制。
echo ====================================================
pause
