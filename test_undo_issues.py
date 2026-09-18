"""多选历史记录 / 改名 undo 错位 / 导入唯一性 综合验证 v2。

用法：.venv\\Scripts\\python.exe test_undo_issues.py
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from PyQt6.QtCore import QTimer, QEventLoop
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from spritesheet.ui.main_window import MainWindow
from spritesheet.importer import ImportedProject, ImportedAction, ImportedFrame, ImportedPart

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

    # ================= 场景 1：多选变换/位移落账 =================
    print("=== 场景1: 多选变换/位移进历史 ===")
    win = MainWindow()
    win.show()
    wait_ms(app, 300)
    pm = load_demo(win, app)

    win._create_action()
    action = win.actions[-1]
    frames = win.image_viewer._frames
    n = min(8, len(frames))
    action.selected_indices = list(range(n))
    win.image_viewer.set_current_action(action)
    win.image_viewer.crop_frames_for_action(action)
    win.preview.set_action(action)
    win.preview.set_actions(win.actions)
    win._end_all_renames()
    wait_ms(app, 700)   # 等 set_action 的"切换帧"记录落完

    ap = win.preview.adjust_panel
    thumbs = win.preview.thumb_list
    for i in (0, 1, 2):
        thumbs.item(i).setSelected(True)
    wait_ms(app, 80)

    stack = action.undo_stack
    assert stack is not None
    before_len = len(stack_texts(stack))

    # 多选旋转
    action.frame_rotations.clear()
    win._transform_history_state = win._capture_transform_state()
    ap._apply_rotation(45)
    wait_ms(app, 700)
    texts = stack_texts(stack)
    check("多选旋转产生历史记录", any("变换帧" in t for t in texts) and len(texts) > before_len,
          f"cmds={texts[before_len:]}")
    check("旋转记录作用于3帧", sorted(action.frame_rotations.keys()) == [0, 1, 2],
          f"rot={sorted(action.frame_rotations.keys())}")
    # undo 一次：应撤销旋转（撤销的是最后一条，即旋转）
    stack.undo()
    check("undo多选旋转后全部还原", action.frame_rotations == {},
          f"rot={action.frame_rotations}")
    stack.redo()
    check("redo多选旋转恢复", sorted(action.frame_rotations.keys()) == [0, 1, 2],
          f"rot={sorted(action.frame_rotations.keys())}")
    stack.undo()  # 再撤掉，保持无旋转状态

    # 多选位移（方向按钮）
    action.frame_offsets.clear()
    win._last_snapshot_state = win._capture_snapshot()
    ap._adjust_frame(1, 0)
    wait_ms(app, 700)
    texts = stack_texts(stack)
    check("多选位移产生历史记录", any("微调帧偏移" in t for t in texts),
          f"cmds={texts}")
    check("位移记录作用于3帧", sorted(action.frame_offsets.keys()) == [0, 1, 2],
          f"off={sorted(action.frame_offsets.keys())}")
    stack.undo()
    check("undo多选位移后全部还原", action.frame_offsets == {},
          f"off={action.frame_offsets}")
    win.close()

    # ================= 场景 2：改名 undo 索引错位 =================
    print("=== 场景2: 改名undo恢复错位 ===")
    win = MainWindow()
    win.show()
    wait_ms(app, 300)
    pm = load_demo(win, app)
    for _ in range(3):
        win._create_action()
        win._end_all_renames()
    wait_ms(app, 300)
    acts = list(win.actions)
    check("创建3个动作", len(acts) == 3, f"n={len(acts)}")
    a1, a2, a3 = acts
    win.image_viewer.set_current_action(a1)
    win.action_list.setCurrentRow(0)
    wait_ms(app, 200)

    # 给 a2（非当前，索引1）改名
    win._on_action_name_changed(a2, "改名二号")
    check("改名落在a2", a2.name == "改名二号", f"name={a2.name}")
    s2 = a2.undo_stack
    check("a2有自己的改名记录", s2 is not None and s2.count() >= 1,
          f"count={s2.count() if s2 else None}")

    # 删除 a1（a2 索引从 1 → 0，索引漂移！）
    win._delete_action(a1)
    wait_ms(app, 200)
    check("删除a1后a2排第0", win.actions[0] is a2, f"first={win.actions[0].name}")

    names_before = [a.name for a in win.actions]
    colors_before = [a.color for a in win.actions]
    s2.undo()
    check("undo改名只改目标动作", a2.name == "动作2", f"a2.name={a2.name}")
    others_unchanged = all(
        a.name == nb and a.color == cb
        for a, nb, cb in zip(win.actions, names_before, colors_before)
        if a is not a2
    )
    check("其他动作名字/颜色未被改动", others_unchanged,
          f"names={[a.name for a in win.actions]} colors={[a.color for a in win.actions]}")

    # 再测颜色改名的 undo（同样防错位）
    win._on_action_color_changed(a2, "#00AAFF")
    check("改色落在a2", a2.color == "#00AAFF", f"color={a2.color}")
    s2.undo()
    check("undo改色只改目标动作", a2.color == colors_before[0], f"a2.color={a2.color}")
    others_unchanged2 = all(
        a.color == cb for a, cb in zip(win.actions, colors_before) if a is not a2
    )
    check("其他动作颜色未被改动", others_unchanged2,
          f"colors={[a.color for a in win.actions]}")
    win.close()

    # ================= 场景 3：导入唯一性 =================
    print("=== 场景3: 导入动作名/颜色唯一性 ===")
    win = MainWindow()
    win.show()
    wait_ms(app, 300)
    pm = load_demo(win, app)

    part = ImportedPart(rect=(0, 0, 32, 32), pos=(0, 0))
    fr = ImportedFrame(parts=[part])
    imp_acts = [
        ImportedAction(name="重复名", frames=[fr], color="#E91E63"),
        ImportedAction(name="重复名", frames=[fr], color="#E91E63"),
        ImportedAction(name="唯一名", frames=[fr], color="#E91E63"),
    ]
    proj = ImportedProject(json_path=Path("x.json"), atlas_path=Path("x.png"),
                           actions=imp_acts, fmt="本工具格式")
    win._apply_imported_project(proj, pm, append=False)
    wait_ms(app, 200)

    names = [a.name for a in win.actions]
    colors = [a.color for a in win.actions]
    check("导入动作名全部唯一", len(set(names)) == len(names), f"names={names}")
    check("导入动作颜色全部唯一", len(set(colors)) == len(colors), f"colors={colors}")

    # append 模式：追加到已有工程也要唯一
    win._create_action()
    win._end_all_renames()
    imp_acts2 = [ImportedAction(name="动作1", frames=[fr], color="#E91E63")]
    proj2 = ImportedProject(json_path=Path("y.json"), atlas_path=Path("y.png"),
                            actions=imp_acts2, fmt="本工具格式")
    win._apply_imported_project(proj2, pm, append=True)
    wait_ms(app, 200)
    names2 = [a.name for a in win.actions]
    colors2 = [a.color for a in win.actions]
    check("append导入名字仍唯一", len(set(names2)) == len(names2), f"names={names2}")
    check("append导入颜色仍唯一", len(set(colors2)) == len(colors2), f"colors={colors2}")
    win.close()

    print("=" * 50)
    print(f"PASSED {passed}  |  FAILED {len(FAILURES)}")
    if FAILURES:
        print("Failures:", *FAILURES, sep="\n  - ")
        return 1
    print("UNDO ISSUES TEST OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
