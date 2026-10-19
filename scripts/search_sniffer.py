# -*- coding: utf-8 -*-
"""
PC 端微信“搜一搜”流量嗅探与自动爬取归档助手 (WeChat Search Traffic Sniffer)
----------------------------------------------------------------------
功能特点：
1. 基于 mitmproxy Addon 架构，自动监听拦截 PC 端微信搜一搜 HTTP/HTTPS 流量；
2. 智能识别与深度递归解析搜一搜搜索结果（JSON、JSONP、HTML卡片流及内嵌数据）；
3. 支持双通道捕获：
   - 搜一搜结果列表自动嗅探与后台批量多线程爬取落盘；
   - 用户在微信内置浏览器中直接点击/浏览文章时的原生完整 HTML 零损实时归档；
4. 深度对接项目现有引擎：
   - 调用 WechatHtmlToMarkdown 完成本地防盗链高清配图下载与 Markdown 结构化转换；
   - 调用 WechatHtmlExporter 生成内嵌 Base64 图片的自包含独立单文件 HTML 及一键复制剪贴板；
   - 自动维护 output/sync_history.json，无缝同步至 Web 知识库门户；
5. Windows 平台原生支持系统代理安全启闭与状态自动备份复原（注册表 + WinINet API 即时刷新）；
6. 域名级 TLS 拦截隔离（--allow-hosts 只代理微信生态，完全不干扰正常网页与其他应用）；
7. 完备的退出安全保障（atexit、SIGINT、SIGTERM 及 Windows 控制台关闭事件，确保 100% 还原系统网络）。
"""

import os
import re
import sys
import json
import time
import html
import glob
import ctypes
import signal
import socket
import atexit
import hashlib
import logging
import argparse
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, List, Optional, Tuple, Set

# 控制台编码设置（针对 Windows CMD / PowerShell）
if sys.platform.startswith("win"):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)

# 将 scripts 目录加入 sys.path 以便引用现有模块
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

try:
    from html_to_markdown import WechatHtmlToMarkdown
except ImportError:
    WechatHtmlToMarkdown = None

try:
    from html_exporter import convert_markdown_to_html
except ImportError:
    convert_markdown_to_html = None

try:
    import requests
except ImportError:
    requests = None

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

# 日志配置
logger = logging.getLogger("SearchSniffer")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


