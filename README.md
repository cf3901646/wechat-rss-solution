# 微信公众号知识库助手 (WeChat RSS & Knowledge Base Assistant)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10+-brightgreen.svg)](https://www.python.org/)
[![Docker: Ready](https://img.shields.io/badge/Docker-Ready-2496ED.svg)](DOCKER_GUIDE.md)

微信公众号转 RSS 订阅、高保真 Markdown 知识库本地归档与离线单文件 HTML 阅读管理系统。

---

## 🌟 核心特性与亮点

1. **双轨灵活运行架构**：
   - **极简本地模式（推荐）**：基于微信读书官方通道，微信直接扫码授权，零封号风险；支持直接运行 `启动知识库助手.bat` 或 Docker 一键启动，无需搭建复杂云端后端；
   - **云端 RSS 引擎模式**：支持与 `we-mp-rss`、私有 VPS 无缝集成，提供标准 RSS/Atom 源，供各类 RSS 阅读器（Follow、NetNewsWire 等）或后台 Cron 定时拉取。
2. **高保真排版与防盗链图片本地化**：
   - 微信文章图片普遍采用 `data-src` 延迟加载及防盗链机制。内置的清洗引擎会自动将所有高清配图下载至本地 `output/articles/images/`，并转换为标准 Markdown 相对链接，确保在 **Obsidian、Logseq、Notion** 中永不失效；
   - 支持生成自包含、内嵌 Base64 真实图片的独立单文件 HTML，脱机双击浏览器直接打开。
3. **沉浸式 Web 知识库门户与阅读器**：
   - 内置 Web 视窗控制台（默认端口 `8002`），集成微信扫码、书架公众号多选管理、时间范围筛选（最近 7 天 / 30 天 / 90 天 / 全量）；
   - 双栏知识库检索与时间排序阅读器，支持快捷键（`[` 折叠左侧列表、`]` 折叠顶部管理面板、`/` 快速聚焦搜索）；
   - 原生支持 **「一键复制全文 (图文)」**（将文字与本地图片直接以富文本贴入微信、Word、飞书）与 **「复制纯文本」**。
4. **PC 微信搜一搜实时嗅探与剪贴板监听**：
   - 内置基于 mitmproxy 的搜一搜嗅探模块（`scripts/search_sniffer.py`），用户在微信电脑端点击或搜索文章即时静默归档；
   - 包含剪贴板监听守护线程，只要复制公众号链接即可自动触发深度抓取。
5. **增量精准去重与多渠道推送**：
   - 基于全局唯一 GUID / URL 记录在 `sync_history.json` 中，无论触发多少次同步均不重复下载；
   - 支持飞书、企业微信、钉钉、Server酱、Bark 等多渠道 Webhook 报警与同步成功通知。

---

## 📁 目录结构

```text
wechat-rss-solution/
├── config/                             # 【配置文件目录】
│   ├── config.example.yaml             # 完整配置参数模板与中文注释
│   └── config.yaml                     # 当前生效的主配置文件
├── core/                               # 【控制台核心工具库】
│   └── print.py                        # 跨平台彩色终端与日志打印工具
├── docker/                             # 【云端 VPS 部署配置】
│   ├── docker-compose.yml              # we-mp-rss 官方镜像 Docker-Compose 配置
│   ├── deploy-vps.sh                   # Linux VPS 一键安装部署脚本
│   ├── vps-update.sh                   # 数据库备份与平滑升级脚本
│   └── Caddyfile                       # 域名自动 HTTPS 反向代理模板
├── docs/                               # 【详细使用与维护手册】
│   ├── DEPLOY_VPS.md                   # 云端 1 核 VPS 极简部署指南
│   ├── WINDOWS_GUIDE.md                # Windows 本地定时任务详细设置教程
│   └── WEWE_RSS_GUIDE.md               # 扫码绑定、添加公众号与防风控操作指南
├── driver/                             # 【微信底层接口驱动】
│   └── weread_qr.py                    # 微信读书扫码授权、凭据维护与文章检索驱动
├── scripts/                            # 【核心业务脚本与工具】
│   ├── web_portal.py                   # 微信公众号知识库本地 Web 控制台与阅读器
│   ├── sync_weread_direct.py           # 本地直连微信读书增量抓取与归档脚本
│   ├── rss_sync.py                     # 标准 RSS 源定时抓取与 Markdown 转换引擎
│   ├── search_sniffer.py               # PC 微信搜一搜嗅探与剪贴板监听服务
│   ├── html_to_markdown.py             # 微信专用 HTML 转 Markdown 清洗转换引擎
│   ├── html_exporter.py                # 独立单文件 HTML 导出器（含 Base64 图片内嵌）
│   ├── notify.py                       # 多渠道通知推送模块
│   └── run_demo_test.py                # 本地端到端全链路模拟验证脚本
├── windows/                            # 【Windows 常用快捷工具集】
│   ├── start_portal.bat                # 启动 Web 知识库门户
│   ├── start_sync.bat                  # 运行一次增量同步
│   ├── setup_scheduled_task.ps1        # 注册开机后台静默定时任务
│   └── remove_scheduled_task.ps1       # 注销定时任务
├── Dockerfile                          # 生产级全内置容器镜像构建文件
├── docker-compose.yml                  # 容器编排配置（自动卷挂载与端口映射）
├── docker_start.bat / .sh              # Docker 容器一键启动脚本
├── docker_stop.bat / .sh               # Docker 容器一键停止脚本
├── 启动知识库助手.bat                   # Windows 根目录一键启动 Web 门户
├── 全自动搜索并爬取.bat                 # Windows 根目录一键启动搜一搜全自动爬取
├── 一键打包开箱版.bat                   # 一键导出安全绿色的离线 ZIP 分发包
├── requirements.txt                    # Python 依赖清单
└── LICENSE                             # 开源授权协议 (MIT)
```

> **路径约定**：所有相对路径（如 `output/articles`、`data/`）均以**项目根目录**为基准进行解析。

---

## 🚀 快速上手

### 方式一：Docker 容器化一键运行（推荐，全平台免配环境）

无需在本地电脑安装 Python 或配置虚拟环境，所有依赖、驱动与控制台已内置打包：

1. **Windows 用户**：
   - 确保 Docker Desktop 已启动；
   - 直接双击运行根目录下的 **`docker_start.bat`**；
   - 浏览器将自动打开管理面板：**http://localhost:8002**。
2. **Mac / Linux 用户**：
   ```bash
   chmod +x *.sh
   ./docker_start.sh
   ```
3. **停止服务**：
   - Windows 双击 **`docker_stop.bat`** 或终端执行 `docker compose down`。

---

### 方式二：本地 Python 环境运行

#### 1. 安装环境与依赖

本项目支持 Python 3.10 至 3.12：

```bash
# Windows
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

# Linux / macOS
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

#### 2. 启动可视化管理门户

- **Windows 用户**：直接双击根目录下的 **`启动知识库助手.bat`**；
- **命令行启动**：
  ```bash
  python scripts/web_portal.py 8002
  ```
- 访问 **http://127.0.0.1:8002**：
  - 点击「微信扫码绑定」，使用微信扫描二维码授权；
  - 勾选你需要同步的公众号；
  - 选择同步时间跨度（最近 7 天 / 30 天 / 90 天 / 全部历史）；
  - 点击「立即增量同步」，文章将实时抓取并呈现于左侧列表与右侧阅读器！

---

### 方式三：PC 微信搜一搜实时嗅探

如果你需要在电脑端边看边存，或通过微信客户端搜一搜批量嗅探：

1. 双击根目录下的 **`安装嗅探证书.bat`** 安装本地信任根证书（首次需要）；
2. 双击 **`启动搜一搜监听助手.bat`**；
3. 打开电脑微信搜一搜任意文章，文章将实时下载配图、生成 Markdown 与独立离线 HTML；
4. 退出时双击 **`关闭系统代理.bat`** 确保网络设置即时还原。

---

## 🧪 自动化测试验证

本方案内置了完整的单元测试、集成测试与端到端模拟测试套件：

### 1. 运行完整自动化测试套件

```bash
python -m unittest discover -s scripts -p "test_*.py" -v
```

涵盖 47 个全景测试用例：
- RSS / Atom 协议解析与增量去重；
- HTML 清洗、延迟加载 `data-src` 还原、公式与代码块转换；
- 图片二进制格式合法性校验与防盗链拦截保护；
- 独立单文件 HTML 生成器与 Base64 转换；
- Web 门户扫码凭据管理与状态机调度；
- 嗅探器代理管理与网络复原机制。

### 2. 运行端到端全链路模拟验证

```bash
python scripts/run_demo_test.py
```
该测试会在本地快速启动模拟 RSS 服务，完整走通「拉取 -> 增量清洗 -> 转换 Markdown -> 下载图片 -> 写入历史索引」全流程。

---

## ⚙️ 常见问题 (FAQ)

### Q1：抓取的文章保存在哪里？
所有已下载文章、Markdown 原文及高清配图默认存放在 **`output/articles/`** 目录：
- `output/articles/*.md`：包含元数据的 Markdown 原文；
- `output/articles/*.html`：自包含图片的单文件 HTML（双击任意浏览器即可离线查看）；
- `output/articles/images/`：下载的本地高清配图。

### Q2：微信扫码凭据有效期多久？
微信读书扫码凭据通常具备 **1至2 周左右** 的有效生命周期。失效后只需在 Web 控制台点击重新扫码即可无缝续期。

---

## 📄 开源许可证

本项目基于 [MIT License](LICENSE) 开源。
