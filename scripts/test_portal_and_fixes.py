# -*- coding: utf-8 -*-
"""
微信公众号知识库系统与门户 10 项 Bug 修复综合自动化测试套件
-----------------------------------------------------------
全面覆盖 P0/P1/P2 级 10 项 Bug 修复点：
1. 微信扫码授权闭环与凭证持久化
2. 静态资源路径穿越漏洞防御
3. 微信文章 13 位时间戳与旧文章冒充今日发布拦截
4. 作者筛选标签防蒸发死锁
5. 端口占用探测与顺延递增机制
6. 阅读器剪贴板父页面上下文直接读取 DOM 写入
7. HTML 导出器引号截断与 HTML 注入防御
8. Markdown 导出 source_url 键名断层修复
9. sync_history.json 跨机与目录迁移去重兼容
10. 自适应代理与 Socket 服务端优雅关闭
"""

import os
import sys
import json
import time
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import requests

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
sys.path.insert(0, CURRENT_DIR)
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "server"))

from web_portal import (
    is_safe_path,
    ARTICLES_DIR,
    PORTAL_STATIC_DIR,
    PortalRequestHandler,
    check_existing_instance
)
from sync_weread_direct import WereadDirectSyncEngine
from html_exporter import convert_markdown_to_html
from html_to_markdown import WechatHtmlToMarkdown
from run_weread_login import save_credentials


