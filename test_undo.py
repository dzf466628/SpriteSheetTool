# -*- coding: utf-8 -*-
"""Undo 系统专项测试：模拟真实操作序列，验证历史记录全面行为。

覆盖：
- 记录归属：全局操作进全局栈、动作操作进动作自己的栈；
- 单次改动（偏移按钮/动画参数）也要产生记录；
- undo 一步只回退一步（变换后选择 → undo 选择不能把变换抹掉）；
- 删除动作后 undo：动作对象复用、其独立 undo 栈保留；
- 改非当前动作名字/颜色 → 记录进那个动作自己的栈；
- 历史面板树状显示：全局历史 + 各动作分组。
"""
import sys
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import time
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPixmap

app = QApplication(sys.argv)

from spritesheet.ui.main_window import MainWindow

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


def pump(t=0.6):
    """跑事件循环让 debounce timer 触发。"""
    deadline = time.time() + t
    while time.time() < deadline:
        app.processEvents()


def texts(stack):
    return [stack.command(i).text() for i in range(stack.count())]


print("=== Undo 系统专项测试 ===\n")

win = MainWindow()

# 准备：加载一张图
pm = make_pixmap(128, 128)
win.image_viewer._pixmap = pm
win.image_viewer._source_images = [pm]
# 模拟检测出帧
win.image_viewer._frames = [(0, 0, 32, 32), (32, 0, 32, 32), (0, 32, 32, 32), (32, 32, 32, 32)]

# ============================================================ 归属与基础
# 1. 添加动作 → 全局栈
n0 = win._undo_stack.count()
win._on_add_action_clicked()
pump()
n1 = win._undo_stack.count()
check("添加动作产生1条全局历史", n1 == n0 + 1)
check("添加动作记录在全局栈", win._undo_stack.command(n1 - 1).text().startswith("添加动作"))

# 2. 选择图块 → 动作自己的栈
action = win.actions[0]
win.image_viewer.set_current_action(action)
action.selected_indices = [0, 1]
win._on_selection_changed()
pump()
astack = action.undo_stack
n2 = astack.count()
check("选择图块产生历史记录", n2 > 0)
check("选择图块记录在动作栈", astack.command(n2 - 1).text().startswith("选择图块"))
check("全局栈没有选择记录", win._undo_stack.count() == n1)

# 3. 变换 → 动作自己的栈
win._begin_transform_history()
action.frame_rotations[0] = 90
action.invalidate_transforms()
win._finish_transform_history()
pump()
n3 = astack.count()
check("变换产生历史记录", n3 > n2)
check("全局栈仍没有动作内记录", win._undo_stack.count() == n1)

# ============================================================ 回溯不丢、不多记
# 4. 回溯到变换之前，变换记录不能消失
astack.setIndex(n2)
pump()
check("回溯后 undo stack 记录数不变", astack.count() == n3)
check("回溯后 index 在变换之前", astack.index() == n2)

# 5. redo 回变换后
astack.setIndex(n3)
pump()
check("redo 后 index 回到变换后", astack.index() == n3)
check("redo 后变换状态恢复", action.frame_rotations.get(0) == 90)

# 6. 3次往返 undo/redo 不产生新记录
for _ in range(3):
    astack.setIndex(n2)
    pump(0.1)
    astack.setIndex(n3)
    pump(0.1)
check("3次往返undo/redo后记录数不变", astack.count() == n3)

# ============================================================ 单次改动也要记录（问题1）
# 7. 单次偏移按钮点击
ap = win.preview.adjust_panel
ap._action = action
ap._current_frame = 0
n4 = astack.count()
ap._adjust_frame(1, 0)
pump()
check("单次偏移按钮点击产生历史记录", astack.count() == n4 + 1)
check("偏移数据已写入", action.frame_offsets.get(0) == (1, 0))

