#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
单文件独立 HTML 导出器 (WeChat Article Standalone HTML Exporter)
-------------------------------------------------------------
功能：
1. 将 Markdown 文章及其引用的本地图片（防盗链图片）转换为自包含（Self-contained）的单文件 HTML。
2. 图片全部通过 Base64 内嵌，脱离外部文件夹依然完整显示，单文件任意传输永不丢图。
3. 遵循微信公众号优雅阅读排版（字号、行距、引用框、自适应布局）。
4. 交互式剪贴板增强：
   - 【📋 复制纯文字】：自动提取全文文本，过滤格式代码，一键粘贴；
   - 【📑 复制全文 (图文)】：将图文一体的富文本写入系统剪贴板，可直接在微信电脑版、Word、飞书文档中 Ctrl+V；
   - 【🖼️ 单图一键复制】：每张图片右上角悬浮复制按键，点击直出 PNG 位图到系统剪贴板，微信聊天框 Ctrl+V 直接发送。
"""

import os
import re
import sys
import html
import glob
import hashlib
import base64
import mimetypes
import logging
import urllib.parse
import urllib.request
from typing import Optional, Dict, Any
from bs4 import BeautifulSoup

try:
    import requests
except ImportError:
    requests = None

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    import markdown
except ImportError:
    markdown = None

logger = logging.getLogger("HTMLExporter")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="referrer" content="no-referrer">
  <title>{page_title}</title>
  <style>
    :root {{
      --primary-color: #07c160;
      --primary-hover: #06ad56;
      --bg-color: #f7f7f7;
      --card-bg: #ffffff;
      --text-main: #222222;
      --text-sub: #888888;
      --border-color: #ebebeb;
      --quote-bg: #f8f9fa;
      --code-bg: #f3f4f6;
    }}
    * {{
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
      background-color: var(--bg-color);
      color: var(--text-main);
      line-height: 1.8;
      letter-spacing: 0.03em;
      -webkit-font-smoothing: antialiased;
      padding-bottom: 60px;
    }}

    /* 顶部固定操作工具栏 */
    .top-toolbar {{
      position: sticky;
      top: 0;
      z-index: 1000;
      background: rgba(255, 255, 255, 0.95);
      backdrop-filter: blur(12px);
      -webkit-backdrop-filter: blur(12px);
      border-bottom: 1px solid var(--border-color);
      padding: 4px 16px;
      height: 38px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      box-shadow: 0 1px 6px rgba(0, 0, 0, 0.03);
    }}
    .toolbar-left {{
      display: flex;
      align-items: center;
      gap: 8px;
      overflow: hidden;
      white-space: nowrap;
      text-overflow: ellipsis;
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 4px;
      background: #e8f7ee;
      color: var(--primary-color);
      font-size: 11px;
      font-weight: 600;
      padding: 2px 6px;
      border-radius: 4px;
      flex-shrink: 0;
    }}
    .toolbar-title {{
      font-size: 13px;
      font-weight: 500;
      color: #555;
      overflow: hidden;
      text-overflow: ellipsis;
      max-width: 320px;
    }}
    .toolbar-right {{
      display: flex;
      align-items: center;
      gap: 8px;
      flex-shrink: 0;
    }}
    .btn {{
      display: inline-flex;
      align-items: center;
      gap: 5px;
      padding: 4px 11px;
      font-size: 12px;
      font-weight: 500;
      border-radius: 5px;
      border: 1px solid #d1d5db;
      background: #ffffff;
      color: #374151;
      cursor: pointer;
      transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
      user-select: none;
    }}
    .btn:hover {{
      background: #f9fafb;
      border-color: #9ca3af;
      transform: translateY(-1px);
    }}
    .btn:active {{
      transform: translateY(0);
    }}
    .btn-primary {{
      background: var(--primary-color);
      color: #ffffff;
      border-color: var(--primary-color);
    }}
    .btn-primary:hover {{
      background: var(--primary-hover);
      border-color: var(--primary-hover);
      color: #ffffff;
      box-shadow: 0 2px 8px rgba(7, 193, 96, 0.3);
    }}

    /* 文章主容器 - 紧凑高可视面积 */
    .article-container {{
      max-width: 820px;
      margin: 12px auto;
      background: var(--card-bg);
      padding: 22px 30px;
      border-radius: 10px;
      box-shadow: 0 2px 12px rgba(0, 0, 0, 0.04);
      border: 1px solid var(--border-color);
    }}
    @media (max-width: 768px) {{
      .article-container {{
        margin: 6px auto;
        padding: 16px 14px;
        border-radius: 6px;
      }}
      .toolbar-title {{
        display: none;
      }}
    }}

    /* 标题与元信息区 */
    .article-header {{
      margin-bottom: 18px;
      padding-bottom: 14px;
      border-bottom: 1px solid var(--border-color);
    }}
    .article-title {{
      font-size: 21px;
      font-weight: 700;
      color: #111827;
      line-height: 1.35;
      margin-bottom: 10px;
    }}
    .article-meta {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 14px;
      font-size: 13px;
      color: var(--text-sub);
    }}
    .meta-author {{
      color: #576b95;
      font-weight: 600;
    }}
    .meta-tag {{
      background: #f3f4f6;
      color: #6b7280;
      padding: 2px 8px;
      border-radius: 12px;
      font-size: 12px;
    }}

    /* 正文排版 */
    .article-body {{
      font-size: 16px;
      color: #2b2b2b;
      word-break: break-word;
    }}
    .article-body p {{
      margin-bottom: 1.4em;
      text-align: justify;
    }}
    .article-body h1, .article-body h2, .article-body h3 {{
      margin-top: 1.8em;
      margin-bottom: 0.8em;
      color: #111;
      font-weight: 700;
      line-height: 1.4;
    }}
    .article-body h1 {{ font-size: 20px; border-left: 4px solid var(--primary-color); padding-left: 10px; }}
    .article-body h2 {{ font-size: 18px; }}
    .article-body h3 {{ font-size: 16px; }}
    .article-body strong {{
      font-weight: 600;
      color: #111827;
    }}
    .article-body blockquote {{
      margin: 1.2em 0;
      padding: 12px 18px;
      background: var(--quote-bg);
      border-left: 4px solid #cbd5e1;
      color: #475569;
      border-radius: 0 6px 6px 0;
    }}
    .article-body ul, .article-body ol {{
      margin: 1em 0 1.4em 1.5em;
    }}
    .article-body li {{
      margin-bottom: 0.4em;
    }}
    .article-body a {{
      color: #576b95;
      text-decoration: none;
    }}
    .article-body a:hover {{
      text-decoration: underline;
    }}

    /* 图片容器与单图复制按键 */
    .img-box {{
      position: relative;
      display: block;
      margin: 20px auto;
      text-align: center;
      max-width: 100%;
    }}
    .img-box img {{
      max-width: 100%;
      height: auto;
      border-radius: 8px;
      display: inline-block;
      box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
      transition: opacity 0.2s ease;
    }}
    .copy-img-btn {{
      position: absolute;
      top: 12px;
      right: 12px;
      background: rgba(15, 23, 42, 0.78);
      color: #ffffff;
      border: 1px solid rgba(255, 255, 255, 0.25);
      backdrop-filter: blur(8px);
      -webkit-backdrop-filter: blur(8px);
      padding: 6px 12px;
      font-size: 12px;
      font-weight: 500;
      border-radius: 6px;
      cursor: pointer;
      opacity: 0;
      transform: translateY(4px);
      transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
      display: inline-flex;
      align-items: center;
      gap: 5px;
      box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
      z-index: 20;
      user-select: none;
      -webkit-user-select: none;
      -moz-user-select: none;
      -ms-user-select: none;
    }}
    .img-box:hover .copy-img-btn {{
      opacity: 1;
      transform: translateY(0);
    }}
    @media (hover: none) {{
      /* 触屏移动端默认弱显 */
      .copy-img-btn {{
        opacity: 0.85;
        transform: translateY(0);
      }}
    }}
    .copy-img-btn:hover {{
      background: var(--primary-color);
      border-color: var(--primary-color);
      color: #ffffff;
    }}

    /* 底部 Toast 弹窗通知 */
    .toast {{
      position: fixed;
      bottom: 40px;
      left: 50%;
      transform: translateX(-50%) translateY(30px);
      background: rgba(17, 24, 39, 0.92);
      color: #ffffff;
      padding: 12px 26px;
      border-radius: 40px;
      font-size: 14px;
      font-weight: 500;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.3);
      backdrop-filter: blur(10px);
      -webkit-backdrop-filter: blur(10px);
      opacity: 0;
      pointer-events: none;
      transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
      z-index: 99999;
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .toast.show {{
      opacity: 1;
      transform: translateX(-50%) translateY(0);
    }}
  </style>
</head>
<body>

  <!-- 顶部悬浮工具栏 -->
  <header class="top-toolbar">
    <div class="toolbar-left">
      <span class="badge">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/></svg>
        单文件离线版
      </span>
      <span class="toolbar-title" title="{page_title}">{page_title}</span>
    </div>
    <div class="toolbar-right">
      <button class="btn" onclick="copyPureText()" title="一键复制纯文字内容（自动滤除排版代码）">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line></svg>
        复制文字
      </button>
      <button class="btn btn-primary" onclick="copyFullRichText()" title="一键复制全文（图文一体），可在微信/Word/飞书中按 Ctrl+V 粘贴">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
        复制全文 (图文)
      </button>
    </div>
  </header>

  <!-- 文章主体容器 -->
  <main class="article-container" id="article-root">
    <header class="article-header">
      <h1 class="article-title">{article_title}</h1>
      <div class="article-meta">
        {meta_html}
      </div>
    </header>

    <article class="article-body" id="article-body">
      {body_html}
    </article>
  </main>

  <!-- 提示弹窗 -->
  <div id="toast" class="toast">
    <span id="toast-icon">✅</span>
    <span id="toast-msg">已成功复制到剪贴板！</span>
  </div>

  <script>
    function showToast(msg, icon = '✅') {{
      const toast = document.getElementById('toast');
      const toastMsg = document.getElementById('toast-msg');
      const toastIcon = document.getElementById('toast-icon');
      toastMsg.innerText = msg;
      toastIcon.innerText = icon;
      toast.classList.add('show');
      clearTimeout(window.__toastTimer);
      window.__toastTimer = setTimeout(() => {{
        toast.classList.remove('show');
      }}, 2400);
    }}

    // 1. 一键复制纯文字
    async function copyPureText() {{
      const title = document.querySelector('.article-title')?.innerText?.trim() || '';
      const bodyEl = document.getElementById('article-body');
      let bodyText = '';
      if (bodyEl) {{
        // 克隆节点净化所有交互按钮（如“复制图片”按钮）、脚本及样式，确保只提取纯正正文
        const clone = bodyEl.cloneNode(true);
        clone.querySelectorAll('.copy-img-btn, button, script, style').forEach(el => el.remove());
        bodyText = clone.innerText?.trim() || '';
        // 规避移除节点后可能遗留的多余连续换行
        bodyText = bodyText.replace(/\\n{{3,}}/g, '\\n\\n');
      }}
      const fullText = title ? `${{title}}\\n\\n${{bodyText}}` : bodyText;

      try {{
        await navigator.clipboard.writeText(fullText);
        showToast('已复制纯文本！可直接 Ctrl+V 粘贴');
      }} catch (err) {{
        // 降级方案
        const ta = document.createElement('textarea');
        ta.value = fullText;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        showToast('已复制纯文本！');
      }}
    }}

    // 2. 一键复制全文（图文并茂富文本）
    async function copyFullRichText() {{
      const root = document.getElementById('article-root');
      if (!root) return;

      // 克隆节点以净化掉单独的复制按钮与控制元素
      const clone = root.cloneNode(true);
      clone.querySelectorAll('.copy-img-btn, button, script, style').forEach(el => el.remove());

      const htmlContent = clone.innerHTML;
      const textContent = clone.innerText;

      try {{
        if (navigator.clipboard && window.ClipboardItem) {{
          const blobHtml = new Blob([htmlContent], {{ type: 'text/html' }});
          const blobText = new Blob([textContent], {{ type: 'text/plain' }});
          await navigator.clipboard.write([
            new ClipboardItem({{
              'text/html': blobHtml,
              'text/plain': blobText
            }})
          ]);
          showToast('🎉 全文(图文)已复制！可在微信/Word中按 Ctrl+V 粘贴');
          return;
        }}
      }} catch (e) {{
        console.warn('ClipboardItem API 异常，回退降级方案:', e);
      }}

      // 降级使用选区复制
      try {{
        const selection = window.getSelection();
        const range = document.createRange();
        range.selectNodeContents(root);
        selection.removeAllRanges();
        selection.addRange(range);
        document.execCommand('copy');
        selection.removeAllRanges();
        showToast('🎉 全文已复制！可直接在微信/文档中按 Ctrl+V 粘贴');
      }} catch (err) {{
        showToast('复制失败，请尝试鼠标全选后 Ctrl+C 复制', '⚠️');
      }}
    }}

    // 3. 每张图上单独的一键复制图片按键
    async function copySingleImage(btn) {{
      const container = btn.closest('.img-box');
      if (!container) return;
      const img = container.querySelector('img');
      if (!img) return;

      const originalText = btn.innerHTML;
      btn.innerText = '正在复制...';

      try {{
        // 使用 Canvas 生成标准的 image/png 二进制 Blob
        const canvas = document.createElement('canvas');
        canvas.width = img.naturalWidth || img.width;
        canvas.height = img.naturalHeight || img.height;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(img, 0, 0);

        canvas.toBlob(async (blob) => {{
          if (!blob) {{
            throw new Error('Canvas toBlob 返回空');
          }}
          try {{
            await navigator.clipboard.write([
              new ClipboardItem({{ 'image/png': blob }})
            ]);
            btn.innerText = '✓ 已复制';
            showToast('✅ 图片已成功复制！可在微信聊天中直接 Ctrl+V 粘贴');
            setTimeout(() => {{ btn.innerHTML = originalText; }}, 2000);
          }} catch (writeErr) {{
            // 尝试通过 fetch blob
            fallbackFetchCopy(img.src, btn, originalText);
          }}
        }}, 'image/png');
      }} catch (err) {{
        fallbackFetchCopy(img.src, btn, originalText);
      }}
    }}

    async function fallbackFetchCopy(dataUri, btn, originalText) {{
      try {{
        const res = await fetch(dataUri);
        const blob = await res.blob();
        await navigator.clipboard.write([
          new ClipboardItem({{ [blob.type]: blob }})
        ]);
        btn.innerText = '✓ 已复制';
        showToast('✅ 图片已复制！可直接在微信中 Ctrl+V');
        setTimeout(() => {{ btn.innerHTML = originalText; }}, 2000);
      }} catch (e) {{
        btn.innerHTML = originalText;
        showToast('当前浏览器限制直接写剪贴板，请右键图片选“复制图片”', '💡');
      }}
    }}
  </script>
</body>
</html>
"""


