# -*- coding: utf-8 -*-
"""
PC 端微信搜一搜全自动批量爬取机器人 (WeChat Auto Crawler Robot)
--------------------------------------------------------------
核心功能：
只需输入搜索关键词（如“医疗器械”）和期望篇数（如 5）：
1. 自动唤起并置顶电脑微信客户端；
2. 自动激活微信搜一搜并键入关键词搜索；
3. 自动遍历点击搜索结果卡片；
4. 自动全选并复制文章全文与富文本排版；
5. 自动完成 Markdown 结构化转换与单文件独立 HTML 落盘；
6. 自动关闭当前文章并滚动切换下一篇；
7. 全自动高效获取微信文章高清图文并本地归档！
"""

import os
import sys
import time
import socket
import argparse
import subprocess
import threading
import atexit
import signal
import hashlib
from typing import Optional, Tuple

# 终端输出 UTF-8 设置
if sys.platform.startswith("win"):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def release_all_modifier_keys():
    """
    强制向 Windows 系统底层广播释放 Ctrl, Shift, Alt, Win 等所有修饰键的 KEYUP 事件。
    核心防御：彻底根除因自动化中断（如 Ctrl+C）导致的逻辑卡键/粘滞键（打字变快捷键）。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        u32 = ctypes.windll.user32
        KEYEVENTF_KEYUP = 0x0002
        # 0x11: VK_CONTROL, 0xA2: VK_LCONTROL, 0xA3: VK_RCONTROL
        # 0x10: VK_SHIFT,   0xA0: VK_LSHIFT,   0xA1: VK_RSHIFT
        # 0x12: VK_MENU,    0xA4: VK_LMENU,    0xA5: VK_RMENU
        # 0x5B: VK_LWIN,    0x5C: VK_RWIN
        keys_to_release = [0x11, 0xA2, 0xA3, 0x10, 0xA0, 0xA1, 0x12, 0xA4, 0xA5, 0x5B, 0x5C]
        for vk in keys_to_release:
            try:
                u32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
            except Exception:
                pass
    except Exception:
        pass


# 进程退出全局兜底：无论正常退出还是异常崩溃，一律无条件释放所有修饰键
atexit.register(release_all_modifier_keys)

# 注册 Windows 原生控制台事件处理器：即使用户直接点击 CMD 窗口右上角 [X] 关闭，也强制自动清理按键
_console_handler_ref = None
if sys.platform == "win32":
    try:
        import ctypes
        from ctypes import wintypes
        PHANDLER_ROUTINE = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

        def _console_ctrl_handler(ctrl_type):
            release_all_modifier_keys()
            return False

        _console_handler_ref = PHANDLER_ROUTINE(_console_ctrl_handler)
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_console_handler_ref, True)
    except Exception:
        pass

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

try:
    import uiautomation as auto
except ImportError:
    auto = None

def get_clipboard_text() -> str:
    """跨平台安全提取剪贴板纯文本（自动链式降级：pyperclip -> win32clipboard -> ctypes）"""
    try:
        import pyperclip
        t = pyperclip.paste()
        if t:
            return t
    except Exception:
        pass

    try:
        import win32clipboard
        import win32con
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                data = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                if data:
                    return str(data)
            elif win32clipboard.IsClipboardFormatAvailable(win32con.CF_TEXT):
                data = win32clipboard.GetClipboardData(win32con.CF_TEXT)
                if data:
                    return data.decode("gbk", errors="ignore")
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        pass

    if sys.platform == "win32":
        try:
            import ctypes
            u32 = ctypes.windll.user32
            k32 = ctypes.windll.kernel32
            if u32.OpenClipboard(None):
                try:
                    h_mem = u32.GetClipboardData(13)  # CF_UNICODETEXT
                    if h_mem:
                        p_mem = k32.GlobalLock(h_mem)
                        if p_mem:
                            try:
                                return ctypes.wstring_at(p_mem)
                            finally:
                                k32.GlobalUnlock(h_mem)
                finally:
                    u32.CloseClipboard()
        except Exception:
            pass

    return ""


def set_clipboard_text(text: str) -> bool:
    """安全设置系统剪贴板文本（自动链式降级：pyperclip -> win32clipboard）"""
    try:
        import pyperclip
        pyperclip.copy(text)
        return True
    except Exception:
        pass

    try:
        import win32clipboard
        import win32con
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
            return True
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        pass

    return False


from search_sniffer import WechatArticleArchiver, run_clipboard_listener, is_port_available


def ensure_sniffer_running(port: int = 8080) -> Tuple[threading.Event, Optional[threading.Thread]]:
    """确保后台剪贴板归档监听引擎就绪"""
    stop_event = threading.Event()
    archiver = WechatArticleArchiver()
    thread = threading.Thread(
        target=run_clipboard_listener,
        args=(archiver, stop_event, 0.2),  # 提高轮询频率至 200ms，毫秒级即时归档
        name="AutoCrawlerSniffer",
        daemon=True
    )
    thread.start()
    return stop_event, thread


def find_and_activate_wechat() -> Tuple[bool, Optional[int]]:
    """寻找并激活微信主窗口到屏幕最前端，返回 (成功状态, 主窗口句柄)"""
    if sys.platform != "win32":
        return False, None
    import ctypes
    from ctypes import wintypes
    u32 = ctypes.windll.user32
    k32 = ctypes.windll.kernel32

    # 核心突破：绑定交互式窗口工作站 WinSta0 与 Default 桌面
    h_desk = None
    try:
        h_winsta = u32.OpenWindowStationW("WinSta0", False, 0x0000037F)
        if h_winsta:
            u32.SetProcessWindowStation(h_winsta)
        h_desk = u32.OpenDesktopW("Default", 0, False, 0x10000000)
        if h_desk:
            u32.SetThreadDesktop(h_desk)
    except Exception:
        pass

    target_hwnds = []

    def _enum_cb(hwnd, lparam):
        if u32.IsWindowVisible(hwnd):
            buf_cls = ctypes.create_unicode_buffer(256)
            u32.GetClassNameW(hwnd, buf_cls, 256)
            buf_txt = ctypes.create_unicode_buffer(512)
            u32.GetWindowTextW(hwnd, buf_txt, 512)
            cname = buf_cls.value
            tname = buf_txt.value
            if any(k in cname for k in ["WeChat", "Weixin", "ChatWnd", "Qt51514QWindowIcon"]) or any(k in tname for k in ["微信", "WeChat"]):
                target_hwnds.append((hwnd, cname, tname))
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    u32.EnumWindows(WNDENUMPROC(_enum_cb), 0)

    # 若常规 EnumWindows 未捕获，使用桌面原生 EnumDesktopWindows 强力遍历
    if not target_hwnds and h_desk:
        DESKENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        u32.EnumDesktopWindows(h_desk, DESKENUMPROC(_enum_cb), 0)

    if not target_hwnds:
        return False, None

    # 优先匹配主窗口
    main_hwnd = target_hwnds[0][0]
    for h, c, t in target_hwnds:
        if "MainWnd" in c or t == "微信" or t == "WeChat":
            main_hwnd = h
            break

    # 强化置顶与按键输入上下文绑定
    try:
        cur_thread = k32.GetCurrentThreadId()
        fore_hwnd = u32.GetForegroundWindow()
        fore_thread = u32.GetWindowThreadProcessId(fore_hwnd, None)
        if cur_thread != fore_thread and fore_thread:
            u32.AttachThreadInput(cur_thread, fore_thread, True)

        u32.ShowWindow(main_hwnd, 9)  # SW_RESTORE
        u32.BringWindowToTop(main_hwnd)
        u32.SetForegroundWindow(main_hwnd)

        if cur_thread != fore_thread and fore_thread:
            u32.AttachThreadInput(cur_thread, fore_thread, False)
    except Exception:
        u32.ShowWindow(main_hwnd, 9)
        u32.SetForegroundWindow(main_hwnd)

    time.sleep(0.5)
    return True, main_hwnd


def is_wechat_article_window(hwnd: int) -> bool:
    """安全门禁：校验指定窗口是否确为微信文章阅读窗口，坚决防止 Ctrl+W 误杀用户工作窗口"""
    if not hwnd or sys.platform != "win32":
        return False
    import ctypes
    u32 = ctypes.windll.user32
    k32 = ctypes.windll.kernel32
    
    # 检查进程名是否为 Weixin 或 WeChatAppEx
    pid = ctypes.wintypes.DWORD()
    u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h_proc = k32.OpenProcess(0x1000, False, pid.value)
    if not h_proc:
        return False
    pbuf = ctypes.create_unicode_buffer(512)
    sz = ctypes.wintypes.DWORD(512)
    k32.QueryFullProcessImageNameW(h_proc, 0, pbuf, ctypes.byref(sz))
    k32.CloseHandle(h_proc)
    pname = os.path.basename(pbuf.value).lower()
    if not any(k in pname for k in ["weixin", "wechat"]):
        return False

    buf_txt = ctypes.create_unicode_buffer(512)
    u32.GetWindowTextW(hwnd, buf_txt, 512)
    title = buf_txt.value

    # 检查窗口类名，坚决排除微信主控制面板（WeChatMainWndForPC）
    buf_cls = ctypes.create_unicode_buffer(256)
    u32.GetClassNameW(hwnd, buf_cls, 256)
    cname = buf_cls.value
    if "MainWnd" in cname:
        return False

    # 若标题为空且类名不是独立内容视窗，安全排除
    if not title and not any(k in cname for k in ["ChatWnd", "AppEx", "Qt5", "Chrome_WidgetWin"]):
        return False

    return True


def auto_crawl_wechat(keyword: str = "", max_articles: int = 5, auto_search: bool = True):
    """
    全自动控制电脑微信搜一搜并批量采集文章（脱胎换骨重构版）：
    1. 动态自适应高 DPI 与多显示器视口；
    2. 搜一搜自动切换至【文章】专用分类，剔除视频号与百科杂项；
    3. 毫秒级剪贴板序列号变化监听，杜绝盲等 sleep；
    4. 安全门禁防误关：严格校验文章窗口句柄，杜绝 Ctrl+W 误杀工作窗口；
    5. 操作结束后完整恢复用户原系统剪贴板环境。
    """
    mode_desc = f"全自动搜索【{keyword}】" if auto_search and keyword else "直接收割当前搜一搜结果列表"
    print("======================================================================")
    print(f"   🤖 启动微信搜一搜收割机器人 (整风重构版): {mode_desc} | 目标: {max_articles} 篇")
    print("======================================================================")

    if not auto:
        print("❌ [错误] 缺少 uiautomation 依赖，请确认虚拟环境完整。")
        return

    import ctypes
    from ctypes import wintypes
    u32 = ctypes.windll.user32

    # 0. 安全暂存用户原本的系统剪贴板文本
    user_orig_clipboard = get_clipboard_text()

    # 1. 启动守护监听引擎
    stop_event, _ = ensure_sniffer_running(8080)
    time.sleep(0.5)

    # 2. 唤起微信
    print("👉 [步骤 1/4] 正在唤起并置顶电脑微信客户端...")
    activated, main_hwnd = find_and_activate_wechat()
    if not activated:
        try:
            w_ctl = auto.WindowControl(searchDepth=1, SubName="微信")
            if w_ctl.Exists(0.5):
                w_ctl.SetActive()
                activated = True
                main_hwnd = w_ctl.NativeWindowHandle
        except Exception:
            pass

    if not activated:
        print("⚠️ 未能自动找到微信窗口，请手动点击打开电脑微信并保持在屏幕前台！")
        time.sleep(2.0)

    # 智能自适应定位：基于微信窗口实际矩形坐标计算
    if main_hwnd:
        rect = wintypes.RECT()
        u32.GetWindowRect(main_hwnd, ctypes.byref(rect))
        win_left, win_top = rect.left, rect.top
        win_w = rect.right - rect.left
        win_h = rect.bottom - rect.top

        # 针对微型折叠窗口自动标准化展开
        if win_w < 950 or win_h < 650:
            print("   📐 检测到微信窗口过小，自动标准化展开至最佳视口 (1200x800)...")
            u32.SetWindowPos(main_hwnd, 0, win_left, win_top, 1200, 800, 0x0040)
            time.sleep(0.3)
            u32.GetWindowRect(main_hwnd, ctypes.byref(rect))
            win_left, win_top = rect.left, rect.top
            win_w = rect.right - rect.left
            win_h = rect.bottom - rect.top

        search_panel_x = win_left + int(win_w * 0.78)
        search_box_y = win_top + int(win_h * 0.36)
        search_btn_x = win_left + int(win_w * 0.925)
        article_tab_x = win_left + int(win_w * 0.745)
        article_tab_y = win_top + int(win_h * 0.415)
        
        card_x = search_panel_x
        start_y = win_top + int(win_h * 0.48)  # 留出文章Tab下方的真实卡片位置
        step_y = max(90, int(win_h * 0.13))
        max_y = win_top + int(win_h * 0.85)
    else:
        screen_w, screen_h = auto.GetScreenSize()
        card_x = int(screen_w * 0.75)
        start_y = int(screen_h * 0.45)
        step_y = 110
        max_y = int(screen_h * 0.8)
        search_box_y = int(screen_h * 0.36)
        search_btn_x = int(screen_w * 0.925)
        article_tab_x = int(screen_w * 0.745)
        article_tab_y = int(screen_h * 0.415)

    # 3. 如果需要自动搜索
    if auto_search and keyword:
        print(f"👉 [步骤 2/4] 正在向搜一搜键入关键词【{keyword}】并检索...")
        auto.Click(card_x, search_box_y)
        time.sleep(0.3)

        if set_clipboard_text(keyword):
            auto.SendKeys("{Ctrl}a")
            time.sleep(0.1)
            auto.SendKeys("{Ctrl}v")
        else:
            auto.SendKeys(keyword)
        time.sleep(0.3)

        # 触发搜索
        auto.Click(search_btn_x, search_box_y)
        auto.SendKeys("{Enter}")
        print("👉 搜索已触发，等待结果卡片加载 (2.5 秒)...")
        time.sleep(2.5)

        # 关键防御：点击【文章】分类标签，剔除视频号与百科杂质
        print("👉 [步骤 3/4] 锁定【文章】专属分类标签，剔除视频号与百科杂项...")
        auto.Click(article_tab_x, article_tab_y)
        time.sleep(1.5)
    else:
        print("👉 [步骤 2/4] 已锁定当前微信搜一搜结果，准备开始批量收割...")
        time.sleep(1.0)

    # 4. 循环遍历点击文章
    print("👉 [步骤 4/4] 开始全自动批量收割文章内容与高清配图...")
    print(f"   🎯 目标篇数: {max_articles} 篇 | 自动开启连续滚动翻页、防重复与防假死机制...\n")

    harvested_signatures = set()
    harvested_titles = []
    consecutive_misses = 0
    curr_y = start_y
    max_loops = max(max_articles * 4, 40)
    loop_idx = 0

    # 循环前基准状态清零，确保按键无残留
    release_all_modifier_keys()

    try:
        while len(harvested_signatures) < max_articles and loop_idx < max_loops and consecutive_misses < 8:
            loop_idx += 1
            curr_progress = len(harvested_signatures) + 1
            print(f"--- [正在探测/采集第 {curr_progress}/{max_articles} 篇 (第 {loop_idx} 轮扫描)] ---")

            # 记录点击前的剪贴板序列号
            seq_before = u32.GetClipboardSequenceNumber()

            # 点击搜一搜结果卡片
            auto.Click(card_x, curr_y)

            # 动态等待：轮询检测文章阅读窗口唤起（最长 2.5 秒）
            opened_w = None
            t_detect = time.time()
            while time.time() - t_detect < 2.5:
                fw = u32.GetForegroundWindow()
                if is_wechat_article_window(fw) and (not main_hwnd or fw != main_hwnd):
                    opened_w = fw
                    break
                time.sleep(0.2)

            if opened_w:
                try:
                    u32.SetForegroundWindow(opened_w)
                    time.sleep(0.15)
                except Exception:
                    pass

                # 模拟全选并复制
                auto.SendKeys("{Ctrl}a")
                time.sleep(0.25)
                auto.SendKeys("{Ctrl}c")
                time.sleep(0.2)
                release_all_modifier_keys()

                # 动态等待：轮询检测剪贴板更新（最多等待 2.5 秒）
                wait_start = time.time()
                while time.time() - wait_start < 2.5:
                    if u32.GetClipboardSequenceNumber() != seq_before:
                        break
                    time.sleep(0.15)

                # 读取剪贴板特征进行去重判定
                clip_text = get_clipboard_text().strip()

                if clip_text:
                    first_line = clip_text.splitlines()[0].strip() if clip_text.splitlines() else ""
                    text_sig = hashlib.md5(clip_text[:300].encode("utf-8")).hexdigest()
                    art_title = first_line[:40] if first_line else f"文章_{text_sig[:8]}"

                    if text_sig in harvested_signatures:
                        print(f"   ⏭️ [重复跳过] 《{art_title}》 之前已收割过，继续寻找下一篇...")
                        consecutive_misses += 1
                    else:
                        harvested_signatures.add(text_sig)
                        harvested_titles.append(art_title)
                        consecutive_misses = 0
                        print(f"   🎉 [收割成功 {len(harvested_signatures)}/{max_articles}] 《{art_title}》")
                else:
                    consecutive_misses += 1

                time.sleep(1.0)  # 留足后台提取、下载图片与 HTML 落盘时间

                # 安全关闭文章视窗
                fw_now = u32.GetForegroundWindow()
                if is_wechat_article_window(fw_now) and (not main_hwnd or fw_now != main_hwnd):
                    auto.SendKeys("{Ctrl}w")
                else:
                    auto.SendKeys("{Esc}")

                time.sleep(0.3)
                release_all_modifier_keys()
                time.sleep(0.4)

                curr_y += step_y
            else:
                # 未能触发卡片窗口（可能点到了空白缝隙或到底部）
                consecutive_misses += 1
                curr_y += step_y

            # 判断是否需要向下滚动翻页
            if curr_y > max_y or consecutive_misses >= 2:
                print("   📜 列表向下平滑滚动加载更多搜索结果...")
                auto.MoveTo(card_x, int((start_y + max_y) / 2))
                time.sleep(0.1)
                auto.WheelDown(6)
                time.sleep(1.0)
                curr_y = start_y + int(step_y * 0.3)
                if consecutive_misses >= 2:
                    consecutive_misses = 0

        crawled_count = len(harvested_signatures)
        print("\n======================================================================")
        print(f"🎉 [批量收割完成] 共全自动采集 {crawled_count} 篇独立公众号文章！")
        if consecutive_misses >= 8:
            print("ℹ️ 搜一搜结果列表已触底（无更多文章）。")
        print("📂 文章已保存到: output/articles/ 目录 (包含完整排版、高清图片、Markdown 及单文件 HTML)")
        print("🌐 您可以双击运行【启动知识库助手.bat】在浏览器中检索与阅读！")
        print("======================================================================\n")

    except KeyboardInterrupt:
        print("\n⚠️ [安全退出] 收到用户终止信号 (Ctrl+C)，正在恢复系统键盘按键与剪贴板...")
    finally:
        # 铁壁防御：全局退出与异常兜底，100% 释放系统修饰键，恢复剪贴板
        release_all_modifier_keys()
        stop_event.set()
        if user_orig_clipboard:
            try:
                set_clipboard_text(user_orig_clipboard)
            except Exception:
                pass
        release_all_modifier_keys()


def main():
    parser = argparse.ArgumentParser(description="微信公众号搜一搜全自动批量爬虫机器人")
    parser.add_argument("--keyword", "-k", type=str, default="", help="搜索关键词")
    parser.add_argument("--count", "-c", type=int, default=5, help="抓取文章数量 (支持 10、20、50 篇批量抓取，默认 5 篇)")
    parser.add_argument("--current", action="store_true", help="直接收割当前微信已搜出的结果列表")
    args = parser.parse_args()

    kw = args.keyword.strip()
    auto_search = True

    if not kw and not args.current:
        print("======================================================================")
        print("      🚀 微信公众号搜一搜 —— 全自动批量收割机 (WeChat Auto Crawler)")
        print("======================================================================")
        print("  💡 使用提示：")
        print("  【方式 1】输入关键词：机器人自动唤起微信并在搜一搜中检索后自动抓取；")
        print("  【方式 2】直接回车：机器人直接收割当前微信上您已搜出的搜一搜结果列表！")
        print("----------------------------------------------------------------------")
        kw = input("请输入搜索关键词 (或直接回车收割当前页面): ").strip()
        if not kw:
            auto_search = False
            print(">> 已选择【方式 2】：将直接对当前微信搜一搜列表进行批量抓取！")

        count_input = input("请输入需要抓取的文章篇数 [默认 5, 支持输入 10/20/50 批量滚动抓取]: ").strip()
        count = int(count_input) if count_input.isdigit() and int(count_input) > 0 else 5
    else:
        count = args.count
        if args.current:
            auto_search = False

    try:
        auto_crawl_wechat(keyword=kw, max_articles=count, auto_search=auto_search)
    except KeyboardInterrupt:
        print("\n👋 已安全终止任务。")
    finally:
        release_all_modifier_keys()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n👋 程序已安全退出。")
        release_all_modifier_keys()
        sys.exit(0)
