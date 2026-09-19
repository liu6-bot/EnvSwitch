"""Windows 提权与系统级 PATH 修复。

为什么需要它：Windows 拼接 PATH 的顺序是 **系统 PATH 在前，用户 PATH 在后**。
如果系统 PATH 里躺着另一个 `...\\Java\\jdk-x\\bin`，我们往用户 PATH 前面加再多东西也压不过它，
于是"切换了但不生效"。这个问题必须动系统 PATH，而动系统 PATH 需要管理员权限。

设计（2.2.1 重写）：

  * **两段式**：先在用户上下文里算出"删哪些、留哪些、为什么"（``plan_system_drop``），
    确认无误后才进提权上下文写入（``apply_system_drop``）。绝不在提权后的黑盒里
    临时按关键词决定要删什么。
  * **只删真正抢路的**：待删条目由 audit 精确给出 —— "排在你选中版本前面、且真的能提供
    该命令"的条目。不再用关键词（``python`` / ``java``）去猜，所以
    ``C:\\Python314\\Scripts``（pip 的所在地）不会因为名字里带 python 就被删掉。
  * **不拆安装**：被选中版本接管时，安装目录在系统 PATH 里的条目（根目录 +
    ``Scripts`` 等）**整组一起清掉**，不留半拉旧版本尾巴；但若整组移除会让某个命令
    （例如旧版本独有的 pip）失去来源，则整组保留并说明原因。
  * **删前模拟**：逐条模拟"删掉之后所有工具还能不能找到命令行入口"，会删没的一律保留并写明原因。
  * **可还原**：动手前把原始系统 PATH 备份到 ``~/.envswitch/system_path_backup.json``；
    还原时**不会把"会重新抢在前面"的条目放回去**（否则用户会遇到"还原一次又乱了"）。
"""

from __future__ import annotations

import ctypes
import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .models import (
    IS_WIN,
    bad_var_ref_reason,
    is_managed_var_ref,
    norm_key,
    provides_command,
)

RESULT_FILE = "elevated_result.json"
SEE_MASK_NOCLOSEPROCESS = 0x00000040
SW_HIDE = 0
INFINITE = 0xFFFFFFFF


# --------------------------------------------------------------------------- #
# 权限判定与提权执行
# --------------------------------------------------------------------------- #


def is_admin() -> bool:
    if not IS_WIN:
        return os.geteuid() == 0 if hasattr(os, "geteuid") else False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def self_command(sub: List[str]) -> Tuple[str, str]:
    """构造"再启动一个自己"的命令行，返回 (exe, 参数串)。"""
    params = " ".join(shlex.quote(str(x)) for x in sub)
    if getattr(sys, "frozen", False):
        return sys.executable, params
    here = Path(__file__).resolve().parents[2]   # core/ -> envswitch/ -> 仓库根目录
    main_py = here / "main.py"
    if main_py.exists():
        return sys.executable, f'"{main_py}" {params}'
    return sys.executable, f'-m envswitch {params}'


class _SHELLEXECUTEINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("fMask", ctypes.c_ulong),
        ("hwnd", ctypes.c_void_p),
        ("lpVerb", ctypes.c_wchar_p),
        ("lpFile", ctypes.c_wchar_p),
        ("lpParameters", ctypes.c_wchar_p),
        ("lpDirectory", ctypes.c_wchar_p),
        ("nShow", ctypes.c_int),
        ("hInstApp", ctypes.c_void_p),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", ctypes.c_wchar_p),
        ("hKeyClass", ctypes.c_void_p),
        ("dwHotKey", ctypes.c_ulong),
        ("hIcon", ctypes.c_void_p),
        ("hProcess", ctypes.c_void_p),
    ]


