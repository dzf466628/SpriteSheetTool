#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""回归测试：原图查看器里 Ctrl / Alt / Ctrl+Alt 的点击与框选语义（含帧号标注）。

用户口径（NEXT.md「原图选择 / 合成帧的 Ctrl+Alt 组合键」）：
- 单 Ctrl：把点到的元素合成为一帧（只加不减）；
- Alt+Ctrl：**一次按住 = 一张新帧**（轮内点一下加进来、再点一下移出去），
  松开组合键这一组定格成一帧，再按住才是下一帧；单 Alt 不受影响；
- 单 Alt：复用 / 取消复用 toggle，且**不能被"合成按钮保持模式"抢走**。

本脚本驱动真实的 `_Canvas.mouseReleaseEvent` 分支，并且**每次点击后都调用
`crop_frames_for_action`**（= 主窗口 `_on_selection_changed` 的做法），
然后**按"实际生成出来的帧"断言**。

为什么必须按帧断言：只看 `composite_groups` 会漏掉真正那个缺陷 ——
生成帧用的 `group_by_idx` 是"一个元素只属于一个组"，后一轮的组会把前一轮吞掉，
`composite_groups` 里明明有三组，帧数却永远只剩 1（用户报的"帧序号都是第一帧"）。

对应修复（image_viewer.py）：
- seal / _end_alt_ctrl_round：一轮一帧，松手才落进 frame_layouts，按住期间现算临时帧；
- _normalize_composite_groups：原地裁剪，别造"孤儿组"；
- mouseReleaseEvent：单击三分支（Alt 优先，不被合成模式抢）；
- _drop_from_layouts：取消选择只摘元素，不再整份清空布局；
- _action_frame_badges：帧号标注按**帧自己的号**（原来取 group[0] 的首帧号，
  元素跨多帧时几个框会一起显示第一帧的号）。

Usage: .venv\\Scripts\\python.exe tools\\verify_ctrl_alt.py
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QMouseEvent, QPixmap
from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from spritesheet.ui.image_viewer import BADGE_SQ, BADGE_STEP, ImageViewer
from spritesheet.ui.models import Action

CTRL = Qt.KeyboardModifier.ControlModifier
ALT = Qt.KeyboardModifier.AltModifier
NO_MOD = Qt.KeyboardModifier.NoModifier
# 四个 10x10 的元素，横排（元素序号 1..4 = 索引 0..3）
ELEMS = [(0, 0, 10, 10), (20, 0, 10, 10), (40, 0, 10, 10), (60, 0, 10, 10)]

FAILURES: list[str] = []
passed = 0


def check(name: str, cond, detail: str = "") -> None:
    global passed
    if cond:
        passed += 1
        print(f"  [OK]   {name}")
    else:
        FAILURES.append(name)
        print(f"  [FAIL] {name} {detail}")


# ---- 场景搭建与驱动 ----

def make_viewer(elems=None):
    """建一个带元素的原图查看器；elems 不给就用默认的四个 10x10 小元素。"""
    elems = list(elems or ELEMS)
    viewer = ImageViewer()
    pm = QPixmap(400, 300)
    pm.fill(QColor("#404040"))
    viewer._pixmap = pm
    viewer._zoom = 1.0
    viewer._offset = QPointF(0, 0)
    viewer._frames = elems
    viewer._frame_logical = {}
    viewer._test_elems = elems          # click/框选/帧反查都按这一份元素表来
    action = Action("验证动作")
    # 必须挂进 actions：画布的绘制、色块、帧号都只遍历 viewer.actions，
    # 只设 current_action 的话画布上什么都不画（本脚本第一次就踩了这个）。
    viewer.set_actions([action])
    viewer.current_action = action
    return viewer, action


def frames_of(viewer, action):
    """实际帧 → 每帧装着的元素索引集合（用 frame_parts 的裁剪矩形反查）。"""
    viewer.crop_frames_for_action(action)          # = 主窗口 _on_selection_changed
    by_rect = {r: i for i, r in enumerate(viewer._test_elems)}
    return [{by_rect.get(p[0], "?") for p in parts} for parts in action.frame_parts]


