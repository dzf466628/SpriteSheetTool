# -*- coding: utf-8 -*-
"""Undo 系统专项测试：模拟真实操作序列，验证回溯不丢历史、不产生多余记录。"""
import sys
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPixmap
from PyQt6.QtCore import QTimer

app = QApplication(sys.argv)

from spritesheet.ui.main_window import MainWindow
from spritesheet.ui.models import Action

passed = 0
failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  OK   {name}")
    else:
        failed += 1
        print(f"  FAIL {name}")


def make_pixmap(w=64, h=64):
    pm = QPixmap(w, h)
    pm.fill()
    return pm


print("=== Undo 系统专项测试 ===\n")

win = MainWindow()

# 准备：加载一张图
pm = make_pixmap(128, 128)
win.image_viewer._pixmap = pm
win.image_viewer._source_images = [pm]
# 模拟检测出帧
win.image_viewer._frames = [(0, 0, 32, 32), (32, 0, 32, 32), (0, 32, 32, 32), (32, 32, 32, 32)]

# 1. 添加动作
n0 = win._undo_stack.count()
win._on_add_action_clicked()
app.processEvents()
n1 = win._undo_stack.count()
check("添加动作产生1条历史", n1 == n0 + 1)

# 2. 选择图块
action = win.actions[0]
win.image_viewer.set_current_action(action)
action.selected_indices = [0, 1]
win._on_selection_changed()
# 等 debounce timer（200ms）
import time
deadline = time.time() + 1.0
while time.time() < deadline:
    app.processEvents()
n2 = win._undo_stack.count()
check("选择图块产生历史记录", n2 > n1)

# 3. 模拟变换（直接改 frame_rotations）
win._begin_transform_history()
action.frame_rotations[0] = 90
action.invalidate_transforms()
win._finish_transform_history()
app.processEvents()
n3 = win._undo_stack.count()
check("变换产生历史记录", n3 > n2)

# 4. 关键测试：回溯到变换之前，变换记录不能消失
win._undo_stack.setIndex(n2)
app.processEvents()
check("回溯后 undo stack 记录数不变（变换记录还在）", win._undo_stack.count() == n3)
check("回溯后 index 在变换之前", win._undo_stack.index() == n2)

# 5. redo 回变换后
win._undo_stack.setIndex(n3)
app.processEvents()
check("redo 后 index 回到变换后", win._undo_stack.index() == n3)
check("redo 后变换状态恢复", action.frame_rotations.get(0) == 90)

# 6. 回溯到最开始，不应该有多余记录
win._undo_stack.setIndex(0)
app.processEvents()
check("回溯到初始后记录数不变", win._undo_stack.count() == n3)

# 7. 回溯后再前进，记录数不变
win._undo_stack.setIndex(n3)
app.processEvents()
check("前进后记录数不变", win._undo_stack.count() == n3)

# 8. 多次连续 undo/redo 不产生新记录
for i in range(3):
    win._undo_stack.setIndex(n2)
    app.processEvents()
    win._undo_stack.setIndex(n3)
    app.processEvents()
check("3次往返undo/redo后记录数不变", win._undo_stack.count() == n3)

# 9. 历史面板树状分组不为空
tree = win._history_panel.tree
check("树状历史有顶级分组", tree.topLevelItemCount() > 0)

# 10. 历史面板能找到子节点
total_children = 0
for i in range(tree.topLevelItemCount()):
    total_children += tree.topLevelItem(i).childCount()
check("树状历史有子操作节点", total_children > 0)

print(f"\n=== 结果: {passed} passed, {failed} failed ===")
sys.exit(1 if failed else 0)
