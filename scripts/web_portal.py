# -*- coding: utf-8 -*-
"""
微信公众号本地知识库管理门户 (WeChat Knowledge Portal Server)
-------------------------------------------------------------
专为开箱即用打造的免安装、可视化管理控制台：
1. 网页扫码与授权监控（免看黑框终端）
2. 公众号书架卡片勾选与多选同步
3. 时间范围选择（最近 7 天 / 30 天 / 90 天 / 全量）
4. 抓取进度实时动态呈现
5. 同页面双栏知识库检索与时间排序阅读器
"""

import os
import sys
import json
import time
import glob
import urllib.parse
import threading
from typing import Dict, Any, List, Optional
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

# 保证控制台字符编码兼容
if sys.platform.startswith("win"):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
ARTICLES_DIR = os.path.normpath(os.path.join(PROJECT_ROOT, "output", "articles"))
PORTAL_STATIC_DIR = os.path.normpath(os.path.join(CURRENT_DIR, "portal_static"))

sys.path.insert(0, CURRENT_DIR)
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "server"))

from sync_weread_direct import WereadDirectSyncEngine
from driver.weread_qr import WereadQRLogin

def is_safe_path(base_dir: str, target_path: str) -> bool:
    """严格校验目标路径是否位于指定基准目录内部，彻底杜绝路径穿越漏洞"""
    try:
        base = os.path.normcase(os.path.realpath(base_dir))
        target = os.path.normcase(os.path.realpath(target_path))
        return os.path.commonpath([base, target]) == base
    except Exception:
        return False

# 全局任务状态管理
g_sync_lock = threading.Lock()
g_sync_task = {
    "is_running": False,
    "percent": 0,
    "message": "空闲中",
    "current_article": "",
    "start_time": "",
    "logs": [],
    "total_new": 0,
    "error": ""
}


def add_sync_log(msg: str):
    timestamp = time.strftime("%H:%M:%S")
    log_entry = f"[{timestamp}] {msg}"
    g_sync_task["logs"].append(log_entry)
    if len(g_sync_task["logs"]) > 200:
        g_sync_task["logs"].pop(0)