def _release(viewer, pos, mods):
    canvas = viewer.canvas
    canvas._panning = False
    canvas._pan_start = None
    canvas._src_press = None
    ev = QMouseEvent(QEvent.Type.MouseButtonRelease, pos, pos, pos,
                     Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, mods)
    canvas.mouseReleaseEvent(ev)
    canvas._drag_start = None
    canvas._selection_rect = None


def click(viewer, idx, mods):
    """单击第 idx 个元素（真实分支 + 真实刷新）。"""
    x, y, w, h = viewer._test_elems[idx]
    pos = QPointF(x + w / 2.0, y + h / 2.0)
    viewer.canvas._drag_start = pos
    viewer.canvas._selection_rect = QRectF(pos, pos)     # < 5px → 走单击分支
    _release(viewer, pos, mods)


def box_select(viewer, indices, mods):
    """框选若干元素。"""
    rect = None
    for i in indices:
        x, y, w, h = viewer._test_elems[i]
        r = QRectF(x - 1, y - 1, w + 2, h + 2)
        rect = r if rect is None else rect.united(r)
    viewer.canvas._drag_start = rect.topLeft()
    viewer.canvas._selection_rect = rect
    _release(viewer, rect.bottomRight(), mods)


def key_alt_down(viewer):
    viewer.begin_alt_reuse()


def key_alt_up(viewer):
    viewer.end_alt_reuse()


def key_ctrl_down(viewer):
    viewer.begin_ctrl_composition()


def key_ctrl_up(viewer):
    viewer.end_ctrl_composition()


def release_all(viewer):
    key_alt_up(viewer)
    key_ctrl_up(viewer)


# ---- 用例 ----

def case_three_rounds():
    """NEXT.md 的验收场景：Ctrl+{1,2} / AltCtrl+{1,2,3} / AltCtrl+{1,2,3,4} = 3 帧。"""
    print("--- 三轮组合键：Ctrl+{1,2} / Alt+Ctrl+{1,2,3} / Alt+Ctrl+{1,2,3,4} ---")
    viewer, act = make_viewer()

    key_ctrl_down(viewer)
    click(viewer, 0, CTRL)
    click(viewer, 1, CTRL)
    key_ctrl_up(viewer)
    r1 = frames_of(viewer, act)
    check("第 1 轮（单 Ctrl）→ 1 帧 {元素1,2}", r1 == [{0, 1}], f"frames={r1}")

    key_ctrl_down(viewer)
    key_alt_down(viewer)
    click(viewer, 0, CTRL | ALT)
    click(viewer, 1, CTRL | ALT)
    click(viewer, 2, CTRL | ALT)
    r2_live = frames_of(viewer, act)
    check("第 2 轮按住期间就能看到这一帧", r2_live == [{0, 1}, {0, 1, 2}], f"frames={r2_live}")
    release_all(viewer)
    r2 = frames_of(viewer, act)
    check("第 2 轮松手后定格 → 2 帧（前一帧没被吞）", r2 == [{0, 1}, {0, 1, 2}], f"frames={r2}")

    key_ctrl_down(viewer)
    key_alt_down(viewer)
    click(viewer, 0, CTRL | ALT)
    click(viewer, 1, CTRL | ALT)
    click(viewer, 2, CTRL | ALT)
    click(viewer, 3, CTRL | ALT)
    release_all(viewer)
    r3 = frames_of(viewer, act)
    check("第 3 轮松手后 → 3 帧，每帧成员对得上",
          r3 == [{0, 1}, {0, 1, 2}, {0, 1, 2, 3}], f"frames={r3}")
    check("动作里帧数 = 3（不是 1）", len(act.frames) == 3, f"n={len(act.frames)}")
    check("composite_groups 三轮都在册",
          act.composite_groups == [[0, 1], [0, 1, 2], [0, 1, 2, 3]],
          f"groups={act.composite_groups}")
    return not FAILURES


