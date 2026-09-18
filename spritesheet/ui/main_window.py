"""主窗口：系统标题栏 + 菜单栏 + Logo 名称栏 + 左侧边栏 + 原图区(带标尺) + 右侧预览 + 状态栏。

本模块经 SPLIT_GUIDE 步骤 3-7 拆分后只保留：
- ``UpdateDownloadDialog``：自动更新下载弹窗（主窗口专用）；
- ``_TransformHistoryCommand``：帧变换撤销命令；
- ``MainWindow``：主窗口装配（菜单、侧边栏、原图区/预览区接线、工程读写、导入导出、自动更新）。

其余部件已迁出：
``widgets.py``（通用控件）/ ``image_viewer.py``（原图区）/ ``preview.py``（预览区）/
``export_dialog.py``（导出弹窗）；工程序列化辅助在 ``spritesheet/project_file.py``。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, QSize, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import (
    QAction, QColor, QKeySequence, QPainter, QPen, QPixmap, QUndoCommand, QUndoStack,
)
from PyQt6.QtWidgets import (
    QApplication, QDialog, QDockWidget, QFileDialog, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QSplitter, QVBoxLayout, QWidget,
    QTreeWidget, QTreeWidgetItem,
)

from spritesheet import __app_name__, __version__
from spritesheet.project_file import (
    _pixmap_bytes, _pixmap_from_bytes, _rect_from_dict, read_project, write_project,
)
from spritesheet.settings import (
    last_directory, set_last_directory, show_project_thumbnail,
)
from spritesheet.updater import UPDATE_URL, UpdateCheckThread, UpdateDownloadThread

from .anim_settings import AnimSettingsBar
from .image_viewer import SUPPORTED_IMAGE_EXT, ImageViewer
from .models import Action, MACARON_COLORS
from .preview import ActionPreviewWidget
from .settings_dialog import SettingsDialog
from .transform import action_frame_pixmap
from .widgets import (
    ActionCardWidget, ActionItemDelegate, AppNameLabel, ProjectTabBar, _APP_ROOT,
    paint_checkerboard,
)

# 工程文件缩略图：**正方形** 256×256（资源管理器用方形槽位显示，按画面比例输出
# 会让宽条素材显示成一条）。
#   底色  = 灰色棋盘格（与软件预览区同一套配色），内容装不满时由它兜底
#   描边  = 黑 → 白 → 粉 三层（由外到内），把缩略图"罩"起来
#   角标  = 工程图标.png，右下角，占短边 _THUMB_BADGE_DIV 分之一
_THUMB_SIZE = 256
_THUMB_PAD = 6                      # 内容相对描边内缘再留的空白
_THUMB_CHECKER_CELL = 12
_THUMB_BADGE_DIV = 3                # 角标 = 短边/3（此前是 1/4，用户要求再大些）
# 描边三层（外→内）。红色用**角标图的品牌粉** 工程图标.png 的主色 #FB5582，
# 和右下角标同色；界面强调色是 theme.ACCENT #E91E63，要换成它就改这一处。
_THUMB_STROKE = (("#000000", 4), ("#FFFFFF", 4), ("#FB5582", 6))


# 主窗口
class UpdateDownloadDialog(QDialog):
    """自动更新下载对话框（深色风格 + 实时进度 + 取消）。"""

    download_ok = pyqtSignal(str)  # 安装包本地路径

    def __init__(self, version: str, url: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("正在更新")
        self.setModal(True)
        self.setFixedWidth(420)
        self._url = url
        self._thread: UpdateDownloadThread | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        title = QLabel(f"正在下载 v{version} ...")
        title.setStyleSheet("color: #ffffff; font-size: 14px; font-weight: bold;")
        layout.addWidget(title)

        self.lbl_status = QLabel("连接服务器 ...")
        self.lbl_status.setStyleSheet("color: #9a9a9a; font-size: 12px;")
        layout.addWidget(self.lbl_status)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setStyleSheet(
            "QProgressBar { background: #2b2b2b; border: 1px solid #3d3d3d; border-radius: 4px;"
            " height: 14px; text-align: center; color: #ffffff; font-size: 10px; }"
            "QProgressBar::chunk { background: #E91E63; border-radius: 3px; }"
        )
        layout.addWidget(self.progress)

        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.clicked.connect(self._on_cancel)
        layout.addWidget(self.btn_cancel, 0, Qt.AlignmentFlag.AlignRight)

        self._start()

    def _start(self):
        import tempfile
        target_dir = os.path.join(tempfile.gettempdir(), "SpriteSheetToolUpdate")
        self._thread = UpdateDownloadThread(self._url, target_dir, self)
        self._thread.progress.connect(self._on_progress)
        self._thread.download_finished.connect(self._on_finished)
        self._thread.download_failed.connect(self._on_failed)
        self._thread.finished.connect(self._on_thread_done)
        self._thread.start()

    def _on_progress(self, done: int, total: int):
        if total > 0:
            percent = int(done * 100 / total)
            self.progress.setValue(percent)
            self.lbl_status.setText(
                f"已下载 {done / 1024 / 1024:.1f} MB / {total / 1024 / 1024:.1f} MB"
            )
        else:
            self.lbl_status.setText(f"已下载 {done / 1024 / 1024:.1f} MB")

    def _on_finished(self, path: str):
        self.lbl_status.setText("下载完成，即将安装 ...")
        self.progress.setValue(100)
        self.btn_cancel.setEnabled(False)
        QTimer.singleShot(300, lambda: (self.accept(), self.download_ok.emit(path)))

    def _on_failed(self, reason: str):
        QMessageBox.warning(self, "下载失败", f"下载更新失败：\n{reason}")
        self.reject()

    def _on_cancel(self):
        if self._thread is not None:
            self._thread.cancel()
        self.reject()

    def _on_thread_done(self):
        self._thread = None


class _TransformHistoryCommand(QUndoCommand):
    """Undo command for per-frame free-transform state."""

    def __init__(self, owner, before, after, text: str = "变换") -> None:
        super().__init__(text)
        self._owner = owner
        self.before = before
        self.after = after
        self._initial_redo = True

    def redo(self) -> None:
        if self._initial_redo:
            self._initial_redo = False
            return
        self._owner._restore_transform_state(self.after)

    def undo(self) -> None:
        self._owner._restore_transform_state(self.before)


class _SnapshotCommand(QUndoCommand):
    """全量快照式 undo：保存整个工程状态（含帧图），undo/redo 恢复。"""

    def __init__(self, owner, before: dict, after: dict, text: str) -> None:
        super().__init__(text)
        self._owner = owner
        self.before = before
        self.after = after
        self._initial_redo = True

    def redo(self) -> None:
        if self._initial_redo:
            self._initial_redo = False
            return
        self._owner._restore_snapshot(self.after)

    def undo(self) -> None:
        self._owner._restore_snapshot(self.before)


class AboutDialog(QDialog):
    """关于对话框：GPL v3 声明 + 第三方组件清单。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("关于 SpriteSheetTool")
        self.setModal(True)
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        title = QLabel(f"SpriteSheetTool  v{__version__}")
        title.setStyleSheet("color: #ffffff; font-size: 18px; font-weight: bold;")
        layout.addWidget(title)

        subtitle = QLabel("精灵图打包工具：自动检测元素、按动作编排动画、自由变换、一键导出图集/GIF/JSON")
        subtitle.setStyleSheet("color: #9a9a9a; font-size: 11px;")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        layout.addSpacing(4)

        gpl_label = QLabel(
            "本软件以 <span style='color:#E91E63;'>GNU GPL v3</span> 开源，"
            "你可以自由使用、修改和再分发。"
            "程序按\"原样\"提供，不附带任何担保。"
        )
        gpl_label.setStyleSheet("color: #e8e8e8; font-size: 11px;")
        gpl_label.setWordWrap(True)
        layout.addWidget(gpl_label)

        layout.addSpacing(4)

        third_title = QLabel("使用的第三方开源组件：")
        third_title.setStyleSheet("color: #e8e8e8; font-size: 12px; font-weight: bold;")
        layout.addWidget(third_title)

        components = [
            ("PyQt6 6.11", "GPL v3 / Commercial — Riverbank Computing"),
            ("Pillow 12.3", "HPND License — Python Imaging Library"),
            ("PyInstaller 6.22", "GPL v2+ with Bootloader Exception"),
            ("Inno Setup 6", "Inno Setup License — Jordan Russell"),
            ("简体中文翻译", "Inno Setup 社区翻译 — kira-96"),
        ]
        for name, desc in components:
            item = QLabel(f"  •  <span style='color:#E91E63;'>{name}</span>  —  {desc}")
            item.setStyleSheet("color: #b0b0b0; font-size: 10px;")
            item.setWordWrap(True)
            layout.addWidget(item)

        layout.addSpacing(8)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.setFixedWidth(90)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)


class HistoryPanel(QWidget):
    """树状操作历史：按动作分組，双击跳转。"""

    def __init__(self, stack: QUndoStack, parent=None) -> None:
        super().__init__(parent)
        self._stack = stack
        self.setObjectName("historyPanel")
        self.setStyleSheet(
            "#historyPanel { background: #1e1e1e; border: 1px solid #3d3d3d; border-radius: 6px; }"
        )
        self.setMinimumWidth(200)
        self.setMaximumWidth(280)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setStyleSheet(
            "QTreeWidget { background: #1a1a1a; color: #e0e0e0; border: 1px solid #3d3d3d; border-radius: 4px; }"
            "QTreeWidget::item { padding: 3px 6px; font-size: 11px; }"
            "QTreeWidget::item:selected { background: rgba(233,30,99,0.3); }"
            "QTreeBranch:open, QTreeBranch:closed { color: #F06292; }"
        )
        self.tree.itemDoubleClicked.connect(self._jump_to)
        layout.addWidget(self.tree)

        self._refresh()
        stack.indexChanged.connect(self._refresh)

    def _parse_action(self, text: str) -> str:
        """从'变换帧3「动作1」(rotate)'提取动作名'动作1'。"""
        if "「" in text and "」" in text:
            return text.split("「", 1)[1].split("」", 1)[0]
        return "全局"

    def _refresh(self, *_):
        self.tree.clear()
        idx = self._stack.index()
        groups: dict[str, list[tuple[int, str]]] = {}
        # 初始状态
        groups.setdefault("全局", []).append((0, "● 初始状态"))
        for i in range(self._stack.count()):
            cmd = self._stack.command(i)
            text = cmd.text() or f"操作 {i+1}"
            action = self._parse_action(text)
            groups.setdefault(action, []).append((i + 1, text))
        # 建树
        for action, items in groups.items():
            parent = QTreeWidgetItem(self.tree, [f"▸ {action} ({len(items)})"])
            parent.setForeground(0, QColor("#F06292"))
            for undo_idx, text in items:
                child = QTreeWidgetItem(parent, [text])
                child.setData(0, Qt.ItemDataRole.UserRole, undo_idx)
                if undo_idx == idx:
                    child.setBackground(0, QColor(233, 30, 99, 60))
                    child.setForeground(0, QColor("#ffffff"))
            parent.setExpanded(True)

    def _jump_to(self, item, *_):
        if item.childCount() > 0:
            return  # 点的是分组头，不跳转
        idx = item.data(0, Qt.ItemDataRole.UserRole)
        if idx is not None:
            self._stack.setIndex(int(idx))


