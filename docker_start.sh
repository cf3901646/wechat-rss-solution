#!/bin/bash
set -e

echo "============================================================"
echo "        微信公众号知识库助手 (Docker 容器版)"
echo "============================================================"

# 切换到脚本所在项目根目录
cd "$(dirname "$0")"

# 检查 Docker 守护进程
if ! docker info >/dev/null 2>&1; then
    echo "[错误] 未检测到 Docker 正在运行，请启动 Docker Desktop 或 docker daemon 后重试！"
    exit 1
fi

echo "[*] 正在构建并后台启动容器服务..."
if docker compose version >/dev/null 2>&1; then
    docker compose up -d --build
else
    docker-compose up -d --build
fi

echo ""
echo "============================================================"
echo "[成功] 知识库助手已顺利在后台运行！"
echo "本地访问地址: http://localhost:8002"
echo "============================================================"

# 尝试唤起系统默认浏览器
if command -v open >/dev/null 2>&1; then
    sleep 1
    open http://localhost:8002
elif command -v xdg-open >/dev/null 2>&1; then
    sleep 1
    xdg-open http://localhost:8002
fi