def run_elevated(sub: List[str], wait: bool = True, timeout_ms: int = 120000) -> Tuple[bool, str]:
    """以管理员身份执行本程序的子命令。返回 (是否成功, 说明)。"""
    if not IS_WIN:
        return False, "仅 Windows 需要提权"
    exe, params = self_command(sub)
    try:
        sei = _SHELLEXECUTEINFO()
        sei.cbSize = ctypes.sizeof(sei)
        sei.fMask = SEE_MASK_NOCLOSEPROCESS
        sei.lpVerb = "runas"
        sei.lpFile = exe
        sei.lpParameters = params
        sei.nShow = SW_HIDE
        ok = ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei))
        if not ok or not sei.hProcess:
            code = ctypes.windll.shell32.GetLastError()
            if code in (1223,):     # ERROR_CANCELLED：用户拒绝了 UAC
                return False, "已取消（没有授予管理员权限）"
            return False, f"提权失败（错误码 {code}）"
        if wait:
            ctypes.windll.kernel32.WaitForSingleObject(ctypes.c_void_p(sei.hProcess), timeout_ms)
            code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(
                ctypes.c_void_p(sei.hProcess), ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(sei.hProcess))
            return (code.value == 0), ("" if code.value == 0 else f"子进程退出码 {code.value}")
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(sei.hProcess))
        return True, ""
    except Exception as e:  # noqa: BLE001
        return False, str(e)


# --------------------------------------------------------------------------- #
# 提权子进程的结果回传
# --------------------------------------------------------------------------- #


def result_file() -> Path:
    return Path.home() / ".envswitch" / RESULT_FILE


def write_result(data: Dict[str, Any]) -> None:
    try:
        f = result_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def read_result() -> Optional[Dict[str, Any]]:
    f = result_file()
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
    except Exception:  # noqa: BLE001
        return None


def clear_result() -> None:
    try:
        result_file().unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
# 参数编码：提权子进程的命令行不经过 shell，路径里的空格/引号很容易被拆坏，
# 所以把结构化参数塞进一个"无空格无引号"的 base64 串里传过去。
# --------------------------------------------------------------------------- #


def encode_arg(obj: Any) -> str:
    import base64  # noqa: PLC0415

    raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_arg(text: str) -> Any:
    import base64  # noqa: PLC0415

    pad = "=" * (-len(text) % 4)
    return json.loads(base64.urlsafe_b64decode(text + pad).decode("utf-8"))


# --------------------------------------------------------------------------- #
# 基础判断
# --------------------------------------------------------------------------- #


def _k(p: str) -> str:
    """PATH 条目比对用的归一化（保留 %VAR% 原样，不走 abspath）。"""
    return norm_key(p)


def _reachable(tool, entries: List[str]) -> bool:
    """这一串 PATH 条目里，还能找到该工具的命令吗？"""
    names = list(tool.bin_names or [])
    if not names:
        return False
    return any(provides_command(e, names) for e in entries)


def _resolve(entries: List[str], names) -> str:
    """命令行实际会命中哪一段（第一个真能提供该命令的条目）。"""
    for p in entries:
        if provides_command(p, names or []):
            return p
    return ""


def _current_install(mgr, tool):
    """当前选中的安装（没有就返回 None）。"""
    try:
        return next((i for i in mgr.scan(tool.id) if i.current), None)
    except Exception:  # noqa: BLE001
        return None


def _managed_names(mgr) -> List[str]:
    out: List[str] = []
    for t in mgr.tools():
        for v in t.managed_vars():
            if v and v not in out:
                out.append(v)
    return out


# --------------------------------------------------------------------------- #
# 第一段：计划（普通权限即可执行，纯计算，不动任何东西）
# --------------------------------------------------------------------------- #


