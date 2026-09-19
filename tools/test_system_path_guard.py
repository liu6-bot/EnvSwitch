#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""系统 PATH 清理的安全闸回归测试（内存后端 + 磁盘上真实存在的占位目录）。

为什么要在磁盘上建真目录：安全闸的「删掉之后命令行还能不能找到命令」判定走的是
provides_command() —— 它只看目录里是不是真有 python.exe / java.exe 这种文件。
如果像旧版测试那样用 C:\\Python314 / D:\\soft\\... 这种不存在的路径，provides_command
一律返回 False，闸会形同虚设、所有条目都被「安全保留」，反而测不出
「真实切换场景下要能放心删旧版本」这一面。所以这里在 temp 里建一组占位目录。

复现的事故：旧版按 conflict_keywords 关键词盲删，python 的关键词把机器上唯一的
Python 入口 C:\\Python314 一起删掉，于是「Python 一个安装都扫不到」。

跑法： python tools/test_system_path_guard.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from envswitch.core import env as envmod  # noqa: E402
from envswitch.core.builtins import JAVA, NODE, PYTHON  # noqa: E402
from envswitch.core import elevate as elevate_mod  # noqa: E402
from envswitch.core.elevate import apply_system_fix, fix_system_drop  # noqa: E402
from envswitch.core.models import Install, is_store_alias, provides_command, store_alias_target  # noqa: E402

# 回归测试必须「封闭」：强制走进程内的假后端，绝不真的弹 UAC、绝不真的改真实系统 PATH。
# 否则 fix_system_drop 在「非管理员」时会启动一个提权子进程，去动机器上真正的系统 PATH。
elevate_mod.is_admin = lambda: True  # noqa: E402

FAILED: list = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("  ✓ " if cond else "  ✗ ") + name + (f"   {extra}" if extra and not cond else ""))
    if not cond:
        FAILED.append(name)


# --------------------------------------------------------------------------- #
# 在磁盘上搭一个真实存在的占位环境（安全闸只看真文件）
# --------------------------------------------------------------------------- #

_ROOT = tempfile.mkdtemp(prefix="envswitch_test_")
_NEW = os.path.dirname(os.path.abspath(sys.executable))   # 当前解释器，真实存在


def _h(sub: str) -> str:
    return os.path.join(_ROOT, *sub.split("/"))


def _touch(d: str, *names: str) -> None:
    os.makedirs(d, exist_ok=True)
    for n in names:
        with open(os.path.join(d, n), "w") as fp:
            fp.write("")


# Python 3.14（系统 PATH 里的「旧版本」）：根目录有 python.exe；Scripts 里是 pip（不是 python）
_touch(_h("Python314"), "python.exe")
_touch(_h("Python314/Scripts"), "pip.exe")
# Oracle 的 javapath（系统 PATH 里抢在 jdk 前面的 java 入口，真有 java.exe）
_touch(_h("Oracle/javapath"), "java.exe")
# 当前 Java（jdk-26.0.1，放在用户 PATH，通过 bin 提供 java.exe）
_touch(_h("jdk-26.0.1/bin"), "java.exe")
# Node / dotnet（系统 PATH 里，但不该被误伤）
_touch(_h("nodejs"), "node.exe")
_touch(_h("dotnet"), "dotnet.exe")
# 一个同时提供 python.exe + node.exe 的目录：用来验证「拆掉会弄丢别的工具命令」就保留
_touch(_h("Combo"), "python.exe", "node.exe")

PY314 = _h("Python314")
PY314_SCRIPTS = _h("Python314/Scripts")
JAVAPATH = _h("Oracle/javapath")
JDK_BIN = _h("jdk-26.0.1/bin")
NODE_HOME = _h("nodejs")
DOTNET = _h("dotnet")
COMBO = _h("Combo")
MALFORMED = r"%%JAVA_HOME%\bin"     # 畸形写法，这条本来就是死的，且磁盘上不存在

