"""微软商店（应用执行别名）识别 的回归测试。

要证明两件事：

  1. 商店版**真装着**（别名指向真实存在的可执行文件）→ 能被当成一个安装列出来、
     来源标 ``store``、切换时只把别名目录前置到 PATH、**不写**主目录变量
     （那个目录是塞满各种别名的转发目录，写成 PYTHON_HOME 后患无穷）；
  2. 商店别名是个**空壳**（应用已卸载，跑它只会弹应用商店）→ 一律不纳入；
     并且安全闸 ``provides_command`` 仍然不把别名目录当成"能提供命令"的地方，
     否则会出现「删掉真正的 C:\\Python314 也没事，WindowsApps 里还有个 python.exe」
     这种误判 —— 那正是上一次把用户 Python 入口删掉的根源。

全程只用临时目录，**不碰注册表、不碰真实系统 PATH、不执行任何切换**。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from envswitch.core.builtins import PYTHON  # noqa: E402
from envswitch.core.manager import EnvManager  # noqa: E402
from envswitch.core.models import (  # noqa: E402
    Install,
    is_store_alias,
    is_store_stub,
    provides_command,
    store_alias_target,
)
from envswitch.core.scanner import Scanner  # noqa: E402

FAILED = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else f"   <-- {extra}"))
    if not cond:
        FAILED.append(name)


def noop(*_a, **_k):
    pass


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="envswitch-store-"))
    # 造一个"商店版真装着"的最小现场：...\Microsoft\WindowsApps\python.exe 是真文件
    wa = tmp / "Microsoft" / "WindowsApps"
    wa.mkdir(parents=True)
    live = wa / "python.exe"
    live.write_bytes(b"MZ")                       # 内容无所谓，只要真实存在
    live_dir = str(wa)
    live_exe = str(live)
    # 空壳：同样是 WindowsApps 路径，但文件不存在（应用已卸载）
    stub_exe = str(wa / "python3.exe")

    print("1) 商店别名的分类：真装着 vs 空壳")
    check("WindowsApps 路径被识别成商店别名", is_store_alias(live_exe), live_exe)
    check("真装着的别名能解析出真实程序", bool(store_alias_target(live_exe)), live_exe)
    check("真装着的别名不算空壳", not is_store_stub(live_exe), live_exe)
    check("不存在的别名不算真安装", not store_alias_target(stub_exe), stub_exe)
    check("不存在的别名被判定为空壳", is_store_stub(stub_exe), stub_exe)
    check("普通路径不受影响", not is_store_alias(r"C:\Python314\python.exe"))

    print("\n2) 扫描器：真装着的商店版纳入，空壳剔除")
    sc = Scanner(log=noop, reference_path=lambda: [live_dir])
    check("真装着的别名可纳入", sc._live(live_exe), live_exe)
    check("空壳别名被剔除", not sc._live(stub_exe), stub_exe)
    home, exe = sc._interpret(PYTHON, live_exe)
    check("能把别名解释成 (home, exe)", home == live_dir and exe == live_exe, f"{home} | {exe}")
    check("空壳解释不出来", sc._interpret(PYTHON, stub_exe) == ("", ""), str(sc._interpret(PYTHON, stub_exe)))

    print("\n3) 安全闸：别名目录永远不算「能提供命令」")
    check("provides_command 对商店别名目录返回 False",
          not provides_command(live_dir, PYTHON.bin_names), live_dir)
    check("provides_command 对真实安装目录返回 True",
          provides_command(r"C:\Python314", PYTHON.bin_names),
          "注意：本机若把 C:\\Python314 删了这条会假失败，属于环境问题")

    print("\n4) 切换计划：只前置别名目录，不写主目录变量")
    mgr = EnvManager(log=noop)
    ins = Install(tool_id="python", home=live_dir, exe=live_exe,
                  version="3.12.10", vendor="Microsoft Store", source="store")
    plan = mgr.plan_for(PYTHON, ins)
    check("path_entries 就是那个别名目录", plan.path_entries[:1] == [live_dir], str(plan.path_entries))
    check("home_value 为空（不写 PYTHON_HOME）", plan.home_value == "", repr(plan.home_value))
    check("extra_vars 为空", not plan.extra_vars, str(plan.extra_vars))

    print("\n5) 空壳不能被切换（而且要在动注册表之前就拦下）")
    stub_ins = Install(tool_id="python", home=live_dir, exe=stub_exe, version="", source="store")
    ok, msg, _w = mgr.use("python", stub_ins)
    check("空壳切换被拒绝", not ok, msg)
    check("拒绝信息说明了原因", "卸载" in msg or "不存在" in msg, msg)

    print("\n6) 新增的 is_default 标记默认关闭（不污染老配置文件）")
    fresh = Install.from_dict({"tool_id": "java", "home": r"D:\x"})
    check("from_dict 后 is_default 为 False", fresh.is_default is False, str(fresh.is_default))

    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项未通过：" + "、".join(FAILED))
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