# 8. undo 单次偏移 → 还原
astack.undo()
pump()
check("undo单次偏移后还原", action.frame_offsets.get(0) is None)

# 9. 单次动画参数（fps）修改
# 注意：刚才 undo 过偏移（index 回退），此时新记录按 Qt 语义**替换**被撤销的分支，
# 所以断言"最后一条记录是 fps"而不是"数量+1"。
win.anim_bar.set_action(action)
win.anim_bar._on_fps_changed(12)
pump()
check("单次fps修改产生历史记录", astack.command(astack.count() - 1).text().startswith("修改动画参数"))
check("fps已生效", action.fps == 12)
astack.undo()
pump()
check("undo单次fps后还原", action.fps == 10)

# ============================================================ 不再跨步回滚（问题2）
# 10. 选择→变换→再选择→undo 一步：变换保留、只回退选择
action.frame_offsets.clear()
astack.setIndex(n2)  # 回到"选择[0,1]"之后
pump()
win._begin_transform_history()
action.frame_rotations[0] = 90
action.invalidate_transforms()
win._finish_transform_history()
pump(0.1)
action.selected_indices = [0, 1, 2]
win._on_selection_changed()
pump()
n_before_undo = astack.count()
check("再选择产生了记录", n_before_undo > n2)
astack.undo()
pump()
check("undo一步后选择回退", list(action.selected_indices) == [0, 1])
check("undo一步后变换保留（不跨步回滚）", action.frame_rotations.get(0) == 90)

# ============================================================ 历史面板树状
# 11. 面板树包含"全局历史"与动作分组
tree = win._history_panel.tree
check("树状历史有顶级分组", tree.topLevelItemCount() > 0)
top_texts = [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]
check("面板含全局历史组", any("全局历史" in t for t in top_texts))
check("面板含动作分组", any("动作：" in t for t in top_texts))
total_children = sum(tree.topLevelItem(i).childCount() for i in range(tree.topLevelItemCount()))
check("树状历史有子操作节点", total_children > 0)

# ============================================================ 多动作隔离
# 12. 动作2、3存在时，undo 动作1不影响动作2、3
win._on_add_action_clicked()
pump()
a2 = win.actions[-1]
win.image_viewer.set_current_action(a2)
a2.selected_indices = [2, 3]
win._on_selection_changed()
pump()

win._on_add_action_clicked()
pump()
a3 = win.actions[-1]
win.image_viewer.set_current_action(a3)
a3.selected_indices = [0, 1, 2]
win._on_selection_changed()
pump()

# 切回动作1，把它自己的历史全部撤销
win.image_viewer.set_current_action(action)
sel_before_undo_a2 = list(a2.selected_indices)
sel_before_undo_a3 = list(a3.selected_indices)
action.undo_stack.setIndex(0)
pump()
check("undo动作1不影响动作2", list(a2.selected_indices) == sel_before_undo_a2)
check("undo动作1不影响动作3", list(a3.selected_indices) == sel_before_undo_a3)
check("动作1的选择被undo", list(action.selected_indices) == [])

# ============================================================ 删除动作 undo 保留动作栈（问题3）
# 13. 删除动作2 → undo → 对象复用、独立栈保留
stack2_before = a2.undo_stack.count()
g_before = win._undo_stack.count()
win._delete_action(a2)
pump()
check("删除动作产生全局记录", win._undo_stack.count() == g_before + 1)
check("删除后动作2不在列表", all(a is not a2 for a in win.actions))
win._undo_stack.undo()
pump()
a2_restored = next((x for x in win.actions if x is a2), None)
check("undo删除动作找回同一对象", a2_restored is a2)
check("动作2的独立undo栈保留", a2_restored is not None and a2_restored.undo_stack.count() == stack2_before)

