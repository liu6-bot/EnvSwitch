"""EnvSwitch 核心数据模型。

设计目标：把「Java / Python / Node / 任意你想切的命令行工具」抽象成统一的结构。

    ToolDef  —— 一类工具的**定义**（怎么找到它、怎么读版本、切换时要写哪些环境变量）
    Install  —— 一类工具在本机上的**一个具体安装实例**（路径 + 版本 + 厂商）

只要能写出一个 ToolDef，就能被本工具管理；内置工具和用户自定义工具走完全相同的代码路径。
"""

from __future__ import annotations

import os
import platform
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# 平台常量
# --------------------------------------------------------------------------- #

_SYSTEM = platform.system()
SYSTEM: str = {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}.get(_SYSTEM, _SYSTEM.lower())
IS_WIN = SYSTEM == "windows"
IS_MAC = SYSTEM == "macos"
IS_LINUX = SYSTEM == "linux"

#: Windows 下可执行文件后缀，其它平台为空字符串
EXT = ".exe" if IS_WIN else ""


def render(tpl: str, **kw) -> str:
    """渲染模板字符串。

    可用占位符：
        {ext}   —— ".exe"（Windows）或 ""（其它平台）
        {home}  —— 安装主目录
        {bin}   —— 主程序可执行文件的完整路径
        {dir}   —— 同 {home}，语义更直观的别名
        {sep}   —— 路径分隔符
    """
    kw.setdefault("ext", EXT)
    kw.setdefault("sep", os.sep)
    kw.setdefault("dir", kw.get("home", ""))
    try:
        return tpl.format(**kw)
    except (KeyError, IndexError, ValueError):
        return tpl


# --------------------------------------------------------------------------- #
# 工具定义
# --------------------------------------------------------------------------- #


@dataclass
class ToolDef:
    """一类可切换工具的定义。"""

    id: str                                   # 唯一标识，如 "java" / "python"
    name: str                                 # 显示名，如 "Java (JDK)"
    icon: str = "◆"                           # 侧边栏图标（单个字符/emoji）
    entry: str = "dir"                        # "dir"：主目录模式；"file"：可执行文件模式
    home_var: str = ""                        # 切换时写入的主目录变量，如 JAVA_HOME
    extra_vars: Dict[str, str] = field(default_factory=dict)  # 附加环境变量，值支持模板
    detect: str = ""                          # dir 模式：判定文件（相对主目录），如 "bin/java{ext}"
    file_pattern: str = ""                    # file 模式：主程序文件名正则，如 r"python[\d.]*(\.exe)?"
    bin_names: List[str] = field(default_factory=list)        # PATH 反查用的命令名
    version_cmd: List[str] = field(default_factory=list)      # 版本命令，通常用 ["{bin}", "--version"]
    version_regex: str = ""                   # 从命令输出里提取版本的正则（取第 1 分组）
    path_entries: List[str] = field(default_factory=list)     # 需要前置到 PATH 的目录模板
    conflict_keywords: List[str] = field(default_factory=list)  # 切换时从 PATH 清理的冲突关键词
    project_file: str = ""                    # 项目级版本文件名，如 ".java-version"
    project_value: str = "full"               # 写入项目文件的内容：full / major / path
    search_roots: Dict[str, List[str]] = field(default_factory=dict)  # 平台 -> 扫描根目录列表
    builtin: bool = False                     # 是否为内置工具（内置不可删除，只能禁用）
    enabled: bool = True
    notes: str = ""

    # ------------------------- 平台相关取值 ------------------------- #

    def roots(self) -> List[str]:
        """当前平台下的扫描根目录（已展开 ~ 与环境变量）。"""
        out: List[str] = []
        for raw in self.search_roots.get(SYSTEM, []) + self.search_roots.get("*", []):
            try:
                p = os.path.expanduser(os.path.expandvars(raw))
            except Exception:  # noqa: BLE001
                continue
            out.append(p)
        return out

    def detect_rel(self) -> str:
        """判定文件的相对路径（已替换 {ext}）。"""
        return render(self.detect) if self.detect else ""

    def bin_for(self, home: str, exe: str = "") -> str:
        """主程序可执行文件路径。"""
        if self.entry == "file":
            return exe or ""
        rel = self.detect_rel()
        return os.path.join(home, rel) if rel else home

    def version_command(self, home: str, exe: str = "") -> List[str]:
        return [render(c, home=home, bin=self.bin_for(home, exe)) for c in self.version_cmd]

    def version_command_bin(self, bin_path: str) -> List[str]:
        """直接对某个可执行文件探测版本（PATH 反查到的命令、shim 转发器等场景）。"""
        return [render(c, bin=bin_path) for c in self.version_cmd]

    def path_entries_for(self, home: str, exe: str = "") -> List[str]:
        return [render(t, home=home, bin=exe, exe=exe) for t in self.path_entries]

    def extra_vars_for(self, home: str, exe: str = "") -> Dict[str, str]:
        return {k: render(v, home=home, bin=self.bin_for(home, exe)) for k, v in self.extra_vars.items()}

    def managed_vars(self) -> List[str]:
        """本工具会写入的环境变量名（用于判断 PATH 条目是否"跟着我们变"）。"""
        names = []
        if self.home_var:
            names.append(self.home_var)
        names += [k for k in self.extra_vars if k not in names]
        return names

    def file_regex(self) -> Optional["re.Pattern[str]"]:
        if self.entry != "file" or not self.file_pattern:
            return None
        try:
            return re.compile(self.file_pattern, re.IGNORECASE)
        except re.error:
            return None

    def is_valid_home(self, home: str, exe: str = "") -> bool:
        """判断某个路径是否是该工具的一个合法安装。"""
        try:
            if self.entry == "dir":
                rel = self.detect_rel()
                return bool(rel) and os.path.isfile(os.path.join(home, rel))
            return bool(exe) and os.path.isfile(exe)
        except Exception:  # noqa: BLE001
            return False

    # ----------------------------- 序列化 ----------------------------- #

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "ToolDef":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        kwargs = {k: v for k, v in data.items() if k in known}
        kwargs.setdefault("id", data.get("id", "custom"))
        kwargs.setdefault("name", data.get("id", "custom"))
        return cls(**kwargs)

    def clone(self) -> "ToolDef":
        return ToolDef.from_dict(self.to_dict())