def plan_system_drop(mgr, wanted: List[str], protect_current: bool = True) -> Dict[str, Any]:
    """算出"想把 ``wanted`` 从系统 PATH 里请出去"这件事，实际能做多少。

    返回::

        {
          "ok": bool, "error": str,
          "system": [...],          # 当前系统 PATH 快照
          "remove": [...],          # 可以安全移除的条目
          "keep":  [{"path","reason"}],   # 决定保留的条目 + 原因
          "missing": [...],         # wanted 里已经不在系统 PATH 的
        }

    安全闸（三条，缺一条就会出事故）：

      1. **当前选中版本自己的条目不动** —— 那是要用的东西，删了没意义；
      2. **不拆安装**：命中条目如果归属于某个安装目录（``<Python>`` /
         ``<Python>\\Scripts`` / ``<JDK>\\bin``），该目录在系统 PATH 里的条目必须
         **整组**都在待删名单里才动手；只删一半会把 pip 这类自带命令弄丢；
      3. **删前模拟可达性**：把待删条目从"系统 + 用户"里去掉后，所有工具都必须
         仍然找得到命令行入口（真文件才算，Store 别名不算）。会删没的一律保留。
    """
    res: Dict[str, Any] = {
        "ok": False, "error": "", "system": [],
        "remove": [], "keep": [], "missing": [],
    }
    if not IS_WIN:
        res["error"] = "仅 Windows 存在系统级 PATH"
        return res

    from .env import get_backend  # noqa: PLC0415

    backend = get_backend()
    if not hasattr(backend, "system_path"):
        res["error"] = "当前后端不支持系统 PATH 修改"
        return res

    try:
        system = [p for p in backend.system_path() if p and p.strip()]
    except Exception as e:  # noqa: BLE001
        res["error"] = f"读不到系统 PATH：{e}"
        return res
    if not system:
        res["error"] = "读不到系统 PATH"
        return res
    res["system"] = list(system)

    try:
        user = backend.get_path()
    except Exception:  # noqa: BLE001
        user = []

    sys_keys = {_k(p) for p in system}
    wanted = [p for p in (wanted or []) if p]
    res["missing"] = [p for p in wanted if _k(p) not in sys_keys]
    cand = [p for p in system if _k(p) in {_k(x) for x in wanted}]
    if not cand:
        res["ok"] = True
        return res

    all_tools = [t for t in mgr.tools() if t.bin_names]
    managed = _managed_names(mgr)

    # 当前选中版本自己的目录：一律不动
    protect = set()
    if protect_current:
        for t in all_tools:
            ins = _current_install(mgr, t)
            if not ins:
                continue
            protect.add(_k(ins.home))
            for e in t.path_entries_for(ins.home, ins.exe):
                protect.add(_k(e))

    scanner = getattr(mgr, "scanner", None)
    can_root = callable(getattr(scanner, "install_root_of", None))

    def root_of(item: str) -> Tuple[str, str]:
        """这条 PATH 属于哪个安装目录（找不到返回 ("", "")）。"""
        if not can_root:
            return "", ""
        for t in all_tools:
            try:
                r = scanner.install_root_of(t, item)
            except Exception:  # noqa: BLE001
                continue
            if r:
                return _k(r), t.name
        return "", ""

    def keep(item: str, reason: str) -> None:
        res["keep"].append({"path": item, "reason": reason})

    # 所有工具的入口可达性基线
    before = {t.id: _reachable(t, system + user) for t in all_tools}
    kept = list(system)

    def would_break(drop: List[str]) -> List[str]:
        keys = {_k(p) for p in drop}
        left = [p for p in kept if _k(p) not in keys]
        return [t for t in all_tools if before.get(t.id) and not _reachable(t, left + user)]

    # 分类：受保护 / 变量引用 / 属于某个安装目录 / 零散条目
    grouped: Dict[str, Dict[str, Any]] = {}
    singles: List[str] = []
    for item in cand:
        key = _k(item)
        if key in protect:
            keep(item, "它是你当前选中版本自己的目录，删了就轮到别的版本了")
            continue
        if is_managed_var_ref(item, managed):
            keep(item, "它引用的是本工具写入的变量（%JAVA_HOME% 之类），会跟着切换一起变")
            continue
        root, owner = root_of(item)
        if root:
            grouped.setdefault(root, {"items": [], "owner": owner, "root": root})["items"].append(item)
        else:
            singles.append(item)

    # 1) 归属于某个安装目录：整组决策
    #    关键修正（旧版一刀切「组里只要有一条不在待删名单就整组保留」，导致旧版本的
    #    根目录 C:\Python314 被它的 Scripts 绑住、删不掉，切换一直不生效）。
    #    正确做法是看「主命令入口」是否被抢路：
    #      · 主命令入口（python.exe / java.exe 所在的根目录）在待删名单里 → 说明这个
    #        安装已被新版本接管，整组（根 + Scripts 等）一起清掉最干净；
    #      · 整组清掉之前用 would_break 验证「每条命令仍有别的来源」，不安全就整组保留。
    for root, g in grouped.items():
        whole = [p for p in system if root_of(p)[0] == root]
        if not whole:
            continue
        cand_keys = {_k(x) for x in wanted}
        cands = [p for p in whole if _k(p) in cand_keys]
        if not cands:
            continue
        # 找出这个安装里真正提供「主命令」的那一条（根目录；Scripts 只提供 pip 这种副命令）
        owner_tool = next((t for t in all_tools if t.name == g["owner"]), None)
        primary = None
        if owner_tool is not None:
            for e in whole:
                if provides_command(e, owner_tool.bin_names or []):
                    primary = e
                    break
        if primary is None:
            for t in all_tools:
                hit = next((e for e in whole if provides_command(e, t.bin_names or [])), None)
                if hit:
                    primary = hit
                    break
        if primary is None:
            primary = whole[0]
        if _k(primary) in cand_keys:
            # 主命令入口正在被抢路 → 整组（根 + Scripts 等）一起请出去
            broke = would_break(whole)
            if broke:
                for item in whole:
                    keep(item, f"整组保留：删掉之后 "
                               f"{'、'.join(t.name for t in broke[:3])} 在命令行里就找不到了")
                continue
            keys = {_k(p) for p in whole}
            kept = [p for p in kept if _k(p) not in keys]
            res["remove"] += whole
        else:
            # 只有副命令条目被点名（极少见），逐条安全删除，绝不拆得半拉
            for item in cands:
                if _k(item) not in {_k(p) for p in kept}:
                    continue
                broke = would_break([item])
                if broke:
                    keep(item, f"删掉之后 {'、'.join(t.name for t in broke[:3])} "
                               "在命令行里就找不到了，已保留")
                    continue
                kept = [p for p in kept if _k(p) != _k(item)]
                res["remove"].append(item)

    # 2) 零散条目：逐条决策
    for item in singles:
        if _k(item) not in {_k(p) for p in kept}:
            continue
        broke = would_break([item])
        if broke:
            keep(item, f"删掉之后 {'、'.join(t.name for t in broke[:3])} 在命令行里就找不到了，已保留")
            continue
        kept = [p for p in kept if _k(p) != _k(item)]
        res["remove"].append(item)

    res["ok"] = True
    return res


