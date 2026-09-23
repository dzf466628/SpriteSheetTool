"""拖动原图移位回归：位置存储 / 夹取 / 帧跟着走 / 撤销重做 / 工程往返。

为什么单独一个脚本：允许拖动之后"位置"从**算出来的**变成了**存下来的状态**，于是有三件
最容易出错、又肉眼看不出来的事必须钉死：

  1. 拼出来的大图跟偏移必须一致（错一位，虚框就和内容错开）；
  2. 帧要跟着原图一起走（不跟的话内容与框错位，排好的动作全废）；
  3. 撤销/工程往返要把偏移一起还原（只还原图不还原偏移，就是"撤销后位置回不去"）。

素材用三张**宽度不同**的原图（80 / 60 / 60）：画布宽度取最大宽度 80，于是 60 宽的块
才有左右可移动的余量 —— 都用 60 宽的话横向根本没地方挪，夹取逻辑就测不到了。
全程 offscreen 可跑。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image
from PyQt6.QtCore import QEvent, QPointF, QSize, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

from spritesheet import project_file as PF
from spritesheet.ui.key_panel import pil_to_pixmap
from spritesheet.ui.main_window import MainWindow

# QApplication 挂模块级：放函数里真机收尾会 0xC0000409（offscreen 看不出来）
_APP = QApplication.instance() or QApplication(sys.argv)

BG = [(40, 120, 200), (200, 40, 200), (60, 200, 90)]
SPRITE = [(200, 40, 40), (240, 230, 60), (230, 120, 40)]
SIZES = [(80, 50), (60, 50), (60, 50)]
GAP = 16
HEIGHTS = [h for _w, h in SIZES]
OFFSETS0 = [(0, 0), (0, HEIGHTS[0] + GAP), (0, HEIGHTS[0] + GAP + HEIGHTS[1] + GAP)]


def make_source(index: int) -> Image.Image:
    """一张原图：纯色底 + 中间一个角色。"""
    w, h = SIZES[index]
    img = Image.new("RGBA", (w, h), BG[index] + (255,))
    for y in range(15, 36):
        for x in range(20, 41):
            img.putpixel((x, y), SPRITE[index] + (255,))
    return img


def main() -> int:
    app = _APP
    passed = failed = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, failed
        if ok:
            passed += 1
            print(f"  PASS  {name} {detail}")
        else:
            failed += 1
            print(f"  FAIL  {name} {detail}")

    def px(pixmap, x: int, y: int) -> tuple:
        c = pixmap.toImage().pixelColor(int(x), int(y))
        return (c.red(), c.green(), c.blue(), c.alpha())

    win = MainWindow()
    win.resize(1400, 900)
    win.show()
    for _ in range(6):
        app.processEvents()
    viewer = win.image_viewer
    canvas = viewer.canvas

    def wait(ms: int = 300) -> None:
        for _ in range(max(1, ms // 50)):
            app.processEvents()
            app.thread().msleep(50)

    def canvas_point(image_x: float, image_y: float) -> QPointF:
        return QPointF(viewer._offset.x() + image_x * viewer._zoom,
                       viewer._offset.y() + image_y * viewer._zoom)

    def drag(from_image, to_image) -> None:
        """在画布上真实拖一把（按下 -> 移动 -> 松开）。"""
        start, end = canvas_point(*from_image), canvas_point(*to_image)
        canvas.mousePressEvent(QMouseEvent(
            QEvent.Type.MouseButtonPress, start, start, Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        canvas.mouseMoveEvent(QMouseEvent(
            QEvent.Type.MouseMove, end, end, Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        canvas.mouseReleaseEvent(QMouseEvent(
            QEvent.Type.MouseButtonRelease, end, end, Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))

    print("---- 准备：三张宽度不同的原图（80/60/60），还没有动作 ----")
    sources = [make_source(i) for i in range(3)]
    win._on_image_loaded(pil_to_pixmap(sources[0]))
    for pm_src in sources[1:]:
        win._on_image_loaded(pil_to_pixmap(pm_src))
    wait(900)
    check("画布宽度取最大宽度 80", viewer._pixmap.width() == 80, f"宽={viewer._pixmap.width()}")
    check("没有动作（拖动不依赖动作）", win.actions == [])
    win._on_source_tool_toggled(True)      # 拖动是「原图选择」工具模式下的手势
    wait(200)
    check("原图选择工具已开（下面才谈得上拖动）", viewer._source_tool is True)

    print("---- 1. 偏移初值就是拼装算法那一套 ----")
    check("初始偏移与拼装算法一致", viewer._source_offsets == OFFSETS0,
          f"实际 {viewer._source_offsets}")
    check("source_rects 与偏移一致",
          viewer.source_rects() == [(x, y, SIZES[i][0], SIZES[i][1])
                                    for i, (x, y) in enumerate(OFFSETS0)],
          f"实际 {viewer.source_rects()}")

    print("---- 2. 移动一块：偏移变了，拼出来的大图跟着变 ----")
    before = list(viewer._source_offsets)
    moved = viewer.move_sources([1], 7, 5)
    check("move_sources 报告移动成功", moved is True)
    check("偏移变成 (7, 71)", viewer._source_offsets[1] == (7, 71),
          f"实际 {viewer._source_offsets[1]}")
    check("别的块没被动", viewer._source_offsets[0] == before[0]
          and viewer._source_offsets[2] == before[2])
    check("内容出现在新位置",
          px(viewer._pixmap, 7 + 30, 71 + 25) == SPRITE[1] + (255,),
          f"实际 {px(viewer._pixmap, 7 + 30, 71 + 25)}")
    check("原位置留下的空缺是透明的",
          px(viewer._pixmap, 3, 66 + 2)[3] == 0, f"实际 {px(viewer._pixmap, 3, 66 + 2)}")
    check("第 1 块没被顺带挪动", px(viewer._pixmap, 30, 25) == SPRITE[0] + (255,))

    print("---- 3. 夹取：左上以 (0,0) 为界，右下按需长大画布 ----")
    viewer._source_offsets = list(OFFSETS0)          # 第 2 节挪过一块，先摆回原样
    viewer._recompose_sources(QSize(80, 182))
    check("移动前画布宽度=最宽的图（同宽的图横向一点余量都没有，这就是"
          "\"不能随意移动\"的根因）",
          (viewer._pixmap.width(), viewer._pixmap.height()) == (80, 182),
          f"实际 {viewer._pixmap.width()}×{viewer._pixmap.height()}")
    check("第 1 块已经在左上角，往左上拖返回 False（一点没动）",
          viewer.move_sources([0], -10, -10) is False)
    check("往右下拖 -> 位置就是你要的那个数，不再撞墙",
          viewer.move_sources([1], 30, 40) is True
          and viewer._source_offsets[1] == (30, 106),
          f"偏移={viewer._source_offsets[1]}")
    check("画布长到装得下：宽 30+60=90（高不动，下面那块本来就占到底 182）",
          (viewer._pixmap.width(), viewer._pixmap.height()) == (90, 182),
          f"实际 {viewer._pixmap.width()}×{viewer._pixmap.height()}")
    check("原点没动：第 1 块的偏移还是 (0,0)", viewer._source_offsets[0] == (0, 0))
    check("内容也跟到新位置",
          px(viewer._pixmap, 30 + 30, 106 + 25) == SPRITE[1] + (255,),
          f"实际 {px(viewer._pixmap, 60, 131)}")
    check("最下面那块往右下拖也得动（不是\"到底了就拖不动\"）",
          viewer.move_sources([2], 5, 6) is True
          and viewer._source_offsets[2] == (5, 138),
          f"偏移={viewer._source_offsets[2]}")
    check("拖到 8192 上限才夹住（不会无限长大）",
          viewer.move_sources([1], 99999, 0) is True
          and viewer._source_offsets[1][0] == 8192 - 60,
          f"偏移={viewer._source_offsets[1]}")
    viewer._source_offsets = list(OFFSETS0)
    viewer._recompose_sources(QSize(80, 182))       # 收回原尺寸，后面还要按原几何核对
    viewer.canvas.update()

    print("---- 4. 帧跟着原图走，别的块里的帧不动 ----")
    wait(300)
    viewer._frames = [(20, 66 + 15, 21, 21), (20, 15, 21, 21)]
    viewer.move_sources([1], 7, 5)
    check("第 2 块里的帧跟着平移 (+7, +5)",
          tuple(viewer._frames[0]) == (27, 86, 21, 21), f"实际 {tuple(viewer._frames[0])}")
    check("第 1 块里的帧一动不动", tuple(viewer._frames[1]) == (20, 15, 21, 21),
          f"实际 {tuple(viewer._frames[1])}")

    print("---- 5. 动作的帧缓存要跟着重裁 ----")
    win._on_add_action_clicked()
    wait(300)
    check("动作建出来了", len(win.actions) == 1, f"动作数={len(win.actions)}")
    check("有了动作之后，原图拖动自动退回（画布拖动回到'框选元素'这个主用途）",
          viewer._source_tool is False)
    win._on_source_tool_toggled(True)       # 本节要测拖动，把工具再打开
    wait(150)
    action = win.actions[0]
    # 帧要"属于这个动作"才会被裁进 frame_rects（_frames 只是检测出来的候选）
    action.selected_indices = [0, 1]
    for act in win.actions:
        act.invalidate_transforms()
        viewer.crop_frames_for_action(act)
    before_rects = [tuple(r) for r in action.frame_rects]
    viewer.move_sources([1], -7, -5)
    win._on_source_move_finished()
    wait(300)
    check("动作的帧矩形跟着挪回去了",
          [tuple(r) for r in action.frame_rects] != before_rects
          and tuple(action.frame_rects[0]) == (20, 81, 21, 21),
          f"实际 {[tuple(r) for r in action.frame_rects]}")

    print("---- 6. 真实拖一把：位置变、记一条全局撤销、能撤回来 ----")
    viewer.set_selected_sources([1])
    before_offsets = list(viewer._source_offsets)
    before_index = win._undo_stack.index()
    drag((30, viewer._source_offsets[1][1] + 25),
         (40, viewer._source_offsets[1][1] + 33))
    wait(400)
    check("拖完位置真的变了",
          viewer._source_offsets[1] == (before_offsets[1][0] + 10, before_offsets[1][1] + 8),
          f"偏移={viewer._source_offsets[1]}")
    check("记了一条全局撤销", win._undo_stack.index() == before_index + 1,
          f"index {before_index} -> {win._undo_stack.index()}")
    check("状态栏说明了移动", "已移动原图" in win.lbl_status.text(),
          f"状态栏={win.lbl_status.text()!r}")
    win._do_undo()
    wait(400)
    check("撤销后位置回原位", viewer._source_offsets == before_offsets,
          f"偏移={viewer._source_offsets}")
    check("撤销后拼出来的图也跟着回去了（旧位置又空了）",
          px(viewer._pixmap, before_offsets[1][0] + 30, before_offsets[1][1] + 25)
          == SPRITE[1] + (255,))
    win._do_redo()
    wait(400)
    check("重做又回到移动后的位置",
          viewer._source_offsets[1] == (before_offsets[1][0] + 10, before_offsets[1][1] + 8),
          f"偏移={viewer._source_offsets[1]}")
    win._do_undo()
    wait(400)

    print("---- 7. 没被选中的原图：拖它不移动，只把它选中 ----")
    viewer.set_selected_sources([0])
    before_offsets = list(viewer._source_offsets)
    drag((30, viewer._source_offsets[1][1] + 25),
         (48, viewer._source_offsets[1][1] + 41))
    wait(300)
    check("位置一点没动（用户定的：先选中再说）", viewer._source_offsets == before_offsets,
          f"偏移={viewer._source_offsets}")
    check("它被选中了", viewer.selected_sources() == [1],
          f"选中={viewer.selected_sources()}")

    print("---- 8. 工程往返：偏移存进去了，也能读回来 ----")
    moved_offsets = list(viewer._source_offsets)
    manifest, sections = win._project_manifest()
    check("清单里带上了 source_offsets",
          manifest.get("viewer", {}).get("source_offsets") == [list(o) for o in moved_offsets],
          f"清单={manifest.get('viewer', {}).get('source_offsets')}")
    tmp = Path(tempfile.mkdtemp(prefix="sstool_move_")) / "移动.sstool"
    PF.write_project(tmp, manifest, sections)
    # 先把位置改乱，再读回来 —— 读回来必须是你存的那份
    viewer.move_sources([1], 0, -6)
    viewer._source_offsets[1] = (0, 0)
    m2, s2 = PF.read_project(tmp)
    win._apply_sstool_project(m2, s2)
    wait(400)
    check("读回来的偏移与存的一致", viewer._source_offsets == moved_offsets,
          f"实际 {viewer._source_offsets}")
    check("读回来的内容也在那个位置",
          px(viewer._pixmap, moved_offsets[1][0] + 30, moved_offsets[1][1] + 25)
          == SPRITE[1] + (255,))

    print("---- 11. 检测还在跑时拖动：结果落地要补上这次的位移 ----")
    # 这是"拖动后留在原处、跟内容错开的虚线框"的根因：后台检测算的是开始那一刻的位置，
    # 用户在检测跑完前拖走了那张图，新元素就会落在拖动前的位置上
    viewer._source_offsets = list(OFFSETS0)
    viewer._recompose_sources(QSize(80, 182))
    viewer._detect_inflight = True
    viewer._pending_detect_shifts = []
    check("检测在跑时拖动会把位移记下来（供结果补正）",
          viewer.move_sources([2], 4, 3) is True
          and viewer._pending_detect_shifts == [([(2, (0, 132, 60, 50))], 4, 3)],
          f"记录={viewer._pending_detect_shifts}")
    check("落在被拖那块里的新元素补上位移",
          viewer._apply_pending_detect_shifts([(10, 140, 20, 20)]) == [(14, 143, 20, 20)])
    check("补偿用完就清空，不会重复补", viewer._pending_detect_shifts == [])
    check("别的块里的元素原样不动",
          viewer._apply_pending_detect_shifts([(10, 40, 20, 20)]) == [(10, 40, 20, 20)])
    viewer._detect_inflight = False
    viewer._pending_detect_shifts = []
    viewer.move_sources([2], -4, -3)
    check("检测没在跑时不留补偿（新检测的基准就是当前状态）",
          viewer._pending_detect_shifts == [])
    check("帧框跟着内容走：中心在被拖块旧矩形里就算（边界算在内）",
          viewer._belongs_to_moved((40, 140, 40, 40), [(2, (0, 132, 60, 50))]) is True
          and viewer._belongs_to_moved((80, 140, 40, 40), [(2, (0, 132, 60, 50))]) is False)
    viewer._source_offsets = list(OFFSETS0)
    viewer._recompose_sources(QSize(80, 182))

    print("---- 12. 拖动扫过邻居：不能把邻居的元素框一起带走 ----")
    # 用户原话："拖动的时候如果路过另一张图，就会带走另一个图大小的灰色虚线"。
    # 根因是归属按"当前几何重叠"逐次重判：被拖的图扫过邻居时，邻居的框被算成自己的。
    viewer._source_offsets = list(OFFSETS0)
    viewer._recompose_sources(QSize(80, 182))
    viewer._frames = [(20, 15, 21, 21), (20, 81, 21, 21), (20, 147, 21, 21)]
    before = [tuple(f) for f in viewer._frames]
    viewer.set_selected_sources([0])
    viewer.begin_source_move([0])            # 拖动开始：归属一次定死
    for _ in range(12):                      # 逐次小步往下拖，模拟真实拖动扫过第 2 块
        viewer.move_sources([0], 0, 5)
    check("只有第 1 块里的框跟着走",
          tuple(viewer._frames[0]) == (20, before[0][1] + 60, 21, 21),
          f"实际 {tuple(viewer._frames[0])}")
    check("被扫过的第 2 块的框一动不动",
          tuple(viewer._frames[1]) == before[1], f"实际 {tuple(viewer._frames[1])}")
    check("第 3 块的框也一动不动", tuple(viewer._frames[2]) == before[2])
    viewer.end_source_move()
    check("拖动结束后归属清掉（下次拖动重新算）", viewer._move_frame_owner is None)
    check("没有定死归属时（脚本直接调 API）仍按几何判一次",
          viewer._belongs_to_moved((30, 100, 10, 10), [(1, (0, 66, 60, 50))]) is True)

    print("---- 13. 空格平移优先：原图选择模式下也不抢这一下 ----")
    viewer._source_offsets = list(OFFSETS0)
    viewer._recompose_sources(QSize(80, 182))
    viewer.set_source_tool(True)
    viewer.set_selected_sources([0])
    canvas._panning = True                   # 等价于"空格按住"
    before_offset = QPointF(viewer._offset)
    start, end = canvas_point(30, 25), canvas_point(120, 70)
    canvas.mousePressEvent(QMouseEvent(
        QEvent.Type.MouseButtonPress, start, start, Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    check("空格按住时原图手势不接管（否则空格就没法平移了）", canvas._src_press is None)
    canvas.mouseMoveEvent(QMouseEvent(
        QEvent.Type.MouseMove, end, end, Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    check("画布跟着平移了", viewer._offset != before_offset,
          f"偏移 {before_offset} -> {viewer._offset}")
    canvas.mouseReleaseEvent(QMouseEvent(
        QEvent.Type.MouseButtonRelease, end, end, Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    check("松开后平移状态清掉", canvas._pan_start is None)
    canvas._panning = False

    print("---- 9. 只有一张原图、没有动作时，工具照样能用 ----")
    win2 = MainWindow()
    win2.resize(1200, 800)
    win2.show()
    for _ in range(6):
        app.processEvents()
    v2 = win2.image_viewer
    win2._on_image_loaded(pil_to_pixmap(make_source(0)))
    wait(600)
    check("第二窗口没有动作", win2.actions == [])
    win2._on_source_tool_toggled(True)
    wait(200)
    check("没有动作也能开工具（用户明确要求）",
          v2._source_tool is True and v2.source_interaction is True)
    check("单张时不画分块框（免得看着像多出来一圈框）",
          v2.source_borders_visible is False)
    check("只有一张时它自动成为选中项（否则拖动会因为选中集合为空而拖不动）",
          v2.selected_sources() == [0], f"选中={v2.selected_sources()}")
    start = QPointF(v2._offset.x() + 30 * v2._zoom, v2._offset.y() + 25 * v2._zoom)
    end = QPointF(start.x() + 12 * v2._zoom, start.y() + 9 * v2._zoom)
    v2.canvas.mousePressEvent(QMouseEvent(
        QEvent.Type.MouseButtonPress, start, start, Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    v2.canvas.mouseMoveEvent(QMouseEvent(
        QEvent.Type.MouseMove, end, end, Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    v2.canvas.mouseReleaseEvent(QMouseEvent(
        QEvent.Type.MouseButtonRelease, end, end, Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    wait(400)
    check("单张原图、无动作也能拖动移位（用户明确要求）",
          v2._source_offsets == [(12, 9)], f"偏移={v2._source_offsets}")
    check("画布跟着长到 80+12 × 50+9",
          (v2._pixmap.width(), v2._pixmap.height()) == (92, 59),
          f"实际 {v2._pixmap.width()}×{v2._pixmap.height()}")
    check("状态栏如实报告", "已移动原图" in win2.lbl_status.text(),
          f"状态栏={win2.lbl_status.text()!r}")

    print("---- 10. 双击棋盘格 = 添加图片 ----")
    # 别真弹模态文件对话框把测试卡死：先摘掉主窗口那头的连接
    viewer.add_image_requested.disconnect(win._open_image)
    hits: list[int] = []
    viewer.add_image_requested.connect(lambda: hits.append(1))
    checker = canvas_point(SIZES[0][0] + 30, 10)     # 图右侧的棋盘格
    inside = canvas_point(30, 25)                   # 图里面
    for pt, label in ((checker, "棋盘格"), (inside, "图上")):
        canvas.mouseDoubleClickEvent(QMouseEvent(
            QEvent.Type.MouseButtonDblClick, pt, pt, Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        app.processEvents()
    check("双击棋盘格发出添加图片请求", len(hits) == 1, f"次数={len(hits)}")
    check("双击图上不抢（现在没有含义，留着以后用）", len(hits) == 1,
          f"次数={len(hits)}")

    win2.image_viewer.shutdown()      # 显式收尾（幂等）：真机上偶发的退出时崩溃要靠它
    win2.close()
    win.image_viewer.shutdown()
    win.close()
    print(f"\nSOURCE MOVE VERIFY {'OK' if not failed else 'FAILED'}  {passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())