class PortalRequestHandler(SimpleHTTPRequestHandler):
    """自定义请求路由处理器"""

    def log_message(self, format, *args):
        # 压制过多的 HTTP 200 请求日志，只在控制台输出关键 API
        if "/api/sync_progress" not in self.path and "/api/status" not in self.path:
            super().log_message(format, *args)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        # 1. 静态主页
        if path == "/" or path == "/index.html":
            self._serve_portal_html()
            return

        # 2. 映射已归档文章与图片资源
        if path.startswith("/articles/"):
            rel_path = path[len("/articles/"):]
            local_file = os.path.normpath(os.path.join(ARTICLES_DIR, urllib.parse.unquote(rel_path)))
            self._serve_static_file(local_file, allowed_root=ARTICLES_DIR)
            return

        if path.startswith("/static/"):
            rel_path = path[len("/static/"):]
            local_file = os.path.normpath(os.path.join(PORTAL_STATIC_DIR, urllib.parse.unquote(rel_path)))
            self._serve_static_file(local_file, allowed_root=PORTAL_STATIC_DIR)
            return

        # 3. 业务 API
        if path == "/api/status":
            self._handle_get_status()
            return
        elif path == "/api/qrcode":
            self._handle_get_qrcode()
            return
        elif path == "/api/qr_status":
            self._handle_get_qr_status()
            return
        elif path == "/api/shelf":
            self._handle_get_shelf()
            return
        elif path == "/api/sync_progress":
            self._handle_get_sync_progress()
            return
        elif path == "/api/articles":
            self._handle_get_articles(query)
            return

        self.send_error(404, "Not Found")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/sync":
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else "{}"
            try:
                data = json.loads(body)
            except Exception:
                data = {}
            self._handle_post_sync(data)
            return

        self.send_error(404, "Not Found")

    def _serve_portal_html(self):
        html_file = os.path.join(PORTAL_STATIC_DIR, "index.html")
        if not os.path.isfile(html_file):
            self.send_error(404, "Portal index.html not found")
            return
        self._serve_static_file(html_file, allowed_root=PORTAL_STATIC_DIR, content_type="text/html; charset=utf-8")

    def _serve_static_file(self, filepath: str, allowed_root: Optional[str] = None, content_type: Optional[str] = None):
        # 严格防御路径穿越
        if allowed_root:
            if not is_safe_path(allowed_root, filepath):
                self.send_error(403, "Access Denied: Path Traversal Detected")
                return
        elif not (is_safe_path(ARTICLES_DIR, filepath) or is_safe_path(PORTAL_STATIC_DIR, filepath)):
            self.send_error(403, "Access Denied: Path Traversal Detected")
            return

        if not os.path.isfile(filepath):
            self.send_error(404, "File not found")
            return

        if not content_type:
            ext = os.path.splitext(filepath)[1].lower()
            if ext in [".html", ".htm"]:
                content_type = "text/html; charset=utf-8"
            elif ext == ".js":
                content_type = "application/javascript; charset=utf-8"
            elif ext == ".css":
                content_type = "text/css; charset=utf-8"
            elif ext in [".png", ".jpg", ".jpeg", ".gif", ".webp"]:
                content_type = f"image/{ext.lstrip('.')}"
            elif ext == ".md":
                content_type = "text/markdown; charset=utf-8"
            elif ext == ".json":
                content_type = "application/json; charset=utf-8"
            else:
                content_type = "application/octet-stream"

        try:
            with open(filepath, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, f"Error reading file: {e}")

    def _send_json(self, data: Any, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    # ---------- API 实现 ----------

    def _handle_get_status(self):
        engine = WereadDirectSyncEngine()
        cookies, vid = engine.load_weread_credentials()
        articles_dir = os.path.join(PROJECT_ROOT, "output", "articles")
        images_dir = os.path.join(articles_dir, "images")

        total_articles = len(glob.glob(os.path.join(articles_dir, "*.html")))
        total_images = len(glob.glob(os.path.join(images_dir, "*.*"))) if os.path.exists(images_dir) else 0

        is_logged_in = bool(cookies and vid)
        need_relogin = False

        if is_logged_in:
            # 探测一次书架接口验证凭证有效性（若是本地实例探活请求 PortalHealthCheck 则跳过外部网络探测，实现毫秒级快速响应）
            if self.headers.get("User-Agent") != "PortalHealthCheck":
                headers = engine.get_headers(cookies)
                mps = engine.fetch_shelf_mps(headers)
                if mps is None:
                    need_relogin = True
                    is_logged_in = False

        self._send_json({
            "is_logged_in": is_logged_in,
            "vid": vid if (is_logged_in and not need_relogin) else "",
            "need_relogin": need_relogin,
            "total_articles": total_articles,
            "total_images": total_images,
            "is_syncing": g_sync_task["is_running"]
        })

    def _handle_get_qrcode(self):
        login_mgr = WereadQRLogin()
        # 启动新会话前无条件重置登录态并取消先前残留的轮询定时器，杜绝陈旧状态误判
        login_mgr._reset()

        import base64
        try:
            import qrcode
            from io import BytesIO

            uid = login_mgr._get_login_uid()
            if not uid:
                self._send_json({"error": "向微信读书申请登录会话失败，请检查网络连接"}, status=500)
                return

            login_mgr._uid = uid
            login_mgr._has_qr = True
            confirm_url = f"https://weread.qq.com/web/confirm?uid={uid}"

            # 生成 Base64 图片 Data URL
            qr = qrcode.QRCode(version=1, box_size=8, border=2)
            qr.add_data(confirm_url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")

            buf = BytesIO()
            img.save(buf, format="PNG")
            b64_qr = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("utf-8")

            # 启动轮询监听并显式传入持久化回调
            def on_login_success(res):
                try:
                    from run_weread_login import save_credentials
                    save_credentials(PROJECT_ROOT, res)
                    print(f"[Portal] 微信读书扫码授权成功并持久化落盘: VID={res.get('vid')}")
                except Exception as e:
                    print(f"[Portal] 扫码登录成功持久化凭证警告: {e}")

            login_mgr._start_polling(CallBack=on_login_success)

            self._send_json({
                "uid": uid,
                "confirm_url": confirm_url,
                "qrcode_data_url": b64_qr
            })
        except Exception as e:
            self._send_json({"error": f"生成二维码失败: {e}"}, status=500)

    def _handle_get_qr_status(self):
        login_mgr = WereadQRLogin()
        status = login_mgr.QrStatus()
        is_ok = status.get("login_status", False)
        msg = status.get("msg", "")

        vid = ""
        if is_ok:
            data = status.get("data", {})
            vid = str(data.get("vid", "") or data.get("webLoginVid", ""))
            # 自动持久化落盘
            try:
                from run_weread_login import save_credentials
                save_credentials(PROJECT_ROOT, data)
            except Exception as e:
                print(f"[Portal] 自动保存凭证警告: {e}")

        self._send_json({
            "login_status": is_ok,
            "msg": msg,
            "vid": vid
        })

    def _handle_get_shelf(self):
        engine = WereadDirectSyncEngine()
        cookies, vid = engine.load_weread_credentials()
        if not cookies or not vid:
            self._send_json({"error": "尚未登录微信读书", "need_login": True}, status=401)
            return

        headers = engine.get_headers(cookies)
        mps = engine.fetch_shelf_mps(headers)

        if mps is None:
            self._send_json({"error": "微信读书登录已超时，请重新扫码", "need_login": True}, status=401)
            return

        # 统计每个公众号本地已同步的文章数量
        history = engine._load_history().get("synced_items", {})
        count_map = {}
        for item in history.values():
            auth = item.get("author", "")
            if auth:
                count_map[auth] = count_map.get(auth, 0) + 1

        result = []
        for m in mps:
            name = m["name"]
            result.append({
                "book_id": m["book_id"],
                "name": name,
                "cover": m.get("cover", ""),
                "author": m.get("author", ""),
                "synced_count": count_map.get(name, 0)
            })

        self._send_json({"mps": result, "total": len(result)})

    def _handle_post_sync(self, req_data: dict):
        global g_sync_task

        with g_sync_lock:
            if g_sync_task["is_running"]:
                self._send_json({"error": "已有同步任务正在进行中，请等待完成"}, status=400)
                return

            selected_book_ids = req_data.get("book_ids", [])
            days_limit = int(req_data.get("days_limit", 0))

            g_sync_task["is_running"] = True
            g_sync_task["percent"] = 5
            g_sync_task["message"] = "同步任务已启动..."
            g_sync_task["current_article"] = ""
            g_sync_task["start_time"] = time.strftime("%H:%M:%S")
            g_sync_task["logs"] = []
            g_sync_task["total_new"] = 0
            g_sync_task["error"] = ""
            add_sync_log(f"任务启动，指定公众号: {len(selected_book_ids) if selected_book_ids else '全量'}, 时间限制: {days_limit if days_limit > 0 else '不限'} 天")

        def task_worker():
            global g_sync_task
            try:
                engine = WereadDirectSyncEngine()

                def on_progress(p):
                    g_sync_task["percent"] = p.get("percent", g_sync_task["percent"])
                    g_sync_task["message"] = p.get("message", "")
                    g_sync_task["current_article"] = p.get("current_article", "")
                    add_sync_log(p.get("message", ""))

                total_new = engine.run(
                    selected_book_ids=selected_book_ids,
                    days_limit=days_limit,
                    progress_callback=on_progress
                )
                g_sync_task["total_new"] = total_new
                g_sync_task["percent"] = 100
                g_sync_task["message"] = f"同步完毕，共新增/补全 {total_new} 篇文章！"
                add_sync_log(f"🎉 同步完成！新增 {total_new} 篇文章")
            except Exception as e:
                g_sync_task["error"] = str(e)
                g_sync_task["message"] = f"任务发生异常: {e}"
                add_sync_log(f"❌ 任务出错: {e}")
            finally:
                time.sleep(1)
                g_sync_task["is_running"] = False

        threading.Thread(target=task_worker, daemon=True).start()
        self._send_json({"status": "started", "message": "任务已在后台启动"})

    def _handle_get_sync_progress(self):
        self._send_json(g_sync_task)

    def _handle_get_articles(self, query_params: dict):
        """获取文章列表，支持关键字搜索与多维排序"""
        articles_dir = os.path.join(PROJECT_ROOT, "output", "articles")
        if not os.path.exists(articles_dir):
            self._send_json({"articles": [], "total": 0, "authors": []})
            return

        keyword = query_params.get("q", [""])[0].strip().lower()
        filter_author = query_params.get("author", [""])[0].strip()
        sort_by = query_params.get("sort", ["date_desc"])[0].strip()

        # 读取同步历史元数据
        history_file = os.path.join(PROJECT_ROOT, "output", "sync_history.json")
        history_items = {}
        if os.path.exists(history_file):
            try:
                with open(history_file, "r", encoding="utf-8") as f:
                    history_items = json.load(f).get("synced_items", {})
            except Exception:
                pass

        # 扫描 HTML 文件
        html_files = glob.glob(os.path.join(articles_dir, "*.html"))
        articles = []
        all_authors_set = set()

        for h_path in html_files:
            file_name = os.path.basename(h_path)
            file_stem = os.path.splitext(file_name)[0]
            # 解析日期与标题前缀
            date_prefix = ""
            raw_title = file_stem
            if len(raw_title) >= 11 and raw_title[4] == "-" and raw_title[7] == "-":
                date_prefix = raw_title[:10]
                raw_title = raw_title[11:]

            author = ""
            link = ""
            digest = ""

            # 在 history 中反查：优先根据落盘文件基准名精确比对，其次精确比对文章标题（兼容文件名冲突后缀与跨扩展名）
            for h in history_items.values():
                h_saved = h.get("saved_path", "")
                h_stem = os.path.splitext(os.path.basename(h_saved))[0] if h_saved else ""
                h_title = h.get("title", "").strip()
                if (h_stem and file_stem == h_stem) or (h_title and (raw_title == h_title or raw_title.startswith(h_title))):
                    author = h.get("author", "")
                    link = h.get("link", "")
                    if h.get("date"):
                        date_prefix = h.get("date")
                    break

            resolved_author = author or "微信公众号"
            all_authors_set.add(resolved_author)

            # 简要过滤
            if filter_author and resolved_author != filter_author:
                continue

            if keyword:
                matched = (keyword in raw_title.lower()) or (keyword in resolved_author.lower())
                if not matched:
                    continue

            file_size = os.path.getsize(h_path)
            articles.append({
                "filename": file_name,
                "title": raw_title,
                "author": resolved_author,
                "date": date_prefix or "近期",
                "link": link,
                "size_kb": round(file_size / 1024, 1),
                "url": f"/articles/{urllib.parse.quote(file_name)}",
                "md_url": f"/articles/{urllib.parse.quote(os.path.splitext(file_name)[0] + '.md')}"
            })

        # 排序
        if sort_by == "date_desc":
            articles.sort(key=lambda x: (x["date"], x["title"]), reverse=True)
        elif sort_by == "date_asc":
            articles.sort(key=lambda x: (x["date"], x["title"]), reverse=False)
        elif sort_by == "title":
            articles.sort(key=lambda x: x["title"])

        # 提取全局完整的公众号标签供前端过滤（防止单次过滤导致标签永久蒸发死锁）
        authors = sorted(list(a for a in all_authors_set if a))

        self._send_json({
            "articles": articles,
            "total": len(articles),
            "authors": authors
        })


def check_existing_instance(port: int = 8002) -> bool:
    """探测是否已有本系统的知识库助手实例在运行"""
    try:
        import urllib.request
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/status", headers={"User-Agent": "PortalHealthCheck"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                if "is_logged_in" in data and "total_articles" in data:
                    return True
    except Exception:
        pass
    return False


def run_portal_server(port: int = 8002, host: Optional[str] = None):
    if host is None:
        host = os.environ.get("HOST") or os.environ.get("PORTAL_HOST") or "127.0.0.1"

    # 1. 优先探测是否已有本系统的知识库助手实例在运行
    if check_existing_instance(port):
        print("\n" + "=" * 60)
        print(f"💡 检测到已有微信公众号知识库助手实例在端口 {port} 运行中！无需重复启动。")
        print(f"访问地址: http://127.0.0.1:{port}")
        if sys.platform.startswith("win"):
            os.system(f"start http://127.0.0.1:{port}")
        else:
            import webbrowser
            webbrowser.open(f"http://127.0.0.1:{port}")
        print("已为您调起系统默认浏览器打开界面。")
        print("=" * 60 + "\n", flush=True)
        return

    # 2. 若端口被其他第三方程序占用，顺延递增探测 8003-8020
    httpd = None
    actual_port = port
    for candidate_port in range(port, 8021):
        # 探测候选端口上是否已有本系统的知识库助手实例在运行
        if candidate_port != port and check_existing_instance(candidate_port):
            print("\n" + "=" * 60)
            print(f"💡 检测到已有微信公众号知识库助手实例在端口 {candidate_port} 运行中！无需重复启动。")
            print(f"访问地址: http://127.0.0.1:{candidate_port}")
            if sys.platform.startswith("win"):
                os.system(f"start http://127.0.0.1:{candidate_port}")
            else:
                import webbrowser
                webbrowser.open(f"http://127.0.0.1:{candidate_port}")
            print("已为您调起系统默认浏览器打开界面。")
            print("=" * 60 + "\n", flush=True)
            return

        try:
            httpd = ThreadingHTTPServer((host, candidate_port), PortalRequestHandler)
            actual_port = candidate_port
            break
        except OSError:
            print(f"[Portal] 端口 {candidate_port} 已被占用，尝试顺延递增探测下一个端口...")
            continue

    if not httpd:
        print(f"❌ 无法绑定端口 ({port}-8020 全部被占用)，服务启动终止。")
        return

    if sys.platform.startswith("win"):
        try:
            os.system("title 微信公众号知识库助手 (可视化控制台)")
        except Exception:
            pass

    print("\n" + "=" * 62)
    print("        微信公众号知识库助手 (可视化控制台)")
    print("=" * 62)
    print(f"[*] 运行环境正常，管理台服务已启动！")
    print(f"[*] 监听绑定地址: {host}:{actual_port}")
    print(f"[*] 本地访问地址: http://127.0.0.1:{actual_port}")
    if host == "0.0.0.0":
        print(f"[*] 宿主机/局域网访问: http://<宿主机IP>:{actual_port}")
    if os.environ.get("AUTO_OPEN_BROWSER") == "1":
        print(f"[*] 正在为您调起默认浏览器打开可视化界面，请稍候...")
    print("=" * 62 + "\n", flush=True)

    if os.environ.get("AUTO_OPEN_BROWSER") == "1":
        def open_browser():
            time.sleep(1.2)
            try:
                if sys.platform.startswith("win"):
                    os.system(f"start http://127.0.0.1:{actual_port}")
                else:
                    import webbrowser
                    webbrowser.open(f"http://127.0.0.1:{actual_port}")
            except Exception:
                pass
        threading.Thread(target=open_browser, daemon=True).start()

    try:
        httpd.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        print("\n[Portal] 正在停止服务...")
    except Exception as e:
        print(f"\n[Portal] 服务运行异常: {e}")
    finally:
        if httpd:
            try:
                httpd.server_close()
            except Exception:
                pass
        print("[Portal] 服务已停止，端口已成功释放。")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="微信公众号知识库助手 Web 门户")
    parser.add_argument("port_pos", nargs="?", type=int, default=None, help="服务端口（可选，默认 8002）")
    parser.add_argument("--port", "-p", type=int, default=None, help="服务端口（默认 8002 或环境变量 PORT）")
    parser.add_argument("--host", "-H", type=str, default=None, help="监听主机IP（默认 127.0.0.1，Docker内使用 0.0.0.0）")
    args = parser.parse_args()

    target_port = args.port or args.port_pos or int(os.environ.get("PORT", 8002))
    target_host = args.host or os.environ.get("HOST") or os.environ.get("PORTAL_HOST") or "127.0.0.1"

    run_portal_server(port=target_port, host=target_host)

