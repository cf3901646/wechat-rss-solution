#!/bin/bash
set -e

cd "$(dirname "$0")"

echo "============================================================"
echo "    正在停止微信公众号知识库助手 Docker 服务..."
echo "============================================================"

if docker compose version >/dev/null 2>&1; then
    docker compose down
else
    docker-compose down
fi

echo ""
echo "============================================================"
echo "[OK] 容器已安全停止，数据完整保存在本地 data 与 output 目录中。"
echo "============================================================"
