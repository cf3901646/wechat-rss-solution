# -*- coding: utf-8 -*-
"""
微信读书直连公众号同步与 Markdown 归档引擎
无需 Docker，无需本地 8001 端口服务，直接利用本地已授权的微信读书凭据同步书架上的公众号文章。
安全合规通道：优先走微信读书官方正文接口，稳定低风控。
"""

import os
import sys
import json
import time
import hashlib
import logging
from typing import Dict, List, Any, Optional

# 保证 Windows 命令行控制台输出中文字符串不报错
if sys.platform.startswith("win"):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "scripts"))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "server"))

import requests
import yaml

from html_to_markdown import WechatHtmlToMarkdown
from notify import Notifier
from rss_sync import sanitize_filename, setup_file_logging
from driver.weread_qr import WereadQRLogin

logger = logging.getLogger("WereadDirectSync")


class WereadDirectSyncEngine:
    def __init__(self, config_path: Optional[str] = None):
        self.config_path = config_path or os.path.join(PROJECT_ROOT, "config", "config.yaml")
        self.config = self._load_config()

        # 初始化输出与历史路径
        storage_cfg = self.config.get("storage", {})
        output_dir = storage_cfg.get("output_dir", "./output/articles")
        if not os.path.isabs(output_dir):
            output_dir = os.path.normpath(os.path.join(PROJECT_ROOT, output_dir))
        self.output_dir = output_dir

        images_name = storage_cfg.get("images_dir_name", "images")
        self.images_dir = os.path.join(self.output_dir, images_name)
        self.images_rel_path = images_name

        history_path = storage_cfg.get("history_db_path", "./output/sync_history.json")
        if not os.path.isabs(history_path):
            history_path = os.path.normpath(os.path.join(PROJECT_ROOT, history_path))
        self.history_path = history_path

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

        # 全局专属请求 Session
        self.session = requests.Session()
        self.session.trust_env = True

        self.login_mgr = WereadQRLogin()

    def _request_adaptive(self, method: str, url: str, **kwargs) -> requests.Response:
        """
        网络请求自适应容错：
        遇到超时或连接异常自动建立备选会话重试。
        """
        kwargs.setdefault("timeout", 15)

        try:
            return self.session.request(method, url, **kwargs)
        except (requests.exceptions.RequestException, Exception) as e:
            logger.debug(f"网络请求未通 ({e})，正在切换网络通道重试...")
            fallback = requests.Session()
            try:
                return fallback.request(method, url, **kwargs)
            finally:
                fallback.close()

    def _load_config(self) -> Dict[str, Any]:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return yaml.safe_load(f) or {}
            except Exception:
                pass
        return {}

    def _load_history(self) -> Dict[str, Any]:
        if os.path.exists(self.history_path):
            try:
                with open(self.history_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and isinstance(data.get("synced_items"), dict):
                    return data
            except Exception as e:
                logger.warning(f"读取历史记录失败，将重新初始化: {e}")
        return {"synced_items": {}}

    def _save_history(self):
        os.makedirs(os.path.dirname(self.history_path), exist_ok=True)
        with open(self.history_path, "w", encoding="utf-8") as f:
            json.dump(self.history, f, ensure_ascii=False, indent=2)

    def load_weread_credentials(self) -> tuple[dict, str]:
        """读取本地授权凭证"""
        data_dir = os.path.join(PROJECT_ROOT, "data")
        cred_file = os.path.join(data_dir, "weread_credentials.json")
        lic_file = os.path.join(data_dir, "wx.lic")

        cookies = {}
        vid = ""

        if os.path.exists(cred_file):
            try:
                with open(cred_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    cookies = data.get("cookies", {})
                    vid = str(data.get("vid", ""))
            except Exception:
                pass

        if (not cookies or not vid) and os.path.exists(lic_file):
            try:
                with open(lic_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    w = data.get("weread_data", {})
                    cookies = w.get("cookies", {})
                    vid = str(w.get("vid", ""))
            except Exception:
                pass

        return cookies, vid

    def get_headers(self, cookies: dict) -> dict:
        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
        return {
            "Cookie": cookie_str,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/json,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Origin": "https://weread.qq.com",
            "Referer": "https://weread.qq.com/",
        }

    def fetch_shelf_mps(self, headers: dict) -> Optional[List[Dict[str, Any]]]:
        """
        获取微信读书书架上的所有公众号专栏
        若返回 None：表示授权过期（登录超时 -2012），需触发扫码向导
        若返回 []：表示凭证有效但书架确实为空
        """
        url = "https://weread.qq.com/web/shelf/sync"
        try:
            resp = self._request_adaptive("GET", url, params={"userVid": "", "synckey": 0}, headers=headers, timeout=15)
            if resp.status_code != 200:
                logger.error(f"请求微信读书书架返回 HTTP {resp.status_code}")
                return []
            data = resp.json()
            err_code = data.get("errCode")
            err_msg = str(data.get("errMsg", ""))
            if err_code == -2012 or "登录超时" in err_msg or "请重新登录" in err_msg:
                logger.warning(f"微信读书授权凭据已失效 ({err_code}: {err_msg})")
                return None

            books = data.get("books", [])
            mps = []
            for b in books:
                book_id = b.get("bookId", "")
                if str(book_id).startswith("MP_WXS_"):
                    mps.append({
                        "book_id": book_id,
                        "name": b.get("title", ""),
                        "cover": b.get("cover", ""),
                        "author": b.get("author", ""),
                    })
            return mps
        except Exception as e:
            logger.error(f"获取微信读书书架异常: {e}")
            return []


    def fetch_mp_articles(self, book_id: str, headers: dict, days_limit: int = 0) -> List[Dict[str, Any]]:
        """获取指定公众号的文章列表，支持时间跨度过滤（最近 N 天）与多页获取"""
        articles = []
        now_ts = time.time()
        min_ts = (now_ts - days_limit * 86400) if days_limit > 0 else 0

        offset = 0
        max_pages = 5
        stop_paging = False

        for _ in range(max_pages):
            if stop_paging:
                break
            try:
                url = "https://weread.qq.com/web/mp/articles"
                resp = self._request_adaptive("GET", url, params={"bookId": book_id, "offset": offset}, headers=headers, timeout=15)
                if resp.status_code != 200:
                    break
                data = resp.json()
                reviews = data.get("reviews", [])
                if not reviews:
                    break

                batch_count = 0
                for r_group in reviews:
                    if stop_paging:
                        break
                    for sub in r_group.get("subReviews", []):
                        rev = sub.get("review", {})
                        mp_info = rev.get("mpInfo", {})
                        review_id = rev.get("reviewId") or sub.get("reviewId")
                        orig_id = mp_info.get("originalId")
                        title = mp_info.get("title", "").strip()

                        raw_ctime = rev.get("createTime") or rev.get("ctime") or 0
                        try:
                            ctime_int = int(raw_ctime)
                            ctime = int(ctime_int / 1000) if ctime_int > 1e11 else ctime_int
                        except (ValueError, TypeError):
                            ctime = 0

                        # 时间过滤：如果早于限制时间，停止向下翻页
                        if min_ts > 0 and ctime and ctime < min_ts:
                            stop_paging = True
                            break

                        if orig_id:
                            link = f"https://mp.weixin.qq.com/s/{orig_id}"
                        elif review_id:
                            token = review_id.split("_")[-1]
                            link = f"https://mp.weixin.qq.com/s/{token}"
                        else:
                            link = ""

                        if link and title:
                            date_str = time.strftime("%Y-%m-%d", time.localtime(ctime)) if ctime > 0 else time.strftime("%Y-%m-%d")
                            articles.append({
                                "title": title,
                                "link": link,
                                "guid": review_id or link,
                                "date": date_str,
                                "timestamp": ctime,
                                "digest": mp_info.get("content", ""),
                            })
                            batch_count += 1

                if stop_paging or batch_count == 0 or len(reviews) < 5:
                    break
                offset += len(reviews)
            except Exception as e:
                logger.warning(f"获取公众号 {book_id} 文章列表异常: {e}")
                break

        # 2. 智能最新文章抓取：当 /web/mp/articles 无返回或无权限 (-2041) 时，
        # 联合请求 /api/mp/cover 与 /web/book/info，提取真实发布时间与最新文章
        if not articles:
            try:
                url_cover = "https://weread.qq.com/api/mp/cover"
                resp_cover = self._request_adaptive("GET", url_cover, params={"bookId": book_id}, headers=headers, timeout=15)
                if resp_cover.status_code == 200:
                    data = resp_cover.json()
                    review_id = data.get("reviewId", "")
                    title = data.get("title", "").strip()
                    if review_id and title:
                        token = review_id.split("_")[-1]
                        
                        # 深度获取真实更新时间戳 (优先从 book/info 的 updateTime 获取)
                        ctime = 0
                        try:
                            url_info = "https://weread.qq.com/web/book/info"
                            resp_info = self._request_adaptive("GET", url_info, params={"bookId": book_id}, headers=headers, timeout=10)
                            if resp_info.status_code == 200:
                                info_data = resp_info.json()
                                ctime = int(info_data.get("updateTime", 0))
                        except Exception:
                            pass
                        
                        if not ctime:
                            raw_ctime = data.get("createTime") or data.get("ctime") or 0
                            try:
                                ctime_int = int(raw_ctime)
                                ctime = int(ctime_int / 1000) if ctime_int > 1e11 else ctime_int
                            except (ValueError, TypeError):
                                ctime = 0

                        # 时间跨度过滤校验
                        if min_ts > 0 and ctime > 0 and ctime < min_ts:
                            logger.info(f"公众号 {book_id} 最新文章时间 ({time.strftime('%Y-%m-%d', time.localtime(ctime))}) 早于筛选范围，跳过")
                        else:
                            cover_date = time.strftime("%Y-%m-%d", time.localtime(ctime)) if ctime > 0 else time.strftime("%Y-%m-%d")
                            articles.append({
                                "title": title,
                                "link": f"https://mp.weixin.qq.com/s/{token}",
                                "guid": review_id,
                                "date": cover_date,
                                "timestamp": ctime,
                                "digest": data.get("digest", ""),
                            })
            except Exception as e:
                logger.warning(f"智能获取公众号 {book_id} 最新文章异常: {e}")

        return articles

    def _resolve_target_path(self, date_str: str, title: str, guid: str) -> tuple[str, str]:
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

    def sync_article(self, article: Dict[str, Any], author: str, headers: dict, dry_run: bool = False) -> bool:
        """抓取单篇微信文章 HTML 并转换为 Markdown 与独立单文件 HTML 落盘"""
        title = article["title"]
        link = article["link"]
        guid = article["guid"]
        date_str = article["date"]

        # 增量去重检查：若已记录且本地文件体积 > 500 字节（已具备完整正文），则跳过
        if guid in self.history["synced_items"]:
            saved = self.history["synced_items"][guid].get("saved_path", "")
            actual_saved = ""
            if saved and os.path.exists(saved):
                actual_saved = saved
            elif saved:
                # 兼容换机或盘符/目录变更：在当前 output_dir 寻找同名文件
                candidate = os.path.join(self.output_dir, os.path.basename(saved))
                if os.path.exists(candidate):
                    actual_saved = candidate

            if actual_saved and os.path.getsize(actual_saved) > 500:
                html_path = os.path.splitext(actual_saved)[0] + ".html"
                if os.path.exists(html_path):
                    if actual_saved != saved:
                        self.history["synced_items"][guid]["saved_path"] = actual_saved
                        self.history["synced_items"][guid]["html_path"] = html_path
                        self._save_history()
                    return False

        logger.info(f"正在抓取并解析文章: {title}")
        if dry_run:
            return True

        content_html = ""

        # 方式一（首选推荐）：通过微信读书官方正文接口获取完整内容（格式完整且不受外企代理/SSL拦截）
        if guid and str(guid).startswith("MP_WXS_"):
            try:
                resp = self._request_adaptive(
                    "GET",
                    "https://weread.qq.com/web/mp/content",
                    params={"reviewId": guid},
                    headers=headers,
                    timeout=20
                )
                if resp.status_code == 200 and len(resp.text) > 1000:
                    content_html = resp.text
            except Exception as e:
                logger.debug(f"通过微信读书接口获取正文异常: {e}")

        # 方式二（备选）：若微信读书无正文，通过微信官网链接直连下载（自适应通道）
        if not content_html and link:
            try:
                resp = self._request_adaptive("GET", link, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                }, timeout=20)
                if resp.status_code == 200:
                    resp.encoding = "utf-8"
                    if "activity-name" in resp.text or "js_content" in resp.text:
                        content_html = resp.text
            except Exception as e:
                logger.warning(f"下载文章 HTML 失败 {link}: {e}")

        # 降级兜底：使用摘要
        if not content_html:
            content_html = f"<p>{article.get('digest', '')}</p><p><a href='{link}'>阅读原文</a></p>"

        metadata = {
            "title": title,
            "author": author,
            "date": date_str,
            "source_url": link,
            "tags": ["微信公众号", author] if author else ["微信公众号"]
        }

        md_content = self.converter.convert(content_html, metadata=metadata)
        filename, file_path = self._resolve_target_path(date_str, title, guid)

        os.makedirs(self.output_dir, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        # 同时自动输出单文件独立 HTML (自包含 Base64 图片与一键复制功能)
        html_path = os.path.splitext(file_path)[0] + ".html"
        try:
            from html_exporter import convert_markdown_to_html
            convert_markdown_to_html(file_path, html_path)
        except Exception as e:
            logger.warning(f"导出单文件 HTML 提示: {e}")

        logger.info(f"✅ 已落盘: {filename} 及对应单文件 HTML")

        self.history["synced_items"][guid] = {
            "title": title,
            "author": author,
            "link": link,
            "date": date_str,
            "saved_path": file_path,
            "html_path": html_path,
            "sync_time": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        self._save_history()
        return True

    def run(self, selected_book_ids: Optional[List[str]] = None, days_limit: int = 0, dry_run: bool = False, progress_callback: Optional[Any] = None) -> int:
        """
        执行公众号同步
        :param selected_book_ids: 待抓取的公众号 bookId 列表（None 则全量抓取）
        :param days_limit: 时间限制天数（0 为不限，7/30/90 等）
        :param dry_run: 是否仅试跑
        :param progress_callback: 进度回调函数 callback(dict)
        :return: 本次新增/更新文章数量
        """
        def report_progress(msg: str, percent: int = 0, current_article: str = ""):
            if progress_callback:
                try:
                    progress_callback({
                        "message": msg,
                        "percent": percent,
                        "current_article": current_article,
                        "time": time.strftime("%H:%M:%S")
                    })
                except Exception:
                    pass

        report_progress("正在读取微信读书登录凭证...", percent=5)
        cookies, vid = self.load_weread_credentials()
        if not cookies or not vid:
            report_progress("未检测到有效微信读书登录凭证，请扫码", percent=0)
            logger.error("未完成微信读书登录，同步终止。")
            return 0

        headers = self.get_headers(cookies)

        report_progress(f"正在读取微信读书书架 (VID: {vid})...", percent=10)
        mps = self.fetch_shelf_mps(headers)

        if mps is None:
            report_progress("微信读书授权已过期，请重新扫码登录！", percent=0)
            logger.warning("检测到授权过期，需要重新扫码。")
            return 0

        if not mps:
            report_progress("微信读书书架上暂无公众号", percent=100)
            logger.info("书架上暂无公众号。")
            return 0

        # 如果指定了特定公众号进行过滤
        if selected_book_ids:
            mps = [m for m in mps if m["book_id"] in selected_book_ids]

        total_mps = len(mps)
        if total_mps == 0:
            report_progress("未选择任何公众号或选中的公众号不在书架中", percent=100)
            return 0

        logger.info(f"本次将同步 {total_mps} 个公众号: {[m['name'] for m in mps]}")
        report_progress(f"准备开始同步 {total_mps} 个公众号...", percent=15)

        total_new = 0
        for idx, mp in enumerate(mps, 1):
            mp_name = mp["name"]
            book_id = mp["book_id"]
            mp_percent = int(15 + (idx / total_mps) * 80)

            report_progress(f"[{idx}/{total_mps}] 正在检索公众号: 【{mp_name}】...", percent=mp_percent)
            articles = self.fetch_mp_articles(book_id, headers, days_limit=days_limit)
            logger.info(f"【{mp_name}】在指定时间窗口内获取到 {len(articles)} 篇候选文章")

            for art_idx, art in enumerate(articles, 1):
                art_title = art["title"]
                report_progress(f"正在同步: 【{mp_name}】({art_idx}/{len(articles)}) {art_title}", percent=mp_percent, current_article=art_title)
                if self.sync_article(art, author=mp_name, headers=headers, dry_run=dry_run):
                    total_new += 1

        report_progress(f"🎉 抓取任务圆满完成！本次新增归档 {total_new} 篇文章！", percent=100)
        logger.info(f"🎉 微信读书同步完成！本次新增/补全新文章: {total_new} 篇")

        if total_new > 0 and not dry_run:
            try:
                self.notifier.notify(
                    title="微信公众号同步完成",
                    content=f"成功从微信读书归档 {total_new} 篇新文章至知识库",
                    url=""
                )
            except Exception as e:
                logger.debug(f"推送通知失败: {e}")

        return total_new


def main():
    setup_file_logging()
    engine = WereadDirectSyncEngine()
    engine.run()


if __name__ == "__main__":
    main()
