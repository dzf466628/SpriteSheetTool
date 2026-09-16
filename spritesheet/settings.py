"""软件设置：键值读写（QSettings）。

目前只有一项：**工程文件是否内嵌缩略图**。
  - 开（默认）：保存的 .sstool 里带一张 256×256 的缩略图，Windows 资源管理器
    直接显示它（需要安装包注册的缩略图处理器）；
  - 关：.sstool 里不写缩略图区段，资源管理器回退到文件类型图标（SSTool.ico）。

放在独立模块而不是塞进 UI 里，是因为读设置的地方在 ``_project_manifest()``
（保存工程时），而写设置的入口在设置弹窗 —— 两边都要用同一份键名，
且这里必须能在没有界面的环境（回归脚本）里单独测。
"""
from __future__ import annotations

from PyQt6.QtCore import QSettings

ORG_NAME = "SpriteSheetTool"
APP_NAME = "SpriteSheetTool"

KEY_SHOW_THUMBNAIL = "project/show_thumbnail"


def _settings() -> QSettings:
    return QSettings(ORG_NAME, APP_NAME)


def show_project_thumbnail() -> bool:
    """工程文件是否内嵌缩略图。默认 True。"""
    try:
        value = _settings().value(KEY_SHOW_THUMBNAIL, True)
    except Exception:  # noqa: BLE001 - 读不到就按默认值走，绝不因为设置崩保存
        return True
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "no", "")
    return bool(value)


def set_show_project_thumbnail(enabled: bool) -> None:
    try:
        _settings().setValue(KEY_SHOW_THUMBNAIL, bool(enabled))
    except Exception:  # noqa: BLE001
        pass
