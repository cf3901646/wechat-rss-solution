# -*- coding: utf-8 -*-
"""
微信读书扫码登录交互式控制器
功能：
1. 从微信读书官方接口获取登录 UID
2. 生成二维码图片（保存至项目目录及 Artifact 目录供展示）
3. 打印控制台 ASCII 二维码方便终端扫码
4. 轮询登录状态，检测用户扫码与确认动作
5. 登录成功后保存 Cookie 凭据至 data/weread_credentials.json
"""

import os
import sys
import time
import json
import shutil
import requests
import qrcode
import io

# 确保在 Windows 控制台环境下输出 UTF-8
if sys.platform.startswith("win"):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

WEREAD_BASE = "https://weread.qq.com"


class WereadLoginHelper:
    def __init__(self, artifact_dir: str = "", project_dir: str = ""):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": "https://weread.qq.com/",
            "Origin": "https://weread.qq.com",
            "Accept": "application/json, text/plain, */*"
        })
        self.uid = ""
        self.artifact_dir = artifact_dir
        self.project_dir = project_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.data_dir = os.path.join(self.project_dir, "data")
        os.makedirs(self.data_dir, exist_ok=True)

    def get_login_uid(self) -> str:
        """获取登录唯一凭据 UID"""
        url = f"{WEREAD_BASE}/api/auth/getLoginUid"
        resp = self.session.get(url, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            self.uid = data.get("uid") or (data.get("data") or {}).get("uid") or ""
            return self.uid
        raise RuntimeError(f"获取微信读书 UID 失败: HTTP {resp.status_code}")

    def generate_qrcode(self) -> str:
        """生成登录二维码图片并保存"""
        if not self.uid:
            self.get_login_uid()

        confirm_url = f"{WEREAD_BASE}/web/confirm?uid={self.uid}"

        # 1. 生成高清晰度 PNG 图片
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=4
        )
        qr.add_data(confirm_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")

        # 保存到本地项目目录
        local_qr_path = os.path.join(self.project_dir, "output", "weread_qrcode.png")
        os.makedirs(os.path.dirname(local_qr_path), exist_ok=True)
        img.save(local_qr_path)

        # 如果提供了 artifact 目录，复制一份过去以便在 UI 中渲染
        if self.artifact_dir and os.path.exists(self.artifact_dir):
            artifact_qr_path = os.path.join(self.artifact_dir, "weread_qrcode.png")
            shutil.copyfile(local_qr_path, artifact_qr_path)

        # 2. 打印控制台 ASCII 二维码
        print("\n" + "=" * 50)
        print("请打开手机微信【扫一扫】，扫描下方二维码登录微信读书：")
        print("=" * 50 + "\n")
        try:
            f = io.StringIO()
            qr.print_ascii(out=f, invert=True)
            print(f.getvalue())
        except Exception:
            pass

        print(f"二维码图片已保存到: {local_qr_path}")
        print(f"手机端登录确认链接: {confirm_url}\n")
        return local_qr_path

    def poll_login(self, timeout: int = 300) -> dict:
        """轮询等待用户扫码和确认（默认 300 秒 / 5 分钟）"""
        start_time = time.time()
        print(">>> 正在等待扫码...", flush=True)
        scanned_alerted = False

        while time.time() - start_time < timeout:
            try:
                url = f"{WEREAD_BASE}/api/auth/getLoginInfo"
                params = {"uid": self.uid, "otp": ""}
                resp = self.session.get(url, params=params, timeout=20)

                if resp.status_code == 200:
                    data = resp.json()
                    inner = data.get("data") or {}
                    succeed = data.get("succeed") or inner.get("succeed")
                    logic_code = data.get("logicCode")

                    # 已扫码但未确认
                    if logic_code == 1 and not scanned_alerted:
                        print(">>> 📱 微信已成功扫描二维码！请在手机上点击【确认登录】...")
                        scanned_alerted = True

                    # 登录成功
                    if succeed:
                        print(">>> 🎉 微信读书登录成功！正在保存授权凭据...")
                        # 收集所有 cookies
                        cookie_dict = self.session.cookies.get_dict()
                        vid = data.get("vid") or inner.get("vid") or inner.get("userVid") or ""
                        access_token = data.get("accessToken") or inner.get("accessToken") or ""
                        refresh_token = data.get("refreshToken") or inner.get("refreshToken") or ""

                        login_info = {
                            "vid": str(vid),
                            "accessToken": access_token,
                            "refreshToken": refresh_token,
                            "cookies": cookie_dict,
                            "login_time": time.strftime("%Y-%m-%d %H:%M:%S")
                        }

                        # 保存凭证
                        cred_path = os.path.join(self.data_dir, "weread_credentials.json")
                        with open(cred_path, "w", encoding="utf-8") as f:
                            json.dump(login_info, f, ensure_ascii=False, indent=2)

                        # 同时保存一份给 we-mp-rss 兼容的格式
                        lic_data = {
                            "token_data": {},
                            "weread_data": {
                                "cookies": cookie_dict,
                                "vid": str(vid),
                                "update_time": time.strftime("%Y-%m-%d %H:%M:%S")
                            }
                        }
                        lic_path = os.path.join(self.data_dir, "wx.lic")
                        with open(lic_path, "w", encoding="utf-8") as f:
                            json.dump(lic_data, f, ensure_ascii=False, indent=2)

                        print(f">>> 授权凭证已持久化到: {cred_path}")
                        return login_info

                    if logic_code == "LOGIN_TIMEOUT":
                        print(">>> ⚠️ 二维码已失效，请重新生成！")
                        return {}

                time.sleep(2)
            except Exception:
                time.sleep(2)

        print(f">>> ⚠️ 等待扫码超时（{timeout} 秒），请重新执行！")
        return {}


def main():
    # 可选：额外复制一份二维码到指定目录（供外部 UI 渲染），通过环境变量配置。
    # 之前这里硬编码了某台机器上的绝对路径，换机器即失效。
    artifact_dir = os.environ.get("WECHAT_RSS_QR_COPY_DIR", "").strip()
    helper = WereadLoginHelper(artifact_dir=artifact_dir)
    print("正在请求微信读书二维码...")
    helper.generate_qrcode()
    result = helper.poll_login()
    if result:
        print(f"\n[完成] 微信读书用户已就绪，VID: {result.get('vid')}")
    else:
        print("\n[未完成] 登录未成功。")


if __name__ == "__main__":
    main()
