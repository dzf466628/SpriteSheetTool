# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""「帧工具 · 自动对齐」专项回归。

用户口径（这一版按他的话重写）：
  *你先正常切割记录锚点位置，然后再紧切割记录位置，这两个位置的差值不就是偏移量吗*
  → **精确路**：原图是整帧画面时，按「原图锚点 − 紧切框中心」把每帧放回它在原图里的位置。
  → **估算路**：拿不到正常切割（原图已经被紧切过 / 带变换 / 多元素合成）时，只能按帧间
    重合度去猜，**必须先提示"可能不准、建议手动核对"**，用户点了「用估算值」才动手。
  两条路都**只改逐帧偏移**：锚点、padding、帧图像、尺寸一个字不碰，一条 Ctrl+Z 可退回。

播放与导出都走 `compute_preview_layout`（transform.py）那**一份**落点公式，所以这里直接量
它的输出 —— 量它就是量"播放时到底飘不飘"。

用法: QT_QPA_PLATFORM=offscreen .venv\\Scripts\\python.exe -X utf8 tools\\verify_auto_align.py
"""
from __future__ import annotations

import gc
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PyQt6.QtCore import QElapsedTimer, Qt                        # noqa: E402
from PyQt6.QtGui import QColor, QPainter, QPixmap                 # noqa: E402
from PyQt6.QtWidgets import QApplication                          # noqa: E402

from settings_guard import protect_settings                       # noqa: E402

_APP = QApplication.instance() or QApplication(sys.argv)
FAILS: list[str] = []
COUNT = 0
OLD_WINDOWS: list = []      # 关掉但没删的窗口，避免定时器打到已析构对象上
CANVAS = (100, 80)          # 每张「原图画面」的尺寸（整帧 = 同尺寸）


def wait(ms: int) -> None:
    t = QElapsedTimer()
    t.start()
    while t.elapsed() < ms:
        QApplication.processEvents()


def check(what: str, ok: bool, detail: str = "") -> None:
    global COUNT
    COUNT += 1
    if not ok:
        FAILS.append(what)
    print(f"  [{'OK' if ok else 'FAIL'}] {what} {detail}")


def block(canvas, x, y, w, h, arms=0) -> QPixmap:
    """一整个「原图画面」：透明底 + 身体块 +（可选）往右伸出去的手臂/尾巴。"""
    pm = QPixmap(canvas[0], canvas[1])
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.fillRect(x, y, w, h, QColor("#1565C0"))
    if arms:
        p.fillRect(x + w, y + 8, arms, 5, QColor("#1565C0"))   # 细长一条：外框会被它撑宽
    p.end()
    return pm


def tight(pm: QPixmap) -> QPixmap:
    """把一张画面紧切成它自己的 alpha 外框（模拟"原图已经被紧切过"）。"""
    from spritesheet.ui.transform import _alpha_bounds
    b = _alpha_bounds(pm)
    return pm.copy(b) if b is not None else pm


def positions_of(action):
    from spritesheet.ui.transform import compute_preview_layout
    return compute_preview_layout(action)[2]


def canvas_of(action):
    from spritesheet.ui.transform import compute_preview_layout
    layout = compute_preview_layout(action)
    return layout[0], layout[1]


def make_action(win, frames, name: str):
    """把 frames 当原图导进当前窗口，然后**取最后这几个元素**建动作。

    ``import_frames`` 是**追加**的：同一个窗口里第二次导入时，前面那批元素还在，
    必须从新元素开始选 —— 否则会拿到上一批（这个坑踩过：紧切夹具拿到的是整帧夹具的帧）。
    """
    win.image_viewer.import_frames(frames)
    wait(200)
    start = max(0, len(win.image_viewer._frames) - len(frames))
    action = win._create_animation_action(name, [], start, len(frames))
    win._install_animation_action(action)
    wait(300)
    return action


def fresh_window(win):
    """再开一个干净的窗口（多个夹具之间不互相污染）。

    老窗口**只 close、不 deleteLater**：它身上还挂着 400ms 的快照合并定时器等一堆东西，
    删掉之后定时器在已析构的对象上触发 → 整个进程 0xC0000409 崩掉（踩过）。
    """
    win.image_viewer.set_current_action(None)
    win.close()
    wait(120)
    from spritesheet.ui.main_window import MainWindow
    new = MainWindow()
    new.resize(1280, 800)
    new.show()
    wait(200)
    OLD_WINDOWS.append(win)          # 留着引用，别让 GC 把它连定时器一起带走
    return new


def main() -> int:
    restore = protect_settings()
    from spritesheet.settings import set_detect_min_area
    set_detect_min_area(4)
    from spritesheet.ui.main_window import MainWindow

    win = MainWindow()
    win.resize(1280, 800)
    win.show()
    wait(200)
    panel = win.preview.adjust_panel

    # ---------------------------------------------------------------- 段 1
    print("=== 1. 整帧原图（工具的真实流程）：自己判断出「可以精确」===")
    spec = [(20, 40, 20, 30, 0), (30, 40, 20, 30, 0), (40, 40, 20, 30, 18)]
    sources = [block(CANVAS, *s) for s in spec]
    action = make_action(win, sources, "整帧对齐测试")
    check("三个元素被检测出来（每帧画面一个元素）", len(action.frames) == 3,
          f"{len(action.frames)} 帧")
    sizes = [(action.frames[i].width(), action.frames[i].height()) for i in range(3)]
    check("帧就是整帧本身（工具的检测把一张原图当一个元素）", sizes == [CANVAS] * 3, f"{sizes}")
    reason = win._auto_align_reason(action)
    check("检测器说「可以精确」（不弹窗问用户）", reason == "", f"reason={reason!r}")
    data = win._source_anchor_data(action)
    check("精确路需要的数据齐全（每帧内容在原图坐标系里的框）", data is not None)
    local_boxes = data[0] if data else []
    check("每帧内容在原图坐标里就是整张原图 (0,0,100,80)",
          all(tuple(b) == (0, 0, CANVAS[0], CANVAS[1]) for b in local_boxes), f"{local_boxes}")
    panel._sync_align_buttons()
    check("悬停写着「精确」", "自动对齐（精确）" in panel.btn_auto_align.toolTip(),
          panel.btn_auto_align.toolTip().splitlines()[0])

    # ---------------------------------------------------------------- 段 2
    print("=== 2. 整帧这种工程本来就摆得正 → 按钮老实说「本来就在该在的位置上」===")
    check("按钮此刻可用", panel.btn_auto_align.isEnabled())
    panel.btn_auto_align.click()
    wait(400)
    check("没往工程里写一堆没用的 0 值", dict(action.frame_offsets) == {},
          f"{dict(action.frame_offsets)}")
    check("状态栏老实说「本来就在该在的位置上」",
          "本来就在该在的位置" in win.lbl_status.text(), win.lbl_status.text()[:70])

    # ---------------------------------------------------------------- 段 3
    print("=== 3. 原图已经被紧切过：自己认出来 + 提示「不准、建议手动核对」===")
    win = fresh_window(win)                # 换干净窗口：紧切夹具不跟整帧夹具搅在一起
    panel = win.preview.adjust_panel
    tight_sources = [tight(s) for s in sources]      # 三张紧切小图，尺寸各不相同（20/20/38 宽）
    action2 = make_action(win, tight_sources, "紧切对齐测试")
    check("紧切夹具建起来了（3 帧）", len(action2.frames) == 3, f"{len(action2.frames)} 帧")
    tsizes = [(action2.frames[i].width(), action2.frames[i].height()) for i in range(3)]
    check("紧切原图 → 帧尺寸各不相同（这就是会飘的那种工程）",
          len({s[0] for s in tsizes}) > 1, f"{tsizes}")
    reason2 = win._auto_align_reason(action2)
    check("检测器自己认出「原图已经被紧切」", "紧切" in reason2, f"reason={reason2!r}")
    panel._sync_align_buttons()
    tip2 = panel.btn_auto_align.toolTip()
    check("悬停改成「只能估算」并写明原因、写明建议手动",
          "只能估算" in tip2 and reason2 in tip2 and "手动核对" in tip2,
          tip2.splitlines()[0])

    # 模态弹窗在 offscreen 里点不了：把确认换成桩，分别验「取消」和「用估算值」两条路
    asked: list[str] = []
    win._confirm_estimate_align = lambda why: (asked.append(why), False)[1]
    before2 = dict(action2.frame_offsets)
    before_pos = positions_of(action2)
    spread_before = max(p[0] for p in before_pos) - min(p[0] for p in before_pos)
    print(f"  对齐前各帧落点 x：{[p[0] for p in before_pos]}（跨度 {spread_before}px = 飘的幅度）")
    panel.btn_auto_align.click()
    wait(300)
    check("先弹了提示（内容就是那条原因）", bool(asked) and asked[0] == reason2, f"{asked}")
    check("点「取消」→ 偏移一点都不动", dict(action2.frame_offsets) == before2)
    check("点「取消」会说出来", "已取消" in win.lbl_status.text(), win.lbl_status.text()[:60])

    win._confirm_estimate_align = lambda why: True
    panel.btn_auto_align.click()
    wait(700)
    got2 = dict(action2.frame_offsets)
    print(f"  估算出来的逐帧偏移：{got2}")
    check("点「用估算值」→ 偏移写进去了", got2 != before2, f"{got2}")
    check("被手臂撑宽的那一帧按身体对齐（x 补 +9 = (38−20)/2）",
          got2.get(2, (0, 0))[0] == 9, f"{got2.get(2)}")
    after_pos = positions_of(action2)
    spread_after = max(p[0] for p in after_pos) - min(p[0] for p in after_pos)
    print(f"  对齐后各帧落点 x：{[p[0] for p in after_pos]}（跨度 {spread_after}px）")
    check("飘的幅度真的小了（9px → ≤1px）", spread_before == 9 and spread_after <= 1,
          f"{spread_before} → {spread_after}")
    check("状态栏说明这次是估的、建议手动核对",
          "估算" in win.lbl_status.text() and "手动核对" in win.lbl_status.text(),
          win.lbl_status.text()[:90])

    # ---------------------------------------------------------------- 段 4
    print("=== 4. 用户口径：只改偏移，别的都没变 + 画布装得下 ===")
    check("锚点没变", tuple(action2.anchor) == (0.5, 0.5), f"{tuple(action2.anchor)}")
    check("padding 没变", action2.padding == 0, f"{action2.padding}")
    after_pixmaps = [(action2.frames[i].width(), action2.frames[i].height()) for i in range(3)]
    check("帧图像本身没变（还是原来那几张、尺寸一样）", after_pixmaps == tsizes,
          f"{after_pixmaps}")
    canvas_w, canvas_h = canvas_of(action2)
    inside = all(0 <= p[0] and 0 <= p[1] and p[0] + p[2] <= canvas_w
                 and p[1] + p[3] <= canvas_h for p in after_pos)
    check("对齐后每一帧都还在画布内（没被切掉）", inside,
          f"画布={canvas_w}x{canvas_h} 落点={after_pos}")

    # ---------------------------------------------------------------- 段 5
    print("=== 5. 连点两次结果一样（幂等）===")
    snapshot = dict(action2.frame_offsets)
    win._confirm_estimate_align = lambda why: True
    panel.btn_auto_align.click()
    wait(700)
    check("再点一次偏移没变（不会越点越偏）", dict(action2.frame_offsets) == snapshot,
          f"{dict(action2.frame_offsets)}")

    # ---------------------------------------------------------------- 段 6
    print("=== 6. 一条撤销退回对齐前 ===")
    win._do_undo()
    wait(300)
    check("撤销后偏移回到对齐前", dict(action2.frame_offsets) == before2,
          f"{dict(action2.frame_offsets)}")
    win._do_redo()
    wait(300)
    check("重做又对齐上", dict(action2.frame_offsets) == snapshot,
          f"{dict(action2.frame_offsets)}")

    # ---------------------------------------------------------------- 段 7
    print("=== 7. 空帧不参与 + 没得对齐时按钮禁用并写明原因 ===")
    win = fresh_window(win)                # 再换干净窗口，夹具之间不搅在一起
    panel = win.preview.adjust_panel
    action3 = make_action(win, [tight(block(CANVAS, 20, 40, 20, 30)),
                                tight(block(CANVAS, 30, 40, 20, 30)),
                                tight(block(CANVAS, 40, 40, 20, 30, 18))], "空帧测试")
    check("空帧夹具建起来了（3 帧）", len(action3.frames) == 3, f"{len(action3.frames)} 帧")
    if len(action3.frames) < 2:                 # 夹具塌了就明说，别拿假绿糊过去
        check("夹具够用才能继续验空帧", False, f"{len(action3.frames)} 帧")
    empty = QPixmap(CANVAS[0], CANVAS[1])
    empty.fill(Qt.GlobalColor.transparent)
    action3.frames[1] = empty
    action3.frame_rects[1] = (0, 0, CANVAS[0], CANVAS[1])
    action3.frame_parts[1] = []
    action3.frame_offsets = {}
    action3.invalidate_transforms()          # 换过帧图必须让变换缓存失效，否则还按老图判空
    check("第 1 帧确实是空的（回归自检）",
          not __import__("spritesheet.ui.transform", fromlist=["x"]).frame_has_content(action3, 1))
    win._confirm_estimate_align = lambda why: True      # 紧切夹具走估算路，先过确认这关
    win._on_auto_align()
    wait(700)
    got3 = dict(action3.frame_offsets)
    check("带画面的帧照常对齐（说明这条断言不是空的）", 2 in got3, f"{got3}")
    check("全透明的那帧不参与对齐（不给它写偏移）", 1 not in got3, f"{got3}")

    win.preview.set_action(None)
    panel._sync_align_buttons()
    check("没有动作 → 禁用，提示写明原因",
          not panel.btn_auto_align.isEnabled()
          and "先选一个动作" in panel.btn_auto_align.toolTip(),
          panel.btn_auto_align.toolTip())
    one = make_action(win, [block(CANVAS, 20, 40, 20, 30)], "单帧测试")
    win._install_animation_action(one)
    wait(200)
    check("只有一帧 → 禁用，提示说「谈不上对齐」",
          not panel.btn_auto_align.isEnabled()
          and "谈不上对齐" in panel.btn_auto_align.toolTip(),
          panel.btn_auto_align.toolTip())
    check("第二颗「相似帧」是开关，且看的是全图元素 —— 没动作也能按（用户要求直接看原图）",
          panel.btn_similar_frames.isCheckable()
          and panel.btn_similar_frames.isEnabled()
          and "元素框右下角" in panel.btn_similar_frames.toolTip(),
          panel.btn_similar_frames.toolTip())
    check("第三颗是「抽帧」且已实装（能按、四档说明在、撤回口径在、禁用原因也接上了）",
          panel.btn_extract.isEnabled() and "抽帧3/5" in panel.btn_extract.toolTip()
          and "撤回" in panel.btn_extract.toolTip()
          and callable(getattr(panel, "extract_enable_provider", None)),
          panel.btn_extract.toolTip())

    print("=== 8. 精灵图横竖均分：每格 = 正常那一帧，紧切框在格里的落点就是原图里的落点 ===")
    win = fresh_window(win)
    panel = win.preview.adjust_panel
    COLS, ROWS, CW, CH = 3, 3, 60, 70
    atlas = QPixmap(COLS * CW, ROWS * CH)
    atlas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(atlas)
    plan = []                                   # (列, 行, 帧内宽, 帧内高, 格内 x, 格内 y)
    for row in range(ROWS):
        for col in range(COLS):
            # 内容：最长边 41~44（> 设置里的噪点阈值 30，不会被当碎片滤掉），
            # 且 dx≤16 + w≤44 = 60 刚好不出格、dy≤22 + h≤44 = 66 也在 70 以内
            w = 41 + ((col + row) % 3)
            h = 42 + ((col * 2 + row) % 3)
            dx = 8 + 4 * col
            dy = 10 + 6 * row
            painter.fillRect(col * CW + dx, row * CH + dy, w, h, QColor("#1565C0"))
            plan.append((col, row, w, h, dx, dy))
    painter.end()
    win.image_viewer.set_pixmap(atlas)          # 用户拖单张图集的入口（走自动检测）
    for _ in range(50):                         # 检测在后台线程里跑，等它 settle
        if len(win.image_viewer._frames) >= 9:  # （固定 wait 会偶发"只切出 7 个"的假失败）
            break
        wait(200)
    wait(200)
    check("图集被切成 9 个元素（3×3 格各一个）",
          len(win.image_viewer._frames) == 9, f"{len(win.image_viewer._frames)} 个")
    action4 = win._create_animation_action("均分图集测试", [], 0, 9)
    win._install_animation_action(action4)
    wait(600)
    from spritesheet.ui.transform import detect_sheet_grid, grid_shape
    index_of = detect_sheet_grid(action4.frame_rects, atlas.width(), atlas.height())
    check("判定出「横竖均分 = 3×3」", grid_shape(index_of) if index_of else None == (3, 3),
          f"{grid_shape(index_of) if index_of else None}")
    check("每个元素都被认到正确的那一格", bool(index_of) and all(
        index_of[i] == (c, r) for i, (c, r, *_rest) in enumerate(plan)),
        f"{index_of}")
    before_pos = positions_of(action4)
    # 防呆：均分那条路**不该弹任何确认**。万一它没走成、退到估算那条路，这里直接拒掉，
    # 免得模态弹窗把 offscreen 测试挂死（那样只会看到超时，看不到"走错路了"）。
    win._confirm_estimate_align = lambda why: False
    panel.btn_auto_align.click()
    wait(600)
    print("  状态栏：", win.lbl_status.text()[:80])
    check("状态栏写明走的是均分格那条路、不是估的",
          "均分格（3×3）" in win.lbl_status.text() and "不是估的" in win.lbl_status.text(),
          win.lbl_status.text()[:90])
    after_pos = positions_of(action4)
    # 真值：每帧内容在"格坐标系"里的落点 = 格内 (dx, dy)=plan[4]/plan[5]；
    # 对齐后落点必须等于它 + 同一个常数（整体平移无所谓，跨帧不一致就是没对齐）
    kx = [p[0] - r[4] for p, r in zip(after_pos, plan)]
    ky = [p[1] - r[5] for p, r in zip(after_pos, plan)]
    check("每帧都落回它那一格里的原位（误差是同一个常数）",
          max(kx) - min(kx) == 0 and max(ky) - min(ky) == 0,
          f"x 异 {max(kx)-min(kx)}，y 异 {max(ky)-min(ky)}")
    check("横向真实位移被保住（不是把各帧拉齐了）",
          max(r[4] for r in plan) - min(r[4] for r in plan) == 8,
          f"格内 x 跨度 {max(r[4] for r in plan) - min(r[4] for r in plan)}（夹具里是 8~16）")
    check("锚点/padding 没动",
          tuple(action4.anchor) == (0.5, 0.5) and action4.padding == 0,
          f"{tuple(action4.anchor)} / {action4.padding}")
    snap4 = dict(action4.frame_offsets)
    panel.btn_auto_align.click()
    wait(400)
    check("连点两次结果一样", dict(action4.frame_offsets) == snap4, f"{snap4}")
    win._do_undo()
    wait(300)
    check("一条 Ctrl+Z 退回均分对齐前", dict(action4.frame_offsets) == {},
          f"{dict(action4.frame_offsets)}")

    print("=== 9. 不是均分的图：判定不出来 → 老实说，不硬猜 ===")
    win = fresh_window(win)
    scattered = QPixmap(180, 180)
    scattered.fill(Qt.GlobalColor.transparent)
    p2 = QPainter(scattered)
    for k, (x, y) in enumerate([(10, 12), (48, 40), (95, 20), (30, 90), (120, 110),
                                (70, 150), (150, 60), (16, 150), (110, 78)]):
        p2.fillRect(x, y, 20 + k % 3 * 5, 26 + k % 2 * 6, QColor("#1565C0"))
    p2.end()
    win.image_viewer.set_pixmap(scattered)
    wait(1200)
    a5 = win._create_animation_action("散图测试", [], 0, len(win.image_viewer._frames))
    win._install_animation_action(a5)
    wait(400)
    check("散着摆的图判不出均分（返回 None，不硬猜）",
          detect_sheet_grid(a5.frame_rects, scattered.width(), scattered.height()) is None)

    win.image_viewer.set_current_action(None)
    for w in OLD_WINDOWS + [win]:
        w.close()
    # 光 close() 不够：headless 下解释器退出时才回收原生对象，实测会随机 0xC0000005。
    # 复用上面循环的 w，把每个窗口都交给 Qt 自己析构，再跑事件让 deleteLater 落地。
    for w in OLD_WINDOWS + [win]:
        w.deleteLater()
    for _ in range(3):
        _APP.processEvents()
    _APP.quit()
    _APP.processEvents()
    del win, action, action2, action3, sources, tight_sources
    OLD_WINDOWS.clear()
    gc.collect()
    restore()

    print()
    if FAILS:
        print(f"AUTO ALIGN FAILED: {len(FAILS)}/{COUNT}")
        for f in FAILS:
            print(f"  - {f}")
        return 1
    print(f"AUTO ALIGN OK  {COUNT}/{COUNT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())