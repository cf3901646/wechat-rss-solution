# 云端 VPS 极简部署指南（路线 A：we-mp-rss 方案）

本文档指导你如何在 1 核 1G 的轻量云服务器（VPS）上快速完成微信公众号转 RSS 服务的搭建与全自动运行。

---

## 一、 前置准备

1. **一台云服务器（VPS）**：
   - 推荐系统：Ubuntu 20.04 / 22.04 / 24.04 或 Debian 11 / 12。
   - 最低配置：1 核 CPU、1GB 内存（每月成本约 10至25 元左右，如腾讯云轻量、阿里云轻量或各类海外 VPS）。
2. **云厂商控制台放行端口**：
   - 进入服务器管理后台的【防火墙 / 安全组】规则，添加放行：**TCP 8001** 端口。

---

## 二、 极速一键部署（3 分钟搞定）

通过 SSH 连接到你的 VPS，执行以下一键安装命令：

```bash
# 1. 切换为 root 权限
sudo su

# 2. 创建安装目录并进入
mkdir -p /opt/we-mp-rss && cd /opt/we-mp-rss

# 3. 将本项目 docker/ 目录下的文件复制或上传至 /opt/we-mp-rss
# 或者直接写入 docker-compose.yml：
cat << 'EOF' > docker-compose.yml
services:
  we-mp-rss:
    image: ghcr.io/rachelos/we-mp-rss:latest
    container_name: we-mp-rss
    restart: unless-stopped
    ports:
      - "8001:8001"
    volumes:
      - ./data:/app/data
    environment:
      - TZ=Asia/Shanghai
    deploy:
      resources:
        limits:
          memory: 512M
EOF

# 4. 启动服务
docker compose up -d
```

*(如果尚未安装 Docker，可以运行项目内附带的 `bash docker/deploy-vps.sh`，脚本会自动检测并自动安装 Docker 与 Docker Compose)*。

---

## 三、 首次使用与微信绑定

1. 在电脑浏览器打开：`http://<你的VPS公网IP>:8001`。
2. 页面会提示扫码登录微信读书：
   - 建议使用**微信小号**（或平时不常用的备用微信）。
   - 打开手机微信扫一扫，确认登录授权。
3. 授权成功后，进入管理面板：
   - 点击【添加账号 / 订阅】，搜索你想关注的微信公众号名称并添加。
   - `we-mp-rss` 会在后台自动为你建立该公众号的文章索引。
4. 获取 RSS 订阅链接：
   - 在面板列表中找到已添加的公众号，点击【复制 RSS】按钮。
   - 链接格式通常为：`http://<你的VPS公网IP>:8001/feeds/<id>.xml`，或者聚合源 `http://<你的VPS公网IP>:8001/feeds/all.xml`。

---

## 四、 云端自动化定时归档（Cron 调度）

如果你希望这台 1 核 VPS 不仅能提供 RSS，还能**7×24 小时自动把文章下载成 Markdown 保存下来**：

1. 将本项目的 `scripts/` 目录和 `config/config.yaml` 复制到 VPS 的 `/opt/wechat-sync`：
   ```bash
   mkdir -p /opt/wechat-sync
   # 安装运行环境需要的 Python 库
   pip3 install pyyaml requests beautifulsoup4
   ```
2. 修改 `/opt/wechat-sync/config.yaml`：
   ```yaml
   rss_feeds:
     - name: "我的公众号聚合"
       url: "http://localhost:8001/feeds/all.xml"
       enabled: true
   storage:
     output_dir: "/opt/wechat-sync/articles"
     download_images: true
   ```
3. 配置系统 Crontab（每 4 小时自动同步一次）：
   ```bash
   crontab -e
   ```
   在末尾添加一行：
   ```bash
   0 */4 * * * /usr/bin/python3 /opt/wechat-sync/scripts/rss_sync.py -c /opt/wechat-sync/config.yaml >> /opt/wechat-sync/sync.log 2>&1
   ```

---

## 五、 日常维护与命令清单

| 操作 | 对应命令 |
| :--- | :--- |
| **查看实时运行日志** | `cd /opt/we-mp-rss && docker compose logs -f` |
| **平滑升级版本** | 运行项目中的 `bash docker/vps-update.sh` |
| **重启服务** | `cd /opt/we-mp-rss && docker compose restart` |
| **停止服务** | `cd /opt/we-mp-rss && docker compose down` |
| **备份数据库** | 拷贝 `/opt/we-mp-rss/data/db.db` 即可 |
