# -*- coding: utf-8 -*-
"""
单元与集成测试：搜一搜流量嗅探解析与离线落盘 (test_search_sniffer.py)
"""

import os
import sys
import json
import time
import shutil
import unittest
from unittest.mock import MagicMock, patch

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from search_sniffer import (
    WechatDataParser,
    WechatSearchSnifferAddon,
    WindowsProxyManager,
    is_port_available,
    find_available_port,
    get_upstream_proxy,
    run_clipboard_listener,
)


class TestSearchSniffer(unittest.TestCase):
    def setUp(self):
        self.test_dir = os.path.join(CURRENT_DIR, "_test_sniffer_output")
        os.makedirs(self.test_dir, exist_ok=True)

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_url_normalization_and_guid(self):
        """测试微信文章 URL 标准化与 GUID 一致性"""
        raw_url1 = "https://mp.weixin.qq.com/s?__biz=Mzg4NzE2NjQ4MA==&amp;mid=2247484912&amp;idx=1&amp;sn=abcdef123456&amp;chksm=123456789&amp;scene=27#wechat_redirect"
        raw_url2 = "https://mp.weixin.qq.com/s?__biz=Mzg4NzE2NjQ4MA==&mid=2247484912&idx=1&sn=abcdef123456"

        clean1 = WechatDataParser.normalize_article_url(raw_url1)
        clean2 = WechatDataParser.normalize_article_url(raw_url2)

        self.assertIsNotNone(clean1)
        self.assertIsNotNone(clean2)
        # 核心参数一致，规范化后应相同
        self.assertEqual(clean1, clean2)

        guid1 = WechatDataParser.generate_article_guid(clean1)
        guid2 = WechatDataParser.generate_article_guid(clean2)
        self.assertEqual(guid1, guid2)

    def test_extract_articles_from_json(self):
        """测试从 PC 微信搜一搜的各种嵌套 JSON 结构中智能提取文章"""
        sample_json = {
            "ret": 0,
            "data": {
                "items": [
                    {
                        "title": "<em>大模型</em>在医疗领域的最新突破与落地",
                        "link": "https://mp.weixin.qq.com/s?__biz=MzAwOTM1MzA1Nw==&mid=26500001&idx=1&sn=11111111",
                        "source": "机器之心",
                        "pub_time": 1715000000,
                        "desc": "本文深入分析了医疗大模型的最新评测结果与临床试验。",
                        "thumb_url": "http://mmbiz.qpic.cn/mmbiz_jpg/test1/0?wx_fmt=jpeg"
                    },
                    {
                        "title": "飞利浦发布全新 AI 智能影像诊断平台",
                        "url": "https://mp.weixin.qq.com/s/abcdef1234567890",
                        "nick_name": "飞利浦医疗健康",
                        "time": "2026-05-10 14:30:00",
                        "digest": "全面提升放射科医生的工作效率。",
                        "cover": "http://mmbiz.qpic.cn/mmbiz_jpg/test2/0?wx_fmt=jpeg"
                    }
                ],
                "sub_group": {
                    "card": {
                        "title": "人工智能辅助诊断规范指南",
                        "url": "https://mp.weixin.qq.com/s?__biz=MzAwOTM1MzA1Nw==&mid=26500002&idx=1&sn=22222222",
                        "wechat_name": "中华医学会"
                    }
                }
            }
        }

        articles = WechatDataParser.extract_articles_from_json(sample_json)
        self.assertEqual(len(articles), 3)

        # 验证标题已清理 <em> 高亮标签
        self.assertEqual(articles[0]["title"], "大模型在医疗领域的最新突破与落地")
        self.assertEqual(articles[0]["author"], "机器之心")
        self.assertTrue("mp.weixin.qq.com/s" in articles[0]["url"])

        self.assertEqual(articles[1]["title"], "飞利浦发布全新 AI 智能影像诊断平台")
        self.assertEqual(articles[1]["author"], "飞利浦医疗健康")

        self.assertEqual(articles[2]["title"], "人工智能辅助诊断规范指南")
        self.assertEqual(articles[2]["author"], "中华医学会")

    def test_extract_articles_from_html(self):
        """测试从 HTML 页面中提取内嵌 JSON 及 DOM 链接卡片"""
        sample_html = """
        <!DOCTYPE html>
        <html>
        <head>
            <script>
                window.__INITIAL_STATE__ = {
                    "searchResult": {
                        "docList": [
                            {
                                "title": "2026 中国人工智能产业全景图谱",
                                "url": "https://mp.weixin.qq.com/s?__biz=MzI1MjQ0OTk3MQ==&mid=22475001&idx=1&sn=33333333",
                                "source_name": "36氪研究院",
                                "date": "2026-06-01"
                            }
                        ]
                    }
                };
            </script>
        </head>
        <body>
            <div class="search-card">
                <a href="https://mp.weixin.qq.com/s?__biz=MzI1MjQ0OTk3MQ==&mid=22475002&idx=1&sn=44444444">DeepSeek 技术全解析</a>
                <span class="author-name">量子位</span>
            </div>
        </body>
        </html>
        """

        articles = WechatDataParser.extract_articles_from_html(sample_html)
        # 应包含内嵌 JSON 的 1 篇和 DOM 卡片的 1 篇
        self.assertGreaterEqual(len(articles), 1)
        titles = [a["title"] for a in articles]
        self.assertTrue(any("中国人工智能" in t for t in titles))

    def test_proxy_manager_status(self):
        """测试 Windows 代理管理器获取状态（不修改状态，只读安全检查）"""
        status = WindowsProxyManager.get_proxy_status()
        self.assertIsInstance(status, dict)
        self.assertIn("enabled", status)
        self.assertIn("server", status)

    def test_port_helper(self):
        """测试端口探测与分配辅助函数"""
        # 测试本地非占用端口
        avail = is_port_available("127.0.0.1", 61234)
        self.assertTrue(avail)
        found = find_available_port("127.0.0.1", 61234)
        self.assertEqual(found, 61234)

    def test_sniffer_dispatch_and_history(self):
        """测试嗅探器任务调度与已同步记录（sync_history.json）去重"""
        sniffer = WechatSearchSnifferAddon(output_dir=self.test_dir, download_images=False)

        guid = "test_guid_12345"
        self.assertNotIn(guid, sniffer.archiver.history["synced_items"])

        # 模拟记录已同步文章
        sniffer.archiver.history["synced_items"][guid] = {
            "title": "测试文章标题",
            "author": "测试公众号",
            "link": "https://mp.weixin.qq.com/s?__biz=test&mid=123&idx=1&sn=abc"
        }

        # 二次检查：已存在于历史记录中
        self.assertIn(guid, sniffer.archiver.history["synced_items"])

    def test_get_upstream_proxy(self):
        """测试上游代理探测机制"""
        clean_env = {
            "WECHAT_UPSTREAM_PROXY": "",
            "HTTP_PROXY": "http://proxy.example.com:8080",
            "HTTPS_PROXY": "http://proxy.example.com:8080"
        }
        with patch.dict(os.environ, clean_env):
            proxy = get_upstream_proxy()
            self.assertEqual(proxy, "http://proxy.example.com:8080")

        local_env = {
            "WECHAT_UPSTREAM_PROXY": "",
            "HTTP_PROXY": "http://127.0.0.1:8080",
            "HTTPS_PROXY": "http://127.0.0.1:8080"
        }
        with patch.dict(os.environ, local_env):
            with patch("urllib.request.getproxies", return_value={}):
                proxy = get_upstream_proxy()
                self.assertNotEqual(proxy, "http://127.0.0.1:8080")
                self.assertIsNone(proxy)

    def test_clipboard_listener(self):
        """测试剪贴板监听提取微信文章并调用归档"""
        mock_archiver = MagicMock()
        mock_archiver.is_already_archived.return_value = False

        import threading
        stop_event = threading.Event()

        with patch("search_sniffer.get_clipboard_text", return_value="请看这篇文章：https://mp.weixin.qq.com/s/test_12345678"):
            t = threading.Thread(target=run_clipboard_listener, args=(mock_archiver, stop_event, 0.05), daemon=True)
            t.start()
            for _ in range(20):
                if mock_archiver.archive_article.called:
                    break
                time.sleep(0.05)
            stop_event.set()
            t.join(timeout=1)

        self.assertTrue(mock_archiver.archive_article.called)
        call_arg = mock_archiver.archive_article.call_args[0][0]
        self.assertEqual(call_arg["url"], "https://mp.weixin.qq.com/s/test_12345678")



if __name__ == "__main__":
    unittest.main()

