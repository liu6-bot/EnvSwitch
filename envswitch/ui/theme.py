"""界面主题。

tkinter 原生控件比较朴素，这里统一定义配色与字体，让整体观感接近现代桌面应用。
所有颜色都集中在 THEMES 里，改主题只需要动这一个字典。
"""

from __future__ import annotations

import platform
import tkinter as tk
from tkinter import font as tkfont
from typing import Dict

# --------------------------------------------------------------------------- #
# 字体
# --------------------------------------------------------------------------- #

_FONT_CANDIDATES = {
    "windows": ["Microsoft YaHei UI", "微软雅黑", "Segoe UI"],
    "darwin": ["PingFang SC", "Helvetica Neue"],
    "linux": ["Noto Sans CJK SC", "WenQuanYi Zen Hei", "DejaVu Sans"],
}

_MONO_CANDIDATES = ["Cascadia Mono", "Consolas", "Menlo", "DejaVu Sans Mono", "Courier New"]


def _pick(candidates) -> str:
    available = set(tkfont.families())
    for c in candidates:
        if c in available:
            return c
    return candidates[-1]


def ui_family() -> str:
    key = {"Windows": "windows", "Darwin": "darwin", "Linux": "linux"}.get(platform.system(), "linux")
    return _pick(_FONT_CANDIDATES[key])


def mono_family() -> str:
    return _pick(_MONO_CANDIDATES)


FAMILY = None      # 延迟初始化（需要 Tk root 存在）
MONO = None


def init_fonts(root: tk.Misc) -> None:
    global FAMILY, MONO
    if FAMILY is None:
        FAMILY = ui_family()
        MONO = mono_family()


def f(size: int = 10, weight: str = "normal") -> tuple:
    return (FAMILY or "TkDefaultFont", size, weight)


def mono(size: int = 9, weight: str = "normal") -> tuple:
    return (MONO or "Courier New", size, weight)


# --------------------------------------------------------------------------- #
# 配色
# --------------------------------------------------------------------------- #

THEMES: Dict[str, Dict[str, str]] = {
    "light": {
        "bg": "#f4f6f9",
        "panel": "#ffffff",
        "sidebar": "#eef1f6",
        "sidebar_hover": "#e3e8f0",
        "sidebar_active": "#dbe7fe",
        "sidebar_text": "#1f2937",
        "accent": "#2563eb",
        "accent_hover": "#1d4ed8",
        "accent_fg": "#ffffff",
        "text": "#1f2937",
        "text_muted": "#6b7280",
        "border": "#d9dee7",
        "row_alt": "#fafbfd",
        "row_cur": "#e0f2fe",
        "row_cur_fg": "#0369a1",
        "ok": "#15803d",
        "warn": "#b45309",
        "err": "#b91c1c",
        "log_bg": "#1e2530",
        "log_fg": "#cbd5e1",
        "header": "#111827",
        "badge_bg": "#dbeafe",
        "badge_fg": "#1e40af",
    },
    "dark": {
        "bg": "#14181f",
        "panel": "#1b212b",
        "sidebar": "#171c25",
        "sidebar_hover": "#222936",
        "sidebar_active": "#23324d",
        "sidebar_text": "#e5e7eb",
        "accent": "#3b82f6",
        "accent_hover": "#2563eb",
        "accent_fg": "#ffffff",
        "text": "#e5e7eb",
        "text_muted": "#9ca3af",
        "border": "#2b3340",
        "row_alt": "#1f2632",
        "row_cur": "#1e3a5f",
        "row_cur_fg": "#7dd3fc",
        "ok": "#4ade80",
        "warn": "#fbbf24",
        "err": "#f87171",
        "log_bg": "#0f131a",
        "log_fg": "#cbd5e1",
        "header": "#f3f4f6",
        "badge_bg": "#1e3a8a",
        "badge_fg": "#bfdbfe",
    },
}

_current = "light"


def colors() -> Dict[str, str]:
    return THEMES[_current]


def set_theme(name: str) -> None:
    global _current
    _current = name if name in THEMES else "light"


def theme_name() -> str:
    return _current


# --------------------------------------------------------------------------- #
# ttk 样式
# --------------------------------------------------------------------------- #


def apply_ttk_style(style, c: Dict[str, str]) -> None:
    """把主题色刷进 ttk 控件。"""
    try:
        style.theme_use("clam")
    except Exception:  # noqa: BLE001
        pass

    style.configure(".", background=c["panel"], foreground=c["text"],
                    fieldbackground=c["panel"], bordercolor=c["border"],
                    font=f(10))
    style.configure("TFrame", background=c["panel"])
    style.configure("Panel.TFrame", background=c["bg"])
    style.configure("Card.TFrame", background=c["panel"], relief="flat")
    style.configure("TLabel", background=c["panel"], foreground=c["text"], font=f(10))
    style.configure("Muted.TLabel", foreground=c["text_muted"], font=f(9))
    style.configure("Header.TLabel", font=f(16, "bold"), foreground=c["header"])
    style.configure("Sub.TLabel", foreground=c["text_muted"], font=f(9))

    style.configure("TButton", padding=(12, 6), font=f(10),
                    background=c["panel"], foreground=c["text"], bordercolor=c["border"])
    style.map("TButton",
              background=[("active", c["sidebar_hover"]), ("pressed", c["sidebar_hover"])],
              bordercolor=[("focus", c["accent"])])
    style.configure("Accent.TButton", background=c["accent"], foreground=c["accent_fg"],
                    bordercolor=c["accent"], padding=(14, 7), font=f(10, "bold"))
    style.map("Accent.TButton",
              background=[("active", c["accent_hover"]), ("pressed", c["accent_hover"])])
    style.configure("Danger.TButton", foreground=c["err"], padding=(12, 6))

    style.configure("TNotebook", background=c["bg"], borderwidth=0)
    style.configure("TNotebook.Tab", padding=(14, 6), font=f(10))

    style.configure("Treeview", background=c["panel"], fieldbackground=c["panel"],
                    foreground=c["text"], bordercolor=c["border"], rowheight=30, font=f(10))
    style.configure("Treeview.Heading", background=c["sidebar"], foreground=c["text"],
                    font=f(10, "bold"), bordercolor=c["border"], relief="flat")
    style.map("Treeview", background=[("selected", c["sidebar_active"])],
              foreground=[("selected", c["text"])])

    style.configure("TCheckbutton", background=c["panel"], foreground=c["text"], font=f(10))
    style.map("TCheckbutton", background=[("active", c["panel"])])
    style.configure("TRadiobutton", background=c["panel"], foreground=c["text"], font=f(10))
    style.map("TRadiobutton", background=[("active", c["panel"])])
    style.configure("TEntry", padding=(6, 4), font=f(10))
    style.configure("TCombobox", padding=(6, 4), font=f(10))
    style.configure("TSeparator", background=c["border"])
    style.configure("Vertical.TScrollbar", background=c["sidebar"], bordercolor=c["border"])
    style.configure("TLabelframe", background=c["panel"], foreground=c["text"])
    style.configure("TLabelframe.Label", background=c["panel"], foreground=c["text_muted"], font=f(9, "bold"))
