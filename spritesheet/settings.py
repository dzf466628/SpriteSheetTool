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
from PyQt6.QtGui import QColor

ORG_NAME = "SpriteSheetTool"
APP_NAME = "SpriteSheetTool"

KEY_SHOW_THUMBNAIL = "project/show_thumbnail"
KEY_LAST_DIR = "files/last_directory"
KEY_DETECT_MIN_AREA = "detect/min_area"
KEY_SIMILAR_TOLERANCE = "similar/tolerance"
KEY_UNDO_LIMIT = "history/undo_limit"

# ---- 视图辅助线（标线 / 每像素网格）的颜色与显示门槛 ----
# 这些线是**看图和量尺寸**用的，颜色按人眼习惯（和素材底色）来定，所以放到设置里能改。
KEY_GUIDE_COLOR = "view/guide_color"
KEY_GUIDE_SELECTED_COLOR = "view/guide_selected_color"
KEY_PIXEL_GRID_COLOR = "view/pixel_grid_color"
KEY_PIXEL_GRID_ZOOM = "view/pixel_grid_zoom"

# 默认值：普通标线青色、选中标线主题色、像素网格半透明灰（#AARRGGBB，
# 半透明是为了 400% 下不至于把画面糊住）
DEFAULT_GUIDE_COLOR = "#00ffff"
DEFAULT_GUIDE_SELECTED_COLOR = "#e91e63"
DEFAULT_PIXEL_GRID_COLOR = "#80808080"
DEFAULT_PIXEL_GRID_ZOOM = 400


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


def detect_min_area() -> int:
    """自动识别时忽略的最大碎片边长（像素）。默认 2。"""
    try:
        value = int(_settings().value(KEY_DETECT_MIN_AREA, 2))
    except Exception as e:  # noqa: BLE001
        print(f"[settings] 读取 {KEY_DETECT_MIN_AREA} 失败: {e}", file=sys.stderr)
        return 2
    return max(0, value)


def set_detect_min_area(value: int) -> None:
    try:
        _settings().setValue(KEY_DETECT_MIN_AREA, max(0, int(value)))
    except Exception as e:  # noqa: BLE001
        print(f"[settings] 写入 {KEY_DETECT_MIN_AREA} 失败: {e}", file=sys.stderr)


def similar_tolerance() -> int:
    """「相似帧」判定的容差（0-100）。默认 0 = **一模一样**才算相似。

    用户口径：「阈值要放设置里 默认是要一模一样」—— 所以默认 0，字节级相同才同色；
    调大以后转成"形状重合度 + 颜色差"的宽容度（呼吸动画那种小幅差异也能归一组）。
    """
    try:
        value = int(_settings().value(KEY_SIMILAR_TOLERANCE, 0))
    except Exception as e:  # noqa: BLE001
        print(f"[settings] 读取 {KEY_SIMILAR_TOLERANCE} 失败: {e}", file=sys.stderr)
        return 0
    return max(0, min(100, value))


def set_similar_tolerance(value: int) -> None:
    try:
        _settings().setValue(KEY_SIMILAR_TOLERANCE, max(0, min(100, int(value))))
    except Exception as e:  # noqa: BLE001
        print(f"[settings] 写入 {KEY_SIMILAR_TOLERANCE} 失败: {e}", file=sys.stderr)


def undo_limit() -> int:
    """历史记录保留步数。默认 100，范围 0-500。快照纯数据(不含PNG)，每步几KB，500步也不卡。"""
    try:
        value = int(_settings().value(KEY_UNDO_LIMIT, 100))
    except Exception as e:  # noqa: BLE001
        print(f"[settings] 读取 {KEY_UNDO_LIMIT} 失败: {e}", file=sys.stderr)
        return 100
    return max(0, min(500, value))


def set_undo_limit(value: int) -> None:
    try:
        _settings().setValue(KEY_UNDO_LIMIT, max(0, min(500, int(value))))
    except Exception as e:  # noqa: BLE001
        print(f"[settings] 写入 {KEY_UNDO_LIMIT} 失败: {e}", file=sys.stderr)


def _color(key: str, default: str) -> QColor:
    """读一个颜色设置。#AARRGGBB / #RRGGBB / 颜色名都能认，认不出来就用默认色。"""
    raw = default
    try:
        raw = str(_settings().value(key, default) or default)
    except Exception as e:  # noqa: BLE001 - 读不到就按默认色走，绝不因为设置崩绘制
        print(f"[settings] 读取 {key} 失败: {e}", file=sys.stderr)
    color = QColor(raw)
    if not color.isValid():
        color = QColor(default)
    return color


def _set_color(key: str, color) -> None:
    try:
        if isinstance(color, QColor):
            _settings().setValue(key, color.name(QColor.NameFormat.HexArgb))
        else:
            _settings().setValue(key, str(color))
    except Exception as e:  # noqa: BLE001
        print(f"[settings] 写入 {key} 失败: {e}", file=sys.stderr)


def guide_color() -> QColor:
    """普通标线（从标尺拖出来的参考线）的颜色。默认青色。"""
    return _color(KEY_GUIDE_COLOR, DEFAULT_GUIDE_COLOR)


def set_guide_color(color) -> None:
    _set_color(KEY_GUIDE_COLOR, color)


def guide_selected_color() -> QColor:
    """选中的那条标线的颜色。默认主题色（一眼看出选中了哪条）。"""
    return _color(KEY_GUIDE_SELECTED_COLOR, DEFAULT_GUIDE_SELECTED_COLOR)


def set_guide_selected_color(color) -> None:
    _set_color(KEY_GUIDE_SELECTED_COLOR, color)


def pixel_grid_color() -> QColor:
    """完美像素模式下每像素网格线的颜色（可带透明度）。默认半透明灰。"""
    return _color(KEY_PIXEL_GRID_COLOR, DEFAULT_PIXEL_GRID_COLOR)


def set_pixel_grid_color(color) -> None:
    _set_color(KEY_PIXEL_GRID_COLOR, color)


def pixel_grid_zoom() -> int:
    """放大到多少倍才显示每像素网格（100 = 100%）。默认 400。

    为什么不一直显示：网格线间距 = 放大倍数，100% 时线宽 1px、间距 1px，
    整张图会被线盖满（等于给图蒙一层纱）。PS 也是放大到一定倍数才显示像素网格。
    """
    try:
        value = int(_settings().value(KEY_PIXEL_GRID_ZOOM, DEFAULT_PIXEL_GRID_ZOOM))
    except Exception as e:  # noqa: BLE001
        print(f"[settings] 读取 {KEY_PIXEL_GRID_ZOOM} 失败: {e}", file=sys.stderr)
        return DEFAULT_PIXEL_GRID_ZOOM
    return max(100, min(3200, value))


def set_pixel_grid_zoom(percent: int) -> None:
    try:
        _settings().setValue(KEY_PIXEL_GRID_ZOOM, max(100, min(3200, int(percent))))
    except Exception as e:  # noqa: BLE001
        print(f"[settings] 写入 {KEY_PIXEL_GRID_ZOOM} 失败: {e}", file=sys.stderr)


def reset_view_colors() -> None:
    """把三个辅助线颜色恢复默认（设置弹窗里的「恢复默认」）。"""
    set_guide_color(DEFAULT_GUIDE_COLOR)
    set_guide_selected_color(DEFAULT_GUIDE_SELECTED_COLOR)
    set_pixel_grid_color(DEFAULT_PIXEL_GRID_COLOR)
