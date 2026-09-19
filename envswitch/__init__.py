"""EnvSwitch —— 一键切换本机多版本运行环境（Java / Python / Node / Go / 任意自定义工具）。

零第三方依赖，只用 Python 标准库 + tkinter。
"""

__version__ = "2.2.3"
__author__ = "EnvSwitch contributors"
__license__ = "MIT"

from .core.manager import EnvManager  # noqa: F401

__all__ = ["EnvManager", "__version__"]
