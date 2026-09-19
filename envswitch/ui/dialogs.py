"""各类对话框：自定义工具编辑器、设置、关于。"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Callable, List, Optional

from ..core.builtins import TEMPLATES
from ..core.models import ToolDef, safe_name
from . import theme as T

ICON_CHOICES = ["◆", "☕", "🐍", "⬢", "🐹", "🅼", "🅶", "🅽", "🔧", "⚙", "📦", "🚀", "🧩", "🛠", "🌐", "💠"]


# --------------------------------------------------------------------------- #
# 基础
# --------------------------------------------------------------------------- #


class ScrollFrame(ttk.Frame):
    """带垂直滚动条的容器。"""

    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.vsb.pack(side="right", fill="y")
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.body = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfig(self._win, width=e.width))
        for w in (self.canvas, self):
            w.bind("<MouseWheel>", self._wheel)

    def _wheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        return "break"


class BaseDialog(tk.Toplevel):
    def __init__(self, master, title: str, width: int, height: int):
        super().__init__(master)
        self.title(title)
        self.transient(master)
        self.resizable(True, True)
        self.configure(background=T.colors()["bg"])
        self.geometry(f"{width}x{height}")
        self.update_idletasks()
        try:
            x = master.winfo_rootx() + (master.winfo_width() - width) // 2
            y = master.winfo_rooty() + (master.winfo_height() - height) // 2
            self.geometry(f"{width}x{height}+{max(x, 20)}+{max(y, 20)}")
        except Exception:  # noqa: BLE001
            pass
        self.result = None
        self.protocol("WM_DELETE_WINDOW", self._cancel)

    def _cancel(self):
        self.result = None
        self.destroy()

    def modal(self):
        self.grab_set()
        self.focus_force()
        self.wait_window()
        return self.result


def _entry(parent, label: str, value: str = "", hint: str = "", row: int = 0) -> ttk.Entry:
    c = T.colors()
    ttk.Label(parent, text=label, font=T.f(10, "bold")).grid(row=row, column=0, sticky="w", pady=(8, 2))
    var = tk.StringVar(value=value)
    e = ttk.Entry(parent, textvariable=var)
    e.grid(row=row + 1, column=0, sticky="ew", columnspan=2)
    if hint:
        ttk.Label(parent, text=hint, style="Muted.TLabel", wraplength=560).grid(
            row=row + 2, column=0, sticky="w", columnspan=2)
    e.var = var  # type: ignore[attr-defined]
    return e


def _text(parent, label: str, value: str, hint: str, row: int, height: int = 4) -> tk.Text:
    ttk.Label(parent, text=label, font=T.f(10, "bold")).grid(row=row, column=0, sticky="w", pady=(8, 2))
    box = tk.Text(parent, height=height, wrap="word", font=T.mono(9), relief="solid", borderwidth=1,
                  background=T.colors()["panel"], foreground=T.colors()["text"],
                  insertbackground=T.colors()["text"])
    box.insert("1.0", value)
    box.grid(row=row + 1, column=0, sticky="ew", columnspan=2)
    if hint:
        ttk.Label(parent, text=hint, style="Muted.TLabel", wraplength=560).grid(
            row=row + 2, column=0, sticky="w", columnspan=2)
    return box


def _text_get(box: tk.Text) -> str:
    return box.get("1.0", "end").strip()


def _lines(box: tk.Text) -> List[str]:
    return [l.strip() for l in _text_get(box).splitlines() if l.strip()]


# --------------------------------------------------------------------------- #
# 工具编辑器
# --------------------------------------------------------------------------- #


class ToolEditor(BaseDialog):
    """新建/编辑一个 ToolDef。这是"自定义任何工具"的入口。"""

    def __init__(self, master, mgr, tool: Optional[ToolDef] = None, on_log=print):
        super().__init__(master, "自定义工具" if tool is None else f"编辑工具 · {tool.name}", 720, 720)
        self.mgr = mgr
        self.on_log = on_log
        self.editing = tool
        c = T.colors()

        outer = ttk.Frame(self, padding=14)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)

        # ---- 预设 ---- #
        top = ttk.Frame(outer)
        top.grid(row=0, column=0, sticky="ew")
        ttk.Label(top, text="从预设开始：").pack(side="left")
        self.tpl = tk.StringVar(value="空模板")
        cb = ttk.Combobox(top, textvariable=self.tpl, values=list(TEMPLATES.keys()), state="readonly", width=38)
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", self._apply_template)

        sf = ScrollFrame(outer)
        sf.grid(row=1, column=0, sticky="nsew", pady=(8, 6))
        outer.rowconfigure(1, weight=1)
        body = sf.body
        body.columnconfigure(0, weight=1)

        r = 0
        self.e_name = _entry(body, "显示名称 *", tool.name if tool else "", "例如：Ruby / PHP / Flutter", r); r += 3
        self.e_id = _entry(body, "工具 ID *（英文/数字，保存后不可改）", tool.id if tool else "",
                           "用于配置文件与命令行，例如 ruby", r); r += 3

        # 图标
        ttk.Label(body, text="图标", font=T.f(10, "bold")).grid(row=r, column=0, sticky="w", pady=(8, 2)); r += 1
        icons = ttk.Frame(body)
        icons.grid(row=r, column=0, sticky="w"); r += 1
        self.icon = tk.StringVar(value=(tool.icon if tool else "🔧"))
        for i, ch in enumerate(ICON_CHOICES):
            b = tk.Radiobutton(icons, text=ch, variable=self.icon, value=ch, indicatoron=False,
                               width=3, font=T.f(12), relief="flat", background=c["panel"],
                               selectcolor=c["sidebar_active"], activebackground=c["sidebar_hover"])
            b.grid(row=i // 8, column=i % 8, padx=1, pady=1)

        # 类型
        ttk.Label(body, text="识别方式 *", font=T.f(10, "bold")).grid(row=r, column=0, sticky="w", pady=(8, 2)); r += 1
        types = ttk.Frame(body)
        types.grid(row=r, column=0, sticky="w"); r += 1
        self.entry_mode = tk.StringVar(value=(tool.entry if tool else "dir"))
        ttk.Radiobutton(types, text="目录模式：找到「包含某文件」的目录（如 JDK 的 bin/java）",
                        variable=self.entry_mode, value="dir",
                        command=self._sync_mode).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(types, text="文件模式：找到「文件名匹配正则」的可执行文件（如 python3.11.exe）",
                        variable=self.entry_mode, value="file",
                        command=self._sync_mode).grid(row=1, column=0, sticky="w")

        self.e_detect = _entry(body, "判定文件（相对安装目录）", tool.detect if tool else "bin/{name}{ext}",
                               "占位符：{ext} → .exe（Windows）；目录模式必填", r); r += 3
        self.e_pattern = _entry(body, "主程序文件名正则", tool.file_pattern if tool else "",
                                r"例如 python[\d.]*(\.exe)? ；文件模式必填", r); r += 3

        self.e_bins = _entry(body, "命令行名（逗号分隔，用于 PATH 反查与「当前版本」判定）",
                             ", ".join(tool.bin_names) if tool else "", "例如：ruby,ruby3", r); r += 3
        self.e_home_var = _entry(body, "主目录环境变量（可留空）", tool.home_var if tool else "",
                                 "例如 JAVA_HOME；切换时会写入该变量", r); r += 3
        self.e_extra = _text(body, "附加环境变量（每行 KEY=VALUE）",
                             "\n".join(f"{k}={v}" for k, v in (tool.extra_vars or {}).items()) if tool else "",
                             "值支持 {home} / {bin} 占位符", r, 3); r += 3
        self.e_vcmd = _entry(body, "版本命令", " ".join(tool.version_cmd) if tool else "{bin} --version",
                             "占位符：{bin} 主程序路径、{home} 安装目录", r); r += 3
        self.e_vregex = _entry(body, "版本正则（取第 1 个分组）", tool.version_regex if tool else r"(\d+\.\d+\.\d+)",
                               "留空则取命令输出第一行", r); r += 3
        self.e_paths = _text(body, "需要前置到 PATH 的目录（每行一个）",
                             "\n".join(tool.path_entries) if tool else "{home}",
                             "占位符：{home} 安装目录、{bin} 主程序；Windows 下常见的 Scripts 也可以写 {home}/Scripts",
                             r, 3); r += 3
        self.e_conflict = _entry(body, "冲突关键词（逗号分隔）", ", ".join(tool.conflict_keywords) if tool else "",
                                 "切换时会把这些关键词命中的 PATH 条目先摘掉（Windows 有效）", r); r += 3
        self.e_projfile = _entry(body, "项目级版本文件名（可留空）", tool.project_file if tool else "",
                                 "例如 .java-version / .python-version", r); r += 3
        self.e_projval = _entry(body, "项目文件内容", tool.project_value if tool else "full",
                                "full=完整版本号 / major=主版本号 / path=安装路径", r); r += 3
        self.e_roots = _text(body, f"扫描根目录（每行一个，当前平台：{__import__('platform').system()}）",
                             "\n".join(tool.roots()) if tool else "",
                             "支持 ~ 家目录、%VAR% 环境变量、* 通配符", r, 5); r += 3
        self.e_notes = _text(body, "备注", tool.notes if tool else "", "", r, 2); r += 3

        # ---- 底部按钮 ---- #
        bar = ttk.Frame(outer)
        bar.grid(row=2, column=0, sticky="ew")
        ttk.Button(bar, text="🔍 测试识别", command=self._test).pack(side="left")
        self.test_label = ttk.Label(bar, text="", style="Muted.TLabel")
        self.test_label.pack(side="left", padx=10)
        ttk.Button(bar, text="保存", style="Accent.TButton", command=self._save).pack(side="right")
        ttk.Button(bar, text="取消", command=self._cancel).pack(side="right", padx=8)

        if tool:
            self.e_id.configure(state="disabled")
        self._sync_mode()

    # ------------- 行为 ------------- #

    def _apply_template(self, _e=None):
        tpl = TEMPLATES.get(self.tpl.get())
        if tpl is None:
            return
        self.icon.set(tpl.icon)
        self.entry_mode.set(tpl.entry)
        self._set(self.e_detect, tpl.detect)
        self._set(self.e_pattern, tpl.file_pattern)
        self._set(self.e_bins, ", ".join(tpl.bin_names))
        self._set(self.e_home_var, tpl.home_var)
        self._set(self.e_vcmd, " ".join(tpl.version_cmd))
        self._set(self.e_vregex, tpl.version_regex)
        self._set(self.e_conflict, ", ".join(tpl.conflict_keywords))
        self._set(self.e_projfile, tpl.project_file)
        self._set(self.e_projval, tpl.project_value)
        box = self.e_extra
        box.delete("1.0", "end")
        box.insert("1.0", "\n".join(f"{k}={v}" for k, v in (tpl.extra_vars or {}).items()))
        box = self.e_paths
        box.delete("1.0", "end")
        box.insert("1.0", "\n".join(tpl.path_entries))
        box = self.e_roots
        box.delete("1.0", "end")
        box.insert("1.0", "\n".join(tpl.roots()))
        if not self.editing:
            self._set(self.e_name, tpl.name)
            self._set(self.e_id, tpl.id + "-custom")
        self._sync_mode()

    @staticmethod
    def _set(widget, value: str):
        if hasattr(widget, "var"):
            widget.var.set(value)
        else:
            widget.delete("1.0", "end")
            widget.insert("1.0", value)

    def _sync_mode(self):
        is_dir = self.entry_mode.get() == "dir"
        self.e_detect.configure(state="normal" if is_dir else "disabled")
        self.e_pattern.configure(state="disabled" if is_dir else "normal")

    def _collect(self) -> Optional[ToolDef]:
        import platform

        name = self.e_name.var.get().strip()
        tid = safe_name(self.e_id.var.get() or name)
        if not name:
            messagebox.showerror("缺少字段", "请填写显示名称", parent=self)
            return None
        mode = self.entry_mode.get()
        detect = self.e_detect.var.get().strip()
        pattern = self.e_pattern.var.get().strip()
        if mode == "dir" and not detect:
            messagebox.showerror("缺少字段", "目录模式必须填写「判定文件」", parent=self)
            return None
        if mode == "file" and not pattern:
            messagebox.showerror("缺少字段", "文件模式必须填写「主程序文件名正则」", parent=self)
            return None

        extra = {}
        for line in _lines(self.e_extra):
            if "=" in line:
                k, v = line.split("=", 1)
                extra[k.strip()] = v.strip()

        sys_key = {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}.get(platform.system(), "linux")
        tool = ToolDef(
            id=tid,
            name=name,
            icon=self.icon.get(),
            entry=mode,
            home_var=self.e_home_var.var.get().strip(),
            extra_vars=extra,
            detect=detect if mode == "dir" else "",
            file_pattern=pattern if mode == "file" else "",
            bin_names=[x.strip() for x in self.e_bins.var.get().split(",") if x.strip()],
            version_cmd=self.e_vcmd.var.get().split(),
            version_regex=self.e_vregex.var.get().strip(),
            path_entries=_lines(self.e_paths) or ["{home}"],
            conflict_keywords=[x.strip() for x in self.e_conflict.var.get().split(",") if x.strip()],
            project_file=self.e_projfile.var.get().strip(),
            project_value=self.e_projval.var.get().strip() or "full",
            search_roots={sys_key: _lines(self.e_roots)},
            builtin=False,
            enabled=True,
            notes=_text_get(self.e_notes),
        )
        return tool

    def _test(self):
        tool = self._collect()
        if not tool:
            return
        if tool.entry == "dir":
            path = filedialog.askdirectory(title="选择一个安装目录", parent=self)
        else:
            path = filedialog.askopenfilename(title="选择主程序", parent=self)
        if not path:
            return
        home, exe = self.mgr.scanner._interpret(tool, path)
        if not home:
            self.test_label.configure(text=f"✗ 未识别：{path}", foreground=T.colors()["err"])
            return
        version = self.mgr.scanner._version(tool, home, exe)
        self.test_label.configure(
            text=f"✓ 识别成功：home={home}  version={version}", foreground=T.colors()["ok"])

    def _save(self):
        tool = self._collect()
        if not tool:
            return
        existing = self.mgr.tool(tool.id)
        if existing and existing.builtin:
            messagebox.showerror("ID 冲突", f"「{tool.id}」是内置工具占用的 ID，请换一个", parent=self)
            return
        if self.editing is None and existing:
            messagebox.showerror("ID 冲突", f"已存在 ID 为「{tool.id}」的工具", parent=self)
            return
        self.result = tool
        self.destroy()


# --------------------------------------------------------------------------- #
# 设置
# --------------------------------------------------------------------------- #


class SettingsDialog(BaseDialog):
    def __init__(self, master, mgr, on_theme_change: Callable[[str], None]):
        super().__init__(master, "设置", 620, 520)
        self.mgr = mgr
        self.on_theme_change = on_theme_change

        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)

        ttk.Label(body, text="外观", font=T.f(11, "bold")).grid(row=0, column=0, sticky="w")
        row = ttk.Frame(body)
        row.grid(row=1, column=0, sticky="w", pady=(4, 10))
        self.theme = tk.StringVar(value=mgr.config.setting("theme", "light"))
        for label, val in (("浅色", "light"), ("深色", "dark")):
            ttk.Radiobutton(row, text=label, variable=self.theme, value=val,
                            command=lambda: self.on_theme_change(self.theme.get())
                            ).pack(side="left", padx=(0, 14))

        self.confirm = tk.BooleanVar(value=bool(mgr.config.setting("confirm_switch", True)))
        ttk.Checkbutton(body, text="切换前弹确认框", variable=self.confirm).grid(row=2, column=0, sticky="w")
        self.show_log = tk.BooleanVar(value=bool(mgr.config.setting("show_log", True)))
        ttk.Checkbutton(body, text="显示底部日志面板", variable=self.show_log).grid(row=3, column=0, sticky="w")
        self.auto_term = tk.BooleanVar(value=bool(mgr.config.setting("launch_terminal", False)))
        ttk.Checkbutton(body, text="切换后自动打开一个已生效的终端", variable=self.auto_term).grid(
            row=4, column=0, sticky="w")

        ttk.Separator(body).grid(row=5, column=0, sticky="ew", pady=14)

        ttk.Label(body, text="内置工具（取消勾选即从侧边栏隐藏）", font=T.f(11, "bold")).grid(
            row=6, column=0, sticky="w")
        self.tool_vars = {}
        grid = ttk.Frame(body)
        grid.grid(row=7, column=0, sticky="w", pady=(4, 10))
        from ..core.builtins import BUILTIN_TOOLS

        for i, t in enumerate(BUILTIN_TOOLS):
            v = tk.BooleanVar(value=not mgr.config.is_disabled(t.id))
            self.tool_vars[t.id] = v
            ttk.Checkbutton(grid, text=f"{t.icon} {t.name}", variable=v).grid(
                row=i // 2, column=i % 2, sticky="w", padx=(0, 20), pady=2)

        ttk.Separator(body).grid(row=8, column=0, sticky="ew", pady=14)

        ttk.Label(body, text="配置", font=T.f(11, "bold")).grid(row=9, column=0, sticky="w")
        path_row = ttk.Frame(body)
        path_row.grid(row=10, column=0, sticky="ew", pady=(4, 10))
        ttk.Label(path_row, text=str(mgr.config.path), style="Muted.TLabel").pack(side="left")
        ttk.Button(path_row, text="导出", command=self._export).pack(side="right")
        ttk.Button(path_row, text="导入", command=self._import).pack(side="right", padx=6)

        bar = ttk.Frame(body)
        bar.grid(row=11, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(bar, text="关闭", command=self._cancel).pack(side="right")
        ttk.Button(bar, text="还原全部切换", command=self._revert_all).pack(side="left")

    def _export(self):
        p = filedialog.asksaveasfilename(parent=self, defaultextension=".json",
                                         filetypes=[("JSON", "*.json")], title="导出配置")
        if p:
            self.mgr.config.export_to(p)
            messagebox.showinfo("完成", f"已导出到 {p}", parent=self)

    def _import(self):
        p = filedialog.askopenfilename(parent=self, filetypes=[("JSON", "*.json")], title="导入配置")
        if p:
            try:
                self.mgr.config.import_from(p)
                messagebox.showinfo("完成", "已导入，重启后完整生效", parent=self)
            except Exception as e:  # noqa: BLE001
                messagebox.showerror("失败", str(e), parent=self)

    def _revert_all(self):
        if messagebox.askyesno("确认", "清除 EnvSwitch 对所有工具写入的环境变量改动？", parent=self):
            ok, msg = self.mgr.revert_all()
            messagebox.showinfo("完成", msg, parent=self)

    def _cancel(self):
        cfg = self.mgr.config
        cfg.set_setting("theme", self.theme.get())
        cfg.set_setting("confirm_switch", self.confirm.get())
        cfg.set_setting("show_log", self.show_log.get())
        cfg.set_setting("launch_terminal", self.auto_term.get())
        for tid, v in self.tool_vars.items():
            cfg.set_disabled(tid, not v.get())
        super()._cancel()


# --------------------------------------------------------------------------- #
# 环境体检
# --------------------------------------------------------------------------- #


class AuditDialog(BaseDialog):
    """列出"装了但没进 PATH""失效条目""系统级冲突""未纳管路径"，并支持一键修复与忽略。"""

    def __init__(self, master, mgr, on_log=print, issues=None):
        super().__init__(master, "环境体检", 940, 680)
        self.mgr = mgr
        self.on_log = on_log
        self.issues: List = []
        self._busy = False
        self.show_ignored = False

        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)
        body.rowconfigure(3, weight=0)

        self.summary = ttk.Label(body, text="检测中…", font=T.f(11, "bold"))
        self.summary.grid(row=0, column=0, sticky="w", pady=(0, 8))

        cols = ("sev", "kind", "title", "path")
        self.tree = ttk.Treeview(body, columns=cols, show="headings", selectmode="browse")
        for k, w in (("sev", 60), ("kind", 130), ("title", 330), ("path", 320)):
            head = {"sev": "级别", "kind": "类型", "title": "问题", "path": "涉及路径"}[k]
            self.tree.heading(k, text=head)
            self.tree.column(k, width=w, anchor="w", stretch=(k == "title"))
        vs = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.grid(row=1, column=0, sticky="nsew")
        vs.grid(row=1, column=1, sticky="ns")
        self.tree.tag_configure("warn", foreground=T.colors()["warn"])
        self.tree.tag_configure("ignored", foreground=T.colors()["text_muted"])
        self.tree.bind("<Double-1>", lambda e: self.fix_selected())
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._show_detail())

        # 详情面板：选中问题的解释、完整涉及路径、修复方式
        detail_box = ttk.Frame(body, style="Card.TFrame", padding=8)
        detail_box.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        detail_box.columnconfigure(0, weight=1)
        self.detail = tk.Text(detail_box, height=5, wrap="word", font=T.f(9), relief="flat",
                              background=T.colors()["panel"], foreground=T.colors()["text"],
                              padx=8, pady=6, state="disabled")
        self.detail.pack(fill="both", expand=True)

        bar = ttk.Frame(body)
        bar.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(bar, text="🩺 重新检测", command=self.reload).pack(side="left")
        ttk.Button(bar, text="🔧 修复选中", style="Accent.TButton",
                   command=self.fix_selected).pack(side="left", padx=8)
        ttk.Button(bar, text="✨ 一键修复全部", command=self.fix_all).pack(side="left")
        self.ignore_btn = ttk.Button(bar, text="🚫 忽略选中", command=self.toggle_ignore)
        self.ignore_btn.pack(side="left", padx=8)
        self.show_ignored_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="显示已忽略", variable=self.show_ignored_var,
                        command=self._toggle_view).pack(side="left")
        self.tip = ttk.Label(bar, text="", style="Muted.TLabel")
        self.tip.pack(side="left", padx=12)
        ttk.Button(bar, text="关闭", command=self._cancel).pack(side="right")

        if issues is not None:
            self.set_issues(issues)
        else:
            self.reload()

    # ------------- 数据 ------------- #

    def reload(self):
        self.tip.configure(text="检测中…")
        try:
            from ..core.audit import audit as run_audit
        except Exception as e:  # noqa: BLE001
            self.tip.configure(text=str(e))
            return
        import threading

        def work():
            try:
                issues = run_audit(self.mgr, include_ignored=self.show_ignored)
            except Exception as e:  # noqa: BLE001
                issues = []
                err = str(e)
            else:
                err = ""
            self.after(0, lambda: self._done(issues, err))

        threading.Thread(target=work, daemon=True).start()

    def _done(self, issues, err):
        self.set_issues(issues)
        self.tip.configure(text=("检测出错：" + err) if err else "")

    def _toggle_view(self):
        self.show_ignored = self.show_ignored_var.get()
        self.ignore_btn.configure(text="↩ 取消忽略" if self.show_ignored else "🚫 忽略选中")
        self.reload()

    def set_issues(self, issues):
        self.issues = issues
        for i in self.tree.get_children():
            self.tree.delete(i)
        self._show_detail()
        if not issues:
            self.summary.configure(text="✓ 没有发现问题，环境变量很干净")
        else:
            # 只有 blocked 会让"切换了不生效"；
            # 商店别名、主目录变量失效这些属于"要看一眼"，别混在一起吓人
            blockers = [x for x in issues if x.kind in ("blocked",)]
            text = f"发现 {len(issues)} 个问题"
            if blockers:
                text += f"（{len(blockers)} 个会让切换不生效）"
            if not self.show_ignored:
                n = len(self.mgr.config.audit_ignored())
                if n:
                    text += f"　·　另有 {n} 条已忽略，勾选「显示已忽略」查看"
            self.summary.configure(text=text)
        for idx, iss in enumerate(issues):
            sev = "⚠ 警告" if iss.severity == "warn" else "· 提示"
            self.tree.insert("", "end", iid=str(idx),
                             tags=("warn",) if iss.severity == "warn" else (),
                             values=(sev, iss.kind_label(), iss.title,
                                     " ; ".join(iss.items[:3])))

    def _show_detail(self):
        iss = self._selected()
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        if iss:
            items = "\n".join("　· " + p for p in iss.items[:12]) if iss.items else ""
            self.detail.insert("1.0",
                               f"{iss.title}\n{iss.detail}"
                               + (f"\n涉及路径：\n{items}" if items else ""))
        self.detail.configure(state="disabled")

    # ------------- 修复 ------------- #

    def _selected(self):
        sel = self.tree.selection()
        if not sel or not self.issues:
            return None
        try:
            return self.issues[int(sel[0])]
        except (ValueError, IndexError):
            return None

    def toggle_ignore(self):
        iss = self._selected()
        if not iss:
            messagebox.showinfo("提示", "先选中一个问题", parent=self)
            return
        if self.show_ignored:
            self.mgr.config.unignore_issue(iss.ignore_key())
            self._log("已恢复显示：" + iss.title)
        else:
            self.mgr.config.ignore_issue(iss.ignore_key())
            self._log("已忽略（之后体检不再提示）：" + iss.title)
        self.reload()

    def fix_selected(self):
        iss = self._selected()
        if not iss:
            messagebox.showinfo("提示", "先选中一个问题", parent=self)
            return
        if not iss.fixable:
            messagebox.showinfo("这条要手动处理", f"{iss.title}\n\n{iss.detail}", parent=self)
            return
        if iss.needs_confirm:
            items = "\n".join("　· " + p for p in iss.items[:12]) if iss.items else ""
            body = (f"这个问题会改动【系统 PATH】，请确认是否执行：\n\n{iss.title}\n\n"
                    f"将要处理：\n{items}\n\n{iss.detail}")
            if not messagebox.askyesno("需要确认", body, parent=self):
                return
        self._run_fix([iss])

    def fix_all(self):
        auto = [i for i in self.issues if i.fixable and not i.needs_confirm]
        held = [i for i in self.issues if i.fixable and i.needs_confirm]
        if not auto:
            if held:
                messagebox.showinfo(
                    "提示",
                    "当前所有可修复的问题都会改动系统 PATH，需要逐个确认。\n"
                    "请选中它们后点「修复选中」（会弹 UAC）。", parent=self)
            else:
                messagebox.showinfo("提示", "没有可自动修复的问题", parent=self)
            return
        msg = f"将自动修复 {len(auto)} 个问题（不会改动系统 PATH）。"
        if held:
            msg += (f"\n另有 {len(held)} 个会改动系统 PATH 的问题已跳过，"
                    "请逐个点「修复选中」处理。")
        if not messagebox.askyesno("确认", msg + "\n继续？", parent=self):
            return
        self._run_fix(auto)

    def _run_fix(self, issues):
        if self._busy:
            return
        self._busy = True
        self.tip.configure(text="修复中…（如弹 UAC 请允许）")

        def work():
            results = []
            for iss in issues:
                try:
                    from ..core.audit import fix_issue

                    ok, msg = fix_issue(self.mgr, iss)
                except Exception as e:  # noqa: BLE001
                    ok, msg = False, str(e)
                results.append((iss, ok, msg))
                if callable(self.on_log):
                    self.on_log(("✓ " if ok else "✗ ") + f"{iss.title} → {msg}")
            self.after(0, lambda: self._fix_done(results))

        import threading

        threading.Thread(target=work, daemon=True).start()

    def _fix_done(self, results):
        self._busy = False
        ok_n = sum(1 for _i, ok, _m in results if ok)
        self.tip.configure(text=f"修复完成：成功 {ok_n}/{len(results)}")
        failed = [f"{i.title}：{m}" for i, ok, m in results if not ok]
        self.reload()
        if failed:
            messagebox.showwarning("部分修复失败", "\n".join(failed[:8]), parent=self)
        elif results:
            messagebox.showinfo("完成", f"已修复 {ok_n} 个问题", parent=self)

    def _log(self, msg: str):
        if callable(self.on_log):
            self.on_log(msg)


# --------------------------------------------------------------------------- #
# 关于
# --------------------------------------------------------------------------- #


class AboutDialog(BaseDialog):
    def __init__(self, master):
        from .. import __version__
        import platform
        import sys

        super().__init__(master, "关于 EnvSwitch", 520, 400)
        c = T.colors()
        body = ttk.Frame(self, padding=20)
        body.pack(fill="both", expand=True)

        ttk.Label(body, text="🔀 EnvSwitch", font=T.f(20, "bold")).pack(anchor="w")
        ttk.Label(body, text=f"v{__version__} · 多版本运行环境一键切换", style="Muted.TLabel").pack(anchor="w", pady=(2, 14))

        info = (
            "把 Java / Python / Node / Go / Maven / Gradle / .NET …… 的多个版本收进同一个列表，\n"
            "点一下就切换全局默认；任何其它命令行工具也能自己定义进来。\n\n"
            "· 零第三方依赖，只用 Python 标准库 + tkinter\n"
            "· Windows 写用户级环境变量（无需管理员），macOS/Linux 写 shell 配置标记块\n"
            "· 所有写入都可一键还原，不会污染你的配置文件\n"
        )
        ttk.Label(body, text=info, justify="left", wraplength=460).pack(anchor="w")

        ttk.Separator(body).pack(fill="x", pady=14)
        ttk.Label(body, text=f"Python {sys.version.split()[0]} · {platform.system()} {platform.machine()}",
                  style="Muted.TLabel").pack(anchor="w")
        ttk.Label(body, text="MIT License · 欢迎 PR", style="Muted.TLabel").pack(anchor="w")

        ttk.Button(body, text="关闭", command=self._cancel, style="Accent.TButton").pack(anchor="e", pady=(16, 0))
