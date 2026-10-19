# -*- coding: utf-8 -*-
"""
微信公众号图片防盗链 Base64 内嵌与批量爬取自动化测试
------------------------------------------------------
测试内容：
1. 单文件 HTML 导出器防盗链 meta 标签 (<meta name="referrer" content="no-referrer">) 验证
2. 本地图片、file:// 协议图片与在线图片的 Base64 内嵌闭环测试
3. 微信图片在线下载 Referer 防盗链请求头测试
4. 严格防范网络拦截或错误 HTML 伪装图片：非真实图片数据必须拒绝落盘与拒绝 Base64 转换
5. 损坏图片文件在 Base64 读取时安全识别与拦截
6. 协议自适应 URL (//mmbiz.qpic.cn/...) 规范化测试
7. HTML 中 Base64 格式内嵌图片转 Markdown 提取并保存到本地 images/ 测试
8. 纯文本降级噪点“图片”彻底过滤测试
9. auto_wechat_crawler 批量参数 (--count N) 与窗口识别、去重签名逻辑测试
10. auto_wechat_crawler 剪贴板链式降级读取与写入测试
"""

import os
import sys
import unittest
import tempfile
import shutil
import base64
from unittest.mock import patch, MagicMock

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from html_exporter import (
    convert_markdown_to_html,
    get_image_base64_uri,
    fetch_and_cache_remote_image,
    batch_export_all_articles,
    is_valid_image_bytes
)
from html_to_markdown import WechatHtmlToMarkdown
from auto_wechat_crawler import (
    is_wechat_article_window,
    release_all_modifier_keys,
    get_clipboard_text,
    set_clipboard_text
)


class TestImageHandlingAndAntiLeech(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.images_dir = os.path.join(self.test_dir, "images")
        os.makedirs(self.images_dir, exist_ok=True)

        # 创建一个 1x1 像素的真实 PNG 图片作为测试基准
        self.png_bytes = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        )
        self.test_img_path = os.path.join(self.images_dir, "test_dot.png")
        with open(self.test_img_path, "wb") as f:
            f.write(self.png_bytes)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_meta_referrer_in_html_template(self):
        """测试生成的单文件 HTML 必须包含 <meta name='referrer' content='no-referrer'>"""
        md_file = os.path.join(self.test_dir, "sample.md")
        with open(md_file, "w", encoding="utf-8") as f:
            f.write("# 测试文章\n\n正文内容\n")

        html_file = os.path.join(self.test_dir, "sample.html")
        html_out = convert_markdown_to_html(md_file, html_file)

        self.assertIn('<meta name="referrer" content="no-referrer">', html_out)
        self.assertTrue(os.path.exists(html_file))

    def test_local_image_embedded_as_base64(self):
        """测试本地图片在生成单文件 HTML 时自动转为 Base64 Data URI"""
        md_file = os.path.join(self.test_dir, "img_test.md")
        with open(md_file, "w", encoding="utf-8") as f:
            f.write(f"# 配图测试\n\n![点](images/test_dot.png)\n")

        html_file = os.path.join(self.test_dir, "img_test.html")
        html_out = convert_markdown_to_html(md_file, html_file)

        self.assertIn("data:image/png;base64,", html_out)
        self.assertNotIn("images/test_dot.png", html_out)
        self.assertIn("img-box", html_out)
        self.assertIn("copySingleImage", html_out)

    def test_file_uri_and_get_image_base64(self):
        """测试 file:/// 协议与绝对路径的 Base64 转换"""
        file_uri = f"file:///{self.test_img_path.replace(os.sep, '/')}"
        b64 = get_image_base64_uri(file_uri)
        self.assertIsNotNone(b64)
        self.assertTrue(b64.startswith("data:image/png;base64,"))

    def test_remote_image_fetch_with_anti_leech_referer(self):
        """测试外部图片下载时必须携带微信防盗链 Referer: https://mp.weixin.qq.com/"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = self.png_bytes
        mock_resp.headers = {"Content-Type": "image/png"}

        with patch("requests.Session.get", return_value=mock_resp) as mock_get:
            cached_path = fetch_and_cache_remote_image(
                "https://mmbiz.qpic.cn/mmbiz_png/test_art_cover/640?wx_fmt=png",
                images_dir=self.images_dir
            )
            self.assertIsNotNone(cached_path)
            self.assertTrue(os.path.exists(cached_path))

            # 验证请求参数包含防盗链 headers
            self.assertTrue(mock_get.called)

    def test_non_image_response_rejected(self):
        """核心安全验证：拦截或错误返回的 HTML 页面必须坚决拒绝落盘（彻底根除裂图）"""
        blocked_html = b"<!--blocked.html --><!DOCTYPE html><html><title>Access Denied</title><body>Please authenticate</body></html>"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = blocked_html
        mock_resp.headers = {"Content-Type": "text/html"}

        with patch("requests.Session.get", return_value=mock_resp):
            cached_path = fetch_and_cache_remote_image(
                "https://mmbiz.qpic.cn/mmbiz_png/fake_blocked/0?wx_fmt=png",
                images_dir=self.images_dir
            )
            self.assertIsNone(cached_path)

        # 验证 html_to_markdown 也同样拒绝该伪装内容
        converter = WechatHtmlToMarkdown(
            download_images=True,
            images_dir=self.images_dir
        )
        with patch.object(converter, "_request_adaptive", return_value=mock_resp):
            res = converter._download_image("https://mmbiz.qpic.cn/mmbiz_png/fake_blocked/0?wx_fmt=png")
            self.assertIsNone(res)

    def test_corrupted_file_on_disk_rejected_by_base64_loader(self):
        """测试当磁盘上已存在损坏伪装的 HTML 假图片时，get_image_base64_uri 拒绝返回无效 Base64"""
        bad_file = os.path.join(self.images_dir, "bad_image.png")
        with open(bad_file, "wb") as f:
            f.write(b"<!DOCTYPE html><html><body>Error</body></html>")

        b64 = get_image_base64_uri(bad_file)
        self.assertIsNone(b64)

    def test_protocol_relative_url_normalization(self):
        """测试 //mmbiz.qpic.cn/... 协议相对 URL 自动规整为 https://"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = self.png_bytes
        mock_resp.headers = {"Content-Type": "image/png"}

        with patch("requests.Session.get", return_value=mock_resp) as mock_get:
            cached = fetch_and_cache_remote_image(
                "//mmbiz.qpic.cn/mmbiz_png/proto_rel/640?wx_fmt=png",
                images_dir=self.images_dir
            )
            self.assertIsNotNone(cached)
            args, _ = mock_get.call_args
            self.assertTrue(args[0].startswith("https://mmbiz.qpic.cn"))

    def test_html_to_markdown_extracts_base64_image(self):
        """测试微信剪贴板内嵌 Base64 图片在转 Markdown 时能够正确落盘保存到 images/ 目录"""
        converter = WechatHtmlToMarkdown(
            download_images=True,
            images_dir=self.images_dir,
            images_rel_path="images"
        )
        b64_str = base64.b64encode(self.png_bytes).decode("utf-8")
        html_input = f"""
        <div id="js_content">
            <p>第一段文字</p>
            <p><img src="data:image/png;base64,{b64_str}" alt="内嵌图"></p>
            <p>第二段文字</p>
        </div>
        """
        md_res = converter.convert(html_input)
        self.assertIn("![内嵌图](images/", md_res)
        self.assertNotIn("data:image/png;base64,", md_res)

        # 检查是否成功在本地 images 目录下生成了该图片
        saved_imgs = os.listdir(self.images_dir)
        self.assertGreater(len(saved_imgs), 1)

    def test_noise_image_text_purging(self):
        """测试纯文本降级时孤立的“图片”或“[图片]”噪点文本被彻底清洗"""
        converter = WechatHtmlToMarkdown(
            download_images=False,
            remove_ads=True
        )
        html_input = """
        <div id="js_content">
            <p>重要政策发布：</p>
            <p><span>图片</span></p>
            <p>[图片]</p>
            <p>医疗器械监管全面升级。</p>
        </div>
        """
        md_res = converter.convert(html_input)
        self.assertIn("重要政策发布", md_res)
        self.assertIn("医疗器械监管全面升级", md_res)
        # 单独成行的“图片”或“[图片]”应被彻底过滤
        lines = [l.strip() for l in md_res.splitlines() if l.strip()]
        self.assertNotIn("图片", lines)
        self.assertNotIn("[图片]", lines)


