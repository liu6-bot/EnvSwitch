"""安装扫描器。

完全由 ToolDef 驱动，不针对任何具体工具写死逻辑：

    dir  模式：在扫描根目录下找"包含某个判定文件"的目录（如 bin/java）
    file 模式：在扫描根目录下找"文件名匹配某个正则"的可执行文件（如 python3.11.exe）

再加上三条补充来源：PATH 反查、当前主目录环境变量、用户手动添加的路径。
"""

from __future__ import annotations

import glob
import os
import re
import platform
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .models import (
    IS_WIN,
    Install,
    ToolDef,
    expand,
    is_store_alias,
    major_of,
    norm_path,
    store_alias_target,
    version_key,
)

# 路径关键词 -> 来源标签
VENDOR_TAGS = [
    (("pyenv",), "pyenv"),
    (("conda", "miniforge", "anaconda", "mamba"), "Conda"),
    (("scoop",), "Scoop"),
    (("nvm", "fnm", "volta"), "nvm"),
    (("sdkman",), "SDKMAN"),
    (("asdf",), "asdf"),
    ((".jdks", "jetbrains"), "JetBrains"),
    (("homebrew", "/opt/homebrew", "cellar"), "Homebrew"),
    (("adoptium", "temurin"), "Eclipse Temurin"),
    (("corretto",), "Amazon Corretto"),
    (("zulu",), "Azul Zulu"),
    (("graalvm",), "GraalVM"),
    (("bellsoft", "liberica"), "BellSoft"),
    (("microsoft",), "Microsoft"),
    (("android studio", "jbr"), "Android Studio"),
    (("eclipse",), "Eclipse"),
    (("oracle",), "Oracle"),
    (("/usr/lib/jvm", "/usr/java", "/library/java"), "系统 JDK"),
    (("program files",), "官方安装包"),
    (("/usr/bin", "/usr/local/bin", "program files"), "系统路径"),
]

DEFAULT_CMD_TIMEOUT = 6


#: Windows：创建子进程时不显示控制台窗口（否则每次探测版本都会闪一个黑框）
CREATE_NO_WINDOW = 0x08000000


def _silent_kwargs() -> dict:
    """Windows 下让子进程静默运行，避免一闪而过的控制台窗口。"""
    if not IS_WIN:
        return {}
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        return {"startupinfo": si, "creationflags": CREATE_NO_WINDOW}
    except Exception:  # noqa: BLE001
        return {}


def run(cmd, timeout: int = DEFAULT_CMD_TIMEOUT, env: Optional[dict] = None) -> Tuple[int, str]:
    """安静地执行一条命令，返回 (返回码, 输出)。任何异常都不抛。

    注意：java -version / python --version 这类命令在 Windows 上会拉起控制台窗口，
    扫描十几条安装就会闪十几下，所以这里统一隐藏窗口。
    env 不为空时用它作为子进程环境（用来喂一份"干净的 PATH"，见 _lookup_env）。
    """
    try:
        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            shell=isinstance(cmd, str),
            env=env,
            **_silent_kwargs(),
        )
        out = p.stdout.decode("utf-8", "replace") if isinstance(p.stdout, bytes) else (p.stdout or "")
        return p.returncode, (out or "").strip()
    except Exception as e:  # noqa: BLE001
        return -1, str(e)