# =====================================================================
# 网络与端口工具函数
# =====================================================================
def is_port_available(host: str, port: int) -> bool:
    """检查指定 host 和 port 是否可用（未被其他进程占用）"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            s.bind((host, port))
            return True
    except Exception:
        return False


def find_available_port(host: str, start_port: int, max_tries: int = 20) -> int:
    """从 start_port 开始寻找首个可用的端口"""
    for p in range(start_port, start_port + max_tries):
        if is_port_available(host, p):
            return p
    return start_port


def get_upstream_proxy() -> Optional[str]:
    """检测环境或配置中的上游代理服务器（排除本地 127.0.0.1 或 localhost 代理）"""
    # 1. 优先读取已锁定的上游代理环境变量
    cached = os.environ.get("WECHAT_UPSTREAM_PROXY")
    if cached and "127.0.0.1" not in cached and "localhost" not in cached:
        return cached.strip()

    # 2. 检查 WindowsProxyManager 备份的原始系统代理
    try:
        if WindowsProxyManager._previous_settings:
            prev_srv = WindowsProxyManager._previous_settings.get("server", "")
            if prev_srv and "127.0.0.1" not in prev_srv and "localhost" not in prev_srv:
                if not prev_srv.startswith("http://") and not prev_srv.startswith("https://"):
                    prev_srv = f"http://{prev_srv}"
                return prev_srv.strip()
    except Exception:
        pass

    # 3. 检查系统标准环境变量
    for key in ["HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"]:
        val = os.environ.get(key)
        if val:
            val_clean = val.strip()
            if "127.0.0.1" not in val_clean and "localhost" not in val_clean:
                return val_clean

    # 4. 检查系统注册表 / 网络代理
    try:
        import urllib.request
        proxies = urllib.request.getproxies()
        for k in ["https", "http"]:
            p = proxies.get(k)
            if p and "127.0.0.1" not in p and "localhost" not in p:
                return p
    except Exception:
        pass
    return None


# 全局引用以防 Windows 控制台回调被垃圾回收
_win_ctrl_handler = None



def setup_windows_close_handler(cleanup_fn):
    """注册 Windows 终端关闭事件处理程序（点击右上角关闭按钮时自动还原代理）"""
    if sys.platform != "win32":
        return
    try:
        HandlerRoutine = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint32)

        def _handler(ctrl_type):
            cleanup_fn()
            return False

        global _win_ctrl_handler
        _win_ctrl_handler = HandlerRoutine(_handler)
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_win_ctrl_handler, 1)
    except Exception:
        pass


# =====================================================================
# 1. Windows 系统代理原生管理器 (Winreg + WinINet 即时生效)
# =====================================================================
class WindowsProxyManager:
    """Windows 系统 HTTP/HTTPS 代理自动设置与安全还原工具"""
    INTERNET_SETTINGS_PATH = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    INTERNET_OPTION_SETTINGS_CHANGED = 39
    INTERNET_OPTION_REFRESH = 37

    _previous_settings: Optional[Dict[str, Any]] = None

    @classmethod
    def _notify_wininet(cls):
        """调用 Windows 底层 wininet.dll 通知系统及所有 CEF/Chromium 进程刷新代理缓存"""
        if sys.platform != "win32":
            return
        try:
            wininet = ctypes.windll.wininet
            wininet.InternetSetOptionW.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32]
            wininet.InternetSetOptionW.restype = ctypes.c_bool
            wininet.InternetSetOptionW(0, cls.INTERNET_OPTION_SETTINGS_CHANGED, 0, 0)
            wininet.InternetSetOptionW(0, cls.INTERNET_OPTION_REFRESH, 0, 0)
        except Exception as e:
            logger.debug(f"通知 WinINet 刷新失败: {e}")

    @classmethod
    def _backup_current_settings(cls):
        """备份当前系统代理设置，以便在退出时精准还原"""
        if sys.platform != "win32":
            return
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, cls.INTERNET_SETTINGS_PATH, 0, winreg.KEY_READ) as key:
                enabled = 0
                server = ""
                override = ""
                auto_url = ""
                try:
                    enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
                except FileNotFoundError:
                    pass
                try:
                    server, _ = winreg.QueryValueEx(key, "ProxyServer")
                except FileNotFoundError:
                    pass
                try:
                    override, _ = winreg.QueryValueEx(key, "ProxyOverride")
                except FileNotFoundError:
                    pass
                try:
                    auto_url, _ = winreg.QueryValueEx(key, "AutoConfigURL")
                except FileNotFoundError:
                    pass

                cls._previous_settings = {
                    "enabled": bool(enabled),
                    "server": server,
                    "override": override,
                    "auto_config_url": auto_url
                }
        except Exception:
            cls._previous_settings = {"enabled": False, "server": "", "override": "", "auto_config_url": ""}

    @classmethod
    def enable_proxy(cls, host: str = "127.0.0.1", port: int = 8080) -> bool:
        """启用 Windows 本地代理服务器（自动备份原始状态）"""
        if sys.platform != "win32":
            logger.info(f"非 Windows 系统，请手动设置 HTTP/HTTPS 代理为 {host}:{port}")
            return False

        try:
            import winreg
            server_str = f"{host}:{port}"
            cls._backup_current_settings()

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, cls.INTERNET_SETTINGS_PATH, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, server_str)
                winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, "<local>;*.local")
                if cls._previous_settings and cls._previous_settings.get("auto_config_url"):
                    try:
                        winreg.DeleteValue(key, "AutoConfigURL")
                    except FileNotFoundError:
                        pass

            cls._notify_wininet()
            logger.info(f"🟢 [系统代理] 已自动开启 Windows 本地代理 -> {server_str}")
            return True
        except Exception as e:
            logger.error(f"❌ [系统代理] 开启 Windows 代理失败: {e}")
            return False

    @classmethod
    def disable_proxy(cls, force_off: bool = False) -> bool:
        """禁用 Windows 本地代理服务器并还原正常直连"""
        if sys.platform != "win32":
            return False

        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, cls.INTERNET_SETTINGS_PATH, 0, winreg.KEY_SET_VALUE) as key:
                if force_off or not cls._previous_settings or not cls._previous_settings.get("enabled"):
                    winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)
                else:
                    prev = cls._previous_settings
                    winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1 if prev.get("enabled") else 0)
                    if prev.get("server"):
                        winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, prev["server"])
                    if prev.get("override"):
                        winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, prev["override"])
                    if prev.get("auto_config_url"):
                        winreg.SetValueEx(key, "AutoConfigURL", 0, winreg.REG_SZ, prev["auto_config_url"])

            cls._notify_wininet()
            logger.info("⚪ [系统代理] 已安全关闭/还原 Windows 本地代理，恢复正常网络连接")
            return True
        except Exception as e:
            logger.error(f"❌ [系统代理] 关闭 Windows 代理失败: {e}")
            return False

    @classmethod
    def get_proxy_status(cls) -> Dict[str, Any]:
        """查询当前系统代理状态"""
        if sys.platform != "win32":
            return {"enabled": False, "server": ""}

        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, cls.INTERNET_SETTINGS_PATH, 0, winreg.KEY_READ) as key:
                enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
                server = ""
                try:
                    server, _ = winreg.QueryValueEx(key, "ProxyServer")
                except FileNotFoundError:
                    pass
                return {"enabled": bool(enabled), "server": server}
        except Exception:
            return {"enabled": False, "server": ""}


# =====================================================================
# 2. 微信数据包与文章元数据深度解析器
# =====================================================================
class WechatDataParser:
    """搜一搜网络响应深度解析与公众号文章元数据提取"""

    @staticmethod
    def sanitize_text(text: Any) -> str:
        """清洗 HTML 标签、实体字符及不可见字符，具备非字符串防御"""
        if text is None:
            return ""
        if not isinstance(text, str):
            text = str(text)
        if not text:
            return ""
        # 预先将 &nbsp; 替换为标准空格
        text = text.replace("&nbsp;", " ").replace("\xa0", " ")
        # 反转义常见实体
        text = html.unescape(text)
        # 去除 HTML 高亮标签（如 <em>、<b>、<span> 等）
        text = re.sub(r'<[^>]+>', '', text)
        # 去除不可见零宽字符
        text = re.sub(r'[\u200b\u200c\u200d\ufeff]', '', text)
        # 清理多余连续空白
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    @staticmethod
    def sanitize_filename(filename: str, max_bytes: int = 180) -> str:
        """清理 Windows 文件名非法字符并按字节安全截断"""
        cleaned = re.sub(r'[\\/*?:"<>|]', '_', filename)
        cleaned = re.sub(r'[\r\n\t]+', ' ', cleaned).strip()
        cleaned = cleaned.rstrip(". ")
        encoded = cleaned.encode("utf-8")
        if len(encoded) > max_bytes:
            cleaned = encoded[:max_bytes].decode("utf-8", errors="ignore").rstrip(". ").strip()
        return cleaned or "untitled"

    @classmethod
    def normalize_article_url(cls, url: Optional[str]) -> Optional[str]:
        """归一化微信文章链接，解除转义字符，剥离追踪参数，保留永久访问必备参数"""
        if not url:
            return None

        url = str(url).strip()
        if not url:
            return None

        # 1. 解码常见转义（JSON、HTML、JS 转义字符）
        url = url.replace(r"\/", "/").replace("\\/", "/")
        url = url.replace(r"\x26", "&").replace(r"\u0026", "&")
        url = html.unescape(url)  # 将 &amp; 彻底还原为 &

        # 2. 检查是否有内嵌跳转参数（如 jump?url=http%3A%2F%2Fmp.weixin.qq.com...）
        if "jump" in url or "url=" in url or "target=" in url:
            try:
                parsed_init = urllib.parse.urlparse(url)
                qs_init = urllib.parse.parse_qs(parsed_init.query)
                for qk in ["url", "target", "jump_url", "redirect"]:
                    if qk in qs_init:
                        candidate = urllib.parse.unquote(qs_init[qk][0])
                        if "mp.weixin.qq.com/s" in candidate:
                            url = candidate
                            break
            except Exception:
                pass

        # 3. 处理无协议头或相对链接
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = "https://mp.weixin.qq.com" + url

        if not ("mp.weixin.qq.com/s" in url or "weixin.qq.com" in url):
            return None

        # 统一转为 https
        if url.startswith("http://"):
            url = "https://" + url[7:]

        try:
            parsed = urllib.parse.urlparse(url)
            path = parsed.path
            params = urllib.parse.parse_qs(parsed.query, keep_blank_values=False)

            # 冗余追踪参数列表
            drop_keys = {
                "scene", "clicktime", "enterid", "sessionid", "ascene",
                "fasttmpl_type", "version", "exportkey", "pass_ticket",
                "wx_header", "wfr", "source", "poc_token", "from", "chksm"
            }

            cleaned_params = {}
            for k, v in params.items():
                # 过滤带 amp; 前缀的脏键（如果仍残留）
                clean_k = k[4:] if k.startswith("amp;") else k
                if clean_k not in drop_keys and v:
                    cleaned_params[clean_k] = v[0]

            new_query = urllib.parse.urlencode(cleaned_params)
            clean_url = urllib.parse.urlunparse((
                "https",
                parsed.netloc or "mp.weixin.qq.com",
                path,
                "",
                new_query,
                ""
            ))
            return clean_url
        except Exception:
            return url.split("#")[0]

    @classmethod
    def generate_article_guid(cls, url: str) -> str:
        """根据文章 URL 生成稳定唯一的 GUID（增量去重用）"""
        try:
            parsed = urllib.parse.urlparse(url)
            qs = urllib.parse.parse_qs(parsed.query)
            biz = qs.get("__biz", [""])[0]
            mid = qs.get("mid", [""])[0]
            idx = qs.get("idx", [""])[0]
            sn = qs.get("sn", [""])[0]

            if biz and mid and sn:
                return f"MP_SNIFF_{biz}_{mid}_{idx}_{sn}"

            # 若为 /s/xxxxxxxx 形式的短链
            path = parsed.path.strip("/")
            if path.startswith("s/"):
                short_code = path[2:]
                if short_code:
                    return f"MP_SNIFF_SHORT_{short_code}"
        except Exception:
            pass

        # 降级：使用规范化 URL 的 MD5
        return f"MP_SNIFF_{hashlib.md5(url.encode('utf-8')).hexdigest()[:16]}"

    @classmethod
    def format_publish_date(cls, val: Any) -> str:
        """将各种格式的时间戳或日期字符串格式化为 YYYY-MM-DD"""
        if not val:
            return time.strftime("%Y-%m-%d")

        # 1. 纯数字时间戳（秒或毫秒）
        if isinstance(val, (int, float)) or (isinstance(val, str) and val.strip().isdigit()):
            ts = int(val)
            if ts > 10000000000:  # 毫秒
                ts = ts // 1000
            try:
                return time.strftime("%Y-%m-%d", time.localtime(ts))
            except Exception:
                pass

        val_str = str(val).strip()

        # 2. 正则提取完整 YYYY-MM-DD 或 YYYY/MM/DD 或 YYYY年M月D日
        match = re.search(r'(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})', val_str)
        if match:
            year, month, day = match.groups()
            return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"

        # 3. 提取 MM-DD 或 MM月DD日（无年份，按当年计算）
        m_short = re.search(r'(?:^|[^\d])(\d{1,2})[-/月](\d{1,2})日?', val_str)
        if m_short:
            month, day = m_short.groups()
            curr_year = time.localtime().tm_year
            return f"{curr_year:04d}-{int(month):02d}-{int(day):02d}"

        # 4. 常见中文相对时间
        now = time.time()
        if "今天" in val_str or "刚刚" in val_str or "小时前" in val_str or "分钟前" in val_str:
            return time.strftime("%Y-%m-%d", time.localtime(now))
        elif "昨天" in val_str:
            return time.strftime("%Y-%m-%d", time.localtime(now - 86400))
        elif "前天" in val_str:
            return time.strftime("%Y-%m-%d", time.localtime(now - 172800))
        day_match = re.search(r'(\d+)\s*天前', val_str)
        if day_match:
            days = int(day_match.group(1))
            return time.strftime("%Y-%m-%d", time.localtime(now - days * 86400))

        return time.strftime("%Y-%m-%d")

    @classmethod
    def _try_extract_article_dict(cls, d: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """判断一个 dict 是否表示一篇微信文章，并提取规范化元数据（支持扁平及嵌套结构）"""
        if not isinstance(d, dict):
            return None

        appmsg = d.get("appmsg_info") if isinstance(d.get("appmsg_info"), dict) else {}
        article_info = d.get("article_info") if isinstance(d.get("article_info"), dict) else {}
        base_info = d.get("base_info") if isinstance(d.get("base_info"), dict) else {}
        source_info = d.get("source_info") if isinstance(d.get("source_info"), dict) else {}
        comm_info = d.get("comm_msg_info") if isinstance(d.get("comm_msg_info"), dict) else {}
        author_info = d.get("author_info") if isinstance(d.get("author_info"), dict) else {}

        # 寻找 URL / 链接字段
        url_candidates = [
            d.get("url"), d.get("link"), d.get("doc_url"), d.get("jump_url"),
            d.get("article_url"), d.get("target_url"), d.get("appmsg_url"),
            d.get("msg_url"), d.get("content_url"),
            appmsg.get("url"), appmsg.get("content_url"), appmsg.get("link"),
            article_info.get("url"), article_info.get("jump_url"), article_info.get("link"),
            base_info.get("url"), base_info.get("link")
        ]
        raw_url = None
        for candidate in url_candidates:
            if candidate and isinstance(candidate, str) and ("mp.weixin.qq.com/s" in candidate or "weixin.qq.com" in candidate):
                raw_url = candidate
                break

        if not raw_url:
            return None

        clean_url = cls.normalize_article_url(raw_url)
        if not clean_url:
            return None

        # 寻找标题字段
        title_candidates = [
            d.get("title"), d.get("doc_title"), d.get("msg_title"),
            d.get("title_highlight"), d.get("topic_name"),
            appmsg.get("title"), article_info.get("title"), base_info.get("title")
        ]
        raw_title = ""
        for t in title_candidates:
            if t and isinstance(t, (str, int)):
                raw_title = str(t)
                break
        clean_title = cls.sanitize_text(raw_title)
        if not clean_title:
            return None

        # 寻找作者 / 公众号昵称
        author_candidates = [
            d.get("author"), d.get("nickname"), d.get("nick_name"), d.get("source"),
            d.get("user_name"), d.get("author_name"), d.get("wechat_name"),
            d.get("wechat_nickname"), d.get("account_name"), d.get("appmsg_author"),
            d.get("source_name"), d.get("wx_name"),
            source_info.get("user_name"), source_info.get("name"), source_info.get("nickname"),
            source_info.get("nick_name"),
            author_info.get("nickname"), author_info.get("name"), author_info.get("nick_name"),
            appmsg.get("author")
        ]
        raw_author = ""
        for a in author_candidates:
            if a and isinstance(a, (str, int)):
                raw_author = str(a)
                break
        clean_author = cls.sanitize_text(raw_author) or "微信公众号"

        # 寻找时间字段
        time_candidates = [
            d.get("pub_time"), d.get("publish_time"), d.get("time"),
            d.get("create_time"), d.get("date"), d.get("update_time"),
            d.get("datetime"), d.get("timestamp"),
            comm_info.get("datetime"), comm_info.get("time"),
            appmsg.get("create_time"), base_info.get("pub_time")
        ]
        raw_time = None
        for t in time_candidates:
            if t is not None:
                raw_time = t
                break
        formatted_date = cls.format_publish_date(raw_time)

        # 封面图
        cover_candidates = [
            d.get("cover"), d.get("thumb_url"), d.get("pic_url"),
            d.get("img_url"), d.get("cover_url"), d.get("cdn_url"),
            appmsg.get("cover"), appmsg.get("thumb_url"), article_info.get("cover")
        ]
        cover_url = ""
        for c in cover_candidates:
            if c and isinstance(c, str) and c.startswith("http"):
                cover_url = c
                break

        # 摘要
        digest_candidates = [
            d.get("digest"), d.get("desc"), d.get("summary"), d.get("snippet"),
            appmsg.get("digest")
        ]
        raw_digest = ""
        for dg in digest_candidates:
            if dg and isinstance(dg, (str, int)):
                raw_digest = str(dg)
                break
        clean_digest = cls.sanitize_text(raw_digest)

        guid = cls.generate_article_guid(clean_url)

        return {
            "guid": guid,
            "title": clean_title,
            "author": clean_author,
            "date": formatted_date,
            "url": clean_url,
            "cover": cover_url,
            "digest": clean_digest
        }

    @classmethod
    def extract_articles_from_json(cls, data: Any) -> List[Dict[str, Any]]:
        """深度递归遍历任意 JSON 结构，提取所有符合微信文章特征的条目（去重）"""
        results: List[Dict[str, Any]] = []
        seen_guids: Set[str] = set()

        def _traverse(node: Any):
            if isinstance(node, dict):
                extracted = cls._try_extract_article_dict(node)
                if extracted:
                    guid = extracted["guid"]
                    if guid not in seen_guids:
                        seen_guids.add(guid)
                        results.append(extracted)

                for k, v in node.items():
                    if isinstance(v, str) and (v.startswith("{") or v.startswith("[")):
                        try:
                            inner = json.loads(v)
                            _traverse(inner)
                        except Exception:
                            pass
                    else:
                        _traverse(v)
            elif isinstance(node, list):
                for item in node:
                    _traverse(item)

        _traverse(data)
        return results

    @classmethod
    def extract_articles_from_html(cls, text_body: str) -> List[Dict[str, Any]]:
        """从搜一搜结果 HTML 页面中提取内嵌 JSON 或 DOM 链接卡片"""
        if not text_body:
            return []

        articles_found: List[Dict[str, Any]] = []

        # 1. 检查是否有内嵌 JSON 变量（使用 JSONDecoder.raw_decode 支持跨多行和复杂嵌套）
        decoder = json.JSONDecoder()
        for m in re.finditer(r'(?:__INITIAL_STATE__|searchData|resultData|cgiData|__SEARCH_DATA__|window\.\w+)\s*=\s*', text_body):
            idx = m.end()
            while idx < len(text_body) and text_body[idx] in " \t\r\n":
                idx += 1
            if idx < len(text_body) and text_body[idx] in "{[":
                try:
                    j_obj, _ = decoder.raw_decode(text_body[idx:])
                    articles_found.extend(cls.extract_articles_from_json(j_obj))
                except Exception:
                    pass

        # 2. 检查 <script type="application/json">
        if not articles_found and BeautifulSoup:
            try:
                soup = BeautifulSoup(text_body, "html.parser")
                for s_tag in soup.find_all("script", type="application/json"):
                    if s_tag.string:
                        try:
                            j_obj = json.loads(s_tag.string)
                            articles_found.extend(cls.extract_articles_from_json(j_obj))
                        except Exception:
                            pass
            except Exception:
                pass

        # 3. DOM 链接卡片提取
        if not articles_found and "mp.weixin.qq.com/s" in text_body:
            if BeautifulSoup:
                try:
                    soup = BeautifulSoup(text_body, "html.parser")
                    for a_tag in soup.find_all("a", href=True):
                        href = a_tag["href"]
                        if "mp.weixin.qq.com/s" in href:
                            clean_url = cls.normalize_article_url(href)
                            if clean_url:
                                card_title = a_tag.get_text().strip() or a_tag.get("title", "")
                                card_author = "微信公众号"
                                parent = a_tag.parent
                                if parent:
                                    author_el = parent.find(class_=re.compile(r"author|source|wechat|account|name", re.I))
                                    if author_el:
                                        card_author = author_el.get_text().strip() or "微信公众号"
                                articles_found.append({
                                    "guid": cls.generate_article_guid(clean_url),
                                    "title": card_title or "搜一搜文章",
                                    "author": card_author,
                                    "date": time.strftime("%Y-%m-%d"),
                                    "url": clean_url
                                })
                except Exception:
                    pass

            # 正则降级
            if not articles_found:
                for link_match in re.finditer(r'href=["\'](https?://mp\.weixin\.qq\.com/s[^"\']+)["\']', text_body):
                    found_url = html.unescape(link_match.group(1))
                    clean_url = cls.normalize_article_url(found_url)
                    if clean_url:
                        articles_found.append({
                            "guid": cls.generate_article_guid(clean_url),
                            "title": "搜一搜文章",
                            "author": "微信公众号",
                            "date": time.strftime("%Y-%m-%d"),
                            "url": clean_url
                        })

        return articles_found

    @classmethod
    def extract_article_from_html(cls, html_content: str, page_url: str = "") -> Optional[Dict[str, Any]]:
        """从微信文章原生 HTML 页面中提取完整元数据及文章正文"""
        if not html_content or len(html_content) < 300:
            return None

        # 检查是否为微信文章正文页面（必须有正文容器或核心标题特征）
        is_wechat_article = (
            "js_content" in html_content
            or "rich_media_content" in html_content
            or "var msg_title" in html_content
            or "activity-name" in html_content
        )
        if not is_wechat_article:
            return None

        soup = BeautifulSoup(html_content, "html.parser") if BeautifulSoup else None

        # 1. 提取标题
        title = ""
        if soup:
            t_node = soup.find(id="activity-name") or soup.find(class_="rich_media_title")
            if t_node:
                title = t_node.get_text().strip()
        if not title:
            m = re.search(r'var\s+msg_title\s*=\s*["\']([^"\']+)["\']', html_content)
            if m:
                title = m.group(1)
        if not title and soup and soup.title:
            raw_soup_title = soup.title.get_text().strip()
            cleaned_soup_title = re.sub(r'[\s\-_]+(?:微信公众平台|微信).*$', '', raw_soup_title)
            if cleaned_soup_title and cleaned_soup_title not in ["微信公众平台", "微信", "阅读文章"]:
                title = cleaned_soup_title
        title = cls.sanitize_text(title)
        if not title:
            title = "未命名微信文章"

        # 2. 提取作者 / 公众号昵称
        author = ""
        if soup:
            a_node = soup.find(id="js_name") or soup.find(class_="rich_media_meta_nickname")
            if a_node:
                author = a_node.get_text().strip()
            if not author:
                meta_author = soup.find("meta", attrs={"name": "author"})
                if meta_author and meta_author.get("content"):
                    author = meta_author["content"].strip()
        if not author:
            m = re.search(r'var\s+nickname\s*=\s*["\']([^"\']+)["\']', html_content)
            if m:
                author = m.group(1)
        author = cls.sanitize_text(author) or "微信公众号"

        # 3. 提取发布时间
        date_str = ""
        m_ct = re.search(r'var\s+ct\s*=\s*["\']?(\d+)["\']?', html_content)
        if m_ct:
            date_str = cls.format_publish_date(int(m_ct.group(1)))
        if not date_str and soup:
            p_node = soup.find(id="publish_time")
            if p_node:
                date_str = cls.format_publish_date(p_node.get_text().strip())
            if not date_str:
                meta_time = soup.find("meta", attrs={"property": re.compile(r"published_time|pubdate", re.I)})
                if meta_time and meta_time.get("content"):
                    date_str = cls.format_publish_date(meta_time["content"])
        if not date_str:
            date_str = time.strftime("%Y-%m-%d")

        # 4. 提取永久链接
        canonical_url = page_url
        m_url = re.search(r'var\s+msg_link\s*=\s*["\']([^"\']+)["\']', html_content)
        if m_url:
            raw_url = m_url.group(1)
            norm = cls.normalize_article_url(raw_url)
            if norm:
                canonical_url = norm

        if not canonical_url and page_url:
            canonical_url = cls.normalize_article_url(page_url) or page_url

        guid = cls.generate_article_guid(canonical_url)

        return {
            "guid": guid,
            "title": title,
            "author": author,
            "date": date_str,
            "url": canonical_url,
            "content_html": html_content,
            "digest": ""
        }


# =====================================================================
# 3. 文章下载、转换、排版与落盘归档器 (WechatArticleArchiver)
# =====================================================================
class WechatArticleArchiver:
    """负责将嗅探到的文章安全清洗、转 Markdown、Base64 单文件 HTML 并落盘"""

    def __init__(
        self,
        output_dir: Optional[str] = None,
        images_dir_name: str = "images",
        history_file: Optional[str] = None,
        download_images: bool = True,
        remove_ads: bool = True
    ):
        self.output_dir = os.path.normpath(output_dir or os.path.join(PROJECT_ROOT, "output", "articles"))
        self.images_dir = os.path.normpath(os.path.join(self.output_dir, images_dir_name))
        self.history_file = os.path.normpath(history_file or os.path.join(PROJECT_ROOT, "output", "sync_history.json"))
        self.download_images = download_images
        self.remove_ads = remove_ads

        os.makedirs(self.output_dir, exist_ok=True)
        if self.download_images:
            os.makedirs(self.images_dir, exist_ok=True)

        self._lock = threading.Lock()
        self.history = self._load_history()

        upstream_proxy = get_upstream_proxy()

        # 初始化 Markdown 转换引擎
        if WechatHtmlToMarkdown:
            self.converter = WechatHtmlToMarkdown(
                download_images=self.download_images,
                images_dir=self.images_dir,
                images_rel_path=images_dir_name,
                remove_ads=self.remove_ads
            )
            # 网络代理配置：若存在配置/环境变量指定的上游代理则使用，否则默认直连
            if hasattr(self.converter, "session") and self.converter.session:
                if upstream_proxy:
                    self.converter.session.proxies = {"http": upstream_proxy, "https": upstream_proxy}
                else:
                    self.converter.session.trust_env = True
        else:
            self.converter = None

        # 爬虫 HTTP 请求 Session
        self.session = requests.Session() if requests else None
        if self.session:
            if upstream_proxy:
                self.session.proxies = {"http": upstream_proxy, "https": upstream_proxy}
            else:
                self.session.trust_env = True
            self.session.headers.update({
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI"
                ),
                "Referer": "https://mp.weixin.qq.com/",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"
            })

    def _load_history(self) -> Dict[str, Any]:
        """加载历史记录"""
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and "synced_items" in data:
                        return data
            except Exception as e:
                logger.warning(f"读取历史记录异常: {e}")
        return {"synced_items": {}}

    def _save_history(self):
        """保存历史记录（支持多进程/多模块合并，避免覆盖其他同步工具写入的数据）"""
        try:
            os.makedirs(os.path.dirname(self.history_file), exist_ok=True)
            disk_data = self._load_history()
            disk_data["synced_items"].update(self.history["synced_items"])
            self.history = disk_data
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump(self.history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存历史记录失败: {e}")

    def is_already_archived(self, guid: str, url: str = "") -> bool:
        """检查文章是否已经成功归档落盘且文件有效"""
        with self._lock:
            # 1. 优先根据 GUID 查找
            item = self.history["synced_items"].get(guid)
            if item:
                saved_md = item.get("saved_path", "")
                if saved_md and os.path.exists(saved_md) and os.path.getsize(saved_md) > 50:
                    return True
                base_name = os.path.basename(saved_md) if saved_md else ""
                if base_name:
                    candidate = os.path.join(self.output_dir, base_name)
                    if os.path.exists(candidate) and os.path.getsize(candidate) > 50:
                        return True

            # 2. 次选根据规范化 URL 查找（跨模块或不同去重键兼容）
            if url:
                norm_target = WechatDataParser.normalize_article_url(url)
                for h_item in self.history["synced_items"].values():
                    h_link = h_item.get("link") or h_item.get("url")
                    if h_link and norm_target:
                        if WechatDataParser.normalize_article_url(h_link) == norm_target:
                            saved_md = h_item.get("saved_path", "")
                            if saved_md and os.path.exists(saved_md) and os.path.getsize(saved_md) > 50:
                                return True
        return False

    @staticmethod
    def is_valid_article_html(text: str) -> bool:
        """校验响应是否为真实的微信文章正文，排除网络错误/拦截页面"""
        if not text or len(text) < 300:
            return False
        text_lower = text.lower()
        bad_markers = [
            "access denied", "blocked by policy", "gateway error",
            "web filter", "security service block"
        ]
        if any(bm in text_lower for bm in bad_markers):
            return False
        good_markers = ["js_content", "rich_media_content", "activity-name", "var msg_title", "rich_media_title"]
        return any(gm in text for gm in good_markers)

    def fetch_article_html(self, url: str) -> Optional[str]:
        """下载微信文章全文 HTML（具备自动重试与剪贴板兜底机制）"""
        if not self.session:
            return None

        # 检查响应是否受到网络策略拦截
        def _check_blocked(resp_obj) -> bool:
            if resp_obj is None:
                return False
            resp_text = resp_obj.text or ""
            resp_lower = resp_text.lower()
            if resp_obj.status_code in (401, 403) or "access denied" in resp_lower:
                logger.warning(
                    f"⚠️ [网络访问受限] 访问该文章返回 HTTP {resp_obj.status_code}。\n"
                    f"   💡 提示：您可直接在微信文章窗口中按【Ctrl+A】全选并【Ctrl+C】复制，助手将自动从剪贴板完整归档！"
                )
                return True
            return False

        # 1. 优先使用当前配置会话
        try:
            resp = self.session.get(url, timeout=12)
            if resp.status_code == 200:
                resp.encoding = "utf-8"
                if self.is_valid_article_html(resp.text):
                    return resp.text
            _check_blocked(resp)
        except requests.exceptions.ConnectTimeout:
            logger.debug(f"首选网络通道连接超时 [{url}]")
        except Exception as e:
            logger.debug(f"首选网络通道获取文章异常 [{url}]: {e}")

        # 2. 备选会话重试
        try:
            fallback = requests.Session()
            try:
                fallback.headers.update(self.session.headers)
                if WindowsProxyManager.get_proxy_status().get("enabled"):
                    fallback.proxies = {"http": "http://127.0.0.1:8080", "https": "http://127.0.0.1:8080"}
                resp = fallback.get(url, timeout=12)
                if resp.status_code == 200:
                    resp.encoding = "utf-8"
                    if self.is_valid_article_html(resp.text):
                        return resp.text
                _check_blocked(resp)
            finally:
                fallback.close()
        except requests.exceptions.ConnectTimeout:
            logger.warning(f"⏱️ [连接超时] 外网文章直连超时。建议直接在微信窗口中按【Ctrl+A】全选【Ctrl+C】复制正文进行归档！")
        except Exception as e:
            logger.warning(f"下载文章 HTML 失败 [{url}]: {e}")
        return None


    def _resolve_target_path(self, date_str: str, title: str, guid: str) -> Tuple[str, str]:
        """
        计算 Markdown 落盘路径，避免同天同标题不同文章覆盖。
        若同一 guid 之前已落盘，则复用原文件名；重名冲突追加 guid 摘要。
        """
        safe_title = WechatDataParser.sanitize_filename(title, max_bytes=100)
        filename = f"{date_str}_{safe_title}.md"
        file_path = os.path.join(self.output_dir, filename)

        with self._lock:
            recorded = self.history["synced_items"].get(guid, {}).get("saved_path") or ""
        if recorded:
            if os.path.normcase(os.path.abspath(recorded)) == os.path.normcase(os.path.abspath(file_path)):
                return filename, file_path
            rec_basename = os.path.basename(recorded)
            if rec_basename:
                compat_path = os.path.join(self.output_dir, rec_basename)
                if os.path.normcase(compat_path) == os.path.normcase(file_path) or os.path.exists(compat_path):
                    return rec_basename, compat_path

        if os.path.exists(file_path):
            suffix = hashlib.md5(guid.encode("utf-8")).hexdigest()[:6]
            filename = f"{date_str}_{safe_title}_{suffix}.md"
            file_path = os.path.join(self.output_dir, filename)

        return filename, file_path

    def archive_article(self, article_meta: Dict[str, Any]) -> bool:
        """将单篇嗅探到的文章执行完整落盘归档（去重 -> 下载正文 -> Markdown -> 单文件 HTML -> 历史记录）"""
        guid = article_meta["guid"]
        title = article_meta["title"]
        author = article_meta.get("author", "微信公众号")
        date_str = article_meta.get("date", time.strftime("%Y-%m-%d"))
        url = article_meta["url"]

        if self.is_already_archived(guid, url=url):
            logger.info(f"⏭️ [已归档跳过] 《{title}》 之前已落盘")
            return False

        logger.info(f"📥 [开始归档] 《{title}》 | 公众号: {author} | 发布日期: {date_str}")

        # 1. 获取正文 HTML
        content_html = article_meta.get("content_html")
        if not content_html:
            content_html = self.fetch_article_html(url)

        if not content_html:
            logger.warning(f"⚠️ [抓取失败] 未能获取正文 HTML: 《{title}》")
            return False

        # 2. 如果之前只是从列表获取的元数据，解析完整 HTML 补充更权威的元数据
        detail_meta = WechatDataParser.extract_article_from_html(content_html, url)
        if detail_meta:
            detail_title = detail_meta.get("title", "").strip()
            if detail_title and detail_title != "未命名微信文章":
                title = detail_title
            if detail_meta.get("author") and detail_meta["author"] != "微信公众号":
                author = detail_meta["author"]
            if detail_meta.get("date"):
                date_str = detail_meta["date"]

        # 3. 转 Markdown
        md_metadata = {
            "title": title,
            "author": author,
            "date": date_str,
            "source_url": url,
            "tags": ["微信公众号", "搜一搜嗅探", author] if author else ["微信公众号", "搜一搜嗅探"]
        }

        if self.converter:
            md_content = self.converter.convert(content_html, metadata=md_metadata)
        else:
            md_content = f"# {title}\n\n作者：{author}\n日期：{date_str}\n\n原文：{url}\n\n"

        # 4. 计算保存路径并处理重名冲突
        filename, file_path = self._resolve_target_path(date_str, title, guid)

        # 写入 Markdown
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(md_content)
        except Exception as e:
            logger.error(f"写入 Markdown 失败 [{file_path}]: {e}")
            return False

        # 5. 生成自包含 Base64 单文件 HTML
        html_filename = os.path.splitext(filename)[0] + ".html"
        html_path = os.path.join(self.output_dir, html_filename)
        if convert_markdown_to_html:
            try:
                convert_markdown_to_html(file_path, html_path)
            except Exception as e:
                logger.warning(f"转换单文件 HTML 提示: {e}")

        # 6. 更新并持久化历史记录
        with self._lock:
            self.history["synced_items"][guid] = {
                "title": title,
                "author": author,
                "link": url,
                "date": date_str,
                "saved_path": file_path,
                "html_path": html_path,
                "sync_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "source": "wechat_search_sniffer"
            }
            self._save_history()

        logger.info(f"🎉 [归档成功] 《{title}》 -> {filename} 与同名独立 HTML")
        return True

    def archive_from_clipboard_content(
        self,
        raw_text: str,
        html_content: Optional[str] = None,
        source_url: str = ""
    ) -> bool:
        """从剪贴板复制的文章全文（富文本 HTML 或纯文本）直接落盘归档，提供高容错双轨保底"""
        if not raw_text and not html_content:
            return False

        lines = [l.strip() for l in raw_text.strip().splitlines() if l.strip()]
        if not lines and not html_content:
            return False

        # 1. 智能提取标题与作者
        title = ""
        author = "微信公众号"
        date_str = time.strftime("%Y-%m-%d")

        if html_content and BeautifulSoup:
            try:
                soup = BeautifulSoup(html_content, "html.parser")
                t_node = soup.find(id="activity-name") or soup.find(class_="rich_media_title") or soup.find("h1")
                if t_node:
                    title = t_node.get_text().strip()
                a_node = soup.find(id="js_name") or soup.find(class_="rich_media_meta_nickname")
                if a_node:
                    author = a_node.get_text().strip()
            except Exception:
                pass

        if not title and lines:
            title = lines[0]
            # 若第一行较长，合理提取
            if len(title) > 60:
                title = title[:50]

        title = WechatDataParser.sanitize_text(title) or "剪贴板归档文章"

        # 尝试从前几行分析作者
        for l in lines[1:5]:
            m_auth = re.search(r'(?:来源|作者|公众号)[:：]\s*([^\s]{2,20})', l)
            if m_auth:
                author = m_auth.group(1).strip()
                break

        # 2. 生成稳定 GUID
        if source_url:
            guid = WechatDataParser.generate_article_guid(source_url)
        else:
            guid = f"MP_CLIP_{hashlib.md5(title.encode('utf-8')).hexdigest()[:16]}"

        if self.is_already_archived(guid, url=source_url):
            logger.info(f"⏭️ [已归档跳过] 《{title}》 之前已落盘")
            return False

        logger.info(f"📋 [剪贴板正文捕获] 开始归档微信文章: 《{title}》 | 公众号: {author}")

        # 3. 转换为 Markdown
        md_metadata = {
            "title": title,
            "author": author,
            "date": date_str,
            "source_url": source_url,
            "tags": ["微信公众号", "剪贴板归档", author] if author else ["微信公众号", "剪贴板归档"]
        }

        md_content = ""
        if html_content:
            # 提取 StartFragment 和 EndFragment 之间的真实 DOM 片段
            m_frag = re.search(r'<!--StartFragment-->(.*?)<!--EndFragment-->', html_content, re.DOTALL | re.I)
            clean_html = m_frag.group(1).strip() if m_frag else html_content
            if not m_frag:
                idx_h = clean_html.find("<html")
                if idx_h != -1:
                    clean_html = clean_html[idx_h:]

            if self.converter:
                try:
                    md_content = self.converter.convert(clean_html, metadata=md_metadata)
                except Exception as e:
                    logger.debug(f"HTML 转 Markdown 异常: {e}")

        if not md_content or len(md_content) < 50:
            # 纯文本段落格式化（彻底净化由微信富文本降级为纯文本时留下的“图片”等孤立噪点）
            cleaned_lines = []
            for l in (lines[1:] if len(lines) > 1 else lines):
                text_clean = re.sub(r'[\u200b\u200c\u200d\ufeff\s]', '', l)
                if text_clean in ["图片", "[图片]", "图片1", "图片2", "点击查看大图"]:
                    continue
                cleaned_lines.append(l)
            body_paragraphs = "\n\n".join(cleaned_lines)
            url_part = f"\n\n原文：{source_url}" if source_url else ""
            md_content = (
                f"---\n"
                f'title: "{title}"\n'
                f'author: "{author}"\n'
                f'date: "{date_str}"\n'
                f'source_url: "{source_url}"\n'
                f"tags:\n"
                f"  - 微信公众号\n"
                f"  - 剪贴板归档\n"
                f"  - {author}\n"
                f"---\n\n"
                f"# {title}\n\n"
                f"作者：{author}  |  日期：{date_str}{url_part}\n\n"
                f"{body_paragraphs}\n"
            )

        # 4. 计算保存路径并落盘
        filename, file_path = self._resolve_target_path(date_str, title, guid)
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(md_content)
        except Exception as e:
            logger.error(f"写入 Markdown 失败 [{file_path}]: {e}")
            return False

        # 5. 生成单文件 HTML
        html_filename = os.path.splitext(filename)[0] + ".html"
        html_path = os.path.join(self.output_dir, html_filename)
        if convert_markdown_to_html:
            try:
                convert_markdown_to_html(file_path, html_path)
            except Exception as e:
                logger.warning(f"转换单文件 HTML 提示: {e}")

        # 6. 更新历史记录
        with self._lock:
            self.history["synced_items"][guid] = {
                "title": title,
                "author": author,
                "link": source_url,
                "date": date_str,
                "saved_path": file_path,
                "html_path": html_path,
                "sync_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "source": "clipboard_fulltext"
            }
            self._save_history()

        logger.info(f"🎉 [归档成功] 《{title}》已落盘 -> {filename} 与独立 HTML")
        return True


# =====================================================================
# 4. Mitmproxy 嗅探 Addon (核心流量拦截与调度器)
# =====================================================================
class WechatSearchSnifferAddon:
    """Mitmproxy 中间人代理插件，监听并拦截搜一搜数据包与文章页面"""

    def __init__(
        self,
        output_dir: Optional[str] = None,
        download_images: bool = True,
        max_workers: int = 3
    ):
        if output_dir is None:
            output_dir = os.environ.get("WECHAT_SNIFFER_OUTPUT_DIR")
        if os.environ.get("WECHAT_SNIFFER_DOWNLOAD_IMAGES") == "0":
            download_images = False

        self.archiver = WechatArticleArchiver(
            output_dir=output_dir,
            download_images=download_images
        )
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="WechatSnifferWorker")
        self.enqueued_urls: Set[str] = set()
        self._lock = threading.Lock()

    def request(self, flow):
        """请求拦截：检测搜一搜检索关键词与流量视觉反馈"""
        req = flow.request
        url = req.url
        host = req.pretty_host

        # 实时打印微信生态相关的请求流量，给用户清晰的视觉反馈
        if any(h in host for h in ["weixin.qq.com", "wechat.com", "qpic.cn", "servicewechat.com"]):
            logger.info(f"🌐 [微信流量] {req.method} {host}{req.path[:60]}")

        # 监听搜一搜搜索关键词
        if "search.weixin.qq.com" in host or "searchweb" in url or "searchapi" in url:
            keyword = req.query.get("query") or req.query.get("search_key") or req.query.get("key") or req.query.get("keyword")
            if not keyword and req.method == "POST" and req.text:
                try:
                    p_json = json.loads(req.text)
                    keyword = p_json.get("query") or p_json.get("search_key") or p_json.get("key")
                except Exception:
                    pass

            if keyword:
                keyword = WechatDataParser.sanitize_text(keyword)
                logger.info(f"🔍 [搜一搜侦测] 用户正在检索关键词: 【{keyword}】")

    def response(self, flow):
        """响应拦截：解密提取搜索结果列表与文章正文数据包"""
        req = flow.request
        resp = flow.response
        if not resp or not resp.content:
            return

        url = req.url
        host = req.pretty_host
        status = resp.status_code

        # 仅关注微信生态及公众号相关域名
        is_wechat_traffic = (
            "weixin.qq.com" in host
            or "wechat.com" in host
            or "wx.qq.com" in host
            or "qpic.cn" in host
            or "servicewechat.com" in host
            or (host.endswith(".qq.com") and ("search" in host or "search" in url or "mp" in host))
        )
        if not is_wechat_traffic or status != 200:
            return

        # 自动解压缩响应内容（gzip, brotli, zstd 等）
        try:
            resp.decode()
        except Exception:
            pass

        content_type = resp.headers.get("content-type", "").lower()

        # =========================================================
        # 场景 A：文章详情页面原生 HTML 零损实时拦截（用户在微信内点击或阅读）
        # =========================================================
        is_article_url = "mp.weixin.qq.com/s" in url or "weixin.qq.com/s" in url
        has_article_dom = (
            "text/html" in content_type
            and ("id=\"js_content\"" in resp.text or "class=\"rich_media_content\"" in resp.text)
            and ("activity-name" in resp.text or "var msg_title" in resp.text)
        )
        if is_article_url or has_article_dom:
            extracted = WechatDataParser.extract_article_from_html(resp.text, page_url=url)
            if extracted and extracted.get("title") and extracted["title"] != "未命名微信文章":
                guid = extracted["guid"]
                with self._lock:
                    if guid not in self.enqueued_urls:
                        self.enqueued_urls.add(guid)
                        logger.info(f"👁️ [实时捕获] 侦测到微信内置文章详情浏览: 《{extracted['title']}》")
                        self.executor.submit(self.archiver.archive_article, extracted)
                return

        # =========================================================
        # 场景 B：搜一搜搜索结果列表数据包拦截（JSON / JSONP / HTML）
        # =========================================================
        is_search_api = (
            "search.weixin.qq.com" in host
            or "searchweb" in url
            or "searchapi" in url
            or "prefixsearch" in url
            or "cgi-bin/search" in url
            or "appmsgsearch" in url
            or "wapsearch" in url
            or "topstorysearch" in url
        )

        articles_found: List[Dict[str, Any]] = []
        text_body = resp.text.strip()

        # 1. 尝试 JSON 解析
        if text_body.startswith("{") or text_body.startswith("["):
            try:
                json_data = json.loads(text_body)
                articles_found = WechatDataParser.extract_articles_from_json(json_data)
            except Exception:
                pass

        # 2. 尝试 JSONP 解析
        if not articles_found and ("json" in content_type or "javascript" in content_type or is_search_api):
            jsonp_match = re.search(r'^\s*[\w\.\$]+\s*\(\s*([\{\[].*[\}\]])\s*\)\s*;?\s*$', text_body, re.DOTALL)
            if jsonp_match:
                try:
                    json_data = json.loads(jsonp_match.group(1))
                    articles_found = WechatDataParser.extract_articles_from_json(json_data)
                except Exception:
                    pass

        # 3. 尝试从 HTML 中搜寻内嵌 JSON 或链接卡片
        if not articles_found and "text/html" in content_type:
            articles_found = WechatDataParser.extract_articles_from_html(text_body)

        # 去重并派发抓取落盘任务
        if articles_found:
            new_articles = []
            with self._lock:
                for art in articles_found:
                    guid = art["guid"]
                    if guid not in self.enqueued_urls:
                        self.enqueued_urls.add(guid)
                        new_articles.append(art)

            if new_articles:
                logger.info(f"✨ [搜一搜捕获] 从接口截获 {len(new_articles)} 篇待归档公众号文章！开始批量下载...")
                for art in new_articles:
                    self.executor.submit(self.archiver.archive_article, art)


# mitmproxy 加载入口
addons = [WechatSearchSnifferAddon()]


# =====================================================================
# 5. 命令行启动与系统代理自愈运行管理
# =====================================================================
def get_clipboard_text() -> str:
    """跨平台安全提取剪贴板纯文本（自动链式降级：pyperclip -> win32clipboard -> ctypes）"""
    try:
        import pyperclip
        t = pyperclip.paste()
        if t:
            return t
    except Exception:
        pass

    try:
        import win32clipboard
        import win32con
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                data = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                if data:
                    return str(data)
            elif win32clipboard.IsClipboardFormatAvailable(win32con.CF_TEXT):
                data = win32clipboard.GetClipboardData(win32con.CF_TEXT)
                if data:
                    return data.decode("gbk", errors="ignore")
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        pass

    if sys.platform == "win32":
        try:
            import ctypes
            u32 = ctypes.windll.user32
            k32 = ctypes.windll.kernel32
            if u32.OpenClipboard(None):
                try:
                    h_mem = u32.GetClipboardData(13)  # CF_UNICODETEXT
                    if h_mem:
                        p_mem = k32.GlobalLock(h_mem)
                        if p_mem:
                            try:
                                return ctypes.wstring_at(p_mem)
                            finally:
                                k32.GlobalUnlock(h_mem)
                finally:
                    u32.CloseClipboard()
        except Exception:
            pass

    return ""


def run_clipboard_listener(archiver: WechatArticleArchiver, stop_event: threading.Event, poll_interval: float = 0.8):
    """后台监听系统剪贴板：双轨支持【复制链接】秒级嗅探 与 【Ctrl+A 全选复制】正文直出归档"""
    # 尝试引入 win32clipboard 以便提取微信原生的 HTML 富文本排版
    has_win32clipboard = False
    try:
        import win32clipboard
        has_win32clipboard = True
    except ImportError:
        pass

    last_text = ""
    enqueued: Set[str] = set()
    latest_copied_url = ""
    latest_url_time = 0.0

    logger.info(
        "📋 [剪贴板助手] 已就绪（双轨保底）：\n"
        "   👉 方式一：在搜一搜卡片上【右键 -> 复制链接】自动拉取；\n"
        "   👉 方式二：在已打开的微信文章窗口按【Ctrl+A】全选【Ctrl+C】复制正文（秒级快捷归档）！"
    )

    while not stop_event.is_set():
        try:
            current_text = get_clipboard_text()
            if current_text and current_text != last_text:
                last_text = current_text
                now = time.time()

                # 场景 1：复制了公众号文章链接 (URL)
                matches = re.findall(r'https?://(?:mp\.)?weixin\.qq\.com/s[^\s\'"<>]+', current_text)
                if matches:
                    for u in matches:
                        clean_u = html.unescape(u).strip().rstrip("),;。，")
                        guid = clean_u.split("#")[0]
                        latest_copied_url = clean_u
                        latest_url_time = now

                        if guid not in enqueued:
                            enqueued.add(guid)
                            if not archiver.is_already_archived(guid, url=clean_u):
                                logger.info(f"📋 [剪贴板捕获] 侦测到复制的文章链接: {clean_u}")
                                threading.Thread(
                                    target=archiver.archive_article,
                                    args=({"url": clean_u, "title": "微信公众号文章", "guid": guid},),
                                    daemon=True
                                ).start()

                # 场景 2：在文章内直接按了 Ctrl+A 和 Ctrl+C 复制全文（长文本且非链接）
                elif len(current_text) > 80 and "\n" in current_text and not current_text.strip().startswith("[16:"):
                    # 获取潜在的 HTML 富文本格式（带并发互斥锁重试机制）
                    cf_html_str = None
                    if has_win32clipboard:
                        for attempt in range(5):
                            try:
                                win32clipboard.OpenClipboard()
                                try:
                                    cf_html_id = win32clipboard.RegisterClipboardFormat("HTML Format")
                                    if win32clipboard.IsClipboardFormatAvailable(cf_html_id):
                                        raw_bytes = win32clipboard.GetClipboardData(cf_html_id)
                                        if raw_bytes:
                                            if isinstance(raw_bytes, bytes):
                                                cf_html_str = raw_bytes.decode("utf-8", errors="ignore")
                                            elif isinstance(raw_bytes, str):
                                                cf_html_str = raw_bytes
                                    break
                                finally:
                                    win32clipboard.CloseClipboard()
                            except Exception:
                                time.sleep(0.04 * (attempt + 1))

                    # 若最近 10 分钟内复制过文章链接，自动关联来源
                    bound_url = latest_copied_url if (now - latest_url_time < 600) else ""

                    text_hash = hashlib.md5(current_text[:200].encode("utf-8", errors="ignore")).hexdigest()
                    if text_hash not in enqueued:
                        enqueued.add(text_hash)
                        threading.Thread(
                            target=archiver.archive_from_clipboard_content,
                            args=(current_text,),
                            kwargs={"html_content": cf_html_str, "source_url": bound_url},
                            daemon=True
                        ).start()

        except Exception as e:
            logger.debug(f"剪贴板监听异常: {e}")
        time.sleep(poll_interval)





def print_banner(host: str, port: int, output_dir: str, upstream_proxy: Optional[str] = None):
    upstream_desc = upstream_proxy if upstream_proxy else "直连外网"
    banner = f"""
