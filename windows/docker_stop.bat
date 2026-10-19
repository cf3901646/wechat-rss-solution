@echo off
chcp 65001 >nul
title 微信公众号知识库助手 - Docker停止服务

echo ============================================================
echo         正在停止微信公众号知识库助手 Docker 服务...
echo ============================================================
echo.

cd /d "%~dp0\.."

docker compose version >nul 2>&1
if %errorlevel% equ 0 (
    docker compose down
) else (
    docker-compose down
)

echo.
echo ============================================================
echo [OK] 容器已安全停止，数据已完整保存在本地 data 与 output 目录中。
echo ============================================================
pause
