"""配置持久化。

配置文件默认位于 ``~/.envswitch/config.json``。
也支持两种覆盖方式（方便做成绿色版 / 便于测试）：

    1. 设置环境变量 ``ENVSWITCH_HOME`` 指向任意目录
    2. 程序所在目录下存在 ``config.json``（便携模式）
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import Install, ToolDef, norm_path

CONFIG_VERSION = 2

DEFAULT_SETTINGS = {
    "theme": "light",           # light | dark
    "confirm_switch": True,     # 切换前弹确认框
    "show_log": True,           # 显示底部日志面板
    "launch_terminal": False,   # 切换后自动开一个已生效的终端
    "last_tool": "java",
}


def config_dir() -> Path:
    env = os.environ.get("ENVSWITCH_HOME")
    if env:
        return Path(env).expanduser()
    # 便携模式：可执行文件同目录下有 config.json
    try:
        if getattr(sys, "frozen", False):
            here = Path(sys.executable).resolve().parent
        else:
            here = Path(__file__).resolve().parents[2]
        if (here / "config.json").exists():
            return here
    except Exception:  # noqa: BLE001
        pass
    return Path.home() / ".envswitch"


class Config:
    """读写 ~/.envswitch/config.json。"""

    def __init__(self, path: Optional[Path] = None):
        self.dir = Path(path) if path else config_dir()
        self.path = self.dir / "config.json"
        self.data: Dict[str, Any] = self._defaults()
        self.load()

    # ----------------------------- 基础读写 ----------------------------- #

    @staticmethod
    def _defaults() -> Dict[str, Any]:
        return {
            "version": CONFIG_VERSION,
            "tools": [],          # 用户自定义工具定义（ToolDef dict）
            "disabled": [],       # 被禁用的内置工具 id
            "custom_paths": {},   # tool_id -> [路径]
            "applied": {},        # tool_id -> 上一次切换的记录（用于还原）
            "audit_ignored": [],  # 体检忽略的问题 key（Issue.ignore_key()）
            # 见过的安装就记下来：某版本一旦离开 PATH（历史版本删过、或用户手改 PATH），
            # 光靠 PATH 反查就永远找不回来了 —— 用户看到的就是"切完之后少了一个选项"。
            # tool_id -> [install dict]，每轮扫描都用磁盘现状复核，真卸载了才丢弃。
            "known_installs": {},
            # 从已知安装学来的扫描根（通常是各版本的父目录）：浅扫一层就能把
            # 同目录下的兄弟版本（jdk-17 旁边的 jdk1.8.0_202）一并找回。
            "learned_roots": {},
            "scan_seeded": False,  # 是否已从旧的切换记录里补种过 known_installs
            # 用户手动"忘记"的安装（norm_path 后的 home）：记忆里永不复活。
            # 用来兜住"扫描给了一条其实是别的软件自带运行时的假条目"这类情况。
            "forgotten_installs": {},
            "settings": dict(DEFAULT_SETTINGS),
        }

    def load(self) -> None:
        try:
            if self.path.exists():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                for k, v in self._defaults().items():
                    raw.setdefault(k, v)
                raw["settings"] = {**DEFAULT_SETTINGS, **(raw.get("settings") or {})}
                self.data = raw
        except Exception:  # noqa: BLE001
            self.data = self._defaults()
        self._migrate_v1()

    def save(self) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(tmp, self.path)
        except Exception:  # noqa: BLE001
            # 退路：写临时目录，至少不崩
            try:
                fallback = Path(tempfile.gettempdir()) / "envswitch-config.json"
                fallback.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass

    def _migrate_v1(self) -> None:
        """把 v1 的 ~/.envswitch.json（{"java": [...], "python": [...]}）迁进来。"""
        old = Path.home() / ".envswitch.json"
        if not old.exists():
            return
        try:
            legacy = json.loads(old.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return
        if not isinstance(legacy, dict):
            return
        cp = self.data.setdefault("custom_paths", {})
        changed = False
        for kind in ("java", "python"):
            for p in legacy.get(kind, []) or []:
                cp.setdefault(kind, [])
                if p not in cp[kind]:
                    cp[kind].append(p)
                    changed = True
        if changed:
            self.save()

    # ------------------------------ 设置项 ------------------------------ #

    def setting(self, key: str, default: Any = None) -> Any:
        return self.data.get("settings", {}).get(key, DEFAULT_SETTINGS.get(key, default))

    def set_setting(self, key: str, value: Any) -> None:
        self.data.setdefault("settings", {})[key] = value
        self.save()

    # --------------------------- 自定义工具定义 --------------------------- #

    def custom_tools(self) -> List[ToolDef]:
        out: List[ToolDef] = []
        for d in self.data.get("tools", []) or []:
            try:
                t = ToolDef.from_dict(d)
                t.builtin = False
                out.append(t)
            except Exception:  # noqa: BLE001
                continue
        return out

    def add_tool(self, tool: ToolDef) -> None:
        tools = self.data.setdefault("tools", [])
        for i, d in enumerate(tools):
            if d.get("id") == tool.id:
                tools[i] = tool.to_dict()
                self.save()
                return
        tools.append(tool.to_dict())
        self.save()

    def remove_tool(self, tool_id: str) -> None:
        self.data["tools"] = [d for d in self.data.get("tools", []) if d.get("id") != tool_id]
        self.data.setdefault("custom_paths", {}).pop(tool_id, None)
        self.data.setdefault("applied", {}).pop(tool_id, None)
        self.save()

    def is_disabled(self, tool_id: str) -> bool:
        return tool_id in (self.data.get("disabled") or [])

    def set_disabled(self, tool_id: str, disabled: bool) -> None:
        arr = set(self.data.get("disabled") or [])
        arr.add(tool_id) if disabled else arr.discard(tool_id)
        self.data["disabled"] = sorted(arr)
        self.save()

    # ------------------------------ 自定义路径 ------------------------------ #

    def custom_paths(self, tool_id: str) -> List[str]:
        return list((self.data.get("custom_paths") or {}).get(tool_id, []))

    def add_path(self, tool_id: str, path: str) -> None:
        cp = self.data.setdefault("custom_paths", {})
        arr = cp.setdefault(tool_id, [])
        if path not in arr:
            arr.append(path)
            self.save()

    def remove_path(self, tool_id: str, path: str) -> None:
        cp = self.data.setdefault("custom_paths", {})
        if path in cp.get(tool_id, []):
            cp[tool_id].remove(path)
            self.save()

    # --------------------------- 切换记录（还原用） --------------------------- #

    def applied(self, tool_id: str) -> Optional[dict]:
        return (self.data.get("applied") or {}).get(tool_id)

    def set_applied(self, tool_id: str, record: Optional[dict]) -> None:
        ap = self.data.setdefault("applied", {})
        if record is None:
            ap.pop(tool_id, None)
        else:
            ap[tool_id] = record
        self.save()

    # --------------------------- 安装记忆（永不丢版本） --------------------------- #

    def known_installs(self, tool_id: str) -> List[dict]:
        return list((self.data.get("known_installs") or {}).get(tool_id, []))

    def set_known_installs(self, tool_id: str, installs: List[dict]) -> None:
        self.data.setdefault("known_installs", {})[tool_id] = list(installs)

    def learned_roots(self, tool_id: str) -> List[str]:
        return list((self.data.get("learned_roots") or {}).get(tool_id, []))

    def remember(self, tool_id: str, installs: List[dict],
                 roots: Optional[List[str]] = None) -> None:
        """记住该工具"见过的安装"与"学到的扫描根"；有变化才落盘（避免每轮扫描都写文件）。

        ``installs`` 存的是**本轮扫描的全部结果**，``roots`` 是本轮重新算出来的扫描根，
        两者都是**整体替换**而不是追加：真正被卸载的版本、不再成立的扫描根会自然消失，
        不会越积越多。（``installs`` 里已经合并过记忆，所以结果集是稳定的。）
        """
        ki = self.data.setdefault("known_installs", {})
        lr = self.data.setdefault("learned_roots", {})
        changed = False
        if ki.get(tool_id) != installs:
            ki[tool_id] = list(installs)
            changed = True
        new_roots: List[str] = []
        for r in (roots or []):
            if r and r not in new_roots:
                new_roots.append(r)
        if list(lr.get(tool_id, [])) != new_roots:
            lr[tool_id] = new_roots
            changed = True
        if changed:
            self.save()

    def scan_seeded(self) -> bool:
        return bool(self.data.get("scan_seeded"))

    def set_scan_seeded(self, done: bool = True) -> None:
        self.data["scan_seeded"] = bool(done)
        self.save()

    def forgotten_installs(self, tool_id: str) -> List[str]:
        return list((self.data.get("forgotten_installs") or {}).get(tool_id, []))

    def forget_install(self, tool_id: str, home: str) -> None:
        """让某条"记错了的安装"从**记忆**里永久消失（下次扫描不再靠记忆复活）。

        只压制记忆来源：如果扫描器仍然能从 PATH / 扫描根**实际找到**它（说明它确实是
        一个真实存在的安装），还是会照常列出来 —— 藏起来只会让用户更糊涂。
        """
        fi = self.data.setdefault("forgotten_installs", {})
        arr = list(fi.get(tool_id, []))
        key = norm_path(home)
        if key not in arr:
            arr.append(key)
            fi[tool_id] = arr
            # 顺手把记忆里的同一条摘掉，界面立刻干净
            ki = self.data.setdefault("known_installs", {})
            ki[tool_id] = [d for d in (ki.get(tool_id) or [])
                           if norm_path(d.get("home", "")) != key]
            self.save()

    def unforget_installs(self, tool_id: str) -> None:
        (self.data.setdefault("forgotten_installs", {})).pop(tool_id, None)
        self.save()

    # --------------------------- 体检忽略列表 --------------------------- #

    def audit_ignored(self) -> List[str]:
        return list(self.data.get("audit_ignored") or [])

    def ignore_issue(self, key: str) -> None:
        arr = set(self.data.get("audit_ignored") or [])
        arr.add(key)
        self.data["audit_ignored"] = sorted(arr)
        self.save()

    def unignore_issue(self, key: str) -> None:
        arr = self.data.get("audit_ignored") or []
        if key == "*":
            self.data["audit_ignored"] = []
        elif key in arr:
            arr = [k for k in arr if k != key]
            self.data["audit_ignored"] = arr
        else:
            return
        self.save()

    # ------------------------------ 导入导出 ------------------------------ #

    def export_to(self, path: str) -> None:
        Path(path).write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def import_from(self, path: str) -> None:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        for k, v in self._defaults().items():
            raw.setdefault(k, v)
        self.data = raw
        self.save()

    def install_from_dict(self, d: Optional[dict]) -> Optional[Install]:
        if not d:
            return None
        try:
            return Install.from_dict(d)
        except Exception:  # noqa: BLE001
            return None
