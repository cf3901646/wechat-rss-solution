# -*- coding: utf-8 -*-
"""
智能公众号同步调度器
自动识别运行模式：
1. 若用户配置了有效的外部 RSS 源，或本地 8001 端口服务已启动 -> 走 RSS 订阅抓取引擎
2. 若仅配置了默认的 localhost:8001 且端口未启动 -> 自动无缝切换至【微信读书原生直连引擎】（无需 Docker 与端口）
"""

import os
import sys
import yaml
import socket
import logging

# 保证 Windows 控制台 UTF-8
if sys.platform.startswith("win"):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "scripts"))

from rss_sync import setup_file_logging, RssSyncEngine
from sync_weread_direct import WereadDirectSyncEngine

logger = logging.getLogger("SyncRunner")


def is_port_open(host="127.0.0.1", port=8001, timeout=0.8) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def main():
    setup_file_logging()
    config_path = os.path.join(PROJECT_ROOT, "config", "config.yaml")

    feeds = []
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
                feeds = [f for f in cfg.get("rss_feeds", []) if f.get("enabled", True)]
        except Exception:
            pass

    # 检查是否全部是未启动的 localhost:8001
    only_default_localhost = False
    if feeds and all("localhost:8001" in f.get("url", "") or "127.0.0.1:8001" in f.get("url", "") for f in feeds):
        only_default_localhost = True

    if (only_default_localhost and not is_port_open(port=8001)) or not feeds:
        print("\n" + "=" * 60)
        print("💡 [智能调度] 检测到本地未运行 8001 端口服务")
        print("🚀 已自动为您切换至【微信读书原生直连模式】（免 Docker，极速稳定）")
        print("=" * 60 + "\n")
        engine = WereadDirectSyncEngine(config_path)
        engine.run()
    else:
        engine = RssSyncEngine(config_path)
        for feed in feeds:
            engine.sync_feed(feed)


if __name__ == "__main__":
    main()