def case_one_round_one_frame():
    """一轮里点两个新元素 → 仍然只多一帧（不是一点一帧）。"""
    print("--- 一轮里点两个新元素：Alt+Ctrl 点 3、点 4 ---")
    viewer, act = make_viewer()
    key_ctrl_down(viewer)
    click(viewer, 0, CTRL)
    click(viewer, 1, CTRL)
    key_ctrl_up(viewer)

    key_ctrl_down(viewer)
    key_alt_down(viewer)
    click(viewer, 2, CTRL | ALT)
    click(viewer, 3, CTRL | ALT)
    release_all(viewer)
    got = frames_of(viewer, act)
    check("本轮只出一帧 {元素3,4}", got == [{0, 1}, {2, 3}], f"frames={got}")
    check("没有把元素3 单独留成一帧", {2} not in got, f"frames={got}")
    return got == [{0, 1}, {2, 3}]


def case_toggle_out_then_add():
    """轮内：点1、点2、再点1（移出）、再点3 → 元素3 必须和元素2 同一帧（不留孤儿组）。"""
    print("--- 轮内移出元素后再点新元素（孤儿组回归） ---")
    viewer, act = make_viewer()
    key_ctrl_down(viewer)
    click(viewer, 0, CTRL)
    click(viewer, 1, CTRL)
    key_ctrl_up(viewer)

    key_ctrl_down(viewer)
    key_alt_down(viewer)
    click(viewer, 0, CTRL | ALT)          # 元素1 拉进本轮
    click(viewer, 1, CTRL | ALT)          # 元素2 拉进本轮
    click(viewer, 0, CTRL | ALT)          # 元素1 移出（触发 _normalize_composite_groups）
    click(viewer, 2, CTRL | ALT)          # 元素3 进来 → 应和元素2 同帧
    release_all(viewer)
    got = frames_of(viewer, act)
    check("元素3 与元素2 在同一帧（没被拆成独立帧）", got == [{1}, {1, 2}], f"frames={got}")
    check("当前组对象仍挂在 action.composite_groups 上（不是孤儿列表）",
          viewer._active_composite_group is None
          or any(viewer._active_composite_group is g for g in act.composite_groups),
          f"active={viewer._active_composite_group} groups={act.composite_groups}")
    return got == [{1}, {1, 2}]


def case_alt_repress_new_round():
    """按住 Ctrl 不动、只松开再按 Alt → 另起一轮（新一帧）。"""
    print("--- Ctrl 不松，Alt 松开再按 = 新的一轮 ---")
    viewer, act = make_viewer()
    key_ctrl_down(viewer)
    key_alt_down(viewer)
    click(viewer, 0, CTRL | ALT)
    click(viewer, 1, CTRL | ALT)
    key_alt_up(viewer)                    # 只松 Alt（Ctrl 仍按住）
    key_alt_down(viewer)                  # 再按 Alt
    click(viewer, 2, CTRL | ALT)
    release_all(viewer)
    got = frames_of(viewer, act)
    check("第 2 轮另起一帧 {元素3}", got == [{0, 1}, {2}], f"frames={got}")
    return got == [{0, 1}, {2}]


def case_manual_compose_alt():
    """合成按钮保持模式开着时，单独 Alt+点击 仍必须是复用（不被合成抢走）。"""
    print("--- 合成按钮保持模式 + 单独 Alt+点击 ---")
    viewer, act = make_viewer()
    key_ctrl_down(viewer)
    click(viewer, 0, CTRL)
    click(viewer, 1, CTRL)
    key_ctrl_up(viewer)
    base = frames_of(viewer, act)
    check("基线：Ctrl 合成 1+2 = 1 帧", base == [{0, 1}], f"frames={base}")

    viewer.set_compose_mode(True)          # 手动点亮「Ctrl+合成」按钮
    check("合成模式已常驻", viewer.compose_mode is True)
    click(viewer, 0, ALT)                  # 单 Alt：复用
    release_all(viewer)
    got = frames_of(viewer, act)
    check("Alt+点击 = 复用（多出一帧 {0,1}）", got == [{0, 1}, {0, 1}], f"frames={got}")
    return got == [{0, 1}, {0, 1}]