class Scanner:
    """按 ToolDef 扫描本机安装。"""

    def __init__(self, log=print, timeout: int = DEFAULT_CMD_TIMEOUT, reference_path=None):
        self.log = log
        self.timeout = timeout
        self.reference_path = reference_path   # -> List[str]：查 where/which 时用的 PATH
        self._vcache: Dict[str, str] = {}     # exe/home -> version，避免重复 fork

    # ------------------------------ 入口 ------------------------------ #

    def lookup_path(self) -> List[str]:
        """查命令用的 PATH（已展开 %VAR%）。

        不要直接用本进程继承来的 PATH：EnvSwitch 很可能是从某个**被改过 PATH
        的终端**里启动的（VS Code / Git Bash / 各种工具链都会改），
        那样 ``where python`` 看到的是一份残缺的 PATH，扫描结果会莫名其妙
        "少了好几个安装"甚至"一个都没有"。Windows 里真正生效的永远是
        「系统 PATH + 用户 PATH」，所以这里显式用注册表里那份去查。
        """
        if not self.reference_path:
            return [p for p in os.environ.get("PATH", "").split(os.pathsep) if p.strip()]
        try:
            items = [p for p in self.reference_path() if p and p.strip()]
        except Exception:  # noqa: BLE001
            items = []
        if not items:
            items = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p.strip()]
        out: List[str] = []
        for p in items:
            try:
                out.append(os.path.expandvars(p.strip().strip('"')))
            except Exception:  # noqa: BLE001
                out.append(p)
        return out

    def _lookup_env(self) -> Optional[dict]:
        env = dict(os.environ)
        env["PATH"] = os.pathsep.join(self.lookup_path())
        return env

    def scan(self, tool: ToolDef, custom_paths: Optional[List[str]] = None,
             max_depth: int = 2, extra_roots: Optional[List[str]] = None) -> List[Install]:
        candidates: List[Tuple[str, str, str]] = []   # (home, exe, source)

        # 1) 环境变量里已配置的主目录
        for var in [tool.home_var] + list(tool.extra_vars.keys()):
            v = os.environ.get(var)
            if v and os.path.isdir(v):
                candidates.append((v, tool.bin_for(v), "env"))

        # 2) PATH 反查
        for exe in self._from_path(tool):
            home = self._home_from_exe(tool, exe)
            if home:
                candidates.append((home, exe if tool.entry == "file" else "", "path"))

        # 3) 扫描根目录
        for home, exe in self._from_roots(tool, max_depth):
            candidates.append((home, exe, "auto"))

        # 3b) 「学到的」扫描根：以前见过该工具的某个版本 → 记住它的父目录。
        #     浅扫一层就能把同一目录下的兄弟版本一起找回来
        #     （D:\soft\Java\jdk-17 旁边的 jdk1.8.0_202、C:\Python\3.11 旁边的 3.12）。
        #     这是"某版本离开 PATH 就再也扫不到"的第二道保险。
        for root in (extra_roots or []):
            try:
                if not os.path.isdir(root):
                    continue
                for home, exe in self._scan_root(tool, root, 1):
                    candidates.append((home, exe, "auto"))
            except Exception:  # noqa: BLE001
                continue

        # 4) 用户手动添加
        for p in custom_paths or []:
            home, exe = self._interpret(tool, p)
            if home:
                candidates.append((home, exe, "custom"))

        # 去重 + 校验
        seen, uniq = set(), []
        for home, exe, source in candidates:
            try:
                home = str(Path(home).resolve())
                exe = str(Path(exe).resolve()) if exe else ""
            except Exception:  # noqa: BLE001
                continue
            # 微软商店的「应用执行别名」要分两种情况看（见 _live）：
            #   * 别名指向真实存在的可执行文件 → 商店版确实装着，是一个能跑的安装，
            #     来源标成 store（切换时只把那个别名目录排到前面，不写主目录变量）；
            #   * 别名是个空壳（应用已卸载）→ 跑它只会弹应用商店，绝不纳入。
            probe = exe if (tool.entry == "file" and exe) else home
            if not self._live(probe):
                continue
            if is_store_alias(probe):
                source = "store"
            if not tool.is_valid_home(home, exe if tool.entry == "file" else ""):
                continue
            key = norm_path(home) + "|" + norm_path(exe)
            if key in seen:
                continue
            seen.add(key)
            # 同一个 home 若既被扫描到又是用户手动添加的，保留 custom
            dup = next((x for x in uniq if x[0] == home), None)
            if dup:
                if source == "custom" and dup[2] != "custom":
                    uniq.remove(dup)
                else:
                    continue
            uniq.append((home, exe, source))

        # 并发探测版本
        with ThreadPoolExecutor(max_workers=min(8, max(1, len(uniq)))) as pool:
            versions = list(pool.map(lambda t: self._version(tool, t[0], t[1]), uniq))

        installs = []
        for (home, exe, source), version in zip(uniq, versions):
            installs.append(Install(
                tool_id=tool.id,
                home=home,
                exe=exe,
                version=version,
                vendor=self._vendor(tool, home),
                arch=self._arch(tool, home),
                source=source,
            ))

        installs.sort(key=lambda i: version_key(i.version), reverse=True)
        return installs

    # --------------------------- 来源收集 --------------------------- #

    def _expand_roots(self, tool: ToolDef) -> List[str]:
        out: List[str] = []
        for root in tool.roots():
            if "*" in root or "?" in root:
                try:
                    out += [g for g in glob.glob(root) if os.path.isdir(g)]
                except Exception:  # noqa: BLE001
                    pass
            out.append(root)
        seen, uniq = set(), []
        for r in out:
            n = norm_path(r)
            if os.path.isdir(r) and n not in seen:
                seen.add(n)
                uniq.append(r)
        return uniq

    def _from_roots(self, tool: ToolDef, max_depth: int) -> List[Tuple[str, str]]:
        found: List[Tuple[str, str]] = []
        for root in self._expand_roots(tool):
            found += self._scan_root(tool, root, max_depth)
        return found

    def _scan_root(self, tool: ToolDef, root: str, max_depth: int) -> List[Tuple[str, str]]:
        out: List[Tuple[str, str]] = []
        # 根目录自己就可能是安装目录
        home, exe = self._interpret(tool, root)
        if home:
            out.append((home, exe))

        stack = [(root, 0)]
        while stack:
            cur, depth = stack.pop()
            try:
                entries = list(os.scandir(cur))
            except Exception:  # noqa: BLE001
                continue
            for e in entries:
                try:
                    if e.is_dir(follow_symlinks=False):
                        # macOS 的 JDK 包结构：xxx.jdk/Contents/Home
                        sub = os.path.join(e.path, "Contents", "Home")
                        if os.path.isdir(sub):
                            h, x = self._interpret(tool, sub)
                            if h:
                                out.append((h, x))
                        h, x = self._interpret(tool, e.path)
                        if h:
                            out.append((h, x))
                        elif depth < max_depth:
                            stack.append((e.path, depth + 1))
                    elif tool.entry == "file" and e.is_file(follow_symlinks=False):
                        rgx = tool.file_regex()
                        if rgx and rgx.fullmatch(e.name):
                            h, x = self._interpret(tool, e.path)
                            if h:
                                out.append((h, x))
                except Exception:  # noqa: BLE001
                    continue
        return out

    def _from_path(self, tool: ToolDef) -> List[str]:
        exes: List[str] = []
        env = self._lookup_env()
        for name in tool.bin_names or []:
            if IS_WIN:
                rc, out = run(["where", name], timeout=self.timeout, env=env)
                paths = [l.strip() for l in out.splitlines() if l.strip()]
            else:
                rc, out = run(["which", "-a", name], timeout=self.timeout, env=env)
                paths = [l.strip() for l in out.splitlines() if l.strip()]
            if rc != 0:
                continue
            for p in paths:
                if IS_WIN and not os.path.isfile(p):
                    continue
                try:
                    exes.append(str(Path(p).resolve()))
                except Exception:  # noqa: BLE001
                    exes.append(p)
        # 路径反查只认"真安装"：空壳商店别名（应用没装）不算；
        # 商店版真装着的别名要算 —— 否则用户装了商店版 Python 却看不见，表现成"少了一个"
        exes = [e for e in exes if self._live(e)]
        # Windows 上 where 会返回 python.exe 之外的 .bat/.cmd，过滤掉
        if tool.entry == "file":
            rgx = tool.file_regex()
            if rgx:
                exes = [e for e in exes if rgx.fullmatch(os.path.basename(e))]
        return exes

    @staticmethod
    def _live(path: str) -> bool:
        """这个路径能不能当成"真实存在的东西"。

        只对商店别名做额外判断：别名指向真实可执行文件（商店版真装着）→ 算；
        别名是空壳（应用已卸载，跑它只会弹商店）→ 不算。其它路径一律算。
        """
        return (not is_store_alias(path)) or bool(store_alias_target(path))

    def store_live(self, tool: ToolDef, home: str, exe: str = "") -> bool:
        """这个商店安装是不是**真装着**（别名指向真实存在的程序）。

        ``exe`` 给了就以它为准；只给目录时要顺着目录再找一次主程序 —
        别名目录自己是个目录，``isfile`` 永远为假，拿它当判据会让
        "手动输入别名目录"和"列表里那一条"得出相反结论（一个被拒、一个可用）。
        """
        if exe:
            if store_alias_target(exe):
                return True
            if is_store_alias(exe):
                return False
        if home and is_store_alias(home):
            return bool(store_alias_target(self.main_exe_in(tool, home)))
        return False

    def _interpret(self, tool: ToolDef, path: str) -> Tuple[str, str]:
        """把任意路径解释成 (home, exe)；不合法返回 ("", "")。"""
        try:
            if not self._live(path):
                return "", ""
            if tool.entry == "file":
                if os.path.isfile(path):
                    rgx = tool.file_regex()
                    if rgx and not rgx.fullmatch(os.path.basename(path)):
                        return "", ""
                    return str(Path(path).resolve().parent), str(Path(path).resolve())
                return "", ""
            if os.path.isdir(path) and tool.is_valid_home(path):
                return str(Path(path).resolve()), ""
            # 传进来的是可执行文件（比如 PATH 反查结果），往上找 home
            if os.path.isfile(path):
                return self._home_from_exe(tool, path), ""
        except Exception:  # noqa: BLE001
            pass
        return "", ""

    def main_exe_in(self, tool: ToolDef, directory: str) -> str:
        """在目录（含 bin / Scripts 子目录）里找该工具的主程序，找不到返回 ""。

        file 模式的工具（Python / Node / .NET）主程序就躺在安装目录里，
        用户手动加路径时经常只给目录，这里补上"目录 -> 主程序"的转换，
        免得报一句"不是有效的可执行文件"就没下文。
        """
        rgx = tool.file_regex()
        if tool.entry != "file" or not rgx or not os.path.isdir(directory):
            return ""
        for base in (directory, os.path.join(directory, "bin"), os.path.join(directory, "Scripts")):
            try:
                names = sorted(os.listdir(base))
            except OSError:
                continue
            for n in names:
                if not rgx.fullmatch(n):
                    continue
                full = os.path.join(base, n)
                if not self._live(full) or not os.path.isfile(full):
                    continue
                return full
        return ""

    def install_root_of(self, tool: ToolDef, path: str, levels: int = 2) -> str:
        """这个路径属于哪个**安装目录**？找不到返回 ""。

        先看它自己是不是安装目录，再往上找一两层。``<Python>`` 和
        ``<Python>\\Scripts`` 必须归到同一组 —— 否则清理 PATH 时会出现
        "留下主目录、删掉 Scripts"这种把一个安装拆一半的情况（pip 直接没了）。
        """
        cands = [str(path).rstrip("\\/")]
        cur = cands[0]
        for _ in range(max(1, levels)):
            cur = os.path.dirname(cur)
            if not cur:
                break
            cands.append(cur)
        for c in cands:
            if not c or is_store_alias(c):
                continue
            try:
                if tool.entry == "dir":
                    if tool.is_valid_home(c):
                        return c
                elif self.main_exe_in(tool, c):
                    return c
            except Exception:  # noqa: BLE001
                continue
        return ""

    def _home_from_exe(self, tool: ToolDef, exe: str) -> str:
        """从可执行文件往上回溯若干层，找到合法的 home。"""
        if not self._live(exe):
            return ""
        cur = Path(exe).parent
        for _ in range(4):
            if tool.is_valid_home(str(cur)):
                return str(cur)
            if cur.parent == cur:
                break
            cur = cur.parent
        # 兜底：可执行文件所在目录
        return str(Path(exe).parent) if tool.entry == "file" else ""

    # --------------------------- 版本与标签 --------------------------- #

    def _version(self, tool: ToolDef, home: str, exe: str) -> str:
        cache_key = f"{tool.id}|{exe or home}"
        if cache_key in self._vcache:
            return self._vcache[cache_key]

        version = ""
        cmd = tool.version_command(home, exe)
        if cmd and os.path.isfile(cmd[0]):
            rc, out = run(cmd, timeout=self.timeout)
            if rc == 0 and out:
                if tool.version_regex:
                    m = re.search(tool.version_regex, out)
                    version = (m.group(1) if m and m.groups() else (m.group(0) if m else "")).strip()
                else:
                    version = out.splitlines()[0].strip()
        if not version or version == "未知":
            version = self._version_from_path(home)
        self._vcache[cache_key] = version
        return version

    @staticmethod
    def _version_from_path(home: str) -> str:
        name = os.path.basename(home.rstrip("\\/"))
        m = re.search(r"(\d+(?:\.\d+){1,3})", name)
        return m.group(1) if m else "未知"

    @staticmethod
    def _vendor(tool: ToolDef, home: str) -> str:
        # 商店安装直接标出来：一眼就能看出"这不是用官方安装包装的"
        if is_store_alias(home):
            return "Microsoft Store"
        # Java 的 release 文件里有厂商信息，优先用它
        rel = os.path.join(home, "release")
        if os.path.isfile(rel):
            try:
                text = Path(rel).read_text(encoding="utf-8", errors="replace")
                kv = dict(
                    (l.split("=", 1)[0].strip(), l.split("=", 1)[1].strip().strip('"'))
                    for l in text.splitlines() if "=" in l
                )
                v = kv.get("IMPLEMENTOR") or kv.get("JAVA_VENDOR") or ""
                v = re.sub(r"(,?\s*Inc\.?| Corporation| Ltd\.?| GmbH)$", "", v).strip()
                if v:
                    return v
            except Exception:  # noqa: BLE001
                pass
        low = home.lower().replace("\\", "/")
        for keys, label in VENDOR_TAGS:
            if any(k in low for k in keys):
                return label
        return "本机安装"

    @staticmethod
    def _arch(tool: ToolDef, home: str) -> str:
        rel = os.path.join(home, "release")
        if os.path.isfile(rel):
            try:
                text = Path(rel).read_text(encoding="utf-8", errors="replace")
                m = re.search(r"^OS_ARCH=\"?([^\"\n]+)\"?", text, re.M)
                if m:
                    return m.group(1).strip()
            except Exception:  # noqa: BLE001
                pass
        return platform.machine()
