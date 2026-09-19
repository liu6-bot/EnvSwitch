"""EnvManager —— 对外统一门面。

GUI / CLI / 未来的任何前端都只跟这个类打交道，不直接碰注册表和 shell 配置文件。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import env as envmod
from .builtins import BUILTIN_TOOLS
from .config import Config
from .env import ApplyPlan, ApplyRecord, build_env, get_backend, launch_terminal
from .models import Install, ToolDef, is_store_alias, is_store_stub, major_of, norm_path, version_key
from .scanner import Scanner


class EnvManager:
    def __init__(self, config: Optional[Config] = None, log=print):
        self.config = config or Config()
        self.log = log
        self.scanner = Scanner(log=log, reference_path=self.effective_path)
        self.backend = get_backend(log)
        self._cache: Dict[str, List[Install]] = {}

    # ---------------------------- 生效的环境 ---------------------------- #

    def effective_path(self) -> List[str]:
        """本机**真正生效**的 PATH：系统段 + 用户段（去重、丢空项）。

        和 ``os.environ["PATH"]`` 的区别很重要：进程自己继承来的 PATH 取决于
        EnvSwitch 是从哪儿启动的（从 IDE / Git Bash / 某个工具链的终端里启动时
        往往是被改过的），拿它判断"装了什么、当前是哪个版本"会得出错误结论。
        """
        items: List[str] = []
        if hasattr(self.backend, "system_path"):
            try:
                items += self.backend.system_path()
            except Exception:  # noqa: BLE001
                pass
        try:
            items += self.backend.get_path()
        except Exception:  # noqa: BLE001
            pass
        seen, out = set(), []
        for p in items:
            if not p or not p.strip():
                continue
            k = norm_path(p)
            if k in seen:
                continue
            seen.add(k)
            out.append(p)
        if not out:
            out = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p.strip()]
        return out

    def lookup_path(self) -> List[str]:
        """把生效 PATH 展开 %VAR% 后的结果，用于 which / where 查找。"""
        out: List[str] = []
        for p in self.effective_path():
            try:
                out.append(os.path.expandvars(p.strip().strip('"')))
            except Exception:  # noqa: BLE001
                out.append(p)
        return out

    def var_value(self, name: str) -> str:
        """读环境变量的**持久值**（注册表 / shell 配置），而不是本进程继承来的值。"""
        if not name:
            return ""
        getter = getattr(self.backend, "get", None)
        if callable(getter):
            try:
                v = getter(name)
                if v:
                    return v
            except Exception:  # noqa: BLE001
                pass
        return os.environ.get(name, "")

    # ------------------------------- 工具表 ------------------------------- #

    def tools(self) -> List[ToolDef]:
        out: List[ToolDef] = []
        for t in BUILTIN_TOOLS:
            if self.config.is_disabled(t.id):
                continue
            out.append(t)
        out += [t for t in self.config.custom_tools() if t.enabled]
        return out

    def all_tools(self) -> List[ToolDef]:
        """包含被禁用的内置工具（设置页用）。"""
        return list(BUILTIN_TOOLS) + self.config.custom_tools()

    def tool(self, tool_id: str) -> Optional[ToolDef]:
        for t in self.all_tools():
            if t.id == tool_id:
                return t
        return None

    def add_tool(self, tool: ToolDef) -> Tuple[bool, str]:
        if not tool.id or not tool.name:
            return False, "工具 ID 与名称不能为空"
        if self.tool(tool.id) and self.tool(tool.id).builtin:  # type: ignore[union-attr]
            return False, f"ID「{tool.id}」与内置工具冲突，换一个"
        self.config.add_tool(tool)
        return True, f"已保存工具「{tool.name}」"

    def remove_tool(self, tool_id: str) -> Tuple[bool, str]:
        t = self.tool(tool_id)
        if not t:
            return False, "工具不存在"
        if t.builtin:
            return False, "内置工具不能删除，可以在设置里隐藏"
        self.config.remove_tool(tool_id)
        self._cache.pop(tool_id, None)
        return True, f"已删除工具「{t.name}」"

    def set_tool_enabled(self, tool_id: str, enabled: bool) -> None:
        t = self.tool(tool_id)
        if not t:
            return
        if t.builtin:
            self.config.set_disabled(tool_id, not enabled)
        else:
            t.enabled = enabled
            self.config.add_tool(t)

    # ------------------------------- 扫描 ------------------------------- #

    def scan(self, tool_id: str, refresh: bool = False) -> List[Install]:
        if not refresh and tool_id in self._cache:
            return self._cache[tool_id]
        tool = self.tool(tool_id)
        if not tool:
            return []
        self._seed_from_applied()
        installs = self.scanner.scan(
            tool,
            self.config.custom_paths(tool_id),
            extra_roots=self._learned_roots(tool_id),
        )
        installs = self._merge_remembered(tool, installs)
        self._remember(tool, installs)
        self._mark_current(tool, installs)
        self._cache[tool_id] = installs
        return installs

    # --------------------------- 安装记忆（永不丢版本） --------------------------- #

    def _learned_roots(self, tool_id: str) -> List[str]:
        """学到的扫描根只对 **dir 模式**工具启用（Java / Go / Maven…）。

        dir 模式里"多版本"就是同一父目录下的一排目录（``D:\\soft\\Java\\jdk-17`` 旁边
        还有 ``jdk1.8.0_202``），学父目录几乎不会误伤。
        而 file 模式（Python / Node / .NET）的父目录里什么都有 —— 本机实测把 ``D:\\soft``
        学成 Node 的扫描根之后，Adobe Photoshop 自带的 ``node.exe`` 也被当成"一个 Node 安装"
        列了出来。file 模式的"版本离开 PATH"已经由 ``known_installs`` 记忆兜住，
        内置扫描根（``C:\\Python*`` 等）也覆盖了同盘多版本的常见布局，不需要再学父目录。
        """
        tool = self.tool(tool_id)
        if not tool or tool.entry != "dir":
            return []
        out: List[str] = []
        for d in self.config.learned_roots(tool_id) or []:
            if d and os.path.isdir(d) and d not in out:
                out.append(d)
        return out

    def _merge_remembered(self, tool: ToolDef, installs: List[Install]) -> List[Install]:
        """把"以前见过、现在磁盘上还在"的安装补回列表。

        这是「切换之后原来的版本会消失」的根治。扫描器原本唯一的"记忆"就是 PATH：
        某版本一旦从 PATH 里掉出去（历史版本切换时删过、用户手改了 PATH、换了机器账户），
        靠 ``where`` 反查就永远找不回来 —— 用户看到列表里少了一项，还以为是工具"弄丢"了。

        现在只要见过一次就记下来，之后每轮扫描都拿磁盘现状复核：目录/主程序还在就继续列出，
        真被卸载了才丢掉（不会阴魂不散）。
        """
        have = {norm_path(i.home) for i in installs}
        forgotten = set(self.config.forgotten_installs(tool.id) or [])
        for d in self.config.known_installs(tool.id) or []:
            try:
                ins = Install.from_dict(d)
            except Exception:  # noqa: BLE001
                continue
            if not ins.home:
                continue
            key = norm_path(ins.home)
            if key in have or key in forgotten:
                continue
            probe = ins.exe if (tool.entry == "file" and ins.exe) else ins.home
            if not self.scanner._live(probe):
                continue
            if not os.path.isdir(ins.home):
                continue
            if not tool.is_valid_home(ins.home, ins.exe if tool.entry == "file" else ""):
                continue
            ins.source = "remembered"
            ins.current = False
            ins.is_default = False
            installs.append(ins)
            have.add(key)
        installs.sort(key=lambda i: version_key(i.version), reverse=True)
        return installs

    @staticmethod
    def _parent_dir(p: str) -> str:
        """安装目录的父目录；顶层/盘符根返回 ""（扫盘符根既没意义又很慢）。"""
        p = (p or "").rstrip("\\/")
        par = os.path.dirname(p)
        if not par or par == p or par.rstrip("\\/").endswith(":"):
            return ""
        return par

    def _remember(self, tool: ToolDef, installs: List[Install]) -> None:
        """把本轮结果写进记忆，并顺手把"各版本的父目录"学成扫描根。

        只对 **dir 模式**的普通安装目录学父目录：
          * file 模式（Python / Node）的父目录里什么都有，学了会把 Photoshop 自带的
            ``node.exe`` 之类也当安装列出来（本机实测踩过）；
          * 商店别名那种 home 是 WindowsApps 下的转发目录，父目录跟安装布局无关。
        """
        roots: List[str] = []
        if tool.entry == "dir":
            for i in installs:
                if is_store_alias(i.home):
                    continue
                par = self._parent_dir(i.home)
                if par and par not in roots:
                    roots.append(par)
        self.config.remember(tool.id, [i.to_dict() for i in installs], roots[:12])

    def _seed_from_applied(self) -> None:
        """一次性补种：从旧的切换记录里把"曾经管过的安装"捡回记忆。

        历史版本切换时会把旧版本的 PATH 条目删掉（记录里的 ``removed_path`` 就是证据），
        那些版本于是再也扫不到 —— 用户看到的就是"切完之后少了一个选项"。
        这里顺着记录的 ``install`` / ``path_entries`` / ``other_entries`` / ``removed_path``
        反推出安装目录，补进 ``known_installs``，让它们重新出现在列表里；
        随后 ``learned_roots`` 又会把父目录学成扫描根，连带找回同目录的兄弟版本。
        """
        if self.config.scan_seeded():
            return
        seeded = dict(self.config.data.get("known_installs") or {})
        try:
            for tid, rec in (self.config.data.get("applied") or {}).items():
                tool = self.tool(tid)
                if not tool:
                    continue
                rec = rec or {}
                r = rec.get("record") or {}
                plan = r.get("plan") or {}
                cands: List[str] = []
                inst = rec.get("install") or {}
                if inst.get("home"):
                    cands.append(inst["home"])
                cands += list(plan.get("path_entries") or [])
                cands += list(plan.get("other_entries") or [])
                cands += list(r.get("removed_path") or [])

                arr = list(seeded.get(tid) or [])
                for c in cands:
                    try:
                        home, exe = self.scanner._interpret(tool, c)
                        if not home:
                            home = self.scanner.install_root_of(tool, c)
                            exe = (self.scanner.main_exe_in(tool, home)
                                   if (home and tool.entry == "file") else "")
                        if not home or not os.path.isdir(home):
                            continue
                        probe = exe if (tool.entry == "file" and exe) else home
                        if not self.scanner._live(probe):
                            continue
                        if not tool.is_valid_home(home, exe if tool.entry == "file" else ""):
                            continue
                        if any(norm_path(x.get("home", "")) == norm_path(home) for x in arr):
                            continue
                        d = Install(
                            tool_id=tid, home=home, exe=exe, source="remembered",
                            version=self.scanner._version(tool, home, exe),
                            vendor=self.scanner._vendor(tool, home),
                        ).to_dict()
                        arr.append(d)
                    except Exception:  # noqa: BLE001
                        continue
                if arr:
                    seeded[tid] = arr
            self.config.data["known_installs"] = seeded
        except Exception:  # noqa: BLE001
            pass
        self.config.set_scan_seeded(True)

    def scan_all(self, refresh: bool = False) -> Dict[str, List[Install]]:
        return {t.id: self.scan(t.id, refresh) for t in self.tools()}

    def _mark_current(self, tool: ToolDef, installs: List[Install]) -> None:
        """标记"实际生效"与"变量指向"——这两件事必须分开标。

        ``JAVA_HOME`` 说 26、PATH 里却先命中 17 的时候，旧版会把两行都打上
        "当前"，用户看到表里两个"当前"只会更糊涂（这正是"怎么越修复越乱"的来源）。
        现在 ``current`` 只给真正生效的那一个，变量指向的用 ``is_default`` 弱标记，
        于是"变量说 A、命令行走的是 B"一眼就能看出来。
        """
        resolved = self._current_exe(tool)
        home_env = self.var_value(tool.home_var) if tool.home_var else ""
        if is_store_stub(home_env):
            home_env = ""       # 空壳商店别名不是任何安装
        for ins in installs:
            ins.current = False
            ins.is_default = False

        # 1) 主目录变量指向谁（"已设为默认"，但未必真生效）
        if home_env:
            for ins in installs:
                if norm_path(home_env) == norm_path(ins.home):
                    ins.is_default = True
                    break

        # 2) 真正生效的：PATH 里第一个命中它可执行文件的安装；全局只标一个
        if resolved:
            for ins in installs:
                if tool.entry == "file" and ins.exe and norm_path(resolved) == norm_path(ins.exe):
                    ins.current = True
                    break
                if norm_path(resolved).startswith(norm_path(ins.home) + os.sep):
                    ins.current = True
                    break

        # 3) 一个都没命中时（典型：Oracle javapath / Homebrew shim 这类转发器），
        #    探测 PATH 里那个命令的实际版本，和列表里的版本对号入座
        if not any(i.current for i in installs) and resolved:
            ver = self._probe_bin(tool, resolved)
            for ins in installs:
                if ver and ins.version and (ver == ins.version or ver.startswith(ins.version)):
                    ins.current = True
                    break
            else:
                for ins in installs:
                    if norm_path(ins.home) in norm_path(resolved):
                        ins.current = True
                        break

    def _probe_bin(self, tool: ToolDef, bin_path: str) -> str:
        import re as _re  # noqa: PLC0415

        from .scanner import run as _run  # noqa: PLC0415

        rc, out = _run(tool.version_command_bin(bin_path))
        if rc != 0 or not out:
            return ""
        if tool.version_regex:
            m = _re.search(tool.version_regex, out)
            if m:
                return (m.group(1) if m.groups() else m.group(0)).strip()
        return out.splitlines()[0].strip()

    def _current_exe(self, tool: ToolDef) -> str:
        path = os.pathsep.join(self.lookup_path())
        for name in tool.bin_names or []:
            try:
                p = shutil.which(name, path=path)
            except Exception:  # noqa: BLE001
                p = None
            # 空壳商店别名（应用已卸载）不算"当前版本"：跑它只会弹应用商店；
            # 但商店版**确实装着**的别名要算 —— 用户 PATH 里第一个命中的就是它，得如实报出来
            if p and not is_store_stub(p):
                try:
                    return str(Path(os.path.realpath(p)))
                except Exception:  # noqa: BLE001
                    return p
        return ""

    def current_install(self, tool_id: str) -> Optional[Install]:
        for ins in self.scan(tool_id):
            if ins.current:
                return ins
        return None

    def current_version(self, tool_id: str) -> str:
        ins = self.current_install(tool_id)
        if ins:
            return ins.version or os.path.basename(ins.home)
        tool = self.tool(tool_id)
        if not tool:
            return "未检测到"
        exe = self._current_exe(tool)
        if exe:
            v = self._probe_bin(tool, exe)
            return v or os.path.basename(str(Path(exe).parent))
        if tool.home_var and self.var_value(tool.home_var):
            val = self.var_value(tool.home_var)
            # 变量里写的东西不一定真是一个安装（比如被 Store 别名写坏的那阵子），
            # 那种情况别拿它当"当前版本"糊弄用户
            if not is_store_alias(val) and (
                self.scanner._interpret(tool, val)[0] or self.scanner.main_exe_in(tool, val)
            ):
                return val
        return "未检测到"

    # ------------------------------- 切换 ------------------------------- #

    def plan_for(self, tool: ToolDef, ins: Install) -> ApplyPlan:
        """把一次切换摊成「要写哪些变量 + 要前置哪些 PATH 条目」。

        商店安装特殊处理：它的 home 只是 ``%LOCALAPPDATA%\\Microsoft\\WindowsApps``
        这个塞满各种别名的转发目录，**不是**真正的主目录，所以：

          * 不写主目录变量（写了等于把 PYTHON_HOME 指到一个假目录，后患无穷）；
          * 不写附加变量；
          * 只把那个别名目录前置到 PATH —— 这才是商店版生效的方式。
        """
        store = self.scanner.store_live(tool, ins.home, ins.exe)
        return ApplyPlan(
            tool_id=tool.id,
            home_var=tool.home_var,
            home_value="" if store else ins.home,
            extra_vars={} if store else tool.extra_vars_for(ins.home, ins.exe),
            path_entries=tool.path_entries_for(ins.home, ins.exe),
            conflict_keywords=tool.conflict_keywords,
        )

    def use(self, tool_id: str, ins: Install) -> Tuple[bool, str, List[str]]:
        """切换某个工具到指定安装。返回 (是否成功, 消息, 警告列表)。"""
        tool = self.tool(tool_id)
        if not tool:
            return False, f"未知工具：{tool_id}", []
        # 商店安装里也分两种：真装着（别名指向真实程序）可以切；
        # 空壳（应用已卸载，跑它只会弹应用商店）不能切。
        store = self.scanner.store_live(tool, ins.home, ins.exe)
        if (is_store_alias(ins.home) or is_store_alias(ins.exe)) and not store:
            return False, (f"{ins.home}\n是微软商店的「应用执行别名」，但它指向的程序已经不存在了"
                           f"（应用已卸载），不是真的 {tool.name} 安装，不能切换过去。"), []
        if not tool.is_valid_home(ins.home, ins.exe):
            return False, f"不是有效的{tool.name}安装：{ins.home}", []

        plan = self.plan_for(tool, ins)
        warnings: List[str] = []
        if store:
            warnings.append(
                f"这是微软商店（Microsoft Store）安装的 {tool.name}：\n"
                "  · 它靠「应用执行别名」生效，只会把 WindowsApps 目录前置到用户 PATH，"
                "不写主目录变量（那个目录不是真正的主目录）；\n"
                "  · Windows 的系统 PATH 优先级**高于**用户 PATH："
                "如果系统 PATH 里还有别的同名命令，这次切换不会真正生效 ——"
                "用「环境体检」处理那条冲突，或干脆以官方安装包为准。"
            )

        # 「全部纳入 + 只改顺序」：把本工具所有已安装版本的 PATH 条目都算进来，
        # 切换时只把选中版本调到最前，其它版本保持（排在后），绝不删除。
        # 这样无论切到哪个版本，其它版本都还在 PATH 里，不会"切换之后原来的会消失"。
        other_entries: List[str] = []
        try:
            all_installs = self.scan(tool_id, refresh=False)
        except Exception:  # noqa: BLE001
            all_installs = []
        sel_key = (norm_path(ins.home), ins.exe)
        for other in all_installs:
            if (norm_path(other.home), other.exe) == sel_key:
                continue
            for e in tool.path_entries_for(other.home, other.exe):
                if e and e not in other_entries:
                    other_entries.append(e)
        plan.other_entries = other_entries

        # 目录都不存在时不要"假装切换成功"：那只会在界面上报一句已切换，
        # 实际 PATH 一动不动，用户回头再体检还是同一个问题。
        if plan.path_entries and not [e for e in plan.path_entries if os.path.isdir(e)]:
            return False, (f"{ins.home} 下找不到可用的可执行目录"
                           f"（{'、'.join(plan.path_entries)}），PATH 无法添加"), []

        try:
            conflicts = self.backend.system_conflicts(tool.conflict_keywords, tool.managed_vars())
        except Exception:  # noqa: BLE001
            conflicts = []
        if conflicts:
            warnings.append(
                "系统级 PATH 里存在可能冲突的条目（Windows 系统 PATH 优先级高于用户 PATH）：\n  "
                + "\n  ".join(conflicts[:5])
                + "\n这些条目会让切换「看起来没生效」。可以用「环境体检」里的"
                  "「系统 PATH 冲突」一键处理（会先备份，随时能还原）。"
            )

        try:
            record = self.backend.apply(plan)
        except Exception as e:  # noqa: BLE001
            return False, f"写入环境变量失败：{e}", warnings

        self.config.set_applied(tool_id, {
            "record": record.to_dict(),
            "install": ins.to_dict(),
        })
        self._cache.pop(tool_id, None)

        if os.name == "nt":
            self._mark_current(tool, self.scan(tool_id, refresh=True))
            resolved = self._current_exe(tool)
            if resolved and not norm_path(resolved).startswith(norm_path(ins.home) + os.sep):
                warnings.append(
                    f"切换已写入，但当前 PATH 里 {tool.bin_names[0] if tool.bin_names else '命令'} "
                    f"仍解析到：{resolved}\n多半是上面那条系统 PATH 冲突，或需要重开终端。"
                )

        name = ins.title(tool)
        lines = [f"已将 {tool.name} 切换到 {name}", f"路径：{ins.home}"]
        if tool.home_var and not store:
            lines.append(f"{tool.home_var} = {ins.home}")
        elif store:
            lines.append("商店安装：只把 WindowsApps 别名目录前置到 PATH，不写主目录变量")
        if hasattr(self.backend, "files"):
            lines.append("提示：新开的终端窗口生效（或执行 source ~/.zshrc / ~/.bashrc）")
        else:
            lines.append("提示：新开的终端窗口生效；已打开的窗口需重启。")
        return True, "\n".join(lines), warnings

    def revert(self, tool_id: str) -> Tuple[bool, str]:
        data = self.config.applied(tool_id)
        if not data:
            return False, "该工具没有切换记录，无需还原"
        record = ApplyRecord.from_dict(data.get("record", {}))
        try:
            self.backend.revert(record)
        except Exception as e:  # noqa: BLE001
            return False, f"还原失败：{e}"
        self.config.set_applied(tool_id, None)
        self._cache.pop(tool_id, None)
        return True, f"已还原 {self.tool(tool_id).name if self.tool(tool_id) else tool_id} 的环境变量改动"

    def revert_all(self) -> Tuple[bool, str]:
        ids = list((self.config.data.get("applied") or {}).keys())
        if not ids:
            return True, "没有任何切换记录"
        ok, msgs = True, []
        for tid in ids:
            good, msg = self.revert(tid)
            ok = ok and good
            msgs.append(msg)
        return ok, "\n".join(msgs)

    def last_install(self, tool_id: str) -> Optional[Install]:
        return self.config.install_from_dict((self.config.applied(tool_id) or {}).get("install"))

    # ------------------------------ 项目级 ------------------------------ #

    def pin(self, tool_id: str, ins: Install, cwd: str = ".") -> Tuple[bool, str]:
        tool = self.tool(tool_id)
        if not tool or not tool.project_file:
            toolname = tool.name if tool else tool_id
            return False, f"{toolname} 没有配置项目级版本文件，可在「编辑工具」里填"
        value = {
            "major": major_of(ins.version),
            "path": ins.home,
        }.get(tool.project_value, ins.version)
        try:
            f = Path(cwd) / tool.project_file
            f.write_text(value + "\n", encoding="utf-8")
            return True, f"已写入 {f}（{value}）"
        except Exception as e:  # noqa: BLE001
            return False, str(e)

    # ------------------------------ 路径管理 ------------------------------ #

    def add_path(self, tool_id: str, path: str) -> Tuple[bool, str]:
        tool = self.tool(tool_id)
        if not tool:
            return False, "工具不存在"
        home, exe = self.scanner._interpret(tool, path)
        if not home and tool.entry == "file" and os.path.isdir(path):
            found = self.scanner.main_exe_in(tool, path)
            if found:
                home, exe = self.scanner._interpret(tool, found)
        # 商店别名分两种：真装着的可以纳入管理（切换时只前置别名目录、不写主目录变量）；
        # 空壳（应用已卸载，跑它只会弹应用商店）一律拒绝。
        # 判断要拿**换算之后**的主程序：别名目录自己是个目录，拿它判死活必然判不出来。
        if (is_store_alias(path) or is_store_alias(exe)) and not self.scanner.store_live(tool, path, exe):
            name = tool.name
            return False, (f"{path}\n是微软商店的「应用执行别名」，但它指向的程序已经不存在了"
                           f"（应用已卸载），不是真的 {name} 安装；要装真 {name} 请用官方安装包。")
        if not home:
            if tool.entry == "dir":
                return False, f"该目录下没有 {tool.detect_rel() or '可执行文件'}，不是有效的{tool.name}安装"
            return False, f"该目录下没有{tool.name}的主程序，不是有效的{tool.name}安装"
        self.config.add_path(tool_id, path)
        self._cache.pop(tool_id, None)
        return True, f"已添加：{home}"

    def remove_path(self, tool_id: str, path: str) -> None:
        self.config.remove_path(tool_id, path)
        self._cache.pop(tool_id, None)

    def forget_install(self, tool_id: str, home: str) -> None:
        """忘记一条"记错了的安装"（只压制记忆来源，不影响扫描器实际找到的结果）。"""
        self.config.forget_install(tool_id, home)
        self._cache.pop(tool_id, None)

    # ------------------------------ 终端 / 环境 ------------------------------ #

    def env_for(self, tool_id: str, ins: Install) -> dict:
        tool = self.tool(tool_id)
        if not tool:
            return dict(os.environ)
        return build_env(self.plan_for(tool, ins))

    def open_terminal(self, tool_id: str, ins: Install, cwd: Optional[str] = None) -> Tuple[bool, str]:
        return launch_terminal(self.env_for(tool_id, ins), cwd)

    # ------------------------------ 诊断信息 ------------------------------ #

    def diagnose(self) -> str:
        import platform
        from .models import SYSTEM

        lines = [
            f"EnvSwitch 配置目录：{self.config.dir}",
            f"配置文件：{self.config.path}",
            f"平台：{platform.system()} {platform.machine()}（{SYSTEM}）",
            f"后端：{self.backend.name}",
            "",
        ]
        for t in self.tools():
            cur = self.current_version(t.id)
            n = len(self.scan(t.id))
            lines.append(f"  {t.icon} {t.name:<12} 检测到 {n} 个，当前：{cur}")
        return "\n".join(lines)