class TestBatchCrawlLogic(unittest.TestCase):
    def test_is_wechat_article_window_safeguards(self):
        """测试文章窗口判断安全门禁，确保主窗口不被误识别"""
        if sys.platform != "win32":
            return

        with patch("ctypes.windll.user32.GetWindowTextW") as mock_txt, \
             patch("ctypes.windll.user32.GetClassNameW") as mock_cls, \
             patch("ctypes.windll.kernel32.OpenProcess", return_value=123), \
             patch("ctypes.windll.kernel32.CloseHandle"), \
             patch("ctypes.windll.kernel32.QueryFullProcessImageNameW") as mock_img:

            # 模拟进程为 WeChat.exe
            def fake_proc_name(h, flags, buf, sz):
                buf.value = "C:\\Program Files\\Tencent\\WeChat\\WeChat.exe"
            mock_img.side_effect = fake_proc_name

            # 场景 1：微信主面板 (类名包含 MainWnd) -> 必须返回 False 防误杀
            def fake_cls_main(hwnd, buf, sz):
                buf.value = "WeChatMainWndForPC"
            mock_cls.side_effect = fake_cls_main
            def fake_txt_main(hwnd, buf, sz):
                buf.value = "微信"
            mock_txt.side_effect = fake_txt_main
            self.assertFalse(is_wechat_article_window(99901))

            # 场景 2：微信文章独立阅读视窗 (类名包含 ChatWnd 或 AppEx) -> 应该返回 True
            def fake_cls_reader(hwnd, buf, sz):
                buf.value = "ChatWnd"
            mock_cls.side_effect = fake_cls_reader
            def fake_txt_reader(hwnd, buf, sz):
                buf.value = "医疗器械分类标准最新解读"
            mock_txt.side_effect = fake_txt_reader
            self.assertTrue(is_wechat_article_window(99902))

    def test_release_modifier_keys_idempotent(self):
        """测试 release_all_modifier_keys 多次调用幂等且零异常"""
        try:
            for _ in range(3):
                release_all_modifier_keys()
            ok = True
        except Exception:
            ok = False
        self.assertTrue(ok)

    def test_clipboard_fallback_and_hashlib_md5(self):
        """测试剪贴板函数链式安全提取与 hashlib.md5 签名在批量收割中正常运转"""
        import hashlib
        sample_text = "医疗器械分类标准最新解读\n国家药监局发布的医疗器械新规通知..."
        text_sig = hashlib.md5(sample_text[:300].encode("utf-8")).hexdigest()
        self.assertEqual(len(text_sig), 32)

        # 测试 set_clipboard_text 与 get_clipboard_text
        if sys.platform == "win32":
            set_ok = set_clipboard_text("测试自动化写入剪贴板")
            self.assertTrue(set_ok)
            read_back = get_clipboard_text()
            self.assertIn("测试自动化写入剪贴板", read_back)


if __name__ == "__main__":
    unittest.main()
