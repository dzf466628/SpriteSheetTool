"""5 项 UI 需求综合验证：
1. 底部横条标题「动作编辑」
2. 底部横条变换：切帧/切工具自动取消弹回
3. 动画编辑与帧编辑变换工具独立（点一个不再都亮）
4. 「忽略选中元素」→「取消选中」（菜单 + 历史文字）
5. 「忽略选中区域」：标记不参与自动识别，灰色虚线保留，undo + 工程持久化

用法：.venv\\Scripts\\python.exe test_ui_req5.py
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from PyQt6.QtCore import QTimer, QEventLoop, QRectF, QPointF, QPoint, Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from spritesheet.ui.main_window import MainWindow

FAILURES: list[str] = []
passed = 0


def check(name: str, cond, detail: str = "") -> None:
    global passed
    if cond:
        passed += 1
        print(f"  [OK]   {name}")
    else:
        FAILURES.append(name)
        print(f"  [FAIL] {name}  {detail}")


def wait_ms(app: QApplication, ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def load_demo(win, app):
    img = ROOT / "mage_sprite_keyed.png"
    pm = QPixmap(str(img))
    win._on_image_loaded(pm)
    detected = {"n": None}
    win.image_viewer.frames_updated.connect(lambda: detected.update(n=len(win.image_viewer._frames)))
    import time
    t0 = time.perf_counter()
    while detected["n"] is None and time.perf_counter() - t0 < 60:
        wait_ms(app, 100)
    return pm


def stack_texts(stack) -> list[str]:
    return [stack.command(i).text() for i in range(stack.count())]


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    print("=== 需求1: 底部横条标题 ===")
    win = MainWindow()
    win.show()
    wait_ms(app, 200)
    check("标题为「动作编辑」", win.anim_bar._bar_title.text() == "动作编辑",
          f"实际: {win.anim_bar._bar_title.text()!r}")

    print("=== 场景准备: 加载素材 + 建动作 ===")
    pm = load_demo(win, app)
    win._create_action()
    action = win.actions[-1]
    frames = win.image_viewer._frames
    n = min(8, len(frames))
    check("检测到足够元素", n >= 4, f"n={n}")
    action.selected_indices = list(range(n))
    win.image_viewer.set_current_action(action)
    win.image_viewer.crop_frames_for_action(action)
    win.preview.set_action(action)
    win.preview.set_actions(win.actions)
    wait_ms(app, 100)

    canvas = win.preview.canvas
    panel = win.preview.adjust_panel
    anim = win.anim_bar
    ab = anim._all_mode_buttons
    pb = {"rotate": panel.btn_mode_rotate, "scale": panel.btn_mode_scale,
          "skew": panel.btn_mode_skew, "perspective": panel.btn_mode_perspective,
          "distort": panel.btn_mode_distort}

    print("=== 需求3: 变换工具独立 ===")
    # 底部横条激活 rotate → 只有底部亮，右侧不亮
    ab["rotate"].click()
    check("底部激活 rotate 后 canvas 模式=rotate",
          canvas.transform_mode() == "rotate")
    check("底部 rotate 按钮点亮", ab["rotate"].isChecked())
    right_lit = [k for k, b in pb.items() if b.isChecked()]
    check("右侧 5 个变换按钮全不亮", not right_lit, f"点亮了: {right_lit}")
    check("canvas owner='all'", canvas._transform_owner == "all",
          f"实际: {canvas._transform_owner!r}")

    # 右侧激活 scale → 只有右侧亮，底部弹回
    panel._on_mode_button("scale", True)
    check("右侧激活 scale 后 canvas 模式=scale",
          canvas.transform_mode() == "scale")
    check("右侧 scale 按钮点亮", pb["scale"].isChecked())
    all_lit = [k for k, b in ab.items() if b.isChecked()]
    check("底部 5 个变换按钮全弹回", not all_lit, f"点亮了: {all_lit}")
    check("canvas owner='frame'", canvas._transform_owner == "frame",
          f"实际: {canvas._transform_owner!r}")

    print("=== 需求2: 切换工具/点击其他自动取消 ===")
    # 底部激活 rotate，用户点缩略图切帧 → 自动退出 + 底部弹回
    ab["rotate"].click()
    check("底部再次激活 rotate", canvas.transform_mode() == "rotate"
          and canvas._transform_owner == "all")
    win.preview._on_thumb_changed(1)  # 模拟用户点击第 2 帧缩略图
    check("切帧后变换模式自动退出", canvas.transform_mode() is None,
          f"实际: {canvas.transform_mode()!r}")
    all_lit = [k for k, b in ab.items() if b.isChecked()]
    check("切帧后底部按钮全部弹回", not all_lit, f"点亮了: {all_lit}")
    right_lit = [k for k, b in pb.items() if b.isChecked()]
    check("切帧后右侧按钮也弹回", not right_lit, f"点亮了: {right_lit}")

    print("=== 需求2b: 变换拖动中不弹起 + 帧索引稳定 ===")
    # 右侧 scale 激活，从手柄按下→拖动→释放，全程模式保持、帧索引不被重置
    panel._on_mode_button("scale", True)
    win.preview._frame_index = 2
    win.preview._sync_frame()
    handle_pts = canvas._handles_canvas(canvas._current_mo())
    check("变换手柄可命中", len(handle_pts) >= 4, f"pts={len(handle_pts)}")
    hp0 = canvas._logical_to_screen(handle_pts[0]).toPoint()
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=hp0)
    check("按下后进入拖动", canvas.transform_mode() == "scale" and canvas._drag_kind == "corner",
          f"实际 mode={canvas.transform_mode()!r} drag={canvas._drag_kind!r}")
    hp1 = hp0 + QPoint(25, 12)
    QTest.mouseMove(canvas, pos=hp1)
    check("拖动中模式保持（不弹起）",
          canvas.transform_mode() == "scale" and canvas._drag_kind == "corner",
          f"实际 mode={canvas.transform_mode()!r} drag={canvas._drag_kind!r}")
    check("拖动中帧索引未被重置", win.preview._frame_index == 2,
          f"实际: {win.preview._frame_index}")
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=hp1)
    check("释放后模式保持", canvas.transform_mode() == "scale",
          f"实际: {canvas.transform_mode()!r}")
    panel._on_mode_button("scale", False)

    print("=== 需求2c: 播放中切帧自动取消变换 ===")
    ab["rotate"].click()
    check("激活 rotate", canvas.transform_mode() == "rotate")
    win.preview._playing = True
    win.preview._tick()  # 模拟播放定时器切帧
    check("播放切帧自动退出变换模式", canvas.transform_mode() is None,
          f"实际: {canvas.transform_mode()!r}")
    win.preview._playing = False

    # 底部激活 rotate → 再点同按钮 = 退出
    ab["rotate"].click()
    check("切帧后再点激活 rotate", canvas.transform_mode() == "rotate")
    ab["rotate"].click()
    check("底部激活 rotate 后再点同按钮退出",
          canvas.transform_mode() is None)
    check("退出后底部 rotate 弹回", not ab["rotate"].isChecked())

    # 点画布空白 → 自动退出 + 发起方弹回
    ab["rotate"].click()
    check("空白点击前已激活 rotate", canvas.transform_mode() == "rotate"
          and ab["rotate"].isChecked())
    QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=QPoint(2, 2))
    check("点画布空白自动退出变换模式", canvas.transform_mode() is None,
          f"实际: {canvas.transform_mode()!r}")
    check("空白点击后底部按钮弹回", not ab["rotate"].isChecked())

    print("=== 需求4: 取消选中（只取消鼠标所指单个）===")
    seen = []
    win.image_viewer.action_performed.connect(seen.append)
    action.selected_indices = [0, 1]
    win.image_viewer.crop_frames_for_action(action)
    win.image_viewer.menu_remove_selected_elements(0)
    check("历史记录文字为「取消选中」", seen and seen[-1] == "取消选中",
          f"实际: {seen}")
    check("只取消所指元素0、元素1保留", action.selected_indices == [1],
          f"实际: {action.selected_indices}")
    before = list(action.selected_indices)
    win.image_viewer.menu_remove_selected_elements(5)  # 未选中/越界 = no-op
    check("取消未命中元素为 no-op", action.selected_indices == before,
          f"实际: {action.selected_indices}")

    print("=== 需求5: 忽略选中区域 ===")
    viewer = win.image_viewer
    viewer._ignored_frames.clear()
    action.selected_indices = [0, 1]
    win.image_viewer.set_current_action(action)
    win.image_viewer.crop_frames_for_action(action)
    # 调用前栈长度（全量操作走「当前动作的栈」）
    stack = win._active_stack()
    before_count = stack.count()
    viewer.menu_ignore_selected_regions()
    check("忽略集合 = {0,1}", viewer._ignored_frames == {0, 1},
          f"实际: {viewer._ignored_frames}")
    check("忽略后当前动作不再选中 0/1",
          all(i not in action.selected_indices for i in (0, 1)),
          f"实际选中: {action.selected_indices}")
    check("历史栈新增「忽略选中区域」",
          stack_texts(stack)[before_count:] == [f"忽略选中区域「{action.name}」"],
          f"实际: {stack_texts(stack)[before_count:]}")

    # 点击被忽略元素 → 选不中
    fx, fy, fw, fh = viewer._frames[0]
    cx = viewer._offset.x() + (fx + fw / 2) * viewer._zoom
    cy = viewer._offset.y() + (fy + fh / 2) * viewer._zoom
    check("点击被忽略元素返回 -1",
          viewer._frame_at_point(QPointF(cx, cy)) == -1,
          f"实际: {viewer._frame_at_point(QPointF(cx, cy))}")

    # 框选覆盖被忽略元素 → 不含
    rect = QRectF(viewer._offset.x(), viewer._offset.y(),
                  viewer._pixmap.width() * viewer._zoom,
                  viewer._pixmap.height() * viewer._zoom)
    got = viewer._frames_in_view_rect(rect)
    check("框选结果不含被忽略元素", 0 not in got and 1 not in got,
          f"实际包含: {[i for i in (0, 1) if i in got]}")

    # 全选 / 反选排除被忽略元素
    viewer.menu_select_all()
    check("全选不含被忽略元素",
          all(i not in action.selected_indices for i in (0, 1)),
          f"实际选中: {action.selected_indices}")
    viewer.menu_invert_selection()
    check("反选不含被忽略元素",
          all(i not in action.selected_indices for i in (0, 1)),
          f"实际选中: {action.selected_indices}")

    # undo 恢复
    win._do_undo()
    check("undo 后忽略集合恢复为空", viewer._ignored_frames == set(),
          f"实际: {viewer._ignored_frames}")

    # 合并为一整块：忽略标记重映射
    viewer._ignored_frames = {0, 1}
    action.selected_indices = [2, 3]
    viewer.menu_merge_single_block()
    nf = len(viewer._frames)
    check("合并后忽略索引全部 < 新帧数", all(i < nf for i in viewer._ignored_frames),
          f"忽略: {viewer._ignored_frames}, 帧数: {nf}")
    old_count = len(viewer._frames) + 1  # 合并前帧数 = 现在 - 1（新元素）
    check("合并后旧索引 0/1 已重映射（新索引不含越界）",
          all(i < old_count for i in viewer._ignored_frames))

    # 工程持久化：manifest 保存 + 恢复
    print("=== 需求5: 工程持久化 ===")
    viewer._ignored_frames = {0, 2}
    manifest, sections = win._project_manifest()
    check("manifest 含 ignored_frames", "ignored_frames" in (manifest.get("viewer") or {}),
          f"viewer 键: {list((manifest.get('viewer') or {}).keys())}")
    check("manifest ignored_frames=[0,2]",
          (manifest.get("viewer") or {}).get("ignored_frames") == [0, 2],
          f"实际: {(manifest.get('viewer') or {}).get('ignored_frames')}")
    # 恢复到新窗口（正常完成不抛异常即成功）
    win2 = MainWindow()
    win2.show()
    wait_ms(app, 100)
    try:
        win2._apply_sstool_project(manifest, sections)
        applied = True
    except Exception as exc:  # noqa: BLE001
        applied = False
        print("  [INFO] apply 异常:", exc)
    check("工程恢复无异常", applied)
    check("恢复后动作数一致", len(win2.actions) == len(win.actions),
          f"实际: {len(win2.actions)} vs {len(win.actions)}")
    check("恢复后 ignored_frames={0,2}",
          win2.image_viewer._ignored_frames == {0, 2},
          f"实际: {win2.image_viewer._ignored_frames}")
    win2.close()

    win.close()
    print("=" * 50)
    print(f"PASSED {passed}  |  FAILED {len(FAILURES)}")
    if FAILURES:
        print("失败项:")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("UI REQ5 TEST OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