def is_valid_image_bytes(data: bytes) -> bool:
    """严格校验二进制数据是否为真实图片格式（防范网络网关拦截或防盗链返回的 HTML 错误页）"""
    if not data or len(data) < 8:
        return False
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
    trimmed = data[:512].strip()
    if trimmed.startswith(b"<?xml") or trimmed.startswith(b"<svg") or b"<svg" in trimmed.lower():
        if b"<html" not in trimmed.lower() and b"<!doctype" not in trimmed.lower():
            return True
    return False


def get_image_base64_uri(img_path: str) -> Optional[str]:
    """读取本地图片并转换为 Base64 Data URI"""
    if not img_path:
        return None

    # 支持 file:/// 协议路径
    if img_path.startswith("file://"):
        parsed = urllib.parse.urlparse(img_path)
        img_path = urllib.request.url2pathname(parsed.path)

    if not os.path.exists(img_path):
        # 尝试在全局图片缓存备选路径中查找
        candidate = os.path.join(PROJECT_ROOT, "output", "articles", "images", os.path.basename(img_path))
        if os.path.exists(candidate):
            img_path = candidate
        else:
            return None

    mime_type, _ = mimetypes.guess_type(img_path)
    if not mime_type:
        ext = os.path.splitext(img_path)[1].lower()
        if ext in [".jpg", ".jpeg"]:
            mime_type = "image/jpeg"
        elif ext == ".png":
            mime_type = "image/png"
        elif ext == ".gif":
            mime_type = "image/gif"
        elif ext == ".webp":
            mime_type = "image/webp"
        elif ext == ".svg":
            mime_type = "image/svg+xml"
        else:
            mime_type = "image/jpeg"

    try:
        with open(img_path, "rb") as f:
            raw_bytes = f.read()

        if not is_valid_image_bytes(raw_bytes):
            logger.debug(f"文件非有效图片数据，跳过 Base64 转换 [{img_path}]")
            return None

        b64_data = base64.b64encode(raw_bytes).decode("utf-8")
        return f"data:{mime_type};base64,{b64_data}"
    except Exception as e:
        logger.warning(f"读取图片转 Base64 失败 [{img_path}]: {e}")
        return None


