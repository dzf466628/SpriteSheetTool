"""软件设置：键值读写（QSettings）。

内容：
  - **工程文件是否内嵌缩略图**（软件设置弹窗里的开关）；
  - **上次用过的目录**（打开/保存/导出对话框默认从这里开始，取不到就回退到桌面）。

放在独立模块而不是塞进 UI 里，是因为读设置的地方在 ``_project_manifest()``
（保存工程时），而写设置的入口在设置弹窗 —— 两边都要用同一份键名，
且这里必须能在没有界面的环境（回归脚本）里单独测。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PyQt6.QtCore import QSettings

ORG_NAME = "SpriteSheetTool"
APP_NAME = "SpriteSheetTool"

KEY_SHOW_THUMBNAIL = "project/show_thumbnail"
KEY_LAST_DIR = "files/last_directory"


def _settings() -> QSettings:
    return QSettings(ORG_NAME, APP_NAME)


def show_project_thumbnail() -> bool:
    """工程文件是否内嵌缩略图。默认 True。"""
    try:
        value = _settings().value(KEY_SHOW_THUMBNAIL, True)
    except Exception as e:  # noqa: BLE001 - 读不到就按默认值走，绝不因为设置崩保存
        print(f"[settings] 读取 {KEY_SHOW_THUMBNAIL} 失败: {e}", file=sys.stderr)
        return True
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "no", "")
    return bool(value)


def set_show_project_thumbnail(enabled: bool) -> None:
    try:
        _settings().setValue(KEY_SHOW_THUMBNAIL, bool(enabled))
    except Exception as e:  # noqa: BLE001
        print(f"[settings] 写入 {KEY_SHOW_THUMBNAIL} 失败: {e}", file=sys.stderr)


def desktop_directory() -> str:
    """桌面目录；取不到就退回家目录，再不行返回空串。"""
    for candidate in (Path.home() / "Desktop", Path.home() / "桌面"):
        if candidate.is_dir():
            return str(candidate)
    home = Path.home()
    return str(home) if home.is_dir() else ""


def last_directory() -> str:
    """上次用过的目录。没记录过、目录已被删/改名时都回退到桌面。"""
    try:
        value = str(_settings().value(KEY_LAST_DIR, "") or "")
    except Exception as e:  # noqa: BLE001
        print(f"[settings] 读取 {KEY_LAST_DIR} 失败: {e}", file=sys.stderr)
        value = ""
    if value and os.path.isdir(value):
        return value
    return desktop_directory()


def set_last_directory(path: str | os.PathLike | None) -> None:
    """记住一个目录（传文件路径也可以，会取它所在的目录）。"""
    if not path:
        return
    try:
        candidate = Path(path)
        target = candidate if candidate.is_dir() else candidate.parent
        if target.is_dir():
            _settings().setValue(KEY_LAST_DIR, str(target))
    except Exception as e:  # noqa: BLE001
        print(f"[settings] 写入 {KEY_LAST_DIR} 失败: {e}", file=sys.stderr)
