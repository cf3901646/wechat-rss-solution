@echo off
chcp 65001 >nul
title 微信公众号知识库助手 - Docker一键启动

echo ============================================================
echo         微信公众号知识库助手 (Docker 容器版)
echo ============================================================
echo.

:: 1. 检查 Docker 是否正在运行
docker info >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未检测到正在运行的 Docker 服务！
    echo.
    echo 请确认：
    echo 1. 本机已安装 Docker Desktop；
    echo 2. Docker Desktop 已启动且状态为 "Engine running"；
    echo.
    echo 启动 Docker Desktop 后，请重新双击此脚本！
    echo ============================================================
    pause
    exit /b 1
)

echo [*] 检测到 Docker 运行正常，正在构筑并启动服务...
cd /d "%~dp0"
if exist "..\docker-compose.yml" cd ..

docker compose version >nul 2>&1
if %errorlevel% equ 0 (
    docker compose up -d --build
) else (
    docker-compose up -d --build
)

if %errorlevel% neq 0 (
    echo [错误] Docker 启动失败，请检查上方日志输出。
    pause
    exit /b 1
)

echo.
echo ============================================================
echo [成功] 微信公众号知识库助手已在 Docker 后台顺利启动！
echo 本地访问地址: http://localhost:8002
echo 正在为您打开默认浏览器，请稍候...
echo ============================================================
timeout /t 2 >nul
start http://localhost:8002
exit /b 0
pause
