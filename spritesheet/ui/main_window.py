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
from pathlib import Path

from PyQt6.QtCore import QPointF, QSize, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import (
    QAction, QIcon, QKeySequence, QPainter, QPixmap, QUndoCommand, QUndoStack,
)
from PyQt6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QProgressBar, QPushButton, QSplitter, QVBoxLayout, QWidget,
)

from spritesheet import __app_name__, __version__
from spritesheet.project_file import (
    _pixmap_bytes, _pixmap_from_bytes, _rect_from_dict, read_project, write_project,
)
from spritesheet.updater import UPDATE_URL, UpdateCheckThread, UpdateDownloadThread

from .image_viewer import SUPPORTED_IMAGE_EXT, ImageViewer
from .models import Action, MACARON_COLORS
from .preview import ActionPreviewWidget
from .transform import action_frame_pixmap
from .widgets import ActionCardWidget, ActionItemDelegate, _APP_ROOT, make_logo_pixmap

# 工程文件缩略图的长边尺寸：资源管理器按 96/256 显示，192 在"看得清"和
# "文件不变胖"之间比较平衡（.sstool 以这张 PNG 开头）。
_THUMB_LONG_SIDE = 192


# 主窗口
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

    def __init__(self, owner, before, after) -> None:
        super().__init__("变换")
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
        self._transform_history_state = None
        self._transform_history_pending = None
        self._restoring_transform_history = False
        self._project_path: Path | None = None
        self._project_dirty = False
        self._build_ui()
        self._build_menu()
        self._transform_history_state = self._capture_transform_state()

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

    def _begin_transform_history(self) -> None:
        if not self._restoring_transform_history and self._transform_history_pending is None:
            self._transform_history_pending = self._capture_transform_state()

    def _finish_transform_history(self) -> None:
        if self._transform_history_pending is None:
            return
        before = self._transform_history_pending
        self._transform_history_pending = None
        after = self._capture_transform_state()
        if before != after and not self._restoring_transform_history:
            self._undo_stack.push(_TransformHistoryCommand(self, before, after))
        self._transform_history_state = after

    def _on_transform_history_changed(self) -> None:
        if self._restoring_transform_history:
            return
        current = self._capture_transform_state()
        if self._transform_history_pending is not None:
            # Continuous drag is committed once on mouse release.
            return
        before = self._transform_history_state
        if before is not None and before != current:
            self._undo_stack.push(_TransformHistoryCommand(self, before, current))
        self._transform_history_state = current

    def _reset_transform_history(self) -> None:
        self._undo_stack.clear()
        self._transform_history_pending = None
        self._transform_history_state = self._capture_transform_state()

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
        if viewer.current_action and viewer.current_action.frames:
            index = min(self.preview._frame_index, len(viewer.current_action.frames) - 1)
            thumb = add_pixmap("thumbnail.png", action_frame_pixmap(viewer.current_action, index))
        if thumb is None:
            thumb = add_pixmap("thumbnail.png", viewer._pixmap)
        # 工程文件缩略图：归一尺寸后在右下角叠加 工程图标.png 角标。
        # .sstool 会以这张 PNG 开头，资源管理器（配好图片缩略图处理器后）直接显示它，
        # 所以这张图要经得起"被缩到 96px 显示"：太小则角标盖住内容，太大则文件变胖。
        if thumb and "thumbnail.png" in sections:
            preview_pm = _pixmap_from_bytes(sections["thumbnail.png"])
            badge = QPixmap(str(_APP_ROOT / "工程图标.png"))
            if not preview_pm.isNull():
                long_side = max(preview_pm.width(), preview_pm.height())
                if long_side > 0 and long_side != _THUMB_LONG_SIDE:
                    scale = _THUMB_LONG_SIDE / long_side
                    # 放大用最近邻（精灵图多为像素画，平滑会糊），缩小才用平滑
                    mode = (Qt.TransformationMode.FastTransformation if scale > 1.0
                            else Qt.TransformationMode.SmoothTransformation)
                    preview_pm = preview_pm.scaled(
                        max(1, round(preview_pm.width() * scale)),
                        max(1, round(preview_pm.height() * scale)),
                        Qt.AspectRatioMode.IgnoreAspectRatio,
                        mode,
                    )
                if not badge.isNull():
                    side = max(16, min(preview_pm.width(), preview_pm.height()) // 4)
                    margin = max(2, side // 8)
                    badge = badge.scaled(
                        side, side,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    painter = QPainter(preview_pm)
                    painter.drawPixmap(
                        preview_pm.width() - badge.width() - margin,
                        preview_pm.height() - badge.height() - margin,
                        badge,
                    )
                    painter.end()
                sections["thumbnail.png"] = _pixmap_bytes(preview_pm)
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
        name = self._project_path.name if self._project_path else "未命名工程"
        marker = " *" if self._project_dirty else ""
        self.setWindowTitle(f"{name}{marker} - {__app_name__} v{__version__}")

    def _mark_project_dirty(self) -> None:
        self._project_dirty = True
        self._set_project_title()

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
        self._set_project_title()
        self.lbl_status.setText(f"工程已保存：{target.name}")
        return True

    def _save_project_as(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(
            self, "保存 SSTOOL 工程", str(self._project_path or "工程.sstool"),
            "SSTOOL 工程 (*.sstool)"
        )
        if not path:
            return False
        if not path.lower().endswith(".sstool"):
            path += ".sstool"
        return self._save_project(path)

    def _new_project(self) -> None:
        """清空为空白工程。

        注意：按需求已从「文件」菜单移除（界面不再暴露入口），实现保留以便随时恢复
        （本机没有 git，删掉就找不回来了）。需要重新挂到菜单时，加一个
        ``QAction("新建工程")`` 连接到本方法即可。
        """
        self._end_all_renames()
        self.actions.clear()
        self.action_list.clear()
        viewer = self.image_viewer
        viewer._detect_generation += 1
        viewer._pixmap = None
        viewer._source_images = []
        viewer._frames = []
        viewer._frame_logical = {}
        viewer.set_current_action(None)
        self.preview.set_action(None)
        self.preview.set_source_pixmap(None)
        self._project_path = None
        self._project_dirty = False
        self._reset_transform_history()
        self._set_project_title()
        self.lbl_status.setText("已新建空白工程")

    def _open_project_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "打开 SSTOOL 工程", "", "SSTOOL 工程 (*.sstool)")
        if path:
            self._open_project(path)

    def _open_project(self, path: str) -> bool:
        try:
            manifest, sections = read_project(path)
            self._apply_sstool_project(manifest, sections)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "打开工程失败", str(exc))
            return False
        self._project_path = Path(path)
        self._project_dirty = False
        self._set_project_title()
        self.lbl_status.setText(f"工程已打开：{self._project_path.name}")
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

        # Logo 名称栏
        logo_bar = QWidget()
        logo_bar.setFixedHeight(64)
        logo_bar.setStyleSheet("background-color: #1a1a1a; border-bottom: 1px solid #333;")
        logo_layout = QHBoxLayout(logo_bar)
        logo_layout.setContentsMargins(8, 5, 8, 5)
        logo_layout.setSpacing(8)
        logo_label = QLabel()
        logo_label.setFixedSize(54, 54)
        software_icon = QIcon(str(_APP_ROOT / "SpriteSheetTool.ico"))
        real_logo = software_icon.pixmap(50, 50)
        if not real_logo.isNull():
            logo_label.setPixmap(real_logo)
        else:
            real_logo = QPixmap(str(_APP_ROOT / "SpriteSheetTool.png"))
            if not real_logo.isNull():
                logo_label.setPixmap(real_logo.scaled(
                    50,
                    50,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ))
            else:
                logo_label.setPixmap(make_logo_pixmap(50))
        # logo 白色描边（加粗）
        logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_label.setStyleSheet("border: 2px solid #ffffff; border-radius: 10px;")
        logo_layout.addWidget(logo_label)
        # 软件名 + 版本号：同一行，版本号在名字右边；整行底部与 logo 下边缘齐平
        name_row = QWidget()
        name_row.setFixedHeight(54)
        row = QHBoxLayout(name_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        name_label = QLabel(
            "<span style='color:#ffffff;'>SpriteSheet</span>"
            "<span style='color:#E91E63;'>Tool</span>"
        )
        name_label.setStyleSheet("background: transparent; border: none; font-size: 22px; font-weight: bold;")
        version_label = QLabel(f"v{__version__}")
        version_label.setStyleSheet("background: transparent; border: none; color: #9a9a9a; font-size: 11px;")
        row.addWidget(name_label, 0, Qt.AlignmentFlag.AlignBottom)
        row.addWidget(version_label, 0, Qt.AlignmentFlag.AlignBottom)
        row.addStretch()
        logo_layout.addWidget(name_row)
        logo_layout.addStretch()
        main_layout.addWidget(logo_bar)

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
        self.image_viewer.selection_changed.connect(self._on_selection_changed)
        self.image_viewer.selection_changed.connect(self._mark_project_dirty)
        self.image_viewer.frames_updated.connect(self._on_frames_updated)
        self.splitter.addWidget(self.image_viewer)

        self.preview = ActionPreviewWidget()
        self.preview.set_actions(self.actions)
        # 导出工程（.sstool）需要完整文档数据；provider 由主窗口注入
        self.preview._project_manifest = self._project_manifest
        self.preview.adjust_panel.changed.connect(self._on_transform_history_changed)
        self.preview.adjust_panel.changed.connect(self._mark_project_dirty)
        self.preview.canvas.transform_edit_started.connect(self._begin_transform_history)
        self.preview.canvas.transform_edit_finished.connect(self._finish_transform_history)
        self.preview.toolbar.loop_changed.connect(lambda _mode: self._mark_project_dirty())
        self.preview.toolbar.anchor_toggled.connect(lambda _checked: self._mark_project_dirty())
        self.preview.toolbar.bg_changed.connect(lambda _mode: self._mark_project_dirty())
        self.image_viewer._toolbar.bg_changed.connect(lambda _mode: self._mark_project_dirty())
        self.image_viewer._toolbar.compose_toggled.connect(lambda _checked: self._mark_project_dirty())
        # 程序退出（含未走 close 的路径）也要收尾原图区，否则线程/过滤器会悬空
        app = QApplication.instance()
        if app is not None:
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
        # 文件菜单分组：① 导入素材（添加图片 / 导入 JSON 工程）
        #              ② 工程读写（打开 / 保存 / 另存为）
        #              ③ 杂项（检查更新 / 退出）
        file_menu = menubar.addMenu("文件")
        act_open = QAction("添加图片", self)
        act_open.triggered.connect(self._open_image)
        file_menu.addAction(act_open)
        act_import = QAction("导入 JSON 工程…", self)
        act_import.setShortcut("Ctrl+I")
        act_import.triggered.connect(self._import_json_dialog)
        file_menu.addAction(act_import)
        file_menu.addSeparator()
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
        act_check_update = QAction("检查更新", self)
        act_check_update.triggered.connect(self._check_update_manual)
        file_menu.addAction(act_check_update)
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
        """菜单手动检查：始终弹窗反馈结果。"""
        self._start_update_check(notify_no_update=True)

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
                QMessageBox.information(
                    self, "检查更新",
                    f"当前已是最新版本 v{__version__}"
                    f"（服务器最新 v{info.get('version', '')}）",
                )
            elif status == "no_installer":
                QMessageBox.warning(
                    self, "检查更新",
                    "服务器目录里没有找到安装包。\n\n"
                    "请先把 SpriteSheetTool_v.X.Y.Z.exe 上传到：\n"
                    f"{info.get('url', '')}\n\n"
                    "（服务器需开启目录索引）",
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

    def closeEvent(self, event) -> None:
        """关闭窗口前先收尾原图区（停检测线程 + 卸下全局事件过滤器）。

        不做这两件事会导致随机闪退（原生 access violation）：检测线程在跑时宿主被
        析构、或 app 级事件过滤器指向已释放对象。参见 ``ImageViewer.shutdown``。
        """
        self.image_viewer.shutdown()
        super().closeEvent(event)

    def _open_image(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "添加图片", "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.gif *.webp)"
        )
        for path in paths:
            pm = QPixmap(path)
            if not pm.isNull():
                self._on_image_loaded(pm)

    # ---- 导入 JSON 工程 ----

    def _import_json_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入 JSON 工程", "", "精灵图 JSON (*.json)"
        )
        if path:
            self._import_project(path)

    def _import_project(self, path_str: str) -> None:
        """导入 JSON（自动识别格式），配套图片由 JSON 引用的文件名在同目录查找。"""
        from spritesheet.importer import ImportError_, load_project

        try:
            project = load_project(path_str)
        except ImportError_ as exc:
            QMessageBox.warning(self, "导入失败", str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - 兜底，避免导入异常打断程序
            QMessageBox.warning(self, "导入失败", f"解析 {path_str} 时出错：{exc}")
            return

        if self.actions:
            answer = QMessageBox.question(
                self, "导入工程",
                f"导入将<b>替换当前工程</b>（现有 {len(self.actions)} 个动作）。<br><br>"
                f"识别格式：{project.format_label}<br>"
                f"配套图片：{project.atlas_path.name}<br>"
                f"动作数量：{len(project.actions)} 个<br><br>是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        pm = QPixmap(str(project.atlas_path))
        if pm.isNull():
            QMessageBox.warning(self, "导入失败", f"无法读取配套图片：\n{project.atlas_path}")
            return

        try:
            self._apply_imported_project(project, pm)
        except Exception as exc:  # noqa: BLE001 - 导入失败不应导致程序退出
            import traceback
            traceback.print_exc()
            QMessageBox.warning(self, "导入失败", f"应用工程时出错：{exc}")

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

    def _apply_imported_project(self, project, pm: QPixmap) -> None:
        """把解析好的工程套用到界面：图集当原图、图块当元素、动作重建。

        若 JSON 带 elements（本工具格式），用其中的 origin 还原每个元素的**逻辑坐标**，
        于是合成帧的拼接间距与导出前完全一致 —— 图集块数、画面、合成组结构都不变。
        若缺少 elements（第三方格式），退化为把合成帧按 parts 拼成整块。
        """
        viewer = self.image_viewer
        atlas = pm

        tiles: list[tuple[int, int, int, int]] = []
        tile_index: dict[tuple[int, int, int, int], int] = {}

        def add_tile(rect) -> int:
            if rect not in tile_index:
                tile_index[rect] = len(tiles)
                tiles.append(rect)
            return tile_index[rect]

        # 1) 单部件帧的图块（直接来自图集）
        for act in project.actions:
            for fr in act.frames:
                if len(fr.parts) == 1:
                    add_tile(fr.parts[0].rect)

        # 2) 收集所有帧用到的图块（含合成帧的各个部件）——
        #    合成帧不再拼成整块，而是保留「每帧由哪些图块、贴在什么位置」的布局，
        #    这样既不会丢帧（同一元素可为多个帧服务），导出时也能复用元素图块。
        for act in project.actions:
            for fr in act.frames:
                for p in fr.parts:
                    add_tile(p.rect)

        # 3) 载入原图（其触发的异步自动检测随后会被作废）
        viewer.set_pixmap(atlas)
        viewer._detect_generation += 1
        viewer._frames = tiles

        # 4) 还原元素逻辑坐标（来自 JSON 的 elements；用于普通帧的对齐参考）
        logical: dict[int, tuple[int, int]] = {}
        for el in project.elements:
            rect = _rect_from_dict(el.get("rect") or {})
            o = el.get("origin") or {}
            if rect in tile_index:
                logical[tile_index[rect]] = (int(o.get("x", 0)), int(o.get("y", 0)))
        viewer._frame_logical = logical

        viewer.canvas.update()
        viewer._update_rulers()

        # 5) 重建动作
        # 先结束所有卡片的编辑并取消其待执行回调，再清空列表，
        # 否则卡片销毁后仍有 0ms 定时器回调 -> 访问已释放对象导致崩溃。
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
                layout = [(tile_index[p.rect], p.pos[0], p.pos[1])
                          for p in fr.parts if p.rect in tile_index]
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
                suffix = Path(path).suffix.lower()
                if suffix == ".sstool":
                    QTimer.singleShot(0, lambda p=path: self._safe_open_project(p))
                    event.acceptProposedAction()
                    return
                if suffix == ".json":
                    # 拖放回调期间不做重活：延迟处理，避免 OLE 拖放未结束就弹出模态框/重建界面
                    QTimer.singleShot(0, lambda p=path: self._import_project(p))
                    event.acceptProposedAction()
                    return
                if suffix in SUPPORTED_IMAGE_EXT:
                    QTimer.singleShot(0, lambda p=path: self._on_image_loaded(QPixmap(p)))
                    event.acceptProposedAction()
                    return
        except Exception:  # noqa: BLE001
            pass

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
        self.actions.append(action)
        self._reset_transform_history()
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

    def _on_action_color_changed(self, action: Action, color: str):
        action.color = color
        if self.image_viewer.current_action is action:
            self.image_viewer.action_color = color
        self.image_viewer.refresh()
        self._mark_project_dirty()

    def _on_action_name_changed(self, action: Action, name: str):
        action.name = name
        self._mark_project_dirty()

    def _delete_action(self, action: Action):
        """删除动作卡片并将选择切换到相邻动作。"""
        if action not in self.actions:
            return
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
        self._reset_transform_history()
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
            self._reset_transform_history()

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
        self._reset_transform_history()
        self._mark_project_dirty()
