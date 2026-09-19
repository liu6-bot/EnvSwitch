#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「见过的安装永不丢」的回归测试。

背景（用户报的 bug）：切换完之后，列表里的版本会变少。
根因不是切换删了 PATH（reorder 模型已经不删了），而是**扫描器唯一的记忆原本就是 PATH**：
某版本一旦从 PATH 里掉出去（历史版本切换时删过、用户手改了 PATH），
靠 ``where`` 反查就永远找不回来 —— 列表里那一项就"消失"了。

本测试覆盖三层保险（各场景用独立目录，互不串味）：
  1. known_installs 记忆：见过一次就记住，之后每轮扫描用磁盘现状复核；
     目录还在 → 继续列出；真被卸载 → 丢弃（不会阴魂不散）。
  2. learned_roots：从已知安装学出父目录当扫描根，浅扫一层找回兄弟版本。
  3. _seed_from_applied：从旧的切换记录（removed_path / path_entries）里
     把"曾经管过、后被删出 PATH"的安装捡回记忆。

全程：合成工具 + 临时目录假文件系统 + 内存假后端，不碰真实注册表、不写真实配置、
不启动任何真实可执行文件（version_cmd 为空）。
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from envswitch.core.config import Config                     # noqa: E402
from envswitch.core.env import WindowsBackend                # noqa: E402
from envswitch.core.manager import EnvManager                # noqa: E402
from envswitch.core.models import ToolDef, norm_path         # noqa: E402


def new_root():
    return tempfile.mkdtemp(prefix="envswitch_mem_")


# 合成一个 dir 模式工具：不需要真跑版本命令（version_cmd 为空 → 版本从目录名推断）
JT = ToolDef(
    id="jtest",
    name="JTest",
    entry="dir",
    home_var="JTEST_HOME",
    detect="bin/jtest{ext}",
    bin_names=["jtest"],
    version_cmd=[],
    path_entries=["{home}/bin"],
    search_roots={},          # 故意不给扫描根：逼迫逻辑只靠 PATH / 记忆 / 学到的根
    builtin=False,
)


def make_install(root, name):
    """造一个"安装目录"：<root>/installs/<name>/bin/jtest.exe，返回 home。"""
    d = os.path.join(root, "installs", name, "bin")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "jtest.exe"), "w", encoding="utf-8") as f:
        f.write("")
    return os.path.join(root, "installs", name)


class MemBackend(WindowsBackend):
    """PATH / 变量都走内存，系统 PATH 返回空（模拟"系统里没有同名命令"）。"""

    def __init__(self):
        self.log = print
        self._path = []

    def get_path(self):
        return list(self._path)

    def set_path(self, items):
        self._path = list(items)

    def system_path(self):
        return []

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


def versions(installs):
    return sorted(i.version for i in installs)


def new_mgr(path_items, seed_applied=None):
    """新的临时配置 + 假后端；path_items 是初始用户 PATH。"""
    cfgdir = tempfile.mkdtemp(prefix="envswitch_memcfg_")
    cfg = Config(cfgdir)
    cfg.add_tool(JT)
    if seed_applied:
        cfg.data["applied"] = seed_applied
        cfg.save()
    mgr = EnvManager(config=cfg, log=lambda *a: None)
    be = MemBackend()
    be._path = list(path_items)
    mgr.backend = be
    return mgr, cfg, be


def clear_learned(cfg, tool_id="jtest"):
    """清掉学到的扫描根，好把「记忆」这条路径单独隔离出来验证。"""
    cfg.data.setdefault("learned_roots", {})[tool_id] = []
    cfg.save()


# --------------------------------------------------------------------------- #
print("1) 记忆：版本离开 PATH 后，仅靠记忆仍然留在列表里")
r1 = new_root()
J26 = make_install(r1, "jdk-26.0.1")
J17 = make_install(r1, "jdk-17.0.12")

mgr, cfg, be = new_mgr([os.path.join(J26, "bin"), os.path.join(J17, "bin")])
first = mgr.scan("jtest", refresh=True)
check("初次扫描找到 2 个", len(first) == 2, str(versions(first)))

clear_learned(cfg)                              # 关掉"学到的根"，逼它只能靠记忆
be._path = [os.path.join(J26, "bin")]           # 把 jdk-17 从 PATH 里拿掉（模拟历史误删）
second = mgr.scan("jtest", refresh=True)
check("掉出 PATH 后仍然是 2 个（靠记忆）", len(second) == 2, str(versions(second)))
check("jdk-17 还在，且标成「历史记录」",
      any(i.version == "17.0.12" and i.source == "remembered" for i in second),
      str([(i.version, i.source) for i in second]))

# --------------------------------------------------------------------------- #
print("\n2) 真被卸载的版本要丢掉，不能阴魂不散")
r2 = new_root()
K18 = make_install(r2, "jdk-1.8.0")
K26 = make_install(r2, "jdk-26.0.1")
mgr2, cfg2, be2 = new_mgr([os.path.join(K18, "bin"), os.path.join(K26, "bin")])
check("先扫到 2 个", len(mgr2.scan("jtest", refresh=True)) == 2)

be2._path = [os.path.join(K26, "bin")]
shutil.rmtree(K18, ignore_errors=True)          # 模拟卸载：目录没了
after = mgr2.scan("jtest", refresh=True)
check("目录消失后只剩 1 个", len(after) == 1, str(versions(after)))
check("剩下的不是被卸载的那个", all(i.version != "1.8.0" for i in after), str(versions(after)))

