#!/bin/sh
set -e

# 确保持久化目录存在
mkdir -p /app/data /app/output/articles /app/logs /app/config

# 如果挂载的配置目录下尚未创建 config.yaml，自动初始化默认模板
if [ ! -f /app/config/config.yaml ] && [ -f /app/config/config.example.yaml ]; then
    echo "[Docker Init] 首次运行，自动初始化 /app/config/config.yaml..."
    cp /app/config/config.example.yaml /app/config/config.yaml
fi

echo "=========================================================="
echo "    微信公众号知识库助手 (Docker 容器化服务)"
echo "=========================================================="
echo "[*] 时区: $(cat /etc/timezone 2>/dev/null || echo 'Asia/Shanghai')"
echo "[*] 端口: ${PORT:-8002} (绑定地址: ${HOST:-0.0.0.0})"
echo "[*] 本地文章库路径: /app/output/articles"
echo "[*] 正在启动可视化管理门户..."
echo "=========================================================="

exec python scripts/web_portal.py --host "${HOST:-0.0.0.0}" --port "${PORT:-8002}"