======================================================================
     🚀 PC 端微信“搜一搜”流量嗅探与自动爬取助手 (WeChat Sniffer)
======================================================================
  [代理端口] http://{host}:{port}
  [上游网关] {upstream_desc}
  [文章归档] {output_dir}
  [双轨捕获] 1. 代理透明拦截（在微信内点击任意文章浏览即捕获）
             2. 剪贴板自动归档（搜一搜卡片上右键复制链接即秒级捕获）
  [安全说明] 按 Ctrl+C 或关闭本窗口，系统代理将 100% 自动还原
----------------------------------------------------------------------
  💡 极速使用指南（两种方式均可自动归档入库）：
  方式一【直接浏览】：在电脑微信搜一搜中检索，点击心仪文章打开阅读；
  方式二【一键复制】：在搜一搜结果卡片上【右键 -> 复制链接】（或文章右上角复制链接）；
  👉 助手将全自动嗅探、下载高清配图、生成 Markdown 及单文件 HTML！
======================================================================
"""
    print(banner)


def main():
    parser = argparse.ArgumentParser(description="PC 端微信“搜一搜”流量嗅探助手")
    parser.add_argument("--port", type=int, default=8080, help="中间人代理监听端口 (默认: 8080)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="代理监听地址 (默认: 127.0.0.1)")
    parser.add_argument("--auto-proxy", action="store_true", default=True, help="启动时自动开启 Windows 系统代理并在退出时复原 (默认开启)")
    parser.add_argument("--no-proxy", action="store_true", help="不自动修改 Windows 系统代理 (需手动配置)")
    parser.add_argument("--enable-proxy", action="store_true", help="工具命令：仅启用系统代理并立即退出")
    parser.add_argument("--disable-proxy", action="store_true", help="工具命令：仅禁用系统代理并立即退出")
    parser.add_argument("--status-proxy", action="store_true", help="工具命令：查询当前 Windows 系统代理配置")
    parser.add_argument("--install-cert", action="store_true", help="工具命令：安装本地根证书到受信任根证书库")
    parser.add_argument("--output-dir", type=str, default=os.path.join(PROJECT_ROOT, "output", "articles"), help="文章保存目录")
    parser.add_argument("--no-download-images", action="store_true", help="不下载图片到本地")

    args = parser.parse_args()

    # 处理独立工具命令
    if args.install_cert:
        cert_dir = os.path.expanduser("~/.mitmproxy")
        cert_file = os.path.join(cert_dir, "mitmproxy-ca-cert.cer")
        if not os.path.exists(cert_file):
            print("[1/2] 正在初始化生成本地专用根证书...")
            try:
                from mitmproxy.certs import CertStore
                CertStore.from_store(cert_dir, "mitmproxy", 2048)
            except Exception as e:
                print(f"[错误] 生成证书异常: {e}")
                sys.exit(1)
        else:
            print(f"[1/2] 本地证书已就绪: {cert_file}")

        print("\n======================================================================")
        print("         PC 微信“搜一搜”嗅探助手 —— 本地根证书安装向导")
        print("======================================================================")
        print("【重要提示】")
        print("Windows 即将弹出【安全警告】对话框：")
        print("询问：“是否将证书添加到‘受信任的根证书颁发机构’存储区？”")
        print(">>> 请务必在弹出的窗口中点击【是(Y)】允许安装！ <<<")
        print("======================================================================\n")

        ret = os.system(f'certutil -addstore -user Root "{cert_file}"')
        if ret == 0:
            print("\n======================================================================")
            print(" [OK] 根证书已成功安装到【受信任的根证书颁发机构】！")
            print(" 现在您可以直接双击运行【启动搜一搜监听助手.bat】开始抓取文章。")
            print("======================================================================")
        else:
            print("\n[提示] 未能成功安装证书。如果在弹窗中点击了“否”，可随时重新运行进行安装。")
        sys.exit(0)

    if args.disable_proxy:
        WindowsProxyManager.disable_proxy(force_off=True)
        sys.exit(0)

    if args.enable_proxy:
        WindowsProxyManager.enable_proxy(host=args.host, port=args.port)
        sys.exit(0)

    if args.status_proxy:
        st = WindowsProxyManager.get_proxy_status()
        print(f"Windows 系统代理状态: 开启={st['enabled']}, 服务器={st['server']}")
        sys.exit(0)

    target_port = args.port
    if not is_port_available(args.host, target_port):
        new_port = find_available_port(args.host, target_port + 1)
        logger.warning(f"⚠️ 默认端口 {target_port} 已被占用，自动切换为可用端口 {new_port}")
        target_port = new_port

    manage_proxy = args.auto_proxy and not args.no_proxy
    proxy_enabled = False

    # 注册退出与信号清理函数
    def _cleanup():
        nonlocal proxy_enabled
        if proxy_enabled:
            WindowsProxyManager.disable_proxy()
            proxy_enabled = False

    atexit.register(_cleanup)

    def _sig_handler(signum, frame):
        _cleanup()
        sys.exit(0)

    try:
        signal.signal(signal.SIGINT, _sig_handler)
        signal.signal(signal.SIGTERM, _sig_handler)
    except Exception:
        pass

    setup_windows_close_handler(_cleanup)

    # 在启用本地系统代理前，优先探测并锁定环境上游代理
    upstream_proxy = get_upstream_proxy()
    if upstream_proxy:
        os.environ["WECHAT_UPSTREAM_PROXY"] = upstream_proxy

    if manage_proxy:
        proxy_enabled = WindowsProxyManager.enable_proxy(host=args.host, port=target_port)

    # 通过环境变量向下传递给 mitmdump 中的 Addon
    os.environ["WECHAT_SNIFFER_OUTPUT_DIR"] = os.path.abspath(args.output_dir)
    os.environ["WECHAT_SNIFFER_DOWNLOAD_IMAGES"] = "0" if args.no_download_images else "1"

    print_banner(args.host, target_port, args.output_dir, upstream_proxy)

    # 启动后台剪贴板监听线程（双重保障）
    clipboard_stop_event = threading.Event()
    global_archiver = WechatArticleArchiver(
        output_dir=os.path.abspath(args.output_dir),
        download_images=not args.no_download_images
    )
    clip_thread = threading.Thread(
        target=run_clipboard_listener,
        args=(global_archiver, clipboard_stop_event),
        name="ClipboardSniffer",
        daemon=True
    )
    clip_thread.start()

    # 启动 mitmdump
    try:
        import subprocess
        # 寻找 mitmdump 可执行文件路径
        venv_mitmdump = os.path.join(PROJECT_ROOT, ".venv", "Scripts", "mitmdump.exe")
        if not os.path.exists(venv_mitmdump):
            venv_mitmdump = "mitmdump"

        allow_hosts_pattern = r"(weixin\.qq\.com|wechat\.com|qpic\.cn|wx\.qq\.com|servicewechat\.com|qq\.com)"

        cmd = [
            venv_mitmdump,
            "-p", str(target_port),
            "--listen-host", args.host,
            "-s", os.path.abspath(__file__),
            "--set", "block_global=false",
            "--set", "flow_detail=1",
            "--set", "termlog_verbosity=info"
        ]

        if upstream_proxy:
            cmd.extend(["--mode", f"upstream:{upstream_proxy}"])
            logger.info(f"🌐 侦测到上游代理网关: {upstream_proxy}，已配置上游代理级联模式")
        else:
            cmd.extend(["--allow-hosts", allow_hosts_pattern])

        # 子进程环境变量中清除代理变量，防止 mitmdump 自身向 8080 陷入死锁
        child_env = os.environ.copy()
        for k in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]:
            child_env.pop(k, None)

        logger.info(f"正在启动 mitmdump 核心服务 (端口: {target_port}): {' '.join(cmd)}")
        proc = subprocess.run(cmd, env=child_env)
    except KeyboardInterrupt:
        logger.info("用户按 Ctrl+C 中止程序...")
    except Exception as e:
        logger.error(f"运行嗅探服务异常: {e}")
    finally:
        clipboard_stop_event.set()
        _cleanup()


if __name__ == "__main__":
    main()

