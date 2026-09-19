"""环境体检（audit）。

回答几个"为什么不生效 / 我到底装了些啥"的问题：

    not_in_path     —— 磁盘上装了、但 PATH/主目录变量里没有它的路径（本工具最常被问的场景）
    unmanaged       —— PATH 里有某个工具的路径，但 EnvSwitch 列表里没管到
    blocked         —— 命令行实际命中的**不是**你选中的版本（有更早的条目抢在前面）
    bad_var_ref     —— PATH 里的 %VAR% 引用不可达（含 %%JAVA_HOME%\\bin 这种畸形写法）
    store_alias     —— 命令行命中的其实是微软商店的「应用执行别名」（已失效的，或抢在真安装前面的）
    home_var_stale  —— JAVA_HOME / PYTHON_HOME 之类指向了无效位置
    system_path_modified —— 以前移除过的系统 PATH 条目，可以还原回去
    dead_path       —— PATH 里指向已不存在目录的条目
    duplicate       —— PATH 里重复出现的条目

每个问题都带一个 fix 标识，交给 fix_issue() 一键处理。

三条硬规矩（都是踩过坑补上的）：

  * **不假装修好**：fix_issue() 执行完动作后会重新体检一次，
    同一个问题还在就如实报"没修好"，而不是弹一句"已修复"糊弄过去。

  * **判定要如实**：「被更靠前的条目挡住」必须基于"这一段 PATH 到底能不能提供
    该命令"（``provides_command``），而不是拿 ``python`` / ``java`` 这类关键词做
    子串匹配。旧版按关键词判定，于是 ``C:\\Python314\\Scripts``（里面的 pip 是
    用户天天要用的）因为目录名里带 python 被算成"挡路的条目"，一键修复时直接
    从系统 PATH 删掉 —— 用户看到的就是"越修越乱"。

  * **动系统 PATH 的修复一律要单独确认**：这类问题带上 ``needs_confirm=True``，
    「一键修复全部」会跳过它们，必须逐个点、看清要删什么、再决定。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

from .models import (
    IS_WIN,
    Install,
    ToolDef,
    bad_var_ref_reason,
    is_managed_var_ref,
    is_store_alias,
    norm_key,
    norm_path,
    provides_command,
    store_alias_target,
)

KIND_LABELS = {
    "not_in_path": "未加入环境变量",
    "unmanaged": "未纳入管理",
    "blocked": "命令行命中的不是选中版本",
    "bad_var_ref": "变量引用不可达",
    "store_alias": "商店别名（微软商店安装）",
    "home_var_stale": "主目录变量失效",
    "system_path_modified": "系统 PATH 备份",
    "dead_path": "失效条目",
    "duplicate": "重复条目",
}

SEVERITY_ORDER = {"warn": 0, "info": 1}

# 包管理器的 shim 目录：里面是"跳板"，不代表真实安装，不该报「未纳入管理」
SHIM_MARKERS = ("chocolatey", "scoop\\shims", "scoop/shims", "scoop\\apps", "scoop/apps")


def _is_shim_dir(p: str) -> bool:
    low = p.replace("/", "\\").lower()
    return any(m.replace("/", "\\") in low for m in SHIM_MARKERS)


@dataclass
class Issue:
    kind: str
    severity: str                      # warn | info
    tool_id: str
    title: str
    detail: str
    items: List[str] = field(default_factory=list)
    #: use | add_path | unblock | drop_bad_ref | clear_home_var | restore_system_path | drop_dead | dedupe
    fix: str = ""
    fixable: bool = True
    #: 会改动系统 PATH 的问题：必须逐个确认，不参与「一键修复全部」
    needs_confirm: bool = False
    payload: Any = None

    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)

    def ignore_key(self) -> str:
        """忽略标识：精确到第一个涉及路径，避免"忽略一条把同类问题全隐藏"。"""
        if not self.items:
            return f"{self.kind}:{self.tool_id}"
        return f"{self.kind}:{self.tool_id}:{self.items[0]}"


# --------------------------------------------------------------------------- #
# 基础信息
# --------------------------------------------------------------------------- #


def effective_path(mgr) -> List[str]:
    """当前真正生效的 PATH（Windows = 系统 PATH + 用户 PATH，顺序即优先级）。"""
    items: List[str] = []
    if hasattr(mgr.backend, "system_path"):
        try:
            items += mgr.backend.system_path()
        except Exception:  # noqa: BLE001
            pass
    try:
        items += mgr.backend.get_path()
    except Exception:  # noqa: BLE001
        items += [p for p in os.environ.get("PATH", "").split(os.pathsep) if p.strip()]
    return [x for x in items if x.strip()]


def _user_path(mgr) -> List[str]:
    try:
        return mgr.backend.get_path()
    except Exception:  # noqa: BLE001
        return [p for p in os.environ.get("PATH", "").split(os.pathsep) if p.strip()]


def _system_path(mgr) -> List[str]:
    """系统级 PATH（只有 Windows 有；读不到就返回空表）。"""
    if not hasattr(mgr.backend, "system_path"):
        return []
    try:
        return [p for p in mgr.backend.system_path() if p and p.strip()]
    except Exception:  # noqa: BLE001
        return []


def _key(p: str) -> str:
    """比对用的 PATH 条目归一化（保留 %VAR% 原样，不走 abspath）。"""
    return norm_key(p)


def selected_entry(mgr, tool: ToolDef, cur: Install) -> List[str]:
    """当前选中版本**自己的**、真能提供命令的 PATH 条目。

    例如 Python 选中 ``C:\\Python314`` 时返回 ``[C:\\Python314]``；
    ``C:\\Python314\\Scripts`` 不在这里 —— 它提供的是 pip，不是 python.exe。
    """
    names = list(tool.bin_names or [])
    out: List[str] = []
    try:
        cands = list(tool.path_entries_for(cur.home, cur.exe))
    except Exception:  # noqa: BLE001
        cands = []
    if cur.home:
        cands.append(cur.home)
    for e in cands:
        if not e or is_store_alias(e):
            continue
        if not os.path.isdir(e):
            continue
        if provides_command(e, names) and e not in out:
            out.append(e)
    return out


def shadow_entries(mgr, tool: ToolDef, cur: Install,
                   eff: Optional[List[str]] = None) -> Tuple[List[str], List[str]]:
    """返回 ``(选中版本自己的条目, 抢在它前面的条目)``。

    "抢在前面"的判定标准只有三条，全都可验证：

      1. 它排在"选中版本自己的条目"**之前**（PATH 从左到右第一个命中的才算数）；
      2. 它**真的能提供**这个命令（``provides_command``：目录里得有 python.exe / java.exe
         这种真文件；Store 别名不算）—— 只靠名字里带 python/java 不算；
      3. 它和"选中版本自己的条目"**不属于同一个安装目录**
         （``C:\\Python314\\Scripts`` 和 ``C:\\Python314`` 是一家的，不算抢路）。

    这样算出来的才是真会让"切换不生效"的条目。旧版用关键词子串匹配，
    会把 pip 的目录、别的工具的目录一起算进来，一键修复就等于在乱删。
    """
    own = selected_entry(mgr, tool, cur)
    if not own:
        return [], []
    if eff is None:
        eff = effective_path(mgr)
    own_keys = {_key(e) for e in own}
    managed = tool.managed_vars()
    shadow: List[str] = []
    for p in eff:
        k = _key(p)
        if k in own_keys:
            break
        if not p.strip() or is_store_alias(p):
            continue
        if is_managed_var_ref(p, managed):
            continue
        if not provides_command(p, tool.bin_names or []):
            continue
        if _same_install(k, own_keys):
            continue
        shadow.append(p)
    return own, shadow


def _same_install(key: str, own_keys) -> bool:
    """这条 PATH 和"选中版本自己的条目"是不是属于同一个安装目录。"""
    for o in own_keys:
        if key == o or key.startswith(o + os.sep) or o.startswith(key + os.sep):
            return True
    return False


def _drop_from_user_path(mgr, entries: List[str]) -> int:
    """从**用户** PATH 里精确删掉若干条目，返回真正删掉的条数（无需管理员）。"""
    want = {_key(e) for e in entries or [] if e}
    if not want or not hasattr(mgr.backend, "set_path"):
        return 0
    user = _user_path(mgr)
    keep = [p for p in user if _key(p) not in want]
    if len(keep) == len(user):
        return 0
    mgr.backend.set_path(keep)
    return len(user) - len(keep)


def _owner_tool_id(mgr, path: str) -> str:
    """这条 PATH 看起来是哪个工具的（找不到返回 "*"）。"""
    low = path.replace("\\", "/").lower()
    for tool in mgr.tools():
        for kw in tool.conflict_keywords or []:
            k = kw.lower().strip("\\/")
            if k and k in low:
                return tool.id
    return "*"


def _addable_target(mgr, tool: ToolDef, home: str, entry: str) -> str:
    """给出一个「加进去之后真的能被识别」的路径（file 模式要给主程序本身）。

    「未纳入管理」的修复走的是 ``mgr.add_path()``，而它要的是**主程序或安装目录**；
    之前这里直接把 PATH 里的那一段（往往是个目录）塞进去，
    于是 file 模式的工具必然失败 —— 用户看到的就是「部分修复失败：
    Node.js：PATH 里有一段没被管理的路径：不是有效的 Node.js 可执行文件」。
    """
    if tool.entry != "file":
        h, _e = mgr.scanner._interpret(tool, home)
        return home if h else ""
    found = mgr.scanner.main_exe_in(tool, home)
    if not found:
        h, _e = mgr.scanner._interpret(tool, entry)
        return entry if h else ""
    h, _e = mgr.scanner._interpret(tool, found)
    return found if h else ""


def _alias_provides(tool: ToolDef, entry: str) -> bool:
    """这一段 Store 别名目录里，是不是真有该工具的命令跳板。"""
    import os as _os  # noqa: PLC0415

    try:
        base = _os.path.expandvars(entry.strip().strip('"'))
    except Exception:  # noqa: BLE001
        base = entry
    for n in tool.bin_names or []:
        try:
            if _os.path.isfile(_os.path.join(base, n + (".exe" if IS_WIN else ""))):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _alias_home_exe(tool: ToolDef, entry: str) -> str:
    """商店别名目录里，该工具那个命令跳板的完整路径（找不到返回 ""）。"""
    import os as _os  # noqa: PLC0415

    try:
        base = _os.path.expandvars(entry.strip().strip('"'))
    except Exception:  # noqa: BLE001
        base = entry
    for n in tool.bin_names or []:
        p = _os.path.join(base, n + (".exe" if IS_WIN else ""))
        try:
            if _os.path.isfile(p):
                return p
        except Exception:  # noqa: BLE001
            continue
    return ""


def _alias_live(tool: ToolDef, entry: str) -> bool:
    """这一段商店别名目录，是不是**真装着的**商店版（别名指向真实存在的程序）。

    真装着的商店版现在是一个正常的安装（扫描能列出来、来源标 store、可以切换），
    所以不能再像旧版那样一律说它"不是真安装"；只有空壳（应用已卸载）才该报警。
    """
    return bool(store_alias_target(_alias_home_exe(tool, entry)))


def _alias_wins(tool: ToolDef, eff: List[str], entry: str) -> bool:
    """命令行第一个命中的是不是这个别名目录。

    Windows 自己**不看我们那套安全闸**：谁在 PATH 前面谁赢，别名也一样。
    所以"别名有没有真抢到命令"要按 PATH 顺序实算，不能只看它存不存在。
    """
    names = tool.bin_names or []
    key = _key(entry)
    for p in eff:
        if not p.strip():
            continue
        ok = _alias_provides(tool, p) if is_store_alias(p) else provides_command(p, names)
        if not ok:
            continue
        return _key(p) == key
    return False


def _home_var_valid(mgr, tool: ToolDef, value: str) -> bool:
    """主目录变量指的地方，是不是该工具的一个真安装。"""
    if not value or not os.path.isdir(value) or is_store_alias(value):
        return False
    return bool(find_tool_home(mgr, tool, value))


# --------------------------------------------------------------------------- #
# 体检
# --------------------------------------------------------------------------- #


def find_tool_home(mgr, tool: ToolDef, p: str) -> str:
    """从 PATH 里的某一段反查它属于哪个安装主目录（找不到返回空串）。

    PATH 里放的可能是主程序文件、安装根目录、或者只是 `.../bin`，三种都要试。
    """
    if not p:
        return ""
    for cand in (p, os.path.dirname(p.rstrip("\\/"))):
        if not cand:
            continue
        home, _exe = mgr.scanner._interpret(tool, cand)
        if home:
            return home
    if os.path.isdir(p):
        # file 模式：主程序就躺在这个目录里（python.exe / node.exe ...）
        rgx = tool.file_regex()
        if rgx:
            try:
                for name in sorted(os.listdir(p)):
                    if rgx.fullmatch(name):
                        home, _exe = mgr.scanner._interpret(tool, os.path.join(p, name))
                        if home:
                            return home
            except OSError:
                pass
    return ""


def audit(mgr, include_info: bool = True, include_ignored: bool = False) -> List[Issue]:
    issues: List[Issue] = []
    eff = effective_path(mgr)
    eff_norm = {norm_path(p) for p in eff}
    ignored = set() if include_ignored else set(mgr.config.audit_ignored())

    for tool in mgr.tools():
        installs = mgr.scan(tool.id)
        homes = {norm_path(i.home) for i in installs}

        # 1) 命令行实际命中的是不是「你选中的版本」？
        #    判定完全基于「这一段 PATH 到底能不能提供该命令」：
        #      · C:\Python314\Scripts 里没有 python.exe → 它挡不住 python，不报；
        #      · Oracle javapath 里有 java.exe 且排在 jdk-17\bin 前面 → 报，且它就是根因。
        #    旧版拿 python/java 这类关键词做子串匹配，才会把 pip 的目录算成"挡路条目"。
        cur = next((i for i in installs if i.current), None)
        if cur:
            own, shadow = shadow_entries(mgr, tool, cur, eff)
            if shadow:
                wins = shadow[0]
                issues.append(Issue(
                    kind="blocked",
                    severity="warn",
                    tool_id=tool.id,
                    title=f"{tool.name}：命令行命中的是 {wins}，不是你选中的 "
                          f"{cur.version or cur.home}",
                    detail="Windows 的生效顺序是「系统 PATH → 用户 PATH」，从左到右第一个\n"
                           "能提供该命令的条目说了算。下面这些条目排在你要用的版本前面，\n"
                           "所以命令行跑到的还是它们。\n\n"
                           "修复＝把它们从系统 PATH 里请出去（会先备份系统 PATH，"
                           "随时可以在「系统 PATH 备份」那条问题里还原）。\n"
                           "这一步会影响整个系统的命令行环境，所以不参与「一键修复全部」，"
                           "需要你确认后再动手。",
                    items=shadow[:6],
                    fix="unblock",
                    needs_confirm=True,
                    payload=tool.id,
                ))

        # 2) 每个安装：有没有进 PATH
        for ins in installs:
            entries = [e for e in tool.path_entries_for(ins.home, ins.exe) if os.path.isdir(e)]
            in_path = [e for e in entries if norm_path(e) in eff_norm]
            home_var_hit = bool(tool.home_var) and norm_path(
                os.environ.get(tool.home_var, "")) == norm_path(ins.home)
            # PATH 里引用了 %JAVA_HOME% 之类、且该变量正指向这里 → 实际可达，不算缺失
            # 注意：%%JAVA_HOME%\bin 这种百分号没配对的写法不算（展开后是死路径）
            var_ref_hit = bool(tool.home_var) and any(
                is_managed_var_ref(p, tool.managed_vars()) for p in eff)
            if in_path or (home_var_hit and (not entries or var_ref_hit)):
                continue
            if not include_info:
                continue
            issues.append(Issue(
                kind="not_in_path",
                severity="info",
                tool_id=tool.id,
                title=f"{tool.name} {ins.version or ''}：未出现在环境变量中".strip(),
                detail="磁盘上有这个安装，但 PATH 里没有它的目录，命令行目前用不到。"
                       "修复＝把它设为当前版本（会写入主目录变量并前置 PATH）。",
                items=entries or [ins.home],
                fix="use",
                payload=(tool.id, ins),
            ))

        # 4) PATH 里存在该工具、但没被管理到的路径
        for p in eff:
            np = norm_path(p)
            # Store 别名目录单独报（下面第 5 条），不算"未纳管"；shim 目录同理
            if np in homes or _is_shim_dir(p) or is_store_alias(p):
                continue
            home = find_tool_home(mgr, tool, p)
            if not home or norm_path(home) in homes or is_store_alias(home):
                continue
            homes.add(norm_path(home))
            target = _addable_target(mgr, tool, home, p)
            if target:
                issues.append(Issue(
                    kind="unmanaged",
                    severity="info",
                    tool_id=tool.id,
                    title=f"{tool.name}：PATH 里有一段没被管理的路径",
                    detail="这段路径下能找到该工具，但不在 EnvSwitch 列表中。修复＝纳入管理，之后就能一键切换。",
                    items=[home],
                    fix="add_path",
                    payload=(tool.id, target),
                ))
            else:
                issues.append(Issue(
                    kind="unmanaged",
                    severity="info",
                    tool_id=tool.id,
                    title=f"{tool.name}：PATH 里有一段没被管理的路径",
                    detail="这一段看起来和该工具有关，但里面找不到它的主程序，"
                           "无法自动纳入管理。确认需要的话，用主界面的「+ 添加路径」手动选。",
                    items=[home],
                    fix="",
                    fixable=False,
                ))

        # 4.5) Microsoft Store 的「应用执行别名」：分两种情况说
        #   * 真装着的商店版**真把命令抢了过去** → 提醒一句（它能用，但排在真安装前面
        #     会让"切换看起来不生效"）；
        #   * 空壳（应用已卸载，跑它只会弹应用商店）→ 提醒一句。
        #   商店版装着、但命令根本没轮到它 = 完全正常，不报。
        #   旧版只要 PATH 里有这个别名目录就报"不是真安装"：用户明明跑的是
        #   C:\Python314，却被告知"python 指向商店别名"，纯属误报。
        for p in eff:
            if not is_store_alias(p) or not _alias_provides(tool, p):
                continue
            live = _alias_live(tool, p)
            if live and not _alias_wins(tool, eff, p):
                continue
            cmd = tool.bin_names[0] if tool.bin_names else tool.name
            if live:
                title = f"{tool.name}：命令行里的 {cmd} 走的是微软商店版"
                detail = (f"{p} 是微软商店的「应用执行别名」，它排在真安装前面、把 {cmd} 抢了过去。"
                          "商店版本身能用，所以这里只提示不当错误；但它不适合当主力"
                          "（更新 / 卸载都会换掉它背后的目录）。"
                          "想让它别抢：把真正想用的版本切一次（切换会把它排到前面），"
                          "或在「设置 → 应用 → 高级应用设置 → 应用执行别名」里关掉。")
            else:
                title = f"{tool.name}：命令行里的 {cmd} 指向一个已失效的商店别名"
                detail = (f"{p} 是微软商店的「应用执行别名」，但它指向的程序已经不存在了"
                          f"（应用已卸载）—— 这时在命令行里跑 {cmd} 会直接弹应用商店。"
                          "可在「设置 → 应用 → 高级应用设置 → 应用执行别名」里关掉它。")
            issues.append(Issue(
                kind="store_alias",
                severity="info",
                tool_id=tool.id,
                title=title,
                detail=detail,
                items=[p],
                fix="",
                fixable=False,
            ))

    # 5) PATH 里"看起来引用了变量、实际命中不了"的条目
    #    （%%JAVA_HOME%\bin 这类写法在 Windows 里展开成字面量路径，等于死条目，
    #     还会把真正的「未加入环境变量」问题掩盖掉，所以单独报出来）
    for layer_name, layer_items in (("系统 PATH", _system_path(mgr)), ("用户 PATH", _user_path(mgr))):
        for p in layer_items:
            if "%" not in p:
                continue
            reason = bad_var_ref_reason(p)
            if not reason:
                continue
            owner = _owner_tool_id(mgr, p)
            in_system = layer_name.startswith("系统")
            issues.append(Issue(
                kind="bad_var_ref",
                severity="warn" if owner != "*" else "info",
                tool_id=owner,
                title=f"{layer_name}里的变量引用不可达：{p}",
                detail=f"{reason}，命令行其实用不到它。"
                       "修复＝把这条无用条目删掉"
                       + ("（系统层需要管理员权限，动手前会自动备份）。" if in_system
                          else "（用户层直接删）。"),
                items=[p],
                fix="drop_bad_ref",
                needs_confirm=in_system,
                payload=layer_name,
            ))

    # 6) PATH 自身的卫生问题
    user = _user_path(mgr)
    dead = [p for p in user if p.strip() and not os.path.isdir(p) and "%" not in p]
    if dead:
        issues.append(Issue(
            kind="dead_path", severity="info", tool_id="*",
            title=f"用户 PATH 里有 {len(dead)} 条指向不存在的目录",
            detail="多半是卸载软件后留下的残留，拖慢命令查找速度，可安全清理。",
            items=dead, fix="drop_dead", payload=dead,
        ))

    seen, dupes = set(), []
    for p in user:
        n = norm_path(p)
        if n in seen:
            dupes.append(p)
        seen.add(n)
    if dupes:
        issues.append(Issue(
            kind="duplicate", severity="info", tool_id="*",
            title=f"用户 PATH 里有 {len(dupes)} 条重复项",
            detail="重复项不影响功能，但会让 PATH 越来越长。",
            items=dupes, fix="dedupe", payload=dupes,
        ))

    # 7) 主目录变量指向了无效位置 / 系统 PATH 被清理过
    issues += _home_var_issues(mgr)
    issues += _system_path_issues(mgr)

    issues = [i for i in issues if not _is_ignored(i, ignored)]
    issues.sort(key=lambda i: (SEVERITY_ORDER.get(i.severity, 9), i.tool_id))
    return issues


def _var_value(mgr, name: str) -> str:
    """读主目录变量的**持久值**（注册表 / shell 配置），而不是本进程继承来的值。"""
    getter = getattr(mgr.backend, "get", None)
    if callable(getter):
        try:
            v = getter(name)
            if v:
                return v
        except Exception:  # noqa: BLE001
            pass
    return os.environ.get(name, "")


def _home_var_issues(mgr) -> List[Issue]:
    """主目录变量指向了无效位置（典型：被 Store 别名写坏）。"""
    out: List[Issue] = []
    for tool in mgr.tools():
        if not tool.home_var:
            continue
        val = _var_value(mgr, tool.home_var)
        if not val or "%" in val or _home_var_valid(mgr, tool, val):
            continue
        if is_store_alias(val):
            reason = "那是微软商店的应用执行别名，不是真的安装"
        elif not os.path.isdir(val):
            reason = "这个目录已经不存在了"
        else:
            reason = f"这个目录里找不到{tool.name}的主程序"
        out.append(Issue(
            kind="home_var_stale",
            severity="warn",
            tool_id=tool.id,
            title=f"{tool.home_var} 指向的不是有效的{tool.name}安装",
            detail=f"当前 {tool.home_var} = {val}，{reason}。"
                   "IDE、构建脚本这类工具会直接读这个变量，留着会把它们一起带偏。"
                   "修复＝清掉它，之后用「切换」重新写入正确的值。",
            items=[val],
            fix="clear_home_var",
            payload=(tool.id, tool.home_var),
        ))
    return out


def _system_path_issues(mgr) -> List[Issue]:
    """以前从系统 PATH 移除过的条目 → 给一个还原的机会。

    注意这条是**提示**、而且**不参与「一键修复全部」**：
    它的"修复"是把条目放回去，本质是一次撤销动作。如果它自动执行，
    就会和「命令行的不是你选中的版本」互相打架 —— 一个删、一个还原，
    用户看到的就是"越修越乱"。所以只做展示＋手动还原；而且还原时会自动跳过
    "放回去就会重新抢在前面"的条目（见 ``elevate.restore_system_path``）。
    """
    out: List[Issue] = []
    if not (IS_WIN and hasattr(mgr.backend, "system_path")):
        return out
    from .env import load_backup  # noqa: PLC0415

    bak = load_backup() or {}
    bak_items = [p for p in (bak.get("path") or "").split(os.pathsep) if p.strip()]
    if not bak_items:
        return out
    current = {_key(p) for p in _system_path(mgr)}
    missing = [p for p in bak_items if _key(p) not in current]
    if not missing:
        return out
    out.append(Issue(
        kind="system_path_modified",
        severity="info",
        tool_id="*",
        title=f"系统 PATH 备份：当时移除的 {len(missing)} 条可以还原",
        detail=f"这台机器上 {bak.get('time', '')} 的系统 PATH 备份在 "
               "~/.envswitch/system_path_backup.json，其中下面这些现在不在系统 PATH 里。\n\n"
               "如果里面有你还要用的目录（比如 Python 的 Scripts，pip 就在这里），"
               "点「修复选中」可以放回去；会先弹确认框列出具体条目。\n"
               "放回去之后如果又抢在切换结果前面，还原过程会自动跳过它并说明原因。",
        items=missing,
        fix="restore_system_path",
        needs_confirm=True,
        payload=None,
    ))
    return out


def _is_ignored(issue: Issue, ignored) -> bool:
    """是否被忽略。兼容旧格式（老版本忽略的是 ``kind:tool_id``）。"""
    return issue.ignore_key() in ignored or f"{issue.kind}:{issue.tool_id}" in ignored


# --------------------------------------------------------------------------- #
# 修复
# --------------------------------------------------------------------------- #


def fix_issue(mgr, issue: Issue) -> Tuple[bool, str]:
    """对单个问题执行修复，并**复核**结果。返回 (是否真的修好了, 消息)。

    复核这一步是关键：以前只要"动作跑完了"就报成功，于是出现过
    「点一下弹出"已修复"，回头体检同一个问题还在」的情况——
    e.g. 挡住切换的条目在**用户** PATH 里，而 `fix_system` 只清系统 PATH，
    什么都没删也会返回成功。
    """
    ok, msg = _apply_fix(mgr, issue)
    if not ok:
        return False, msg
    try:
        left = verify_issue(mgr, issue)
    except Exception:  # noqa: BLE001
        left = None
    if left is not None:
        return False, (msg.replace("\n", " ").strip()
                       + f"　（复核：问题仍在 —— {left.title}）")
    return True, msg


def _orphans(mgr, drop: List[str]) -> List[str]:
    """删掉 ``drop`` 这些条目之后，会有哪些工具在命令行里彻底找不到命令？

    这就是"清理系统 PATH 冲突"的安全闸：命中的条目可能正是某个工具**唯一**的入口
    （典型：Python 装在 C:\\Python314，而它只出现在系统 PATH 里）。盲删的后果是
    工具从机器上"消失"——界面表现就是一个安装都扫不到。
    """
    tools = [t for t in mgr.tools() if t.bin_names]
    eff = effective_path(mgr)
    keys = {_key(d) for d in drop}
    keep_items = [p for p in eff if _key(p) not in keys]
    broke: List[str] = []
    for t in tools:
        if not any(provides_command(p, t.bin_names) for p in eff):
            continue                       # 本来就没有入口，不算被我们删坏的
        if not any(provides_command(p, t.bin_names) for p in keep_items):
            broke.append(t.name)
    return broke


def _clean_entries(mgr, issue: Issue) -> Tuple[bool, str]:
    """按条目**实际所在层级**清理：用户层直接删，系统层按需提权。"""
    entries = [p for p in (issue.items or []) if p]
    if not entries:
        return False, "没有需要清理的条目"

    broke = _orphans(mgr, entries)
    if broke:
        names = "、".join(broke)
        return False, (f"为了安全没有动这几条：删掉之后 {names} 在命令行里就找不到了。"
                       "想让它让位，先在上面的列表里把要用的版本「切换」过去，"
                       "体检就会给出可以安全清理的条目。")

    user_norm = {_key(p) for p in _user_path(mgr)}
    in_user = [p for p in entries if _key(p) in user_norm]
    in_sys = [p for p in entries if _key(p) not in user_norm]

    msgs: List[str] = []
    if in_user:
        n = _drop_from_user_path(mgr, in_user)
        msgs.append(f"已从用户 PATH 移除 {n} 条" if n else "用户 PATH 里已经找不到这些条目")
    if in_sys:
        from .elevate import fix_system_drop  # noqa: PLC0415

        ok, msg, _removed = fix_system_drop(mgr, in_sys)
        msgs.append(msg)
        if not ok:
            return False, "；".join(msgs)
    if not msgs:
        return False, "没有需要清理的条目"
    return True, "；".join(msgs)


def _apply_fix(mgr, issue: Issue) -> Tuple[bool, str]:
    """执行修复动作（不负责复核）。返回 (动作是否成功, 消息)。"""
    try:
        if issue.fix == "use":
            tool_id, ins = issue.payload
            ok, msg, _warns = mgr.use(tool_id, ins)
            return ok, msg.replace("\n", " ")

        if issue.fix == "add_path":
            tool_id, path = issue.payload
            return mgr.add_path(tool_id, path)

        if issue.fix in ("fix_system", "fix_blocked", "unblock"):
            return _clean_entries(mgr, issue)

        if issue.fix == "drop_bad_ref":
            entry = (issue.items or [""])[0]
            if not entry:
                return False, "没有需要清理的条目"
            if "系统" in str(issue.payload or ""):
                from .elevate import fix_system_drop  # noqa: PLC0415

                ok, msg, _removed = fix_system_drop(mgr, [entry])
                return ok, msg
            n = _drop_from_user_path(mgr, [entry])
            return (True, f"已从用户 PATH 移除 {n} 条") if n else (False, "用户 PATH 里已经找不到这条了")

        if issue.fix == "clear_home_var":
            _tool_id, var = issue.payload
            try:
                mgr.backend.unset(var)
            except Exception as e:  # noqa: BLE001
                return False, f"清除 {var} 失败：{e}"
            return True, f"已清除 {var}（下次切换会写入正确的值）"

        if issue.fix == "restore_system_path":
            from .elevate import fix_restore_system_path  # noqa: PLC0415

            return fix_restore_system_path()

        if issue.fix in ("drop_dead", "dedupe"):
            drop = {_key(p) for p in (issue.payload or [])}
            user = _user_path(mgr)
            if issue.fix == "drop_dead":
                cleaned = [p for p in user if _key(p) not in drop]
            else:
                seen, cleaned = set(), []
                for p in user:
                    n = _key(p)
                    if n in seen:
                        continue
                    seen.add(n)
                    cleaned.append(p)
            if len(cleaned) == len(user):
                return False, "没有需要清理的条目"
            if hasattr(mgr.backend, "set_path"):
                mgr.backend.set_path(cleaned)
                return True, f"已清理 {len(user) - len(cleaned)} 条 PATH 条目"
            return False, "当前平台不支持直接修改 PATH"

        return False, f"暂不支持自动修复：{issue.kind}"
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def verify_issue(mgr, issue: Issue) -> Optional[Issue]:
    """修复后复核：返回仍然存在的同类问题；已经解决则返回 None。

    比对规则：同 kind + 同工具，且**涉及路径有交集**——这样"只清掉了一半条目"
    也能被发现，而不是被当成修好了。
    """
    fresh = audit(mgr, include_info=True, include_ignored=True)
    want = {_key(p) for p in (issue.items or []) if p}
    for i in fresh:
        if i.kind != issue.kind or i.tool_id != issue.tool_id:
            continue
        if not want:
            return i
        if want & {_key(p) for p in (i.items or []) if p}:
            return i
    return None


def has_conflicts(mgr) -> bool:
    """快速判断：是否存在会让切换失效的冲突（用于主界面提示条）。"""
    if not (IS_WIN and hasattr(mgr.backend, "system_conflicts")):
        return False
    for tool in mgr.tools():
        try:
            if mgr.backend.system_conflicts(tool.conflict_keywords, tool.managed_vars()):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False
