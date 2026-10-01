# -*- mode: python ; coding: utf-8 -*-

"""PyInstaller onedir 配置。

安装包由 Inno Setup 递归收集 dist/SpriteSheetTool 目录，因此运行时不需要
单文件 EXE 的临时解压目录，启动和升级都更稳定。
"""

from pathlib import Path
import PyQt6

from PyInstaller.building.datastruct import TOC
from PyInstaller.utils.hooks import collect_all, collect_data_files


project_root = Path(SPEC).parent
datas = [
    (str(project_root / "icons"), "icons"),
    (str(project_root / "SpriteSheetTool.ico"), "."),
    (str(project_root / "SSTool.ico"), "."),
    (str(project_root / "工程图标.png"), "."),
]
datas += collect_data_files("PIL")

# ---- MCP server（--mcp）依赖链：mcp SDK 及其运行时期库必须全量收集 ----
# mcp 是动态 import 大户（pydantic/anyio/httpx/starlette/sse_starlette 等，
# 子模块在 import 时按需加载），PyInstaller 静态分析抓不全，漏一个打包后
# `--mcp` 一握手就崩。逐个 collect_all，把 datas/binaries/hiddenimports
# 全带进来；体积多几十 MB，换取打包后可用。
_mcp_datas: list = []
_mcp_bins: list = []
_mcp_hid: list = []
for _pkg in ("mcp", "pydantic", "anyio", "httpx",
             "starlette", "sse_starlette", "uvicorn"):
    try:
        _d, _b, _h = collect_all(_pkg)
        _mcp_datas += _d
        _mcp_bins += _b
        _mcp_hid += _h
    except Exception:  # noqa: BLE001 - 缺包不打断打包（mcp 本体装了就够）
        print(f"[spec] 未收集到可选依赖：{_pkg}")
# jsonschema loads its metaschema files through importlib.resources at runtime;
# collect_all(mcp) does not pull this separately installed package's data files.
try:
    _js_spec_datas, _js_spec_bins, _js_spec_hid = collect_all("jsonschema_specifications")
    _mcp_datas += _js_spec_datas
    _mcp_bins += _js_spec_bins
    _mcp_hid += _js_spec_hid
    _js_spec_root = Path(__import__("jsonschema_specifications").__file__).resolve().parent
    _mcp_datas.append((str(_js_spec_root / "schemas"), "jsonschema_specifications/schemas"))
except Exception:  # noqa: BLE001
    print("[spec] 未收集到 jsonschema_specifications 数据")
datas += _mcp_datas
# PyQt6's hooks collect the Qt modules that are imported directly, but a few
# transitive runtime DLLs (d3dcompiler/opengl and the MSVC side-by-side
# libraries) are loaded by QtGui only after the frozen process starts.  Keep
# the complete Qt bin directory beside the Qt DLLs so both the desktop app and
# the ``--mcp`` entry point can import PyQt6 reliably on a clean machine.
_qt_bin = Path(PyQt6.__file__).resolve().parent / "Qt6" / "bin"
_qt_runtime_bins = [(str(path), "PyQt6/Qt6/bin")
                    for path in _qt_bin.glob("*.dll")]
binaries = _mcp_bins + _qt_runtime_bins
hiddenimports = [
    "PIL._tkinter_finder",
    "numpy",
] + _mcp_hid


a = Analysis(
    [str(project_root / "main.py")],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(project_root / "pyi_rth_qt6.py")],
    excludes=["tkinter"],
    noarchive=False,
)
# A transitive Poppler dependency can contribute ICU 78 DLLs.  They shadow
# Windows' ICU API-set stubs and make Qt6Core fail with WinError 127 on clean
# Windows installs.  SpriteSheetTool does not load Poppler at runtime, so do
# not ship those conflicting DLLs in the frozen application.
a.binaries = TOC([
    entry for entry in a.binaries
    if Path(entry[1]).name.lower() not in {"icuuc.dll", "icudt78.dll"}
])

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
