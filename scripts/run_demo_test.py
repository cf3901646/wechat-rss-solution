# -*- coding: utf-8 -*-
"""
本地端到端全链路运行验证脚本
启动本地轻量 HTTP 服务提供模拟微信公众号 RSS 源与文章配图，
运行 rss_sync.py 进行真实抓取、清洗、转换 Markdown、下载图片与落盘测试。
"""

import os
import sys
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# 保证 Windows 命令行控制台输出中文字符串不报错
if sys.platform.startswith("win"):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rss_sync import RssSyncEngine

# 1x1 像素透明 PNG 图片二进制
SAMPLE_PNG_BYTES = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
    b'\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00'
    b'\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
)

DEMO_PORT = 8999

# 注意：正文里的图片地址用 __PORT__ 占位符，由 build_sample_rss() 填入实际端口。
# 不能写死端口号，否则改 DEMO_PORT 后图片会指向一个没在监听的端口。
# （用 __PORT__ 而不是 str.format 的 {} ，避免和正文里可能出现的花括号冲突）
SAMPLE_RSS_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>极客技术前沿</title>
    <link>https://mp.weixin.qq.com</link>
    <description>专注前沿开发技术与开源工具</description>
    <item>
      <title>深度拆解：微信公众号自动化归档的最佳姿势</title>
      <link>https://mp.weixin.qq.com/s/sample_article_2026</link>
      <guid>wechat_art_guid_20260921_001</guid>
      <author>极客极速版</author>
      <pubDate>Mon, 21 Sep 2026 10:00:00 +0800</pubDate>
      <content:encoded><![CDATA[
        <div id="js_content">
          <p><span>点击上方蓝字关注</span></p>
          <h1>一、为什么我们需要把公众号转为 RSS？</h1>
          <p>在信息爆炸的今天，微信公众号的文章生态高度封闭，通过<strong>微信读书接口</strong>转换为标准 RSS 源，既能保护主号安全，又能实现自动化知识归档。</p>
          <blockquote>
            “让技术为人服务，把封闭的数据变成开放的资产。”
          </blockquote>
          <h2>二、核心架构示意</h2>
          <p>以下是系统架构流程图：</p>
          <p><img data-src="http://127.0.0.1:__PORT__/sample_image.png?wx_fmt=png" alt="系统架构示意图" /></p>
          <h2>三、核心代码演示</h2>
          <pre><code>import requests

def fetch_feed():
    print("Connecting to WeRSS feed...")
          </code></pre>
          <ul>
            <li>优势一：无封号风险</li>
            <li>优势二：自动下载防盗链图片</li>
            <li>优势三：无缝对接 Obsidian</li>
          </ul>
          <p>长按二维码识别关注</p>
        </div>
      ]]></content:encoded>
    </item>
  </channel>
</rss>
"""


def build_sample_rss(port: int) -> str:
    """把实际监听端口填入示例 RSS，保证正文里的图片地址与服务端口一致"""
    return SAMPLE_RSS_TEMPLATE.replace("__PORT__", str(port))


class MockRssHandler(BaseHTTPRequestHandler):
    # 由 run_demo() 在启动服务前按实际端口填好
    rss_body = ""

    def do_GET(self):
        if self.path.startswith("/feed.xml"):
            self.send_response(200)
            self.send_header("Content-Type", "application/xml; charset=utf-8")
            self.end_headers()
            self.wfile.write(self.rss_body.encode("utf-8"))
        elif "sample_image.png" in self.path:
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            self.wfile.write(SAMPLE_PNG_BYTES)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # 静默日志输出
        pass


DEMO_CONFIG_YAML = """
rss_feeds:
  - name: "极客技术前沿"
    url: "http://127.0.0.1:__PORT__/feed.xml"
    enabled: true

storage:
  output_dir: "./output/demo/articles"
  download_images: true
  images_dir_name: "images"
  history_db_path: "./output/demo/sync_history.json"

cleaner:
  remove_ads: true
  filter_keywords:
    - "点击上方蓝字关注"
    - "长按二维码识别关注"

scheduler:
  interval_minutes: 60

notification:
  channel: "none"
"""


def run_demo(port: int = DEMO_PORT):
    MockRssHandler.rss_body = build_sample_rss(port)

    server = HTTPServer(("127.0.0.1", port), MockRssHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    print(f"[1/4] 本地模拟 RSS 订阅源服务已启动: http://127.0.0.1:{port}/feed.xml")

    # 构造临时运行测试配置
    test_config_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "test_run_config.yaml"
    )

    # try/finally：断言失败时也要关掉 HTTP 服务并删掉临时配置，
    # 否则端口和残留文件会让下一次运行直接失败
    try:
        with open(test_config_path, "w", encoding="utf-8") as f:
            f.write(DEMO_CONFIG_YAML.replace("__PORT__", str(port)))

        print("[2/4] 初始化 RssSyncEngine 引擎并执行一次同步...")
        engine = RssSyncEngine(config_path=test_config_path)
        print(f"      输出目录: {engine.output_dir}")
        engine.run_once(force=True)

        print("\n[3/4] 验证生成的 Markdown 文件与图片...")
        output_dir = engine.output_dir
        md_files = [f for f in os.listdir(output_dir) if f.endswith(".md")]
        print(f"生成的 Markdown 文件列表: {md_files}")

        assert len(md_files) > 0, "未能成功生成 Markdown 文件！"
        target_md = os.path.join(output_dir, md_files[0])
        with open(target_md, "r", encoding="utf-8") as f:
            content = f.read()

        print("\n----- 生成的 Markdown 完整内容预览 -----")
        print(content)
        print("---------------------------------------\n")

        # 图片必须真的下载下来并改写成相对路径，这是本方案的核心卖点
        images_dir = os.path.join(output_dir, "images")
        assert os.path.isdir(images_dir), f"图片目录未创建: {images_dir}"
        downloaded_images = os.listdir(images_dir)
        print(f"下载的本地图片: {downloaded_images}")
        assert len(downloaded_images) > 0, "图片未成功下载！"
        assert f"images/{downloaded_images[0]}" in content, "Markdown 未改写为本地图片相对路径！"

        # 广告话术应被清洗掉
        assert "点击上方蓝字关注" not in content, "开头的引导关注段落未被清洗！"
        assert "长按二维码识别关注" not in content, "结尾的二维码段落未被清洗！"

        # 检查历史同步记录
        with open(engine.history_path, "r", encoding="utf-8") as f:
            hist_data = json.load(f)
        print(f"增量同步历史记录: {json.dumps(hist_data, ensure_ascii=False, indent=2)}")
        assert hist_data.get("synced_items"), "历史记录为空，增量去重无法生效！"

        print("\n[4/4] 🎉 全流程端到端运行验证通过！服务与引擎运转正常！")
    finally:
        if os.path.exists(test_config_path):
            os.remove(test_config_path)
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    run_demo()