class MainWindow(QMainWindow):
    """主窗口：系统标题栏 + 菜单栏 + Logo名称栏 + 左侧边栏 + 原图区 + 右侧预览 + 状态栏。"""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{__app_name__} v{__version__}")
        # Match the supplied reference layout.  Keep this as both the default
        # and the minimum so the adjustment panel is never vertically clipped.
        self.setMinimumSize(1000, 1000)
        self.resize(1000, 1000)
        self.setAcceptDrops(True)   # 支持拖入图片或 JSON 工程
        self.actions: list[Action] = []
        self._undo_stack = QUndoStack(self)
        from spritesheet.settings import undo_limit
        self._undo_stack.setUndoLimit(undo_limit())
        self._transform_history_state = None
        self._transform_history_pending = None
        self._restoring_transform_history = False
        self._project_path: Path | None = None
        self._project_json_path: str | None = None  # 当前工程关联的 JSON 文件路径
        self._project_dirty = False
        # 工程显示名（标签页上那个）与"是否被用户改过名"。改名后不再被文件名覆盖。
        self._project_name = "未命名工程"
        self._name_custom = False
        # 标签页文档表：每项 {name, dirty, state}。state 为 None 表示这份正"活着"
        # （数据就在 self.actions / image_viewer / preview 里），否则是切走时抓的快照。
        self._documents: list[dict] = []
        self._act_undo = None
        self._build_ui()
        self._build_menu()
        self._transform_history_state = self._capture_transform_state()
        # 起始标签页
        self._documents.append({"name": self._project_name, "dirty": False, "state": None})
        self.tabs.add_tab(self._project_name)
        self._refresh_tab_label()
        self._set_project_title()

        # 右侧浮动窗口：历史记录（独立窗口，有关闭按钮，不挤压主窗口）
        self._history_panel = HistoryPanel(self._undo_stack, self)
        self._history_dock = QDockWidget("历史记录", self)
        self._history_dock.setWidget(self._history_panel)
        self._history_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetMovable
        )
        self._history_dock.setFloating(True)
        self._history_dock.setStyleSheet(
            "QDockWidget { color: #e0e0e0; }"
            "QDockWidget::title { background: #2a2a2a; padding: 4px 8px; border-bottom: 1px solid #3d3d3d; }"
        )
        self._history_dock.setMinimumWidth(180)
        self._history_dock.setMaximumWidth(240)
        self._history_dock.setMinimumHeight(300)
        # 放在主窗口右侧
        self._history_dock.move(self.x() + self.width() + 10, self.y() + 80)
        self._history_dock.show()

    def _capture_transform_state(self):
        """Capture only frame transform fields, keeping undo focused and cheap."""
        state = []
        for action in self.actions:
            state.append((
                id(action), action,
                tuple(sorted((int(k), int(v)) for k, v in action.frame_rotations.items())),
                tuple(sorted(int(k) for k in action.frame_mirror_h)),
                tuple(sorted(int(k) for k in action.frame_mirror_v)),
                tuple(sorted((int(k), tuple(float(x) for x in v))
                             for k, v in action.frame_rotation_pivots.items())),
                tuple(sorted((int(k), tuple(float(x) for x in v))
                             for k, v in action.frame_quads.items())),
                tuple(sorted((int(k), tuple(sorted(str(x) for x in v)))
                             for k, v in action.frame_transform_modes.items())),
            ))
        return tuple(state)

    def _restore_transform_state(self, state) -> None:
        if self._restoring_transform_history:
            return
        self._restoring_transform_history = True
        self._suppress_selection_snapshot = True
        # 停掉所有 pending debounce timer
        for tname in ("_selection_snap_timer", "_frame_snap_timer", "_anim_snap_timer", "_offset_snap_timer"):
            t = getattr(self, tname, None)
            if t is not None:
                t.stop()
        self._snapshot_before = None
        self._offset_snap_before = None
        self.preview.blockSignals(True)
        self.image_viewer.blockSignals(True)
        self.action_list.blockSignals(True)
        try:
            restored_ids = set()
            for _, action, rotations, mirror_h, mirror_v, pivots, quads, modes in state:
                if action not in self.actions:
                    continue
                restored_ids.add(id(action))
                action.frame_rotations = dict(rotations)
                action.frame_mirror_h = set(mirror_h)
                action.frame_mirror_v = set(mirror_v)
                action.frame_rotation_pivots = dict(pivots)
                action.frame_quads = dict(quads)
                action.frame_transform_modes = {k: set(v) for k, v in modes}
                action.invalidate_transforms()
            # A snapshot may predate a newly created action. Undoing to it
            # must also clear transforms on actions absent from that snapshot.
            for action in self.actions:
                if id(action) in restored_ids:
                    continue
                action.frame_rotations.clear()
                action.frame_mirror_h.clear()
                action.frame_mirror_v.clear()
                action.frame_rotation_pivots.clear()
                action.frame_quads.clear()
                action.frame_transform_modes.clear()
                action.invalidate_transforms()
            self._transform_history_state = self._capture_transform_state()
            if getattr(self, "preview", None) is not None:
                self.preview.adjust_panel._sync_transform_widgets()
                self.preview.adjust_panel._refresh_canvas_label()
                self.preview.canvas.update()
                self.preview._refresh_thumbs()
                self.preview.adjust_panel._transform_changed = False
        finally:
            self._restoring_transform_history = False
            self.preview.blockSignals(False)
            self.image_viewer.blockSignals(False)
            self.action_list.blockSignals(False)
            self._suppress_selection_snapshot = False
            self._last_selection_state = None
            self._offset_snap_before = None
            self._snapshot_before = None

    def _begin_transform_history(self) -> None:
        self._suppress_selection_snapshot = True
        if not self._restoring_transform_history and self._transform_history_pending is None:
            self._transform_history_pending = self._capture_transform_state()

    def _transform_text(self) -> str:
        """根据当前变换模式生成历史记录文字。"""
        cur = self.image_viewer.current_action
        name = f"「{cur.name}」" if cur else ""
        mode = getattr(self.preview.canvas, "_transform_mode", "")
        fi = getattr(self.preview, "_frame_index", 0)
        if mode == "all":
            return f"变换全部帧{name}"
        if mode:
            return f"变换帧{fi+1}{name}({mode})"
        return f"变换{name}"

    def _finish_transform_history(self) -> None:
        self._suppress_selection_snapshot = False
        if self._transform_history_pending is None:
            return
        before = self._transform_history_pending
        self._transform_history_pending = None
        after = self._capture_transform_state()
        if before != after and not self._restoring_transform_history:
            self._undo_stack.push(_TransformHistoryCommand(self, before, after, self._transform_text()))
        self._transform_history_state = after

    def _on_transform_history_changed(self) -> None:
        if self._restoring_transform_history:
            return
        current = self._capture_transform_state()
        if self._transform_history_pending is not None:
            return
        before = self._transform_history_state
        if before is not None and before != current:
            self._undo_stack.push(_TransformHistoryCommand(self, before, current, self._transform_text()))
        self._transform_history_state = current

    def _reset_transform_history(self) -> None:
        self._undo_stack.clear()
        self._transform_history_pending = None
        self._transform_history_state = self._capture_transform_state()

    def _capture_snapshot(self) -> dict:
        """纯数据快照：不编码任何 PNG，只存 actions 的数据字段，帧图从 atlas 重建。"""
        viewer = self.image_viewer
        actions = []
        for action in self.actions:
            parts = []
            for frame_parts in action.frame_parts:
                parts.append([
                    [list(rect), int(rx), int(ry), int(ox), int(oy)]
                    for rect, rx, ry, ox, oy in frame_parts
                ])
            actions.append({
                "name": action.name, "color": action.color,
                "selected_indices": list(action.selected_indices),
                "composite_groups": [list(g) for g in action.composite_groups],
                "frame_layouts": None if action.frame_layouts is None else [
                    [[int(i), int(x), int(y)] for i, x, y in layout]
                    for layout in action.frame_layouts
                ],
                "frame_layouts_base": action.frame_layouts_base,
                "frame_rects": [list(r) for r in action.frame_rects],
                "frame_parts": parts,
                "frame_count": len(action.frames),
                "fps": action.fps, "loop_mode": action.loop_mode,
                "padding": action.padding, "ground_enabled": action.ground_enabled,
                "ground_offset": action.ground_offset,
                "reuse_frame_settings": action.reuse_frame_settings,
                "show_anchor": action.show_anchor, "anchor": list(action.anchor),
                "frame_offsets": {str(k): list(v) for k, v in action.frame_offsets.items()},
                "frame_durations": {str(k): int(v) for k, v in action.frame_durations.items()},
                "frame_rotations": {str(k): v for k, v in action.frame_rotations.items()},
                "frame_mirror_h": sorted(action.frame_mirror_h),
                "frame_mirror_v": sorted(action.frame_mirror_v),
                "frame_rotation_pivots": {str(k): list(v) for k, v in action.frame_rotation_pivots.items()},
                "frame_quads": {str(k): list(v) for k, v in action.frame_quads.items()},
                "frame_transform_modes": {str(k): sorted(v) for k, v in action.frame_transform_modes.items()},
                "ref_frame": action.ref_frame, "ref_opacity": action.ref_opacity,
                "frame_opacity": action.frame_opacity, "bg_mode": action.bg_mode,
                "action_type": action.action_type, "move_direction": action.move_direction,
                "move_speed": action.move_speed,
            })
        cur_idx = self.actions.index(viewer.current_action) if viewer.current_action in self.actions else -1
        return {
            "format": "spritesheet-tool-project",
            "actions": actions,
            "preview": {"action_index": cur_idx},
            "viewer": {
                "frames": [list(r) for r in viewer._frames],
                "frame_logical": {str(k): list(v) for k, v in viewer._frame_logical.items()},
            },
        }

    def _restore_snapshot(self, snap: dict) -> None:
        """从轻量快照恢复工程：恢复 actions 数据，帧图从 atlas 重建。"""
        self._suppress_selection_snapshot = True
        # 停掉所有 pending debounce timer，避免 restore 后误触发 push
        for tname in ("_selection_snap_timer", "_frame_snap_timer", "_anim_snap_timer", "_offset_snap_timer"):
            t = getattr(self, tname, None)
            if t is not None:
                t.stop()
        self._snapshot_before = None
        self._offset_snap_before = None
        # 彻底屏蔽 restore 期间所有信号，避免任何自动记录
        self.preview.blockSignals(True)
        self.image_viewer.blockSignals(True)
        self.action_list.blockSignals(True)
        try:
            viewer = self.image_viewer
            viewer._detect_generation += 1
            self._end_all_renames()
            self.actions.clear()
            self.action_list.clear()
            for raw in snap.get("actions", []):
                action = Action(str(raw.get("name", "未命名动作")), str(raw.get("color", "#E91E63")))
                for attr in ("fps", "loop_mode", "padding", "ground_enabled", "ground_offset",
                             "reuse_frame_settings", "show_anchor", "ref_frame", "ref_opacity",
                             "frame_opacity", "bg_mode", "action_type", "move_direction", "move_speed"):
                    if attr in raw:
                        setattr(action, attr, raw[attr])
                action.selected_indices = [int(x) for x in raw.get("selected_indices", [])]
                action.composite_groups = [[int(x) for x in g] for g in raw.get("composite_groups", [])]
                layouts = raw.get("frame_layouts")
                action.frame_layouts = None if layouts is None else [
                    [(int(i), int(x), int(y)) for i, x, y in layout] for layout in layouts
                ]
                action.frame_layouts_base = int(raw.get("frame_layouts_base", 0))
                action.frame_rects = [tuple(map(int, x)) for x in raw.get("frame_rects", [])]
                action.frame_parts = []
                for frame_parts in raw.get("frame_parts", []):
                    parsed = []
                    for part in frame_parts:
                        if len(part) >= 5:
                            parsed.append((tuple(map(int, part[0])), int(part[1]), int(part[2]), int(part[3]), int(part[4])))
                    action.frame_parts.append(parsed)
                action.anchor = tuple(float(x) for x in self._tuple2(raw.get("anchor"), (0.5, 0.5)))
                action.frame_offsets = {int(k): tuple(map(int, v)) for k, v in (raw.get("frame_offsets") or {}).items()}
                action.frame_durations = {int(k): max(1, int(v)) for k, v in (raw.get("frame_durations") or {}).items()}
                action.frame_rotations = {int(k): int(v) for k, v in (raw.get("frame_rotations") or {}).items()}
                action.frame_mirror_h = set(map(int, raw.get("frame_mirror_h", [])))
                action.frame_mirror_v = set(map(int, raw.get("frame_mirror_v", [])))
                action.frame_rotation_pivots = {int(k): tuple(map(float, v)) for k, v in (raw.get("frame_rotation_pivots") or {}).items()}
                action.frame_quads = {int(k): tuple(map(float, v)) for k, v in (raw.get("frame_quads") or {}).items()}
                action.frame_transform_modes = {int(k): set(v) for k, v in (raw.get("frame_transform_modes") or {}).items()}
                action.invalidate_transforms()
                self.actions.append(action)
                self._append_action_card(action)
            viewer.set_actions(self.actions)
            viewer.set_current_action(None)
            # 恢复 viewer frames/logical
            vstate = snap.get("viewer") or {}
            if vstate.get("frames"):
                viewer._frames = [tuple(map(int, r)) for r in vstate["frames"]]
            if vstate.get("frame_logical"):
                viewer._frame_logical = {int(k): tuple(map(int, v)) for k, v in vstate["frame_logical"].items()}
            # 从 atlas 重建帧图
            for action in self.actions:
                viewer.crop_frames_for_action(action)
            preview_state = snap.get("preview") or {}
            ai = int(preview_state.get("action_index", -1))
            action = self.actions[ai] if 0 <= ai < len(self.actions) else None
            if action is not None:
                viewer.set_current_action(action)
                self.action_list.setCurrentRow(ai)
                self.preview.set_action(action)
            self._transform_history_state = self._capture_transform_state()
            viewer.canvas.update()
        except Exception as e:  # noqa: BLE001
            print(f"[undo] 恢复快照失败: {e}", file=sys.stderr)
        finally:
            self.preview.blockSignals(False)
            self.image_viewer.blockSignals(False)
            self.action_list.blockSignals(False)
            self._suppress_selection_snapshot = False
            # 重置选择快照缓存，避免 undo 后下次选择用错 before
            self._last_selection_state = None
            self._offset_snap_before = None
            self._snapshot_before = None

    def _push_snapshot(self, text: str) -> None:
        """在结构性操作（增删动作/帧等）前调用：记录操作前快照，操作后自动补 after。

        用法：操作前调一次 before = self._capture_snapshot()，操作完后调
        self._push_snapshot_done(before, "操作名")。本方法只记 before。
        """
        self._snapshot_before = self._capture_snapshot()
        self._snapshot_text = text

    def _push_snapshot_done(self) -> None:
        """结构性操作完成后调用：push snapshot command。"""
        before = getattr(self, "_snapshot_before", None)
        if before is None:
            return
        after = self._capture_snapshot()
        if before != after:
            self._undo_stack.push(_SnapshotCommand(self, before, after, self._snapshot_text))
        self._snapshot_before = None
        self._snapshot_text = ""

    @staticmethod
    def _tuple2(value, default=(0, 0)) -> tuple:
        value = value if isinstance(value, (list, tuple)) else default
        return tuple(value[:2]) if len(value) >= 2 else tuple(default)

    def _project_manifest(self) -> tuple[dict, dict[str, bytes]]:
        """Collect the complete in-memory document into manifest + PNG sections."""
        sections: dict[str, bytes] = {}
        viewer = self.image_viewer

        def add_pixmap(name: str, pm: QPixmap | None) -> str | None:
            payload = _pixmap_bytes(pm)
            if not payload:
                return None
            sections[name] = payload
            return name

        combined_id = add_pixmap("images/combined.png", viewer._pixmap)
        source_ids = []
        for index, pm in enumerate(viewer._source_images):
            name = add_pixmap(f"images/source_{index:04d}.png", pm)
            if name:
                source_ids.append(name)

        actions = []
        for ai, action in enumerate(self.actions):
            frame_ids = []
            for fi, frame in enumerate(action.frames):
                name = add_pixmap(f"actions/{ai:04d}/frame_{fi:04d}.png", frame)
                frame_ids.append(name)
            parts = []
            for frame_parts in action.frame_parts:
                parts.append([
                    [list(rect), int(rx), int(ry), int(ox), int(oy)]
                    for rect, rx, ry, ox, oy in frame_parts
                ])
            actions.append({
                "name": action.name, "color": action.color,
                "selected_indices": list(action.selected_indices),
                "composite_groups": [list(group) for group in action.composite_groups],
                "frame_layouts": None if action.frame_layouts is None else [
                    [[int(i), int(x), int(y)] for i, x, y in layout]
                    for layout in action.frame_layouts
                ],
                "frame_layouts_base": action.frame_layouts_base,
                "frame_rects": [list(rect) for rect in action.frame_rects],
                "frame_parts": parts, "frame_sections": frame_ids,
                "fps": action.fps, "loop_mode": action.loop_mode,
                "padding": action.padding, "ground_enabled": action.ground_enabled,
                "ground_offset": action.ground_offset,
                "reuse_frame_settings": action.reuse_frame_settings,
                "show_anchor": action.show_anchor, "anchor": list(action.anchor),
                "frame_offsets": {str(k): list(v) for k, v in action.frame_offsets.items()},
                "frame_durations": {str(k): int(v) for k, v in action.frame_durations.items()},
                "frame_rotations": {str(k): v for k, v in action.frame_rotations.items()},
                "frame_mirror_h": sorted(action.frame_mirror_h),
                "frame_mirror_v": sorted(action.frame_mirror_v),
                "frame_rotation_pivots": {str(k): list(v) for k, v in action.frame_rotation_pivots.items()},
                "frame_quads": {str(k): list(v) for k, v in action.frame_quads.items()},
                "frame_transform_modes": {str(k): sorted(v) for k, v in action.frame_transform_modes.items()},
                "ref_frame": action.ref_frame, "ref_opacity": action.ref_opacity,
                "frame_opacity": action.frame_opacity, "bg_mode": action.bg_mode,
                "action_type": action.action_type, "move_direction": action.move_direction,
                "move_speed": action.move_speed,
            })

        thumb = None
        current_action_index = self.actions.index(viewer.current_action) if viewer.current_action in self.actions else -1
        # 缩略图受「软件设置 → 工程文件显示缩略图」控制：关掉时整个区段都不写，
        # 资源管理器就会回退到文件类型图标（SSTool.ico）。
        if show_project_thumbnail():
            if viewer.current_action and viewer.current_action.frames:
                index = min(self.preview._frame_index, len(viewer.current_action.frames) - 1)
                thumb = add_pixmap("thumbnail.png", action_frame_pixmap(viewer.current_action, index))
            if thumb is None:
                thumb = add_pixmap("thumbnail.png", viewer._pixmap)
        # 工程文件缩略图：正方形画布 + 灰色棋盘格铺底 + 内容居中 + 黑白粉描边 + 右下角标。
        # .sstool 以这张 PNG 开头，资源管理器（配好图片缩略图处理器后）直接显示它。
        if thumb and "thumbnail.png" in sections:
            preview_pm = _pixmap_from_bytes(sections["thumbnail.png"])
            badge = QPixmap(str(_APP_ROOT / "工程图标.png"))
            if not preview_pm.isNull():
                canvas = QPixmap(_THUMB_SIZE, _THUMB_SIZE)
                painter = QPainter(canvas)
                # ① 铺底：灰色棋盘格 —— 内容透明或装不满时由它兜底
                paint_checkerboard(painter, canvas.rect(), _THUMB_CHECKER_CELL)
                # ② 内容：等比装进「描边内缘再留白」的方框，居中
                stroke_total = sum(width for _c, width in _THUMB_STROKE)
                avail = max(1, _THUMB_SIZE - 2 * (stroke_total + _THUMB_PAD))
                scaled = preview_pm.scaled(
                    avail, avail,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    # 放大用最近邻（精灵图多为像素画，平滑会糊），缩小才用平滑
                    Qt.TransformationMode.FastTransformation
                    if max(preview_pm.width(), preview_pm.height()) < avail
                    else Qt.TransformationMode.SmoothTransformation,
                )
                painter.drawPixmap(
                    (_THUMB_SIZE - scaled.width()) // 2,
                    (_THUMB_SIZE - scaled.height()) // 2,
                    scaled,
                )
                # ③ 描边：黑 → 白 → 粉 三层同心，由外到内
                offset = 0.0
                painter.setBrush(Qt.BrushStyle.NoBrush)
                for color, width in _THUMB_STROKE:
                    pen = QPen(QColor(color))
                    pen.setWidthF(float(width))
                    painter.setPen(pen)
                    half = width / 2.0
                    side_f = _THUMB_SIZE - 2 * (offset + half)
                    painter.drawRect(QRectF(offset + half, offset + half, side_f, side_f))
                    offset += width
                # ④ 角标：右下角，压在描边内缘里
                if not badge.isNull():
                    side = max(16, _THUMB_SIZE // _THUMB_BADGE_DIV)
                    badge = badge.scaled(
                        side, side,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    margin = int(stroke_total) + max(2, side // 10)
                    painter.drawPixmap(
                        _THUMB_SIZE - badge.width() - margin,
                        _THUMB_SIZE - badge.height() - margin,
                        badge,
                    )
                painter.end()
                sections["thumbnail.png"] = _pixmap_bytes(canvas)
        manifest = {
            "format": "spritesheet-tool-project", "version": 1,
            "app_version": __version__, "combined_image": combined_id,
            "source_images": source_ids, "thumbnail": thumb,
            "viewer": {
                "frames": [list(x) for x in viewer._frames],
                "frame_logical": {str(k): list(v) for k, v in viewer._frame_logical.items()},
                "zoom": viewer._zoom, "offset": [viewer._offset.x(), viewer._offset.y()],
                "bg_mode": viewer._bg_mode, "compose_mode": viewer.compose_mode,
                "manual_compose": viewer._manual_compose,
            },
            "preview": {
                "action_index": current_action_index,
                "frame_index": self.preview._frame_index,
                "canvas_zoom": self.preview.canvas._zoom,
                "canvas_offset": [self.preview.canvas._offset.x(), self.preview.canvas._offset.y()],
                "fit": self.preview.canvas._fit,
            },
            "actions": actions,
        }
        return manifest, sections

    def _set_project_title(self) -> None:
        name = self._project_name or "未命名工程"
        marker = " *" if self._project_dirty else ""
        self.setWindowTitle(f"{name}{marker} - {__app_name__} v{__version__}")

    def _mark_project_dirty(self) -> None:
        if self._project_dirty:
            return
        self._project_dirty = True
        self._refresh_tab_label()
        self._set_project_title()

    # ---- 标签页：多工程 ----

    def _tab_name_for_path(self, path: Path | None) -> str:
        return Path(path).stem if path else "未命名工程"

    @staticmethod
    def _ui_interactive() -> bool:
        """当前是否在"有人看着"的界面里跑（offscreen = 回归脚本）。"""
        return os.environ.get("QT_QPA_PLATFORM", "").lower() != "offscreen"

    def _refresh_tab_label(self) -> None:
        """把当前文档的名字与脏标记同步到标签上。"""
        index = self.tabs.current_index()
        if index < 0:
            return
        self.tabs.set_name(index, self._project_name or "未命名工程")
        self.tabs.set_dirty(index, self._project_dirty)
        self.tabs.set_tooltip(index, str(self._project_path) if self._project_path else "尚未保存")

    def _capture_document(self) -> dict:
        """把当前文档状态**按引用**装进一个 dict（不做序列化、不复制像素）。

        为什么不用「切换标签时存一次工程文件」：实测 `_project_manifest()` 对一张
        3.8MB 素材要 **2.1 秒**（原图要重新编码成 PNG），切一次卡两秒没法用。
        文档本来就是内存里的对象，换标签只需要把引用换过去，再把几个控件刷新一遍。
        """
        v = self.image_viewer
        return {
            "actions": list(self.actions),
            "pixmap": v._pixmap,
            "source_images": list(v._source_images),
            "frames": list(v._frames),
            "frame_logical": dict(v._frame_logical),
            "zoom": v._zoom,
            "offset": QPointF(v._offset),
            "bg_mode": v._bg_mode,
            "compose_mode": v.compose_mode,
            "manual_compose": v._manual_compose,
            "current_action": v.current_action,
            "preview_action": self.preview._action,
            "preview_frame_index": self.preview._frame_index,
            "preview_zoom": self.preview.canvas._zoom,
            "preview_offset": QPointF(self.preview.canvas._offset),
            "preview_fit": self.preview.canvas._fit,
            "project_path": self._project_path,
            "project_json_path": self._project_json_path,
            "project_dirty": self._project_dirty,
            "project_name": self._project_name,
            "name_custom": self._name_custom,
            "undo_stack": self._undo_stack,
            "transform_state": self._transform_history_state,
        }

    @staticmethod
    def _blank_document() -> dict:
        """空白文档状态（新建标签页用），字段与 _capture_document 一致。"""
        return {
            "actions": [],
            "pixmap": None,
            "source_images": [],
            "frames": [],
            "frame_logical": {},
            "zoom": 1.0,
            "offset": QPointF(0, 0),
            "bg_mode": "checker",
            "compose_mode": False,
            "manual_compose": False,
            "current_action": None,
            "preview_action": None,
            "preview_frame_index": 0,
            "preview_zoom": 1.0,
            "preview_offset": QPointF(0, 0),
            "preview_fit": True,
            "project_path": None,
            "project_json_path": None,
            "project_dirty": False,
            "project_name": "未命名工程",
            "name_custom": False,
            "undo_stack": None,
            "transform_state": None,
        }

    def _restore_document(self, doc: dict) -> None:
        """把文档状态装回控件（与 _capture_document 对称）。

        刷新顺序照着 `_apply_sstool_project` 来：先原图区数据，再动作列表，
        最后预览区；漏掉任何一处都会出现「切回来画面还是上一个工程」。
        """
        self._end_all_renames()
        viewer = self.image_viewer
        # 切文档时先掐掉上一份文档还在跑的元素检测，否则结果会落到新文档上
        viewer._detect_generation += 1

        self.actions = list(doc["actions"])
        viewer._pixmap = doc["pixmap"]
        viewer._source_images = list(doc["source_images"])
        viewer._frames = list(doc["frames"])
        viewer._frame_logical = dict(doc["frame_logical"])
        viewer._zoom = float(doc["zoom"])
        viewer._offset = QPointF(doc["offset"])
        viewer._bg_mode = doc["bg_mode"]
        viewer.compose_mode = bool(doc["compose_mode"])
        viewer._manual_compose = bool(doc["manual_compose"])

        self.action_list.clear()
        for action in self.actions:
            self._append_action_card(action)
        viewer.set_actions(self.actions)

        current = doc["current_action"]
        viewer.set_current_action(current)
        if current in self.actions:
            row = self.actions.index(current)
            self.action_list.blockSignals(True)
            self.action_list.setCurrentRow(row)
            self.action_list.blockSignals(False)

        self.preview.set_actions(self.actions)
        self.preview.set_source_pixmap(viewer._pixmap)
        self.preview.set_action(doc["preview_action"])
        self.preview._frame_index = int(doc["preview_frame_index"])
        if doc["preview_action"] is not None:
            self.preview._sync_frame()
        self.preview.canvas._zoom = float(doc["preview_zoom"])
        self.preview.canvas._offset = QPointF(doc["preview_offset"])
        self.preview.canvas._fit = bool(doc["preview_fit"])

        self._project_path = doc["project_path"]
        self._project_json_path = doc.get("project_json_path")
        self._project_dirty = bool(doc["project_dirty"])
        self._project_name = doc["project_name"]
        self._name_custom = bool(doc["name_custom"])
        # 左上角 JSON 徽标跟着工程走
        self.image_viewer.set_json_badge(bool(self._project_json_path), self._project_json_path or "")

        # 撤销栈每个文档一份；菜单里的「撤销」要跟着换到新的那份上
        stack = doc["undo_stack"]
        self._undo_stack = stack if stack is not None else QUndoStack(self)
        self._transform_history_state = doc["transform_state"]
        self._transform_history_pending = None
        if self._act_undo is not None:
            try:
                self._act_undo.triggered.disconnect()
            except TypeError:
                pass
            self._act_undo.triggered.connect(self._undo_stack.undo)
        if getattr(self, "_act_redo", None) is not None:
            try:
                self._act_redo.triggered.disconnect()
            except TypeError:
                pass
            self._act_redo.triggered.connect(self._undo_stack.redo)
        if self._transform_history_state is None:
            self._transform_history_state = self._capture_transform_state()

        viewer.canvas.update()
        viewer._update_rulers()
        self._refresh_tab_label()
        self._set_project_title()

    def _switch_tab(self, index: int) -> None:
        if not (0 <= index < self.tabs.count()):
            return
        current = self.tabs.current_index()
        if index == current:
            return
        if 0 <= current < len(self._documents):
            slot = self._documents[current]
            slot["state"] = self._capture_document()
            slot["name"] = self._project_name
            slot["dirty"] = self._project_dirty
        self.tabs.set_current(index)
        doc = self._documents[index]
        self._restore_document(doc["state"])
        doc["state"] = None            # 现在它是"活着"的那份
        self.lbl_status.setText(f"已切换到工程：{self._project_name}")

    def _new_tab(self, name: str | None = None, switch: bool = True) -> int:
        """新建一个空白工程标签页。"""
        current = self.tabs.current_index()
        if 0 <= current < len(self._documents):
            slot = self._documents[current]
            slot["state"] = self._capture_document()
            slot["name"] = self._project_name
            slot["dirty"] = self._project_dirty
        doc = self._blank_document()
        doc["project_name"] = name or self._next_untitled_name()
        self._documents.append({"name": doc["project_name"], "dirty": False, "state": doc})
        index = self.tabs.add_tab(doc["project_name"], select=switch)
        if switch:
            self._restore_document(doc)
            self._documents[index]["state"] = None
            self.lbl_status.setText("已新建空白工程")
        self._refresh_tab_label()
        return index

    def _next_untitled_name(self) -> str:
        used = {slot["name"] for slot in self._documents}
        index = self.tabs.count() + 1
        while f"未命名工程{index}" in used:
            index += 1
        return f"未命名工程{index}"

    def _rename_tab(self, index: int, name: str) -> None:
        """双击标签重命名：只改工程在软件里的显示名，**不动磁盘上的文件名**。"""
        if not (0 <= index < len(self._documents)):
            return
        self._documents[index]["name"] = name
        if index == self.tabs.current_index():
            self._project_name = name
            self._name_custom = True
            self._refresh_tab_label()
            self._set_project_title()
        self.lbl_status.setText(f"工程已重命名为「{name}」")

    def _ask_close_tab(self, name: str) -> str:
        """关闭有改动的标签时询问。返回 "save" / "discard" / "cancel"。

        单独抽出来是为了可测：回归里替换掉它就不必去 mock 模态对话框。
        offscreen 下不弹窗（理由见 ``_ask_quit_dirty``），按"不保存"处理。
        """
        if not self._ui_interactive():
            return "discard"
        box = QMessageBox(self)
        box.setWindowTitle("关闭工程")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(f"「{name}」有未保存的改动。")
        box.setInformativeText("关闭前要保存吗？")
        btn_save = box.addButton("保存", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("不保存", QMessageBox.ButtonRole.DestructiveRole)
        btn_cancel = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(btn_save)
        box.exec()
        clicked = box.clickedButton()
        if clicked is btn_cancel or clicked is None:
            return "cancel"
        return "save" if clicked is btn_save else "discard"

    def _close_tab(self, index: int) -> None:
        if not (0 <= index < self.tabs.count()):
            return
        is_current = index == self.tabs.current_index()
        if is_current:
            name = self._project_name
            dirty = self._project_dirty
        else:
            name = self._documents[index]["name"]
            dirty = bool(self._documents[index]["state"]["project_dirty"]) if self._documents[index]["state"] else False
        if dirty:
            choice = self._ask_close_tab(name)
            if choice == "cancel":
                return
            if choice == "save":
                if not is_current:
                    self._switch_tab(index)
                    is_current = True
                if not self._save_project():
                    return
        self._documents.pop(index)
        self.tabs.remove_tab(index)
        if self.tabs.count() == 0:
            self._new_tab(switch=True)
            return
        if is_current:
            new_index = min(index, self.tabs.count() - 1)
            self.tabs.set_current(new_index)
            doc = self._documents[new_index]
            self._restore_document(doc["state"])
            doc["state"] = None
        else:
            self._refresh_tab_label()

    def _tab_open_target(self) -> int:
        """打开工程该落到哪个标签：当前标签是"没动过的空白页"就复用它（PS 的做法），
        否则新开一个标签，避免把正在编辑的工程顶掉。"""
        current = self.tabs.current_index()
        if current < 0:
            return self._new_tab(switch=False)
        if (not self.actions and self.image_viewer._pixmap is None
                and not self._project_dirty and self._project_path is None):
            return current
        return self._new_tab(name="新工程", switch=False)

    def _save_project(self, path: str | Path | None = None) -> bool:
        target = Path(path) if path else self._project_path
        if target is None:
            return self._save_project_as()
        try:
            manifest, sections = self._project_manifest()
            write_project(target, manifest, sections)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "保存工程失败", str(exc))
            return False
        self._project_path = target
        self._project_dirty = False
        # 文件名跟着工程走（除非用户双击标签改过名 —— 那种情况保留用户起的名字）
        if not self._name_custom:
            self._project_name = self._tab_name_for_path(target)
        self._refresh_tab_label()
        self._set_project_title()
        self.lbl_status.setText(f"工程已保存：{target.name}")
        return True

    def _dialog_start(self, default_name: str = "") -> str:
        """文件对话框的默认位置：优先当前工程所在目录，其次上次用过的目录（没记录过
        或目录已不存在就回退到桌面），最后拼上默认文件名。"""
        if self._project_path is not None:
            folder = str(self._project_path.parent)
        else:
            folder = last_directory()
        return str(Path(folder) / default_name) if default_name else folder

    def _remember_dialog_path(self, path: str | Path | None) -> None:
        set_last_directory(path)

    def _save_project_as(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(
            self, "保存 SSTOOL 工程",
            self._dialog_start(f"{self._project_name or '工程'}.sstool"),
            "SSTOOL 工程 (*.sstool)"
        )
        if not path:
            return False
        if not path.lower().endswith(".sstool"):
            path += ".sstool"
        self._remember_dialog_path(path)
        return self._save_project(path)

    def _new_project(self) -> None:
        """新建工程 —— 现在会**新开一个标签页**（原来是把当前工程清空）。

        入口在 文件 → 新建工程（Ctrl+N），也可以双击标签栏空白处。
        """
        self._new_tab()

    def _open_project_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "打开 SSTOOL 工程", self._dialog_start(), "SSTOOL 工程 (*.sstool)")
        if path:
            self._remember_dialog_path(path)
            self._open_project(path)

    def _open_project(self, path: str) -> bool:
        """打开工程。落在哪个标签由 ``_tab_open_target`` 决定：

        当前标签是"没动过的空白页"就直接用它，否则新开一个标签 —— 免得把正在编辑的
        工程顶掉（PS 也是这个行为）。
        """
        index = self._tab_open_target()
        was_current = index == self.tabs.current_index()
        try:
            manifest, sections = read_project(path)
            self._apply_sstool_project(manifest, sections)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "打开工程失败", str(exc))
            return False
        self.tabs.set_current(index)
        self._project_path = Path(path)
        self._project_json_path = None
        self.image_viewer.set_json_badge(False)
        self._project_dirty = False
        self._project_name = self._tab_name_for_path(self._project_path)
        self._name_custom = False
        self._documents[index]["state"] = None
        self._documents[index]["name"] = self._project_name
        self._documents[index]["dirty"] = False
        self._refresh_tab_label()
        self._set_project_title()
        self.lbl_status.setText(f"工程已打开：{self._project_path.name}"
                               + ("" if was_current else "（新标签页）"))
        return True

    def _safe_open_project(self, path: str) -> None:
        """Safe slot for delayed drag/drop project loading."""
        try:
            self._open_project(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "打开工程失败", str(exc))

    def _apply_sstool_project(self, manifest: dict, sections: dict[str, bytes]) -> None:
        if manifest.get("format") != "spritesheet-tool-project":
            raise ValueError("不是有效的 SSTOOL 工程清单")
        viewer_state = manifest.get("viewer") or {}
        viewer = self.image_viewer
        viewer._detect_generation += 1
        combined = _pixmap_from_bytes(sections.get(manifest.get("combined_image", ""), b""))
        if combined.isNull():
            raise ValueError("工程缺少有效的原图数据")
        viewer._pixmap = combined
        viewer._source_images = [
            _pixmap_from_bytes(sections[name]) for name in manifest.get("source_images", []) if name in sections
        ]
        # Older projects may only contain the merged image.  Keep cropping
        # and re-export functional by treating that image as the sole source.
        if not viewer._source_images:
            viewer._source_images = [combined]
        viewer._frames = [tuple(map(int, x)) for x in viewer_state.get("frames", [])]
        viewer._frame_logical = {int(k): tuple(map(int, v)) for k, v in (viewer_state.get("frame_logical") or {}).items()}
        viewer._zoom = float(viewer_state.get("zoom", 1.0))
        viewer._offset = QPointF(*self._tuple2(viewer_state.get("offset"), (0, 0)))
        viewer._bg_mode = str(viewer_state.get("bg_mode", "checker"))
        viewer.compose_mode = bool(viewer_state.get("compose_mode", False))
        viewer._manual_compose = bool(viewer_state.get("manual_compose", False))

        self._end_all_renames()
        self.actions.clear()
        self.action_list.clear()
        for raw in manifest.get("actions", []):
            action = Action(str(raw.get("name", "未命名动作")), str(raw.get("color", "#E91E63")))
            for attr in ("fps", "loop_mode", "padding", "ground_enabled", "ground_offset",
                         "reuse_frame_settings", "show_anchor", "ref_frame", "ref_opacity",
                         "frame_opacity", "bg_mode", "action_type", "move_direction", "move_speed"):
                if attr in raw:
                    setattr(action, attr, raw[attr])
            action.selected_indices = [int(x) for x in raw.get("selected_indices", [])]
            action.composite_groups = [[int(x) for x in g] for g in raw.get("composite_groups", [])]
            layouts = raw.get("frame_layouts")
            action.frame_layouts = None if layouts is None else [
                [(int(i), int(x), int(y)) for i, x, y in layout] for layout in layouts
            ]
            action.frame_layouts_base = int(raw.get("frame_layouts_base", 0))
            action.frame_rects = [tuple(map(int, x)) for x in raw.get("frame_rects", [])]
            action.frame_parts = []
            for frame_parts in raw.get("frame_parts", []):
                parsed_parts = []
                for part in frame_parts:
                    if len(part) >= 5:
                        parsed_parts.append(
                            (tuple(map(int, part[0])), int(part[1]), int(part[2]), int(part[3]), int(part[4]))
                        )
                    elif len(part) >= 3:
                        # Projects written before logical origins were added.
                        rect = tuple(map(int, part[0]))
                        parsed_parts.append((rect, int(part[1]), int(part[2]), rect[0], rect[1]))
                action.frame_parts.append(parsed_parts)
            for fi, name in enumerate(raw.get("frame_sections", [])):
                frame = _pixmap_from_bytes(sections.get(name, b""))
                if frame.isNull():
                    raise ValueError(f"动作 {action.name} 缺少帧图像")
                action.frames.append(frame)
            action.anchor = tuple(float(x) for x in self._tuple2(raw.get("anchor"), (0.5, 0.5)))
            action.frame_offsets = {int(k): tuple(map(int, v)) for k, v in (raw.get("frame_offsets") or {}).items()}
            action.frame_durations = {int(k): max(1, int(v)) for k, v in (raw.get("frame_durations") or {}).items()}
            action.frame_rotations = {int(k): int(v) for k, v in (raw.get("frame_rotations") or {}).items()}
            action.frame_mirror_h = set(map(int, raw.get("frame_mirror_h", [])))
            action.frame_mirror_v = set(map(int, raw.get("frame_mirror_v", [])))
            action.frame_rotation_pivots = {int(k): tuple(map(float, v)) for k, v in (raw.get("frame_rotation_pivots") or {}).items()}
            action.frame_quads = {int(k): tuple(map(float, v)) for k, v in (raw.get("frame_quads") or {}).items()}
            action.frame_transform_modes = {int(k): set(v) for k, v in (raw.get("frame_transform_modes") or {}).items()}
            action.invalidate_transforms()
            self.actions.append(action)
            self._append_action_card(action)

        viewer.set_actions(self.actions)
        viewer.set_current_action(None)
        preview_state = manifest.get("preview") or {}
        action_index = int(preview_state.get("action_index", -1))
        action = self.actions[action_index] if 0 <= action_index < len(self.actions) else None
        if action is not None:
            viewer.set_current_action(action)
            if 0 <= action_index < self.action_list.count():
                self.action_list.blockSignals(True)
                self.action_list.setCurrentRow(action_index)
                self.action_list.blockSignals(False)
        self.preview.set_actions(self.actions)
        self.preview.set_source_pixmap(viewer._pixmap)
        self.preview.set_action(action)
        frame_index = int(preview_state.get("frame_index", 0))
        if action and action.frames:
            self.preview._frame_index = max(0, min(frame_index, len(action.frames) - 1))
            self.preview._sync_frame()
        self.preview.canvas._zoom = float(preview_state.get("canvas_zoom", self.preview.canvas._zoom))
        self.preview.canvas._offset = QPointF(*self._tuple2(preview_state.get("canvas_offset"), (0, 0)))
        self.preview.canvas._fit = bool(preview_state.get("fit", True))
        viewer.canvas.update()
        viewer._update_rulers()
        self._reset_transform_history()
        self._project_path = None
        self._project_dirty = False

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 名称栏（已去掉左侧软件图标，只留软件名；版本号与名字底对齐，画在同一个控件里）
        logo_bar = QWidget()
        logo_bar.setFixedHeight(64)
        logo_bar.setStyleSheet("background-color: #1a1a1a; border-bottom: 1px solid #333;")
        logo_layout = QHBoxLayout(logo_bar)
        logo_layout.setContentsMargins(12, 5, 8, 5)
        logo_layout.setSpacing(8)
        name_label = AppNameLabel(version=f"v{__version__}")
        logo_layout.addWidget(name_label, 0, Qt.AlignmentFlag.AlignVCenter)
        logo_layout.addStretch()
        main_layout.addWidget(logo_bar)

        # 工程标签栏紧贴在名称栏下面（菜单栏 → 软件名 → 标签 → 内容 → 状态栏）。
        # 交互：单击切换 / 双击重命名 / 双击空白处新建 / × 关闭 / + 新建
        self.tabs = ProjectTabBar()
        self.tabs.tab_selected.connect(self._switch_tab)
        self.tabs.tab_close_requested.connect(self._close_tab)
        self.tabs.tab_renamed.connect(self._rename_tab)
        self.tabs.new_tab_requested.connect(lambda: self._new_tab())
        self.tabs.tab_drop_requested.connect(self._on_tab_drop)
        main_layout.addWidget(self.tabs)

        # 内容区：侧边栏 | QSplitter(原图区 | 右侧预览)
        content = QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)

        # 左侧边栏
        sidebar = QWidget()
        sidebar.setFixedWidth(200)
        sidebar.setStyleSheet("background-color: #1a1a1a; border-right: 1px solid #333;")
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(4, 4, 4, 4)
        sidebar_layout.setSpacing(4)

        self.btn_add_action = QPushButton("+ 添加图片")
        self.btn_add_action.setFixedHeight(32)
        self.btn_add_action.setStyleSheet(
            "QPushButton { background: #E91E63; color: white; border: none; border-radius: 4px; font-size: 12px; }"
            "QPushButton:hover { background: #F06292; }"
        )
        self.btn_add_action.clicked.connect(self._on_add_action_clicked)
        sidebar_layout.addWidget(self.btn_add_action)

        self.action_list = QListWidget()
        self.action_list.setItemDelegate(ActionItemDelegate(self.action_list))
        self.action_list.setStyleSheet(
            "QListWidget { background: #141414; border: none; border-radius: 4px; }"
            "QListWidget::item { height: 34px; padding: 0 4px; }"
        )
        self.action_list.currentRowChanged.connect(self._on_action_selected)
        sidebar_layout.addWidget(self.action_list, 1)

        content.addWidget(sidebar)

        # 右侧：QSplitter(原图区 | 预览区)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(4)
        self.splitter.setStyleSheet("QSplitter::handle { background: #333; } QSplitter::handle:hover { background: #E91E63; }")

        self.image_viewer = ImageViewer()
        self.image_viewer.image_loaded.connect(self._on_image_loaded)
        self.image_viewer.json_dropped.connect(self._import_project)
        self.image_viewer.project_dropped.connect(self._safe_open_project)
        self.image_viewer.json_dropped_current.connect(
            lambda p: self._import_project(p, target="current"))
        self.image_viewer.image_path_dropped.connect(
            lambda p: self._open_path_smart(p, target="current"))
        self.image_viewer.selection_changed.connect(self._on_selection_changed)
        self.image_viewer.selection_changed.connect(self._mark_project_dirty)
        self.image_viewer.frames_updated.connect(self._on_frames_updated)

        # 左列：原图区 + 下方「动画设置」横条（整段动画参数）
        left_column = QWidget()
        left_column.setObjectName("LeftColumn")
        # ID 选择器只涂容器自身，避免级联污染 image_viewer 内部子控件
        left_column.setStyleSheet("#LeftColumn { background-color: #1f1f1f; }")
        left_layout = QVBoxLayout(left_column)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        left_layout.addWidget(self.image_viewer, 1)
        self.anim_bar = AnimSettingsBar()
        self.anim_bar.set_actions(self.actions)
        left_layout.addWidget(self.anim_bar)
        self.splitter.addWidget(left_column)

        self.preview = ActionPreviewWidget()
        self.preview.set_actions(self.actions)
        # 导出工程（.sstool）需要完整文档数据；provider 由主窗口注入
        self.preview._project_manifest = self._project_manifest
        # 导出产物以当前标签页的工程名开头
        self.preview._project_name_provider = lambda: self._project_name
        self.preview.adjust_panel.changed.connect(self._on_transform_history_changed)
        self.preview.adjust_panel.changed.connect(self._mark_project_dirty)
        self.preview.canvas.transform_edit_started.connect(self._begin_transform_history)
        self.preview.canvas.transform_edit_finished.connect(self._finish_transform_history)
        self.preview.toolbar.bg_changed.connect(lambda _mode: self._mark_project_dirty())
        # 「动画设置」横条：当前动作切换时同步；改动后刷新预览并标脏
        self.preview.action_changed.connect(self.anim_bar.set_action)
        self.anim_bar.changed.connect(self.preview._on_adjust_changed)
        self.anim_bar.changed.connect(self._mark_project_dirty)
        self.anim_bar.changed.connect(self._on_anim_bar_changed)
        # 底部横条「变换（全部帧）」模式 → 预览画布（作用于全部帧）
        self.anim_bar.mode_requested.connect(self.preview.adjust_panel.enter_transform_mode_all)
        # 画布变换模式变化（进入/退出/点空白）→ 同步底部横条按钮
        self.preview.canvas.transform_mode_changed.connect(self.anim_bar.set_mode)
        # 当前帧切换 → 底部横条「微调（全部帧）」以当前帧偏移为起点
        self.preview.frame_changed.connect(self.anim_bar.set_current_frame)
        self.preview.frame_changed.connect(self._on_frame_changed)
        self.preview.canvas.frame_offset_dragged.connect(self._on_frame_offset_dragged)
        self.image_viewer._toolbar.bg_changed.connect(lambda _mode: self._mark_project_dirty())
        self.image_viewer._toolbar.compose_toggled.connect(lambda _checked: self._mark_project_dirty())
        # 程序退出（含未走 close 的路径）也要收尾原图区与更新检查线程，
        # 否则线程/过滤器会悬空
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._stop_update_thread)
            app.aboutToQuit.connect(self.image_viewer.shutdown)
        self.splitter.addWidget(self.preview)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([600, 320])

        content.addWidget(self.splitter, 1)
        main_layout.addLayout(content, 1)

        # 状态栏
        self.lbl_status = QLabel(f"{__app_name__} v{__version__}  |  重构版")
        self.lbl_status.setStyleSheet("color: #888; font-size: 11px; padding: 2px 8px; background: #1a1a1a; border-top: 1px solid #333;")
        main_layout.addWidget(self.lbl_status)

    def _build_menu(self) -> None:
        menubar = self.menuBar()
        menubar.setStyleSheet("QMenuBar { background: #1a1a1a; color: #ddd; font-size: 12px; } QMenuBar::item:selected { background: #E91E63; }")

        # 布局：文件在左、编辑在右。
        # 文件菜单分组：① 素材（添加图片 / 导入 JSON 工程）—— 按之前的要求放在最上面
        #              ② 工程（新建 / 打开 / 保存 / 另存为）
        #              ③ 退出
        # 「检查更新」已移进 编辑 → 软件设置…（软件级开关和更新放一起）。
        file_menu = menubar.addMenu("文件")
        act_open = QAction("添加图片", self)
        act_open.triggered.connect(self._open_image)
        file_menu.addAction(act_open)
        act_import = QAction("导入 JSON 工程…", self)
        act_import.setShortcut("Ctrl+I")
        act_import.triggered.connect(self._import_json_dialog)
        file_menu.addAction(act_import)
        file_menu.addSeparator()
        act_new = QAction("新建工程", self)
        act_new.setShortcut(QKeySequence.StandardKey.New)
        act_new.triggered.connect(self._new_project)
        file_menu.addAction(act_new)
        act_open_project = QAction("打开工程…", self)
        act_open_project.setShortcut(QKeySequence.StandardKey.Open)
        act_open_project.triggered.connect(self._open_project_dialog)
        file_menu.addAction(act_open_project)
        act_save = QAction("保存工程", self)
        act_save.setShortcut(QKeySequence.StandardKey.Save)
        act_save.triggered.connect(lambda: self._save_project())
        file_menu.addAction(act_save)
        act_save_as = QAction("工程另存为…", self)
        act_save_as.triggered.connect(self._save_project_as)
        file_menu.addAction(act_save_as)
        file_menu.addSeparator()
        act_quit = QAction("退出", self)
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

        edit_menu = menubar.addMenu("编辑")
        act_undo = QAction("撤销", self)
        act_undo.setShortcut(QKeySequence.StandardKey.Undo)
        act_undo.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        act_undo.triggered.connect(self._undo_stack.undo)
        edit_menu.addAction(act_undo)
        # 撤销栈是「每个文档一份」，切标签时要把它接到当前文档的那份上
        self._act_undo = act_undo

        act_redo = QAction("重做", self)
        act_redo.setShortcut(QKeySequence.StandardKey.Redo)
        act_redo.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        act_redo.triggered.connect(self._undo_stack.redo)
        edit_menu.addAction(act_redo)
        self._act_redo = act_redo

        act_history = QAction("历史记录面板", self)
        act_history.setShortcut(QKeySequence("Ctrl+H"))
        act_history.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        act_history.setCheckable(True)
        act_history.setChecked(True)
        act_history.triggered.connect(self._open_history)
        edit_menu.addAction(act_history)

        edit_menu.addSeparator()
        act_settings = QAction("软件设置…", self)
        act_settings.triggered.connect(self._open_settings)
        edit_menu.addAction(act_settings)

        # 帮助菜单：关于（GPL 声明 + 第三方组件）
        help_menu = menubar.addMenu("帮助")
        act_about = QAction("关于 SpriteSheetTool", self)
        act_about.triggered.connect(self._open_about)
        help_menu.addAction(act_about)
        # 启动后延迟自动检查一次更新（不阻塞界面；失败时静默）
        self._update_thread: UpdateCheckThread | None = None
        self._update_checked = False
        QTimer.singleShot(2500, self._check_update_auto)

    def _check_update_auto(self):
        """启动自动检查：仅检查一次，无更新/失败都静默，不打扰用户。"""
        if self._update_checked:
            return
        self._start_update_check(notify_no_update=False)

    def _check_update_manual(self):
        """手动检查：始终弹窗反馈结果（入口在 编辑 → 软件设置…）。"""
        self._start_update_check(notify_no_update=True)

    def _open_settings(self) -> None:
        """编辑 → 软件设置…：工程缩略图开关 + 检查更新。"""
        dialog = SettingsDialog(self, check_update=self._check_update_manual)
        dialog.exec()

    def _open_history(self) -> None:
        """编辑 → 历史记录面板：显示/隐藏右侧浮动窗口。"""
        self._history_dock.setVisible(not self._history_dock.isVisible())
        if self._history_dock.isVisible():
            self._history_dock.raise_()

    def _open_about(self) -> None:
        """帮助 → 关于 SpriteSheetTool：GPL 声明 + 第三方组件清单。"""
        dialog = AboutDialog(self)
        dialog.exec()
    def _start_update_check(self, notify_no_update: bool):
        if getattr(self, "_update_thread", None) is not None and self._update_thread.isRunning():
            return  # 上一次检查还没结束
        self._update_checked = True
        self._notify_no_update = notify_no_update
        self._update_thread = UpdateCheckThread(__version__, UPDATE_URL, self)
        self._update_thread.result_ready.connect(self._on_update_result)
        self._update_thread.finished.connect(self._on_update_thread_finished)
        self._update_thread.start()

    def _on_update_thread_finished(self):
        self._update_thread = None

    def _on_update_result(self, info: dict | None):
        if not info:
            if self._notify_no_update:
                QMessageBox.information(self, "检查更新", f"当前已是最新版本 v{__version__}")
            return
        status = info.get("status", "")
        if status == "update":
            self._show_update_dialog(info)
        elif self._notify_no_update:
            if status == "latest":
                # 服务器版本与本地相同时不必赘述；只有服务器更旧（本地是自编译/未发布的
                # 版本）时才补一句，否则会出现「已是最新 v1.1.21（服务器最新 v1.1.18）」
                # 这种自相矛盾的提示。
                server = str(info.get("version", ""))
                detail = f"（服务器最高 v{server}）" if server and server != __version__ else ""
                QMessageBox.information(
                    self, "检查更新", f"当前已是最新版本 v{__version__}{detail}"
                )
            elif status == "no_installer":
                QMessageBox.warning(
                    self, "检查更新",
                    "服务器目录里没有找到安装包。\n\n"
                    "请先把 SpriteSheetTool_v.X.Y.Z.exe 上传到：\n"
                    f"{info.get('url', '')}",
                )
            elif status == "error":
                QMessageBox.warning(
                    self, "检查更新",
                    f"检查更新失败：\n{info.get('reason', '网络异常')}",
                )

    def _show_update_dialog(self, info: dict):
        version = info.get("version", "")
        url = info.get("url", "")
        notes = info.get("notes", "")
        msg = f"发现新版本 <b>v{version}</b>（当前 v{__version__}）"
        if notes:
            msg += f"<br><br>更新说明：<br>{notes}"
        box = QMessageBox(self)
        box.setWindowTitle("发现新版本")
        box.setIcon(QMessageBox.Icon.Information)
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(msg)
        if url:
            download_btn = box.addButton("立即更新", QMessageBox.ButtonRole.AcceptRole)
        else:
            download_btn = None
        box.addButton("稍后再说", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if download_btn is not None and box.clickedButton() is download_btn and url:
            self._download_and_install_update(version, url)

    def _download_and_install_update(self, version: str, url: str):
        """弹深色下载窗口，下载完成后运行安装包并退出本程序。"""
        dialog = UpdateDownloadDialog(version, url, self)
        dialog.download_ok.connect(self._launch_installer)
        dialog.exec()

    def _launch_installer(self, installer_path: str):
        """静默安装更新，然后关闭本程序；安装器装完会自动拉起新版本。"""
        import subprocess
        try:
            # /SILENT：显示进度条但不询问；/NORESTART 由 [Run] 段负责启动新版本
            subprocess.Popen(
                [installer_path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"],
                shell=False,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
        except OSError as exc:
            QMessageBox.warning(self, "启动安装程序失败", f"无法启动安装包：\n{exc}")
            return
        # 稍等片刻让安装器进程真正起来（它会自动关闭并重启本程序）
        QTimer.singleShot(800, self.close)

    def _stop_update_thread(self) -> None:
        """关闭时收尾「检查更新」线程。

        它与元素检测线程是同一类隐患：线程还在跑而宿主先被析构，退出时会挂住或崩溃。
        更新检查是网络请求、可能阻塞到超时，所以这里先断开信号，等一小会儿，
        仍未结束就强制终止（此时它只等在网络上，没有需要保护的共享状态）。
        """
        thread = getattr(self, "_update_thread", None)
        if thread is None:
            return
        self._update_thread = None
        try:
            if thread.isRunning():
                thread.result_ready.disconnect()
        except (RuntimeError, TypeError):
            pass
        try:
            if thread.isRunning() and not thread.wait(1500):
                thread.terminate()
                thread.wait(500)
        except RuntimeError:
            pass

    def _dirty_tab_names(self) -> list[tuple[int, str]]:
        """列出所有有未保存改动的标签：(索引, 名字)。"""
        out = []
        current = self.tabs.current_index()
        for index in range(self.tabs.count()):
            if index == current:
                dirty = self._project_dirty
                name = self._project_name
            else:
                state = self._documents[index]["state"] if index < len(self._documents) else None
                dirty = bool(state and state["project_dirty"])
                name = self._documents[index]["name"] if index < len(self._documents) else ""
            if dirty:
                out.append((index, name))
        return out

    def _ask_quit_dirty(self, names: list[str]) -> str:
        """退出前询问多个未保存的工程。返回 "save" / "discard" / "cancel"。

        offscreen（回归脚本）下**不弹窗**，直接按"不保存"处理 —— 否则任何
        "改点东西再 win.close()" 的脚本都会卡在模态框上，跑批时表现为假死。
        真实运行时 QT_QPA_PLATFORM 不是 offscreen，照常弹窗。
        """
        if not self._ui_interactive():
            return "discard"
        shown = "\n".join(f"· {name}" for name in names[:8])
        more = f"\n… 以及另外 {len(names) - 8} 个" if len(names) > 8 else ""
        box = QMessageBox(self)
        box.setWindowTitle("退出前保存")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(f"有 {len(names)} 个工程还没保存：")
        box.setInformativeText(f"{shown}{more}\n\n退出前要保存它们吗？")
        btn_save = box.addButton("全部保存", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("不保存退出", QMessageBox.ButtonRole.DestructiveRole)
        btn_cancel = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(btn_save)
        box.exec()
        clicked = box.clickedButton()
        if clicked is btn_cancel or clicked is None:
            return "cancel"
        return "save" if clicked is btn_save else "discard"

    def _save_all_dirty_tabs(self) -> bool:
        """逐个保存有改动的标签；任何一个取消就整体放弃（返回 False）。"""
        for index, _name in self._dirty_tab_names():
            if index != self.tabs.current_index():
                self._switch_tab(index)
            if not self._save_project():
                return False
        return True

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # 浮动历史窗口跟随主窗口右侧
        if hasattr(self, "_history_dock") and self._history_dock.isFloating():
            self._history_dock.move(self.x() + self.width() + 10, self.y() + 80)

    def closeEvent(self, event) -> None:
        """关闭窗口前：先处理未保存的标签，再收尾原图区。

        收尾这两件事不做会导致随机闪退（原生 access violation）：检测线程在跑时宿主被
        析构、或 app 级事件过滤器指向已释放对象。参见 ``ImageViewer.shutdown``。
        """
        dirty = self._dirty_tab_names()
        if dirty:
            choice = self._ask_quit_dirty([name for _i, name in dirty])
            if choice == "cancel":
                event.ignore()
                return
            if choice == "save" and not self._save_all_dirty_tabs():
                event.ignore()
                return
        self._stop_update_thread()
        self.image_viewer.shutdown()
        super().closeEvent(event)

    def _open_image(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "添加图片", self._dialog_start(),
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.gif *.webp)"
        )
        for path in paths:
            self._remember_dialog_path(path)
            # 导入图片时自动检索同目录同名 JSON，有则优先导入工程（JSON 会连带加载配套图片）
            json_candidate = Path(path).with_suffix(".json")
            if json_candidate.is_file():
                try:
                    self._import_project(str(json_candidate))
                    continue
                except Exception:  # noqa: BLE001 - JSON 导入失败时回退为只导入图片
                    pass
            pm = QPixmap(path)
            if not pm.isNull():
                self._on_image_loaded(pm)

    # ---- 导入 JSON 工程 ----

    def _import_json_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入 JSON 工程", self._dialog_start(), "精灵图 JSON (*.json)"
        )
        if path:
            self._remember_dialog_path(path)
            self._import_project(path)

    def _ask_import_target(self, existing_actions: int, project) -> str:
        """询问导入目标：加入当前工程 / 新建工程 / 取消。"""
        box = QMessageBox(self)
        box.setWindowTitle("导入工程")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(f"当前工程有 {existing_actions} 个动作。")
        box.setInformativeText(
            f"识别格式：{project.format_label}\n"
            f"配套图片：{project.atlas_path.name}\n"
            f"动作数量：{len(project.actions)} 个\n\n"
            "要加入当前工程，还是新建一个标签页？"
        )
        btn_current = box.addButton("加入当前工程", QMessageBox.ButtonRole.AcceptRole)
        btn_new = box.addButton("新建工程", QMessageBox.ButtonRole.ActionRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(btn_current)
        box.exec()
        clicked = box.clickedButton()
        if clicked is btn_new:
            return "new"
        if clicked is btn_current:
            return "current"
        return "cancel"

    def _import_project(self, path_str: str, target: str = "ask") -> None:
        """导入 JSON（自动识别格式）。

        target:
          - "ask"：当前工程有内容时弹窗询问（加入当前 / 新建工程 / 取消）
          - "current"：直接加入当前工程（追加动作，不清空现有）
          - "new"：新建标签页后导入（清空式）
        """
        from spritesheet.importer import ImportError_, load_project

        try:
            project = load_project(path_str)
        except ImportError_ as exc:
            QMessageBox.warning(self, "导入失败", str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "导入失败", f"解析 {path_str} 时出错：{exc}")
            return

        pm = QPixmap(str(project.atlas_path))
        if pm.isNull():
            QMessageBox.warning(self, "导入失败", f"无法读取配套图片：\n{project.atlas_path}")
            return

        has_content = bool(self.actions) or self.image_viewer._pixmap is not None
        append = False
        if target == "ask" and has_content:
            choice = self._ask_import_target(len(self.actions), project)
            if choice == "cancel":
                return
            if choice == "new":
                self._new_tab(switch=True)
                append = False
            else:
                append = True
        elif target == "new":
            self._new_tab(switch=True)
            append = False
        else:  # current 或空工程
            append = bool(has_content)

        try:
            self._apply_imported_project(project, pm, append=append)
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            QMessageBox.warning(self, "导入失败", f"应用工程时出错：{exc}")
            return

        # 左上角 JSON 徽标
        self._project_json_path = str(path_str)
        self.image_viewer.set_json_badge(True, str(path_str))

    def _end_all_renames(self) -> None:
        """结束所有卡片的编辑状态与待执行的延迟回调（销毁卡片前必须先做）。"""
        for i in range(self.action_list.count()):
            item = self.action_list.item(i)
            card = self.action_list.itemWidget(item) if item is not None else None
            if isinstance(card, ActionCardWidget):
                try:
                    card._rename_timer.stop()   # 取消待触发的重命名收尾
                    card.end_rename()
                except (RuntimeError, AttributeError):
                    pass

    def _apply_imported_project(self, project, pm: QPixmap, append: bool = False) -> None:
        """把解析好的工程套用到界面：图集当原图、图块当元素、动作重建。

        若 JSON 带 elements（本工具格式），用其中的 origin 还原每个元素的**逻辑坐标**，
        于是合成帧的拼接间距与导出前完全一致 —— 图集块数、画面、合成组结构都不变。
        若缺少 elements（第三方格式），退化为把合成帧按 parts 拼成整块。
        """
        viewer = self.image_viewer
        atlas = pm

        # 3) 载入原图（append 模式：拼到当前原图下方，新 tiles 的 y 加偏移）
        if append and viewer._pixmap is not None and not viewer._pixmap.isNull():
            old_h = viewer._pixmap.height()
            old_frames = list(viewer._frames)         # 保留旧 tiles
            old_logical = dict(viewer._frame_logical)  # 保留旧 logical
            viewer.append_pixmap(atlas)
            y_off = old_h + 16
        else:
            old_frames = []
            old_logical = {}
            viewer.set_pixmap(atlas)
            y_off = 0

        # 收集图块：旧 tiles 保留在前，新 tiles 加 y_off 追加在后
        tiles: list[tuple[int, int, int, int]] = []
        tile_index: dict[tuple[int, int, int, int], int] = {}
        for r in old_frames:
            if r not in tile_index:
                tile_index[r] = len(tiles)
                tiles.append(r)
        for act in project.actions:
            for fr in act.frames:
                for pp in fr.parts:
                    r = pp.rect
                    rr = (r[0], r[1] + y_off, r[2], r[3]) if append else r
                    if rr not in tile_index:
                        tile_index[rr] = len(tiles)
                        tiles.append(rr)
        viewer._detect_generation += 1  # 作废自动侦测，用精确 tiles
        viewer._frames = tiles

        # 4) 还原元素逻辑坐标：旧 logical 保留，新 elements 的 rect 加 y_off
        logical = dict(old_logical)
        for el in project.elements:
            rect = _rect_from_dict(el.get("rect") or {})
            o = el.get("origin") or {}
            rr = (rect[0], rect[1] + y_off, rect[2], rect[3]) if append else rect
            if rr in tile_index:
                logical[tile_index[rr]] = (int(o.get("x", 0)), int(o.get("y", 0)))
        viewer._frame_logical = logical

        viewer.canvas.update()
        viewer._update_rulers()

        # 5) 重建动作
        # append 模式不清空现有动作，只追加；非 append 才清空重建
        if not append:
            self._end_all_renames()
            self.actions.clear()
            self.action_list.clear()
            viewer.set_current_action(None)
            self.preview.set_action(None)

        total_frames = 0
        for ai, imported in enumerate(project.actions):
            color = imported.color or MACARON_COLORS[ai % len(MACARON_COLORS)]
            action = Action(imported.name, color)
            action.fps = imported.fps
            action.loop_mode = imported.loop
            action.anchor = imported.anchor
            # 动作类型 / 移动参数 / 地面线（仅本工具格式提供；TP/Aseprite 不含）
            action.action_type = imported.action_type or "static"
            if imported.move:
                action.move_direction, action.move_speed = imported.move
            if imported.ground:
                action.ground_enabled, action.ground_offset = imported.ground
            layouts: list[list[tuple[int, int, int]]] = []
            kept_frames: list = []      # 与 layouts 一一对应（丢弃无有效部件的帧）
            for fr in imported.frames:
                layout = []
                for p in fr.parts:
                    r = p.rect
                    if append:
                        r = (r[0], r[1] + y_off, r[2], r[3])
                    if r in tile_index:
                        layout.append((tile_index[r], p.pos[0], p.pos[1]))
                if layout:
                    layouts.append(layout)
                    kept_frames.append(fr)
            if layouts:
                action.frame_layouts = layouts
                # 导入的帧全部视为基线（非用户复用），Alt 点击不会误取消
                action.frame_layouts_base = len(layouts)
                # selected_indices 覆盖布局中用到的全部元素（用于界面高亮，
                # 也让「布局是否仍适用」的判定成立）
                ids: list[int] = []
                for layout in layouts:
                    for tile_id, _rx, _ry in layout:
                        if tile_id not in ids:
                            ids.append(tile_id)
                action.selected_indices = ids
            viewer.crop_frames_for_action(action)
            # 逐帧时长：只记录与 fps 默认值不同的帧（kept_frames 与最终帧同序）
            default_ms = max(1, round(1000 / max(1, action.fps)))
            for _fi, _fr in enumerate(kept_frames):
                _ms = max(1, int(getattr(_fr, "duration_ms", 0) or default_ms))
                if _ms != default_ms:
                    action.frame_durations[_fi] = _ms
            self._restore_imported_canvas(action, imported, kept_frames)
            total_frames += len(action.frames)
            self.actions.append(action)
            self._append_action_card(action)

        viewer.set_actions(self.actions)
        self.preview.set_source_pixmap(viewer._pixmap)
        self.btn_add_action.setText("+ 添加动作")
        if self.actions:
            self.action_list.setCurrentRow(0)
        self._reset_transform_history()

        msg = (f"已导入 {project.format_label}：{len(self.actions)} 个动作、"
               f"{total_frames} 帧、{len(tiles)} 个图块")
        self.lbl_status.setText(msg)
        notices = list(project.warnings)
        if notices:
            QMessageBox.information(
                self, "导入完成",
                f"{msg}\n\n提示：\n" + "\n".join(f"· {n}" for n in notices),
            )

    @staticmethod
    def _restore_imported_canvas(action: Action, imported, kept_frames: list) -> None:
        """按导入记录还原画布几何（padding + 逐帧偏移）。

        导入后的帧是用图块重建的**紧贴小图**，若直接交给 ``compute_preview_layout``，
        它会按各帧自身尺寸重新居中 —— 帧尺寸不一致时帧间相对位置就被破坏（动画抖动）。
        这里用 ``padding``（四周留边）与 ``frame_offsets``（逐帧微调）把内容摆回原先
        在画布中的位置：

            内容落点 = padding + 居中补偿(max-尺寸)//2 + frame_offset

        令其等于导入记录的目标位置（本工具格式的 frames[].offset；TP/Aseprite 为
        帧原点 + 部件 pos），反解出 frame_offset 即可。画布留边优先用文件里写明的
        padding，其次由 sourceSize 与帧尺寸反推。
        """
        n = min(len(action.frames), len(kept_frames))
        if n <= 0:
            return
        sizes = [(action.frames[i].width(), action.frames[i].height()) for i in range(n)]
        max_w = max(s[0] for s in sizes)
        max_h = max(s[1] for s in sizes)

        pad = imported.padding
        if pad is None:
            targets = [f.canvas_size for f in kept_frames[:n] if f.canvas_size]
            if targets:
                target_w = max(t[0] for t in targets)
                target_h = max(t[1] for t in targets)
                pad = max(0, min((target_w - max_w) // 2, (target_h - max_h) // 2))
            else:
                pad = 0
        action.padding = int(pad)

        offsets: dict[int, tuple[int, int]] = {}
        for i in range(n):
            pos = kept_frames[i].canvas_pos
            if pos is None:
                continue
            dx = int(pos[0]) - action.padding - (max_w - sizes[i][0]) // 2
            dy = int(pos[1]) - action.padding - (max_h - sizes[i][1]) // 2
            if dx or dy:
                offsets[i] = (dx, dy)
        action.frame_offsets = offsets
        action.invalidate_transforms()

    # ---- 拖拽：图片 / JSON ----

    def dragEnterEvent(self, event):
        try:
            if event.mimeData().hasUrls():
                for url in event.mimeData().urls():
                    if Path(url.toLocalFile()).suffix.lower() in (SUPPORTED_IMAGE_EXT | {".json", ".sstool"}):
                        event.acceptProposedAction()
                        return
        except Exception:  # noqa: BLE001 - 拖放回调异常会让 PyQt abort（闪退）
            pass

    def dragMoveEvent(self, event):
        try:
            if event.mimeData().hasUrls():
                event.acceptProposedAction()
        except Exception:  # noqa: BLE001
            pass

    def dropEvent(self, event):
        try:
            for url in event.mimeData().urls():
                path = url.toLocalFile()
                # 主窗口空白处拖入：询问新建/加入当前
                QTimer.singleShot(0, lambda p=path: self._open_path_smart(p, target="ask"))
                event.acceptProposedAction()
                return
        except Exception:  # noqa: BLE001
            pass

    def _open_path_smart(self, path: str, target: str = "ask") -> None:
        """智能打开拖入/菜单选择的文件：图片自动找同名 JSON，JSON 走导入。

        target: "ask" = 询问新建/加入当前；"current" = 直接加入当前工程。
        """
        suffix = Path(path).suffix.lower()
        if suffix == ".sstool":
            self._safe_open_project(path)
            return
        if suffix == ".json":
            self._import_project(path, target=target)
            return
        if suffix in SUPPORTED_IMAGE_EXT:
            # 图片自动找同目录同名 JSON
            json_sidecar = Path(path).with_suffix(".json")
            if json_sidecar.is_file():
                self._import_project(str(json_sidecar), target=target)
                return
            pm = QPixmap(path)
            if not pm.isNull():
                self._on_image_loaded(pm)
            return
    def _on_tab_drop(self, path: str) -> None:
        """拖入标签栏：新建一个标签页并打开该文件（不顶掉当前工程）。"""
        suffix = Path(path).suffix.lower()
        if suffix == ".sstool":
            self._new_tab(switch=True)
            self._safe_open_project(path)
        elif suffix == ".json":
            self._import_project(path, target="new")
        else:
            self._new_tab(switch=True)
            self._open_path_smart(path, target="current")

    def _on_image_loaded(self, pm: QPixmap):
        """首次载入或追加原图；追加时保留已有动作和帧排序。"""
        has_image = self.image_viewer._pixmap is not None and not self.image_viewer._pixmap.isNull()
        if not has_image:
            self.image_viewer.set_pixmap(pm)
            self.actions.clear()
            self.action_list.clear()
            self.image_viewer.set_actions(self.actions)
            self.image_viewer.set_current_action(None)
            self.preview.set_action(None)
        else:
            if not self.image_viewer.append_pixmap(pm):
                return
            # 新图追加到帧列表末尾，原有 selected_indices 无需改动；
            # 重新裁剪所有动作以刷新当前源图的帧缓存。
            for action in self.actions:
                self.image_viewer.crop_frames_for_action(action)
            if self.image_viewer.current_action:
                self.preview.set_action(self.image_viewer.current_action)
        # 同步最新原图给预览组件，供导出弹窗的原图预览使用
        self.preview.set_source_pixmap(self.image_viewer._pixmap)
        self._reset_transform_history()
        self.btn_add_action.setText("+ 添加动作")
        source_count = len(self.image_viewer._source_images)
        verb = "已加载" if source_count == 1 else "已添加"
        self.lbl_status.setText(
            f"{verb}第 {source_count} 张原图：{pm.width()}×{pm.height()}，"
            f"当前共检测到 {len(self.image_viewer._frames)} 个元素"
        )
        self._mark_project_dirty()

    def _on_add_action_clicked(self):
        """根据是否已加载图片切换按钮文字。"""
        if self.image_viewer._pixmap is None or self.image_viewer._pixmap.isNull():
            self._open_image()
        else:
            self._create_action()

    def _append_action_card(self, action: Action) -> ActionCardWidget:
        """把动作加入列表并创建卡片（供新建动作与导入复用）。"""
        item = QListWidgetItem()
        self.action_list.addItem(item)
        card = ActionCardWidget(action.name, action.color)
        card.color_changed.connect(lambda c, a=action: self._on_action_color_changed(a, c))
        card.name_changed.connect(lambda n, a=action: self._on_action_name_changed(a, n))
        card.delete_requested.connect(lambda a=action: self._delete_action(a))
        card.clicked.connect(lambda a=action: self._select_action_by_obj(a))
        item.setSizeHint(QSize(0, 34))
        self.action_list.setItemWidget(item, card)
        item.setData(Qt.ItemDataRole.UserRole, action)
        return card

    def _create_action(self):
        """无图片时打开文件对话框；有图片时直接创建动作卡牌。"""
        # 选一个未被使用的颜色
        used_colors = {a.color for a in self.actions}
        color = "#E91E63"
        for c in MACARON_COLORS:
            if c not in used_colors:
                color = c
                break
        name = f"动作{len(self.actions) + 1}"
        action = Action(name, color)
        self._push_snapshot(f"添加动作「{name}」")
        self.actions.append(action)
        self._transform_history_state = self._capture_transform_state()
        self._mark_project_dirty()
        self.image_viewer.set_actions(self.actions)

        card = self._append_action_card(action)

        # 切换动作前结束所有卡牌的编辑状态
        for i in range(self.action_list.count()):
            w = self.action_list.itemWidget(self.action_list.item(i))
            if isinstance(w, ActionCardWidget):
                w.end_rename()

        self.action_list.setCurrentRow(self.action_list.count() - 1)
        # 新建动作直接进入重命名状态
        card.start_rename()
        self.lbl_status.setText(f"已添加动作「{name}」，共 {len(action.frames)} 帧")
        self._push_snapshot_done()

    def _on_action_color_changed(self, action: Action, color: str):
        self._push_snapshot(f"改动作颜色「{action.name}」→ {color}")
        action.color = color
        if self.image_viewer.current_action is action:
            self.image_viewer.action_color = color
        self.image_viewer.refresh()
        self._mark_project_dirty()
        self._push_snapshot_done()

    def _on_action_name_changed(self, action: Action, name: str):
        self._push_snapshot(f"改动作名「{action.name}」→「{name}」")
        action.name = name
        self._mark_project_dirty()
        self._push_snapshot_done()

    def _delete_action(self, action: Action):
        """删除动作卡片并将选择切换到相邻动作。"""
        if action not in self.actions:
            return
        self._push_snapshot(f"删除动作「{action.name}」")
        row = next((i for i in range(self.action_list.count())
                    if self.action_list.item(i).data(Qt.ItemDataRole.UserRole) is action), -1)
        if row < 0:
            return
        current_row = self.action_list.currentRow()
        was_current = self.image_viewer.current_action is action
        item = self.action_list.takeItem(row)
        card = self.action_list.itemWidget(item) if item else None
        if card is not None:
            card.deleteLater()
        del item
        self.actions.remove(action)
        self._transform_history_state = self._capture_transform_state()
        self._mark_project_dirty()
        self.image_viewer.set_actions(self.actions)

        if self.actions:
            if current_row > row:
                target_row = current_row - 1
            elif current_row == row:
                target_row = min(row, self.action_list.count() - 1)
            else:
                target_row = current_row
            if target_row >= 0:
                self.action_list.setCurrentRow(target_row)
        elif was_current:
            self.image_viewer.set_current_action(None)
            self.preview.set_action(None)
            self.lbl_status.setText("已删除动作，当前没有动作")
        self._push_snapshot_done()

    def _select_action_by_obj(self, action: Action):
        for i in range(self.action_list.count()):
            item = self.action_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) is action:
                self.action_list.setCurrentRow(i)
                break

    def _on_action_selected(self, row: int):
        if row < 0 or row >= len(self.actions):
            return
        action = self.actions[row]
        self.image_viewer.set_current_action(action)
        # 裁剪帧
        self.image_viewer.crop_frames_for_action(action)
        self.preview.set_action(action)
        self.lbl_status.setText(f"当前动作：{action.name}，{len(action.frames)} 帧")
        self._mark_project_dirty()

    def _on_selection_changed(self):
        """根据是否已加载图片切换按钮文字。"""
        if self.image_viewer.current_action:
            self.image_viewer.crop_frames_for_action(self.image_viewer.current_action)
            self.preview.set_action(self.image_viewer.current_action)
            self._transform_history_state = self._capture_transform_state()
            # 选择图块改变帧组成：debounce 后 push 快照记录
            self._schedule_selection_snapshot()

    def _schedule_selection_snapshot(self):
        """选择图块后延迟 push：before 用上次缓存的状态（因为信号在点击后才到）。"""
        if not hasattr(self, "_selection_snap_timer"):
            from PyQt6.QtCore import QTimer
            self._selection_snap_timer = QTimer(self)
            self._selection_snap_timer.setSingleShot(True)
            self._selection_snap_timer.timeout.connect(self._finish_selection_snapshot)
        self._selection_snap_timer.start(200)

    def _finish_selection_snapshot(self):
        """选择图块结束：push「上次状态 → 当前状态」。"""
        if getattr(self, "_suppress_selection_snapshot", False):
            return
        after = self._capture_snapshot()
        before = getattr(self, "_last_selection_state", None)
        cur = self.image_viewer.current_action
        name = f"「{cur.name}」" if cur else ""
        # 取当前动作的 selected_indices 数量
        ai = self.actions.index(cur) if cur in self.actions else -1
        n = len(after["actions"][ai].get("selected_indices", [])) if ai >= 0 else 0
        text = f"选择图块{name}({n}个)"
        if before is not None and before != after:
            self._undo_stack.push(_SnapshotCommand(self, before, after, text))
        self._last_selection_state = after

    def _on_anim_bar_changed(self):
        """动画参数(fps/时长/锚点等)改动后 debounce 记录。"""
        if getattr(self, "_suppress_selection_snapshot", False):
            return
        if not hasattr(self, "_anim_snap_timer"):
            from PyQt6.QtCore import QTimer
            self._anim_snap_timer = QTimer(self)
            self._anim_snap_timer.setSingleShot(True)
            self._anim_snap_timer.timeout.connect(self._push_snapshot_done)
        if not getattr(self, "_snapshot_before", None):
            cur = self.image_viewer.current_action
            name = f"「{cur.name}」" if cur else ""
            self._push_snapshot(f"修改动画参数{name}")
        self._anim_snap_timer.start(400)

    def _on_frame_changed(self, idx: int):
        """预览区切换帧序号：debounce 后记录。"""
        if getattr(self, "_suppress_selection_snapshot", False):
            return
        if not hasattr(self, "_frame_snap_timer"):
            from PyQt6.QtCore import QTimer
            self._frame_snap_timer = QTimer(self)
            self._frame_snap_timer.setSingleShot(True)
            self._frame_snap_timer.timeout.connect(self._push_snapshot_done)
        if not getattr(self, "_snapshot_before", None):
            cur = self.image_viewer.current_action
            name = f"「{cur.name}」" if cur else ""
            self._push_snapshot(f"切换帧{name} → 帧{idx+1}")
        self._frame_snap_timer.start(300)

    def _on_frame_offset_dragged(self, idx: int, nx: int, ny: int):
        """微调帧偏移：debounce 后记录。"""
        if not hasattr(self, "_offset_snap_timer"):
            from PyQt6.QtCore import QTimer
            self._offset_snap_timer = QTimer(self)
            self._offset_snap_timer.setSingleShot(True)
            self._offset_snap_timer.timeout.connect(self._finish_offset_snapshot)
        if not getattr(self, "_offset_snap_before", None):
            self._offset_snap_before = self._capture_snapshot()
        self._offset_snap_timer.start(400)

    def _finish_offset_snapshot(self):
        before = getattr(self, "_offset_snap_before", None)
        if before is None:
            return
        after = self._capture_snapshot()
        cur = self.image_viewer.current_action
        name = f"「{cur.name}」" if cur else ""
        if before != after:
            self._undo_stack.push(_SnapshotCommand(self, before, after, f"微调帧偏移{name}"))
        self._offset_snap_before = None

    def _on_frames_updated(self):
        """后台检测完成后刷新动作帧和界面摘要。"""
        for action in self.actions:
            self.image_viewer.crop_frames_for_action(action)
        if self.image_viewer.current_action:
            self.preview.set_action(self.image_viewer.current_action)
        if self.image_viewer._pixmap and not self.image_viewer._pixmap.isNull():
            self.lbl_status.setText(
                f"已完成元素检测：当前共检测到 {len(self.image_viewer._frames)} 个元素"
            )
        self._transform_history_state = self._capture_transform_state()
        self._mark_project_dirty()
