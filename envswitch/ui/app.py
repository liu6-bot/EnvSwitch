"""主界面。

布局：左侧工具导航（内置 + 自定义，可增删） / 右侧该工具的所有安装 / 底部日志。
核心交互只有一个：选中一行，点「切换为全局默认」。
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional

from ..core.models import Install, ToolDef
from . import theme as T
from .dialogs import AboutDialog, AuditDialog, SettingsDialog, ToolEditor


def resource_path(name: str) -> Path:
    """定位 assets 目录下的资源（兼容 PyInstaller 打包后）。"""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).resolve().parents[1]
    return base / "assets" / name


class App(tk.Tk):
    def __init__(self, mgr):
        super().__init__()
        self.mgr = mgr
        T.set_theme(mgr.config.setting("theme", "light"))
        T.init_fonts(self)
        self.c = T.colors()

        self.tool_id: Optional[str] = None
        self.installs: List[Install] = []
        self.nav: Dict[str, dict] = {}
        self._scanning = False

        self.title("EnvSwitch · 多版本运行环境切换器")
        self.geometry("1080x700")
        self.minsize(900, 600)
        self.configure(background=self.c["bg"])
        self._set_icon()
        self._center()

        self._build_menu()
        self._build_layout()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<F5>", lambda e: self.refresh(force=True))
        self.bind("<Control-n>", lambda e: self.new_tool())

        last = mgr.config.setting("last_tool")
        tools = mgr.tools()
        # 侧边栏必须在启动时就渲染，否则工具列表（Java/Python/...）一片空白
        self.rebuild_sidebar()
        first = last if any(t.id == last for t in tools) else (tools[0].id if tools else None)
        if first:
            self.select_tool(first)
        else:
            self._log("还没有任何工具，点左下角「＋ 新建工具」添加一个")
        # 启动 1.5 秒后做一次体检，有问题就在顶部挂提示条
        self.after(1500, self.run_audit)

    # ------------------------------ 基础 ------------------------------ #

    def _center(self):
        self.update_idletasks()
        w, h = 1080, 700
        x = max(0, (self.winfo_screenwidth() - w) // 2)
        y = max(0, (self.winfo_screenheight() - h) // 3)
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _set_icon(self):
        try:
            png = resource_path("icon.png")
            if png.exists():
                self.iconphoto(True, tk.PhotoImage(file=str(png)))
            ico = resource_path("icon.ico")
            if ico.exists() and os.name == "nt":
                self.iconbitmap(str(ico))
        except Exception:  # noqa: BLE001
            pass

    def _build_menu(self):
        bar = tk.Menu(self, tearoff=0, font=T.f(10))
        self.configure(menu=bar)

        m_file = tk.Menu(bar, tearoff=0, font=T.f(10))
        bar.add_cascade(label="文件", menu=m_file)
        m_file.add_command(label="新建工具…", command=self.new_tool, accelerator="Ctrl+N")
        m_file.add_command(label="编辑当前工具…", command=self.edit_tool)
        m_file.add_separator()
        m_file.add_command(label="导出配置…", command=self._export_cfg)
        m_file.add_command(label="导入配置…", command=self._import_cfg)
        m_file.add_separator()
        m_file.add_command(label="退出", command=self._on_close)

        m_tool = tk.Menu(bar, tearoff=0, font=T.f(10))
        bar.add_cascade(label="操作", menu=m_tool)
        m_tool.add_command(label="重新扫描", command=lambda: self.refresh(force=True), accelerator="F5")
        m_tool.add_command(label="切换为全局默认", command=self.do_switch)
        m_tool.add_command(label="打开已生效终端", command=self.do_terminal)
        m_tool.add_command(label="写入项目版本文件…", command=self.do_pin)
        m_tool.add_command(label="环境体检…", command=self.open_audit)
        m_tool.add_separator()
        m_tool.add_command(label="还原此工具的环境变量", command=self.do_revert)

        m_help = tk.Menu(bar, tearoff=0, font=T.f(10))
        bar.add_cascade(label="帮助", menu=m_help)
        m_help.add_command(label="设置…", command=self.open_settings)
        m_help.add_command(label="诊断信息", command=self.show_diagnose)
        m_help.add_separator()
        m_help.add_command(label="关于", command=lambda: AboutDialog(self))

    def _build_layout(self):
        c = self.c
        root = ttk.Frame(self, style="Panel.TFrame")
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)

        # ---------- 侧边栏 ---------- #
        side = tk.Frame(root, width=208, background=c["sidebar"])
        side.grid(row=0, column=0, sticky="nsew")
        side.grid_propagate(False)
        side.rowconfigure(1, weight=1)
        self.side = side

        brand = tk.Frame(side, background=c["sidebar"])
        brand.grid(row=0, column=0, sticky="ew", padx=12, pady=(16, 10))
        tk.Label(brand, text="🔀 EnvSwitch", font=T.f(13, "bold"),
                 background=c["sidebar"], foreground=c["text"]).pack(anchor="w")
        tk.Label(brand, text="版本切换器", font=T.f(9),
                 background=c["sidebar"], foreground=c["text_muted"]).pack(anchor="w")

        self.nav_box = tk.Frame(side, background=c["sidebar"])
        self.nav_box.grid(row=1, column=0, sticky="nsew")

        foot = tk.Frame(side, background=c["sidebar"])
        foot.grid(row=2, column=0, sticky="ew", padx=10, pady=10)
        tk.Button(foot, text="＋ 新建工具", font=T.f(10, "bold"), relief="flat", cursor="hand2",
                  background=c["accent"], foreground=c["accent_fg"], activebackground=c["accent_hover"],
                  activeforeground="#fff", borderwidth=0, padx=10, pady=8,
                  command=self.new_tool).pack(fill="x")
        tk.Button(foot, text="⚙  设置", font=T.f(10), relief="flat", cursor="hand2",
                  background=c["sidebar"], foreground=c["text"],
                  activebackground=c["sidebar_hover"], borderwidth=0, padx=10, pady=8, anchor="w",
                  command=self.open_settings).pack(fill="x", pady=(6, 0))

        # ---------- 主区 ---------- #
        main = ttk.Frame(root, style="Panel.TFrame", padding=(16, 14, 16, 10))
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)

        # 标题行
        head = ttk.Frame(main, style="Panel.TFrame")
        head.grid(row=0, column=0, sticky="ew")
        self.h_icon = tk.Label(head, text="◆", font=T.f(18), background=c["bg"], foreground=c["text"])
        self.h_icon.pack(side="left")
        self.h_name = tk.Label(head, text="—", font=T.f(16, "bold"), background=c["bg"], foreground=c["header"])
        self.h_name.pack(side="left", padx=(8, 12))
        self.h_badge = tk.Label(head, text="", font=T.f(10, "bold"), background=c["badge_bg"],
                                foreground=c["badge_fg"], padx=10, pady=3)
        self.h_badge.pack(side="left")
        self.h_count = tk.Label(head, text="", font=T.f(9), background=c["bg"], foreground=c["text_muted"])
        self.h_count.pack(side="left", padx=10)

        ttk.Button(head, text="重新扫描", command=lambda: self.refresh(force=True)).pack(side="right")
        self.h_note = tk.Label(head, text="", font=T.f(9), background=c["bg"], foreground=c["text_muted"])
        self.h_note.pack(side="right", padx=10)

        # 工具栏
        bar = ttk.Frame(main, style="Panel.TFrame")
        bar.grid(row=1, column=0, sticky="ew", pady=(12, 8))
        ttk.Button(bar, text="▶  切换为全局默认", style="Accent.TButton", command=self.do_switch).pack(side="left")
        ttk.Button(bar, text="🖥 打开已生效终端", command=self.do_terminal).pack(side="left", padx=6)
        ttk.Button(bar, text="📌 写入项目版本文件", command=self.do_pin).pack(side="left")
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=10, pady=2)
        ttk.Button(bar, text="＋ 添加路径", command=self.do_add).pack(side="left")
        ttk.Button(bar, text="－ 移除", command=self.do_remove).pack(side="left", padx=6)
        ttk.Button(bar, text="📋 复制路径", command=self.do_copy).pack(side="left")
        ttk.Button(bar, text="📂 打开目录", command=self.do_open_dir).pack(side="left", padx=6)
        ttk.Button(bar, text="🩺 环境体检", command=self.open_audit).pack(side="left")
        ttk.Button(bar, text="↩ 还原", style="Danger.TButton", command=self.do_revert).pack(side="right")
        ttk.Button(bar, text="✎ 编辑工具", command=self.edit_tool).pack(side="right", padx=6)
        self.buttons = [w for w in bar.winfo_children() if isinstance(w, ttk.Button)]

        # 体检提示条（有问题时才显示）
        self.banner = tk.Frame(main, background=c["warn"], cursor="hand2")
        self.banner_text = tk.Label(self.banner, text="", font=T.f(10, "bold"),
                                    background=c["warn"], foreground="#ffffff",
                                    padx=10, pady=6, anchor="w")
        self.banner_text.pack(fill="x")
        self.banner.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        for w in (self.banner, self.banner_text):
            w.bind("<Button-1>", lambda e: self.open_audit())
        self.banner.grid_remove()

        # 列表
        wrap = ttk.Frame(main, style="Card.TFrame")
        wrap.grid(row=3, column=0, sticky="nsew")
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)

        cols = ("state", "version", "vendor", "path", "source")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="browse")
        heads = {"state": "", "version": "版本", "vendor": "来源 / 厂商", "path": "安装路径", "source": "来源"}
        widths = {"state": 92, "version": 170, "vendor": 140, "path": 400, "source": 90}
        for k in cols:
            self.tree.heading(k, text=heads[k])
            self.tree.column(k, width=widths[k], anchor="w" if k != "state" else "center",
                             stretch=(k == "path"))
        vs = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        self.tree.tag_configure("alt", background=self.c["row_alt"])
        self.tree.tag_configure("cur", background=self.c["row_cur"], foreground=self.c["row_cur_fg"])
        self.tree.bind("<Double-1>", lambda e: self.do_switch())
        self.tree.bind("<Button-3>", self._popup)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._update_header())

        self.empty = tk.Label(wrap, text="未检测到任何安装。\n可以点「＋ 添加路径」手动指定，或检查扫描目录设置。",
                              font=T.f(11), background=self.c["panel"], foreground=self.c["text_muted"],
                              justify="center")

        # 日志
        self.log_frame = ttk.Frame(main, style="Panel.TFrame")
        self.log_frame.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        bar2 = ttk.Frame(self.log_frame, style="Panel.TFrame")
        bar2.pack(fill="x")
        tk.Label(bar2, text="操作日志", font=T.f(9, "bold"), background=self.c["bg"],
                 foreground=self.c["text_muted"]).pack(side="left")
        ttk.Button(bar2, text="清空", width=6, command=lambda: self.log.delete("1.0", "end")).pack(side="right")
        self.log = tk.Text(self.log_frame, height=5, wrap="word", font=T.mono(9), relief="flat",
                           background=self.c["log_bg"], foreground=self.c["log_fg"],
                           insertbackground=self.c["log_fg"], padx=8, pady=6)
        self.log.pack(fill="x", pady=(4, 0))

        # 状态栏
        self.status = tk.Label(self, text="就绪", font=T.f(9), anchor="w",
                               background=self.c["sidebar"], foreground=self.c["text_muted"], padx=10, pady=4)
        self.status.pack(fill="x", side="bottom")

        if not self.mgr.config.setting("show_log", True):
            self.log_frame.grid_remove()

    # ------------------------------ 侧边栏 ------------------------------ #

    def rebuild_sidebar(self):
        for w in self.nav_box.winfo_children():
            w.destroy()
        self.nav.clear()
        for t in self.mgr.tools():
            self._nav_item(t)
        self._highlight_nav()
        # 工具列表变了，侧边栏计数需要重算
        self._counts_done = False
        self.rebuild_sidebar_counts()

    def _nav_item(self, tool: ToolDef):
        c = self.c
        row = tk.Frame(self.nav_box, background=c["sidebar"], cursor="hand2")
        row.pack(fill="x", pady=1)
        accent = tk.Frame(row, width=3, background=c["sidebar"])
        accent.pack(side="left", fill="y")
        inner = tk.Frame(row, background=c["sidebar"])
        inner.pack(side="left", fill="x", expand=True, padx=(9, 8), pady=7)
        icon = tk.Label(inner, text=tool.icon, font=T.f(12), background=c["sidebar"], foreground=c["text"])
        icon.pack(side="left")
        name = tk.Label(inner, text=tool.name, font=T.f(10), background=c["sidebar"], foreground=c["sidebar_text"],
                        anchor="w")
        name.pack(side="left", padx=(8, 0))
        cnt = tk.Label(inner, text="", font=T.f(9), background=c["sidebar"], foreground=c["text_muted"])
        cnt.pack(side="right")

        widgets = [row, inner, icon, name, cnt]
        for w in widgets:
            w.bind("<Button-1>", lambda e, tid=tool.id: self.select_tool(tid))
        for w in (row, inner):
            w.bind("<Enter>", lambda e, r=row: self._nav_hover(r, True))
            w.bind("<Leave>", lambda e, r=row: self._nav_hover(r, False))
        self.nav[tool.id] = {"row": row, "accent": accent, "inner": inner,
                             "icon": icon, "name": name, "cnt": cnt}

    def _nav_hover(self, row: tk.Frame, enter: bool):
        tid = self.tool_id
        if self.nav.get(tid, {}).get("row") is row:
            return
        color = self.c["sidebar_hover"] if enter else self.c["sidebar"]
        self._paint(row, color)

    def _paint(self, row: tk.Frame, color: str):
        row.configure(background=color)
        for w in row.winfo_children():
            try:
                if isinstance(w, tk.Frame):
                    w.configure(background=color)
                    for x in w.winfo_children():
                        x.configure(background=color)
                else:
                    w.configure(background=color)
            except Exception:  # noqa: BLE001
                pass

    def _highlight_nav(self):
        c = self.c
        for tid, item in self.nav.items():
            active = tid == self.tool_id
            color = c["sidebar_active"] if active else c["sidebar"]
            self._paint(item["row"], color)
            item["accent"].configure(background=c["accent"] if active else c["sidebar"])
            item["name"].configure(font=T.f(10, "bold") if active else T.f(10))

    # ------------------------------ 选择工具 ------------------------------ #

    def select_tool(self, tool_id: str):
        if self.tool_id == tool_id and self.installs:
            self._highlight_nav()
            return
        self.tool_id = tool_id
        self.mgr.config.set_setting("last_tool", tool_id)
        self._highlight_nav()
        tool = self.mgr.tool(tool_id)
        if not tool:
            return
        self.h_icon.configure(text=tool.icon)
        self.h_name.configure(text=tool.name)
        self.h_note.configure(text=tool.home_var or "")
        self.installs = []
        self._render([])
        self.refresh(force=False)

    def _update_header(self):
        cur = next((i for i in self.installs if i.current), None)
        if cur:
            self.h_badge.configure(text=f"当前：{cur.version or os.path.basename(cur.home)}")
        else:
            # 没有真正生效的版本时不要装没事：变量指向了谁就如实说出来
            dft = next((i for i in self.installs if getattr(i, "is_default", False)), None)
            if dft:
                self.h_badge.configure(
                    text=f"当前：未生效（变量指向 {dft.version or os.path.basename(dft.home)}）")
            else:
                self.h_badge.configure(text="当前：未生效")
        self.h_badge.pack(side="left")
        self.h_count.configure(text=f"共 {len(self.installs)} 个")

    # ------------------------------ 扫描 ------------------------------ #

    def refresh(self, force: bool = False):
        if not self.tool_id or self._scanning:
            return
        self._scanning = True
        self._set_busy(True)
        self._log(f"扫描中：{self.tool_id} …")
        tid = self.tool_id

        def work():
            try:
                installs = self.mgr.scan(tid, refresh=force)
            except Exception as e:  # noqa: BLE001
                installs = []
                err = str(e)
            else:
                err = ""
            self.after(0, lambda: self._scan_done(tid, installs, err))

        threading.Thread(target=work, daemon=True).start()

    def _scan_done(self, tid: str, installs: List[Install], err: str):
        self._scanning = False
        self._set_busy(False)
        if tid != self.tool_id:
            return
        self.installs = installs
        self._render(installs)
        if err:
            self._log("✗ 扫描出错：" + err)
            self.status.configure(text="扫描出错")
            return
        cur = next((i for i in installs if i.current), None)
        self._log(f"✓ 扫描完成，共 {len(installs)} 个安装"
                  + (f"，当前生效：{cur.version}" if cur else "（未检测到生效版本）"))
        self.status.configure(text=f"就绪 · {len(installs)} 个安装")
        item = self.nav.get(tid)
        if item:
            item["cnt"].configure(text=str(len(installs)) if installs else "")
        # 侧边栏"有几个版本"只在第一次扫描后统计一次即可；
        # 否则每次刷新都会把全部工具重扫一遍，白白多起一批子进程
        if not getattr(self, "_counts_done", False):
            self._counts_done = True
            self.rebuild_sidebar_counts()

    def rebuild_sidebar_counts(self):
        def work():
            counts = {}
            for t in self.mgr.tools():
                try:
                    counts[t.id] = len(self.mgr.scan(t.id))
                except Exception:  # noqa: BLE001
                    counts[t.id] = 0
            self.after(0, lambda: self._apply_counts(counts))

        threading.Thread(target=work, daemon=True).start()

    def _apply_counts(self, counts: Dict[str, int]):
        for tid, n in counts.items():
            item = self.nav.get(tid)
            if item:
                item["cnt"].configure(text=str(n) if n else "")

    def _set_busy(self, busy: bool):
        state = "disabled" if busy else "normal"
        for b in getattr(self, "buttons", []):
            try:
                b.configure(state=state)
            except Exception:  # noqa: BLE001
                pass
        self.configure(cursor="watch" if busy else "")

    def _render(self, installs: List[Install]):
        for i in self.tree.get_children():
            self.tree.delete(i)
        if not installs:
            self.tree.grid_remove()
            self.empty.grid(row=0, column=0, sticky="nsew")
        else:
            self.empty.grid_remove()
            self.tree.grid()
            for idx, ins in enumerate(installs):
                tags = ("cur",) if ins.current else (("alt",) if idx % 2 else ())
                src = {"auto": "自动扫描", "custom": "手动添加", "env": "环境变量",
                       "path": "PATH", "store": "商店安装",
                       "remembered": "历史记录"}.get(ins.source, ins.source)
                # 「当前」= 命令行真正命中的那个（全局唯一）；
                # 「变量指向」= 主目录变量说用它、但 PATH 里没轮到它 —— 两个都标出来，
                # 用户才能一眼看出"变量说 A、实际跑的是 B"这种冲突。
                if ins.current:
                    state = "● 当前"
                elif getattr(ins, "is_default", False):
                    state = "○ 变量指向"
                else:
                    state = ""
                self.tree.insert("", "end", iid=str(idx), tags=tags, values=(
                    state,
                    ins.version or "未知版本",
                    ins.vendor or "",
                    ins.home,
                    src,
                ))
        self._update_header()

    # ------------------------------ 选中项 ------------------------------ #

    def selected(self) -> Optional[Install]:
        sel = self.tree.selection()
        if not sel or not self.installs:
            return None
        try:
            return self.installs[int(sel[0])]
        except (ValueError, IndexError):
            return None

    def _popup(self, event):
        row = self.tree.identify_row(event.y)
        if row:
            self.tree.selection_set(row)
        menu = tk.Menu(self, tearoff=0, font=T.f(10))
        menu.add_command(label="切换为全局默认", command=self.do_switch)
        menu.add_command(label="打开已生效终端", command=self.do_terminal)
        menu.add_separator()
        menu.add_command(label="复制路径", command=self.do_copy)
        menu.add_command(label="打开安装目录", command=self.do_open_dir)
        menu.add_separator()
        menu.add_command(label="写入项目版本文件…", command=self.do_pin)
        menu.add_command(label="移除此条目", command=self.do_remove)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # ------------------------------ 动作 ------------------------------ #

    def do_switch(self):
        ins = self.selected()
        tool = self.mgr.tool(self.tool_id)
        if not ins or not tool:
            messagebox.showinfo("提示", "请先在列表里选中一个版本")
            return
        if ins.current:
            if not messagebox.askyesno("确认", f"{ins.title(tool)} 已经是当前生效版本，仍要重新写入吗？"):
                return
        elif self.mgr.config.setting("confirm_switch", True):
            if not messagebox.askyesno(
                "确认切换",
                f"把 {tool.name} 的全局默认版本切换为：\n\n  {ins.title(tool)}\n  {ins.home}\n\n"
                "已打开的终端 / IDE 需要重开才会生效。继续？",
            ):
                return
        ok, msg, warns = self.mgr.use(tool.id, ins)
        self._log(("✓ " if ok else "✗ ") + msg.replace("\n", " | "))
        for w in warns:
            self._log("⚠ " + w.replace("\n", " | "))
        if not ok:
            messagebox.showerror("切换失败", msg)
            return
        if warns:
            need_fix = any(("系统级 PATH" in w) or ("挡住" in w) or ("优先级" in w) for w in warns)
            if need_fix and messagebox.askyesno(
                "切换已写入，但可能不生效",
                "检测到系统级 PATH 冲突：Windows 会先匹配系统 PATH，"
                "命令行里跑到的可能仍是旧版本。\n\n"
                "是否现在清理这些冲突条目？\n（需要管理员权限，会自动先备份系统 PATH，"
                "之后可在「环境体检」里还原）",
            ):
                self.fix_system_conflicts()
            else:
                messagebox.showwarning("切换完成，但有提示", msg + "\n\n" + "\n\n".join(warns))
        else:
            self.status.configure(text="切换完成")
        if self.mgr.config.setting("launch_terminal", False):
            self.do_terminal()
        self.refresh(force=True)
        self.run_audit()

    # ------------------------------ 环境体检 ------------------------------ #

    def open_audit(self):
        # 传 issues=None，让对话框自己在后台线程里检测，避免卡住主界面
        AuditDialog(self, self.mgr, on_log=self._log)
        self.refresh(force=True)
        self.run_audit()

    def run_audit(self):
        """后台跑一次体检，用结果更新顶部提示条。"""
        if getattr(self, "_audit_running", False):
            return
        self._audit_running = True

        def work():
            try:
                from ..core.audit import audit as run_audit

                issues = run_audit(self.mgr)
            except Exception:  # noqa: BLE001
                issues = []
            self.after(0, lambda: self._audit_done(issues))

        threading.Thread(target=work, daemon=True).start()

    def _audit_done(self, issues):
        self._audit_running = False
        self.issues = issues
        if not issues:
            self.banner.grid_remove()
            return
        conflicts = [i for i in issues if i.kind in ("blocked",)]
        text = f"⚠ 环境体检发现 {len(issues)} 个问题"
        if conflicts:
            text += f"，其中 {len(conflicts)} 个会导致切换不生效"
        text += "　→　点击查看并一键修复"
        self.banner_text.configure(text=text)
        self.banner.grid()

    def fix_system_conflicts(self):
        """清理系统级 PATH 冲突（可能弹 UAC）。"""

        def work():
            try:
                from ..core.elevate import fix_system_conflicts

                ok, msg, _removed = fix_system_conflicts(self.mgr, self.tool_id or "all")
            except Exception as e:  # noqa: BLE001
                ok, msg = False, str(e)
            self.after(0, lambda: self._after_system_fix(ok, msg))

        self._log("正在申请管理员权限清理系统 PATH 冲突…")
        threading.Thread(target=work, daemon=True).start()

    def _after_system_fix(self, ok: bool, msg: str):
        self._log(("✓ " if ok else "✗ ") + msg)
        messagebox.showinfo("完成" if ok else "失败", msg or "操作已结束")
        self.refresh(force=True)
        self.run_audit()

    def do_terminal(self):
        ins = self.selected()
        if not ins:
            messagebox.showinfo("提示", "请先在列表里选中一个版本")
            return
        ok, msg = self.mgr.open_terminal(self.tool_id, ins, os.getcwd())
        self._log(("✓ " if ok else "✗ ") + msg)
        if not ok:
            messagebox.showwarning("打开终端失败", msg)

    def do_add(self):
        tool = self.mgr.tool(self.tool_id)
        if not tool:
            return
        if tool.entry == "dir":
            path = filedialog.askdirectory(title=f"选择 {tool.name} 的安装目录（其下有 {tool.detect_rel() or '主程序'}）")
        else:
            path = filedialog.askopenfilename(title=f"选择 {tool.name} 的主程序")
        if not path:
            return
        ok, msg = self.mgr.add_path(tool.id, path)
        self._log(("✓ " if ok else "✗ ") + msg)
        if ok:
            self.refresh(force=True)
        else:
            messagebox.showerror("添加失败", msg)

    def do_remove(self):
        ins = self.selected()
        if not ins:
            return
        if ins.source == "remembered":
            # 「历史记录」是我们自己记下来的，用户说不对就该能忘掉（否则永远卡在列表里）
            if not messagebox.askyesno("确认", f"忘记这条记录：\n\n  {ins.home}\n\n"
                                              "（只从 EnvSwitch 的记忆里移除；如果扫描器仍能实际找到它，还会照常显示。"
                                              "不会删除磁盘文件）"):
                return
            self.mgr.forget_install(self.tool_id, ins.home)
            self._log("已忘记：" + ins.home)
            self.refresh(force=True)
            return
        if ins.source != "custom":
            messagebox.showinfo("提示", "只能移除你自己手动添加的条目（来源列显示「手动添加」），"
                                        "或忘记工具自动记录下来的条目（来源列显示「历史记录」）")
            return
        if not messagebox.askyesno("确认", f"移除条目：\n\n  {ins.home}\n\n（不会删除磁盘文件）"):
            return
        self.mgr.remove_path(self.tool_id, ins.home)
        self._log("已移除：" + ins.home)
        self.refresh(force=True)

    def do_copy(self):
        ins = self.selected()
        if not ins:
            return
        self.clipboard_clear()
        self.clipboard_append(ins.exe or ins.home)
        self.status.configure(text="已复制：" + (ins.exe or ins.home))

    def do_open_dir(self):
        ins = self.selected()
        if not ins:
            return
        path = ins.home
        try:
            if os.name == "nt":
                os.startfile(path)  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("打开失败", str(e))

    def do_pin(self):
        ins = self.selected()
        tool = self.mgr.tool(self.tool_id)
        if not ins or not tool:
            return
        d = filedialog.askdirectory(title="选择项目根目录", initialdir=os.getcwd())
        if not d:
            return
        ok, msg = self.mgr.pin(tool.id, ins, d)
        self._log(("✓ " if ok else "✗ ") + msg)
        messagebox.showinfo("完成" if ok else "失败", msg)

    def do_revert(self):
        if not messagebox.askyesno("确认还原", "清除 EnvSwitch 对该工具写入的环境变量改动？\n你自己的其它配置不受影响。"):
            return
        ok, msg = self.mgr.revert(self.tool_id)
        self._log(("✓ " if ok else "✗ ") + msg)
        messagebox.showinfo("完成" if ok else "提示", msg)
        self.refresh(force=True)

    # ------------------------------ 工具管理 ------------------------------ #

    def new_tool(self):
        dlg = ToolEditor(self, self.mgr)
        tool = dlg.modal()
        if not tool:
            return
        ok, msg = self.mgr.add_tool(tool)
        self._log(("✓ " if ok else "✗ ") + msg)
        if not ok:
            messagebox.showerror("保存失败", msg)
            return
        self.rebuild_sidebar()
        self.select_tool(tool.id)

    def edit_tool(self):
        tool = self.mgr.tool(self.tool_id)
        if not tool:
            return
        dlg = ToolEditor(self, self.mgr, tool=tool)
        result = dlg.modal()
        if not result:
            self._maybe_delete_tool(tool)
            return
        ok, msg = self.mgr.add_tool(result)
        self._log(("✓ " if ok else "✗ ") + msg)
        self.rebuild_sidebar()
        self.select_tool(result.id)

    def _maybe_delete_tool(self, tool: ToolDef):
        """编辑对话框里点取消时，对自定义工具追问是否删除。"""
        if tool.builtin:
            return
        if messagebox.askyesno("删除工具", f"要删除自定义工具「{tool.name}」吗？"):
            ok, msg = self.mgr.remove_tool(tool.id)
            self._log(("✓ " if ok else "✗ ") + msg)
            self.rebuild_sidebar()
            tools = self.mgr.tools()
            if tools:
                self.select_tool(tools[0].id)

    # ------------------------------ 其它 ------------------------------ #

    def open_settings(self):
        def on_theme(name: str):
            T.set_theme(name)
            self.c = T.colors()
            self.mgr.config.set_setting("theme", name)
            messagebox.showinfo("提示", "主题已切换，重启窗口后完整生效")

        SettingsDialog(self, self.mgr, on_theme)
        show = bool(self.mgr.config.setting("show_log", True))
        if show:
            self.log_frame.grid()
        else:
            self.log_frame.grid_remove()
        self.rebuild_sidebar()
        if self.tool_id and not self.mgr.tool(self.tool_id):
            tools = self.mgr.tools()
            if tools:
                self.select_tool(tools[0].id)

    def show_diagnose(self):
        messagebox.showinfo("诊断信息", self.mgr.diagnose())

    def _export_cfg(self):
        p = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if p:
            self.mgr.config.export_to(p)
            self._log("已导出配置：" + p)

    def _import_cfg(self):
        p = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if not p:
            return
        try:
            self.mgr.config.import_from(p)
            self._log("已导入配置：" + p)
            self.rebuild_sidebar()
            self.refresh(force=True)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("导入失败", str(e))

    def _log(self, msg: str):
        try:
            self.log.insert("end", msg + "\n")
            self.log.see("end")
        except Exception:  # noqa: BLE001
            pass

    def _on_close(self):
        self.destroy()


def main(mgr) -> int:
    """GUI 入口，返回进程退出码。"""
    import tkinter  # noqa: F401  （确保 ImportError 在这里抛出，便于给出友好提示）

    app = App(mgr)
    app.mainloop()
    return 0