# --------------------------------------------------------------------------- #
# 安装实例
# --------------------------------------------------------------------------- #


@dataclass
class Install:
    """本机上的一个具体安装。"""

    tool_id: str
    home: str                 # 主目录（dir 模式 = 目录本身；file 模式 = 可执行文件所在目录）
    exe: str = ""             # file 模式下的可执行文件完整路径
    version: str = ""         # 完整版本字符串，如 "17.0.9" / "3.11.4"
    vendor: str = ""          # 来源/厂商标签
    arch: str = ""
    source: str = "auto"      # auto | custom | env | path | store | remembered
    current: bool = False     # 是否是当前**实际生效**的版本（PATH 里命中的那个，全局只标一个）
    is_default: bool = False  # 主目录变量指向它，但未必真生效（界面用弱标记显示，用来暴露"变量说 A、PATH 说 B"）

    @property
    def bin(self) -> str:
        return self.exe or self.home

    def title(self, tool: Optional[ToolDef] = None) -> str:
        base = tool.name if tool else self.tool_id
        return f"{base} {self.version}".strip() if self.version else base

    def to_dict(self) -> dict:
        return {
            "tool_id": self.tool_id,
            "home": self.home,
            "exe": self.exe,
            "version": self.version,
            "vendor": self.vendor,
            "arch": self.arch,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Install":
        return cls(
            tool_id=d.get("tool_id", ""),
            home=d.get("home", ""),
            exe=d.get("exe", ""),
            version=d.get("version", ""),
            vendor=d.get("vendor", ""),
            arch=d.get("arch", ""),
            source=d.get("source", "auto"),
        )


# --------------------------------------------------------------------------- #
# 路径工具
# --------------------------------------------------------------------------- #


def norm_path(p: str) -> str:
    """归一化路径，用于去重与比对。"""
    try:
        return os.path.normcase(os.path.normpath(os.path.abspath(p)))
    except Exception:  # noqa: BLE001
        return p


def expand(p: str) -> str:
    return os.path.expanduser(os.path.expandvars(p))


def norm_key(p: str) -> str:
    """PATH 条目比对用的归一化（保留 %VAR% 原样、不走 abspath、大小写/分隔符无关）。

    用于判断「这条 PATH 条目」是不是我们在谈的那一条。相比 norm_path，
    这里不展开变量、不解析绝对路径——否则 ``%JAVA_HOME%\\bin`` 这种合法引用
    和展开后的真实路径会被当成两条不同的条目，备份/命中比对都会出错。
    """
    if not p:
        return ""
    try:
        s = p.strip().strip('"')
        s = os.path.normcase(s.replace("/", "\\"))
        return s.rstrip("\\")
    except Exception:  # noqa: BLE001
        return p


# --------------------------------------------------------------------------- #
# 环境变量引用（%VAR%）解析
# --------------------------------------------------------------------------- #


def scan_var_refs(text: str) -> Tuple[List[str], bool]:
    """扫描一段文本里的 ``%VAR%`` 引用，返回 (变量名列表, 是否有畸形写法)。

    这里必须**逐字符配对**，不能用 ``"%JAVA_HOME%" in text`` 那种子串判断：
    ``%%JAVA_HOME%\\bin``（多打了一个 %，手改注册表 / setx 时很常见）在 Windows 里
    会展开成字面量 ``%C:\\...\\bin``，是一条**无效路径**；
    但子串匹配会把它当成"引用了 %JAVA_HOME%"，从而把真正的
    「未加入环境变量」问题掩盖掉——体检看着没问题，命令行却找不到这个工具。
    """
    names: List[str] = []
    malformed = False
    i, n = 0, len(text)
    while i < n:
        if text[i] != "%":
            i += 1
            continue
        j = text.find("%", i + 1)
        if j <= i + 1:                      # 紧挨着的 %% ，或落单的 %
            malformed = True
            i += 1
            continue
        name = text[i + 1:j]
        if "%" in name or not name.strip():
            malformed = True
        else:
            names.append(name.strip())
        i = j + 1
    return names, malformed


def _var_defined(name: str) -> bool:
    """该变量当前是否存在（Windows 环境变量名不区分大小写）。"""
    return bool(name) and (name in os.environ or name.upper() in os.environ)


def is_managed_var_ref(text: str, managed_vars) -> bool:
    """文本是否**合法地**引用了某个受管变量（例如 ``%JAVA_HOME%\\bin``）。

    畸形写法（``%%JAVA_HOME%\\bin``）一律不算——那条 PATH 实际命中不了。
    """
    names, malformed = scan_var_refs(text)
    if malformed or not names:
        return False
    low = {x.lower() for x in names}
    return any(v and v.lower() in low for v in (managed_vars or []))


def bad_var_ref_reason(text: str) -> str:
    """这条 PATH 的变量引用有什么毛病（没问题返回空串）。

    两种情况：
      * 百分号没配对（``%%JAVA_HOME%\\bin``）→ Windows 按字面量处理，等于死路径
      * 引用的变量当前不存在（``%SOME_GONE_VAR%\\bin``）→ 展开后同样是死路径
    """
    names, malformed = scan_var_refs(text)
    if malformed and any(_var_defined(n) for n in names):
        return "百分号没有配对（多写或少写了 %），Windows 会把它当成字面量路径"
    if names and not malformed and not any(_var_defined(n) for n in names):
        return "引用的环境变量当前不存在，展开后是无效路径"
    return ""


# --------------------------------------------------------------------------- #
# Microsoft Store 的「应用执行别名」
# --------------------------------------------------------------------------- #

#: 这些目录里的 python.exe / node.exe 只是**跳板**（App Execution Alias）：
#: 它们指向 ``C:\Program Files\WindowsApps\...`` 里的打包应用，普通用户根本读不到，
#: 切过去只会把 PYTHON_HOME 写成这串别名路径，命令行里跑 python 会去弹微软商店。
_STORE_ALIAS_MARKERS = (
    r"microsoft\windowsapps",
    r"program files\windowsapps",
)


def is_store_alias(p: str) -> bool:
    """路径是不是 Microsoft Store 的应用执行别名（或它的打包目录）。"""
    if not p:
        return False
    try:
        low = os.path.normpath(os.path.expandvars(p.strip().strip('"'))).replace("/", "\\").lower()
    except Exception:  # noqa: BLE001
        low = p.replace("/", "\\").lower()
    return any(m in low for m in _STORE_ALIAS_MARKERS)


def store_alias_target(p: str) -> str:
    """商店别名指向的、**真实存在**的可执行文件；不是别名或目标没了就返回 ""。

    「应用执行别名」只是转发器，真身在 ``C:\\Program Files\\WindowsApps\\<包名>\\…``。
    所以要分两种情况看：

      * 别名能打开（目标真实存在）→ 商店版**确实装着**，是一个能跑的真安装；
      * 别名打不开（应用已卸载，只剩一个空壳）→ 跑它只会弹应用商店，绝不能当安装。

    旧版一刀切"凡 WindowsApps 一律不算安装"，于是用户明明装了商店版 Python
    却在列表里看不到，表现成"我的 Python 怎么少了一个"。
    """
    if not is_store_alias(p):
        return ""
    try:
        q = os.path.expandvars(str(p).strip().strip('"'))
        if os.path.isfile(q):
            try:
                return os.path.realpath(q)
            except Exception:  # noqa: BLE001
                return q
    except Exception:  # noqa: BLE001
        pass
    return ""


def is_store_stub(p: str) -> bool:
    """是不是一个"空壳"商店别名（别名在、目标已经没了）。"""
    return is_store_alias(p) and not store_alias_target(p)


def provides_command(entry: str, names) -> bool:
    """PATH 里的这一段**实际**能不能提供某个命令（``names`` 是命令名列表）。

    只认真实存在的可执行文件，Store 别名目录一律不算。
    这条判断是"能不能删掉这段 PATH"的安全闸：没有它，
    「删掉 ``C:\\Python314`` 也没事，WindowsApps 里还有个 python.exe」
    这种判断会把用户机器上真正的 Python 入口抹掉。
    """
    if not entry or not entry.strip():
        return False
    if is_store_alias(entry):
        return False
    try:
        base = os.path.expandvars(entry.strip().strip('"'))
    except Exception:  # noqa: BLE001
        base = entry
    suffixes = (EXT, ".bat", ".cmd") if IS_WIN else ("",)
    for n in names or []:
        for suf in suffixes:
            try:
                if os.path.isfile(os.path.join(base, n + suf)):
                    return True
            except Exception:  # noqa: BLE001
                continue
    return False


def dir_commands(directory: str, limit: int = 6) -> List[str]:
    """目录里有哪几个可执行文件（用来解释"删了会丢什么命令"）。

    ``C:\\Python314\\Scripts`` 里是 pip.exe / wheel.exe，删掉这一段不只是少个目录，
    而是 pip 直接从命令行消失 —— 报错信息里得说清楚，否则用户只会看到"越修越乱"。
    """
    if not directory:
        return []
    try:
        d = os.path.expandvars(directory.strip().strip('"'))
        names = []
        for n in sorted(os.listdir(d)):
            full = os.path.join(d, n)
            if not os.path.isfile(full):
                continue
            stem, ext = os.path.splitext(n)
            if IS_WIN and ext.lower() in (".exe", ".bat", ".cmd", ".com"):
                names.append(n)
            elif not IS_WIN and os.access(full, os.X_OK) and not ext:
                names.append(n)
        return names[:limit]
    except Exception:  # noqa: BLE001
        return []


def safe_name(s: str) -> str:
    """把任意字符串整理成合法的 tool id。"""
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "-", s.strip().lower())
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "custom"


def version_key(v: str) -> tuple:
    """把版本号转成可排序元组：17.0.9 -> (17, 0, 9)。"""
    nums = re.findall(r"\d+", v or "")
    return tuple(int(n) for n in nums[:6]) or (0,)


def major_of(v: str) -> str:
    """取主版本号。1.8.0_362 -> 8；17.0.9 -> 17。"""
    v = (v or "").strip()
    if v.startswith("1."):
        parts = v.split(".")
        return parts[1] if len(parts) > 1 else v
    return v.split(".")[0].split("-")[0].split("+")[0]


def user_home() -> Path:
    return Path.home()
