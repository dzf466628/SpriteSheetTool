"""删除此帧回归：预览/抽帧缩略图右键删除 + 取消还原语义 + 滚动条悬浮动画。

覆盖三个新需求：
1. 缩略图条横向滚动条自绘加高 + hover 动画（HoverHScrollBar：固定高 16、
   150ms OutCubic 驱动 hover_t，进预览区即生效）；
2. 预览缩略图右键「删除此帧」：走抽帧同一套删帧管道（派生源重排 + 重建帧 +
   偏移补偿），Ctrl+Z 可撤回，唯一帧时禁用；
3. 抽帧窗口右键同款删除，套现有 _extract_origin：点「取消」还原、确定/关窗定案。

Usage: .venv\\Scripts\\python.exe tools\\verify_delete_frame.py
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEasingCurve, QPoint, QRect, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QPainter, QPixmap  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

_ = QApplication(sys.argv)  # 必须持有实例防 GC

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from spritesheet.ui.main_window import MainWindow  # noqa: E402
from spritesheet.ui.models import Action  # noqa: E402
from spritesheet.ui.transform import compute_preview_layout  # noqa: E402
from spritesheet.ui.widgets import HoverHScrollBar  # noqa: E402

FAILS: list[str] = []
PASSED = [0]


def check(name, ok, detail=""):
    if ok:
        PASSED[0] += 1
        print(f"  [OK]   {name}")
    else:
        FAILS.append(name)
        print(f"  [FAIL] {name} {detail}")


def add_action(win, name, n=3, **kw):
    """造 n 个色块元素并裁成一个 n 帧动作（与 verify_anim_settings 同款 helper）。"""
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
    return a


def main() -> int:
    win = MainWindow()
    win.show()
    app = QApplication.instance()
    app.processEvents()

    # ---- 1. 滚动条：自绘 + 加高 + hover 动画 ----
    bar = HoverHScrollBar()
    check("滚动条条带 8px（黑底贴合缩略图）",
          bar.minimumHeight() == 8 and bar.maximumHeight() == 8)
    check("动画 150ms OutCubic",
          bar._anim.duration() == 150
          and bar._anim.easingCurve().type() == QEasingCurve.Type.OutCubic)
    bar._set_hover_t(1.0)
    check("hover 进度可驱动", abs(bar._hover_t - 1.0) < 1e-9)
    sb = win.preview.thumb_list.horizontalScrollBar()
    check("预览缩略图条已换自绘滚动条",
          sb is not None and isinstance(sb, HoverHScrollBar), type(sb).__name__)
    tl = win.preview.thumb_list
    check("缩略图条高容纳 48 格+8 条带（黑底贴合图标）",
          tl.height() >= tl.gridSize().height() + 8,
          f"list={tl.height()} grid={tl.gridSize().height()}")

    # ---- 2. 预览右键菜单：项文本与禁用逻辑 ----
    a3 = add_action(win, "动作三帧", 3)
    win.preview.set_action(a3)
    app.processEvents()
    built = win.preview._build_thumb_menu(QPoint(4, 4))
    check("菜单构建成功", built is not None)
    menu, act_del, row, can_del = built
    check("菜单项=删除此帧且可删", act_del.text() == "删除此帧" and can_del and act_del.isEnabled(),
          f"text={act_del.text()} can={can_del}")
    a1f = add_action(win, "唯一帧", 1)
    win.preview.set_action(a1f)
    app.processEvents()
    built1 = win.preview._build_thumb_menu(QPoint(4, 4))
    _, act1, _, can1 = built1
    check("唯一帧时删除禁用", can1 is False and not act1.isEnabled())

    # ---- 3. 删帧核心：逐帧字典重排 + 唯一帧/越界拒绝 + Ctrl+Z 撤回 ----
    a5 = add_action(win, "五帧", 5)
    a5.frame_durations = {3: 120}
    a5.frame_mirror_h = {4}
    a5.frame_offsets = {1: (2, 3)}
    ok = win._delete_action_frame(a5, 2)
    check("删中间帧返回 True", ok is True)
    check("帧数 5→4", len(a5.frames) == 4, str(len(a5.frames)))
    check("frame_rects 同步", len(a5.frame_rects) == 4)
    check("逐帧字典重排（3→2）", a5.frame_durations == {2: 120}, str(a5.frame_durations))
    check("镜像集合重排（4→3）", a5.frame_mirror_h == {3}, str(a5.frame_mirror_h))
    check("删后偏移保留（1 不变）", a5.frame_offsets == {1: (2, 3)}, str(a5.frame_offsets))
    win._do_undo()
    check("Ctrl+Z 撤回删帧（帧数回 5）", len(a5.frames) == 5, str(len(a5.frames)))
    win._do_redo()
    check("Ctrl+Y 重做再删", len(a5.frames) == 4, str(len(a5.frames)))
    win._do_undo()  # 归位，避免影响后续
    check("越界行拒绝", win._delete_action_frame(a5, 99) is False)
    while len(a5.frames) > 1:               # 一路删到只剩 1 帧
        win._delete_action_frame(a5, 0)
    check("删到只剩 1 帧后拒绝", win._delete_action_frame(a5, 0) is False,
          f"len={len(a5.frames)}")

    # ---- 4. 偏移补偿：删掉"最大帧"（旋转 90 变成 30x40）后保留帧落点不变 ----
    a3b = add_action(win, "补偿", 3)
    a3b.frame_rotations = {1: 90}
    a3b.invalidate_transforms()
    before = compute_preview_layout(a3b, 0)[2]          # positions: (x, y, w, h)
    win._delete_action_frame(a3b, 1)                     # 删含旋转的 30x40 帧（原 max_h 贡献者）
    after = compute_preview_layout(a3b, 0)[2]
    check("删最大帧后保留帧落点不变（补偿生效）",
          after[0][:2] == before[0][:2] and after[1][:2] == before[2][:2],
          f"before={before} after={after}")

    # ---- 5. 预览 UI 路径：删当前帧自动落回同位帧 ----
    a3c = add_action(win, "预览删", 3)
    win.preview.set_action(a3c)
    win.preview._frame_index = 2
    win.preview._delete_thumb_frame(2)
    check("预览删当前帧后帧数 2", len(a3c.frames) == 2, str(len(a3c.frames)))
    check("当前帧落回删后同位帧",
          win.preview._frame_index == 1 and win.preview.thumb_list.currentRow() == 1,
          f"idx={win.preview._frame_index} row={win.preview.thumb_list.currentRow()}")

    # ---- 6. 抽帧窗口：同款右键删除 + 取消还原 / 确定定案 ----
    from spritesheet.ui.extract_dialog import ExtractDialog
    a6 = add_action(win, "抽帧删", 3)
    dlg = ExtractDialog(parent=win)
    dlg.set_action(a6)
    b = dlg._build_thumb_menu(QPoint(4, 4))
    check("抽帧窗菜单项存在", b is not None and b[1].text() == "删除此帧")
    win._extract_origin = None
    _, _, e_row, _ = b
    n0 = len(a6.frames)
    dlg._delete_thumb_frame(e_row)
    check("抽帧窗删除帧数-1", len(a6.frames) == n0 - 1, str(len(a6.frames)))
    check("origin 已捕获（取消可还原）", getattr(win, "_extract_origin", None) is not None)
    win._cancel_extract_state()
    check("点取消：被删帧还原回来", len(a6.frames) == n0, str(len(a6.frames)))
    dlg._delete_thumb_frame(min(e_row, n0 - 2))
    n1 = len(a6.frames)
    win._commit_extract_state()
    check("点确定：origin 清空、删除定案",
          getattr(win, "_extract_origin", None) is None and len(a6.frames) == n1,
          f"origin={getattr(win, '_extract_origin', '?')} frames={len(a6.frames)}")

    win.close()
    print()
    print(f"DELETE FRAME: PASSED {PASSED[0]} | FAILED {len(FAILS)}")
    if FAILS:
        print("Failures:", *FAILS, sep="\n  - ")
        sys.stdout.flush()
        os._exit(1)
    print("DELETE FRAME OK")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()