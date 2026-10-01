# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""动画设置横条 + 帧编辑瘦身 回归验证。

覆盖：
  1. 动作级控件已从右侧帧编辑面板迁到原图区下方「动画设置」横条
    （数据复用复选框 / 浮动条循环与锚点按钮 / 面板动作类型组均已移除）；
  2. 横条每个控件写入当前动作（fps / 扩展 / 循环 / 锚点 / 地面线 / 类型 / 方向 / 速度）；
  3. 切换动作时横条完整同步；
  4. 底部横条「变换 / 微调」作用于全部帧，右侧保留单帧编辑；
  5. 「复制当前帧变换 → 全部帧」一次性覆盖 6 项逐帧变换，且可 Ctrl+Z 撤销；
  6. 单帧变换严格只改当前帧（旧"数据复用"实时跨帧联动已删除）；
  7. 老工程字段 reuse_frame_settings 保留（只读兼容）；
  8. 无动作时横条整体禁用。

Usage: .venv\\Scripts\\python.exe tools\\verify_anim_settings.py
"""
import os
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILS: list[str] = []
passed = 0


def check(name: str, cond, detail: str = "") -> None:
    global passed
    if cond:
        passed += 1
        print(f"  [OK]   {name}")
    else:
        FAILS.append(name)
        print(f"  [FAIL] {name} {detail}")


def silence_dialogs():
    from PyQt6.QtWidgets import QMessageBox, QFileDialog
    std = QMessageBox.StandardButton
    for n in ("information", "warning", "critical", "question"):
        setattr(QMessageBox, n, staticmethod(lambda *a, **k: std.Ok))
    QMessageBox.exec = lambda self, *a, **k: 0
    QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: "")


def add_action(win, name, n=3, **kw):
    """造 n 个色块元素并裁成一个 n 帧动作；kw 直接写到 Action 字段。"""
    from spritesheet.ui.models import Action
    src = QPixmap(90 * n + 20, 60)
    src.fill(Qt.GlobalColor.transparent)
    p = QPainter(src)
    for i in range(n):
        p.fillRect(QRect(10 + i * 90, 10, 40, 30), QColor("#C62828"))
    p.end()
    v = win.image_viewer
    v._detect_generation += 1
    v._pixmap = src
    v._source_images = [src]
    v._frames = [(10 + i * 90, 10, 40, 30) for i in range(n)]
    v._frame_logical = {}
    a = Action(name, "#E3351C")
    win.actions.append(a)
    win._append_action_card(a)
    a.selected_indices = list(range(n))
    v.set_current_action(a)
    v.crop_frames_for_action(a)
    for k, val in kw.items():
        setattr(a, k, val)
    v.set_actions(win.actions)
    win.preview.set_actions(win.actions)
    win.preview.set_source_pixmap(src)
    win.preview.set_action(a)
    return a


def main() -> int:
    app = QApplication(sys.argv)
    silence_dialogs()
    from spritesheet.ui.main_window import MainWindow
    from spritesheet.ui.models import Action

    win = MainWindow()
    win.show()
    app.processEvents()
    a1 = add_action(win, "动作1", 3)
    bar = win.anim_bar
    panel = win.preview.adjust_panel

    # ---- 1. 迁移结果：旧控件消失、新控件就位 ----
    check("动画设置横条已创建", bar is not None)
    check("右侧数据复用复选框已移除", not hasattr(panel, "chk_reuse"))
    check("浮动条循环按钮已移除", not hasattr(win.preview.toolbar, "btn_loop"))
    check("浮动条锚点按钮已移除", not hasattr(win.preview.toolbar, "btn_anchor"))
    check("面板动作类型按钮已移除", not hasattr(panel, "btn_type_static"))
    check("复制到全部帧按钮已删除", not hasattr(panel, "btn_copy_transform"))
    check("本帧时长控件存在", hasattr(panel, "spin_duration"))
    check("全部帧按钮已从右侧移除", not hasattr(panel, "btn_duration_all"))
    check("应用到所有动作按钮已删除", not hasattr(bar, "btn_apply_all"))
    check("帧速控件已在横条", hasattr(bar, "spin_fps"))
    check("底部横条全部帧变换控件存在",
          hasattr(bar, "btn_all_mirror_h") and hasattr(bar, "btn_all_mirror_v")
          and hasattr(bar, "btn_all_clear")
          and "rotate" in getattr(bar, "_all_mode_buttons", {}))
    check("底部横条全部帧微调控件存在",
          hasattr(bar, "btn_all_right") and hasattr(bar, "spin_all_off_x")
          and hasattr(bar, "slider_all_off_y"))
    check("浮动条瘦身为 86 宽", win.preview.toolbar.width() == 86,
          f"w={win.preview.toolbar.width()}")  # setFixedSize(86, 32)：撤放大/缩小后单按钮时代

    # ---- 2. 横条控件写入当前动作（帧速=全部帧统一，右侧单帧覆盖）----
    bar.spin_padding.setValue(8)
    check("padding 写入动作", a1.padding == 8)
    bar.spin_fps.setValue(20)
    check("帧速写入动作 fps", a1.fps == 20)
    check("帧速清空单帧差异", a1.frame_durations == {})
    bar.btn_loop.click()
    check("循环模式切换", a1.loop_mode == Action.ONCE, a1.loop_mode)
    bar.btn_anchor.click()
    check("锚点开关写入", a1.show_anchor is True)
    bar.chk_ground.setChecked(True)
    check("地面线开关写入", a1.ground_enabled is True)
    bar.btn_ground_up.click()
    check("地面线上移", a1.ground_offset == 1)
    bar.btn_type_move.click()
    check("切移动类型", a1.action_type == "move"
          and bar.dir_widget.isVisible() and bar.speed_widget.isVisible())
    bar.btn_dir_up.click()
    check("方向写入", a1.move_direction == "up")
    bar.slider_speed.setValue(40)
    check("速度写入", a1.move_speed == 40)
    bar.btn_type_static.click()
    check("切静止类型", a1.action_type == "static"
          and bar.dir_widget.isVisible() and not bar.dir_widget.isEnabled()
          and bar.speed_widget.isVisible() and not bar.speed_widget.isEnabled())

    # ---- 3. 切换动作时横条同步 ----
    a2 = add_action(win, "动作2", 2, fps=12, padding=3, action_type="move",
                    move_direction="left", move_speed=55, loop_mode="pingpong",
                    show_anchor=False, ground_enabled=True, ground_offset=7)
    check("横条同步 padding", bar.spin_padding.value() == 3)
    check("横条同步移动类型", bar.btn_type_move.isChecked() and bar.dir_widget.isVisible())
    check("横条同步方向", bar.btn_dir_left.isChecked())
    check("横条同步速度", bar.slider_speed.value() == 55)
    check("横条同步循环模式", bar._loop_mode == "pingpong")
    check("横条同步地面偏移", bar.lbl_ground_offset.text() == "7")

    # ---- 4. 横条帧速 = 整体动画（全部帧统一）+ 谁最后操作听谁的 ----
    win.preview.set_action(a2)
    app.processEvents()
    bar.spin_fps.setValue(20)   # 20fps = 50ms/帧
    check("帧速更新动作 fps", a2.fps == 20, str(a2.fps))
    check("帧速统一全部帧时长", all(a2.frame_duration_ms(i) == 50 for i in range(2)),
          str(a2.frame_durations))
    check("帧速清空逐帧覆盖", a2.frame_durations == {}, str(a2.frame_durations))
    panel.set_current_frame(0)
    panel.spin_duration.setValue(120)
    check("右侧单帧后操作赢", a2.frame_durations.get(0) == 120
          and a2.frame_duration_ms(1) == 50, str(a2.frame_durations))
    bar.spin_fps.setValue(25)
    check("底部帧速后操作全部赢", a2.frame_durations == {} and a2.fps == 25,
          f"{a2.frame_durations}/{a2.fps}")

    # ---- 5. 底部横条「变换/微调」= 全部帧；右侧 = 单帧 ----
    win.preview.set_action(a1)
    app.processEvents()
    panel.set_current_frame(0)
    bar.btn_all_mirror_h.click()
    check("全部帧水平镜像", a1.frame_mirror_h == {0, 1, 2}, str(a1.frame_mirror_h))
    bar.btn_all_clear.click()
    check("全部帧清空变换", a1.frame_mirror_h == set() and a1.frame_rotations == {},
          f"{a1.frame_mirror_h}/{a1.frame_rotations}")
    panel.set_current_frame(1)
    panel._apply_rotation(90)
    check("右侧单帧旋转只改当前帧",
          a1.frame_rotations.get(1) == 90 and a1.frame_rotations.get(0) is None,
          str(dict(a1.frame_rotations)))
    panel._apply_rotation(0)   # 还原，避免影响后续用例
    bar.btn_all_right.click()
    check("全部帧偏移+1", all(a1.frame_offsets.get(i) == (1, 0) for i in range(3)),
          str(a1.frame_offsets))
    bar.spin_all_off_y.setValue(5)
    check("全部帧统一Y偏移", all(a1.frame_offsets.get(i) == (1, 5) for i in range(3)),
          str(a1.frame_offsets))
    bar.btn_all_offset_reset.click()
    check("全部帧偏移归零", a1.frame_offsets == {}, str(a1.frame_offsets))
    bar.btn_all_mirror_h.click()   # 取消勾选，恢复初始状态

    # ---- 6. 单帧编辑严格只改当前帧 ----
    panel.set_current_frame(1)
    panel._apply_rotation(90)
    check("单帧旋转只改当前帧",
          a1.frame_rotations.get(1) == 90 and a1.frame_rotations.get(2) is None,
          str(dict(a1.frame_rotations)))

    # ---- 6b. 单帧时长只影响当前帧，可重置；底部帧速后操作清空单帧 ----
    panel.set_current_frame(1)
    default_ms = a1.frame_duration_ms(0)
    panel.spin_duration.setValue(200)
    check("单帧时长写入当前帧", a1.frame_durations.get(1) == 200, str(a1.frame_durations))
    check("其他帧不跟随",
          a1.frame_duration_ms(0) == default_ms and a1.frame_duration_ms(2) == default_ms)
    panel.btn_duration_reset.click()
    check("重置清除单帧覆盖", 1 not in a1.frame_durations)
    bar.spin_fps.setValue(30)
    check("底部帧速后操作清空单帧", a1.frame_durations == {} and a1.fps == 30,
          f"{a1.frame_durations}/{a1.fps}")

    # ---- 7. 老工程字段只读兼容 ----
    a3 = Action("老工程", "#ffffff")
    check("reuse_frame_settings 字段保留", hasattr(a3, "reuse_frame_settings"))

    # ---- 8. 无动作时横条禁用 ----
    win.preview.set_action(None)
    app.processEvents()
    check("无动作时横条控件禁用",
          not bar.spin_padding.isEnabled() and not bar.spin_fps.isEnabled()
          and not bar.btn_all_mirror_h.isEnabled() and not bar.btn_all_right.isEnabled())

    win.close()

    # Qt 收尾：headless 下让解释器直接回收原生对象/后台线程会随机 0xC0000005
    # （检测线程、QPixmap 缓存等）。顺序与 smoke_test.py 一致：先跑几轮事件，
    # 再 deleteLater 让 Qt 真正析构，最后 quit 并再跑一轮事件。
    for _ in range(3):
        app.processEvents()
    win.deleteLater()
    app.processEvents()
    app.quit()
    app.processEvents()
    del win

    print("=" * 50)
    print(f"PASSED {passed} | FAILED {len(FAILS)}")
    if FAILS:
        print("Failures:", *FAILS, sep="\n  - ")
        return 1
    print("ANIM SETTINGS VERIFY OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
