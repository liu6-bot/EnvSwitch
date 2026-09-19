"""环境变量写入后端。

两套实现，对外暴露同一组方法：

    WindowsBackend —— 写 ``HKCU\\Environment``（用户级，不需要管理员权限）
    UnixBackend    —— 写 shell 启动文件的**标记块**（bash/zsh/fish），可完整撤销

两者都会在切换时返回一条 ApplyRecord，记录在配置文件里，用于「还原」。
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .models import IS_WIN, SYSTEM, norm_path

MARK_PREFIX = "EnvSwitch"


def marker(tool_id: str) -> tuple:
    return (f"# >>> {MARK_PREFIX}:{tool_id} >>>", f"# <<< {MARK_PREFIX}:{tool_id} <<<")


@dataclass
class ApplyPlan:
    """一次切换要做的所有环境变量改动。"""

    tool_id: str
    home_var: str = ""
    home_value: str = ""
    extra_vars: Dict[str, str] = field(default_factory=dict)
    path_entries: List[str] = field(default_factory=list)
    other_entries: List[str] = field(default_factory=list)   # 同工具其它版本的 PATH 条目（一并纳入，只改顺序）
    conflict_keywords: List[str] = field(default_factory=list)


@dataclass
class ApplyRecord:
    """切换完成后留下的"撤销凭据"。"""

    backend: str
    plan: dict
    removed_path: List[str] = field(default_factory=list)   # Windows：历史兼容字段（reorder 模型下一般为空）
    added: List[str] = field(default_factory=list)          # Windows：本次新加入 PATH 的条目（revert 时只撤这些）
    files: List[str] = field(default_factory=list)          # Unix：写入过的配置文件

    def to_dict(self) -> dict:
        return {
            "backend": self.backend,
            "plan": self.plan,
            "removed_path": self.removed_path,
            "added": self.added,
            "files": self.files,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ApplyRecord":
        return cls(
            backend=d.get("backend", ""),
            plan=d.get("plan", {}) or {},
            removed_path=d.get("removed_path", []) or [],
            added=d.get("added", []) or [],
            files=d.get("files", []) or [],
        )


# --------------------------------------------------------------------------- #
# Windows
# --------------------------------------------------------------------------- #


class WindowsBackend:
    name = "windows"

    def __init__(self, log=print):
        self.log = log
        import winreg  # noqa: PLC0415

        self.winreg = winreg

    # ---- 注册表读写 ---- #

    def _open(self, access):
        return self.winreg.OpenKey(self.winreg.HKEY_CURRENT_USER, "Environment", 0, access)

    def get(self, name: str) -> str:
        try:
            with self._open(self.winreg.KEY_READ) as k:
                return self.winreg.QueryValueEx(k, name)[0]
        except Exception:  # noqa: BLE001
            return os.environ.get(name, "")

    def set(self, name: str, value: str, expand: bool = False) -> None:
        with self._open(self.winreg.KEY_ALL_ACCESS) as k:
            self.winreg.SetValueEx(
                k, name, 0,
                self.winreg.REG_EXPAND_SZ if expand else self.winreg.REG_SZ,
                value,
            )
        os.environ[name] = value

    def unset(self, name: str) -> None:
        try:
            with self._open(self.winreg.KEY_ALL_ACCESS) as k:
                self.winreg.DeleteValue(k, name)
        except FileNotFoundError:
            pass
        except Exception as e:  # noqa: BLE001
            self.log(f"删除环境变量 {name} 失败：{e}")
        os.environ.pop(name, None)

    def get_path(self) -> List[str]:
        raw = self.get("Path")
        if not raw:
            raw = os.environ.get("PATH", "")
        return [x for x in raw.split(os.pathsep) if x.strip()]

    def set_path(self, items: List[str]) -> None:
        with self._open(self.winreg.KEY_ALL_ACCESS) as k:
            self.winreg.SetValueEx(k, "Path", 0, self.winreg.REG_EXPAND_SZ, os.pathsep.join(items))
        self._sync_process_path(items)
        self.broadcast()

    def _sync_process_path(self, user_items: List[str]) -> None:
        """把本进程的 PATH 同步成「系统段 + 用户段」。

        Windows 真正生效的 PATH 就是「系统 PATH 在前、用户 PATH 在后」。
        旧版这里直接 ``os.environ["PATH"] = 用户段``，等于把自己的系统段抹掉了：
        本进程里 `where` / `shutil.which` 立刻找不到系统命令，
        "切换到底生效没有"的自查会得出错误结论，顺带拉起的终端也是坏的 PATH。
        """
        sys_items = self.system_path()
        seen = {norm_path(p) for p in user_items}
        merged = [p for p in sys_items if norm_path(p) not in seen] + list(user_items)
        os.environ["PATH"] = os.pathsep.join(merged)

    @staticmethod
    def broadcast() -> None:
        """通知资源管理器/其它进程环境变量已变更，新开的窗口立即生效。"""
        try:
            import ctypes

            HWND_BROADCAST = 0xFFFF
            WM_SETTINGCHANGE = 0x001A
            ctypes.windll.user32.SendMessageTimeoutW(
                HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", 2, 5000,
                ctypes.byref(ctypes.c_ulong()),
            )
        except Exception:  # noqa: BLE001
            pass

    # ---- 冲突检测 ---- #

    def system_path(self) -> List[str]:
        """读取系统级 PATH（只读，不需要管理员）。"""
        try:
            with self.winreg.OpenKey(
                self.winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
                0, self.winreg.KEY_READ,
            ) as k:
                return [x for x in self.winreg.QueryValueEx(k, "Path")[0].split(os.pathsep) if x.strip()]
        except Exception:  # noqa: BLE001
            return []

    @staticmethod
    def _is_var_reference(item: str, ignore_vars) -> bool:
        """判断 PATH 条目是否是"合法引用我们管理的变量"（如 %JAVA_HOME%\\bin）。

        这类条目会跟着 JAVA_HOME 一起变，属于理想状态，不能算冲突。
        注意 ``%%JAVA_HOME%\\bin`` 这种百分号没配对的写法**不算**——它展开后是死路径。
        """
        return is_managed_var_ref(item, ignore_vars)

    def system_conflicts(self, keywords: List[str], ignore_vars: Optional[List[str]] = None) -> List[str]:
        """系统级 PATH 里可能与我们冲突的条目。

        注意：Windows 的生效顺序是「系统 PATH 在前，用户 PATH 在后」，
        所以若系统 PATH 里躺着另一个 java/python，我们加到用户 PATH 也压不过它。
        这时必须先清理（需要管理员）或改用「打开已生效终端」。
        """
        hits = []
        for item in self.system_path():
            if self._is_var_reference(item, ignore_vars):
                continue
            low = norm_path(item).replace("\\", "/").lower()
            if any(k.lower().strip("\\/") in low for k in keywords):
                hits.append(item)
        return hits

    # ---- 系统级 PATH：冲突清理（需要管理员权限） ---- #

    SYSTEM_KEY = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"

    def _open_system(self, access):
        return self.winreg.OpenKey(self.winreg.HKEY_LOCAL_MACHINE, self.SYSTEM_KEY, 0, access)

    def write_system_path(self, items: List[str]) -> None:
        """写系统级 PATH（需要管理员权限）。"""
        with self._open_system(self.winreg.KEY_ALL_ACCESS) as k:
            self.winreg.SetValueEx(k, "Path", 0, self.winreg.REG_EXPAND_SZ, os.pathsep.join(items))
        self.broadcast()

    # 说明：按关键词盲删系统 PATH 的 strip_system_path() 已经被移除。
    # 它会把「命中关键词」当成「和我们冲突」，于是 python 的关键词 "python"
    # 能把 C:\Python314 一起删掉 —— 那是机器上唯一的 Python 入口。
    # 现在统一走 elevate.apply_system_fix()：逐条模拟 + 可达性安全闸 + 备份。

    # ---- 应用 / 撤销 ---- #

    def apply(self, plan: ApplyPlan) -> ApplyRecord:
        if plan.home_var and plan.home_value:
            self.set(plan.home_var, plan.home_value)
        for k, v in (plan.extra_vars or {}).items():
            if v:
                self.set(k, v)

        # 「全部纳入 + 只改顺序」模型：
        #   1. 选中版本的 PATH 条目 → 按 plan 声明的顺序排最前；
        #   2. 同工具其它版本的 PATH 条目（other_entries）→ 紧跟其后（同样按声明顺序）；
        #   3. 其余（未管理的、其它工具、系统段保留下来的）→ 保持原相对顺序。
        # 切换**绝不删除**任何原有条目 —— 旧版本只是被排到后面，不会"消失"；
        # 命令行解析命中同名命令时，排在前面的先赢，所以选中版本一定生效。
        #
        # 顺序以 plan 里 path_entries / other_entries 的声明顺序为准：已存在的条目
        # 复用 PATH 里原来的写法（大小写/引号），不存在的（新装版本）就地补到对应位置。
        sel_order = [e for e in plan.path_entries if os.path.isdir(e)]
        oth_order = [e for e in (getattr(plan, "other_entries", []) or []) if os.path.isdir(e)]
        sel_set = {norm_path(e) for e in sel_order}
        oth_set = {norm_path(e) for e in oth_order}

        items = self.get_path()
        existing: Dict[str, str] = {}
        for it in items:
            existing.setdefault(norm_path(it), it)   # 同一条目多个写法时，保留第一个

        sel_block, oth_block, added = [], [], []
        for e in sel_order:
            k = norm_path(e)
            if k in existing:
                sel_block.append(existing[k])
            else:
                sel_block.append(e)
                added.append(e)
        for e in oth_order:
            k = norm_path(e)
            if k in existing:
                oth_block.append(existing[k])
            else:
                oth_block.append(e)
                added.append(e)

        # 其余条目：既不在选中版本、也不在同工具其它版本里的，保持原相对顺序
        rest = [it for it in items
                if norm_path(it) not in sel_set and norm_path(it) not in oth_set]

        merged = sel_block + oth_block + rest
        seen, final = set(), []
        for x in merged:
            k = norm_path(x)
            if k in seen:
                continue
            seen.add(k)
            final.append(x)

        self.set_path(final)
        return ApplyRecord(backend=self.name, plan=plan.__dict__, removed_path=[], added=added)

    def revert(self, record: ApplyRecord) -> None:
        plan = record.plan or {}
        # home_value 为空说明这次切换**本来就没写**那个变量（商店安装就不写主目录变量），
        # 那就不能反手把它 unset 掉 —— 否则用户原本自己的变量会被我们清掉。
        if plan.get("home_var") and plan.get("home_value"):
            self.unset(plan["home_var"])
        for k in (plan.get("extra_vars") or {}):
            self.unset(k)

        # reorder 模型里我们只"新增"、从不"删除"用户原有条目。
        # 还原 = 把本次切换新加入的条目摘掉即可；原本就在 PATH 里的条目原样保留
        # （顺序停留在切换后的状态）。这样最安全：绝不会因为还原把用户的工具弄丢。
        added = {norm_path(e) for e in record.added}
        items = [x for x in self.get_path() if norm_path(x) not in added]
        self.set_path(items)
        self.broadcast()


# --------------------------------------------------------------------------- #
# Unix（macOS / Linux）
# --------------------------------------------------------------------------- #


class UnixBackend:
    name = "unix"

    def __init__(self, log=print):
        self.log = log

    def rc_files(self) -> List[Path]:
        home = Path.home()
        shell = os.environ.get("SHELL", "")
        cands: List[Path] = []
        if "zsh" in shell:
            cands.append(home / ".zshrc")
        if "bash" in shell:
            cands.append(home / (".bash_profile" if SYSTEM == "macos" else ".bashrc"))
        if "fish" in shell:
            cands.append(home / ".config" / "fish" / "config.fish")
        # 兜底：把所有存在的常见配置都算上（用户可能同时用多个 shell）
        for f in (
            home / ".bashrc", home / ".bash_profile", home / ".profile",
            home / ".zshrc", home / ".zprofile",
            home / ".config" / "fish" / "config.fish",
        ):
            if f not in cands:
                cands.append(f)

        seen, uniq = set(), []
        for f in cands:
            if f not in seen:
                seen.add(f)
                uniq.append(f)
        existing = [f for f in uniq if f.exists()]
        if existing:
            return existing
        return [home / (".zshrc" if SYSTEM == "macos" else ".bashrc")]

    @staticmethod
    def _strip(text: str, begin: str, end: str) -> str:
        text = re.sub(
            re.escape(begin) + r".*?" + re.escape(end) + r"\n?", "", text, flags=re.DOTALL
        )
        return text.rstrip() + "\n"

    def _body(self, plan: ApplyPlan, fish: bool) -> str:
        lines: List[str] = []
        entries = [e for e in (list(plan.path_entries)
                               + list(getattr(plan, "other_entries", []) or []))
                   if os.path.isdir(e)]
        if fish:
            if plan.home_var and plan.home_value:
                lines.append(f"set -gx {plan.home_var} {shlex.quote(plan.home_value)}")
            for k, v in (plan.extra_vars or {}).items():
                if v:
                    lines.append(f"set -gx {k} {shlex.quote(v)}")
            if entries:
                quoted = " ".join(shlex.quote(e) for e in entries)
                lines.append(f"set -gx PATH {quoted} $PATH")
        else:
            if plan.home_var and plan.home_value:
                lines.append(f"export {plan.home_var}={shlex.quote(plan.home_value)}")
            for k, v in (plan.extra_vars or {}).items():
                if v:
                    lines.append(f"export {k}={shlex.quote(v)}")
            if entries:
                quoted = ":".join(shlex.quote(e) for e in entries)
                lines.append(f'export PATH="{quoted}:$PATH"')
        return "\n".join(lines)

    def apply(self, plan: ApplyPlan) -> ApplyRecord:
        begin, end = marker(plan.tool_id)
        written: List[str] = []
        for f in self.rc_files():
            try:
                fish = f.name == "config.fish"
                body = self._body(plan, fish)
                if not body:
                    continue
                f.parent.mkdir(parents=True, exist_ok=True)
                old = f.read_text(encoding="utf-8") if f.exists() else ""
                old = self._strip(old, begin, end)
                f.write_text(old + f"\n{begin}\n{body}\n{end}\n", encoding="utf-8")
                written.append(str(f))
            except Exception as e:  # noqa: BLE001
                self.log(f"写入 {f} 失败：{e}")
        return ApplyRecord(backend=self.name, plan=plan.__dict__, files=written)

    def revert(self, record: ApplyRecord) -> None:
        plan = record.plan or {}
        begin, end = marker(plan.get("tool_id", ""))
        for p in record.files:
            f = Path(p)
            if not f.exists():
                continue
            try:
                old = f.read_text(encoding="utf-8")
                new = self._strip(old, begin, end)
                if new != old:
                    f.write_text(new, encoding="utf-8")
            except Exception as e:  # noqa: BLE001
                self.log(f"清除 {f} 失败：{e}")

    def system_conflicts(self, keywords: List[str]) -> List[str]:
        return []


def get_backend(log=print):
    return WindowsBackend(log) if IS_WIN else UnixBackend(log)


# --------------------------------------------------------------------------- #
# 系统级 PATH 备份（Windows 专用，配合提权修复使用）
# --------------------------------------------------------------------------- #


def system_backup_file():
    return Path.home() / ".envswitch" / "system_path_backup.json"


def save_backup(items: List[str]) -> None:
    """备份当前系统 PATH。已有备份时不覆盖（保留最原始的一份）。"""
    f = system_backup_file()
    if f.exists():
        return
    try:
        import time

        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "path": os.pathsep.join(items),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def load_backup() -> Optional[dict]:
    f = system_backup_file()
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# 通用：构造子进程环境 / 打开已生效终端
# --------------------------------------------------------------------------- #


def build_env(plan: ApplyPlan, base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """在不改动系统的前提下，算出一份"已切换"的环境变量，用于启动子进程/终端。"""
    env = dict(base if base is not None else os.environ)
    if plan.home_var and plan.home_value:
        env[plan.home_var] = plan.home_value
    for k, v in (plan.extra_vars or {}).items():
        if v:
            env[k] = v
    entries = [e for e in plan.path_entries if os.path.isdir(e)]
    key = "PATH" if "PATH" in env else "Path"
    cur = env.get(key, "")
    rest = [x for x in cur.split(os.pathsep) if x.strip() and norm_path(x) not in {norm_path(e) for e in entries}]
    env[key] = os.pathsep.join(entries + rest)
    return env


def launch_terminal(env: Dict[str, str], cwd: Optional[str] = None) -> tuple:
    """开一个新终端窗口，里面的环境已经切换好了。

    这是"立刻验证"的关键：系统级改动要等新进程读取才生效，
    而这个终端是我们带着新环境直接拉起来的，打开即用。
    """
    cwd = cwd or os.getcwd()
    try:
        if IS_WIN:
            for cmd in (
                ["wt.exe", "-d", cwd],
                ["powershell.exe", "-NoExit", "-Command", f"Set-Location '{cwd}'"],
                ["cmd.exe", "/k", f"cd /d {cwd}"],
            ):
                try:
                    subprocess.Popen(cmd, env=env, cwd=cwd,
                                     creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
                    return True, f"已打开新终端（{cmd[0]}）"
                except FileNotFoundError:
                    continue
            return False, "没有找到可用的终端程序"

        if SYSTEM == "macos":
            import tempfile

            with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fp:
                for k, v in env.items():
                    if k in ("PATH", "HOME", "SHELL", "USER", "LOGNAME", "TMPDIR"):
                        fp.write(f"export {k}={shlex.quote(v)}\n")
                fp.flush()
                script = fp.name
            os.chmod(script, 0o755)
            subprocess.Popen([
                "osascript", "-e",
                f'tell application "Terminal" to do script "source {script}; rm -f {script}"',
            ])
            return True, "已打开 Terminal（新标签页）"

        for term in (
            ["gnome-terminal", "--", "bash", "-c"],
            ["konsole", "-e", "bash", "-c"],
            ["xfce4-terminal", "-e", "bash", "-c"],
            ["xterm", "-e", "bash", "-c"],
        ):
            try:
                inner = "export PATH='%s'; cd '%s'; exec bash" % (env.get("PATH", ""), cwd)
                subprocess.Popen(term + [inner], env=env, cwd=cwd)
                return True, f"已打开新终端（{term[0]}）"
            except FileNotFoundError:
                continue
        return False, "没有找到可用的终端模拟器"
    except Exception as e:  # noqa: BLE001
        return False, str(e)
