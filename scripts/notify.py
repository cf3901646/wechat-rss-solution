# -*- coding: utf-8 -*-
"""
多渠道消息推送与报警模块
支持：飞书、企业微信、钉钉、Server酱、Bark
用于：
1. 每日抓取成功/新文章推送
2. 微信读书登录凭证（Cookie/Token）过期报警
3. 抓取异常报警
"""

import logging
import requests

logger = logging.getLogger("Notifier")


class Notifier:
    def __init__(self, channel: str = "none", webhook_url: str = ""):
        self.channel = (channel or "none").lower().strip()
        self.webhook_url = (webhook_url or "").strip()

    def notify(self, title: str, content: str, url: str = "") -> bool:
        """发送通知消息（兼容别名）"""
        return self.send(title, content)

    def send(self, title: str, content: str) -> bool:
        """发送通知消息"""
        if not self.webhook_url or self.channel in ["none", ""]:
            return False

        try:
            if self.channel in ["feishu", "lark"]:
                return self._send_feishu(title, content)
            elif self.channel in ["wecom", "work_weixin", "qywx"]:
                return self._send_wecom(title, content)
            elif self.channel in ["dingtalk", "ding"]:
                return self._send_dingtalk(title, content)
            elif self.channel in ["serverchan", "sct"]:
                return self._send_serverchan(title, content)
            elif self.channel in ["bark"]:
                return self._send_bark(title, content)
            else:
                logger.warning(f"不支持的推送渠道: {self.channel}")
                return False
        except Exception as e:
            logger.error(f"消息推送异常 [{self.channel}]: {e}")
            return False

    def _send_feishu(self, title: str, content: str) -> bool:
        """飞书群机器人"""
        payload = {
            "msg_type": "post",
            "content": {
                "post": {
                    "zh_cn": {
                        "title": title,
                        "content": [
                            [{"tag": "text", "text": content}]
                        ]
                    }
                }
            }
        }
        resp = requests.post(self.webhook_url, json=payload, timeout=10)
        return resp.status_code == 200

    def _send_wecom(self, title: str, content: str) -> bool:
        """企业微信群机器人"""
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "content": f"### {title}\n{content}"
            }
        }
        resp = requests.post(self.webhook_url, json=payload, timeout=10)
        return resp.status_code == 200

    def _send_dingtalk(self, title: str, content: str) -> bool:
        """钉钉群机器人"""
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": f"### {title}\n\n{content}"
            }
        }
        resp = requests.post(self.webhook_url, json=payload, timeout=10)
        return resp.status_code == 200

    def _send_serverchan(self, title: str, content: str) -> bool:
        """Server酱微信推送"""
        payload = {
            "title": title,
            "desp": content
        }
        resp = requests.post(self.webhook_url, data=payload, timeout=10)
        return resp.status_code == 200

    def _send_bark(self, title: str, content: str) -> bool:
        """Bark (iOS 推送)"""
        url = f"{self.webhook_url.rstrip('/')}/{requests.utils.quote(title)}/{requests.utils.quote(content)}"
        resp = requests.get(url, timeout=10)
        return resp.status_code == 200