# --------------------------------------------------------------------------- #
print("\n3) 学会扫描根：找回同目录下的兄弟版本")
r3 = new_root()
M26 = make_install(r3, "jdk-26.0.1")
M19 = make_install(r3, "jdk-19.0.2")            # 从没进过 PATH
mgr3, cfg3, be3 = new_mgr([os.path.join(M26, "bin")])
s1 = mgr3.scan("jtest", refresh=True)
check("第一次只看到 1 个（jdk-19 还不在 PATH）", len(s1) == 1, str(versions(s1)))
check("已经学到父目录当扫描根",
      any(norm_path(x) == norm_path(os.path.join(r3, "installs"))
          for x in cfg3.learned_roots("jtest")),
      str(cfg3.learned_roots("jtest")))
s2 = mgr3.scan("jtest", refresh=True)
check("第二次靠学到的根找回了兄弟版本 jdk-19",
      len(s2) == 2 and "19.0.2" in versions(s2), str(versions(s2)))

# --------------------------------------------------------------------------- #
print("\n4) 从旧的切换记录里补种（曾经管过、后被删出 PATH 的安装）")
r4 = new_root()
L11 = make_install(r4, "jdk-11.0.20")
seed = {
    "jtest": {
        "record": {
            "backend": "windows",
            "plan": {"tool_id": "jtest", "home_var": "JTEST_HOME",
                     "home_value": "", "path_entries": [], "other_entries": [],
                     "extra_vars": {}, "conflict_keywords": []},
            # 历史版本就是把这条删掉的 —— 记录里留着证据
            "removed_path": [os.path.join(L11, "bin")],
            "added": [], "files": [],
        },
        "install": {"tool_id": "jtest", "home": L11, "exe": "",
                    "version": "11.0.20", "vendor": "", "arch": "", "source": "auto"},
    }
}
mgr4, cfg4, be4 = new_mgr([], seed_applied=seed)   # PATH 里什么都没有
s4 = mgr4.scan("jtest", refresh=True)
check("靠历史记录把 jdk-11 捡了回来", any(i.version == "11.0.20" for i in s4), str(versions(s4)))
check("捡回来的标成「历史记录」",
      any(i.version == "11.0.20" and i.source == "remembered" for i in s4),
      str([(i.version, i.source) for i in s4]))

# --------------------------------------------------------------------------- #
print("\n5) use() 切换：所有版本都写进 PATH，一个都不少")
r5 = new_root()
N26 = make_install(r5, "jdk-26.0.1")
N17 = make_install(r5, "jdk-17.0.12")
N20 = make_install(r5, "jdk-20.0.1")
mgr5, cfg5, be5 = new_mgr([os.path.join(N17, "bin"), os.path.join(N26, "bin")])
mgr5.scan("jtest", refresh=True)                 # 第 1 轮：学到根 + 建记忆
installs = mgr5.scan("jtest", refresh=True)      # 第 2 轮：靠学到的根找到 jdk-20
check("三轮后三个版本都在", len(installs) == 3, str(versions(installs)))
target = next((i for i in installs if i.version == "20.0.1"), None)
if target is None:
    check("找得到 jdk-20 用来切换", False, str(versions(installs)))
else:
    ok, msg, _ = mgr5.use("jtest", target)
    written = be5.get_path()
    check("切换成功", ok, msg)
    check("选中的 jdk-20 写进了 PATH",
          any(norm_path(x) == norm_path(os.path.join(N20, "bin")) for x in written))
    check("原来的 jdk-26 没被删",
          any(norm_path(x) == norm_path(os.path.join(N26, "bin")) for x in written))
    check("原来的 jdk-17 没被删",
          any(norm_path(x) == norm_path(os.path.join(N17, "bin")) for x in written))
    after5 = mgr5.scan("jtest", refresh=True)
    check("切换后版本数没变少", len(after5) == len(installs),
          f"{versions(installs)} -> {versions(after5)}")

    print("\n6) 不产生重复条目（记忆里的同一条目不能重复列出）")
    check("扫描结果里没有同 home 的重复项",
          len(after5) == len({norm_path(i.home) for i in after5}),
          str([(i.version, i.home) for i in after5]))
    check("写进 PATH 的条目也没有重复",
          len(written) == len({norm_path(x) for x in written}), str(written))

# --------------------------------------------------------------------------- #
print("\n7) 忘记一条记错的安装：不再靠记忆复活")
r6 = new_root()
P26 = make_install(r6, "jdk-26.0.1")
PFAKE = make_install(r6, "Adobe Photoshop 2025")   # 别的软件自带的运行时，被记错了
mgr6, cfg6, be6 = new_mgr([os.path.join(P26, "bin"), os.path.join(PFAKE, "bin")])
before6 = mgr6.scan("jtest", refresh=True)
check("先能扫到 2 个", len(before6) == 2, str(versions(before6)))
mgr6.forget_install("jtest", PFAKE)
clear_learned(cfg6)
be6._path = [os.path.join(P26, "bin")]          # 两条都离开 PATH，只剩记忆
after6 = mgr6.scan("jtest", refresh=True)
check("忘掉的那条不再复活", len(after6) == 1, str(versions(after6)))
check("没被忘掉的那条还在", any(i.version == "26.0.1" for i in after6), str(versions(after6)))

for r in (r1, r2, r3, r4, r5, r6):
    shutil.rmtree(r, ignore_errors=True)

print()
if failed:
    print(f"安装记忆测试失败 {len(failed)} 项：")
    for f in failed:
        print("   - " + f)
    raise SystemExit(1)
print("安装记忆测试通过 ✅（见过的安装永不丢；卸载的会丢；兄弟版本能找回；切换不丢版本）")