class TestBugFixesComprehensive(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.output_dir = os.path.join(self.test_dir, "output", "articles")
        os.makedirs(self.output_dir, exist_ok=True)
        self.history_file = os.path.join(self.test_dir, "output", "sync_history.json")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # -------------------------------------------------------------
    # 1. 【P0】测试微信扫码授权闭环与凭证持久化落盘
    # -------------------------------------------------------------
    def test_fix_01_save_credentials_persistence(self):
        """测试扫码授权凭据落盘，确保 weread_credentials.json 和 wx.lic 均正常生成，且重新申请二维码时重置旧登录态"""
        mock_data = {
            "vid": "12345678",
            "cookies": {
                "wr_vid": "12345678",
                "wr_skey": "test_skey",
                "wr_rt": "test_rt"
            }
        }
        save_credentials(self.test_dir, mock_data)

        cred_path = os.path.join(self.test_dir, "data", "weread_credentials.json")
        lic_path = os.path.join(self.test_dir, "data", "wx.lic")

        self.assertTrue(os.path.exists(cred_path), "weread_credentials.json 应该已生成")
        self.assertTrue(os.path.exists(lic_path), "wx.lic 应该已生成")

        with open(cred_path, "r", encoding="utf-8") as f:
            saved_cred = json.load(f)
            self.assertEqual(saved_cred["vid"], "12345678")
            self.assertEqual(saved_cred["cookies"]["wr_skey"], "test_skey")

        with open(lic_path, "r", encoding="utf-8") as f:
            saved_lic = json.load(f)
            self.assertEqual(saved_lic["weread_data"]["vid"], "12345678")

        # 验证 _handle_get_qrcode 调用 login_mgr._reset() 清理陈旧状态
        from driver.weread_qr import WereadQRLogin
        login_mgr = WereadQRLogin()
        login_mgr._logged_in = True
        login_mgr._login_result = {"vid": "old_vid"}
        login_mgr._login_error = "old_error"

        handler = MagicMock()
        sent_payload = []
        handler._send_json = lambda d, **kwargs: sent_payload.append(d)

        with patch.object(login_mgr, "_get_login_uid", return_value="NEW_UID_999"):
            with patch.object(login_mgr, "_start_polling") as mock_poll:
                PortalRequestHandler._handle_get_qrcode(handler)
                self.assertFalse(login_mgr._logged_in, "重新获取二维码必须重置 _logged_in 为 False")
                self.assertEqual(login_mgr._login_result, {}, "重新获取二维码必须清空 _login_result")
                self.assertEqual(login_mgr._login_error, "", "重新获取二维码必须清空 _login_error")
                mock_poll.assert_called_once()

    # -------------------------------------------------------------
    # 2. 【P0】测试静态文件服务的目录穿越漏洞防御
    # -------------------------------------------------------------
    def test_fix_02_path_traversal_prevention(self):
        """测试 is_safe_path 对 ../ 穿越及敏感路径访问的严格拦截"""
        base_dir = self.output_dir

        # 正常合法子路径
        safe_child = os.path.join(base_dir, "article.html")
        safe_subchild = os.path.join(base_dir, "images", "pic.png")
        self.assertTrue(is_safe_path(base_dir, safe_child))
        self.assertTrue(is_safe_path(base_dir, safe_subchild))

        # 目录穿越攻击尝试
        malicious_traversal1 = os.path.join(base_dir, "..", "sync_history.json")
        malicious_traversal2 = os.path.join(base_dir, "..", "..", "config", "config.yaml")
        malicious_traversal3 = os.path.join(base_dir, "..", "..", "server", "data", "wx.lic")
        self.assertFalse(is_safe_path(base_dir, malicious_traversal1))
        self.assertFalse(is_safe_path(base_dir, malicious_traversal2))
        self.assertFalse(is_safe_path(base_dir, malicious_traversal3))

        # 尝试访问系统敏感根目录
        system_path = "C:\\Windows\\System32\\calc.exe" if sys.platform.startswith("win") else "/etc/passwd"
        self.assertFalse(is_safe_path(base_dir, system_path))

    # -------------------------------------------------------------
    # 3. 【P0】测试微信文章 13 位毫秒时间戳转换与旧文章冒充今日发布拦截
    # -------------------------------------------------------------
    def test_fix_03_timestamp_and_cover_fallback_restriction(self):
        """测试 13 位时间戳安全转换，以及 days_limit > 0 时严禁调用 cover 兜底"""
        engine = WereadDirectSyncEngine()

        # 1. 测试 13 位时间戳与字符串解析
        # 1712345678000 (ms) => 1712345678 (s)
        ms_ts_int = 1712345678000
        ms_ts_str = "1712345678000"

        # 模拟 reviews 包含 13 位毫秒时间戳与正常秒级时间戳
        mock_reviews = [
            {
                "subReviews": [
                    {
                        "review": {
                            "reviewId": "MP_WXS_101",
                            "createTime": ms_ts_int,
                            "mpInfo": {"title": "文章101", "originalId": "orig101"}
                        }
                    },
                    {
                        "review": {
                            "reviewId": "MP_WXS_102",
                            "createTime": ms_ts_str,
                            "mpInfo": {"title": "文章102", "originalId": "orig102"}
                        }
                    }
                ]
            }
        ]

        with patch.object(engine, "_request_adaptive") as mock_req:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"reviews": mock_reviews}
            mock_req.return_value = mock_resp

            # 全量模式获取
            articles = engine.fetch_mp_articles("MP_WXS_TEST", {}, days_limit=0)
            self.assertEqual(len(articles), 2)
            self.assertEqual(articles[0]["timestamp"], 1712345678)
            self.assertEqual(articles[1]["timestamp"], 1712345678)
            # 格式化日期不会抛出 Errno 22
            self.assertTrue(articles[0]["date"].startswith("2024-"))

        # 2. 测试在 days_limit > 0 且最新文章早于筛选范围时，严禁入库
        with patch.object(engine, "_request_adaptive") as mock_req:
            def side_effect_filter(method, url, **kwargs):
                resp = MagicMock()
                resp.status_code = 200
                if "articles" in url:
                    resp.json.return_value = {"reviews": []}
                elif "cover" in url:
                    resp.json.return_value = {
                        "reviewId": "MP_WXS_COVER_OLD",
                        "title": "远古旧文章",
                        "createTime": 1500000000000  # 远古时间戳
                    }
                elif "info" in url:
                    resp.json.return_value = {"updateTime": 1500000000}
                return resp

            mock_req.side_effect = side_effect_filter
            # days_limit=7 时，远古文章应被过滤
            articles_filtered = engine.fetch_mp_articles("MP_WXS_TEST", {}, days_limit=7)
            self.assertEqual(len(articles_filtered), 0, "早于 days_limit 的远古文章严禁入库")

        # 3. 测试全量模式 (days_limit == 0) 下，无文章时仍允许调用 /api/mp/cover 兜底
        with patch.object(engine, "_request_adaptive") as mock_req:
            def side_effect(method, url, **kwargs):
                resp = MagicMock()
                resp.status_code = 200
                if "articles" in url:
                    resp.json.return_value = {"reviews": []}
                elif "cover" in url:
                    resp.json.return_value = {
                        "reviewId": "MP_WXS_COVER_1",
                        "title": "兜底封面文章",
                        "createTime": 1712345678000
                    }
                return resp

            mock_req.side_effect = side_effect
            articles_fallback = engine.fetch_mp_articles("MP_WXS_TEST", {}, days_limit=0)
            self.assertEqual(len(articles_fallback), 1)
            self.assertEqual(articles_fallback[0]["title"], "兜底封面文章")

    # -------------------------------------------------------------
    # -------------------------------------------------------------
    # 4. 【P1】测试作者筛选标签防永久蒸发死锁与真实历史元数据映射
    # -------------------------------------------------------------
    def test_fix_04_author_filter_prevents_evaporation_deadlock(self):
        """测试按特定作者筛选时，接口依然返回全局完整的 authors 列表，且支持真实 .md 路径与冲突后缀精确映射"""
        articles_dir = os.path.join(self.test_dir, "output", "articles")
        os.makedirs(articles_dir, exist_ok=True)

        for filename in ["2026-09-21_文章A.html", "2026-09-22_文章B_8d98cd5b.html", "2026-09-23_文章C.html"]:
            with open(os.path.join(articles_dir, filename), "w", encoding="utf-8") as f:
                f.write("<html><body>正文</body></html>")

        # 构造真实生产历史元数据 (saved_path 为真实落盘的 .md，且文章B具备哈希消歧后缀)
        history_path = os.path.join(self.test_dir, "output", "sync_history.json")
        os.makedirs(os.path.dirname(history_path), exist_ok=True)
        history_data = {
            "synced_items": {
                "id1": {"saved_path": os.path.join(articles_dir, "2026-09-21_文章A.md"), "title": "文章A", "author": "晚点LatePost"},
                "id2": {"saved_path": os.path.join(articles_dir, "2026-09-22_文章B_8d98cd5b.md"), "title": "文章B", "author": "机器之心"},
                "id3": {"saved_path": os.path.join(articles_dir, "2026-09-23_文章C.md"), "title": "文章C", "author": "量子位"}
            }
        }
        with open(history_path, "w", encoding="utf-8") as f:
            json.dump(history_data, f, ensure_ascii=False)

        # 模拟 PortalRequestHandler 的 _handle_get_articles 行为
        handler = MagicMock()
        sent_payload = []
        handler._send_json = lambda d, **kwargs: sent_payload.append(d)

        with patch("web_portal.PROJECT_ROOT", self.test_dir):
            with patch("glob.glob", return_value=[
                os.path.join(articles_dir, "2026-09-21_文章A.html"),
                os.path.join(articles_dir, "2026-09-22_文章B_8d98cd5b.html"),
                os.path.join(articles_dir, "2026-09-23_文章C.html"),
            ]):
                # 传入 author="机器之心" 进行筛选
                PortalRequestHandler._handle_get_articles(handler, {"author": ["机器之心"]})

        self.assertEqual(len(sent_payload), 1)
        res = sent_payload[0]
        # 筛选出的文章仅为机器之心，且正确反查出真实公众号而非降级为微信公众号
        self.assertEqual(len(res["articles"]), 1)
        self.assertEqual(res["articles"][0]["author"], "机器之心")

        # 关键验证：无论当前筛选哪个作者，返回的 authors 集合必须包含全部 3 个公众号
        self.assertIn("晚点LatePost", res["authors"])
        self.assertIn("机器之心", res["authors"])
        self.assertIn("量子位", res["authors"])
        self.assertEqual(len(res["authors"]), 3)

        # 验证若 articles 目录不存在时，返回空列表且包含规范的 authors: []
        empty_payload = []
        handler._send_json = lambda d, **kwargs: empty_payload.append(d)
        with patch("web_portal.PROJECT_ROOT", os.path.join(self.test_dir, "empty_root")):
            PortalRequestHandler._handle_get_articles(handler, {})
        self.assertEqual(len(empty_payload), 1)
        self.assertEqual(empty_payload[0]["authors"], [])
        self.assertEqual(empty_payload[0]["articles"], [])

    # -------------------------------------------------------------
    # 5. 【P1】测试端口占用防并发与多实例探测
    # -------------------------------------------------------------
    def test_fix_05_check_existing_instance_detection(self):
        """测试 check_existing_instance 能否正确识别本系统正在运行的实例，且探活时快速响应免发网络请求"""
        with patch("urllib.request.urlopen") as mock_urlopen:
            # 模拟现有知识库实例响应
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.read.return_value = json.dumps({
                "is_logged_in": True,
                "total_articles": 15
            }).encode("utf-8")
            mock_urlopen.return_value.__enter__.return_value = mock_resp

            self.assertTrue(check_existing_instance(8002))

        with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
            self.assertFalse(check_existing_instance(8002))

        # 验证 PortalHealthCheck 请求头会跳过外部书架网络调用，保证本地探活毫秒级响应
        handler = MagicMock()
        handler.headers = {"User-Agent": "PortalHealthCheck"}
        sent_status = []
        handler._send_json = lambda d, **kwargs: sent_status.append(d)
        with patch("sync_weread_direct.WereadDirectSyncEngine.load_weread_credentials", return_value=({"wr_vid": "1"}, "1")):
            with patch("sync_weread_direct.WereadDirectSyncEngine.fetch_shelf_mps") as mock_shelf:
                PortalRequestHandler._handle_get_status(handler)
                mock_shelf.assert_not_called()

    # -------------------------------------------------------------
    # 6. 【P1】测试 iframe 剪贴板复制逻辑（前端代码静态结构与无焦点丢失）
    # -------------------------------------------------------------
    def test_fix_06_iframe_clipboard_parent_context(self):
        """验证 index.html 中已彻底移除 iframe 内部有损调用，完全转为父页面上下文安全复制"""
        index_html_path = os.path.join(PROJECT_ROOT, "scripts", "portal_static", "index.html")
        with open(index_html_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 确保不再调用 iframe.contentWindow.copyPureText() 和 copyFullRichText()
        self.assertNotIn("iframe.contentWindow.copyPureText()", content)
        self.assertNotIn("iframe.contentWindow.copyFullRichText()", content)

        # 确保包含父页面安全读取 iframe.contentDocument 的逻辑
        self.assertIn("iframe.contentDocument", content)
        self.assertIn("navigator.clipboard.writeText", content)
        self.assertIn("new ClipboardItem", content)

        # 确保包含未选择文章与空内容的保护提示
        self.assertIn("请先在左侧选择一篇文章", content)
        self.assertIn("文章内容为空或正在加载中", content)

        # 确保包含扫码容错限制与关闭停止轮询
        self.assertIn("stopQrPolling", content)
        self.assertIn("qrPollFailures >= 5", content)

    # -------------------------------------------------------------
    # 7. 【P1】测试 HTML 导出器引号截断与 HTML 注入防御
    # -------------------------------------------------------------
    def test_fix_07_html_exporter_escaping(self):
        """测试包含双引号与 HTML 标签的标题在导出单文件 HTML 时得到安全转义"""
        md_file = os.path.join(self.test_dir, "test_quote.md")
        out_html = os.path.join(self.test_dir, "test_quote.html")

        md_content = """---
title: '深度解析："AI大模型"与<script>alert(1)</script>'
author: '微信团队"CEO"'
date: "2026-09-28"
tags:
  - "大模型\""
---

正文内容：测试引号安全。
"""
        with open(md_file, "w", encoding="utf-8") as f:
            f.write(md_content)

        html_result = convert_markdown_to_html(md_file, out_html)

        # 验证 title 属性中的双引号被转义为 &quot;，杜绝截断属性
        self.assertIn('&quot;AI大模型&quot;', html_result)
        self.assertNotIn('title="深度解析："AI大模型"', html_result)

        # 验证 <script> 被转义为 &lt;script&gt;，杜绝 XSS 注入
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', html_result)
        self.assertNotIn('<script>alert(1)</script>', html_result)

        # 验证 author 转义
        self.assertIn('微信团队&quot;CEO&quot;', html_result)

    # -------------------------------------------------------------
    # 8. 【P1】测试 Markdown 导出 source_url 与 url 键名兼容
    # -------------------------------------------------------------
    def test_fix_08_markdown_source_url_key_compat(self):
        """测试 WechatHtmlToMarkdown 能同时兼容 metadata['source_url'] 与 metadata['url']"""
        converter = WechatHtmlToMarkdown(download_images=False, remove_ads=False)
        html_text = "<p>正文内容测试</p>"

        # 1. 仅传入 source_url
        meta1 = {
            "title": "测试文章1",
            "author": "测试号",
            "source_url": "https://mp.weixin.qq.com/s/sample_link_1"
        }
        md1 = converter.convert(html_text, metadata=meta1)
        self.assertIn('source_url: "https://mp.weixin.qq.com/s/sample_link_1"', md1)

        # 2. 仅传入 url
        meta2 = {
            "title": "测试文章2",
            "author": "测试号",
            "url": "https://mp.weixin.qq.com/s/sample_link_2"
        }
        md2 = converter.convert(html_text, metadata=meta2)
        self.assertIn('source_url: "https://mp.weixin.qq.com/s/sample_link_2"', md2)

    # -------------------------------------------------------------
    # 9. 【P2】测试 sync_history 跨机与目录迁移去重兼容
    # -------------------------------------------------------------
    def test_fix_09_cross_machine_history_deduplication(self):
        """测试当 history 中保存的是旧机器路径时，基于当前 output_dir 寻找同名文件避免重复抓取"""
        engine = WereadDirectSyncEngine()
        engine.output_dir = self.output_dir

        # 在当前 output_dir 生成一个已有文章文件 (超过 500 字节)
        filename = "2026-09-28_历史文章.md"
        html_filename = "2026-09-28_历史文章.html"
        current_file_path = os.path.join(self.output_dir, filename)
        current_html_path = os.path.join(self.output_dir, html_filename)

        with open(current_file_path, "w", encoding="utf-8") as f:
            f.write("# 历史文章\n" + "正文内容测试段落。" * 40)
        with open(current_html_path, "w", encoding="utf-8") as f:
            f.write("<html><body>正文内容测试段落。</body></html>")

        # 模拟从另一台机器复制过来的 history.json，路径为 D:\old_machine\...
        foreign_path = "D:\\old_machine\\workspace\\output\\articles\\2026-09-28_历史文章.md"
        engine.history = {
            "synced_items": {
                "MP_WXS_EXISTING": {
                    "title": "历史文章",
                    "saved_path": foreign_path,
                    "date": "2026-09-28"
                }
            }
        }

        # 1. 验证 sync_article 会检测到本地已存在，返回 False 跳过重复抓取并自愈历史记录
        article_info = {
            "title": "历史文章",
            "link": "https://mp.weixin.qq.com/s/test",
            "guid": "MP_WXS_EXISTING",
            "date": "2026-09-28"
        }
        is_synced = engine.sync_article(article_info, author="测试号", headers={}, dry_run=False)
        self.assertFalse(is_synced, "跨机迁移后相同文件名且已存在完整正文的文章应该跳过抓取")
        self.assertEqual(engine.history["synced_items"]["MP_WXS_EXISTING"]["saved_path"], current_file_path, "应自愈更新为本地真实路径")

        # 2. 验证 _resolve_target_path 正确返回本地兼容路径，不会添加无用 hash 产生冗余副本
        res_filename, res_path = engine._resolve_target_path("2026-09-28", "历史文章", "MP_WXS_EXISTING")
        self.assertEqual(res_filename, filename)
        self.assertEqual(os.path.normpath(res_path), os.path.normpath(current_file_path))

        # 3. 验证 rss_sync.RssSyncEngine 也同样具备跨机 basename 路径兼容
        from rss_sync import RssSyncEngine
        with patch.object(RssSyncEngine, "_load_config", return_value={"storage": {"output_dir": self.output_dir}}):
            rss_engine = RssSyncEngine("dummy.yaml")
            rss_engine.history = {
                "synced_items": {
                    "RSS_GUID_1": {
                        "saved_path": "E:\\another_machine\\articles\\2026-09-28_历史文章.md"
                    }
                }
            }
            r_fname, r_path = rss_engine._resolve_target_path("2026-09-28", "历史文章", "RSS_GUID_1")
            self.assertEqual(r_fname, filename)
            self.assertEqual(os.path.normpath(r_path), os.path.normpath(current_file_path))

    # -------------------------------------------------------------
    # 10. 【P2】测试自适应代理与 Socket 服务端优雅关闭
    # -------------------------------------------------------------
    def test_fix_10_adaptive_proxy_and_server_close(self):
        """测试 WechatHtmlToMarkdown 自适应代理请求机制及 web_portal 服务端优雅释放端口"""
        converter = WechatHtmlToMarkdown(download_images=True, images_dir=self.test_dir)
        self.assertTrue(converter.session.trust_env, "默认应优先复用系统环境变量代理")
        self.assertTrue(hasattr(converter, "_request_adaptive"), "应具备 _request_adaptive 自适应方法")

        # 模拟代理异常时自动降级直连重试
        with patch.object(converter.session, "request", side_effect=requests.exceptions.ProxyError("Proxy error")):
            with patch("requests.Session.request") as mock_fallback_req:
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.content = b"fake_png_data"
                mock_fallback_req.return_value = mock_resp

                res = converter._request_adaptive("GET", "https://example.com/test.png")
                self.assertEqual(res.status_code, 200)
                mock_fallback_req.assert_called_once()

        # 验证 web_portal 源码中包含 httpd.server_close() 显式释放端口句柄
        portal_code_path = os.path.join(CURRENT_DIR, "web_portal.py")
        with open(portal_code_path, "r", encoding="utf-8") as f:
            portal_src = f.read()
        self.assertIn("httpd.server_close()", portal_src, "run_portal_server 应显式调用 httpd.server_close()")

    # -------------------------------------------------------------
    # 11. 测试复制纯文字时彻底滤除“复制图片”控制按钮
    # -------------------------------------------------------------
    def test_fix_11_copy_pure_text_filter_buttons(self):
        """测试单文件 HTML 与门户管理台复制纯文本时，均彻底剔除 .copy-img-btn 等控制按钮"""
        # 1. 验证 HTML 模板与生成物中的 copyPureText 和 CSS 样式
        from html_exporter import HTML_TEMPLATE
        self.assertIn(".copy-img-btn, button, script, style", HTML_TEMPLATE)
        self.assertIn("user-select: none;", HTML_TEMPLATE)
        self.assertIn("-webkit-user-select: none;", HTML_TEMPLATE)

        # 2. 验证 portal_static/index.html 中的 copyReaderText
        index_html_path = os.path.join(PROJECT_ROOT, "scripts", "portal_static", "index.html")
        with open(index_html_path, "r", encoding="utf-8") as f:
            portal_src = f.read()
        self.assertIn("clone.querySelectorAll('.copy-img-btn, button, script, style')", portal_src)

    # -------------------------------------------------------------
    # 12. 测试 UI 紧凑化布局与 Docker 容器化打包配置
    # -------------------------------------------------------------
    def test_fix_12_ui_compact_and_docker_packaging(self):
        """验证 UI 视口最大化折叠组件、样式以及 Docker 全套配置与脚本的完整性"""
        # 1. 验证 portal_static/index.html 的紧凑与折叠布局特性
        index_html_path = os.path.join(PROJECT_ROOT, "scripts", "portal_static", "index.html")
        with open(index_html_path, "r", encoding="utf-8") as f:
            portal_src = f.read()

        self.assertIn("toggleSyncPanel", portal_src, "应包含抓取面板折叠函数")
        self.assertIn("toggleSidebar", portal_src, "应包含侧栏折叠/全宽模式函数")
        self.assertIn(".sync-panel.collapsed", portal_src, "CSS 应具备 sync-panel 折叠样式")
        self.assertIn(".left-sidebar.collapsed", portal_src, "CSS 应具备 left-sidebar 折叠样式")
        self.assertIn("mini-sync-indicator", portal_src, "应包含折叠时的微型进度条指示器")
        self.assertIn("btn-toggle-sync", portal_src, "导航栏应包含抓取管理开关按键")
        self.assertIn("btn-toggle-zen", portal_src, "导航栏应包含全宽/专注阅读模式开关按键")
        self.assertIn("btn-reader-toggle-sidebar", portal_src, "阅读器顶部应包含侧栏切换开关")

        # 2. 验证 html_exporter.py 内部单文件 HTML 紧凑样式
        from html_exporter import HTML_TEMPLATE
        self.assertIn("max-width: 820px;", HTML_TEMPLATE)
        self.assertIn("padding: 22px 30px;", HTML_TEMPLATE)

        # 3. 验证 web_portal.py 动态 host 绑定与环境变量支持
        portal_code_path = os.path.join(CURRENT_DIR, "web_portal.py")
        with open(portal_code_path, "r", encoding="utf-8") as f:
            portal_py = f.read()
        self.assertIn("target_host = args.host", portal_py)
        self.assertIn("ThreadingHTTPServer((host, candidate_port)", portal_py)

        # 4. 验证 Dockerfile 生产级配置
        dockerfile_path = os.path.join(PROJECT_ROOT, "Dockerfile")
        self.assertTrue(os.path.exists(dockerfile_path), "根目录必须存在 Dockerfile")
        with open(dockerfile_path, "r", encoding="utf-8") as f:
            df_content = f.read()
        self.assertIn("python:3.11-slim", df_content)
        self.assertIn("fonts-wqy-zenhei", df_content, "必须内置中文字体")
        self.assertIn("EXPOSE 8002", df_content)
        self.assertIn("HEALTHCHECK", df_content)
        self.assertIn("entrypoint.sh", df_content)

        # 5. 验证 docker-compose.yml
        compose_path = os.path.join(PROJECT_ROOT, "docker-compose.yml")
        self.assertTrue(os.path.exists(compose_path), "根目录必须存在 docker-compose.yml")
        with open(compose_path, "r", encoding="utf-8") as f:
            dc_content = f.read()
        self.assertIn("8002:8002", dc_content)
        self.assertIn("./data:/app/data", dc_content)
        self.assertIn("./output:/app/output", dc_content)
        self.assertIn("./config:/app/config", dc_content)

        # 6. 验证启动与交付脚本的存在性
        self.assertTrue(os.path.exists(os.path.join(PROJECT_ROOT, "docker_start.bat")))
        self.assertTrue(os.path.exists(os.path.join(PROJECT_ROOT, "docker_stop.bat")))
        self.assertTrue(os.path.exists(os.path.join(PROJECT_ROOT, "windows", "docker_start.bat")))
        self.assertTrue(os.path.exists(os.path.join(PROJECT_ROOT, "windows", "docker_stop.bat")))
        self.assertTrue(os.path.exists(os.path.join(PROJECT_ROOT, "docker_start.sh")))
        self.assertTrue(os.path.exists(os.path.join(PROJECT_ROOT, "docker_stop.sh")))
        self.assertTrue(os.path.exists(os.path.join(PROJECT_ROOT, "DOCKER_GUIDE.md")))


if __name__ == "__main__":
    unittest.main()