BACKUP_SYSTEM = [PY314, PY314_SCRIPTS, JAVAPATH, DOTNET, COMBO, MALFORMED]
# 用户 PATH：java 指向 jdk-26.0.1/bin（真实存在，能接管）；node/dotnet 也在
USER_HAS_JAVA = [JDK_BIN, NODE_HOME, DOTNET]
# 用户 PATH：python 指向 _NEW（真实存在，能接管 PY314）
USER_HAS_PYTHON = [_NEW, os.path.join(_NEW, "Scripts"), JDK_BIN, NODE_HOME, DOTNET]
# 用户 PATH：啥都没有（python/java/node 只在系统 PATH 里）
USER_EMPTY = [DOTNET]
# 用户 PATH：python 切到 _NEW（接管），但 node 这里没有任何入口（COMBO 是 node 唯一来源）
USER_HAS_PYTHON_NO_NODE = [_NEW, os.path.join(_NEW, "Scripts"), JDK_BIN, DOTNET]


class FakeBackend:
    name = "fake"

    def __init__(self, system, user):
        self._system = list(system)
        self._user = list(user)
        self.written = None

    def system_path(self):
        return list(self._system)

    def get_path(self):
        return list(self._user)

    def write_system_path(self, items):
        self.written = list(items)
        self._system = list(items)


class FakeMgr:
    def __init__(self, tools, installs):
        self._tools = tools
        self._installs = installs
        from envswitch.core.scanner import Scanner

        self.scanner = Scanner(log=lambda *a: None)

    def tools(self):
        return list(self._tools)

    def scan(self, tool_id, refresh=False):
        return list(self._installs.get(tool_id, []))


def fake_install(tool_id, home, exe="", version="", current=False):
    return Install(tool_id=tool_id, home=home, exe=exe, version=version, current=current)


def run_fix(system, user, tools, installs, target="all"):
    """在假后端上跑一次 apply_system_fix（不碰真注册表 / 真备份）。"""
    backend = FakeBackend(system, user)
    saved = (envmod.get_backend, envmod.save_backup)
    envmod.get_backend = lambda log=print: backend          # type: ignore[assignment]
    envmod.save_backup = lambda items: None                 # type: ignore[assignment]
    try:
        mgr = FakeMgr(tools, installs)
        mgr.backend = backend          # effective_path() 需要真实的 backend 引用
        res = apply_system_fix(mgr, target)
    finally:
        envmod.get_backend, envmod.save_backup = saved      # type: ignore[assignment]
    kept = backend.written if backend.written is not None else list(system)
    return res, kept


# --------------------------------------------------------------------------- #
# 1) 基础判定
# --------------------------------------------------------------------------- #

print("1) 商店别名 / 命令可达性判定")
check("WindowsApps 下的 python.exe 是商店别名",
      is_store_alias(os.path.join(os.environ.get("LOCALAPPDATA", ""),
                                 "Microsoft", "WindowsApps", "python.exe"))
      or not os.path.isdir(os.path.join(os.environ.get("LOCALAPPDATA", ""),
                                       "Microsoft", "WindowsApps")))
