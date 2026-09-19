r"""命令行入口。

    python -m envswitch                      启动图形界面
    python -m envswitch list [java]          列出某个工具的所有安装
    python -m envswitch use java 0           按序号切换
    python -m envswitch use python D:\...    按路径切换
    python -m envswitch tools                列出所有工具（含自定义）
    python -m envswitch restore [java]       还原某个/所有工具的切换
    python -m envswitch doctor               诊断信息
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from . import __version__
from .core.config import Config
from .core.manager import EnvManager
from .core.models import Install, ToolDef


_QUIET = False


def _emit(text: str) -> None:
    """打包成 --noconsole 的 exe 后 sys.stdout 为 None，此时改用弹窗展示。"""
    if _QUIET:
        return
    if getattr(sys, "frozen", False) and sys.stdout is None:
        try:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo("EnvSwitch", text)
            root.destroy()
        except Exception:  # noqa: BLE001
            pass
        return
    print(text)


USAGE = f"""EnvSwitch v{__version__} · 多版本运行环境切换器

用法：
  envswitch                              启动图形界面
  envswitch list [<工具id>]               列出可用版本（默认列出全部工具）
  envswitch current <工具id>              查看当前生效版本
  envswitch use <工具id> <序号|路径>       切换为全局默认
  envswitch pin  <工具id> <序号> <目录>    写入项目级版本文件
  envswitch add  <工具id> <路径>           手动添加一个安装路径
  envswitch tools                         列出所有工具定义
  envswitch addtool <json文件|json字符串>   添加一个自定义工具
  envswitch restore [<工具id>]             还原环境变量改动（省略则全部还原）
  envswitch audit [--fix] [--ignore 序号] [--ignored] [--unignore all]
                                           环境体检：没进 PATH / 失效 / 冲突 / 未纳管
  envswitch systemfix [工具id|all]         清理系统级 PATH 冲突（Windows，需管理员）
  envswitch systemdrop <base64>            内部命令：按精确路径清理系统 PATH（提权子进程用）
  envswitch systempath-restore             还原清理前的系统级 PATH
  envswitch doctor                         打印诊断信息

示例：
  envswitch list java
  envswitch use java 0
  envswitch use python C:\\Python311\\python.exe
  envswitch audit
  envswitch systemfix java
  envswitch addtool tool-node.json