def preview_system_fix(mgr, wanted: List[str]) -> Dict[str, Any]:
    """给界面看的预览：将要移除什么、将保留什么（不写任何东西）。"""
    return plan_system_drop(mgr, wanted)


def _shadow_union(mgr, target: str = "all") -> List[str]:
    """汇总"抢在你选中的版本前面、且真的能提供该命令"的系统条目。"""
    from .audit import shadow_entries  # noqa: PLC0415

    wanted: List[str] = []
    for t in mgr.tools():
        if target != "all" and t.id != target:
            continue
        cur = _current_install(mgr, t)
        if not cur:
            continue
        try:
            _own, shadow = shadow_entries(mgr, t, cur)
        except Exception:  # noqa: BLE001
            shadow = []
        for p in shadow:
            if p not in wanted:
                wanted.append(p)
    return wanted


# --------------------------------------------------------------------------- #
# 第二段：写入（需要管理员）
# --------------------------------------------------------------------------- #


def apply_system_drop(entries: List[str], system: Optional[List[str]] = None) -> Dict[str, Any]:
    """按**精确路径**从系统 PATH 里摘掉指定条目（提权上下文里执行）。

    调用方必须先用 ``plan_system_drop`` 算过一遍；这里只负责写。
    每次写入前都会备份当前系统 PATH（已有备份不覆盖，保留最原始的一份）。
    """
    if not IS_WIN:
        return {"ok": False, "removed": [], "error": "仅 Windows 存在系统级 PATH"}

    from .env import get_backend, save_backup  # noqa: PLC0415

    backend = get_backend()
    if not hasattr(backend, "write_system_path"):
        return {"ok": False, "removed": [], "error": "当前后端不支持系统 PATH 修改"}

    if system is None:
        system = [p for p in backend.system_path() if p and p.strip()]
    if not system:
        return {"ok": False, "removed": [], "error": "读不到系统 PATH"}

    want = {_k(e) for e in (entries or []) if e}
    removed = [p for p in system if _k(p) in want]
    if not removed:
        return {"ok": True, "removed": [], "error": ""}

    save_backup(system)
    try:
        backend.write_system_path([p for p in system if _k(p) not in want])
    except PermissionError:
        return {"ok": False, "removed": [], "error": "权限不足，需要以管理员身份运行"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "removed": [], "error": str(e)}
    return {"ok": True, "removed": removed, "error": ""}