def fetch_and_cache_remote_image(url: str, images_dir: Optional[str] = None) -> Optional[str]:
    """抓取外部图片（携带防盗链 Referer）并缓存到本地，返回本地绝对路径"""
    if not url:
        return None

    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("http://") and any(k in url for k in ["qpic.cn", "weixin.qq.com"]):
        url = "https://" + url[7:]

    if not (url.startswith("http://") or url.startswith("https://")):
        return None

    if not images_dir:
        images_dir = os.path.join(PROJECT_ROOT, "output", "articles", "images")
    os.makedirs(images_dir, exist_ok=True)

    url_hash = hashlib.md5(url.encode("utf-8")).hexdigest()

    # 1. 优先复用本地已有缓存（同时校验缓存合法性，排查清理假图片）
    for cached in glob.glob(os.path.join(images_dir, f"{url_hash}.*")):
        if os.path.exists(cached) and os.path.getsize(cached) > 0:
            try:
                with open(cached, "rb") as fp:
                    if is_valid_image_bytes(fp.read(512)):
                        return cached
                    else:
                        os.remove(cached)
            except Exception:
                pass

    # 2. 携带防盗链请求拉取
    if not requests:
        return None

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": "https://mp.weixin.qq.com/",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
    }

    try:
        session = requests.Session()
        session.headers.update(headers)
        session.trust_env = True
        resp = session.get(url, timeout=10)
        if resp.status_code == 200 and resp.content:
            if not is_valid_image_bytes(resp.content):
                logger.debug(f"拉取到的数据不是合法图片格式 (可能遭遇反爬拦截) [{url}]")
                return None

            content_type = resp.headers.get("Content-Type", "").lower()
            ext = ".png"
            if "jpeg" in content_type or "jpg" in content_type:
                ext = ".jpg"
            elif "png" in content_type:
                ext = ".png"
            elif "webp" in content_type:
                ext = ".webp"
            elif "gif" in content_type:
                ext = ".gif"
            elif "svg" in content_type:
                ext = ".svg"
            else:
                parsed = urllib.parse.urlparse(url)
                q = urllib.parse.parse_qs(parsed.query)
                if "wx_fmt" in q:
                    fmt = q["wx_fmt"][0].lower()
                    ext = ".jpg" if fmt in ["jpeg", "jpg"] else f".{fmt}"

            target_file = os.path.join(images_dir, f"{url_hash}{ext}")
            with open(target_file, "wb") as f:
                f.write(resp.content)
            return target_file
    except Exception as e:
        logger.debug(f"在线拉取外部图片失败 [{url}]: {e}")

    return None


