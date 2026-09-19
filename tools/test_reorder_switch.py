#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「全部纳入 + 只改顺序」切换模型的回归测试。

核心保证：切换**绝不删除**任何原有 PATH 条目，只是把选中版本的条目调到最前，
同工具其它版本排其后，未管理的条目保持原相对顺序。

验证：
  1. 切到新版本后，旧版本仍在 PATH 里（不会"切换之后原来的会消失"）。
  2. 来回切换，两条都能用，谁选中谁在前。
  3. 切某个工具不会动到其它工具的条目。
  4. 只新增存在的目录；不存在的目录不写入。
  5. revert 只摘掉本次新增的条目，不动用户原有的。
  6. 不产生重复条目。

全程用内存假后端，不碰真实注册表 / 真实系统 PATH。
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from envswitch.core.env import WindowsBackend, ApplyPlan  # noqa: E402
from envswitch.core.models import norm_path  # noqa: E402

ROOT = tempfile.mkdtemp(prefix="envswitch_reorder_")


def h(sub):
    return os.path.join(ROOT, *sub.split("/"))


def touch(*dirs):
    for d in dirs:
        os.makedirs(d, exist_ok=True)


touch(h("py311"), h("py311/Scripts"), h("py313"), h("py313/Scripts"),
      h("node18"), h("node18/bin"))

PY311, PY311S = h("py311"), h("py311/Scripts")
PY313, PY313S = h("py313"), h("py313/Scripts")
NODE, NODEB = h("node18"), h("node18/bin")
UNMANAGED = r"C:\some\unmanaged"
SYSTEM32 = r"C:\Windows\system32"


class MemWinBackend(WindowsBackend):
    """继承真实 apply/revert，但 PATH 与变量都走内存，绝不写注册表。"""

    def __init__(self):
        self.log = print
        self._path = []

    def get_path(self):
        return list(self._path)

    def set_path(self, items):
        self._path = list(items)

    def set(self, *a, **k):
        pass

    def unset(self, *a, **k):
        pass

    def broadcast(self):
        pass


failed = []


def check(name, cond, extra=""):
    print(("  ✓ " if cond else "  ✗ ") + name + (f"   {extra}" if extra and not cond else ""))
    if not cond:
        failed.append(name)


def first_index(path, target):
    for i, p in enumerate(path):
        if norm_path(p) == norm_path(target):
            return i
    return -1


print("1) 切到新版本 py313：旧版本 py311 必须还在，且 py313 排在前")
b = MemWinBackend()
b._path = [NODEB, PY311, PY311S, SYSTEM32, UNMANAGED]
plan = ApplyPlan(tool_id="python", home_var="PYTHON_HOME", home_value=PY313,
                 path_entries=[PY313, PY313S], other_entries=[PY311, PY311S])
rec = b.apply(plan)
new = b.get_path()
check("py313 在 PATH 里", first_index(new, PY313) >= 0, str(new))
check("py313 排在 py311 前面", first_index(new, PY313) < first_index(new, PY311), str(new))
check("py311 仍然在 PATH 里（没被删）", first_index(new, PY311) >= 0, str(new))
check("py311/Scripts 也还在", first_index(new, PY311S) >= 0, str(new))
check("node 条目没被动", first_index(new, NODEB) >= 0, str(new))
check("未管理条目没被动", first_index(new, UNMANAGED) >= 0, str(new))
check("本次新增的就是 py313 两条", set(rec.added) == {PY313, PY313S}, str(rec.added))
check("没有重复条目", len(new) == len({norm_path(x) for x in new}), str(new))

print("\n2) 再切回 py311：两条都还在，py311 回到最前")
plan2 = ApplyPlan(tool_id="python", home_var="PYTHON_HOME", home_value=PY311,
                  path_entries=[PY311, PY311S], other_entries=[PY313, PY313S])
b.apply(plan2)
new2 = b.get_path()
check("py311 排在 py313 前面", first_index(new2, PY311) < first_index(new2, PY313), str(new2))
check("py313 仍然在 PATH 里", first_index(new2, PY313) >= 0, str(new2))
check("没有重复条目", len(new2) == len({norm_path(x) for x in new2}), str(new2))

print("\n3) 切到 node：python 的两条不能受影响")
plan3 = ApplyPlan(tool_id="node", home_var="NODE_HOME", home_value=NODE,
                  path_entries=[NODE, NODEB], other_entries=[])
b.apply(plan3)
new3 = b.get_path()
check("node 排在最前", first_index(new3, NODE) == 0, str(new3))
check("py311 仍在", first_index(new3, PY311) >= 0, str(new3))
check("py313 仍在", first_index(new3, PY313) >= 0, str(new3))

print("\n4) 只新增存在的目录：构造一个不存在的 other 条目，不应写入")
b2 = MemWinBackend()
b2._path = [PY311, PY311S]
plan4 = ApplyPlan(tool_id="python", home_var="PYTHON_HOME", home_value=PY313,
                  path_entries=[PY313, PY313S],
                  other_entries=[PY311, PY311S, h("py999"), h("py999/Scripts")])
b2.apply(plan4)
new4 = b2.get_path()
check("存在的 py313 被加入", first_index(new4, PY313) >= 0, str(new4))
check("不存在的 py999 没被加入", first_index(new4, h("py999")) < 0, str(new4))
check("不存在的 py999/Scripts 没被加入", first_index(new4, h("py999/Scripts")) < 0, str(new4))
check("原本的 py311 还在", first_index(new4, PY311) >= 0, str(new4))

print("\n5) revert 只摘掉本次新增的，不动用户原有的")
b3 = MemWinBackend()
b3._path = [PY311, PY311S, SYSTEM32, UNMANAGED]   # 用户原本只有 py311
plan5 = ApplyPlan(tool_id="python", home_var="PYTHON_HOME", home_value=PY313,
                  path_entries=[PY313, PY313S], other_entries=[PY311, PY311S])
rec5 = b3.apply(plan5)
check("切换后 py313 在", first_index(b3.get_path(), PY313) >= 0)
b3.revert(rec5)
after = b3.get_path()
check("revert 后 py313 被摘掉", first_index(after, PY313) < 0, str(after))
check("revert 后 py311（用户原有的）仍在", first_index(after, PY311) >= 0, str(after))
check("revert 后未管理条目仍在", first_index(after, UNMANAGED) >= 0, str(after))
check("revert 后没有重复", len(after) == len({norm_path(x) for x in after}), str(after))

print()
if failed:
    print(f"reorder 切换测试失败 {len(failed)} 项：")
    for f in failed:
        print("   - " + f)
    raise SystemExit(1)
print("reorder 切换模型测试通过 ✅（切换只改顺序、永不删条目，旧版本不再消失）")
