# -*- coding: utf-8 -*-
"""
windows/*.bat 的生成器。

这两个 .bat 必须是 **UTF-8 + CRLF**：
- UTF-8 配合脚本里的 `chcp 65001`，中文提示才不会乱码；
- CRLF 是 cmd.exe 的要求，LF 行尾在含 if/goto 块时会出现难以排查的行为。

手工编辑 .bat 容易把行尾改成 LF，所以内容改动请改这里再重新运行：
    .venv\\Scripts\\python.exe scripts\\fix_bat.py
"""

import os

# 依赖缺失时给出可操作的提示，而不是让 cmd 抛 "系统找不到指定的路径"
VENV_GUARD = """if not exist ".venv\\Scripts\\python.exe" (
    echo [错误] 未找到项目虚拟环境: .venv\\Scripts\\python.exe
    echo.
    echo 请先在项目根目录执行以下两条命令安装依赖：
    echo     python -m venv .venv
    echo     .venv\\Scripts\\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)
"""

login_content = """@echo off
chcp 65001 > nul
title 微信读书扫码登录工具
cd /d "%~dp0.."
echo ====================================================
echo       微信读书扫码登录向导 (本地即时交互)
echo ====================================================
echo.
__VENV_GUARD__".venv\\Scripts\\python.exe" "scripts\\run_weread_login.py"
echo.
echo ====================================================
echo 任务已结束，按任意键退出窗口...
pause
"""

sync_content = """@echo off
chcp 65001 > nul
title 微信公众号文章 RSS 同步工具
cd /d "%~dp0.."
echo ====================================================
echo        微信公众号文章自动同步与 Markdown 归档
echo ====================================================
echo.
__VENV_GUARD__".venv\\Scripts\\python.exe" "scripts\\run_sync.py"
echo.
echo ====================================================
echo 同步任务已结束，日志见 logs\\sync.log，按任意键退出窗口...
pause
"""


def write_bat(path: str, content: str):
    """以 UTF-8 + CRLF 写出 .bat"""
    content = content.replace("__VENV_GUARD__", VENV_GUARD)
    normalized = content.replace("\r\n", "\n").replace("\n", "\r\n")
    with open(path, "wb") as f:
        f.write(normalized.encode("utf-8"))
    print(f"written: {path}")


def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    win_dir = os.path.join(base_dir, "windows")

    write_bat(os.path.join(win_dir, "login_wechat.bat"), login_content)
    write_bat(os.path.join(win_dir, "start_sync.bat"), sync_content)
    print("CRLF conversion completed successfully!")


if __name__ == "__main__":
    main()