def case_single_modifiers():
    """单 Ctrl 只加不减；单 Alt 复用 / 取消复用 toggle。"""
    print("--- 单 Ctrl / 单 Alt 不回归 ---")
    viewer, act = make_viewer()
    key_ctrl_down(viewer)
    click(viewer, 0, CTRL)
    click(viewer, 0, CTRL)                # 再点已选中：单 Ctrl 是"只加不减"，不该取消
    got = frames_of(viewer, act)
    check("单 Ctrl 再点已选中元素不取消", got == [{0}], f"frames={got}")
    click(viewer, 1, CTRL)
    key_ctrl_up(viewer)
    got = frames_of(viewer, act)
    check("单 Ctrl 合成 1+2 = 1 帧", got == [{0, 1}], f"frames={got}")

    key_alt_down(viewer)
    click(viewer, 0, ALT)                 # 复用一次
    key_alt_up(viewer)
    got = frames_of(viewer, act)
    check("单 Alt 复用 → 多一帧", got == [{0, 1}, {0, 1}], f"frames={got}")
    key_alt_down(viewer)
    click(viewer, 0, ALT)                 # 再点一次 = 取消最近一次复用
    key_alt_up(viewer)
    got = frames_of(viewer, act)
    check("单 Alt 再点 → 取消最近一次复用", got == [{0, 1}], f"frames={got}")
    return got == [{0, 1}]


def case_deselect_keeps_frames():
    """已有复用帧时，取消选择只把该元素摘掉，不再把 frame_layouts 整份清空。"""
    print("--- 取消选择不再整份清空复用帧 ---")
    viewer, act = make_viewer()
    key_ctrl_down(viewer)
    click(viewer, 0, CTRL)
    click(viewer, 1, CTRL)
    key_ctrl_up(viewer)
    key_alt_down(viewer)
    click(viewer, 0, ALT)                 # 复用一帧 → 建立 frame_layouts
    key_alt_up(viewer)
    got = frames_of(viewer, act)
    check("复用后 = 2 帧", got == [{0, 1}, {0, 1}], f"frames={got}")
    n_layouts = len(act.frame_layouts or [])
    check("frame_layouts 2 条", n_layouts == 2, f"layouts={act.frame_layouts}")

    click(viewer, 0, NO_MOD)              # 普通点击 → 取消选中元素1
    got = frames_of(viewer, act)
    check("取消选中后帧还在（不是整份清空）", act.frame_layouts is not None,
          f"layouts={act.frame_layouts}")
    check("元素1 被摘掉、帧结构保留", got == [{1}, {1}], f"frames={got}")
    check("基线帧计数没越界", act.frame_layouts_base <= len(act.frame_layouts or []),
          f"base={act.frame_layouts_base} n={len(act.frame_layouts or [])}")
    return act.frame_layouts is not None and got == [{1}, {1}]


def case_box_select():
    """Alt+Ctrl+框选 = 同样"一轮一帧"。"""
    print("--- Alt+Ctrl+框选 ---")
    viewer, act = make_viewer()
    key_ctrl_down(viewer)
    box_select(viewer, [0, 1], CTRL)
    key_ctrl_up(viewer)
    got = frames_of(viewer, act)
    check("Ctrl+框选 {1,2} = 1 帧", got == [{0, 1}], f"frames={got}")

    key_ctrl_down(viewer)
    key_alt_down(viewer)
    box_select(viewer, [2, 3], CTRL | ALT)
    release_all(viewer)
    got = frames_of(viewer, act)
    check("Alt+Ctrl+框选 {3,4} = 新的一帧", got == [{0, 1}, {2, 3}], f"frames={got}")
    return got == [{0, 1}, {2, 3}]


