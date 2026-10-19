# -*- coding: utf-8 -*-
"""
基于 we-mp-rss 官方驱动的微信读书扫码登录器
特性：
1. 自动复用有效历史凭证，避免重复扫码
2. 在 CMD / PowerShell 终端直接绘制高对比度字符二维码
3. 自动调起 Windows 默认图片查看器弹出高清二维码图片，免翻找文件夹
4. 扫码成功后自动落盘 weread_credentials.json 与 wx.lic
"""

import os
import sys
import time
import json
import shutil
import argparse

# 保证 Windows 命令行控制台输出中文字符串及 Unicode 符号不乱码
if sys.platform.startswith("win"):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)
server_dir = os.path.join(project_root, "server")
sys.path.insert(0, server_dir)

from driver.weread_qr import WereadQRLogin


def print_terminal_qr(confirm_url: str):
    """在控制台终端直接渲染 ASCII 字符二维码（深色背景专用反色）"""
    try:
        import qrcode
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=1,
            border=2,
        )
        qr.add_data(confirm_url)
        qr.make(fit=True)

        print("\n" + "█" * 48)
        print("  📱 【方式一】请用微信扫描下方控制台二维码登录：")
        print("█" * 48)
        qr.print_ascii(invert=True)
        print("█" * 48 + "\n", flush=True)
    except Exception as e:
        print(f"[提示] 控制台字符二维码渲染跳过: {e}", flush=True)


def auto_open_qr_image(image_path: str):
    """自动调起系统默认图片查看器展示二维码图片，用户无需手动查找"""
    if sys.platform.startswith("win"):
        try:
            os.startfile(image_path)
            print("🖼️ 【方式二】已自动为您弹出二维码图片窗口（对准屏幕直接扫码即可）！", flush=True)
            return True
        except Exception as e:
            print(f"[提示] 自动调起看图软件失败 ({e})，图片保存在: {image_path}", flush=True)
    return False


