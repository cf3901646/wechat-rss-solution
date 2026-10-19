# -*- coding: utf-8 -*-
"""
微信公众号 RSS 自动拉取与 Markdown 知识库同步引擎
核心功能：
1. 定时拉取配置的 RSS 源（由 we-mp-rss 或其他服务生成）
2. 精准去重（基于文章 URL / GUID 增量记录）
3. 自动解析微信文章 HTML，转换为规范的 Markdown 格式
4. 自动下载微信防盗链图片至本地，并替换为相对路径
5. 自动注入 YAML Frontmatter，完美兼容 Obsidian / Logseq / Notion
6. 支持运行单次（供 cron/任务计划程序调度）或守护进程轮询（--daemon）
"""

import os
import re
import sys
import time
import json
import hashlib
import logging
import logging.handlers
import argparse
import datetime

# 保证 Windows 命令行控制台输出中文字符串不报错
if sys.platform.startswith("win"):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from typing import Dict, List, Any, Optional, Tuple
import xml.etree.ElementTree as ET

import yaml
import requests

# 导入同目录下的转换器和推送模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from html_to_markdown import WechatHtmlToMarkdown
from notify import Notifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("RssSync")

# 项目根目录（scripts/ 的上一级）。配置文件里的相对路径统一以此为基准解析，
# 这样无论从哪个工作目录、用哪个位置的 config 启动，产物都落在项目根下的同一处。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def setup_file_logging(log_dir: Optional[str] = None) -> str:
    """
    追加一个滚动文件日志处理器。

    定时任务用 pythonw.exe 静默运行时没有控制台，只靠 stderr 的话出错完全不可见，
    因此把同样的日志写进 logs/sync.log，便于事后排查。
    """
    log_dir = log_dir or os.path.join(PROJECT_ROOT, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "sync.log")

    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )
    )
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)
    return log_path


def sanitize_filename(filename: str, max_bytes: int = 180) -> str:
    """
    清理 Windows/Linux 文件名非法字符，并按 UTF-8 字节数安全截断。

    按字节而不是按字符截断：ext4 等文件系统的单个文件名上限是 255 **字节**，
    一个中文字符占 3 字节，按字符限长在纯中文标题下依然可能超限。
    """
    # 替换 Windows 非法字符 \ / : * ? " < > |
    cleaned = re.sub(r'[\\/*?:"<>|]', '_', filename)
    # 过滤换行符、制表符等
    cleaned = re.sub(r'[\r\n\t]+', ' ', cleaned).strip()
    # Windows 会静默丢弃结尾的点和空格，先自行去掉避免文件名与预期不一致
    cleaned = cleaned.rstrip(". ")

    encoded = cleaned.encode("utf-8")
    if len(encoded) > max_bytes:
        # errors="ignore" 用于丢弃截断处可能出现的半个多字节字符
        cleaned = encoded[:max_bytes].decode("utf-8", errors="ignore").rstrip(". ").strip()

    return cleaned or "untitled"