def apply_system_fix(mgr, target: str = "all") -> Dict[str, Any]:
    """把"抢在你选中版本前面"的系统条目请出去（提权上下文里执行）。"""
    wanted = _shadow_union(mgr, target)
    if not wanted:
        return {"ok": True, "removed": [], "keep": [], "error": ""}
    plan = plan_system_drop(mgr, wanted)
    if not plan.get("ok"):
        return {"ok": False, "removed": [], "keep": [], "error": plan.get("error", "")}
    res = apply_system_drop(plan["remove"], plan["system"])
    res["keep"] = plan.get("keep", [])
    return res


def fix_system_drop(mgr, wanted: List[str]) -> Tuple[bool, str, List[str]]:
    """对外入口：让这些条目让位（先计划，再按需提权写入）。返回 (成功, 消息, 已移除)。"""
    if not IS_WIN:
        return False, "仅 Windows 存在系统级 PATH", []
    wanted = [p for p in (wanted or []) if p]
    if not wanted:
        return False, "没有需要处理的条目", []

    plan = plan_system_drop(mgr, wanted)
    if not plan.get("ok"):
        return False, plan.get("error") or "无法计算要清理的条目", []
    if not plan["remove"]:
        msg = "为了安全，这次一条都没动"
        kept = plan.get("keep") or []
        if kept:
            msg += "：\n" + "\n".join(f"　· {r['path']}　—　{r['reason']}" for r in kept[:6])
        return False, msg, []

    entries = list(plan["remove"])
    if is_admin():
        res = apply_system_drop(entries, plan["system"])
    else:
        clear_result()
        ok, err = run_elevated(["systemdrop", encode_arg(entries), "--quiet"])
        res = read_result() or {}
        if not ok and not res:
            return False, err or "提权失败或已取消", []

    if not res.get("ok"):
        return False, res.get("error", "清理失败"), []
    removed = res.get("removed", [])
    if not removed:
        return False, "这些条目已经不在系统 PATH 里了", []

    msg = f"已从系统 PATH 移除 {len(removed)} 条，切换现在应该能生效了"
    kept = plan.get("keep") or []
    if kept:
        names = "、".join((r["path"].rstrip("\\/").split("\\")[-1] or r["path"]) for r in kept[:3])
        msg += f"；另有 {len(kept)} 条为安全起见保留（{names}），体检里能看到原因"
    return True, msg, removed


def fix_system_conflicts(mgr, target: str = "all") -> Tuple[bool, str, List[str]]:
    """对外入口：让某个工具（或全部）当前选中的版本真正生效。"""
    if not IS_WIN:
        return False, "仅 Windows 存在系统级 PATH 冲突", []
    wanted = _shadow_union(mgr, target)
    if not wanted:
        return True, "系统 PATH 里没有抢在你选中版本前面的条目", []
    return fix_system_drop(mgr, wanted)


# --------------------------------------------------------------------------- #
# 还原
# --------------------------------------------------------------------------- #


def _rebuild_path(backup: List[str], current: List[str], restored: List[str]) -> List[str]:
    """按备份的原始顺序重建系统 PATH：备份里留着的 + 这次放回的，之后追加后来新加的。"""
    restored_keys = {_k(p) for p in restored}
    cur_keys = {_k(p) for p in current}
    bak_keys = {_k(p) for p in backup}
    out = [p for p in backup if _k(p) in restored_keys or _k(p) in cur_keys]
    out += [p for p in current if _k(p) not in bak_keys]
    return out


def _regressed_tools(mgr, system_now: List[str], system_try: List[str], user: List[str]) -> List[str]:
    """这个组合会让哪些工具的命令行不再命中"你选中的版本"？"""
    from .audit import selected_entry  # noqa: PLC0415

    bad: List[str] = []
    for t in mgr.tools():
        names = list(t.bin_names or [])
        if not names:
            continue
        cur = _current_install(mgr, t)
        if not cur:
            continue
        own = {_k(e) for e in selected_entry(mgr, t, cur)}
        if not own:
            continue
        now = _resolve(system_now + user, names)
        if not now or _k(now) not in own:
            continue                      # 现在就已经不是它了，不算被还原搞坏的
        after = _resolve(system_try + user, names)
        if after and _k(after) not in own:
            bad.append(t.name)
    return bad