def check_existing_credentials(login_mgr, base_dir: str, force: bool = False) -> bool:
    """检查本地是否已有有效的微信读书凭据，有效则避免重复扫码"""
    if force:
        return False

    data_dir = os.path.join(base_dir, "data")
    cred_file = os.path.join(data_dir, "weread_credentials.json")
    lic_file = os.path.join(data_dir, "wx.lic")

    cookies = {}
    vid = ""

    if os.path.exists(cred_file):
        try:
            with open(cred_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                cookies = data.get("cookies", {})
                vid = str(data.get("vid", ""))
        except Exception:
            pass
    elif os.path.exists(lic_file):
        try:
            with open(lic_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                weread = data.get("weread_data", {})
                cookies = weread.get("cookies", {})
                vid = str(weread.get("vid", ""))
        except Exception:
            pass

    if cookies and vid:
        print(f"[*] 发现历史登录凭据 (VID: {vid})，正在检测是否仍然有效...", flush=True)
        try:
            if login_mgr._verify_cookies(cookies, vid):
                print("=" * 55)
                print(f"🎉 现有凭据验证通过！当前微信读书账号 (VID: {vid}) 仍然有效。")
                print("💡 您无需重复扫码，可直接运行同步任务！")
                print("=" * 55, flush=True)
                return True

            # 尝试通过 renewal 自动续期
            renewed = login_mgr._renew_cookie(cookies)
            if renewed and login_mgr._verify_cookies(renewed, vid):
                print("=" * 55)
                print(f"🎉 现有凭据自动续期成功！微信读书账号 (VID: {vid}) 授权已刷新。")
                print("💡 您无需重复扫码，可直接运行同步任务！")
                print("=" * 55, flush=True)
                # 更新本地保存的 cookies
                save_credentials(base_dir, {"vid": vid, "cookies": renewed})
                return True
        except Exception as e:
            print(f"[*] 验证历史凭证时发生异常: {e}")

        print("[-] 历史凭据已过期或失效，需要重新扫码授权。\n", flush=True)

    return False


def save_credentials(base_dir: str, res: dict):
    """落盘保存授权信息"""
    vid = res.get("vid", "")
    data_dir = os.path.join(base_dir, "data")
    os.makedirs(data_dir, exist_ok=True)

    cred_file = os.path.join(data_dir, "weread_credentials.json")
    with open(cred_file, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print(f"✅ 凭据已保存至: {cred_file}", flush=True)

    lic_data = {
        "token_data": {},
        "weread_data": {
            "cookies": res.get("cookies", {}),
            "vid": str(vid),
            "update_time": time.strftime("%Y-%m-%d %H:%M:%S")
        }
    }
    lic_file = os.path.join(data_dir, "wx.lic")
    with open(lic_file, "w", encoding="utf-8") as f:
        json.dump(lic_data, f, ensure_ascii=False, indent=2)
    print(f"✅ 授权文件已保存至: {lic_file}", flush=True)

    # 同步写入 server/data/wx.lic
    server_data_dir = os.path.join(server_dir, "data")
    os.makedirs(server_data_dir, exist_ok=True)
    with open(os.path.join(server_data_dir, "wx.lic"), "w", encoding="utf-8") as f:
        json.dump(lic_data, f, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description="微信读书扫码登录向导")
    parser.add_argument("--force", action="store_true", help="强制重新扫码，忽略现有有效凭据")
    args = parser.parse_args()

    print("=" * 58)
    print("       微信读书扫码登录向导 (终端直显 + 自动弹窗免翻找)")
    print("=" * 58)

    base_dir = os.path.dirname(server_dir)
    login_mgr = WereadQRLogin()

    # 0. 优先检测现有授权是否依然有效，避免用户反复扫码
    if check_existing_credentials(login_mgr, base_dir, force=args.force):
        return

    logged_in_data = {}

    def on_success(res):
        nonlocal logged_in_data
        logged_in_data = res
        vid = res.get("vid", "")
        print(f"\n🎉 登录成功回调触发！微信读书 VID: {vid}", flush=True)
        save_credentials(base_dir, res)

    # 1. 设置二维码输出路径（使用时间戳避免 Windows 照片查看器锁定旧文件句柄导致 Errno 22）
    import glob
    output_dir = os.path.abspath(os.path.join(base_dir, "output"))
    os.makedirs(output_dir, exist_ok=True)
    for old_f in glob.glob(os.path.join(output_dir, "weread_qrcode*.png")):
        try:
            os.remove(old_f)
        except Exception:
            pass

    output_qr = os.path.join(output_dir, f"weread_qrcode_{int(time.time())}.png")
    login_mgr._qr_code_path = output_qr

    # 向微信读书官方申请登录会话与二维码
    print("\n[1/3] 正在向微信读书官方申请登录会话...", flush=True)
    qr_path = login_mgr.GetCode(CallBack=on_success)
    if not qr_path:
        print("[错误] 生成二维码失败，请检查网络连接！", flush=True)
        return

    # 可选：额外复制一份二维码到指定目录（供外部 UI 渲染）。
    # 通过环境变量配置，不要硬编码某台机器上的绝对路径。
    extra_copy_dir = os.environ.get("WECHAT_RSS_QR_COPY_DIR", "").strip()
    if extra_copy_dir and os.path.isdir(extra_copy_dir):
        try:
            shutil.copyfile(output_qr, os.path.join(extra_copy_dir, "weread_qrcode.png"))
        except Exception as e:
            print(f"[提示] 复制二维码到 {extra_copy_dir} 失败: {e}", flush=True)

    confirm_url = f"https://weread.qq.com/web/confirm?uid={login_mgr._uid}"

    print(f"[2/3] 二维码生成成功！", flush=True)
    print(f"UID: {login_mgr._uid}", flush=True)

    # 2. 核心体验升级：同时提供控制台字符直显 + 自动调起图片查看器
    # (1) 自动弹出 Windows 照片查看器，直接把二维码送到用户眼前
    auto_open_qr_image(qr_path)

    # (2) 控制台内直接打印字符版二维码
    print_terminal_qr(confirm_url)

    print(f"手机端备用快捷确认链接: {confirm_url}", flush=True)
    print(f"图片物理路径: {qr_path}\n", flush=True)

    # 3. 轮询等待扫码结果（最多等待 300 秒）
    print("[3/3] 正在实时监听扫码状态，请使用微信扫码确认...", flush=True)
    start_t = time.time()
    last_msg = ""

    while time.time() - start_t < 300:
        status = login_mgr.QrStatus()
        msg = status.get("msg", "")

        if msg and msg != last_msg:
            print(f">>> 当前状态: {msg}", flush=True)
            last_msg = msg

        if status.get("login_status") or logged_in_data:
            # 关键保障：主线程显式同步落盘，绝不依赖后台可能提前终止的 daemon 线程
            res_data = logged_in_data or status.get("data") or getattr(login_mgr, "_login_result", {})
            if res_data:
                save_credentials(base_dir, res_data)

            time.sleep(1)
            print("\n" + "=" * 58, flush=True)
            print("🎉 恭喜！微信读书扫码登录验证通过，凭据已自动保存！", flush=True)
            print("💡 现在您可以直接运行 windows\\start_sync.bat 开始同步文章！", flush=True)
            print("=" * 58, flush=True)
            return

        time.sleep(2)

    print("\n[超时] 5 分钟内未完成扫码确认。", flush=True)


if __name__ == "__main__":
    main()
