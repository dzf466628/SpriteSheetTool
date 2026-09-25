"""临时探针：抽帧按钮真点一下 → 窗口开没开、三个区域和 4 颗按钮在不在。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QElapsedTimer, Qt                          # noqa: E402
from PyQt6.QtGui import QColor, QPainter, QPixmap                    # noqa: E402
from PyQt6.QtWidgets import QApplication                            # noqa: E402

from settings_guard import protect_settings                         # noqa: E402

_APP = QApplication.instance() or QApplication(sys.argv)


def wait(ms):
    t = QElapsedTimer()
    t.start()
    while t.elapsed() < ms:
        QApplication.processEvents()


def main():
    restore = protect_settings()
    from spritesheet.ui.main_window import MainWindow
    win = MainWindow()
    win.resize(1280, 800)
    win.show()
    wait(200)
    atlas = QPixmap(200, 60)
    atlas.fill(Qt.GlobalColor.transparent)
    p = QPainter(atlas)
    for x, col in ((10, "#1565C0"), (70, "#1565C0"), (130, "#C62828")):
        p.fillRect(x, 10, 40, 40, QColor(col))
    p.end()
    win.image_viewer.set_pixmap(atlas)
    wait(1500)
    action = win._create_animation_action("抽帧测试", [], 0, len(win.image_viewer._frames))
    win._install_animation_action(action)
    wait(500)
    panel = win.preview.adjust_panel
    panel.btn_extract.click()                       # 真点那颗按钮
    wait(500)
    dlg = getattr(win, "_extract_dialog", None)
    print("窗口开出来了：", dlg is not None and dlg.isVisible())
    if dlg is None:
        return 1
    print("尺寸：", dlg.width(), "x", dlg.height(), "（要求 500x600）")
    print("上面预览：", type(dlg.preview).__name__)
    print("播放进度条：", dlg.slider.value(), "/", dlg.slider.maximum())
    print("缩略图：", dlg.thumb_list.count(), "张")
    print("4 颗抽帧按钮：", [b.text() for b in dlg.decimate_buttons],
          "都可点：", [b.isEnabled() for b in dlg.decimate_buttons])
    # ---- 端到端：真点一次「强力去重」----
    # 这支 atlas 是 2 个一样的蓝块 + 1 个红块 → 帧 0/1 相邻且同组 → 强力去重该只留 1 帧
    before = len(win.image_viewer.current_action.frames)
    print("抽帧前帧数：", before)
    # 位移检查：记下抽帧前每帧的**落点**，抽完比对保留帧的落点是否逐帧不变
    from spritesheet.ui.transform import compute_preview_layout

    def spots(act):
        return [tuple(p[:2]) for p in compute_preview_layout(act)[2]]

    spots_before = spots(win.image_viewer.current_action)
    print("抽帧前落点：", spots_before)
    dlg.decimate_buttons[3].click()          # 强力去重
    wait(600)
    after = len(win.image_viewer.current_action.frames)
    spots_after = spots(win.image_viewer.current_action)
    print("抽帧后落点：", spots_after)
    print("保留帧落点没挪（旧 0→新 0，旧 2→新 1）：",
          spots_after[0] == spots_before[0] and len(spots_after) > 1
          and spots_after[1] == spots_before[2])
    print("强力去重后帧数：", after, "（应为 2）", "窗口缩略图：", dlg.thumb_list.count())
    print("记下了原始状态（可撤回）：", getattr(win, "_extract_origin", None) is not None)
    # 换档位 = 按原始帧表重抽，不叠着抽
    dlg.decimate_buttons[0].click()          # 3/5（力度最轻）
    wait(600)
    print("换成 3/5 后帧数：", len(win.image_viewer.current_action.frames), "（应回到 2，不是 1）")
    dlg.decimate_buttons[3].click()          # 再点回强力去重
    wait(600)
    print("再点强力去重后帧数：", len(win.image_viewer.current_action.frames), "（应为 2）")
    # 取消 = 整个撤回
    dlg._on_cancel()
    wait(600)
    print("取消后帧数：", len(win.image_viewer.current_action.frames), f"（应回到 {before}）")
    print("相似帧开着时缩略图仍在：", dlg.thumb_list.count(), "张")
    icon = dlg.thumb_list.item(0).icon()
    print("第一张缩略图不是空的：", not icon.isNull(),
          "尺寸：", icon.pixmap(36, 36).size().width())
    # 相似帧开着的时候，抽帧窗口的缩略图也要有彩块
    panel.btn_similar_frames.click()
    wait(600)
    win._refresh_extract_dialog()
    wait(300)
    print("相似帧开着时缩略图仍在：", dlg.thumb_list.count(), "张")
    panel.btn_similar_frames.click()
    wait(300)
    # 空格拖动：画布要能收到键盘
    from PyQt6.QtCore import QPointF
    dlg.preview.offset = QPointF(0, 0)
    print("预览焦点策略：", dlg.preview.focusPolicy().name)
    print("播放控件：", dlg.btn_play.text(), "进度条范围：", dlg.slider.minimum(), dlg.slider.maximum())
    dlg.slider.setValue(min(1, dlg.slider.maximum()))
    wait(200)
    print("拖进度条后当前帧：", dlg.preview.frame_index, "标签：", dlg.lbl_frame.text())
    print("缩略图图标尺寸：", dlg.thumb_list.iconSize().width())
    print("空格过滤器目标数：", len(win._space_pan_targets()))
    # 拆除前先把播放定时器停掉：带着还跑着的定时器回收窗口是老坑（0xC0000409）
    dlg._timer.stop()
    dlg.close()
    wait(200)
    win.close()
    wait(200)
    restore()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())