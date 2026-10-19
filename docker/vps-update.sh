#!/usr/bin/env bash
# ==============================================================================
# we-mp-rss 一键安全平滑升级与 SQLite 数据库备份脚本
# ==============================================================================

set -e

INSTALL_DIR="/opt/we-mp-rss"
cd "$INSTALL_DIR"

BACKUP_DIR="$INSTALL_DIR/backups"
mkdir -p "$BACKUP_DIR"

DATE_STR=$(date +"%Y%m%d_%H%M%S")

echo "===> 1. 正在备份当前数据库..."
if [ -f "$INSTALL_DIR/data/db.db" ]; then
    cp "$INSTALL_DIR/data/db.db" "$BACKUP_DIR/db_${DATE_STR}.db"
    echo "数据库已备份至: $BACKUP_DIR/db_${DATE_STR}.db"
fi

echo "===> 2. 拉取最新 Docker 镜像..."
if docker compose version &> /dev/null; then
    docker compose pull
    echo "===> 3. 重启容器以应用更新..."
    docker compose down
    docker compose up -d
else
    docker-compose pull
    echo "===> 3. 重启容器以应用更新..."
    docker-compose down
    docker-compose up -d
fi

echo "===> 升级完成！当前服务状态："
docker ps | grep we-mp-rss
