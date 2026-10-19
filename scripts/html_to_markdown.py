# -*- coding: utf-8 -*-
"""
专为微信公众号优化的 HTML 转 Markdown 转换器
特性：
1. 自动处理微信图片的 data-src / data-original 防盗链机制
2. 可选自动下载图片到本地并替换为 Markdown 相对路径
3. 自动注入 YAML Frontmatter（兼容 Obsidian / Logseq / Notion）
4. 清洗微信特有的广告块、引导关注段落与冗余样式标签
"""

import os
import re
import glob
import base64
import shutil
import hashlib
import urllib.parse
import urllib.request
import warnings
from typing import Optional, Dict, Any
import logging
import requests
from bs4 import BeautifulSoup, Tag, NavigableString, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

logger = logging.getLogger("HtmlToMarkdown")


def is_valid_image_bytes(data: bytes) -> bool:
    """严格校验二进制数据是否为真实图片格式（防范网络网关拦截或防盗链返回的 HTML 错误页）"""
    if not data or len(data) < 8:
        return False
    # PNG, JPEG, GIF, WebP, BMP, ICO
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if data.startswith(b"\xff\xd8\xff"):
        return True
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return True
    if data.startswith(b"RIFF") and len(data) > 12 and data[8:12] == b"WEBP":
        return True
    if data.startswith(b"BM"):
        return True
    if data.startswith(b"\x00\x00\x01\x00"):
        return True
    # SVG (XML or svg root)
    trimmed = data[:512].strip()
    if trimmed.startswith(b"<?xml") or trimmed.startswith(b"<svg") or b"<svg" in trimmed.lower():
        if b"<html" not in trimmed.lower() and b"<!doctype" not in trimmed.lower():
            return True
    return False