def case_alt_click_after_rounds():
    """Alt+Ctrl 攒完帧后，单 Alt+点击 = 复用（不能把轮帧当复用帧删掉）。"""
    print("--- 轮帧之后单 Alt+点击 = 复用（不是删除轮帧） ---")
    viewer, act = make_viewer()
    key_ctrl_down(viewer)
    click(viewer, 0, CTRL)
    click(viewer, 1, CTRL)
    key_ctrl_up(viewer)
    key_ctrl_down(viewer)
    key_alt_down(viewer)
    click(viewer, 0, CTRL | ALT)
    click(viewer, 1, CTRL | ALT)
    click(viewer, 2, CTRL | ALT)
    release_all(viewer)
    base = frames_of(viewer, act)
    check("两轮后 = 2 帧", base == [{0, 1}, {0, 1, 2}], f"frames={base}")
    sel_before = list(act.selected_indices)

    key_alt_down(viewer)
    click(viewer, 2, ALT)                 # 单 Alt：复用元素3 所在的这一帧（= 本轮轮帧）
    key_alt_up(viewer)
    got = frames_of(viewer, act)
    check("Alt+点击 = 复用（多一帧，不是删轮帧）",
          got == [{0, 1}, {0, 1, 2}, {0, 1, 2}], f"frames={got}")
    check("选中项没被清空", act.selected_indices == sel_before,
          f"sel={act.selected_indices}")
    return got == [{0, 1}, {0, 1, 2}, {0, 1, 2}]


def case_frame_badges():
    """帧号标注：三轮后三个合成框必须是 1 / 2 / 3，按住期间本轮也有号。"""
    print("--- 帧号标注（元素 / 合成框左上角的号） ---")
    viewer, act = make_viewer()
    key_ctrl_down(viewer)
    click(viewer, 0, CTRL)
    click(viewer, 1, CTRL)
    key_ctrl_up(viewer)
    for _round, last in ((2, 2), (3, 3)):
        key_ctrl_down(viewer)
        key_alt_down(viewer)
        for i in range(last + 1):
            click(viewer, i, CTRL | ALT)
        release_all(viewer)
    _cr, fns, frames, from_layouts = viewer._action_frame_badges(act)
    boxes = [(no, members) for no, members in frames if len(members) >= 2]
    check("三个合成框的帧号 = 1 / 2 / 3",
          boxes == [(1, (0, 1)), (2, (0, 1, 2)), (3, (0, 1, 2, 3))], f"boxes={boxes}")
    check("元素1 的帧号序列 = 1,2,3（多帧计数）", fns.get(0) == [1, 2, 3], f"fns={fns}")
    check("元素4 只在第 3 帧 → 号 = 3", fns.get(3) == [3], f"fns={fns}")
    check("帧号来源 = 显式布局", from_layouts is True, f"from_layouts={from_layouts}")

    # 按住期间：本轮那一帧还没落盘，元素不能退回"选中序号"当帧号
    v2, a2 = make_viewer()
    key_ctrl_down(v2)
    click(v2, 0, CTRL)
    click(v2, 1, CTRL)
    key_ctrl_up(v2)
    key_ctrl_down(v2)
    key_alt_down(v2)
    click(v2, 0, CTRL | ALT)
    click(v2, 1, CTRL | ALT)
    click(v2, 2, CTRL | ALT)
    _cr2, fns2, frames2, _ = v2._action_frame_badges(a2)
    boxes2 = [(no, members) for no, members in frames2 if len(members) >= 2]
    check("按住期间临时帧已有号（1、2）",
          boxes2 == [(1, (0, 1)), (2, (0, 1, 2))], f"boxes={boxes2}")
    check("元素3 的号 = 2（不是选中序号 3）", fns2.get(2) == [2], f"fns={fns2}")
    release_all(v2)
    return boxes == [(1, (0, 1)), (2, (0, 1, 2)), (3, (0, 1, 2, 3))] and fns2.get(2) == [2]


def dominant_color(img, x, y, w, h):
    """一小块区域里出现最多的颜色名（画布是渲染出来取像素，不看内部状态）。"""
    counts = {}
    for py in range(int(y), int(y + h)):
        for px in range(int(x), int(x + w)):
            name = QColor(img.pixel(px, py)).name().upper()
            counts[name] = counts.get(name, 0) + 1
    return max(counts.items(), key=lambda kv: kv[1])[0]


