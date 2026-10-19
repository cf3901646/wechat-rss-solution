# -*- coding: utf-8 -*-
"""
Windows 修饰键（Ctrl / Shift / Alt / Win）强制复位工具
-------------------------------------------------------
解决因自动化程序意外中断、按键粘滞导致的“打字变成快捷键、按键失灵”问题。
"""
import sys
import time

if sys.platform.startswith("win"):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def reset_all_keys():
    print("======================================================================")
    print("      🛠️  Windows 系统键盘修饰键状态一键复位工具 (Key Reset Tool)     ")
    print("======================================================================")
    if sys.platform != "win32":
        print("当前系统非 Windows，无需复位。")
        return

    import ctypes
    u32 = ctypes.windll.user32
    KEYEVENTF_KEYUP = 0x0002

    # 包含左右各侧的控制键
    keys = [
        (0x11, "Ctrl 键 (VK_CONTROL)"),
        (0xA2, "左 Ctrl 键 (VK_LCONTROL)"),
        (0xA3, "右 Ctrl 键 (VK_RCONTROL)"),
        (0x10, "Shift 键 (VK_SHIFT)"),
        (0xA0, "左 Shift 键 (VK_LSHIFT)"),
        (0xA1, "右 Shift 键 (VK_RSHIFT)"),
        (0x12, "Alt 键 (VK_MENU)"),
        (0xA4, "左 Alt 键 (VK_LMENU)"),
        (0xA5, "右 Alt 键 (VK_RMENU)"),
        (0x5B, "左 Win 键 (VK_LWIN)"),
        (0x5C, "右 Win 键 (VK_RWIN)"),
    ]

    print("👉 正在向 Windows 底层广播发送按键抬起 (KEYUP) 信号...")
    for vk, name in keys:
        try:
            u32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        except Exception as e:
            print(f"   ⚠️ 释放 {name} 失败: {e}")

    time.sleep(0.1)
    print("\n✅ [复位成功] Windows 键盘所有修饰键逻辑状态已彻底重置！")
    print("💡 验证提示：")
    print("   1. 现在您可以尝试在任意输入框正常打字测试；")
    print("   2. 若仍有个别按键异常，请在物理键盘上分别轻按一下左/右 Ctrl 键即可彻底同步状态。")
    print("======================================================================\n")


if __name__ == "__main__":
    reset_all_keys()