class RssSyncEngine:
    def __init__(self, config_path: str):
        self.config_path = os.path.abspath(config_path)
        # 相对路径以项目根目录为基准，而不是 config 文件所在目录。
        # 否则 output_dir: "./output/articles" 会被解析成 <项目>/config/output/articles，
        # 与 README / WINDOWS_GUIDE 里描述的 <项目>/output/ 不符。
        self.base_dir = PROJECT_ROOT
        self.config = self._load_config()

        # 初始化输出与历史路径
        storage_cfg = self.config.get("storage", {})
        output_dir = storage_cfg.get("output_dir", "./output/articles")
        if not os.path.isabs(output_dir):
            output_dir = os.path.normpath(os.path.join(self.base_dir, output_dir))
        self.output_dir = output_dir

        images_name = storage_cfg.get("images_dir_name", "images")
        self.images_dir = os.path.join(self.output_dir, images_name)
        self.images_rel_path = images_name

        history_path = storage_cfg.get("history_db_path", "./output/sync_history.json")
        if not os.path.isabs(history_path):
            history_path = os.path.normpath(os.path.join(self.base_dir, history_path))
        self.history_path = history_path

        # 加载历史同步记录
        self.history = self._load_history()

        # 初始化转换器
        cleaner_cfg = self.config.get("cleaner", {})
        self.converter = WechatHtmlToMarkdown(
            download_images=storage_cfg.get("download_images", True),
            images_dir=self.images_dir,
            images_rel_path=self.images_rel_path,
            remove_ads=cleaner_cfg.get("remove_ads", True),
            filter_keywords=cleaner_cfg.get("filter_keywords", None)
        )

        # 初始化通知器
        notify_cfg = self.config.get("notification", {})
        self.notifier = Notifier(
            channel=notify_cfg.get("channel", "none"),
            webhook_url=notify_cfg.get("webhook_url", "")
        )

        # 网络会话
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        })

    def _load_config(self) -> Dict[str, Any]:
        """读取 YAML 配置文件"""
        if not os.path.exists(self.config_path):
            raise FileNotFoundError(f"配置文件不存在: {self.config_path}")
        with open(self.config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _load_history(self) -> Dict[str, Any]:
        """
        加载已同步文章历史记录。

        必须校验结构：文件被手工编辑成 `{}` 或换了格式时，直接返回原始 JSON
        会让后续所有 self.history["synced_items"] 抛 KeyError。
        """
        if os.path.exists(self.history_path):
            try:
                with open(self.history_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and isinstance(data.get("synced_items"), dict):
                    return data
                logger.warning(
                    f"历史记录结构不符合预期（缺少 synced_items 字典），将重新初始化: {self.history_path}"
                )
            except Exception as e:
                logger.warning(f"读取历史记录失败，将重新初始化: {e}")
        return {"synced_items": {}}

    def _save_history(self):
        """保存历史记录到文件"""
        os.makedirs(os.path.dirname(self.history_path), exist_ok=True)
        with open(self.history_path, "w", encoding="utf-8") as f:
            json.dump(self.history, f, ensure_ascii=False, indent=2)

    def _parse_xml_feed(self, xml_content: str, default_author: str = "") -> List[Dict[str, Any]]:
        """
        通用 RSS 2.0 与 Atom XML 解析引擎（无需额外依赖库）
        """
        articles = []
        try:
            root = ET.fromstring(xml_content)
        except Exception as e:
            logger.error(f"XML 解析失败: {e}")
            return []

        tag_lower = root.tag.lower()

        # 1. 解析 RSS 2.0
        channel = root.find("channel")
        if channel is not None or "rss" in tag_lower:
            ch = channel if channel is not None else root
            feed_title = (ch.findtext("title") or default_author or "微信公众号").strip()

            for item in ch.findall("item"):
                title = (item.findtext("title") or "无标题").strip()
                link = (item.findtext("link") or "").strip()
                guid = (item.findtext("guid") or link or title).strip()
                author = (
                    item.findtext("author")
                    or item.findtext("{http://purl.org/dc/elements/1.1/}creator")
                    or feed_title
                ).strip()
                pub_date = (item.findtext("pubDate") or "").strip()

                # 获取正文内容（优先 content:encoded，其次 description）
                content = (
                    item.findtext("{http://purl.org/rss/1.0/modules/content/}encoded")
                    or item.findtext("description")
                    or ""
                )

                articles.append({
                    "guid": guid,
                    "title": title,
                    "link": link,
                    "author": author,
                    "pub_date": pub_date,
                    "content_html": content
                })
            return articles

        # 2. 解析 Atom 1.0
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall("atom:entry", ns) or root.findall("entry")
        feed_title = (root.findtext("atom:title", namespaces=ns) or root.findtext("title") or default_author).strip()

        for entry in entries:
            title = (entry.findtext("atom:title", namespaces=ns) or entry.findtext("title") or "无标题").strip()
            # 一个 entry 可以有多个 <link>（alternate / self / enclosure / edit ...），
            # 取第一个可能拿到 self 或 edit 链接，必须优先 rel="alternate"（缺省即 alternate）
            link_els = entry.findall("atom:link", ns) or entry.findall("link")
            link = ""
            for link_el in link_els:
                if (link_el.get("rel") or "alternate").lower() == "alternate":
                    link = link_el.get("href", "")
                    break
            if not link and link_els:
                link = link_els[0].get("href", "")
            guid = (entry.findtext("atom:id", namespaces=ns) or entry.findtext("id") or link).strip()
            author_el = entry.find("atom:author/atom:name", ns) if entry.find("atom:author/atom:name", ns) is not None else entry.find("author/name")
            author = (author_el.text if author_el is not None and author_el.text else feed_title).strip()
            pub_date = (entry.findtext("atom:published", namespaces=ns) or entry.findtext("atom:updated", namespaces=ns) or "").strip()

            content_el = entry.find("atom:content", ns) if entry.find("atom:content", ns) is not None else entry.find("content")
            content = content_el.text if content_el is not None and content_el.text else ""

            articles.append({
                "guid": guid,
                "title": title,
                "link": link,
                "author": author,
                "pub_date": pub_date,
                "content_html": content
            })

        return articles

    def _fetch_full_article_html(self, url: str) -> str:
        """当 RSS 仅提供摘要时，直接从微信文章链接抓取完整网页"""
        if not url or not url.startswith("http"):
            return ""
        try:
            resp = self.session.get(url, timeout=15)
            if resp.status_code == 200:
                resp.encoding = "utf-8"
                return resp.text
        except Exception as e:
            logger.warning(f"抓取原文 HTML 失败 [{url}]: {e}")
        return ""

    def _format_date(self, raw_date: str) -> str:
        """归一化日期格式为 YYYY-MM-DD"""
        if not raw_date:
            return datetime.datetime.now().strftime("%Y-%m-%d")
        # 尝试标准 RFC822 / ISO8601
        for fmt in [
            "%a, %d %b %Y %H:%M:%S %z",
            "%a, %d %b %Y %H:%M:%S GMT",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d"
        ]:
            try:
                dt = datetime.datetime.strptime(raw_date.strip(), fmt)
                return dt.strftime("%Y-%m-%d")
            except Exception:
                pass
        # 正则提取年月日
        match = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', raw_date)
        if match:
            y, m, d = match.groups()
            return f"{y}-{int(m):02d}-{int(d):02d}"
        return datetime.datetime.now().strftime("%Y-%m-%d")

    def _resolve_target_path(self, date_str: str, title: str, guid: str) -> Tuple[str, str]:
        """
        计算 Markdown 落盘路径，避免"同一天 + 同标题"的不同文章互相覆盖。

        命中冲突时追加 guid 摘要后缀。force 重新同步时，同一 guid 仍复用它原来的
        文件名（而不是不断堆积新文件）。
        """
        safe_title = sanitize_filename(title)
        filename = f"{date_str}_{safe_title}.md"
        file_path = os.path.join(self.output_dir, filename)

        recorded = self.history["synced_items"].get(guid, {}).get("saved_path") or ""
        if recorded:
            # 绝对路径完全一致
            if os.path.normcase(os.path.abspath(recorded)) == os.path.normcase(os.path.abspath(file_path)):
                return filename, file_path
            # 跨机或路径变动兼容：基于当前 self.output_dir 与 basename 检查
            rec_basename = os.path.basename(recorded)
            if rec_basename:
                compat_path = os.path.join(self.output_dir, rec_basename)
                if os.path.normcase(compat_path) == os.path.normcase(file_path) or os.path.exists(compat_path):
                    return rec_basename, compat_path

        if os.path.exists(file_path):
            suffix = hashlib.md5(guid.encode("utf-8")).hexdigest()[:8]
            filename = f"{date_str}_{safe_title}_{suffix}.md"
            file_path = os.path.join(self.output_dir, filename)

        return filename, file_path

    def sync_feed(self, feed: Dict[str, Any], dry_run: bool = False, force: bool = False) -> List[Dict[str, Any]]:
        """同步单个 RSS 源中的新文章"""
        name = feed.get("name", "未命名源")
        url = feed.get("url", "")
        if not url:
            logger.warning(f"源 [{name}] URL 为空，跳过")
            return []

        logger.info(f"正在拉取 RSS 源: [{name}] -> {url}")
        try:
            resp = self.session.get(url, timeout=20)
            if resp.status_code != 200:
                logger.error(f"源 [{name}] 访问失败 HTTP {resp.status_code}")
                return []
            resp.encoding = "utf-8"
            xml_text = resp.text
        except Exception as e:
            logger.error(f"网络请求异常 [{name}]: {e}")
            return []

        articles = self._parse_xml_feed(xml_text, default_author=name)
        logger.info(f"源 [{name}] 成功解析出 {len(articles)} 篇文章")

        new_synced = []
        os.makedirs(self.output_dir, exist_ok=True)

        for art in articles:
            guid = art["guid"]
            # 增量去重检查
            if not force and guid in self.history["synced_items"]:
                continue

            title = art["title"]
            author = art["author"] or name
            link = art["link"]
            date_str = self._format_date(art["pub_date"])
            content_html = art["content_html"]

            # 如果 RSS 中的 HTML 太短（只有几十字摘要），尝试抓取原文链接获取完整富文本
            if len(content_html) < 200 and link:
                full_html = self._fetch_full_article_html(link)
                if full_html and len(full_html) > len(content_html):
                    content_html = full_html

            if dry_run:
                logger.info(f"[Dry-Run] 发现新文章: {title} ({author}, {date_str})")
                new_synced.append(art)
                continue

            # 转换为 Markdown
            metadata = {
                "title": title,
                "author": author,
                "date": date_str,
                "url": link
            }
            md_content = self.converter.convert(content_html, metadata=metadata)

            # 格式化文件名
            filename, file_path = self._resolve_target_path(date_str, title, guid)

            # 写入 Markdown 文件
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(md_content)

            # 记录历史
            self.history["synced_items"][guid] = {
                "title": title,
                "author": author,
                "link": link,
                "date": date_str,
                "saved_path": file_path,
                "sync_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            new_synced.append(art)
            logger.info(f"✅ 已落盘: {filename}")

        if not dry_run and new_synced:
            self._save_history()

        return new_synced

    def run_once(self, dry_run: bool = False, force: bool = False):
        """执行一次全量同步任务"""
        feeds = self.config.get("rss_feeds", [])
        enabled_feeds = [f for f in feeds if f.get("enabled", True)]
        if not enabled_feeds:
            logger.warning("当前没有启用的 RSS 订阅源，请在 config.yaml 中配置")
            return

        logger.info(f"开始执行同步任务，共有 {len(enabled_feeds)} 个启用的订阅源")
        all_new_articles = []
        errors = []

        for feed in enabled_feeds:
            try:
                synced = self.sync_feed(feed, dry_run=dry_run, force=force)
                all_new_articles.extend(synced)
            except Exception as e:
                err_msg = f"源 [{feed.get('name')}] 处理出错: {e}"
                logger.error(err_msg, exc_info=True)
                errors.append(err_msg)

        logger.info(f"同步任务完成！本次新增文章: {len(all_new_articles)} 篇")

        # 发送通知
        notify_cfg = self.config.get("notification", {})
        if notify_cfg.get("notify_on_new_articles", True) and all_new_articles:
            msg_lines = [f"本次增量抓取到 {len(all_new_articles)} 篇新文章：\n"]
            for a in all_new_articles[:10]:
                msg_lines.append(f"- 【{a.get('author')}】{a.get('title')}")
            if len(all_new_articles) > 10:
                msg_lines.append(f"... 等共 {len(all_new_articles)} 篇")
            self.notifier.send(
                title=f"微信公众号文章已同步 ({len(all_new_articles)}篇)",
                content="\n".join(msg_lines)
            )

        if notify_cfg.get("notify_on_failure", True) and errors:
            self.notifier.send(
                title="微信文章同步异常告警",
                content="\n".join(errors)
            )

    def run_daemon(self):
        """常驻后台轮询调度模式"""
        interval = self.config.get("scheduler", {}).get("interval_minutes", 60)
        logger.info(f"启动守护进程模式，每 {interval} 分钟轮询一次...")
        while True:
            try:
                self.run_once()
            except Exception as e:
                logger.error(f"轮询任务执行异常: {e}", exc_info=True)
            logger.info(f"等待下一次轮询，休眠 {interval} 分钟...")
            time.sleep(interval * 60)


def main():
    parser = argparse.ArgumentParser(description="微信公众号 RSS 自动拉取与 Markdown 归档")
    parser.add_argument("-c", "--config", default="./config/config.yaml", help="配置文件路径")
    parser.add_argument("-d", "--daemon", action="store_true", help="常驻后台守护进程模式")
    parser.add_argument("--dry-run", action="store_true", help="仅演练拉取，不写文件")
    parser.add_argument("--force", action="store_true", help="强制重新同步历史文章")
    parser.add_argument("--no-log-file", action="store_true", help="不写入 logs/sync.log")
    args = parser.parse_args()

    if not args.no_log_file:
        log_path = setup_file_logging()
        logger.info(f"日志文件: {log_path}")

    engine = RssSyncEngine(config_path=args.config)
    logger.info(f"配置文件: {engine.config_path}")
    logger.info(f"Markdown 输出目录: {engine.output_dir}")
    logger.info(f"历史记录文件: {engine.history_path}")

    if args.daemon:
        engine.run_daemon()
    else:
        engine.run_once(dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    main()
