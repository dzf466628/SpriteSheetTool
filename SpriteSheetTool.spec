# -*- mode: python ; coding: utf-8 -*-

"""PyInstaller onedir 配置。

安装包由 Inno Setup 递归收集 dist/SpriteSheetTool 目录，因此运行时不需要
单文件 EXE 的临时解压目录，启动和升级都更稳定。
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files


project_root = Path(SPEC).parent
datas = [
    (str(project_root / "icons"), "icons"),
    (str(project_root / "SpriteSheetTool.ico"), "."),
    (str(project_root / "SSTool.ico"), "."),
    (str(project_root / "工程图标.png"), "."),
]
datas += collect_data_files("PIL")


a = Analysis(
    [str(project_root / "main.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=["PIL._tkinter_finder"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SpriteSheetTool",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(project_root / "SpriteSheetTool.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="SpriteSheetTool",
)