def case_round_badges_side_by_side():
    """真渲染：三轮攒帧后，元素1 左上角的 1/2/3 号必须并排，不能叠成一坨。

    三轮的框都以元素1 为左上角（元素1 是最上最左的那个），帧号色块的落点完全相同 ——
    这是用户说的"元素左上角那个帧号不对"。所以要按 BADGE_STEP 顺延，并排画出来。
    """
    print("--- 元素1 左上角的三个帧号色块并排（真渲染取像素） ---")
    elems = [(10, 10, 60, 40), (80, 10, 60, 40), (150, 10, 60, 40), (220, 10, 60, 40)]
    viewer, act = make_viewer(elems)
    key_ctrl_down(viewer)
    click(viewer, 0, CTRL)
    click(viewer, 1, CTRL)
    key_ctrl_up(viewer)
    for _round, last in ((2, 2), (3, 3)):
        key_ctrl_down(viewer)
        key_alt_down(viewer)
        for i in range(last + 1):
            click(viewer, i, CTRL | ALT)
        release_all(viewer)
    got_frames = frames_of(viewer, act)
    check("三轮 = 3 帧", got_frames == [{0, 1}, {0, 1, 2}, {0, 1, 2, 3}], f"frames={got_frames}")

    viewer.canvas.resize(400, 300)
    # 采样前把两条**浮动**工具条收起来：它们是半透明覆盖层，还可以被用户拖着走，
    # 正好压在帧号色块上就会把颜色染淡（工具条多一个「完美像素」按钮变宽之后，
    # 这个 400 宽的小画布上就压住了第 2、3 个色块）。浮层不影响元素怎么画。
    _bars = [b for b in (viewer._toolbar, viewer._v_toolbar) if b is not None]
    for _bar in _bars:
        _bar.hide()
    viewer.canvas.grab()                       # 先渲染一次：resize 之后的居中/缩放调整要落定
    zoom = viewer._zoom
    off = viewer._offset
    img = viewer.canvas.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)
    want = QColor(act.color).name().upper()
    # 锚点 = 框（= 元素1）左上角在画布上的设备坐标；帧号色块是 16px 的屏幕空间覆盖层，
    # 步进 BADGE_STEP 不随缩放变。
    ax = off.x() + elems[0][0] * zoom
    ay = off.y() + elems[0][1] * zoom
    anchors = []
    for k in range(3):
        x = ax + k * BADGE_STEP
        got = dominant_color(img, x, ay, BADGE_SQ, BADGE_SQ)
        anchors.append((int(x), int(ay)))
        check(f"第 {k + 1} 个帧号色块在元素1 左上角 +{k} 格", got == want, f"got={got}")
    gap = dominant_color(img, ax + BADGE_SQ + 1, ay + 6, 1, 2 * BADGE_SQ + 4)
    check("三块之间留缝（不是叠成一整块）", gap != want, f"gap={gap} anchors={anchors}")
    for _bar in _bars:                         # 采样完了把浮层放回去
        _bar.show()
    return all(dominant_color(img, ax + k * BADGE_STEP, ay, BADGE_SQ, BADGE_SQ) == want
               for k in range(3))


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("SpriteSheetTool verify_ctrl_alt")   # 别让 app 变成"赋值未用"
    case_three_rounds()
    case_one_round_one_frame()
    case_toggle_out_then_add()
    case_alt_repress_new_round()
    case_manual_compose_alt()
    case_single_modifiers()
    case_deselect_keeps_frames()
    case_box_select()
    case_alt_click_after_rounds()
    case_frame_badges()
    case_round_badges_side_by_side()
    print()
    print("=" * 50)
    print(f"PASSED {passed}  |  FAILED {len(FAILURES)}")
    if FAILURES:
        print("Failures:", *FAILURES, sep="\n  - ")
        return 1
    print("CTRL+ALT INTERACTION OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
