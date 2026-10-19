# -*- coding: utf-8 -*-
"""
微信公众号 RSS 同步引擎自动化单元与集成测试
"""

import os
import sys
import json
import shutil
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from html_to_markdown import WechatHtmlToMarkdown
from rss_sync import PROJECT_ROOT, RssSyncEngine, sanitize_filename


class TestWechatSync(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.output_dir = os.path.join(self.test_dir, "output")
        self.images_dir = os.path.join(self.output_dir, "images")
        self.history_file = os.path.join(self.output_dir, "history.json")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _write_config(self) -> str:
        """
        写一份指向临时目录的测试配置并返回路径。

        路径先转成正斜杠再拼进字符串：反斜杠不能出现在 f-string 的表达式部分
        （Python 3.12 之前会直接 SyntaxError），所以必须在 f-string 外面算好。
        """
        output_dir = self.output_dir.replace(os.sep, "/")
        history_file = self.history_file.replace(os.sep, "/")

        config_path = os.path.join(self.test_dir, "config.yaml")
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(
                f"""
rss_feeds: []
storage:
  output_dir: "{output_dir}"
  download_images: false
  history_db_path: "{history_file}"
"""
            )
        return config_path

    def test_sanitize_filename(self):
        """测试文件名非法字符清理"""
        raw = '深度测评：微信/读书与RSS？真的好用吗*<2026>|测试"文件名'
        cleaned = sanitize_filename(raw)
        for char in ['\\', '/', ':', '*', '?', '"', '<', '>', '|']:
            self.assertNotIn(char, cleaned)
        self.assertTrue(len(cleaned) > 0)

    def test_html_to_markdown_conversion(self):
        """测试微信 HTML 转 Markdown 转换效果"""
        converter = WechatHtmlToMarkdown(
            download_images=False,
            remove_ads=True,
            filter_keywords=["点击上方蓝字关注"]
        )

        test_html = """
        <div id="js_content">
            <p><span>点击上方蓝字关注</span></p>
            <h1>核心技术解析</h1>
            <p>这是<strong>微信公众号</strong>的正文段落，包含一个<a href="https://example.com">外部链接</a>。</p>
            <blockquote>这是引用的重要内容</blockquote>
            <pre><code>def hello():\n    print("WeRSS")</code></pre>
            <p><img data-src="https://mmbiz.qpic.cn/mmbiz_png/test/640?wx_fmt=png" alt="测试配图"/></p>
            <ul>
                <li>第一点优势</li>
                <li>第二点优势</li>
            </ul>
        </div>
        """

        metadata = {
            "title": "测试文章标题",
            "author": "测试公众号",
            "date": "2026-09-21",
            "url": "https://mp.weixin.qq.com/s/test12345"
        }

        md = converter.convert(test_html, metadata=metadata)

        # 验证 YAML Frontmatter
        self.assertIn('title: "测试文章标题"', md)
        self.assertIn('author: "测试公众号"', md)
        self.assertIn('source_url: "https://mp.weixin.qq.com/s/test12345"', md)

        # 验证正文元素转换
        self.assertIn('# 核心技术解析', md)
        self.assertIn('**微信公众号**', md)
        self.assertIn('[外部链接](https://example.com)', md)
        self.assertIn('> 这是引用的重要内容', md)
        self.assertIn('def hello():', md)
        self.assertIn('- 第一点优势', md)
        self.assertIn('- 第二点优势', md)
        self.assertIn('![测试配图](https://mmbiz.qpic.cn/mmbiz_png/test/640?wx_fmt=png)', md)

        # 验证广告段落被清洗
        self.assertNotIn('点击上方蓝字关注', md)

    def test_parse_rss_20(self):
        """测试 RSS 2.0 格式 XML 解析"""
        sample_rss = """<?xml version="1.0" encoding="utf-8"?>
        <rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
          <channel>
            <title>测试号Channel</title>
            <item>
              <title>RSS 2.0 文章一</title>
              <link>https://mp.weixin.qq.com/s/art1</link>
              <guid>art1_guid</guid>
              <author>作者A</author>
              <pubDate>Mon, 21 Sep 2026 08:30:00 +0800</pubDate>
              <content:encoded><![CDATA[<p>正文内容一</p>]]></content:encoded>
            </item>
          </channel>
        </rss>
        """

        engine = RssSyncEngine(config_path=self._write_config())
        items = engine._parse_xml_feed(sample_rss)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "RSS 2.0 文章一")
        self.assertEqual(items[0]["guid"], "art1_guid")
        self.assertEqual(items[0]["author"], "作者A")
        self.assertIn("正文内容一", items[0]["content_html"])

    def test_parse_atom_10(self):
        """测试 Atom 1.0 格式 XML 解析"""
        sample_atom = """<?xml version="1.0" encoding="utf-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <title>测试号Feed</title>
          <entry>
            <id>atom_art_1</id>
            <title>Atom 文章一</title>
            <link href="https://mp.weixin.qq.com/s/atom1"/>
            <published>2026-09-21T09:00:00Z</published>
            <author><name>Atom作者</name></author>
            <content type="html"><![CDATA[<p>Atom 正文内容</p>]]></content>
          </entry>
        </feed>
        """

        engine = RssSyncEngine(config_path=self._write_config())
        items = engine._parse_xml_feed(sample_atom)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Atom 文章一")
        self.assertEqual(items[0]["guid"], "atom_art_1")
        self.assertEqual(items[0]["link"], "https://mp.weixin.qq.com/s/atom1")
        self.assertEqual(items[0]["author"], "Atom作者")
        self.assertIn("Atom 正文内容", items[0]["content_html"])

    def test_incremental_sync_and_deduplication(self):
        """测试增量同步与去重持久化逻辑"""
        engine = RssSyncEngine(config_path=self._write_config())

        # 模拟包含两篇文章的 Feed
        fake_feed = {"name": "模拟公众号", "url": "mock://feed"}
        fake_xml = """<?xml version="1.0" encoding="utf-8"?>
        <rss version="2.0">
          <channel>
            <title>模拟公众号</title>
            <item>
              <title>文章101</title>
              <link>https://mp.weixin.qq.com/s/101</link>
              <guid>guid_101</guid>
              <pubDate>2026-09-21</pubDate>
              <description><![CDATA[<p>文章101正文</p>]]></description>
            </item>
            <item>
              <title>文章102</title>
              <link>https://mp.weixin.qq.com/s/102</link>
              <guid>guid_102</guid>
              <pubDate>2026-09-21</pubDate>
              <description><![CDATA[<p>文章102正文</p>]]></description>
            </item>
          </channel>
        </rss>
        """

        # 模拟 session.get 返回 fake_xml
        class MockResp:
            status_code = 200
            text = fake_xml
            encoding = "utf-8"

        engine.session.get = lambda url, timeout=20: MockResp()

        # 第一次同步：应抓取 2 篇
        first_sync = engine.sync_feed(fake_feed)
        self.assertEqual(len(first_sync), 2)
        self.assertTrue(os.path.exists(self.history_file))

        # 检查生成的文件
        files = os.listdir(self.output_dir)
        md_files = [f for f in files if f.endswith(".md")]
        self.assertEqual(len(md_files), 2)

        # 第二次同步：因为已经去重，应抓取 0 篇
        second_sync = engine.sync_feed(fake_feed)
        self.assertEqual(len(second_sync), 0)

        # 开启 force 同步：应重新覆盖抓取 2 篇
        force_sync = engine.sync_feed(fake_feed, force=True)
        self.assertEqual(len(force_sync), 2)

        # force 同步不应该额外堆积文件，仍然是原来那 2 个
        md_files_after_force = [f for f in os.listdir(self.output_dir) if f.endswith(".md")]
        self.assertEqual(len(md_files_after_force), 2)

    def test_relative_paths_resolve_to_project_root(self):
        """配置里的相对路径必须以项目根目录为基准，而不是 config 文件所在目录"""
        config_dir = os.path.join(self.test_dir, "nested", "conf")
        os.makedirs(config_dir, exist_ok=True)
        config_path = os.path.join(config_dir, "config.yaml")
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(
                """
rss_feeds: []
storage:
  output_dir: "./output/articles"
  download_images: false
  history_db_path: "./output/sync_history.json"
"""
            )

        engine = RssSyncEngine(config_path=config_path)

        self.assertEqual(
            os.path.normpath(engine.output_dir),
            os.path.normpath(os.path.join(PROJECT_ROOT, "output", "articles")),
        )
        self.assertEqual(
            os.path.normpath(engine.history_path),
            os.path.normpath(os.path.join(PROJECT_ROOT, "output", "sync_history.json")),
        )
        # 绝不能落到 config 文件所在目录下
        self.assertNotIn(os.path.normpath(config_dir), os.path.normpath(engine.output_dir))

    def test_same_day_same_title_does_not_overwrite(self):
        """同一天发布的同名标题文章（不同 guid）不能互相覆盖"""
        engine = RssSyncEngine(config_path=self._write_config())

        fake_xml = """<?xml version="1.0" encoding="utf-8"?>
        <rss version="2.0">
          <channel>
            <title>模拟公众号</title>
            <item>
              <title>每日速报</title>
              <link>https://mp.weixin.qq.com/s/aaa</link>
              <guid>guid_aaa</guid>
              <pubDate>2026-09-21</pubDate>
              <description><![CDATA[<p>上午版正文</p>]]></description>
            </item>
            <item>
              <title>每日速报</title>
              <link>https://mp.weixin.qq.com/s/bbb</link>
              <guid>guid_bbb</guid>
              <pubDate>2026-09-21</pubDate>
              <description><![CDATA[<p>下午版正文</p>]]></description>
            </item>
          </channel>
        </rss>
        """

        class MockResp:
            status_code = 200
            text = fake_xml
            encoding = "utf-8"

        engine.session.get = lambda url, timeout=20: MockResp()

        synced = engine.sync_feed({"name": "模拟公众号", "url": "mock://feed"})
        self.assertEqual(len(synced), 2)

        md_files = sorted(f for f in os.listdir(self.output_dir) if f.endswith(".md"))
        self.assertEqual(len(md_files), 2, f"同名文章被覆盖了，实际文件: {md_files}")

        # 两篇文章的正文应分别保留，而不是其中一篇被另一篇冲掉
        bodies = []
        for name in md_files:
            with open(os.path.join(self.output_dir, name), "r", encoding="utf-8") as f:
                bodies.append(f.read())
        self.assertTrue(any("上午版正文" in b for b in bodies))
        self.assertTrue(any("下午版正文" in b for b in bodies))

    def test_corrupted_history_file_is_recovered(self):
        """历史记录文件结构异常时应重新初始化，而不是抛 KeyError"""
        os.makedirs(self.output_dir, exist_ok=True)
        # 缺少 synced_items 键：旧实现会在后续访问时直接 KeyError
        with open(self.history_file, "w", encoding="utf-8") as f:
            json.dump({}, f)

        engine = RssSyncEngine(config_path=self._write_config())
        self.assertIn("synced_items", engine.history)
        self.assertEqual(engine.history["synced_items"], {})

        # 内容不是合法 JSON 的情况同样要能恢复
        with open(self.history_file, "w", encoding="utf-8") as f:
            f.write("{ this is not json")
        engine2 = RssSyncEngine(config_path=self._write_config())
        self.assertEqual(engine2.history["synced_items"], {})

    def test_sanitize_filename_truncates_by_utf8_bytes(self):
        """纯中文长标题截断后必须满足文件系统的 255 字节上限"""
        long_title = "深度长标题" * 80  # 400 个中文字符 = 1200 字节
        cleaned = sanitize_filename(long_title)
        self.assertLessEqual(len(cleaned.encode("utf-8")), 180)
        # 截断不能留下半个多字节字符
        cleaned.encode("utf-8").decode("utf-8")

    def test_atom_prefers_alternate_link(self):
        """Atom entry 有多个 link 时应取 rel=alternate，而不是第一个"""
        sample_atom = """<?xml version="1.0" encoding="utf-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <title>测试号Feed</title>
          <entry>
            <id>atom_multi_link</id>
            <title>多链接文章</title>
            <link rel="edit" href="https://example.com/edit/1"/>
            <link rel="alternate" href="https://mp.weixin.qq.com/s/real_article"/>
            <published>2026-09-21T09:00:00Z</published>
            <content type="html"><![CDATA[<p>正文</p>]]></content>
          </entry>
        </feed>
        """

        engine = RssSyncEngine(config_path=self._write_config())
        items = engine._parse_xml_feed(sample_atom)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["link"], "https://mp.weixin.qq.com/s/real_article")


if __name__ == "__main__":
    unittest.main()
