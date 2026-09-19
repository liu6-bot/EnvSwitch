# -*- coding: utf-8 -*-
"""诊断脚本：用「假后端 + 临时配置副本」完整跑一遍切换流程。

目的：验证「记住见过的安装 + 从历史恢复 + 学会扫描根」能不能把
被历史版本删出 PATH 的 jdk-17 / jdk1.8.0_202 找回来，并保证切换不丢版本。

不写真实注册表；配置写到临时目录（复制真实 config.json 作为种子），
所以真实配置不会被改动。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import winreg
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from envswitch.core.config import Config            # noqa: E402
from envswitch.core.env import WindowsBackend       # noqa: E402
from envswitch.core.manager import EnvManager       # noqa: E402


def real_user_path():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return [x for x in winreg.QueryValueEx(k, "Path")[0].split(";") if x.strip()]
    except Exception:
        return []


class FakeBackend(WindowsBackend):
    """只把「用户 PATH / 环境变量写」换成内存，系统 PATH 仍读真实注册表（只读）。"""

    def __init__(self, log=print, user_path=None):
        super().__init__(log)
        self._user = list(user_path or [])
        self.writes = []

    def get_path(self):
        return list(self._user)

    def set_path(self, items):
        self.writes.append(list(items))
        self._user = list(items)

    def broadcast(self):
        pass

    def set(self, name, value, expand=False):
        pass

    def unset(self, name):
        pass


def show(title, items):
    print(f"--- {title} ---")
    for x in items:
        print("   ", x)


def main():
    import time

    t0 = time.time()
    # 临时配置目录：以真实 config.json 为种子
    tmp = Path(tempfile.mkdtemp(prefix="envswitch-diag-"))
    real = Path.home() / ".envswitch" / "config.json"
    if real.exists():
        shutil.copy2(real, tmp / "config.json")
        print(f"（已把真实配置复制到临时目录做种子：{tmp}）")
    cfg = Config(tmp)

    mgr = EnvManager(config=cfg, log=lambda *a: None)
    fake = FakeBackend(user_path=real_user_path())
    mgr.backend = fake

    print("=" * 72)
    print("A) 扫描结果（应能找回被删出 PATH 的版本）")
    print("=" * 72)
    for tid in ("java", "python", "node", "go", "dotnet"):
        ins = mgr.scan(tid, refresh=True)
        print(f"[{tid}] 检测到 {len(ins)} 个：")
        for i, x in enumerate(ins):
            flag = {"path": "PATH", "remembered": "历史", "auto": "扫描",
                    "store": "商店", "env": "变量", "custom": "手动"}.get(x.source, x.source)
            print(f"    [{i}] {x.version:<12} {flag:<4} {x.home}")

    print()
    print("学习的扫描根：", cfg.data.get("learned_roots"))

    # ---- 切换 java，看会不会丢版本 ----
    print("=" * 72)
    print("B) 切 java -> 26.0.1（用假后端，只写内存）")
    print("=" * 72)
    java = mgr.scan("java", refresh=True)
    target = next((i for i in java if "26.0.1" in i.home), None)
    if target:
        ok, msg, warns = mgr.use("java", target)
        print("ok =", ok, "|", msg.replace("\n", " | "))
        plan = mgr.plan_for(mgr.tool("java"), target)
        print("path_entries  =", plan.path_entries)
        print("other_entries =", getattr(plan, "other_entries", None))
        print()
        show("切换后写入的用户 PATH", fake.writes[-1] if fake.writes else [])

    print()
    print("=" * 72)
    print("C) 切换后再扫：版本数有没有变少？")
    print("=" * 72)
    for tid in ("java", "python", "node"):
        after = mgr.scan(tid, refresh=True)
        print(f"[{tid}] {len(after)} 个：" +
              "、".join(f"{x.version}" for x in after))

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n耗时 {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
