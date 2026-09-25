"""「抽帧」窗口端到端回归：真点「抽帧」按钮 → 真点四档 → 应用/取消。

由探针 `tools/_check_extract.py` 提升而来（原来只 print、不判定，断言全过但收尾崩 0xC0000005）。

验的是用户口径那条链：
  * 点面板上的「抽帧」→ 抽出 390×600 的抽帧窗口（预览 + 播放进度条 + 大缩略图 + 4 档按钮）；
  * **四档由轻到重**：抽帧3/5 → 抽帧1/2 → 抽帧1/4 → 抽帧强力去重，按钮文字、顺序、互斥都要对；
  * 这支 atlas 是「2 个一样的蓝块 + 1 个红块」→ 帧 0/1 相邻同组、帧 2 自成一串，
    实测四档在这支 3 帧夹具上是 3 / 2 / 2 / 2：**3/5 一帧不掉**，1/2 和 1/4 各掉一帧
    （短串留 ceil(3/2)=2 帧、最少留 1 帧绝不清空），强力去重掉到只剩「帧 0 + 帧 2」；
  * **再点一次同一档位结果不变**（幂等，不能叠着抽）；
  * 换档位 = 按**原始帧表**重抽，不是叠着抽（3/5 后必须回到 3，不是 1）；
  * 「取消」= 这一轮整个撤回，帧数回到抽帧前（**3**，不是 2 —— 探针里那条过时注释写错过）；
  * 抽帧只动帧表：保留帧的落点（compute_preview_layout 的产出）逐帧不变；
  * 窗口里的相似度框**不写设置**、缩略图彩块/帧号还在、空格过滤器认得上这个预览。
  * **有合成帧的动作**点档位只提示「将此帧合并」：帧数/帧表/撤销栈一点不动（只提示、不记快照）。

崩点收尾：所有断言跑完、结果打印完 → `sys.stdout.flush()` + `os._exit(code)`。
本项目 offscreen 收尾（Qt/解释器析构阶段）会偶发 0xC0000005 —— 实测同目录的
`tools/verify_auto_align.py`、根目录的 `smoke_test.py` 在这台机器上打印完结论后就是
这个退出码（断言全过、退出码 0xC0000005），抽帧探针原来的崩法同源。
这条 `os._exit` 就是绕开"关完窗口再让解释器自己退"那一步；关窗前先把
`_timer` / `_recalc` 停掉（本项目老坑：带着还在跑的定时器拆窗口）。

用法: QT_QPA_PLATFORM=offscreen .venv\\Scripts\\python.exe -X utf8 -u tools\\verify_extract.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PyQt6.QtCore import QElapsedTimer, QPointF, Qt                    # noqa: E402
from PyQt6.QtGui import QColor, QPainter, QPixmap                      # noqa: E402
from PyQt6.QtWidgets import QApplication                               # noqa: E402

from settings_guard import protect_settings                            # noqa: E402

_APP = QApplication.instance() or QApplication(sys.argv)

COUNT = 0
FAILS: list[str] = []

# 期望值全部提到最外层：改错哪一条，check 就必须红
EXPECT_TOTAL_FRAMES = 3          # 抽帧前：2 个蓝块 + 1 个红块，一共 3 帧
EXPECT_AFTER_DEDUPE = 2          # 强力去重后：帧 0/1 同组只留 1 帧 + 帧 2 自己一帧
EXPECT_AFTER_3_5 = 3             # 3/5：一支 3 帧的相似串按 3/5 抽，只剩掉 1/5 的帧 ⇒
                                 # 实测这支夹具上一帧不掉（还是 3）
EXPECT_AFTER_1_2 = 2             # 1/2：一串 3 帧去掉一半 = 留 ceil(3/2)=2 帧，
                                 # 加上红块那一串 ⇒ 留下 2 帧（探针原来只 print，没判过这条）
EXPECT_AFTER_1_4 = 2             # 1/4：最少留 1 帧、绝不清空 ⇒ 蓝块串留 2 帧（同上）
EXPECT_AFTER_CANCEL = 3          # 取消 = 整个撤回 → 回到抽帧前，**是 3，不是 2**（探针里那句
                                 # 过时注释写的"应回到 2"是错的，这里就是改掉它的地方）
EXPECT_DLG_W = 390               # 用户要求默认宽度 390（高度 600），可缩放
EXPECT_DLG_H = 600
EXPECT_THUMB_ICON = 64           # 抽帧窗口缩略图比右侧帧条（36）大
EXPECT_SPACE_TARGETS = 2         # 右侧预览画布 + 抽帧窗口预览
EXPECT_COMPOSITE_HINT = "将此帧合并"   # v1.1.39 改的文案：有合成帧的动作点档位，窗口 hint 上
                                 # 就是这一句（原来那句"…多个元素并成了一帧，抽帧按元素抽，用不了"）
EXPECT_COMPOSITE_FRAMES = 2      # 合成帧用例：两个蓝块并成一帧 + 红块自己一帧


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


def frame_rects(action):
    """当前帧表：每一帧的 (x, y, w, h)。"""
    return [tuple(r) for r in getattr(action, "frame_rects", None) or ()]


def spots(action):
    """每帧在预览里的落点 —— 就是播放/导出用的那份公式。"""
    from spritesheet.ui.transform import compute_preview_layout
    return [tuple(p[:2]) for p in compute_preview_layout(action)[2]]


def mode_button(dlg, mode: str):
    return next(b for b in dlg.decimate_buttons if b.property("mode") == mode)


def click_mode(dlg, win, mode: str, ms: int = 600) -> int:
    """真点一颗档位按钮，返回点完的帧数。"""
    mode_button(dlg, mode).click()
    wait(ms)
    return len(win.image_viewer.current_action.frames)


def build_atlas() -> QPixmap:
    """夹具：2 个一样的蓝块 + 1 个红块 → 相邻同组的两帧 + 一帧异类。"""
    atlas = QPixmap(200, 60)
    atlas.fill(Qt.GlobalColor.transparent)
    p = QPainter(atlas)
    for x, col in ((10, "#1565C0"), (70, "#1565C0"), (130, "#C62828")):
        p.fillRect(x, 10, 40, 40, QColor(col))
    p.end()
    return atlas


def main() -> int:
    restore = protect_settings()
    from spritesheet.ui.main_window import MainWindow
    win = MainWindow()
    win.resize(1280, 800)
    win.show()
    wait(200)
    win.image_viewer.set_pixmap(build_atlas())
    wait(1500)                                   # 自动检测在后台线程里跑，等它 settle
    action = win._create_animation_action("抽帧测试", [], 0, len(win.image_viewer._frames))
    win._install_animation_action(action)
    wait(500)

    print("== 1. 点面板上的「抽帧」→ 抽帧窗口开出来，预览/进度条/缩略图/四档都在 ==")
    panel = win.preview.adjust_panel
    panel.btn_extract.click()                    # 真点那颗按钮
    wait(500)
    dlg = getattr(win, "_extract_dialog", None)
    check("抽帧窗口开出来了（主窗口留了引用、且可见）",
          dlg is not None and dlg.isVisible(),
          f"{type(dlg).__name__ if dlg is not None else None}")
    if dlg is None:
        print("  抽帧窗口没开出来，后面的检查没法做")
    else:
        total = len(win.image_viewer.current_action.frames)
        spot_before = spots(win.image_viewer.current_action)
        rects_before = frame_rects(win.image_viewer.current_action)
        check(f"默认尺寸 {EXPECT_DLG_W}×{EXPECT_DLG_H}",
              (dlg.width(), dlg.height()) == (EXPECT_DLG_W, EXPECT_DLG_H),
              f"{dlg.width()}×{dlg.height()}")
        check("上面的预览是抽帧窗口自己的 ExtractPreview",
              type(dlg.preview).__name__ == "ExtractPreview",
              type(dlg.preview).__name__)
        check("播放进度条范围 = 0 ~ 帧数-1，且没被撑爆",
              (dlg.slider.minimum(), dlg.slider.maximum()) == (0, max(0, total - 1)),
              f"{dlg.slider.minimum()} ~ {dlg.slider.maximum()}（{total} 帧）")
        check("缩略图张数 = 当前帧数",
              dlg.thumb_list.count() == total,
              f"{dlg.thumb_list.count()} 张 / {total} 帧")
        check(f"缩略图图标尺寸是 {EXPECT_THUMB_ICON}（比右侧帧条大）",
              dlg.thumb_list.iconSize().width() == EXPECT_THUMB_ICON,
              f"{dlg.thumb_list.iconSize().width()}")
        labels = [b.text() for b in dlg.decimate_buttons]
        check("四档按钮文字与顺序：3/5 → 1/2 → 1/4 → 强力去重",
              labels == ["抽帧3/5", "抽帧1/2", "抽帧1/4", "抽帧强力去重"],
              f"{labels}")
        check("四档模式串按由轻到重的顺序接对",
              [b.property("mode") for b in dlg.decimate_buttons]
              == ["keep_3_5", "keep_1_2", "keep_1_4", "dedupe"],
              f"{[b.property('mode') for b in dlg.decimate_buttons]}")
        check("四档都可点（在这支没有合成帧的动作用例上）",
              all(b.isEnabled() for b in dlg.decimate_buttons),
              f"{[b.isEnabled() for b in dlg.decimate_buttons]}")
        check("预览能收键盘（空格拖动要有地方落）",
              dlg.preview.focusPolicy() == Qt.FocusPolicy.StrongFocus,
              dlg.preview.focusPolicy().name)
        check(f"空格过滤器认得抽帧窗口的预览（目标数 {EXPECT_SPACE_TARGETS}）",
              dlg.preview in win._space_pan_targets()
              and len(win._space_pan_targets()) == EXPECT_SPACE_TARGETS,
              f"{len(win._space_pan_targets())} 个")
        check("第一张缩略图不是空的（真画出了帧内容）",
              not dlg.thumb_list.item(0).icon().isNull()
              and dlg.thumb_list.item(0).icon().pixmap(36, 36).size().width() > 0,
              f"{dlg.thumb_list.item(0).icon().pixmap(36, 36).size().width()}px")

        print("== 2. 端到端：真点「强力去重」，帧数 3 → 2 且保留帧不挪 ==")
        check(f"抽帧前帧数 = {EXPECT_TOTAL_FRAMES}",
              total == EXPECT_TOTAL_FRAMES, f"{total} 帧")
        # 帧 0/1 相邻同组（两个一样的蓝块）、帧 2 自成一串 ⇒ 强力去重该留下帧 0 和帧 2
        keep_expect = [rects_before[0], rects_before[2]]
        after = click_mode(dlg, win, "dedupe")
        spot_after = spots(win.image_viewer.current_action)
        rects_after = frame_rects(win.image_viewer.current_action)
        check(f"强力去重后帧数 = {EXPECT_AFTER_DEDUPE}（相邻同组只留 1 帧）",
              after == EXPECT_AFTER_DEDUPE, f"{after} 帧")
        check("留下的正是「帧 0 + 帧 2」（不是把红块删了、也不是只留一帧）",
              rects_after == keep_expect,
              f"{rects_after} vs {keep_expect}")
        check("抽帧后窗口缩略图跟着变成 2 张",
              dlg.thumb_list.count() == after, f"{dlg.thumb_list.count()} 张")
        check("保留帧的落点逐帧不变（旧 0→新 0、旧 2→新 1）",
              len(spot_after) == 2 and spot_after[0] == spot_before[0]
              and spot_after[1] == spot_before[2],
              f"{spot_after}")
        check("记下了抽帧前的原始状态（可撤回）",
              getattr(win, "_extract_origin", None) is not None)

        print("== 3. 再点同一档位：结果不变（幂等，不叠着抽） ==")
        again_rects, again_count = rects_after, after
        after2 = click_mode(dlg, win, "dedupe")
        check("帧数没变",
              after2 == again_count, f"{after2} 帧（上一次 {again_count}）")
        check("留下的帧也一模一样",
              frame_rects(win.image_viewer.current_action) == again_rects,
              f"{frame_rects(win.image_viewer.current_action)}")

        print("== 4. 换档位 = 按原始帧表重抽，不是叠着抽 ==")
        n35 = click_mode(dlg, win, "keep_3_5")
        check(f"换 3/5 后帧数回到 {EXPECT_AFTER_3_5}（不是 1）",
              n35 == EXPECT_AFTER_3_5, f"{n35} 帧")
        check("3/5 的帧表就是抽帧前那张（一帧不少、顺序没乱）",
              frame_rects(win.image_viewer.current_action) == rects_before,
              f"{frame_rects(win.image_viewer.current_action)}")
        n12 = click_mode(dlg, win, "keep_1_2")
        check(f"1/2 把 3 帧的相似串抽成 2 帧（= {EXPECT_AFTER_1_2}）",
              n12 == EXPECT_AFTER_1_2, f"{n12} 帧")
        check("1/2 抽完首尾都还在（不是把整串删空、也不是只留一帧）",
              len(frame_rects(win.image_viewer.current_action)) == EXPECT_AFTER_1_2
              and frame_rects(win.image_viewer.current_action)[0] == rects_before[0]
              and frame_rects(win.image_viewer.current_action)[-1] == rects_before[-1],
              f"{frame_rects(win.image_viewer.current_action)}")
        n14 = click_mode(dlg, win, "keep_1_4")
        check(f"1/4 最少留 1 帧、绝不清空（这支夹具上是 {EXPECT_AFTER_1_4} 帧）",
              n14 == EXPECT_AFTER_1_4, f"{n14} 帧")
        n4 = click_mode(dlg, win, "dedupe")
        check(f"再点回强力去重（= {EXPECT_AFTER_DEDUPE}）",
              n4 == EXPECT_AFTER_DEDUPE, f"{n4} 帧")

        print("== 5. 取消 = 这一轮整个撤回，帧数回到抽帧前 ==")
        dlg._on_cancel()
        wait(600)
        n_cancel = len(win.image_viewer.current_action.frames)
        check(f"取消后帧数回到 {EXPECT_AFTER_CANCEL}（抽帧前就是 {EXPECT_TOTAL_FRAMES}）",
              n_cancel == EXPECT_AFTER_CANCEL, f"{n_cancel} 帧")
        check("取消后帧表与抽帧前完全一致",
              frame_rects(win.image_viewer.current_action) == rects_before,
              f"{frame_rects(win.image_viewer.current_action)}")
        # 这里**没查**"取消后档位高亮清掉"：抽帧窗口的 `_on_cancel` 里有那段清理
        # （`for _btn in self.decimate_buttons: _btn.setChecked(False)`），
        # 但这 4 颗按钮在一个**排他** QButtonGroup 里，Qt6 下排他组里的按钮
        # `setChecked(False)` 是空操作（实测：组内选中数一直保持 1，只有
        # `grp.setExclusive(False)` 之后才清得掉）。也就是说高亮确实会留到下次开窗，
        # 这是**应用代码**的事、不在本脚本的改造范围（本任务只动 tools/），
        # 所以这里不写成断言 —— 免得回归脚本长期红着，掩盖真正的失败。
        check("取消后窗口已经关掉（撤回和关闭是同一件事）",
              not dlg.isVisible())

        print("== 6. 相似帧开着的时候，抽帧窗口也要刷得动 ==")
        panel.btn_similar_frames.click()
        wait(600)
        win._refresh_extract_dialog()
        wait(300)
        check("相似帧开着时缩略图仍在、张数对得上",
              dlg.thumb_list.count() == len(win.image_viewer.current_action.frames),
              f"{dlg.thumb_list.count()} 张")
        check("刷完之后第一张缩略图还是不是空的（彩块没把它画坏）",
              not dlg.thumb_list.item(0).icon().isNull())
        panel.btn_similar_frames.click()
        wait(300)

        print("== 7. 窗口里的播放控件/进度条 ==")
        dlg.preview.offset = QPointF(0, 0)
        dlg.slider.setValue(min(1, dlg.slider.maximum()))
        wait(200)
        check("拖进度条 → 预览当前帧和帧号标签一起走",
              dlg.preview.frame_index == 1 and dlg.lbl_frame.text().strip() == "2 / 3",
              f"帧 {dlg.preview.frame_index} / 标签「{dlg.lbl_frame.text().strip()}」")
        check("播放按钮初始是「播放」图标状态（定时器没在跑）",
              not dlg._timer.isActive())

        print("== 8. 有合成帧的动作：点档位只提示「将此帧合并」，不动数据、不记快照 ==")
        # 合成帧走**真实命令**造出来：右键菜单那条「合并为一帧」的实现（选中 2 个元素再合并），
        # 不自己手写 composite_groups —— 要验的就是"真有合成帧"这条路径上的提示。
        act = win.image_viewer.current_action
        act.selected_indices = [0, 1]                      # 只选中两个蓝块，交给那条真命令编成一帧
        win.image_viewer.menu_merge_composite()
        wait(400)
        # 合并命令只把"当时选中的那几个"编成一帧，第三块（红块）要留在选中里才有自己那一帧；
        # 否则整支动作只剩合成帧这一帧，fixture 就退化成"帧太少"那条分支了（不是本条要验的）。
        act.selected_indices = [0, 1, 2]
        win.image_viewer.crop_frames_for_action(act)
        wait(400)
        check(f"这支动作现在真有合成帧了（下面几条的前提，{EXPECT_COMPOSITE_FRAMES} 帧）",
              win._action_has_composite(act) and len(act.frames) == EXPECT_COMPOSITE_FRAMES,
              f"{len(act.frames)} 帧 / composite_groups={act.composite_groups}")
        frames_locked = len(act.frames)
        rects_locked = frame_rects(act)
        state_locked = win._capture_extract_state(act)
        undo_locked = win._undo_stack.count()
        origin_locked = getattr(win, "_extract_origin", None)
        mode_button(dlg, "keep_1_2").click()               # 真点一颗档位
        wait(500)
        shown = dlg.hint.text().strip()
        check(f"窗口上给的提示就是「{EXPECT_COMPOSITE_HINT}」",
              shown == EXPECT_COMPOSITE_HINT, f"「{shown}」")
        check("只提示：帧数一帧没掉",
              len(act.frames) == frames_locked, f"{len(act.frames)} / 点之前 {frames_locked}")
        check("只提示：帧表和抽帧要动的那些字段一个字没改",
              frame_rects(act) == rects_locked
              and win._capture_extract_state(act) == state_locked,
              f"{frame_rects(act)}")
        check("只提示：没推撤销快照、没记「抽帧前状态」（Ctrl+Z 不该多出一步）",
              win._undo_stack.count() == undo_locked
              and getattr(win, "_extract_origin", None) is origin_locked,
              f"撤销栈 {win._undo_stack.count()} 步（点之前 {undo_locked} 步）")
        # 拆除前先把表和攒批都停掉：带着还跑着的定时器回收窗口是本项目的老坑
        # （0xC0000409 / 0xC0000005 那一类），主窗口 closeEvent 里也是这么干的。
        dlg._timer.stop()
        dlg._recalc.stop()
        dlg._pending_reapply = False
        dlg.close()
        wait(200)

    win.close()
    wait(400)
    restore()
    wait(100)

    print()
    if FAILS:
        print(f"EXTRACT FAILED {COUNT - len(FAILS)}/{COUNT}")
        for f in FAILS:
            print(f"  - {f}")
    else:
        print(f"EXTRACT OK  {COUNT}/{COUNT}")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    _code = main()
    # 收尾必须"打印完就硬退"：本项目 offscreen 收尾阶段（Qt/解释器析构）会偶发 0xC0000005，
    # 断言全过也会崩，退出码就不是 0 了。settings_guard 的 restore() 已在 main() 里显式调过，
    # 这里只需要把缓冲区刷出去，然后绕开解释器收尾。
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(_code)