# ============================================================ 改非当前动作的名字
# 14. 当前是动作1，改动作2的名字 → 记录进动作2自己的栈
win.image_viewer.set_current_action(action)
n_a2 = a2.undo_stack.count()
n_a1 = action.undo_stack.count()
win._on_action_name_changed(a2, "改名的动作2")
pump()
check("改名记录在目标动作自己的栈", a2.undo_stack.count() == n_a2 + 1)
check("改名文字正确", a2.undo_stack.command(a2.undo_stack.count() - 1).text().startswith("改动作名"))
check("动作1栈不受改名影响", action.undo_stack.count() == n_a1)

# ============================================================ 动作名一致时面板分组仍正确
win._history_panel.rebuild()
top_texts = [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]
check("改名后面板刷新含动作2", any("改名的动作2" in t for t in top_texts))

win.close()

# ============================================================ 工程 / 图片 / JSON 与历史记录的衔接
# 15. 切标签、追加图片、导入 JSON：不能丢记录、不能误清历史、归属不能串
from unittest import mock  # noqa: E402
import json  # noqa: E402
import tempfile  # noqa: E402

mock.patch("spritesheet.ui.main_window.QMessageBox").start()   # offscreen 下模态框会永久阻塞
win = MainWindow()
pm = make_pixmap(128, 128)
win.image_viewer._pixmap = pm
win.image_viewer._source_images = [pm]
win.image_viewer._frames = [(0, 0, 32, 32), (32, 0, 32, 32), (0, 32, 32, 32), (32, 32, 32, 32)]
win._on_add_action_clicked()
pump()
a1 = win.actions[0]
win.image_viewer.set_current_action(a1)
a1.selected_indices = [0, 1]
win._on_selection_changed()          # 只 schedule，不 pump：模拟"改完立刻切标签"
check("切标签前待决记录未落账", a1.undo_stack.count() == 0)
win._new_tab(switch=True)
check("切标签前自动落账", a1.undo_stack.count() == 1)
win._switch_tab(0)
pump()
check("切回原标签动作还是同一对象", win.actions[0] is a1)
check("切回后记录还在", a1.undo_stack.count() == 1)

# 16. 追加图片：保留已有历史 + 记一条全局操作 + undo 能还原原图
g_before = win._undo_stack.count()
global_before = [win._undo_stack.command(i).text() for i in range(g_before)]
a1_stack_before = a1.undo_stack.count()
old_pixmap = win.image_viewer._pixmap
win._on_image_loaded(make_pixmap(64, 64))
pump()
check("追加图片产生全局记录", win._undo_stack.count() == g_before + 1)
check("追加图片记录文字正确",
      win._undo_stack.command(win._undo_stack.count() - 1).text().startswith("添加第 2 张原图"))
check("追加图片不清空已有历史",
      [win._undo_stack.command(i).text() for i in range(g_before)] == global_before)
check("追加图片不动动作自己的栈", a1.undo_stack.count() == a1_stack_before)
check("原图确实追加了", win.image_viewer._pixmap is not old_pixmap)
win._undo_stack.undo()
pump()
check("undo 追加图片还原原图", win.image_viewer._pixmap is old_pixmap)
check("undo 追加图片还原源图数量", len(win.image_viewer._source_images) == 1)
win._undo_stack.redo()
pump()
check("redo 追加图片回到追加后", win.image_viewer._pixmap is not old_pixmap)

# 17. 导入 JSON 到本工程：动作归属 + 全局记录 + 保留历史
tmp = tempfile.mkdtemp()
atlas = make_pixmap(64, 64)
atlas.save(os.path.join(tmp, "atlas.png"))
with open(os.path.join(tmp, "atlas.json"), "w", encoding="utf-8") as fh:
    json.dump({
        "frames": [
            {"filename": "f0", "frame": {"x": 0, "y": 0, "w": 32, "h": 32}, "duration": 100},
            {"filename": "f1", "frame": {"x": 32, "y": 0, "w": 32, "h": 32}, "duration": 100},
        ],
        "meta": {"image": "atlas.png", "size": {"w": 64, "h": 64}},
    }, fh)
