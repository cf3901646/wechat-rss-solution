# -*- coding: utf-8 -*-
"""
微信公众号搜一搜全自动爬虫自动化回归测试
---------------------------------------------
测试覆盖：
1. auto_wechat_crawler 核心函数与逻辑
2. HTML Fragment 富文本带图解析测试（验证真实图片不被退化为纯文本“图片”）
3. 单文件 HTML 导出器图像保留测试
4. 批处理脚本规范性验证（CRLF、pause、无乱码命令）
"""

import os
import sys
import unittest
import tempfile
import shutil

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from search_sniffer import WechatArticleArchiver
from auto_wechat_crawler import find_and_activate_wechat


class TestAutoWechatCrawler(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.archiver = WechatArticleArchiver(
            output_dir=self.test_dir,
            download_images=False
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_find_and_activate_wechat(self):
        """测试微信窗口查找与激活函数"""
        success, hwnd = find_and_activate_wechat()
        # 在 Windows 环境下应返回 bool 和 Optional[int]
        self.assertIsInstance(success, bool)
        if success:
            self.assertIsInstance(hwnd, int)

    def test_archive_from_clipboard_with_rich_images(self):
        """验证富文本剪贴板 HTML 解析能完整保留图片，而不是被文字‘图片’取代"""
        html_fragment = """
        <html><body>
        <!--StartFragment-->
        <div id="img-content">
            <h1 class="rich_media_title">医疗器械分类法规解读</h1>
            <div class="rich_media_meta_text">国家药监局</div>
            <div class="rich_media_content">
                <p>根据医疗器械监督管理条例，医疗器械分为一类、二类、三类。</p>
                <p><img src="https://mmbiz.qpic.cn/test_device_1.png" alt="医疗器械样本图"></p>
                <p>经营三类医疗器械必须取得经营许可证。</p>
                <p><img src="https://mmbiz.qpic.cn/test_device_2.png" alt="许可证示意图"></p>
            </div>
        </div>
        <!--EndFragment-->
        </body></html>
        """
        raw_text = (
            "医疗器械分类法规解读\n"
            "国家药监局\n"
            "根据医疗器械监督管理条例，医疗器械分为一类、二类、三类。\n"
            "经营三类医疗器械必须取得经营许可证。"
        )

        ok = self.archiver.archive_from_clipboard_content(
            raw_text=raw_text,
            html_content=html_fragment,
            source_url="https://mp.weixin.qq.com/s/test_auto_crawler_sample"
        )
        self.assertTrue(ok)

        # 验证生成的 Markdown 文件
        md_files = [f for f in os.listdir(self.test_dir) if f.endswith(".md")]
        self.assertEqual(len(md_files), 1)
        md_path = os.path.join(self.test_dir, md_files[0])
        with open(md_path, "r", encoding="utf-8") as f:
            md_text = f.read()

        # 关键断言：必须包含真实图片链接，不能退化为纯文本孤立字“图片”
        self.assertIn("https://mmbiz.qpic.cn/test_device_1.png", md_text)
        self.assertIn("https://mmbiz.qpic.cn/test_device_2.png", md_text)
        self.assertIn("医疗器械样本图", md_text)
        self.assertIn("许可证示意图", md_text)

        # 验证生成的单文件 HTML
        html_files = [f for f in os.listdir(self.test_dir) if f.endswith(".html")]
        self.assertEqual(len(html_files), 1)
        html_path = os.path.join(self.test_dir, html_files[0])
        with open(html_path, "r", encoding="utf-8") as f:
            html_text = f.read()

        self.assertIn("<img", html_text)
        self.assertIn("test_device_1.png", html_text)

    def test_bat_script_format_and_rules(self):
        """严格校验批处理脚本规则：CRLF 换行、pause 结尾、无乱码命令"""
        target_bats = [
            os.path.join(PROJECT_ROOT, "全自动搜索并爬取.bat"),
            os.path.join(PROJECT_ROOT, "windows", "全自动搜索并爬取.bat")
        ]
        for bat in target_bats:
            if not os.path.exists(bat):
                continue
            with open(bat, "rb") as f:
                content = f.read()

            # 1. 验证 CRLF
            self.assertIn(b"\r\n", content, f"{bat} 必须包含 CRLF 换行")
            self.assertNotIn(b"\r\r\n", content, f"{bat} 不得有畸形重复回车")

            # 2. 验证末尾包含 pause
            self.assertTrue(b"pause" in content.lower(), f"{bat} 末尾必须包含 pause 防闪退")

            # 3. 验证无多字节 UTF-8 中文污染命令行
            # 允许纯 ASCII 命令
            lines = content.split(b"\r\n")
            for line in lines:
                if line.strip().startswith(b"rem") or line.strip().startswith(b"::"):
                    continue
                # 命令本身不应含有非 ASCII 字符（除了中文注释）
                for b in line:
                    self.assertLess(b, 128, f"{bat} 的命令行严禁包含多字节字符: {line}")

    def test_release_all_modifier_keys_execution(self):
        """测试 release_all_modifier_keys 调用安全无异常"""
        from auto_wechat_crawler import release_all_modifier_keys
        # 调用应安全返回，无任何未捕获异常
        try:
            release_all_modifier_keys()
            executed = True
        except Exception:
            executed = False
        self.assertTrue(executed)

    def test_reset_keyboard_bat_compliance(self):
        """测试 reset_keyboard.bat 规范性（CRLF、包含 pause、无多字节字符）"""
        bat_path = os.path.join(PROJECT_ROOT, "windows", "reset_keyboard.bat")
        self.assertTrue(os.path.exists(bat_path), "windows/reset_keyboard.bat 必须存在")
        with open(bat_path, "rb") as f:
            content = f.read()

        self.assertIn(b"\r\n", content, "reset_keyboard.bat 必须使用 CRLF 换行")
        self.assertTrue(b"pause" in content.lower(), "reset_keyboard.bat 必须以 pause 结尾")
        for b in content:
            self.assertLess(b, 128, "reset_keyboard.bat 严禁包含多字节 UTF-8 中文路径与命令")


if __name__ == "__main__":
    unittest.main()