check("普通安装目录不是商店别名", not is_store_alias(PY314))
check("store 别名目录不算提供 python 命令", not provides_command(
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WindowsApps"), ["python"]))
check("真实目录算提供命令（占位 Python314）", provides_command(PY314, ["python"]))
check("Scripts 目录提供的是 pip，不算 python 命令",
      not provides_command(PY314_SCRIPTS, ["python"]))

# --------------------------------------------------------------------------- #
# 2) 只动「抢在选中版本前面、且真能接管」的条目
# --------------------------------------------------------------------------- #

print("\n2) 只清理真抢在前面的影子条目；非影子条目一律不动")
installs_java = {
    "python": [fake_install("python", PY314, os.path.join(PY314, "python.exe"), "3.14.5", current=True)],
    "java": [fake_install("java", _h("jdk-26.0.1"), "", "26.0.1", current=True)],
    "node": [fake_install("node", NODE_HOME, os.path.join(NODE_HOME, "node.exe"), "24.16.0", current=True)],
}
res, kept = run_fix(BACKUP_SYSTEM, USER_HAS_JAVA, [PYTHON, JAVA, NODE], installs_java)
check("Java 的 javapath（真抢在 jdk 前面）→ 被清掉", JAVAPATH not in kept, str(kept))
check("Python314 不在任何影子列表里 → 不碰", PY314 in kept, str(kept))
check("Python314/Scripts 不碰（它只提供 pip）", PY314_SCRIPTS in kept, str(kept))
check("dotnet 不是受管工具 → 不误伤", DOTNET in kept, str(kept))
check("畸形 %%JAVA_HOME%\\bin 在 apply_system_fix 里不动（它由坏引用专项处理）", MALFORMED in kept, str(kept))

# --------------------------------------------------------------------------- #
# 3) 正常切换：切到别的真实版本，旧版本整组被清掉（且不留半拉）
# --------------------------------------------------------------------------- #

print("\n3) 切换到新版本：旧版本整组被清掉，切换才真的生效")
installs_py = {
    "python": [fake_install("python", _NEW, os.path.join(_NEW, "python.exe"), "3.13.12", current=True)],
    "java": [fake_install("java", _h("jdk-26.0.1"), "", "26.0.1", current=True)],
    "node": [fake_install("node", NODE_HOME, os.path.join(NODE_HOME, "node.exe"), "24.16.0", current=True)],
}
res, kept = run_fix(BACKUP_SYSTEM, USER_HAS_PYTHON, [PYTHON, JAVA, NODE], installs_py)
check("旧版本 Python314 已从系统 PATH 移除", PY314 not in kept, str(kept))
check("旧版本 Python314/Scripts 一起移除（不留半拉）", PY314_SCRIPTS not in kept, str(kept))
check("Java 的 javapath 也清掉（java 有新版本接管）", JAVAPATH not in kept, str(kept))
check("dotnet 不误伤", DOTNET in kept, str(kept))
check("旧的 Python314 是被移除的（不是被跳过）",
      any("Python314" in p for p in res.get("removed", [])), str(res.get("removed")))

# --------------------------------------------------------------------------- #
# 4) 只有系统 PATH 里有、用户 PATH 里没有任何替代 → 一条都不能删
# --------------------------------------------------------------------------- #

print("\n4) 替代完全不存在时，所有入口都不能丢")
res, kept = run_fix(BACKUP_SYSTEM, USER_EMPTY, [PYTHON, JAVA, NODE], {})
check("Python 的入口还在", PY314 in kept, str(kept))
check("Python 的 Scripts 还在", PY314_SCRIPTS in kept, str(kept))
check("COMBO 这种同时提供多种命令的目录也不误伤", COMBO in kept, str(kept))
check("没有任何条目被移除", not res.get("removed"), str(res.get("removed")))
check("被保留的条目都给了原因",
      all(s.get("reason") for s in res.get("keep", [])), str(res.get("keep")))

# --------------------------------------------------------------------------- #
# 5) 安全闸的跨工具保护：删掉会弄丢别的工具命令 → 保留并说明原因
# --------------------------------------------------------------------------- #

print("\n5) 拆掉会弄丢别的工具的命令 → 保留（并给出原因）")
# python 切到 _NEW（用户 PATH，能接管）；node 的当前安装在一个「不在任何 PATH 里」的目录，
# 于是 COMBO（同时提供 python + node）成为 node 在命令行里的唯一入口。COMBO 必须被保留。
installs_combo = {
    "python": [fake_install("python", _NEW, os.path.join(_NEW, "python.exe"), "3.13.12", current=True)],
    "java": [fake_install("java", _h("jdk-26.0.1"), "", "26.0.1", current=True)],
    "node": [fake_install("node", _h("othernode"), os.path.join(_h("othernode"), "node.exe"),
                          "20.0.0", current=True)],
}
res, kept = run_fix(BACKUP_SYSTEM, USER_HAS_PYTHON_NO_NODE, [PYTHON, JAVA, NODE], installs_combo)
check("Python314 整组被安全移除（_NEW 接管了 python）", PY314 not in kept and PY314_SCRIPTS not in kept,
      str(kept))
check("COMBO 被保留（删了 node 就没了）", COMBO in kept, str(kept))
check("保留原因里点名了 node", any("node" in (s.get("reason") or "").lower()
                                   for s in res.get("keep", [])), str(res.get("keep")))

# --------------------------------------------------------------------------- #
# 6) 畸形变量引用：由「坏引用」专项清理（fix_system_drop 精确删），不靠关键词猜
# --------------------------------------------------------------------------- #

print("\n6) 畸形变量引用 %%JAVA_HOME%\\bin：能作为无命令条目被精确移除")
# 直接用 fix_system_drop 验证：畸形条目提供不了任何命令，移除它不会弄丢工具
_backend6 = FakeBackend(BACKUP_SYSTEM, USER_EMPTY)
_saved6 = (envmod.get_backend, envmod.save_backup)
envmod.get_backend = lambda log=print: _backend6          # type: ignore[assignment]
envmod.save_backup = lambda items: None                 # type: ignore[assignment]
try:
    mgr6 = FakeMgr([PYTHON, JAVA, NODE], {})
    mgr6.backend = _backend6
    res3 = fix_system_drop(mgr6, [MALFORMED])
finally:
    envmod.get_backend, envmod.save_backup = _saved6      # type: ignore[assignment]
check("fix_system_drop 能精确移除畸形引用", res3[0], str(res3))
check("移除列表里包含该畸形条目", MALFORMED in res3[2], str(res3[2]))

# --------------------------------------------------------------------------- #
# 7) 手动添加路径：目录要能换算成主程序
# --------------------------------------------------------------------------- #

print("\n7) 目录 -> 主程序 的换算（「未纳入管理」的修复要用）")
from envswitch.core.audit import _addable_target  # noqa: E402
from envswitch.core.scanner import Scanner  # noqa: E402


class StubMgr:
    scanner = Scanner(log=lambda *a: None)


target = _addable_target(StubMgr(), NODE, NODE_HOME, NODE_HOME)
check("node 目录能换算成 node.exe", target.lower().endswith("node.exe"), target)

# 商店别名目录要分死活用不同处理，否则会给出「点下去必然失败」的修复按钮：
#   * 真装着的商店版（别名指向真实程序）→ 换算得出主程序，修复按钮可用；
#   * 空壳 / 里面根本没有主程序 → 换算不出来，不给这个按钮。
wa_live = _h("Microsoft/WindowsApps")
_touch(wa_live, "python.exe")
target = _addable_target(StubMgr(), PYTHON, wa_live, wa_live)
check("真装着的商店目录能换算成主程序", target.lower().endswith("python.exe"), target)
check("换算出来的确实是活的商店别名", bool(store_alias_target(target)), target)

wa_dead = _h("Microsoft/WindowsApps/nothing")
os.makedirs(wa_dead, exist_ok=True)
target = _addable_target(StubMgr(), PYTHON, wa_dead, wa_dead)
check("里面没有主程序的商店目录换算不出结果（不给会失败的修复按钮）", target == "", target)

print()
try:
    shutil.rmtree(_ROOT, ignore_errors=True)
except Exception:  # noqa: BLE001
    pass

if FAILED:
    print(f"失败 {len(FAILED)} 项：")
    for f in FAILED:
        print("   - " + f)
    raise SystemExit(1)
print("全部通过 ✅")