def parse_markdown_frontmatter(content: str) -> tuple[Dict[str, Any], str]:
    """解析 Markdown 文件开头的 YAML Frontmatter"""
    meta = {}
    body = content

    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            raw_meta = parts[1].strip()
            body = parts[2].strip()
            # 简单逐行解析常见的 meta 字段
            current_key = None
            for line in raw_meta.split("\n"):
                line = line.strip()
                if not line:
                    continue
                if ":" in line and not line.startswith("-"):
                    k, v = line.split(":", 1)
                    current_key = k.strip()
                    v = v.strip().strip('"').strip("'")
                    meta[current_key] = v
                elif line.startswith("-") and current_key:
                    val = line.lstrip("-").strip().strip('"').strip("'")
                    if not isinstance(meta[current_key], list):
                        meta[current_key] = []
                    meta[current_key].append(val)

    return meta, body


def convert_markdown_to_html(md_file_path: str, output_html_path: Optional[str] = None) -> str:
    """
    读取单篇 Markdown 文件，将其中的本地图片全部替换为 Base64，
    并包裹交互式单图复制按钮，最终输出独立的单文件 HTML。
    """
    if not os.path.exists(md_file_path):
        raise FileNotFoundError(f"Markdown 文件不存在: {md_file_path}")

    with open(md_file_path, "r", encoding="utf-8") as f:
        raw_content = f.read()

    meta, body_md = parse_markdown_frontmatter(raw_content)

    title = meta.get("title", "")
    if not title:
        # 从首行或文件名推导
        base = os.path.splitext(os.path.basename(md_file_path))[0]
        # 去除前缀日期
        title = re.sub(r"^\d{4}-\d{2}-\d{2}_", "", base)

    author = meta.get("author", "")
    date_str = meta.get("date", "")
    tags = meta.get("tags", [])
    if isinstance(tags, str):
        tags = [tags]

    # 将 Markdown 转换为基础 HTML
    if markdown:
        raw_body_html = markdown.markdown(
            body_md,
            extensions=["extra", "nl2br", "tables"]
        )
    else:
        # 若没有 markdown 模块，简单段落降级
        paragraphs = body_md.split("\n\n")
        raw_body_html = "".join(f"<p>{p.strip()}</p>" for p in paragraphs if p.strip())

    # 使用 BeautifulSoup 进行 DOM 结构增强与 Base64 图片转换
    soup = BeautifulSoup(raw_body_html, "html.parser")
    base_dir = os.path.dirname(os.path.abspath(md_file_path))
    images_dir = os.path.join(base_dir, "images")
    if not os.path.exists(images_dir):
        images_dir = os.path.join(PROJECT_ROOT, "output", "articles", "images")

    # 遍历所有 img 标签
    for img in soup.find_all("img"):
        src = img.get("src", "").strip()
        if not src:
            continue

        b64_uri = None

        # 场景 1：已经是 data:image Base64
        if src.startswith("data:image"):
            b64_uri = src
        # 场景 2：外部 HTTP / HTTPS / 协议自适应 // 链接（微信 CDN 或第三方图片）
        elif src.startswith("http://") or src.startswith("https://") or src.startswith("//"):
            local_cached_file = fetch_and_cache_remote_image(src, images_dir=images_dir)
            if local_cached_file and os.path.exists(local_cached_file):
                b64_uri = get_image_base64_uri(local_cached_file)
            elif src.startswith("//"):
                img["src"] = "https:" + src
        # 场景 3：本地文件路径 (相对路径、绝对路径、file://)
        else:
            if src.startswith("file://"):
                parsed = urllib.parse.urlparse(src)
                local_img_path = urllib.request.url2pathname(parsed.path)
            elif os.path.isabs(src):
                local_img_path = os.path.normpath(src)
            else:
                local_img_path = os.path.normpath(os.path.join(base_dir, src))
                if not os.path.exists(local_img_path):
                    alt_candidate = os.path.normpath(os.path.join(images_dir, os.path.basename(src)))
                    if os.path.exists(alt_candidate):
                        local_img_path = alt_candidate

            if os.path.exists(local_img_path):
                b64_uri = get_image_base64_uri(local_img_path)

        if b64_uri:
            img["src"] = b64_uri

        # 外层包裹 .img-box 容器，并注入每张图右上角的“复制图片”按键
        parent = img.parent
        is_already_boxed = parent and "img-box" in parent.get("class", [])
        if not is_already_boxed:
            img_box = soup.new_tag("div", **{"class": "img-box"})
            img.wrap(img_box)

            copy_btn = soup.new_tag("button", **{
                "class": "copy-img-btn",
                "type": "button",
                "onclick": "copySingleImage(this)",
                "title": "点击直接复制该图片，可直接在微信聊天框或文档中 Ctrl+V 粘贴"
            })
            # 复制图标与文字
            copy_btn.append(BeautifulSoup(
                '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">'
                '<rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>'
                '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>'
                '</svg> 复制图片',
                "html.parser"
            ))
            img_box.append(copy_btn)

    # 转义标题、作者等字符串，防止双引号截断属性及 HTML 注入
    safe_title = html.escape(title, quote=True)
    safe_author = html.escape(author, quote=True) if author else ""
    safe_date_str = html.escape(date_str, quote=True) if date_str else ""
    safe_tags = [html.escape(t, quote=True) for t in tags]

    # 组装元信息 HTML
    meta_parts = []
    if safe_author:
        meta_parts.append(f'<span class="meta-author">@{safe_author}</span>')
    if safe_date_str:
        meta_parts.append(f'<span class="meta-date">📅 {safe_date_str}</span>')
    for t in safe_tags:
        if t != safe_author:
            meta_parts.append(f'<span class="meta-tag">#{t}</span>')
    meta_html = "".join(meta_parts)

    body_html = str(soup)

    # 组装完整 HTML
    full_html = HTML_TEMPLATE.format(
        page_title=safe_title,
        article_title=safe_title,
        meta_html=meta_html,
        body_html=body_html
    )

    if output_html_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_html_path)), exist_ok=True)
        with open(output_html_path, "w", encoding="utf-8") as f:
            f.write(full_html)
        logger.info(f"[OK] 已生成单文件 HTML: {os.path.basename(output_html_path)}")

    return full_html