def restore_system_path(mgr=None, smart: bool = True) -> Dict[str, Any]:
    """把系统 PATH 还原成备份时的样子。

    ``smart=True``（默认）时**不还原"会重新抢在前面"的条目**：备份里那些旧版本的
    java.exe / python.exe 入口如果直接放回去，切换立刻又失效 —— 用户观感就是
    "还原一次又乱一次"。它们会被列在 ``held`` 里，需要的话用 ``smart=False`` 全量还原。

    返回 ``{"ok", "added": [...], "held": [{"path","reason"}], "error"}``。
    """
    empty = {"ok": False, "added": [], "held": [], "error": ""}
    if not IS_WIN:
        return {**empty, "error": "仅 Windows 存在系统级 PATH"}

    from .env import get_backend, load_backup  # noqa: PLC0415

    backend = get_backend()
    data = load_backup()
    if not data:
        return {**empty, "error": "没有找到系统 PATH 备份"}
    backup = [p for p in (data.get("path") or "").split(os.pathsep) if p.strip()]
    if not backup:
        return {**empty, "error": "备份里没有内容"}

    current = [p for p in backend.system_path() if p and p.strip()]
    have = {_k(p) for p in current}
    missing = [p for p in backup if _k(p) not in have]
    if not missing:
        return {"ok": True, "added": [], "held": [], "error": ""}

    add: List[str] = []
    held: List[Dict[str, str]] = []
    for item in missing:
        reason = bad_var_ref_reason(item)
        if reason:
            held.append({"path": item, "reason": f"这条变量引用本来就是坏的（{reason}），"
                                                  "放回去命令行也用不到"})
            continue
        if smart and mgr is not None:
            try:
                user = backend.get_path()
            except Exception:  # noqa: BLE001
                user = []
            trial = _rebuild_path(backup, current, add + [item])
            bad = _regressed_tools(mgr, current, trial, user)
            if bad:
                held.append({
                    "path": item,
                    "reason": f"放回去之后 {'、'.join(bad)} 的命令行会命回它，切换又失效",
                })
                continue
        add.append(item)

    if not add:
        return {"ok": True, "added": [], "held": held, "error": ""}

    try:
        backend.write_system_path(_rebuild_path(backup, current, add))
    except PermissionError:
        return {**empty, "held": held, "error": "权限不足，需要以管理员身份运行"}
    except Exception as e:  # noqa: BLE001
        return {**empty, "held": held, "error": str(e)}
    return {"ok": True, "added": add, "held": held, "error": ""}


def _restore_text(res: Dict[str, Any]) -> str:
    added = res.get("added") or []
    held = res.get("held") or []
    if not res.get("ok"):
        return res.get("error", "还原失败")
    if not added and not held:
        return "系统 PATH 已经和备份一致，无需还原"
    lines = []
    if added:
        lines.append(f"已把 {len(added)} 条放回系统 PATH"
                     + (f"（例如 {added[0]}）" if added else ""))
    if held:
        lines.append(f"另有 {len(held)} 条**没有**放回去（放回去切换就失效了）：")
        lines += [f"　· {h['path']}　—　{h['reason']}" for h in held[:5]]
    return "\n".join(lines)


def fix_restore_system_path(mgr=None, smart: bool = True) -> Tuple[bool, str]:
    """对外入口：还原系统 PATH（需要管理员，会弹 UAC）。"""
    if not IS_WIN:
        return False, "仅 Windows 存在系统级 PATH"

    if is_admin():
        res = restore_system_path(mgr, smart=smart)
    else:
        clear_result()
        sub = ["systempath-restore", "--quiet"]
        if not smart:
            sub = ["systempath-restore", "--all", "--quiet"]
        ok, err = run_elevated(sub)
        res = read_result() or {}
        if not ok and not res:
            return False, err or "提权失败或已取消"
    return bool(res.get("ok")), _restore_text(res)
