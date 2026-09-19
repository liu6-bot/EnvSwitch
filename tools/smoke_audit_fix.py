#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端冒烟测试：复现用户那句「显示已修复，但实际没修复」+「切换之后原来的会消失」。

流程：系统 PATH 里躺着旧版本 C:\\Python314（排在用户 PATH 前面，于是切换不生效）
      → audit 应当报一条 blocked（命令行命中的不是选中版本）
      → fix_issue 真的把它从系统 PATH 移除（不是弹一句「已修复」糊弄）
      → 再 audit 一次，这条 blocked 必须消失（verify_issue 复核通过）

全程用假后端，绝不碰真实注册表 / 真实系统 PATH / 绝不弹 UAC。
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from envswitch.core import env as envmod  # noqa: E402
from envswitch.core import elevate as elevate_mod  # noqa: E402
from envswitch.core.builtins import JAVA, NODE, PYTHON  # noqa: E402
from envswitch.core.audit import audit, fix_issue  # noqa: E402
from envswitch.core.models import Install  # noqa: E402

# 封闭：走进程内假后端，不弹 UAC、不改真实系统 PATH
elevate_mod.is_admin = lambda: True

_ROOT = tempfile.mkdtemp(prefix="envswitch_smoke_")
_NEW = os.path.dirname(os.path.abspath(sys.executable))


def _h(sub):
    return os.path.join(_ROOT, *sub.split("/"))


def _touch(d, *names):
    os.makedirs(d, exist_ok=True)
    for n in names:
        with open(os.path.join(d, n), "w") as fp:
            fp.write("")


# 磁盘上真目录（provides_command 只看真文件）
_touch(_h("Python314"), "python.exe")
_touch(_h("Python314/Scripts"), "pip.exe")
_touch(_h("Oracle/javapath"), "java.exe")
_touch(_h("jdk-26.0.1/bin"), "java.exe")
_touch(_h("nodejs"), "node.exe")

PY314 = _h("Python314")
PY314_SCRIPTS = _h("Python314/Scripts")
JAVAPATH = _h("Oracle/javapath")
JDK_BIN = _h("jdk-26.0.1/bin")
NODE_HOME = _h("nodejs")

SYSTEM = [PY314, PY314_SCRIPTS, JAVAPATH]          # 旧版本躺系统 PATH（排在前面）
USER = [_NEW, os.path.join(_NEW, "Scripts"), JDK_BIN, NODE_HOME]  # 新版本在用户 PATH


class FakeBackend:
    name = "fake"

    def __init__(self, system, user):
        self._system = list(system)
        self._user = list(user)

    def system_path(self):
        return list(self._system)

    def get_path(self):
        return list(self._user)

    def set_path(self, items):
        self._user = list(items)

    def write_system_path(self, items):
        self._system = list(items)

    def unset(self, name):
        pass


class FakeConfig:
    def audit_ignored(self):
        return set()


class FakeMgr:
    def __init__(self, tools, installs):
        self._tools = tools
        self._installs = installs
        from envswitch.core.scanner import Scanner
        self.scanner = Scanner(log=lambda *a: None)
        self.backend = FakeBackend(SYSTEM, USER)
        self.config = FakeConfig()

    def tools(self):
        return list(self._tools)

    def scan(self, tool_id, refresh=False):
        return list(self._installs.get(tool_id, []))


installs = {
    "python": [Install("python", PY314, os.path.join(PY314, "python.exe"), "3.14.5", current=False),
               Install("python", _NEW, os.path.join(_NEW, "python.exe"), "3.13.12", current=True)],
    "java": [Install("java", _h("jdk-26.0.1"), "", "26.0.1", current=True)],
    "node": [Install("node", NODE_HOME, os.path.join(NODE_HOME, "node.exe"), "24.16.0", current=True)],
}
mgr = FakeMgr([PYTHON, JAVA, NODE], installs)

# 把 audit / fix 用到的 get_backend 指向假后端
_saved = (envmod.get_backend, envmod.save_backup)
envmod.get_backend = lambda log=print: mgr.backend
envmod.save_backup = lambda items: None

failed = []


def check(name, cond, extra=""):
    print(("  ✓ " if cond else "  ✗ ") + name + (f"   {extra}" if extra and not cond else ""))
    if not cond:
        failed.append(name)


try:
    print("A) 体检：应当发现 python 被系统 PATH 里的旧版本挡住")
    issues = audit(mgr)
    blocked = [i for i in issues if i.kind == "blocked" and i.tool_id == "python"]
    check("发现了 python 的 blocked 问题", bool(blocked), str([i.title for i in issues]))
    check("shadow 里包含 C:\\Python314（旧版本根目录）",
          any("Python314" in it for i in blocked for it in i.items),
          str([i.items for i in blocked]))

    print("\nB) 修复这条 blocked（应当真的从系统 PATH 移除旧版本，而不是假装修好）")
    ok, msg = fix_issue(mgr, blocked[0])
    check("fix_issue 返回成功", ok, msg)
    check("系统 PATH 里已经没有 C:\\Python314",
          PY314 not in mgr.backend.system_path(), str(mgr.backend.system_path()))
    check("系统 PATH 里已经没有 C:\\Python314\\Scripts（整组移除，不留半拉）",
          PY314_SCRIPTS not in mgr.backend.system_path(), str(mgr.backend.system_path()))

    print("\nC) 复核：再体检一次，这条 blocked 必须消失（证明不是假装修好）")
    issues2 = audit(mgr)
    blocked2 = [i for i in issues2 if i.kind == "blocked" and i.tool_id == "python"]
    check("python 的 blocked 已经不存在了", not blocked2, str([i.title for i in issues2]))
finally:
    envmod.get_backend, envmod.save_backup = _saved

print()
if failed:
    print(f"冒烟测试失败 {len(failed)} 项：")
    for f in failed:
        print("   - " + f)
    raise SystemExit(1)
print("端到端冒烟测试通过 ✅（切换旧版本后，系统 PATH 里的旧版本被整组清掉，切换真正生效）")
