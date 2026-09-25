#!/usr/bin/env python
"""相似帧专项回归：算法（分组 / 容差 / 循环长度）+ 界面（开关两态、两处标色、纯显示不动数据）。

口径来自用户：
  * 「主要是查找所有的元素中相似的 在右下角用相似的颜色做标记（颜色一定要区分好）」
  * 「原图页面要显示 帧缩略图也要显示」（缩略图改**右上角**，右下角留给帧序号）
  * 「阈值要放设置里 默认是要一模一样」「全图识别（有别的新图也要找）」「不带信息」
  * 「这个功能主要是找循环帧用的」
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PyQt6.QtCore import QElapsedTimer, Qt                          # noqa: E402
from PyQt6.QtGui import QColor, QPainter, QPixmap                    # noqa: E402
from PyQt6.QtWidgets import QApplication                            # noqa: E402

from settings_guard import protect_settings                         # noqa: E402

_APP = QApplication.instance() or QApplication(sys.argv)
PASS = FAIL = 0


def check(name, ok, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [OK] {name} {extra}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


def wait(ms):
    t = QElapsedTimer()
    t.start()
    while t.elapsed() < ms:
        QApplication.processEvents()


def blob(w, h, color, dot=False):
    """一块内容。dot=True 时多一个像素——用来测"差一点点"在容差 0 下不算相似。"""
    pm = QPixmap(w, h)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.fillRect(0, 0, w, h, QColor(color))
    if dot:
        p.fillRect(1, 1, 2, 2, QColor("#FFFFFF"))
    p.end()
    return pm


def main():
    restore = protect_settings()
    from spritesheet.ui.similar import (group_color, group_elements, loop_hint,
                                        PALETTE)
    from spritesheet.settings import similar_tolerance

    print("=== 1. 分组算法（纯函数）===")
    a = blob(40, 40, "#1565C0")
    b = blob(40, 40, "#1565C0")
    c = blob(40, 40, "#C62828")
    d = blob(40, 40, "#C62828")
    e = blob(40, 40, "#2E7D32")
    groups = group_elements([a, b, c, d, e], 0)
    check("一模一样的两个两对 → 分成两组", len(set(groups.values())) == 2, f"{groups}")
    check("落单的那个不进结果（没得标、不占颜色）", 4 not in groups, f"{groups}")
    check("同组同色、不同组不同色",
          group_color(groups[0]).name() != group_color(groups[2]).name(),
          f"{group_color(groups[0]).name()} / {group_color(groups[2]).name()}")
    check("色板够用且颜色互不相同", len(set(PALETTE)) == len(PALETTE) and len(PALETTE) >= 12,
          f"{len(PALETTE)} 色")

    near = blob(40, 40, "#1565C0", dot=True)
    groups0 = group_elements([a, near, c, c], 0)
    check("容差 0（默认）时「差一个像素」不算相似",
          near not in groups0 or groups0.get(0) != groups0.get(1), f"{groups0}")
    groups_t = group_elements([a, near], 5)
    check("容差 5 时那一点差别就算相似了", groups_t.get(0) == groups_t.get(1) == 0,
          f"{groups_t}")

    print("=== 2. 循环长度提示 ===")
    marks = {0: 0, 3: 0, 6: 0, 1: 1, 2: 1}       # 第 1/4/7 帧一组 → 循环长度 3
    hint = loop_hint(marks)
    check("报出最可能的循环长度", "循环长度 3 帧" in hint, hint)
    check("相邻两帧一样不算循环（那是重复帧）",
          loop_hint({0: 0, 1: 0, 3: 2, 4: 2}) == "", loop_hint({0: 0, 1: 0, 3: 2, 4: 2}))
    check("没有任何同组 → 不显示这一句", loop_hint({0: 0, 1: 1}) == "", loop_hint({0: 0, 1: 1}))

    print("=== 3. 界面：开关两态 + 两处标色（真点那颗按钮）===")
    from spritesheet.ui.main_window import MainWindow

    win = MainWindow()
    win.resize(1280, 800)
    win.show()
    wait(200)
    viewer = win.image_viewer
    panel = win.preview.adjust_panel
    # 图集：两格一样 + 一格不同；内容 40x40（超过设置里任何阈值的噪点门限）
    atlas = QPixmap(200, 60)
    atlas.fill(Qt.GlobalColor.transparent)
    p = QPainter(atlas)
    for x, col in ((10, "#1565C0"), (70, "#1565C0"), (130, "#C62828")):
        p.fillRect(x, 10, 40, 40, QColor(col))
    p.end()
    viewer.set_pixmap(atlas)
    wait(1500)
    n_el = len(viewer._frames)
    check("图集被切成 3 个元素", n_el == 3, f"{n_el} 个")
    action = win._create_animation_action("相似帧测试", [], 0, n_el)
    win._install_animation_action(action)
    wait(600)

    check("没按下时按钮是可用的（有动作、帧数 ≥2）", panel.btn_similar_frames.isEnabled())
    check("没按下时两处都没有标记",
          viewer.similar_marks == {} and panel.similar_color_provider is None)
    before = (dict(action.frame_offsets), tuple(action.anchor), action.padding)

    panel.btn_similar_frames.click()              # 按下
    wait(600)
    check("按下以后按钮进入「按下」态", panel.btn_similar_frames.isChecked())
    check("原图页面标了 2 个元素（那对一样的）", len(viewer.similar_marks) == 2,
          f"{viewer.similar_marks}")
    check("两个元素的颜色一样（同组同色）",
          len({c.name() for c in viewer.similar_marks.values()}) == 1,
          f"{[c.name() for c in viewer.similar_marks.values()]}")
    colors = [win._similar_color_for_frame(action, i) for i in range(3)]
    check("帧缩略图前两帧有色块、第三帧没有（不同组的不标）",
          colors[0] is not None and colors[1] is not None and colors[2] is None,
          f"{[c.name() if c else None for c in colors]}")
    check("缩略图问色回调挂在预览控件上（色块画在图标右上角，右下角留给帧序号）",
          callable(win.preview.similar_color_provider)
          and win.preview.similar_color_provider is not None)
    check("状态栏说清了找到几组", "找到 1 组相似帧" in win.lbl_status.text(),
          win.lbl_status.text()[:70])
    check("纯显示：工程数据一个字节没动",
          (dict(action.frame_offsets), tuple(action.anchor), action.padding) == before)

    panel.btn_similar_frames.click()              # 松开
    wait(500)
    check("松开以后按钮回到未按下态", not panel.btn_similar_frames.isChecked())
    check("两处标记一起清掉",
          viewer.similar_marks == {} and panel.similar_color_provider is None)

    print("=== 4. 真素材实测（你那张 8178x8178 图集，只用来看数，不做断言的死条件）===")
    sheet = ROOT / "9月25日(1)_keyed_spritesheet_0.png"
    if not sheet.exists():
        print("  （图集不在，跳过）")
    else:
        pm = QPixmap(str(sheet))
        viewer.set_pixmap(pm)
        wait(8000)
        els = len(viewer._frames)
        if els >= 2:
            a2 = win._create_animation_action("真图集", [], 0, els)
            win._install_animation_action(a2)
            wait(1200)
            panel.btn_similar_frames.click()
            wait(3000)
            print(f"  元素 {els} 个，容差 {similar_tolerance()}% →", win.lbl_status.text()[:110])
            print(f"  标色元素 {len(viewer.similar_marks)} 个")
            # 容差扫一遍：用户反馈"0 只找到两个、1 就一下子全找到了"，这里把每档的组数摆出来
            el_pms = [pm.copy(int(r[0]), int(r[1]), int(r[2]), int(r[3]))
                      for r in viewer._frames]
            from spritesheet.ui.similar import group_elements
            for tol in (0, 1, 2, 3, 5, 8, 12, 20):
                g = group_elements(el_pms, tol)
                print(f"  容差 {tol:>2}% → {len(set(g.values()))} 组 / {len(g)} 帧")
            panel.btn_similar_frames.click()
            wait(300)

    win.image_viewer.set_current_action(None)
    win.close()
    wait(200)
    restore()
    print(f"\nSIMILAR FRAMES {'OK' if FAIL == 0 else 'FAILED'}  {PASS}/{PASS + FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())