class WechatHtmlToMarkdown:
    def __init__(
        self,
        download_images: bool = True,
        images_dir: Optional[str] = None,
        images_rel_path: str = "images",
        remove_ads: bool = True,
        filter_keywords: Optional[list] = None
    ):
        """
        :param download_images: 是否将图片下载到本地
        :param images_dir: 图片本地存储绝对/完整路径（若开启下载）
        :param images_rel_path: Markdown 内部引用的图片相对路径前缀
        :param remove_ads: 是否自动清理广告段落
        :param filter_keywords: 自定义过滤关键词列表
        """
        self.download_images = download_images
        self.images_dir = images_dir
        self.images_rel_path = images_rel_path
        self.remove_ads = remove_ads
        self.filter_keywords = filter_keywords or [
            "点击上方蓝字关注",
            "长按二维码识别关注",
            "喜欢本文请点在看",
            "未经授权禁止转载",
            "欢迎关注公众号",
            "扫码进入交流群"
        ]

        # 图片下载会话，设置反防盗链 headers
        self.session = requests.Session()
        self.session.trust_env = True
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": "https://mp.weixin.qq.com/"
        })

    def _request_adaptive(self, method: str, url: str, **kwargs) -> requests.Response:
        """网络请求：具备超时与异常容错重试机制"""
        kwargs.setdefault("timeout", 15)

        try:
            return self.session.request(method, url, **kwargs)
        except (requests.exceptions.ProxyError, requests.exceptions.ConnectTimeout, requests.exceptions.SSLError, requests.exceptions.ConnectionError) as e:
            logger.debug(f"网络通道请求重试 ({e})...")
            fallback = requests.Session()
            try:
                fallback.headers.update(self.session.headers)
                return fallback.request(method, url, **kwargs)
            finally:
                fallback.close()

    def _get_image_ext(self, url: str, content_type: Optional[str] = None) -> str:
        """从 URL 或 Content-Type 推断图片扩展名（微信常见 wx_fmt=png 等）"""
        parsed = urllib.parse.urlparse(url)
        query_params = urllib.parse.parse_qs(parsed.query)

        # 微信通常在 query 中包含 wx_fmt=png/jpeg/webp
        if "wx_fmt" in query_params:
            fmt = query_params["wx_fmt"][0].lower()
            if fmt in ["jpeg", "jpg"]:
                return ".jpg"
            elif fmt in ["png", "webp", "gif", "svg"]:
                return f".{fmt}"

        if content_type:
            ct = content_type.lower()
            if "jpeg" in ct or "jpg" in ct:
                return ".jpg"
            elif "png" in ct:
                return ".png"
            elif "webp" in ct:
                return ".webp"
            elif "gif" in ct:
                return ".gif"

        # 尝试从路径本身后缀获取
        path_ext = os.path.splitext(parsed.path)[1].lower()
        if path_ext in [".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"]:
            return path_ext

        return ".png"

    def _download_image(self, img_url: str) -> Optional[str]:
        """下载图片到本地，返回相对路径"""
        if not self.download_images or not self.images_dir or not img_url:
            return None

        # 协议自愈与安全规范化
        if img_url.startswith("//"):
            img_url = "https:" + img_url
        elif img_url.startswith("http://") and any(k in img_url for k in ["qpic.cn", "weixin.qq.com"]):
            img_url = "https://" + img_url[7:]

        os.makedirs(self.images_dir, exist_ok=True)

        # 使用 URL 的 MD5 作为文件名
        url_hash = hashlib.md5(img_url.encode("utf-8")).hexdigest()

        # 本地已有同一 URL 的文件就直接复用，不再打微信 CDN。
        # 同时校验历史缓存是否包含损坏的假图片文件（如网络错误返回的 HTML 页面）
        for cached in glob.glob(os.path.join(self.images_dir, f"{url_hash}.*")):
            if os.path.exists(cached) and os.path.getsize(cached) > 0:
                try:
                    with open(cached, "rb") as fp:
                        if is_valid_image_bytes(fp.read(512)):
                            return f"{self.images_rel_path.rstrip('/')}/{os.path.basename(cached)}"
                        else:
                            os.remove(cached)
                except Exception:
                    pass

        # 处理本地 file:// 协议与绝对文件路径
        local_file = None
        if img_url.startswith("file://"):
            parsed = urllib.parse.urlparse(img_url)
            local_file = urllib.request.url2pathname(parsed.path)
        elif not (img_url.startswith("http://") or img_url.startswith("https://")):
            if os.path.isabs(img_url) or (len(img_url) > 2 and img_url[1] == ":"):
                local_file = img_url

        if local_file and os.path.exists(local_file):
            try:
                with open(local_file, "rb") as fp:
                    local_bytes = fp.read()
                if is_valid_image_bytes(local_bytes):
                    ext = os.path.splitext(local_file)[1].lower() or ".png"
                    filename = f"{url_hash}{ext}"
                    target_path = os.path.join(self.images_dir, filename)
                    with open(target_path, "wb") as fp:
                        fp.write(local_bytes)
                    return f"{self.images_rel_path.rstrip('/')}/{filename}"
            except Exception as e:
                logger.debug(f"复制本地图片失败 [{local_file}]: {e}")

        try:
            resp = self._request_adaptive("GET", img_url, timeout=15)
            if resp.status_code == 200 and resp.content:
                if not is_valid_image_bytes(resp.content):
                    logger.warning(f"下载内容不是合法图片 (可能遭遇网关或防盗链拦截): {img_url}")
                    return None

                ext = self._get_image_ext(img_url, resp.headers.get("Content-Type"))
                filename = f"{url_hash}{ext}"
                target_path = os.path.join(self.images_dir, filename)

                with open(target_path, "wb") as f:
                    f.write(resp.content)

                # 返回 Markdown 中使用的引用路径
                return f"{self.images_rel_path.rstrip('/')}/{filename}"
        except Exception as e:
            # 图片下载失败时不中断整个文章解析，降级返回原 URL
            # 用 logging 而非 print：pythonw.exe 静默运行时没有 stdout，print 会被直接丢弃
            logger.warning(f"图片下载失败 {img_url}: {e}")

        return None

    def _clean_soup(self, soup: BeautifulSoup):
        """清洗无用标签与广告"""
        # 移除不可见与脚本标签
        for tag in soup(["script", "style", "noscript", "iframe"]):
            tag.decompose()

        # 微信特有的二维码和底部关注推荐常见 class/id
        ad_classes = ["qr_code_pc", "reward_area", "weui-loadmore", "rich_media_tool"]
        for cls_name in ad_classes:
            for el in soup.find_all(class_=cls_name):
                el.decompose()

    def _process_node(self, node) -> str:
        """递归将 DOM 节点转为 Markdown 字符串"""
        if isinstance(node, NavigableString):
            text = str(node)
            # 过滤不可见零宽字符
            return re.sub(r'[\u200b\u200c\u200d\ufeff]', '', text)

        if not isinstance(node, Tag):
            return ""

        tag_name = node.name.lower()

        # 处理图片（微信优先读取 data-src / data-original）
        if tag_name == "img":
            img_url = (
                node.get("data-src")
                or node.get("data-original")
                or node.get("src")
                or ""
            ).strip()

            if not img_url:
                return ""

            alt = node.get("alt", "") or "图片"

            # 处理 Base64 内嵌图片，避免直接遗弃丢图
            if img_url.startswith("data:image"):
                if self.download_images and self.images_dir:
                    try:
                        os.makedirs(self.images_dir, exist_ok=True)
                        header, b64_data = img_url.split(",", 1)
                        m = re.search(r'data:image/(\w+);base64', header)
                        ext = f".{m.group(1).lower()}" if m else ".png"
                        if ext in [".jpeg", ".jpg"]:
                            ext = ".jpg"
                        img_bytes = base64.b64decode(b64_data)
                        if not is_valid_image_bytes(img_bytes):
                            logger.warning("Base64 解码数据不是有效图片格式，跳过保存")
                            return ""
                        url_hash = hashlib.md5(img_bytes).hexdigest()
                        filename = f"{url_hash}{ext}"
                        target_path = os.path.join(self.images_dir, filename)
                        with open(target_path, "wb") as f:
                            f.write(img_bytes)
                        return f"\n\n![{alt}]({self.images_rel_path.rstrip('/')}/{filename})\n\n"
                    except Exception as e:
                        logger.warning(f"保存 Base64 图片失败: {e}")
                return f"\n\n![{alt}]({img_url})\n\n"

            local_path = self._download_image(img_url)
            final_src = local_path if local_path else img_url
            return f"\n\n![{alt}]({final_src})\n\n"

        # 处理超链接
        if tag_name == "a":
            href = node.get("href", "").strip()
            content = "".join(self._process_node(child) for child in node.children).strip()
            if not href:
                return content
            if not content:
                content = href
            return f"[{content}]({href})"

        # 处理标题
        if tag_name in ["h1", "h2", "h3", "h4", "h5", "h6"]:
            level = int(tag_name[1])
            content = "".join(self._process_node(child) for child in node.children).strip()
            if content:
                return f"\n\n{'#' * level} {content}\n\n"
            return ""

        # 处理加粗与斜体
        if tag_name in ["strong", "b"]:
            content = "".join(self._process_node(child) for child in node.children).strip()
            return f" **{content}** " if content else ""

        if tag_name in ["em", "i"]:
            content = "".join(self._process_node(child) for child in node.children).strip()
            return f" *{content}* " if content else ""

        # 处理行内代码与代码块
        if tag_name == "pre":
            code_text = node.get_text()
            return f"\n\n```\n{code_text.strip()}\n```\n\n"

        if tag_name == "code":
            if node.parent and node.parent.name.lower() == "pre":
                return node.get_text()
            return f" `{node.get_text()}` "

        # 处理引用块
        if tag_name == "blockquote":
            content = "".join(self._process_node(child) for child in node.children).strip()
            lines = [f"> {line}" for line in content.split("\n")]
            return f"\n\n{chr(10).join(lines)}\n\n"

        # 处理换行与分割线
        if tag_name == "br":
            return "\n"
        if tag_name == "hr":
            return "\n\n---\n\n"

        # 处理列表
        if tag_name == "ul":
            items = []
            for li in node.find_all("li", recursive=False):
                li_text = "".join(self._process_node(c) for c in li.children).strip()
                if li_text:
                    items.append(f"- {li_text}")
            return f"\n\n{chr(10).join(items)}\n\n"

        if tag_name == "ol":
            items = []
            for idx, li in enumerate(node.find_all("li", recursive=False), 1):
                li_text = "".join(self._process_node(c) for c in li.children).strip()
                if li_text:
                    items.append(f"{idx}. {li_text}")
            return f"\n\n{chr(10).join(items)}\n\n"

        if tag_name == "span":
            children_text = "".join(self._process_node(child) for child in node.children)
            clean_t = children_text.strip()
            if clean_t in ["图片", "[图片]", "图片1", "图片2", "点击查看大图", "（图片）"] and "![" not in clean_t:
                return ""
            return children_text

        # 处理段落和容器 (p, div, section)
        children_text = "".join(self._process_node(child) for child in node.children)
        if tag_name in ["p", "section", "div"]:
            # 检查广告关键词
            if self.remove_ads:
                for kw in self.filter_keywords:
                    if kw in children_text and len(children_text.strip()) < 100:
                        return ""
            if children_text.strip():
                clean_t = children_text.strip()
                # 核心整风过滤：彻底剔除从纯文本退化而来的孤立噪点文字“图片”或“[图片]”
                if clean_t in ["图片", "[图片]", "图片1", "图片2", "点击查看大图", "（图片）"] and "![" not in clean_t:
                    return ""
                return f"\n\n{clean_t}\n\n"
            return ""

        return children_text

    def convert(self, html_content: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """
        核心转换函数：输入 HTML 和元数据，输出格式化好的 Markdown
        """
        if not html_content:
            return ""

        # 清除可能残留的 XML 头部声明，避免 HTML 解析器告警
        clean_html = re.sub(r'^\s*<\?xml[^>]*\?>', '', html_content, flags=re.IGNORECASE)
        soup = BeautifulSoup(clean_html, "html.parser")
        self._clean_soup(soup)

        # 优先寻找微信正文容器
        body_node = (
            soup.find("div", id="js_content")
            or soup.find("div", class_="rich_media_content")
            or soup.body
            or soup
        )

        md_body = self._process_node(body_node)

        # 连续空行归一化
        md_body = re.sub(r'\n{3,}', '\n\n', md_body).strip()

        # 生成 YAML Frontmatter
        frontmatter = ""
        if metadata:
            fm_lines = ["---"]
            title = metadata.get("title", "").replace('"', '\\"')
            author = metadata.get("author", "").replace('"', '\\"')
            date = metadata.get("date", "")
            source_url = (metadata.get("source_url") or metadata.get("url") or "").strip()

            fm_lines.append(f'title: "{title}"')
            if author:
                fm_lines.append(f'author: "{author}"')
            if date:
                fm_lines.append(f'date: "{date}"')
            if source_url:
                fm_lines.append(f'source_url: "{source_url}"')

            # 默认标签
            custom_tags = metadata.get("tags")
            if custom_tags:
                if isinstance(custom_tags, str):
                    tags = [custom_tags]
                else:
                    tags = list(custom_tags)
            else:
                tags = ["微信公众号"]
                if author:
                    tags.append(author)
            fm_lines.append("tags:")
            for t in tags:
                fm_lines.append(f"  - {t}")

            fm_lines.append("---\n\n")
            frontmatter = "\n".join(fm_lines)

        return frontmatter + md_body