"""


def cli(argv=None) -> int:
    global _QUIET
    argv = list(sys.argv[1:] if argv is None else argv)
    _QUIET = "--quiet" in argv
    cmd = argv[0] if argv else "gui"
    mgr = EnvManager(Config())

    if cmd in ("gui", ""):
        try:
            from .ui.app import main as gui_main
        except ImportError:
            _emit(
                "当前 Python 没有 tkinter，无法启动图形界面。\n\n"
                "  · Windows / macOS 官方安装包：重新安装并勾选 “tcl/tk and IDLE”\n"
                "  · Ubuntu / Debian：sudo apt install python3-tk\n"
                "  · Fedora：sudo dnf install python3-tkinter\n\n"
                "命令行模式不受影响，例如：envswitch list java"
            )
            return 1
        return gui_main(mgr)

    if cmd in ("-h", "--help", "help"):
        _emit(USAGE)
        return 0

    if cmd in ("-v", "--version"):
        _emit(f"EnvSwitch {__version__}")
        return 0

    if cmd == "list":
        only = argv[1] if len(argv) > 1 else None
        tools = [t for t in mgr.tools() if not only or t.id == only]
        if only and not tools:
            _emit(f"未知工具：{only}。用 `envswitch tools` 看看有哪些。")
            return 1
        lines = []
        for t in tools:
            installs = mgr.scan(t.id, refresh=True)
            cur = mgr.current_version(t.id)
            lines.append(f"\n{t.icon} {t.name}  （{t.id}）  当前：{cur}")
            if not installs:
                lines.append("   （未检测到，可以用 add 手动添加路径）")
            for i, ins in enumerate(installs):
                mark = " *" if ins.current else (" +" if getattr(ins, "is_default", False) else "  ")
                lines.append(f"  [{i}]{mark} {ins.version:<12} {ins.vendor:<16} {ins.home}")
        _emit("\n".join(lines).strip())
        return 0

    if cmd == "current" and len(argv) > 1:
        _emit(mgr.current_version(argv[1]))
        return 0

    if cmd == "tools":
        lines = []
        for t in mgr.all_tools():
            state = "内置" if t.builtin else "自定义"
            hidden = "（已隐藏）" if mgr.config.is_disabled(t.id) else ""
            lines.append(f"  {t.icon} {t.id:<12} {t.name:<14} {state}{hidden}")
        _emit("\n".join(lines))
        return 0

    if cmd == "use" and len(argv) >= 3:
        tool_id, target = argv[1], argv[2]
        installs = mgr.scan(tool_id, refresh=True)
        ins = None
        if target.isdigit() and int(target) < len(installs):
            ins = installs[int(target)]
        else:
            p = os.path.abspath(os.path.expanduser(target))
            for x in installs:
                if os.path.normcase(x.home) == os.path.normcase(p) or os.path.normcase(x.exe) == os.path.normcase(p):
                    ins = x
                    break
        if ins is None:
            tool = mgr.tool(tool_id)
            if not tool:
                _emit(f"未知工具：{tool_id}")
                return 1
            home, exe = mgr.scanner._interpret(tool, p)
            if home:
                ins = Install(tool_id=tool_id, home=home, exe=exe,
                              version=mgr.scanner._version(tool, home, exe),
                              source="custom")
        if ins is None:
            _emit(f"找不到目标：{target}")
            return 1
        ok, msg, warns = mgr.use(tool_id, ins)
        _emit(("OK  " if ok else "ERR ") + msg)
        for w in warns:
            _emit("WARN " + w)
        return 0 if ok else 1

    if cmd == "pin" and len(argv) >= 4:
        tool_id, idx, directory = argv[1], argv[2], argv[3]
        installs = mgr.scan(tool_id)
        if not idx.isdigit() or int(idx) >= len(installs):
            _emit("序号无效")
            return 1
        ok, msg = mgr.pin(tool_id, installs[int(idx)], directory)
        _emit(("OK  " if ok else "ERR ") + msg)
        return 0 if ok else 1

    if cmd == "add" and len(argv) >= 3:
        ok, msg = mgr.add_path(argv[1], argv[2])
        _emit(("OK  " if ok else "ERR ") + msg)
        return 0 if ok else 1

    if cmd == "addtool" and len(argv) >= 2:
        raw = argv[1]
        data = json.loads(Path(raw).read_text(encoding="utf-8")) if os.path.isfile(raw) else json.loads(raw)
        ok, msg = mgr.add_tool(ToolDef.from_dict(data))
        _emit(("OK  " if ok else "ERR ") + msg)
        return 0 if ok else 1

    if cmd == "restore":
        ok, msg = mgr.revert(argv[1]) if len(argv) > 1 else mgr.revert_all()
        _emit(("OK  " if ok else "ERR ") + msg)
        return 0 if ok else 1

    if cmd == "audit":
        from .core.audit import audit as run_audit, fix_issue

        rest = [a for a in argv[1:] if a != "--fix"]

        # --unignore <key|all>：恢复显示被忽略的问题
        if "--unignore" in rest:
            key = rest[rest.index("--unignore") + 1]
            mgr.config.unignore_issue(key if key != "all" else "*")
            _emit("OK  已恢复显示被忽略的问题")
            return 0

        # --ignore <序号>：把体检列表里的第 n 条加入忽略
        if "--ignore" in rest:
            n = int(rest[rest.index("--ignore") + 1])
            issues = run_audit(mgr)
            if not (0 <= n < len(issues)):
                _emit(f"ERR 序号超出范围（0~{len(issues) - 1}）")
                return 1
            mgr.config.ignore_issue(issues[n].ignore_key())
            _emit("OK  已忽略：" + issues[n].title)
            return 0

        show_ignored = "--ignored" in rest
        issues = run_audit(mgr, include_ignored=True)
        if show_ignored:
            ignored = set(mgr.config.audit_ignored())
            issues = [i for i in issues if i.ignore_key() in ignored]
        else:
            issues = [i for i in issues if i.ignore_key() not in set(mgr.config.audit_ignored())]
        if not issues:
            _emit("✓ 环境体检通过，没有发现问题")
            return 0
        lines = [f"环境体检：发现 {len(issues)} 个问题"
                 + ("（已忽略列表）" if show_ignored else "") + "\n"]
        for i, iss in enumerate(issues):
            lines.append(f"[{i}] [{iss.severity}] {iss.kind_label()} · {iss.title}")
            for it in iss.items[:5]:
                lines.append(f"        - {it}")
        if not show_ignored:
            lines.append("\n提示：--ignore <序号> 忽略某条；--ignored 查看已忽略；--unignore all 全部恢复")
        _emit("\n".join(lines))
        if "--fix" in argv:
            _emit("\n开始修复：")
            for iss in issues:
                if not iss.fixable:
                    continue
                ok, msg = fix_issue(mgr, iss)
                _emit(f"  {'OK ' if ok else 'ERR'} {iss.title} -> {msg.replace(chr(10), ' ')}")
        return 0

    if cmd == "systemfix":
        from .core.elevate import apply_system_fix, fix_system_conflicts, is_admin, write_result

        target = argv[1] if len(argv) > 1 and not argv[1].startswith("-") else "all"
        if is_admin():
            res = apply_system_fix(mgr, target)
            write_result(res)
        else:
            ok, msg, removed = fix_system_conflicts(mgr, target)
            res = {"ok": ok, "removed": removed, "error": "" if ok else msg}
        removed = res.get("removed") or []
        _emit(("OK  " if res.get("ok") else "ERR ")
              + (f"已清理 {len(removed)} 条系统 PATH 条目" if res.get("ok")
                 else res.get("error", "清理失败")))
        return 0 if res.get("ok") else 1

    if cmd == "systemdrop":
        # 内部命令：由 fix_system_drop() 提权后调用，按精确路径清理系统 PATH
        from .core.elevate import apply_system_drop, decode_arg, write_result

        try:
            entries = decode_arg(argv[1]) if len(argv) > 1 else []
        except Exception as e:  # noqa: BLE001
            write_result({"ok": False, "removed": [], "error": f"参数解析失败：{e}"})
            return 1
        res = apply_system_drop(list(entries))
        write_result(res)
        return 0 if res.get("ok") else 1

    if cmd == "systempath-restore":
        from .core.elevate import is_admin, restore_system_path, write_result

        # smart=True 时不会把"放回后又会抢在选中版本前面"的条目放回去，
        # 否则用户会遇到"还原一次又乱了"；--all 表示强制全量还原。
        smart = "--all" not in argv
        if is_admin():
            res = restore_system_path(mgr, smart=smart)
        else:
            res = {"ok": False, "added": [], "held": [],
                   "error": "需要管理员权限（请用管理员身份运行本命令）"}
        write_result(res)
        if res.get("ok"):
            n = len(res.get("added") or [])
            _emit("OK   " + (f"已还原系统 PATH，补回 {n} 条" if n else "系统 PATH 已经和备份一致"))
        else:
            _emit("ERR  " + str(res.get("error") or "还原失败"))
        return 0 if res.get("ok") else 1

    if cmd == "doctor":
        _emit(mgr.diagnose())
        return 0

    _emit(USAGE)
    return 0


def main() -> int:
    return cli()


if __name__ == "__main__":
    raise SystemExit(main())