from spritesheet.importer import load_project  # noqa: E402
json_path = os.path.join(tmp, "atlas.json")
expected_actions = len(load_project(json_path).actions)
g_before = win._undo_stack.count()
n_actions_before = len(win.actions)
win._import_project(json_path, target="current")
pump()
check("导入 JSON 追加了动作", len(win.actions) == n_actions_before + expected_actions)
check("导入 JSON 产生全局记录", win._undo_stack.count() == g_before + 1)
check("导入 JSON 记录文字含动作数",
      "导入" in win._undo_stack.command(win._undo_stack.count() - 1).text())
imported = win.actions[-1]
check("导入的动作有自己的独立栈", imported.undo_stack is not None)
check("导入的动作进历史面板分组",
      any("动作：" in tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())))
win.image_viewer.set_current_action(imported)
imported.selected_indices = [0]
win._on_selection_changed()
pump()
check("导入动作内改动记进它自己的栈", imported.undo_stack.count() == 1)
check("全局栈没被动作内改动污染", win._undo_stack.count() == g_before + 1)
check("导入前的历史保留", win._undo_stack.command(0).text().startswith("添加动作"))
check("原动作的栈没被导入影响", a1.undo_stack.count() == a1_stack_before)
win._undo_stack.undo()
pump()
check("undo 导入还原动作数量", len(win.actions) == n_actions_before)

# 18. 检测完成后快照基线刷新（否则之后第一次动作内改动会拿旧状态当 before）
win._on_frames_updated()
check("检测完成后基线已刷新", win._last_snapshot_state == win._capture_snapshot())

# 19. 导入到新建工程：从干净历史起步
tmp2 = tempfile.mkdtemp()
make_pixmap(64, 64).save(os.path.join(tmp2, "atlas2.png"))
with open(os.path.join(tmp2, "atlas2.json"), "w", encoding="utf-8") as fh:
    json.dump({
        "frames": [{"filename": "f0", "frame": {"x": 0, "y": 0, "w": 32, "h": 32}, "duration": 100}],
        "meta": {"image": "atlas2.png", "size": {"w": 64, "h": 64}},
    }, fh)
win._import_project(os.path.join(tmp2, "atlas2.json"), target="new")
pump()
check("新工程只留导入这一条记录", win._undo_stack.count() == 1)
check("新工程第一条记录是导入", win._undo_stack.command(0).text().startswith("导入"))
check("新工程导入的动作都有独立栈", all(a.undo_stack is not None for a in win.actions))

# 20. 工程与工程之间：打开 .sstool 走新标签、历史各归各的、互不串
from spritesheet.project_file import write_project  # noqa: E402
proj_path = os.path.join(tmp2, "proj.sstool")
manifest, sections = win._project_manifest()
write_project(proj_path, manifest, sections)
tab_before = win.tabs.count()
history_before_open = [win._undo_stack.command(i).text() for i in range(win._undo_stack.count())]
check("打开工程前当前标签有历史", len(history_before_open) > 0)
check("打开工程成功", win._open_project(proj_path) is True)
pump()
check("打开工程落到了新标签", win.tabs.count() == tab_before + 1)
check("新工程历史从干净开始", win._undo_stack.count() == 0)
check("新工程所有动作的栈都是空的",
      all(a.undo_stack is not None and a.undo_stack.count() == 0 for a in win.actions))
win._switch_tab(tab_before - 1)      # 切回打开工程前那个活文档（导入的工程）
pump()
check("切回原工程历史仍在",
      [win._undo_stack.command(i).text() for i in range(win._undo_stack.count())] == history_before_open)
check("切回原工程后动作栈没被新工程串掉",
      all(a.undo_stack is not None for a in win.actions))

win.close()

print(f"\n=== 结果: {passed} passed, {failed} failed ===")
sys.exit(1 if failed else 0)
