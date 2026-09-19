#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键打包脚本。

用法（在项目根目录执行）：
    python build_exe.py               # 单文件版（推荐分发）
    python build_exe.py --onedir      # 目录版（启动更快）
    python build_exe.py --check       # 只检查环境，不打包

依赖：pip install pyinstaller
    Windows：官方 Python 安装包自带 tkinter，无需额外操作
    Ubuntu： sudo apt install python3-tk python3-dev
    macOS：  官方安装包自带
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEC = ROOT / "EnvSwitch.spec"
DIST = ROOT / "dist"


def utf8_console() -> None:
    """把 stdout/stderr 切到 UTF-8，避免中文提示把脚本自己弄崩。

    非中文版 Windows 的控制台编码是 cp1252，print 中文会抛 UnicodeEncodeError；
    GitHub Actions 的 windows-latest 正是这种情况（打包明明成功了，却在打印
    「打包完成：…」这一步挂掉）。这里统一改成 UTF-8，装不下的字符降级成 '?'，
    只影响输出观感，绝不会中断流程。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001  —— 老版本或已被重定向的流，忽略
            pass


def sh(cmd, **kw):
    print("$", " ".join(str(c) for c in cmd))
    return subprocess.run(cmd, **kw).returncode


def check() -> int:
    print(f"Python : {sys.version.split()[0]}  ({sys.executable})")
    print(f"系统   : {platform.system()} {platform.machine()}")
    try:
        import tkinter

        print(f"tkinter: OK  (Tcl/Tk {tkinter.TkVersion})")
    except Exception as e:  # noqa: BLE001
        print(f"tkinter: 缺失！ {e}")
        print("  Ubuntu/Debian: sudo apt install python3-tk")
        print("  Fedora:        sudo dnf install python3-tkinter")
        print("  Windows/macOS: 用官方安装包重装并勾选 tcl/tk")
        return 1
    if not shutil.which("pyinstaller"):
        print("pyinstaller: 未安装，执行 pip install pyinstaller")
        return 1
    ver = subprocess.run(["pyinstaller", "--version"], capture_output=True, text=True)
    print(f"pyinstaller: {ver.stdout.strip() or 'OK'}")
    print("\n环境检查通过，可以执行： python build_exe.py")
    return 0


def build(onedir: bool) -> int:
    if not SPEC.exists():
        print("找不到 EnvSwitch.spec")
        return 1
    env = dict(os.environ)
    env["ENVSWITCH_ONEDIR"] = "1" if onedir else "0"
    code = sh([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(SPEC)], env=env)
    if code != 0:
        print("\n打包失败，请把上面的完整输出贴到 issue 里")
        return code

    if onedir:
        out = DIST / "EnvSwitch" / ("EnvSwitch.exe" if os.name == "nt" else "EnvSwitch")
    else:
        out = DIST / ("EnvSwitch.exe" if os.name == "nt" else "EnvSwitch")
    print("\n" + "=" * 60)
    if out.exists():
        size = out.stat().st_size / 1024 / 1024
        print(f"打包完成：{out}  ({size:.1f} MB)")
    else:
        print(f"打包完成，产物在 {DIST} 目录")
    print("发布前记得先在本机跑一遍：扫描 → 切换 → 还原")
    return 0


def main() -> int:
    utf8_console()
    ap = argparse.ArgumentParser(description="EnvSwitch 打包脚本")
    ap.add_argument("--onedir", action="store_true", help="打目录版而不是单文件版")
    ap.add_argument("--check", action="store_true", help="只检查打包环境")
    args = ap.parse_args()

    if args.check:
        return check()
    if not os.path.isdir(ROOT / "assets"):
        (ROOT / "assets").mkdir(exist_ok=True)
        # 仓库里没带图标时现生成一个
        icon_tool = ROOT / "tools" / "make_icon.py"
        if icon_tool.exists():
            sh([sys.executable, str(icon_tool)])
    return build(args.onedir)


if __name__ == "__main__":
    raise SystemExit(main())