def batch_export_all_articles(articles_dir: Optional[str] = None, output_dir: Optional[str] = None) -> int:
    """批量将指定目录下的所有 Markdown 微信文章转换为单文件 HTML"""
    if not articles_dir:
        articles_dir = os.path.join(PROJECT_ROOT, "output", "articles")
    if not output_dir:
        output_dir = articles_dir  # 默认在同一目录下生成同名 .html，方便用户直接双击查看

    if not os.path.exists(articles_dir):
        logger.error(f"文章目录不存在: {articles_dir}")
        return 0

    md_files = [f for f in os.listdir(articles_dir) if f.endswith(".md")]
    logger.info(f"找到 {len(md_files)} 篇 Markdown 文章，开始批量导出单文件 HTML...")

    count = 0
    for file_name in md_files:
        md_path = os.path.join(articles_dir, file_name)
        html_name = os.path.splitext(file_name)[0] + ".html"
        html_path = os.path.join(output_dir, html_name)

        try:
            convert_markdown_to_html(md_path, html_path)
            count += 1
        except Exception as e:
            logger.error(f"导出失败 [{file_name}]: {e}")

    logger.info(f"[DONE] 批量导出完毕！成功导出 {count} 篇单文件 HTML！")
    return count


if __name__ == "__main__":
    articles_dir = os.path.join(PROJECT_ROOT, "output", "articles")
    batch_export_all_articles(articles_dir)
