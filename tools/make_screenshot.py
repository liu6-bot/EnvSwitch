"""抓取 EnvSwitch 主界面的真机截图，保存到 docs/screenshot.png（供 README 使用）。

用法：
    python tools/make_screenshot.py [输出路径] [--tool java]

会做的事情：把配置里的「默认选中工具」临时改成 --tool 指定的那个 -> 启动 main.py
-> 等窗口出现并完成扫描 -> 把窗口提到最前 -> 抓图（自动剔除窗口阴影）-> 关窗口 -> 还原配置。

只在 Windows 上有意义；抓图依赖 Pillow（`pip install pillow`）。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(ROOT, "docs", "screenshot.png")
TITLE = "EnvSwitch · 多版本运行环境切换器"
CONFIG = os.path.join(os.path.expanduser("~"), ".envswitch", "config.json")

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi

DWMWA_EXTENDED_FRAME_BOUNDS = 9
SW_RESTORE = 9


def _find_window(title: str, timeout: float = 45.0) -> int:
    """轮询等待标题匹配的顶层窗口出现，返回 hwnd（找不到返回 0）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        hwnd = user32.FindWindowW(None, title)
        if hwnd:
            return hwnd
        time.sleep(0.4)
    return 0


def _frame_bounds(hwnd: int):
    """取窗口可见边框（不含投影阴影），拿不到就退回 GetWindowRect。"""
    rc = wt.RECT()
    ok = dwmapi.DwmGetWindowAttribute(
        wt.HWND(hwnd), ctypes.c_uint(DWMWA_EXTENDED_FRAME_BOUNDS),
        ctypes.byref(rc), ctypes.sizeof(rc),
    )
    if ok != 0:
        if not user32.GetWindowRect(hwnd, ctypes.byref(rc)):
            return None
    if rc.right - rc.left < 100 or rc.bottom - rc.top < 100:
        return None
    return (rc.left, rc.top, rc.right, rc.bottom)


def _set_last_tool(tool_id: str):
    """把配置里的默认选中工具改成 tool_id，返回原值以便还原。"""
    try:
        with open(CONFIG, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None, None
    old = (data.get("settings") or {}).get("last_tool")
    data.setdefault("settings", {})["last_tool"] = tool_id
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return old, data


def _restore(entry) -> None:
    old, data = entry
    if not data:
        return
    data.setdefault("settings", {})["last_tool"] = old
    try:
        with open(CONFIG, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    tool_id = None
    if "--tool" in argv:
        i = argv.index("--tool")
        if i + 1 < len(argv):
            tool_id = argv[i + 1]
            del argv[i:i + 2]
    out = argv[0] if argv else DEFAULT_OUT
    os.makedirs(os.path.dirname(out), exist_ok=True)

    try:
        from PIL import ImageGrab
    except ImportError:
        print("缺少 Pillow，先执行：pip install pillow")
        return 2

    saved = _set_last_tool(tool_id) if tool_id else (None, None)
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "main.py")], cwd=ROOT)
    try:
        hwnd = _find_window(TITLE)
        if not hwnd:
            print("等待超时：没找到标题为 %r 的窗口" % TITLE)
            return 1

        # 给扫描/探测留出时间，界面稳定后再抓，否则会拍到空列表
        time.sleep(9)
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
        time.sleep(1.5)

        box = _frame_bounds(hwnd)
        if not box:
            print("拿不到窗口尺寸")
            return 1
        img = ImageGrab.grab(bbox=box, all_screens=True)
        img.save(out)
        print("已保存 %s  (%dx%d, %.0f KB)" % (out, img.width, img.height, os.path.getsize(out) / 1024))
        return 0
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except Exception:
            proc.kill()
        _restore(saved)


if __name__ == "__main__":
    raise SystemExit(main())
