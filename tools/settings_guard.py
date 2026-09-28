# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""回归脚本的"用户设置保险"：把会被测试改动的软件设置先记到磁盘，跑完原样写回。

为什么要落盘，而不是记在内存里就完事：这套回归要验"标线/网格的颜色真的从设置里读、
真的画出来了"，就必须把用户的设置改成测试色。**万一跑到一半崩了**（本项目的
offscreen 收尾偶尔会崩，是记过的老问题），内存里那份原值就跟着进程一起没了，
用户的设置会**永久**留成测试色。2026-XX 真的踩过一次：标线变成 #00FF00、选中变 #FF8800、
识别阈值变成 0，而且更阴的是——下次正常跑完时会"忠实地"把这份脏值当成用户原值再写回一遍，
自己永远好不了；表现出来是 `verify_guides_clear` 那两条颜色断言无缘无故就红了。

用法（脚本开头）：

    from settings_guard import protect_settings   # 同目录
    restore = protect_settings()

    ... 随便改设置 ...

    restore()        # 收尾（推荐再挂一个 atexit，见下）

protect_settings() 自己会：
  1. 若上次留下的存档还在（= 上次没跑到收尾），**先按它恢复** —— 把脏值救回来；
  2. 记录当前值到存档文件；
  3. 返回一个幂等的 restore()，正常收尾调它（同时自动挂上 atexit 兜异常）。
"""

from __future__ import annotations

import atexit
import json
import os

from PyQt6.QtGui import QColor

from spritesheet import settings as _settings

# 存档放在 tools/ 下（和脚本同目录）：不进用户配置目录，删掉也只是少一层保险
_BACKUP = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".settings_backup.json")


def _read() -> dict:
    """当前要保护的那几项（都是"用户可能自己调过"的视图/识别设置）。"""
    return {
        "guide": _settings.guide_color().name(QColor.NameFormat.HexArgb),
        "guide_selected": _settings.guide_selected_color().name(QColor.NameFormat.HexArgb),
        "grid": _settings.pixel_grid_color().name(QColor.NameFormat.HexArgb),
        "grid_zoom": int(_settings.pixel_grid_zoom()),
        "min_area": int(_settings.detect_min_area()),
    }


def _write(values: dict) -> None:
    _settings.set_guide_color(QColor(values["guide"]))
    _settings.set_guide_selected_color(QColor(values["guide_selected"]))
    _settings.set_pixel_grid_color(QColor(values["grid"]))
    _settings.set_pixel_grid_zoom(int(values["grid_zoom"]))
    _settings.set_detect_min_area(int(values["min_area"]))


def protect_settings():
    """见模块开头。返回幂等的 restore()。"""
    if os.path.exists(_BACKUP):
        # 上次没跑到收尾就崩了：存档里那份才是用户的原值，先救回来再重新记
        try:
            with open(_BACKUP, "r", encoding="utf-8") as fp:
                _write(json.load(fp))
            print("  [注意] 发现上次回归留下的设置存档，已按它恢复用户设置")
        except (OSError, ValueError, KeyError):
            pass                      # 存档坏了就别动用户设置，宁可不动
    values = _read()
    try:
        with open(_BACKUP, "w", encoding="utf-8") as fp:
            json.dump(values, fp)
    except OSError:
        pass                          # 写不了存档：至少内存里那份还能兜住正常收尾

    done = False

    def restore() -> None:
        nonlocal done
        if done:
            return                    # 幂等：显式调过之后 atexit 再来一次也没事
        done = True
        _write(values)
        try:
            os.remove(_BACKUP)
        except OSError:
            pass

    atexit.register(restore)          # 抛异常退出也能写回（硬崩就靠下次启动那份存档）
    return restore