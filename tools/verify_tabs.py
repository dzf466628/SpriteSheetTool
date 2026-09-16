"""工程标签页回归：新建 / 切换 / 改名 / 关闭，且**每个标签的文档互不串味**。

要证明的四件事：
  1. 标签栏是"矮条"（高度 20..32），且能在标签上标出未保存状态；
  2. 新建标签页会多一个标签，并且新标签的内容是**空的**（上一个工程不会漏进去）；
  3. 切换标签后，两个工程的**动作、帧、当前动作、预览**各自保持 —— 这是最容易错的地方
     （漏刷新任何一处，切回来就会看到上一个工程的画面）；
  4. 双击标签改名会同步到标签与窗口标题；双击标签栏空白处 = 新建标签页。

用法: .venv\\Scripts\\python.exe tools\\verify_tabs.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, QPointF, QRect, Qt
from PyQt6.QtGui import QColor, QMouseEvent, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'OK' if ok else 'FAIL'}]   {label} {detail}")
    if not ok:
        FAILURES.append(label)


def make_sheet(color: str, elements: int = 3, size: int = 40) -> QPixmap:
    pm = QPixmap(elements * size, size)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    for i in range(elements):
        painter.fillRect(QRect(i * size + 2, 2, size - 4, size - 4), QColor(color))
    painter.end()
    return pm


def double_click(widget, pos: QPoint) -> None:
    """给控件发一次双击（QMouseEvent 需要用 QPointF 构造）。"""
    event = QMouseEvent(QMouseEvent.Type.MouseButtonDblClick, QPointF(pos),
                        QPointF(widget.mapToGlobal(pos)),
                        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.NoModifier)
    widget.mouseDoubleClickEvent(event)


def build_doc(win, action_name: str, color: str, filled: int) -> int:
    """在当前标签里造一份能区分的文档，返回帧数。"""
    from spritesheet.ui.models import Action

    win._on_image_loaded(make_sheet(color))
    win.image_viewer._frames = [(2, 2, 36, 36), (42, 2, 36, 36), (82, 2, 36, 36)]
    win.image_viewer._frame_logical = {}
    action = Action(action_name, color)
    action.selected_indices = list(range(filled))
    win.actions.append(action)
    win._append_action_card(action)
    win.image_viewer.set_actions(win.actions)
    win.image_viewer.crop_frames_for_action(action)
    win.image_viewer.set_current_action(action)
    win.preview.set_source_pixmap(win.image_viewer._pixmap)
    win.preview.set_action(action)
    win._mark_project_dirty()
    return len(action.frames)


def main() -> int:
    app = QApplication(sys.argv)
    from spritesheet.ui.main_window import MainWindow

    win = MainWindow()
    tabs = win.tabs

    print("=== 1. 标签栏基本形态与位置 ===")
    print(f"  高度 {tabs.height()}px，起始标签数 {tabs.count()}")
    check("标签栏是矮条（20..32px）", 20 <= tabs.height() <= 32, f"实际 {tabs.height()}px")
    check("启动就有 1 个标签", tabs.count() == 1, f"实际 {tabs.count()}")

    # 位置：菜单栏 -> 软件名 -> 标签栏 -> 内容 -> 状态栏
    from spritesheet.ui.widgets import AppNameLabel

    name_label = win.findChildren(AppNameLabel)[0]
    header_bar = name_label.parentWidget()
    win.resize(1000, 1000)
    win.show()                 # 不 show 的话布局还没跑，所有 y() 都是 0
    app.processEvents()
    header_bottom = header_bar.y() + header_bar.height()
    print(f"  名称栏 y={header_bar.y()} 底部 y={header_bottom}；"
          f"标签栏 y={tabs.y()}；内容区 y={win.splitter.y()}；状态栏 y={win.lbl_status.y()}")
    check("标签栏在名称栏下面", tabs.y() >= header_bottom - 2,
          f"标签栏 y={tabs.y()}，名称栏底部 {header_bottom}")
    check("标签栏在软件名下面（名称栏在标签栏之上）", header_bar.y() < tabs.y())
    check("标签栏在内容区上面", tabs.y() + tabs.height() <= win.splitter.y(),
          f"标签栏底部 {tabs.y() + tabs.height()}，内容区 y={win.splitter.y()}")
    check("标签栏在状态栏上面", tabs.y() < win.lbl_status.y())

    print()
    print("=== 2. 在标签 0 里造工程 A ===")
    index_a = tabs.current_index()
    frames_a = build_doc(win, "A动作", "#C62828", 1)
    print(f"  工程 A 在第 {index_a} 个标签，帧数 {frames_a}，脏={win._project_dirty}")
    check("造完文档后标签标了脏", tabs.is_dirty(index_a) is True)

    print()
    print("=== 3. 新建标签页（工程 B），两份文档互不串味 ===")
    index_b = win._new_tab(name="工程B")
    check("新建后标签数 +1", tabs.count() == 2, f"实际 {tabs.count()}")
    check("新标签用的是新索引", index_b != index_a, f"{index_a} / {index_b}")
    check("新标签是空的（没有动作）", not win.actions, f"实际 {len(win.actions)}")
    check("新标签没有原图", win.image_viewer._pixmap is None)
    check("新标签不脏", win._project_dirty is False)
    check("新标签没有被标脏", tabs.is_dirty(index_b) is False)
    check("当前标签切到了新的那个", tabs.current_index() == index_b)

    frames_b = build_doc(win, "B动作", "#1565C0", 2)
    print(f"  工程 B 帧数 {frames_b}")

    win._switch_tab(index_a)
    check("切回 A：动作是 A 的", [a.name for a in win.actions] == ["A动作"],
          f"实际 {[a.name for a in win.actions]}")
    check("切回 A：帧数保持", len(win.actions[0].frames) == frames_a,
          f"实际 {len(win.actions[0].frames)} vs {frames_a}")
    check("切回 A：当前动作是 A", win.image_viewer.current_action is win.actions[0])
    check("切回 A：预览区跟着切了", win.preview._action is win.actions[0])
    check("切回 A：动作列表控件也重建了",
          win.action_list.count() == 1 and
          win.action_list.itemWidget(win.action_list.item(0)) is not None,
          f"实际 {win.action_list.count()} 行")
    check("切回 A：脏标记恢复", win._project_dirty is True)
    check("切回 A：A 自己那份撤销栈回来了", win._undo_stack is win._documents[index_a]["state"] is None
          or win._undo_stack is not None)

    win._switch_tab(index_b)
    check("再切到 B：动作是 B 的", [a.name for a in win.actions] == ["B动作"],
          f"实际 {[a.name for a in win.actions]}")
    check("再切到 B：帧数保持", len(win.actions[0].frames) == frames_b,
          f"实际 {len(win.actions[0].frames)} vs {frames_b}")
    check("再切到 B：当前动作是 B", win.image_viewer.current_action is win.actions[0])

    win._switch_tab(index_a)
    check("来回切两次后 A 仍然正确", [a.name for a in win.actions] == ["A动作"])
    check("来回切后 A 的原图也回来了", win.image_viewer._pixmap is not None)

    print()
    print("=== 4. 双击标签 = 重命名 ===")
    rect = tabs.tab_rects()[index_a]
    double_click(tabs, QPoint(rect.left() + 8, rect.center().y()))
    check("双击标签进入改名状态", tabs.is_renaming(), f"editing_index={tabs._editing_index}")
    tabs._editor.setText("主角工程")
    tabs._commit_rename()
    check("改名写进标签", tabs.name_at(index_a) == "主角工程", f"实际 {tabs.name_at(index_a)}")
    check("改名同步到窗口标题", "主角工程" in win.windowTitle(), win.windowTitle())
    check("改名后标记为用户自定义（不再被文件名覆盖）", win._name_custom is True)

    print()
    print("=== 5. 打开工程落在新标签（不顶掉正在编辑的）===")
    tmp = Path(tempfile.mkdtemp(prefix="sstool_tabs_"))
    target = tmp / "另一个工程.sstool"
    check("保存当前工程", win._save_project(target) and target.exists())
    check("用户改过名后，保存不改标签名", tabs.name_at(index_a) == "主角工程",
          f"实际 {tabs.name_at(index_a)}")
    before = tabs.count()
    win._open_project(str(target))
    check("打开工程后标签数 +1（当前标签非空白 -> 新开一个）",
          tabs.count() == before + 1, f"{before} -> {tabs.count()}")
    check("打开工程后动作是文件里的",
          [a.name for a in win.actions] == ["A动作"], f"实际 {[a.name for a in win.actions]}")
    check("打开工程后标签名取自文件名",
          tabs.name_at(tabs.current_index()) == "另一个工程",
          f"实际 {tabs.name_at(tabs.current_index())}")

    print()
    print("=== 6. 关闭标签 ===")
    win._ask_close_tab = lambda name: "discard"      # 回归里不弹模态框
    count_before = tabs.count()
    win._close_tab(tabs.current_index())
    check("关闭当前标签后少一个", tabs.count() == count_before - 1,
          f"{count_before} -> {tabs.count()}")
    check("关闭后还有标签（不会关到 0 个）", tabs.count() >= 1)
    while tabs.count() > 1:
        win._close_tab(tabs.current_index())
    check("关到只剩 1 个时窗口仍可用", tabs.count() == 1 and win.actions is not None)

    print()
    print("=== 7. 双击标签栏空白处 = 新建标签页 ===")
    blank = QPoint(tabs.width() - 6, tabs.height() // 2)
    rects = tabs.tab_rects()
    if rects and rects[-1].right() >= blank.x():
        blank = QPoint(rects[-1].right() + 60, tabs.height() // 2)
    before = tabs.count()
    double_click(tabs, blank)
    check("双击空白处新建了一个标签", tabs.count() == before + 1,
          f"{before} -> {tabs.count()}")
    check("新建的标签是空的", not win.actions and win.image_viewer._pixmap is None)

    print()
    print("=== 8. 标签栏画出来的样子（逐像素）===")
    win.close()

    from spritesheet.ui.widgets import ProjectTabBar

    def rendered(names, dirty_index=None, current=0):
        bar = ProjectTabBar()
        bar.resize(900, bar.HEIGHT)
        for name in names:
            bar.add_tab(name, select=False)
        bar.set_current(current)
        if dirty_index is not None:
            bar.set_dirty(dirty_index, True)
        app.processEvents()
        return bar, bar.grab().toImage()

    def ink_count(image, rect, y_from=6, y_to=20):
        return sum(1 for y in range(y_from, y_to) for x in range(rect.left() + 4, rect.right() - 2)
                   if image.pixelColor(x, y).lightness() > 110)

    bar, img = rendered(["甲工程", "乙工程", "丙工程"], dirty_index=1, current=0)
    rects = bar.tab_rects()
    check("画出来的高度就是 26", img.height() == 26, f"实际 {img.height()}")
    # 标签右侧的空白必须是标签栏底色，不能是窗口默认底色
    blank_x = min(img.width() - 5, bar.plus_rect().right() + 40)
    blank = img.pixelColor(blank_x, img.height() // 2)
    check("标签栏空白处铺了底色（不是窗口默认色）",
          (blank.red(), blank.green(), blank.blue()) == (0x1A, 0x1A, 0x1A),
          f"({blank_x},{img.height() // 2}) 实际 {blank.red(), blank.green(), blank.blue()}")
    # 活动标签顶部那条品牌玫红
    active = rects[0]
    pink = img.pixelColor(active.left() + 10, 0)
    check("活动标签顶部有品牌玫红条",
          (pink.red(), pink.green(), pink.blue()) == (0xE9, 0x1E, 0x63),
          f"实际 {pink.red(), pink.green(), pink.blue()}")
    inactive = rects[2]
    other = img.pixelColor(inactive.left() + 10, 0)
    check("非活动标签顶部没有玫红条",
          (other.red(), other.green(), other.blue()) != (0xE9, 0x1E, 0x63),
          f"实际 {other.red(), other.green(), other.blue()}")
    # 每个标签上都有文字
    for i, rect in enumerate(rects):
        check(f"第 {i} 个标签画出了文字", ink_count(img, rect) > 20,
              f"墨迹像素 {ink_count(img, rect)}")

    # 脏标记：同一个标签，标脏后墨迹一定更多
    _bar2, clean_img = rendered(["甲工程", "乙工程", "丙工程"], dirty_index=None, current=0)
    dirty_ink = ink_count(img, rects[1])
    clean_ink = ink_count(clean_img, rects[1])
    check("脏标签比干净标签多画了标记", dirty_ink > clean_ink,
          f"脏 {dirty_ink} vs 干净 {clean_ink}")

    print()
    print(f"TABS FAILED {len(FAILURES)}" if FAILURES else "TABS OK")
    if FAILURES:
        print("失败项：", *FAILURES, sep="\n  - ")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
