# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置。
#
#   默认打单文件：  pyinstaller EnvSwitch.spec
#   打目录版：      ENVSWITCH_ONEDIR=1 pyinstaller EnvSwitch.spec
#
# 目录版启动更快、杀软误报更少；单文件版只有一个 exe，最好分发。

import os

ONEFILE = os.environ.get("ENVSWITCH_ONEDIR", "0") != "1"

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[("assets", "assets")],
    # audit / elevate 是在函数里延迟导入的，显式声明确保被打进包里
    hiddenimports=["envswitch.core.audit", "envswitch.core.elevate"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "setuptools", "pip", "wheel", "unittest", "xml", "email"],
    noarchive=False,
)

pyz = PYZ(a.pure)

if ONEFILE:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="EnvSwitch",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        icon="assets/icon.ico",
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="EnvSwitch",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        icon="assets/icon.ico",
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name="EnvSwitch",
    )
