#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""动作预览区（从 main_window.py 拆出，纯搬移，逻辑未改动）。

包含：
- ``PreviewFloatingBar``：播放器右上角浮动工具条（缩放 / 循环模式 / 锚点准星 / 背景）；
- ``PreviewCanvas``：预览画布（居中叠放播放、移动背景滚动、洋葱皮、锚点、地面线，
  以及 PS 式自由变换手柄：旋转 / 缩放 / 斜切 / 透视 / 扭曲）；
- ``AnimAdjustPanel``：动画调节组（动作类型、帧设置、自由变换、地面与锚点、当前帧微调）；
- ``ActionPreviewWidget``：预览窗口整体装配（画布 + 工具条 + 播放控制 + 帧缩略图 + 调节组）。

依赖方向：models ← transform ← widgets ← export_dialog ← preview（本模块不依赖 main_window；
导出弹窗由 ``ActionPreviewWidget._on_export_clicked`` 打开，属单向依赖）。
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QElapsedTimer, QPointF, QRectF, QSize, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QTransform,
)
from PyQt6.QtWidgets import (
    QButtonGroup, QCheckBox, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QPushButton, QScrollArea, QSizePolicy, QSlider,
    QSplitter, QVBoxLayout, QWidget,
)

from .export_dialog import ExportDialog
from .models import Action
from .transform import (
    DEFAULT_QUAD, DEFAULT_ROTATION_PIVOT, _alpha_bounds, _normalized_rotation_pivot,
    _rotation_transform, action_anchor_pixels, action_frame_pixmap, action_frame_size,
    compute_preview_layout, frame_base_size,
)
from .widgets import (
    CompactComboBox, CompactSpinBox, EditableNumberLabel, _GROUP_QSS, _SLIDER_QSS,
    make_tool_icon, paint_checkerboard,
)


class PreviewFloatingBar(QWidget):
    """播放器右上角浮动工具条：循环模式 / 锚点准星 / 背景切换（仿原图工具条）。"""

    loop_changed = pyqtSignal(str)
    anchor_toggled = pyqtSignal(bool)
    bg_changed = pyqtSignal(str)
    zoom_in_requested = pyqtSignal()
    zoom_out_requested = pyqtSignal()

    LOOP_ICONS = {
        Action.LOOP: ("loop", "循环播放"),
        Action.ONCE: ("once", "单次播放"),
        Action.PINGPONG: ("pingpong", "往返播放"),
    }

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(232, 32)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._loop_mode = Action.LOOP
        self._bg_mode = "checker"

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)

        self.btn_zoom_in = self._make_button("zoom_in", "放大")
        self.btn_zoom_in.clicked.connect(self.zoom_in_requested)
        layout.addWidget(self.btn_zoom_in)

        self.btn_zoom_out = self._make_button("zoom_out", "缩小")
        self.btn_zoom_out.clicked.connect(self.zoom_out_requested)
        layout.addWidget(self.btn_zoom_out)

        self.btn_loop = self._make_button("loop", "循环模式：循环播放（点击切换）")
        self.btn_loop.clicked.connect(self._cycle_loop)
        layout.addWidget(self.btn_loop)

        self.btn_anchor = self._make_button("anchor", "锚点准星", checkable=True)
        self.btn_anchor.clicked.connect(lambda: self.anchor_toggled.emit(self.btn_anchor.isChecked()))
        layout.addWidget(self.btn_anchor)

        # 背景切换
        self._bg_group = QButtonGroup(self)
        self.btn_bg_checker = self._make_button("bg_checker", "透明背景", checkable=True)
        self.btn_bg_black = self._make_button("bg_black", "黑色背景", checkable=True)
        self.btn_bg_white = self._make_button("bg_white", "白色背景", checkable=True)
        self._bg_group.addButton(self.btn_bg_checker)
        self._bg_group.addButton(self.btn_bg_black)
        self._bg_group.addButton(self.btn_bg_white)
        self.btn_bg_checker.setChecked(True)
        self.btn_bg_checker.clicked.connect(lambda: self.bg_changed.emit("checker"))
        self.btn_bg_black.clicked.connect(lambda: self.bg_changed.emit("black"))
        self.btn_bg_white.clicked.connect(lambda: self.bg_changed.emit("white"))
        layout.addWidget(self.btn_bg_checker)
        layout.addWidget(self.btn_bg_black)
        layout.addWidget(self.btn_bg_white)

    def _cycle_loop(self):
        order = [Action.LOOP, Action.ONCE, Action.PINGPONG]
        idx = order.index(self._loop_mode)
        self._loop_mode = order[(idx + 1) % len(order)]
        icon_kind, tip = self.LOOP_ICONS[self._loop_mode]
        self.btn_loop.setIcon(make_tool_icon(icon_kind, 16))
        self.btn_loop.setToolTip(f"循环模式：{tip}（点击切换）")
        self.loop_changed.emit(self._loop_mode)

    def sync_action(self, action: Action):
        """从动作同步按钮状态（不触发信号）。"""
        self._loop_mode = action.loop_mode
        icon_kind, tip = self.LOOP_ICONS[action.loop_mode]
        self.btn_loop.setIcon(make_tool_icon(icon_kind, 16))
        self.btn_loop.setToolTip(f"循环模式：{tip}（点击切换）")
        self.btn_anchor.setChecked(action.show_anchor)
        self._bg_mode = action.bg_mode
        if action.bg_mode == "checker":
            self.btn_bg_checker.setChecked(True)
        elif action.bg_mode == "black":
            self.btn_bg_black.setChecked(True)
        else:
            self.btn_bg_white.setChecked(True)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(30, 30, 30, 220))
        p.setPen(QPen(QColor("#444"), 1))
        p.drawRoundedRect(self.rect(), 6, 6)
        p.end()

    def _make_button(self, icon_kind: str, tip: str, checkable: bool = False) -> QPushButton:
        btn = QPushButton()
        btn.setFixedSize(24, 24)
        btn.setIcon(make_tool_icon(icon_kind, 16))
        btn.setIconSize(QSize(16, 16))
        btn.setToolTip(tip)
        btn.setCheckable(checkable)
        btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; border-radius: 3px; }"
            "QPushButton:hover { background: rgba(233,30,99,0.3); }"
            "QPushButton:checked { background: rgba(233,30,99,0.5); }"
        )
        return btn


class PreviewCanvas(QWidget):
    """动作预览画布：支持滚轮缩放、空格拖动，移动模式下背景滚动。"""

    MIN_ZOOM = 0.1
    MAX_ZOOM = 8.0

    # ---- 自由变换交互信号 ----
    # 进入/退出变换模式（'' 表示退出）；由帧设置按钮联动
    transform_mode_changed = pyqtSignal(str)
    # 请求把当前帧旋转设为 angle；由帧设置面板传播（数据复用）
    transform_rotated = pyqtSignal(int)
    # 请求把当前帧 quad 设为归一化 8 元组（或 None 清除）
    transform_quadded = pyqtSignal(object)
    # 请求把当前帧旋转中心设为归一化 (x, y)（或 None 清除）
    transform_pivot_moved = pyqtSignal(object)
    # 一次鼠标拖动的边界，用于把连续 mouseMove 合并为一个撤销步骤。
    transform_edit_started = pyqtSignal()
    transform_edit_finished = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(200)
        self.setStyleSheet("background-color: #141414; border: 1px solid #4a4a4a; border-radius: 4px;")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self._action: Action | None = None
        self._frame_index: int = 0
        # 缩放与拖动
        self._zoom = 1.0
        self._offset = QPointF(0, 0)
        self._fit = True  # 初始自适应
        self._panning = False
        self._pan_start = None
        self._toolbar = None
        # 移动模式背景滚动
        self._bg_scroll = 0.0
        self._bg_texture: QPixmap | None = None
        self._bg_texture_key: tuple[str, int] | None = None
        self._scroll_clock = QElapsedTimer()
        self._playing = False
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._scroll_timer.timeout.connect(self._scroll_background)
        # ---- 自由变换交互状态 ----
        self._transform_mode: str | None = None  # None/rotate/scale/skew/perspective/distort
        self._drag_kind: str | None = None       # None/corner/edge/rotate/pivot
        self._drag_index: int = -1
        self._drag_layout: tuple | None = None   # 拖动期间锁定的布局（避免帧跳动）
        self._drag_mo = QPointF(0, 0)            # 拖动开始时 quad 包围盒最小角（快照）
        self._drag_wrot: int = 0
        self._drag_hrot: int = 0
        self._drag_orig: tuple | None = None     # 拖动开始时 quad 快照（归一化 8 元组）
        self._drag_orig_c: list[QPointF] = []    # 拖动开始时 4 角画布逻辑坐标
        self._drag_center = QPointF(0, 0)        # 拖动开始时手柄中心（画布逻辑）
        self._drag_rot_start = 0                 # 拖动开始时旋转角度
        self._drag_rot_mouse_start = 0.0
        self._drag_pivot_canvas = QPointF(0, 0)
        # Hover feedback deliberately uses a tiny pulse rather than a layout
        # animation, so the control frame never changes size while painting.
        self._hover_kind: str | None = None
        self._hover_index: int = -1
        self._hover_phase = 0.0
        self._hover_timer = QTimer(self)
        self._hover_timer.setInterval(45)
        self._hover_timer.timeout.connect(self._advance_handle_hover)
        self._hover_timer.start()

    def set_toolbar(self, toolbar):
        self._toolbar = toolbar
        self._reposition_toolbar()

    def resizeEvent(self, event):
        if self._fit:
            self._apply_fit()
        self._reposition_toolbar()

    def _reposition_toolbar(self):
        if self._toolbar:
            self._toolbar.move(self.width() - self._toolbar.width() - 6, 6)

    def set_action(self, action: Action | None):
        self._action = action
        self._frame_index = 0
        self._hover_kind, self._hover_index = None, -1
        self._direction = 1
        self._bg_scroll = 0.0
        self._playing = False
        self._fit = True
        self._apply_fit()
        self._update_scroll_timer()
        if action is None:
            self.setMinimumHeight(100)
            self.setMaximumHeight(16777215)
        self.update()

    def set_frame(self, idx: int):
        if self._action and 0 <= idx < len(self._action.frames):
            self._frame_index = idx
            self._hover_kind, self._hover_index = None, -1
            self.update()

    def set_playing(self, playing: bool) -> None:
        self._playing = playing
        if playing:
            self._scroll_clock.restart()
        else:
            self._scroll_clock.invalidate()
        self._update_scroll_timer()

    def _update_scroll_timer(self):
        if self._playing and self._action and self._action.action_type == "move":
            self._scroll_timer.start(8)
        else:
            self._scroll_timer.stop()
            if not self._action or self._action.action_type != "move":
                self._bg_scroll = 0.0

    def _scroll_background(self):
        if self._playing and self._action and self._action.action_type == "move":
            elapsed_ms = self._scroll_clock.restart() if self._scroll_clock.isValid() else 16
            speed = self._action.move_speed * 9.0 * elapsed_ms / 1000.0
            # Keep the logical offset unbounded so elapsed-time updates never
            # jump when crossing an arbitrary wrap boundary.  The painter
            # applies the direction and texture-period modulo only when rendering.
            self._bg_scroll += speed
            self.update()

    def _draw_moving_background(self, painter: QPainter, rect: QRectF, scale: float) -> None:
        """绘制随动作方向和速度平移的平铺背景纹理。

        使用一张可缓存的 2x2 纹理交给 Qt 平铺，避免每个定时器 tick
        都逐格填充整个预览区，低缩放时尤其能减少卡顿。
        """
        if not self._action:
            return

        if self._action.bg_mode == "checker":
            colors = (QColor("#2a2a2a"), QColor("#1e1e1e"))
        elif self._action.bg_mode == "black":
            colors = (QColor("#0a0a0a"), QColor("#303030"))
        else:
            colors = (QColor("#ffffff"), QColor("#cfcfcf"))

        scale = max(0.1, scale)
        tile_px = max(2, int(round(48.0 * scale)))
        texture_key = (self._action.bg_mode, tile_px)
        if self._bg_texture is None or self._bg_texture_key != texture_key:
            texture = QPixmap(tile_px * 2, tile_px * 2)
            texture.fill(colors[0])
            tp = QPainter(texture)
            tp.fillRect(0, 0, tile_px, tile_px, colors[0])
            tp.fillRect(tile_px, tile_px, tile_px, tile_px, colors[0])
            tp.fillRect(tile_px, 0, tile_px, tile_px, colors[1])
            tp.fillRect(0, tile_px, tile_px, tile_px, colors[1])
            tp.end()
            self._bg_texture = texture
            self._bg_texture_key = texture_key

        tile = float(tile_px)
        # Use the actual rasterized texture period.  This keeps non-integer
        # zoom levels from accumulating a fractional-period seam at wrap.
        phase = (self._bg_scroll * scale) % (tile * 2.0)
        direction = self._action.move_direction
        shift_x = 0.0
        shift_y = 0.0
        # The preview represents the camera/background response, so it moves
        # opposite to the action direction: down -> background up, left ->
        # background right, and so on.
        if direction == "left":
            shift_x = phase
        elif direction == "right":
            shift_x = -phase
        elif direction == "up":
            shift_y = phase
        elif direction == "down":
            shift_y = -phase
        # QPainter's tile source offset moves the visible texture in the
        # opposite direction, so negate the camera shift here.
        painter.drawTiledPixmap(rect, self._bg_texture, QPointF(-shift_x, -shift_y))

    def _apply_fit(self):
        if not self._action or not self._action.frames:
            return
        canvas_w, canvas_h, _, _ = compute_preview_layout(self._action, self._frame_index)
        if canvas_w <= 0 or canvas_h <= 0:
            return
        zw = self.width() / canvas_w
        zh = self.height() / canvas_h
        self._zoom = min(zw, zh) * 0.9
        self._offset = QPointF(
            (self.width() - canvas_w * self._zoom) / 2,
            (self.height() - canvas_h * self._zoom) / 2,
        )

    def zoom_in(self):
        self._zoom_to(self._zoom * 1.2)

    def zoom_out(self):
        self._zoom_to(self._zoom / 1.2)

    def _zoom_to(self, new_zoom):
        self._zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, new_zoom))
        self._fit = False
        self.update()

    def wheelEvent(self, event):
        if not self._action or not self._action.frames:
            return
        delta = event.angleDelta().y()
        factor = 1.1 if delta > 0 else 1 / 1.1
        new_zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, self._zoom * factor))
        mouse_pos = event.position()
        old_zoom = self._zoom
        self._offset = QPointF(
            mouse_pos.x() - (mouse_pos.x() - self._offset.x()) * (new_zoom / old_zoom),
            mouse_pos.y() - (mouse_pos.y() - self._offset.y()) * (new_zoom / old_zoom),
        )
        self._zoom = new_zoom
        self._fit = False
        self.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Space:
            self._panning = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif (event.key() == Qt.Key.Key_T
              and event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            # PS-style shortcut: Ctrl+T enters the default free-transform mode.
            self.set_transform_mode("scale")
            event.accept()
        elif event.key() == Qt.Key.Key_Escape and self._transform_mode:
            self.set_transform_mode(None)
            event.accept()

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_Space:
            self._panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)

    # ==================== 自由变换交互 ====================

    def set_transform_mode(self, mode: str | None) -> None:
        """进入/退出变换模式（'rotate'/'scale'/'skew'/'perspective'/'distort'/''/None）。"""
        mode = mode or None
        if mode is not None and mode == self._transform_mode:
            mode = None  # 再点同按钮 = 退出
        self._transform_mode = mode
        self._drag_kind = None
        self._drag_layout = None
        self._hover_kind, self._hover_index = None, -1
        self.setCursor(Qt.CursorShape.CrossCursor if self._transform_mode else Qt.CursorShape.ArrowCursor)
        self.update()
        self.transform_mode_changed.emit(self._transform_mode or "")

    def transform_mode(self) -> str | None:
        return self._transform_mode

    def _current_layout(self):
        if not self._action or not self._action.frames:
            return None
        return compute_preview_layout(self._action, self._frame_index)

    def _quad_dst(self, quad):
        """归一化 quad → (相对旋转后帧原点的 4 角像素点, 基准宽, 基准高)。"""
        w, h = frame_base_size(self._action, self._frame_index)
        return ([QPointF(quad[0] * w, quad[1] * h), QPointF(quad[2] * w, quad[3] * h),
                 QPointF(quad[4] * w, quad[5] * h), QPointF(quad[6] * w, quad[7] * h)], w, h)

    def _rotation_pivot_output(self) -> QPointF:
        """旋转中心在旋转后基础图像中的坐标（未应用 quad）。"""
        if not self._action or not self._action.frames:
            return QPointF(0, 0)
        src = self._action.frames[self._frame_index]
        # Mirroring keeps the raster size; the pivot is defined in that space.
        src_w, src_h = src.width(), src.height()
        pivot = self._action.frame_rotation_pivots.get(
            self._frame_index, DEFAULT_ROTATION_PIVOT
        )
        matrix, pivot_px, min_pt = _rotation_transform(
            src_w, src_h, self._action.frame_rotations.get(self._frame_index, 0), pivot
        )
        mapped = matrix.map(pivot_px)
        rotated = src
        if self._frame_index in self._action.frame_mirror_h:
            rotated = rotated.transformed(QTransform().scale(-1.0, 1.0), Qt.TransformationMode.FastTransformation)
        if self._frame_index in self._action.frame_mirror_v:
            rotated = rotated.transformed(QTransform().scale(1.0, -1.0), Qt.TransformationMode.FastTransformation)
        if self._action.frame_rotations.get(self._frame_index, 0) % 360:
            rotated = rotated.transformed(matrix, Qt.TransformationMode.FastTransformation)
        content = _alpha_bounds(rotated)
        trim_x = content.x() if content else 0
        trim_y = content.y() if content else 0
        return QPointF(mapped.x() - min_pt.x() - trim_x,
                       mapped.y() - min_pt.y() - trim_y)

    def _rotation_pivot_canvas(self, layout=None, mo=None) -> QPointF:
        if not self._action or not self._action.frames:
            return QPointF(0, 0)
        layout = layout or self._current_layout()
        if layout is None:
            return QPointF(0, 0)
        _, _, positions, _ = layout
        tx, ty, _, _ = positions[self._frame_index]
        quad = self._action.frame_quads.get(self._frame_index, DEFAULT_QUAD)
        dst, w, h = self._quad_dst(quad)
        mo = mo if mo is not None else QPointF(min(p.x() for p in dst), min(p.y() for p in dst))
        point = self._rotation_pivot_output()
        if tuple(quad) != DEFAULT_QUAD:
            src_poly = QPolygonF([QPointF(0, 0), QPointF(w, 0), QPointF(w, h), QPointF(0, h)])
            dst_poly = QPolygonF(dst)
            matrix = QTransform()
            if QTransform.quadToQuad(src_poly, dst_poly, matrix):
                point = matrix.map(point)
        return QPointF(tx + point.x() - mo.x(), ty + point.y() - mo.y())

    def _current_mo(self) -> QPointF:
        """当前 quad 四角的最小角（pixmap 内容贴左上的平移量）。"""
        quad = self._action.frame_quads.get(self._frame_index, DEFAULT_QUAD)
        dst, _, _ = self._quad_dst(quad)
        return QPointF(min(p.x() for p in dst), min(p.y() for p in dst))

    def _handles_canvas(self, mo: QPointF, layout=None, anchor_override=None) -> list[QPointF]:
        """quad 四角 → 画布逻辑坐标（layout 传 None 用当前布局，或传拖动锁定的快照）。"""
        if layout is None:
            layout = self._current_layout()
        if layout is None:
            return []
        _, _, positions, _ = layout
        tx, ty, _, _ = positions[self._frame_index]
        quad = self._action.frame_quads.get(self._frame_index, DEFAULT_QUAD)
        dst, _, _ = self._quad_dst(quad)
        points = [QPointF(tx + p.x() - mo.x(), ty + p.y() - mo.y()) for p in dst]
        if anchor_override is not None:
            current = self._rotation_pivot_canvas(layout, mo)
            dx = anchor_override.x() - current.x()
            dy = anchor_override.y() - current.y()
            points = [QPointF(p.x() + dx, p.y() + dy) for p in points]
        return points

    def _logical_to_screen(self, pt: QPointF) -> QPointF:
        return QPointF(self._offset.x() + pt.x() * self._zoom,
                       self._offset.y() + pt.y() * self._zoom)

    def _screen_to_logical(self, pt: QPointF) -> QPointF:
        return QPointF((pt.x() - self._offset.x()) / self._zoom,
                       (pt.y() - self._offset.y()) / self._zoom)

    def _rotate_handle_screen(self, canvas_pts, pivot_canvas=None) -> QPointF | None:
        """旋转手柄屏幕坐标，位于以旋转中心为圆心的圆环顶部。"""
        if not canvas_pts:
            return None
        center = pivot_canvas or QPointF(
            sum(p.x() for p in canvas_pts) / 4.0,
            sum(p.y() for p in canvas_pts) / 4.0,
        )
        center_s = self._logical_to_screen(center)
        radius = max(math.hypot((p.x() - center.x()) * self._zoom,
                                (p.y() - center.y()) * self._zoom) for p in canvas_pts) + 28.0
        return QPointF(center_s.x(), center_s.y() - max(28.0, radius))

    def _rotation_circle(self, canvas_pts, pivot_canvas=None) -> tuple[QPointF, float, QPointF] | None:
        if not canvas_pts:
            return None
        center = pivot_canvas or QPointF(
            sum(p.x() for p in canvas_pts) / 4.0,
            sum(p.y() for p in canvas_pts) / 4.0,
        )
        center_s = self._logical_to_screen(center)
        radius = max(math.hypot((p.x() - center.x()) * self._zoom,
                                (p.y() - center.y()) * self._zoom) for p in canvas_pts) + 28.0
        radius = max(28.0, radius)
        return center_s, radius, QPointF(center_s.x(), center_s.y() - radius)

    def _hit_test(self, screen_pos):
        """返回 (kind, index)，只让当前模式的玫红控件可命中。"""
        if not self._transform_mode or not self._action or not self._action.frames:
            return None, -1
        layout = self._drag_layout if self._drag_kind else None
        mo = self._drag_mo if self._drag_kind else self._current_mo()
        pts = self._handles_canvas(mo, layout=layout)
        if not pts:
            return None, -1
        screens = [self._logical_to_screen(p) for p in pts]
        mode = self._transform_mode
        if mode == "rotate":
            pivot = self._rotation_pivot_canvas(layout, mo)
            pivot_s = self._logical_to_screen(pivot)
            if (pivot_s - screen_pos).manhattanLength() < 15:
                return "pivot", -1
            rot = self._rotate_handle_screen(pts, pivot)
            if rot is not None and (rot - screen_pos).manhattanLength() < 15:
                return "rotate", -1
            return None, -1
        if mode in ("scale", "perspective", "distort"):
            for i, s in enumerate(screens):
                if (s - screen_pos).manhattanLength() < 15:
                    return "corner", i
        elif mode == "skew":
            for i in range(4):
                mid = (screens[i] + screens[(i + 1) % 4]) / 2.0
                if (mid - screen_pos).manhattanLength() < 15:
                    return "edge", i
        return None, -1

    def _begin_drag(self, kind: str, index: int, screen_pos: QPointF) -> None:
        self.transform_edit_started.emit()
        self._drag_kind = kind
        self._drag_index = index
        self._drag_layout = self._current_layout()
        quad = self._action.frame_quads.get(self._frame_index, DEFAULT_QUAD)
        self._drag_orig = tuple(quad)
        dst, w, h = self._quad_dst(quad)
        self._drag_wrot, self._drag_hrot = w, h
        mo = QPointF(min(p.x() for p in dst), min(p.y() for p in dst))
        self._drag_mo = mo
        _, _, positions, _ = self._drag_layout
        tx, ty, _, _ = positions[self._frame_index]
        self._drag_orig_c = [QPointF(tx + p.x() - mo.x(), ty + p.y() - mo.y()) for p in dst]
        self._drag_center = QPointF(
            sum(p.x() for p in self._drag_orig_c) / 4.0,
            sum(p.y() for p in self._drag_orig_c) / 4.0,
        )
        self._drag_rot_start = self._action.frame_rotations.get(self._frame_index, 0)
        self._drag_pivot_canvas = self._rotation_pivot_canvas(self._drag_layout, self._drag_mo)
        if kind == "rotate":
            start = self._screen_to_logical(screen_pos)
            self._drag_rot_mouse_start = math.degrees(math.atan2(
                start.y() - self._drag_pivot_canvas.y(),
                start.x() - self._drag_pivot_canvas.x(),
            ))

    def _emit_pivot_from_canvas(self, canvas_point: QPointF) -> None:
        """Convert a displayed pivot point back to pre-rotation normalized space."""
        if not self._action or self._drag_layout is None:
            return
        _, _, positions, _ = self._drag_layout
        tx, ty, _, _ = positions[self._frame_index]
        quad = self._action.frame_quads.get(self._frame_index, DEFAULT_QUAD)
        dst, w, h = self._quad_dst(quad)
        mo = self._drag_mo
        point = QPointF(canvas_point.x() - tx + mo.x(), canvas_point.y() - ty + mo.y())
        if tuple(quad) != DEFAULT_QUAD:
            src_poly = QPolygonF([QPointF(0, 0), QPointF(w, 0), QPointF(w, h), QPointF(0, h)])
            matrix = QTransform()
            if not QTransform.quadToQuad(src_poly, QPolygonF(dst), matrix) or not matrix.isInvertible():
                return
            point = matrix.inverted()[0].map(point)
        src = self._action.frames[self._frame_index]
        angle = self._action.frame_rotations.get(self._frame_index, 0)
        current = self._action.frame_rotation_pivots.get(self._frame_index, DEFAULT_ROTATION_PIVOT)
        matrix, _, min_pt = _rotation_transform(src.width(), src.height(), angle, current)
        rotated = src
        if self._frame_index in self._action.frame_mirror_h:
            rotated = rotated.transformed(QTransform().scale(-1.0, 1.0), Qt.TransformationMode.FastTransformation)
        if self._frame_index in self._action.frame_mirror_v:
            rotated = rotated.transformed(QTransform().scale(1.0, -1.0), Qt.TransformationMode.FastTransformation)
        if angle % 360:
            rotated = rotated.transformed(matrix, Qt.TransformationMode.FastTransformation)
        content = _alpha_bounds(rotated)
        trim_x = content.x() if content else 0
        trim_y = content.y() if content else 0
        point = QPointF(point.x() + min_pt.x() + trim_x,
                        point.y() + min_pt.y() + trim_y)
        if matrix.isInvertible():
            point = matrix.inverted()[0].map(point)
        pivot = (max(-2.0, min(3.0, point.x() / max(1, src.width()))),
                 max(-2.0, min(3.0, point.y() / max(1, src.height()))))
        self.transform_pivot_moved.emit(tuple(round(v, 6) for v in pivot))

    def _emit_quad_from_canvas(self, canvas_pts) -> None:
        """把 4 个画布逻辑角点换算成归一化 quad 并发出信号。"""
        w, h = self._drag_wrot, self._drag_hrot
        mo = self._drag_mo
        _, _, positions, _ = self._drag_layout
        tx, ty, _, _ = positions[self._frame_index]
        quad = []
        for p in canvas_pts:
            dstx = p.x() - tx + mo.x()
            dsty = p.y() - ty + mo.y()
            quad.append(dstx / w if w else 0.0)
            quad.append(dsty / h if h else 0.0)
        self.transform_quadded.emit(tuple(round(v, 6) for v in quad))

    def _update_drag(self, screen_pos: QPointF) -> None:
        mode = self._transform_mode
        if mode == "rotate":
            cur = self._screen_to_logical(screen_pos)
            if self._drag_kind == "pivot":
                self._drag_pivot_canvas = cur
                self._emit_pivot_from_canvas(cur)
                return
            dx = cur.x() - self._drag_pivot_canvas.x()
            dy = cur.y() - self._drag_pivot_canvas.y()
            if dx or dy:
                current_mouse_angle = math.degrees(math.atan2(dy, dx))
                delta = (current_mouse_angle - self._drag_rot_mouse_start + 180.0) % 360.0 - 180.0
                self.transform_rotated.emit((self._drag_rot_start + int(round(delta))) % 360)
            return
        cur = self._screen_to_logical(screen_pos)
        kind = self._drag_kind
        orig_c = self._drag_orig_c
        if kind == "corner":
            k = self._drag_index
            if mode == "distort":
                new = [QPointF(p.x(), p.y()) for p in orig_c]
                new[k] = cur
            elif mode == "perspective":
                new = [QPointF(p.x(), p.y()) for p in orig_c]
                dx = cur.x() - orig_c[k].x()
                dy = cur.y() - orig_c[k].y()
                # 相邻两角联动：边 (k,k+1) 水平、边 (k-1,k) 垂直（对边保持不动 → 透视感）
                new[k].setX(orig_c[k].x() + dx)
                new[(k + 1) % 4].setX(orig_c[(k + 1) % 4].x() + dx)
                new[k].setY(orig_c[k].y() + dy)
                new[(k - 1) % 4].setY(orig_c[(k - 1) % 4].y() + dy)
            elif mode == "scale":
                anchor = orig_c[(k + 2) % 4]
                den_x = orig_c[k].x() - anchor.x()
                den_y = orig_c[k].y() - anchor.y()
                if abs(den_x) < 1e-6 or abs(den_y) < 1e-6:
                    return
                rx = (cur.x() - anchor.x()) / den_x
                ry = (cur.y() - anchor.y()) / den_y
                new = [QPointF(anchor.x() + (p.x() - anchor.x()) * rx,
                               anchor.y() + (p.y() - anchor.y()) * ry) for p in orig_c]
            else:
                return
            self._emit_quad_from_canvas(new)
        elif kind == "edge" and mode == "skew":
            k = self._drag_index
            new = [QPointF(p.x(), p.y()) for p in orig_c]
            if k in (0, 2):  # 顶/底边：水平斜切
                dx = cur.x() - (orig_c[k].x() + orig_c[(k + 1) % 4].x()) / 2.0
                new[k].setX(orig_c[k].x() + dx)
                new[(k + 1) % 4].setX(orig_c[(k + 1) % 4].x() + dx)
            else:            # 左/右边：垂直斜切
                dy = cur.y() - (orig_c[k].y() + orig_c[(k + 1) % 4].y()) / 2.0
                new[k].setY(orig_c[k].y() + dy)
                new[(k + 1) % 4].setY(orig_c[(k + 1) % 4].y() + dy)
            self._emit_quad_from_canvas(new)

    def _end_drag(self) -> None:
        had_drag = self._drag_kind is not None
        self._drag_kind = None
        self._drag_layout = None
        if had_drag:
            self.transform_edit_finished.emit()
        self.update()

    def _advance_handle_hover(self) -> None:
        if self._hover_kind is not None and self._transform_mode:
            self._hover_phase = (self._hover_phase + 0.32) % (math.pi * 2.0)
            self.update()

    def _update_hover(self, screen_pos: QPointF) -> None:
        kind, index = self._hit_test(screen_pos)
        if (kind, index) != (self._hover_kind, self._hover_index):
            self._hover_kind, self._hover_index = kind, index
            self._hover_phase = 0.0
            self.update()

    def leaveEvent(self, event):
        self._hover_kind, self._hover_index = None, -1
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        if self._panning and event.button() == Qt.MouseButton.LeftButton:
            self._pan_start = event.position() - self._offset
            return
        if self._transform_mode and event.button() == Qt.MouseButton.LeftButton:
            kind, idx = self._hit_test(event.position())
            if kind is None:
                self.set_transform_mode(None)  # 点空白退出
            else:
                self._begin_drag(kind, idx, event.position())

    def mouseMoveEvent(self, event):
        if self._panning and self._pan_start is not None:
            self._offset = event.position() - self._pan_start
            self.update()
            return
        if self._drag_kind and self._transform_mode:
            self._update_drag(event.position())
        elif self._transform_mode:
            self._update_hover(event.position())

    def mouseReleaseEvent(self, event):
        if self._panning:
            self._pan_start = None
        if self._drag_kind:
            self._end_drag()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # 整widget填深色
        p.fillRect(self.rect(), QColor("#141414"))

        if not self._action or not self._action.frames:
            p.setPen(QColor("#666"))
            p.setFont(QFont("Microsoft YaHei", 11))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "选择动作以预览")
            p.end()
            return

        canvas_w, canvas_h, positions, (pad, max_w, max_h) = compute_preview_layout(
            self._action, self._frame_index
        )
        # 缩放居中（支持用户缩放和拖动）
        scale = self._zoom
        cx = self._offset.x()
        cy = self._offset.y()
        canvas_rect = QRectF(cx, cy, canvas_w * scale, canvas_h * scale)

        # 背景填满整个播放窗口，内部画布只负责边框、帧和辅助线。
        background_rect = self.rect()
        background_scale = max(0.1, self._zoom)
        if self._action.action_type == "move":
            self._draw_moving_background(p, QRectF(background_rect), background_scale)
        elif self._action.bg_mode == "checker":
            # 一次平铺：原来 cell 最小 4，1000x800 下每帧最多约 5 万次 fillRect
            paint_checkerboard(p, background_rect, max(4, int(12 * background_scale)))
        elif self._action.bg_mode == "black":
            p.fillRect(background_rect, QColor("#000000"))
        else:
            p.fillRect(background_rect, QColor("#FFFFFF"))

        # 统一画布（安全区）边框：灰色虚线
        pen = QPen(QColor("#888"), 1.0, Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawRect(canvas_rect)

        # 参照帧（洋葱皮，半透明）
        if 0 <= self._action.ref_frame < len(self._action.frames) and self._action.ref_frame != self._frame_index:
            ref_tx, ref_ty, ref_w, ref_h = positions[self._action.ref_frame]
            ref_rect = QRectF(cx + ref_tx * scale, cy + ref_ty * scale, ref_w * scale, ref_h * scale)
            p.setOpacity(self._action.ref_opacity)
            ref_pm = action_frame_pixmap(self._action, self._action.ref_frame)
            p.drawPixmap(ref_rect, ref_pm, QRectF(ref_pm.rect()))
            p.setOpacity(1.0)

        # 当前帧（始终显示一帧，居中显示）
        if self._transform_mode and self._drag_layout is not None:
            # 拖动期间锁定布局位置，并用 quad 包围盒最小角变化补偿，避免帧跳动
            _, _, pos_drag, _ = self._drag_layout
            tx, ty, _, _ = pos_drag[self._frame_index]
            mo0 = self._drag_mo
            mo_new = self._current_mo()
            offx = mo_new.x() - mo0.x()
            offy = mo_new.y() - mo0.y()
            fw, fh = action_frame_size(self._action, self._frame_index)
            frame_rect = QRectF(cx + (tx + offx) * scale, cy + (ty + offy) * scale, fw * scale, fh * scale)
        else:
            tx, ty, fw, fh = positions[self._frame_index]
            frame_rect = QRectF(cx + tx * scale, cy + ty * scale, fw * scale, fh * scale)
        p.setOpacity(self._action.frame_opacity)
        frame_pm = action_frame_pixmap(self._action, self._frame_index)
        p.drawPixmap(frame_rect, frame_pm, QRectF(frame_pm.rect()))
        p.setOpacity(1.0)

        # 模拟地面线：默认在内容区最底部（不考虑四周扩展），向上偏移为正
        if self._action.ground_enabled:
            gy = cy + (pad + max_h - self._action.ground_offset) * scale
            p.setPen(QPen(QColor("#E91E63"), 1.5, Qt.PenStyle.DashLine))
            p.drawLine(int(canvas_rect.left()), int(gy), int(canvas_rect.right()), int(gy))

        # 锚点准星
        if self._action.show_anchor:
            anchor_x, anchor_y = action_anchor_pixels(self._action, canvas_w, canvas_h)
            ax = cx + anchor_x * scale
            ay = cy + anchor_y * scale
            p.setPen(QPen(QColor("#4FC3F7"), 1.5))
            p.drawLine(int(ax - 8), int(ay), int(ax + 8), int(ay))
            p.drawLine(int(ax), int(ay - 8), int(ax), int(ay + 8))
            p.drawEllipse(QPointF(ax, ay), 4, 4)

        # 自由变换手柄（PS 式控制框：四角/边点/旋转手柄）
        if self._transform_mode:
            self._draw_transform_handles(p)

        p.end()

    def _draw_transform_handles(self, p: QPainter) -> None:
        pts = self._handles_canvas(
            self._drag_mo if self._drag_kind else self._current_mo(),
            layout=self._drag_layout if self._drag_kind else None,
            anchor_override=self._drag_pivot_canvas if self._drag_kind and self._transform_mode == "rotate" else None,
        )
        if not pts:
            return
        screens = [self._logical_to_screen(pt) for pt in pts]
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        mode = self._transform_mode
        # 四边形边框保持低对比，避免把不可控点误认为可拖拽控件。
        p.setPen(QPen(QColor(120, 190, 205, 190), 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath()
        path.moveTo(screens[0])
        for s in screens[1:]:
            path.lineTo(s)
        path.closeSubpath()
        p.drawPath(path)
        def draw_square(center: QPointF, controllable: bool, kind: str, index: int, size: float) -> None:
            hovered = self._hover_kind == kind and self._hover_index == index
            pulse = 1.0 + (0.16 * (0.5 + 0.5 * math.sin(self._hover_phase)) if hovered else 0.0)
            half = size * pulse / 2.0
            if controllable:
                p.setPen(QPen(QColor("#FF80AB" if hovered else "#E91E63"), 1.2))
                p.setBrush(QColor("#FF4081" if hovered else "#E91E63"))
            else:
                # 空心/透明中心：明确表示该点在当前模式下不可控。
                p.setPen(QPen(QColor(233, 30, 99, 115), 1.0))
                p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRectF(center.x() - half, center.y() - half, half * 2, half * 2))
            if hovered and controllable:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor(255, 128, 171, 170), 1.0))
                p.drawEllipse(center, half + 4.0, half + 4.0)

        corner_control = mode in ("scale", "perspective", "distort")
        edge_control = mode == "skew"
        for i, s in enumerate(screens):
            draw_square(s, corner_control, "corner", i, 9.0)
        for i in range(4):
            mid = (screens[i] + screens[(i + 1) % 4]) / 2.0
            draw_square(mid, edge_control, "edge", i, 8.0)

        if mode == "rotate":
            pivot_canvas = self._rotation_pivot_canvas(
                self._drag_layout if self._drag_kind else None,
                self._drag_mo if self._drag_kind else None,
            )
            if self._drag_kind == "pivot":
                pivot_canvas = self._drag_pivot_canvas
            circle = self._rotation_circle(pts, pivot_canvas)
            if circle is not None:
                center_s, radius, rot = circle
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor(233, 30, 99, 210), 1.5))
                p.drawEllipse(center_s, radius, radius)
                p.setPen(QPen(QColor(233, 30, 99, 155), 1.0, Qt.PenStyle.DashLine))
                p.drawLine(center_s, rot)
                # 独立旋转中心：圆形十字，和动作锚点使用不同颜色。
                pivot_hover = self._hover_kind == "pivot"
                pulse = 1.0 + (0.18 * (0.5 + 0.5 * math.sin(self._hover_phase)) if pivot_hover else 0.0)
                p.setPen(QPen(QColor("#FF80AB" if pivot_hover else "#E91E63"), 1.6))
                p.setBrush(QColor("#FF4081" if pivot_hover else "#E91E63"))
                p.drawEllipse(center_s, 5.0 * pulse, 5.0 * pulse)
                p.setPen(QPen(QColor("#FFB0C8" if pivot_hover else "#F06292"), 1.2))
                p.drawLine(center_s + QPointF(-9, 0), center_s + QPointF(9, 0))
                p.drawLine(center_s + QPointF(0, -9), center_s + QPointF(0, 9))
                handle_hover = self._hover_kind == "rotate"
                handle_r = 6.0 * (1.16 if handle_hover else 1.0)
                p.setBrush(QColor("#FF4081" if handle_hover else "#E91E63"))
                p.setPen(QPen(QColor("#FFB0C8" if handle_hover else "#F06292"), 1.2))
                p.drawEllipse(rot, handle_r, handle_r)


class AnimAdjustPanel(QWidget):
    """动画调节组：统一画布/帧速度、对齐与地面、当前帧微调与参照帧、动作类型。"""

    changed = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._action: Action | None = None
        self._actions: list = []
        self._transform_changed = False
        self._build_ui()

    def _build_ui(self) -> None:
        self.setStyleSheet(_GROUP_QSS)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ---- 动作类型 ----
        g_type = QGroupBox("动作类型")
        type_layout = QVBoxLayout(g_type)
        type_layout.setContentsMargins(6, 12, 6, 6)
        type_layout.setSpacing(4)
        self.btn_type_static = QPushButton("静止")
        self.btn_type_static.setCheckable(True)
        self.btn_type_static.setChecked(True)
        self.btn_type_move = QPushButton("移动")
        self.btn_type_move.setCheckable(True)
        self._type_group = QButtonGroup(self)
        self._type_group.addButton(self.btn_type_static)
        self._type_group.addButton(self.btn_type_move)
        self.btn_type_static.clicked.connect(self._on_type_changed)
        self.btn_type_move.clicked.connect(self._on_type_changed)
        type_buttons_row = QHBoxLayout()
        type_buttons_row.setContentsMargins(0, 0, 0, 0)
        type_buttons_row.setSpacing(4)
        type_buttons_row.addWidget(self.btn_type_static)
        type_buttons_row.addWidget(self.btn_type_move)
        type_layout.addLayout(type_buttons_row)

        # 移动方向
        self.dir_widget = QWidget()
        dir_layout = QHBoxLayout(self.dir_widget)
        dir_layout.setContentsMargins(0, 0, 0, 0)
        dir_layout.setSpacing(2)
        dir_layout.addWidget(QLabel("方向"))
        self._dir_group = QButtonGroup(self)
        self.btn_dir_up = self._small_btn("", "向上移动", "arrow_up")
        self.btn_dir_down = self._small_btn("", "向下移动", "arrow_down")
        self.btn_dir_left = self._small_btn("", "向左移动", "arrow_left")
        self.btn_dir_right = self._small_btn("", "向右移动", "arrow_right")
        for btn in (self.btn_dir_up, self.btn_dir_down, self.btn_dir_left, self.btn_dir_right):
            btn.setCheckable(True)
            self._dir_group.addButton(btn)
            dir_layout.addWidget(btn)
        self.btn_dir_right.setChecked(True)
        self.btn_dir_up.clicked.connect(lambda: self._set_direction("up"))
        self.btn_dir_down.clicked.connect(lambda: self._set_direction("down"))
        self.btn_dir_left.clicked.connect(lambda: self._set_direction("left"))
        self.btn_dir_right.clicked.connect(lambda: self._set_direction("right"))
        type_layout.addWidget(self.dir_widget)

        # 移动速度
        self.speed_widget = QWidget()
        speed_layout = QHBoxLayout(self.speed_widget)
        speed_layout.setContentsMargins(0, 0, 0, 0)
        speed_layout.setSpacing(4)
        self.lbl_speed = QLabel("速度")
        self.slider_speed = QSlider(Qt.Orientation.Horizontal)
        self.slider_speed.setRange(1, 100)
        self.slider_speed.setValue(10)
        self.slider_speed.setStyleSheet(_SLIDER_QSS)
        self.slider_speed.valueChanged.connect(self._on_speed_changed)
        self.lbl_speed_value = QLabel("10")
        self.lbl_speed_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_speed_value.setMinimumWidth(28)
        self.slider_speed.valueChanged.connect(lambda value: self.lbl_speed_value.setText(str(value)))
        speed_layout.addWidget(self.lbl_speed)
        speed_layout.addWidget(self.slider_speed, 1)
        speed_layout.addWidget(self.lbl_speed_value)
        type_layout.addWidget(self.speed_widget)

        self.dir_widget.hide()
        self.speed_widget.hide()
        layout.addWidget(g_type)

        # ---- 帧设置 ----
        g1 = QGroupBox("帧设置")
        f1 = QFormLayout(g1)
        f1.setContentsMargins(6, 12, 6, 6)
        f1.setSpacing(4)
        self.spin_padding = CompactSpinBox()
        self.spin_padding.setRange(0, 200)
        self.spin_padding.setValue(0)
        self.spin_padding.valueChanged.connect(self._on_padding_changed)
        f1.addRow("四周扩展", self.spin_padding)

        fps_row = QHBoxLayout()
        fps_row.setContentsMargins(0, 0, 0, 0)
        fps_row.setSpacing(6)
        self.slider_fps = QSlider(Qt.Orientation.Horizontal)
        self.slider_fps.setRange(1, 60)
        self.slider_fps.setValue(10)
        self.slider_fps.setStyleSheet(_SLIDER_QSS)
        self.slider_fps.valueChanged.connect(self._on_fps_changed)
        self.lbl_fps = QLabel("10")
        self.lbl_fps.setFixedWidth(24)
        self.slider_fps.valueChanged.connect(lambda v: self.lbl_fps.setText(str(v)))
        fps_row.addWidget(self.slider_fps, 1)
        fps_row.addWidget(self.lbl_fps)
        fps_w = QWidget()
        fps_w.setLayout(fps_row)
        f1.addRow("帧速度", fps_w)

        self.lbl_canvas = QLabel("-")
        self.lbl_canvas.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_canvas.setMinimumWidth(72)
        self.chk_reuse = QCheckBox("数据复用")
        self.chk_reuse.setChecked(True)
        self.chk_reuse.stateChanged.connect(self._on_reuse_toggled)
        # 用显式透明容器：QFormLayout.addRow(布局) 会自动包一层 QWidget，
        # 那层容器会带上全局 QWidget 的底色，与面板背景不一致。
        canvas_wrap = QWidget()
        canvas_wrap.setStyleSheet("background: transparent;")
        canvas_row = QHBoxLayout(canvas_wrap)
        canvas_row.setContentsMargins(0, 0, 0, 0)
        canvas_row.setSpacing(4)
        canvas_row.addWidget(self.chk_reuse)
        canvas_row.addStretch(1)
        canvas_row.addWidget(QLabel("画布大小"))
        canvas_row.addWidget(self.lbl_canvas)
        f1.addRow(canvas_wrap)

        # ---- 自由变换（每帧独立；勾选"数据复用"时整组帧一起改）----
        # 变换模式按钮：点击后直接在预览上拖手柄调整（PS 式自由变换）
        trans_wrap = QWidget()
        trans_wrap.setStyleSheet("background: transparent;")
        trans_v = QVBoxLayout(trans_wrap)
        trans_v.setContentsMargins(0, 0, 0, 0)
        trans_v.setSpacing(5)
        transform_grid = QGridLayout()
        transform_grid.setContentsMargins(0, 0, 0, 0)
        transform_grid.setHorizontalSpacing(5)
        transform_grid.setVerticalSpacing(5)
        self.btn_mode_rotate = self._small_btn("旋转", "旋转：点击后在预览上拖圆形旋转手柄或旋转中心", "rotate")
        self.btn_mode_scale = self._small_btn("缩放", "缩放：点击后在预览上拖角点（对角固定）", "scale")
        self.btn_mode_skew = self._small_btn("斜切", "斜切：点击后在预览上拖边点", "skew")
        self.btn_mode_perspective = self._small_btn("透视", "透视：点击后在预览上拖角点（对边联动）", "perspective")
        self.btn_mode_distort = self._small_btn("扭曲", "扭曲：点击后在预览上拖角点（单角自由）", "distort")
        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        mode_buttons = (self.btn_mode_rotate, self.btn_mode_scale, self.btn_mode_skew,
                        self.btn_mode_perspective, self.btn_mode_distort)
        for row, b in enumerate(mode_buttons):
            b.setCheckable(True)
            b.setFixedHeight(30)
            b.setMinimumWidth(112)
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self._mode_group.addButton(b)
            transform_grid.addWidget(b, row, 0)
        self.btn_mode_rotate.toggled.connect(lambda on: self._on_mode_button("rotate", on))
        self.btn_mode_scale.toggled.connect(lambda on: self._on_mode_button("scale", on))
        self.btn_mode_skew.toggled.connect(lambda on: self._on_mode_button("skew", on))
        self.btn_mode_perspective.toggled.connect(lambda on: self._on_mode_button("perspective", on))
        self.btn_mode_distort.toggled.connect(lambda on: self._on_mode_button("distort", on))
        trans_v.addLayout(transform_grid)
        # 镜像按钮 + 只读变换摘要（旋转角度只能由预览圆形手柄修改）
        self.btn_mirror_h = self._small_btn("水平镜像", "水平镜像（左右翻转）", "mirror_h")
        self.btn_mirror_v = self._small_btn("垂直镜像", "垂直镜像（上下翻转）", "mirror_v")
        self.btn_mirror_h.setFixedHeight(30)
        self.btn_mirror_v.setFixedHeight(30)
        self.btn_mirror_h.setCheckable(True)
        self.btn_mirror_v.setCheckable(True)
        self.btn_mirror_h.clicked.connect(lambda: self._toggle_mirror("h"))
        self.btn_mirror_v.clicked.connect(lambda: self._toggle_mirror("v"))
        self.lbl_transform_summary = QLabel("当前变换：无变换")
        self.lbl_transform_summary.setMinimumHeight(28)
        self.lbl_transform_summary.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_transform_summary.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.lbl_transform_summary.setStyleSheet(
            "QLabel { color: #bdbdbd; background: #252525; border: 1px solid #3d3d3d; "
            "border-radius: 3px; padding: 0 7px; }"
        )
        self.lbl_transform_summary.setToolTip("当前帧的全部变换状态；旋转角度只读")
        mirror_row = QHBoxLayout()
        mirror_row.setContentsMargins(0, 0, 0, 0)
        mirror_row.setSpacing(5)
        mirror_row.addWidget(self.btn_mirror_h, 1)
        mirror_row.addWidget(self.btn_mirror_v, 1)
        mirror_row.addWidget(self.lbl_transform_summary, 2)
        transform_grid.addLayout(mirror_row, len(mode_buttons), 0)
        transform_grid.setColumnStretch(0, 1)
        f1.addRow("变换", trans_wrap)

        layout.addWidget(g1)

        # ---- 地面与锚点 ----
        g2 = QGroupBox("地面与锚点")
        g2_layout = QVBoxLayout(g2)
        g2_layout.setContentsMargins(6, 12, 6, 6)
        g2_layout.setSpacing(4)
        self.chk_ground = QCheckBox("显示地面线")
        self.chk_ground.setChecked(False)
        self.chk_ground.stateChanged.connect(self._on_ground_toggled)

        ground_row = QHBoxLayout()
        ground_row.setSpacing(4)
        ground_row.addWidget(self.chk_ground)
        ground_row.addWidget(QLabel("微调"))
        self.btn_ground_up = self._small_btn("", "地面线上移1px", "arrow_up")
        self.btn_ground_down = self._small_btn("", "地面线下移1px", "arrow_down")
        self.lbl_ground_offset = QLabel("0")
        self.lbl_ground_offset.setMinimumWidth(28)
        self.btn_ground_up.clicked.connect(lambda: self._adjust_ground(1))
        self.btn_ground_down.clicked.connect(lambda: self._adjust_ground(-1))
        ground_row.addWidget(self.btn_ground_up)
        ground_row.addWidget(self.btn_ground_down)
        ground_row.addWidget(self.lbl_ground_offset)
        ground_row.addStretch()
        g2_layout.addLayout(ground_row)
        layout.addWidget(g2)

        # ---- 当前帧微调 / 参照 ----
        g3 = QGroupBox("当前帧微调 / 参照")
        g3_layout = QVBoxLayout(g3)
        g3_layout.setContentsMargins(6, 12, 6, 6)
        g3_layout.setSpacing(4)

        offset_header = QHBoxLayout()
        offset_header.setSpacing(4)
        offset_header.addWidget(QLabel("帧偏移"))
        # 只显示数字，双击可直接输入（无上下调节箭头）
        self.spin_off_x = EditableNumberLabel(0)
        self.spin_off_x.setToolTip("当前帧水平偏移：双击可直接输入")
        self.spin_off_x.value_edited.connect(self._on_offset_label_changed)
        self.spin_off_y = EditableNumberLabel(0)
        self.spin_off_y.setToolTip("当前帧垂直偏移：双击可直接输入")
        self.spin_off_y.value_edited.connect(self._on_offset_label_changed)
        offset_header.addWidget(self.spin_off_x)
        offset_header.addWidget(self.spin_off_y)
        offset_header.addStretch()
        g3_layout.addLayout(offset_header)

        self.btn_up = self._small_btn("", "当前帧上移1px", "arrow_up")
        self.btn_down = self._small_btn("", "当前帧下移1px", "arrow_down")
        self.btn_left = self._small_btn("", "当前帧左移1px", "arrow_left")
        self.btn_right = self._small_btn("", "当前帧右移1px", "arrow_right")
        self.btn_up.clicked.connect(lambda: self._adjust_frame(0, -1))
        self.btn_down.clicked.connect(lambda: self._adjust_frame(0, 1))
        self.btn_left.clicked.connect(lambda: self._adjust_frame(-1, 0))
        self.btn_right.clicked.connect(lambda: self._adjust_frame(1, 0))
        self.btn_reset = self._small_btn("", "重置当前帧偏移", "reset")
        self.btn_reset.clicked.connect(self._reset_frame_offset)

        # 水平滑块（下方）与垂直滑块（右侧）：用于大幅调节偏移
        self.slider_off_h = QSlider(Qt.Orientation.Horizontal)
        self.slider_off_h.setRange(-200, 200)
        self.slider_off_h.setValue(0)
        self.slider_off_h.setFixedHeight(20)
        self.slider_off_h.setStyleSheet(_SLIDER_QSS)
        self.slider_off_h.setToolTip("大幅调节水平偏移（-200 ~ +200）")
        self.slider_off_h.valueChanged.connect(self._on_offset_slider_h)
        self.slider_off_v = QSlider(Qt.Orientation.Vertical)
        self.slider_off_v.setRange(-200, 200)
        self.slider_off_v.setValue(0)
        self.slider_off_v.setFixedWidth(20)
        self.slider_off_v.setStyleSheet(_SLIDER_QSS)
        self.slider_off_v.setToolTip("大幅调节垂直偏移（-200 ~ +200，向上为负）")
        self.slider_off_v.valueChanged.connect(self._on_offset_slider_v)

        offset_grid = QGridLayout()
        offset_grid.setContentsMargins(0, 0, 0, 0)
        offset_grid.setSpacing(2)
        offset_grid.addWidget(self.btn_up, 0, 1)
        offset_grid.addWidget(self.btn_left, 1, 0)
        offset_grid.addWidget(self.btn_reset, 1, 1)
        offset_grid.addWidget(self.btn_right, 1, 2)
        offset_grid.addWidget(self.btn_down, 2, 1)
        # 右侧：垂直滑块（跨 3 行）——调 Y
        offset_grid.addWidget(self.slider_off_v, 0, 3, 3, 1)
        # 下方：水平滑块（跨 3 列）——调 X
        offset_grid.addWidget(self.slider_off_h, 3, 0, 1, 3)
        offset_grid.setColumnStretch(0, 1)
        offset_grid.setColumnStretch(2, 1)
        g3_layout.addLayout(offset_grid)

        ref_row = QFormLayout()
        ref_row.setContentsMargins(0, 4, 0, 0)
        ref_row.setSpacing(4)
        self.combo_ref = CompactComboBox()
        self.combo_ref.currentIndexChanged.connect(self._on_ref_changed)
        ref_row.addRow("参照帧", self.combo_ref)

        # 同样用透明容器，避免 QFormLayout 包装层带来色差
        op_wrap = QWidget()
        op_wrap.setStyleSheet("background: transparent;")
        op_row = QHBoxLayout(op_wrap)
        op_row.setContentsMargins(0, 0, 0, 0)
        op_row.setSpacing(4)
        self.slider_opacity = QSlider(Qt.Orientation.Horizontal)
        self.slider_opacity.setRange(10, 100)
        self.slider_opacity.setValue(100)
        self.slider_opacity.setStyleSheet(_SLIDER_QSS)
        self.slider_opacity.valueChanged.connect(self._on_opacity_changed)
        op_row.addWidget(self.slider_opacity, 1)
        ref_row.addRow("当前帧透明度", op_wrap)
        g3_layout.addLayout(ref_row)
        layout.addWidget(g3)

        layout.addStretch()

    def _small_btn(self, text: str, tip: str, icon_kind: str | None = None) -> QPushButton:
        btn = QPushButton(text)
        btn.setFixedSize(24, 24)
        btn.setToolTip(tip)
        if icon_kind:
            btn.setIcon(make_tool_icon(icon_kind, 14))
            btn.setIconSize(QSize(14, 14))
        btn.setStyleSheet(
            "QPushButton { background: #2b2b2b; border: 1px solid #3d3d3d; border-radius: 3px; color: #e8e8e8; font-size: 11px; }"
            "QPushButton:hover { background: #3a3a3a; border-color: #E91E63; }"
            "QPushButton:checked { background: #E91E63; border-color: #E91E63; }"
        )
        return btn

    def set_action(self, action: Action | None):
        self._action = action
        if action is None:
            self.lbl_canvas.setText("-")
            self._sync_offset_widgets(0, 0)
            self.lbl_ground_offset.setText("0")
            self.combo_ref.clear()
            self._sync_transform_widgets()
            return
        self.spin_padding.blockSignals(True)
        self.spin_padding.setValue(action.padding)
        self.spin_padding.blockSignals(False)
        self.slider_fps.blockSignals(True)
        self.slider_fps.setValue(action.fps)
        self.lbl_fps.setText(str(action.fps))
        self.slider_fps.blockSignals(False)
        self.chk_ground.blockSignals(True)
        self.chk_ground.setChecked(action.ground_enabled)
        self.chk_ground.blockSignals(False)
        self.chk_reuse.blockSignals(True)
        self.chk_reuse.setChecked(action.reuse_frame_settings)
        self.chk_reuse.blockSignals(False)
        self.lbl_ground_offset.setText(str(action.ground_offset))
        # 动作类型
        if action.action_type == "move":
            self.btn_type_move.setChecked(True)
            self.dir_widget.show()
            self.speed_widget.show()
        else:
            self.btn_type_static.setChecked(True)
            self.dir_widget.hide()
            self.speed_widget.hide()
        # 方向
        dir_map = {"up": self.btn_dir_up, "down": self.btn_dir_down,
                   "left": self.btn_dir_left, "right": self.btn_dir_right}
        dir_map.get(action.move_direction, self.btn_dir_right).setChecked(True)
        # 速度
        self.slider_speed.blockSignals(True)
        self.slider_speed.setValue(action.move_speed)
        self.slider_speed.blockSignals(False)
        self.lbl_speed_value.setText(str(action.move_speed))
        # 参照帧下拉
        self.combo_ref.blockSignals(True)
        self.combo_ref.clear()
        self.combo_ref.addItem("无", -1)
        for i in range(len(action.frames)):
            self.combo_ref.addItem(f"帧{i + 1}", i)
        idx = self.combo_ref.findData(action.ref_frame)
        if idx >= 0:
            self.combo_ref.setCurrentIndex(idx)
        self.combo_ref.blockSignals(False)
        # 透明度
        self.slider_opacity.blockSignals(True)
        self.slider_opacity.setValue(int(action.frame_opacity * 100))
        self.slider_opacity.blockSignals(False)
        self._sync_transform_widgets()
        self._refresh_canvas_label()

    def _refresh_canvas_label(self):
        if self._action and self._action.frames:
            canvas_w, canvas_h, _, _ = compute_preview_layout(self._action, 0)
            self.lbl_canvas.setText(f"{canvas_w} × {canvas_h}")
            idx = getattr(self, "_current_frame", 0)
            dx, dy = self._action.frame_offsets.get(idx, (0, 0))
            self._sync_offset_widgets(dx, dy)
        else:
            self.lbl_canvas.setText("-")
            self._sync_offset_widgets(0, 0)

    def _sync_offset_widgets(self, dx: int, dy: int) -> None:
        """把当前帧偏移同步到数字文本与滑块（阻塞信号，避免递归触发）。"""
        self.spin_off_x.set_value(dx)
        self.spin_off_y.set_value(dy)
        for w, v in ((self.slider_off_h, dx), (self.slider_off_v, dy)):
            w.blockSignals(True)
            # 超出滑块范围时滑块钳制显示，文本始终显示真值
            w.setValue(max(w.minimum(), min(w.maximum(), v)))
            w.blockSignals(False)

    def _set_current_offset(self, dx: int | None = None, dy: int | None = None) -> None:
        """设置当前帧偏移的某个分量并刷新界面。"""
        if not self._action or not self._action.frames:
            return
        idx = getattr(self, "_current_frame", 0)
        if not (0 <= idx < len(self._action.frames)):
            return
        cur = self._action.frame_offsets.get(idx, (0, 0))
        nx = cur[0] if dx is None else int(dx)
        ny = cur[1] if dy is None else int(dy)
        if (nx, ny) == cur:
            return
        if nx == 0 and ny == 0:
            self._action.frame_offsets.pop(idx, None)
        else:
            self._action.frame_offsets[idx] = (nx, ny)
        self._sync_offset_widgets(nx, ny)
        self._refresh_canvas_label()   # 边缘并集随微调实时变化
        self.changed.emit()

    def _on_offset_label_changed(self, _value: int) -> None:
        self._set_current_offset(self.spin_off_x.value(), self.spin_off_y.value())

    def _on_offset_slider_h(self, value: int) -> None:
        self._set_current_offset(dx=value)

    def _on_offset_slider_v(self, value: int) -> None:
        self._set_current_offset(dy=value)

    def _on_type_changed(self):
        if not self._action:
            return
        if self.btn_type_move.isChecked():
            self._action.action_type = "move"
            self.dir_widget.show()
            self.speed_widget.show()
        else:
            self._action.action_type = "static"
            self.dir_widget.hide()
            self.speed_widget.hide()
        self.changed.emit()

    def _set_direction(self, direction: str):
        if self._action:
            self._action.move_direction = direction
            self.changed.emit()

    def _on_speed_changed(self, val: int):
        if self._action:
            self._action.move_speed = val
            self.changed.emit()

    def _on_padding_changed(self, val: int):
        if self._action:
            self._action.padding = val
            self._refresh_canvas_label()
            self.changed.emit()

    def _on_fps_changed(self, val: int):
        if self._action:
            self._action.fps = val
            self.changed.emit()

    def _on_reuse_toggled(self, state):
        if self._action:
            self._action.reuse_frame_settings = state == 2
            self.changed.emit()

    def _on_ground_toggled(self, state):
        if self._action:
            self._action.ground_enabled = state == 2
            self.changed.emit()

    def _adjust_ground(self, delta: int):
        """地面线上下调整：delta>0 上移，delta<0 下移。"""
        if self._action:
            self._action.ground_offset += delta
            self.lbl_ground_offset.setText(str(self._action.ground_offset))
            self.changed.emit()

    def _adjust_frame(self, dx: int, dy: int):
        """方向按钮：当前帧偏移微调 1px。"""
        if not self._action or not self._action.frames:
            return
        idx = getattr(self, "_current_frame", 0)
        if 0 <= idx < len(self._action.frames):
            cur = self._action.frame_offsets.get(idx, (0, 0))
            self._set_current_offset(cur[0] + dx, cur[1] + dy)

    def _reset_frame_offset(self):
        if not self._action:
            return
        idx = getattr(self, "_current_frame", 0)
        if idx in self._action.frame_offsets:
            del self._action.frame_offsets[idx]
        self._sync_offset_widgets(0, 0)
        self._refresh_canvas_label()
        self.changed.emit()

    def set_current_frame(self, idx: int):
        self._current_frame = idx
        self._sync_transform_widgets()

    def set_actions(self, actions: list):
        """提供动作列表引用，用于"数据复用"跨动作传播旋转/镜像。"""
        self._actions = actions

    def _sync_transform_widgets(self) -> None:
        """同步镜像按钮和只读变换摘要（阻塞信号避免递归）。"""
        if not self._action or not self._action.frames:
            self.lbl_transform_summary.setText("当前变换：无变换")
            for b in (self.btn_mirror_h, self.btn_mirror_v):
                b.blockSignals(True)
                b.setChecked(False)
                b.blockSignals(False)
            return
        idx = getattr(self, "_current_frame", 0)
        self.btn_mirror_h.blockSignals(True)
        self.btn_mirror_h.setChecked(idx in self._action.frame_mirror_h)
        self.btn_mirror_h.blockSignals(False)
        self.btn_mirror_v.blockSignals(True)
        self.btn_mirror_v.setChecked(idx in self._action.frame_mirror_v)
        self.btn_mirror_v.blockSignals(False)
        items = [f"旋转 {self._action.frame_rotations.get(idx, 0)}°"]
        modes = self._action.frame_transform_modes.get(idx, set())
        labels = {"scale": "缩放", "skew": "斜切", "perspective": "透视", "distort": "扭曲"}
        for key in ("scale", "skew", "perspective", "distort"):
            if key in modes:
                items.append(labels[key])
        if self._action.frame_quads.get(idx, DEFAULT_QUAD) != DEFAULT_QUAD and not modes:
            items.append("自由变换")
        if idx in self._action.frame_mirror_h:
            items.append("水平镜像")
        if idx in self._action.frame_mirror_v:
            items.append("垂直镜像")
        self.lbl_transform_summary.setText("当前变换：" + " · ".join(items))

    def _transform_targets(self) -> list:
        """返回 [(action, 帧索引迭代器)]。

        勾选"数据复用"时覆盖本动作所有帧，并同步到其他勾选了复用帧设置的动作；
        未勾选时只改当前帧。当前动作始终包含（即使未注册进动作列表）。
        """
        if not self._action:
            return []
        if getattr(self._action, "reuse_frame_settings", False):
            targets = []
            seen: set[int] = set()
            for act in getattr(self, "_actions", ()) or ():
                if act is not None and act.reuse_frame_settings:
                    targets.append((act, range(len(act.frames))))
                    seen.add(id(act))
            if id(self._action) not in seen:
                targets.append((self._action, range(len(self._action.frames))))
            return targets
        return [(self._action, [getattr(self, "_current_frame", 0)])]

    # ---- 自由变换：模式按钮 + 预览手柄回调 ----
    def set_canvas(self, canvas) -> None:
        self.canvas = canvas

    def _on_mode_button(self, mode: str, on: bool) -> None:
        canvas = getattr(self, "canvas", None)
        if canvas is None:
            return
        if on:
            canvas.set_transform_mode(mode)
        elif canvas.transform_mode() == mode:
            canvas.set_transform_mode(None)

    def _on_canvas_mode_changed(self, mode: str) -> None:
        """预览画布侧的退出（点空白/Esc/再点同按钮）→ 同步按钮选中态。"""
        btn = {"rotate": self.btn_mode_rotate, "scale": self.btn_mode_scale,
               "skew": self.btn_mode_skew, "perspective": self.btn_mode_perspective,
               "distort": self.btn_mode_distort}.get(mode)
        buttons = (self.btn_mode_rotate, self.btn_mode_scale, self.btn_mode_skew,
                   self.btn_mode_perspective, self.btn_mode_distort)
        # QButtonGroup 的 exclusive 模式下，显式暂时关闭互斥才能可靠地
        # 清除最后一个 checked 按钮（尤其是点击画布空白处退出时）。
        self._mode_group.setExclusive(False)
        for candidate in buttons:
            candidate.blockSignals(True)
            candidate.setChecked(candidate is btn)
            candidate.blockSignals(False)
        self._mode_group.setExclusive(True)

    def _apply_rotation(self, angle: int) -> None:
        """旋转角度变更（仅来自预览圆形手柄）→ 传播到目标帧。"""
        if not self._action or not self._action.frames:
            return
        angle = int(angle) % 360
        for act, indices in self._transform_targets():
            for i in indices:
                if angle == 0:
                    act.frame_rotations.pop(i, None)
                else:
                    act.frame_rotations[i] = angle
            act.invalidate_transforms()
        self._transform_changed = True
        self.changed.emit()
        self._sync_transform_widgets()

    def _apply_pivot(self, pivot) -> None:
        """旋转中心变更，只写入旋转参数，不触碰动作锚点。"""
        if not self._action or not self._action.frames:
            return
        pivot = _normalized_rotation_pivot(pivot)
        for act, indices in self._transform_targets():
            for i in indices:
                if pivot == DEFAULT_ROTATION_PIVOT:
                    act.frame_rotation_pivots.pop(i, None)
                else:
                    act.frame_rotation_pivots[i] = pivot
            act.invalidate_transforms()
        self._transform_changed = True
        self.changed.emit()

    def _apply_quad(self, quad) -> None:
        """自由四边形变更（预览拖角/边点）→ 传播到目标帧（数据复用联动）。"""
        if not self._action or not self._action.frames:
            return
        quad = tuple(quad)
        mode = getattr(getattr(self, "canvas", None), "transform_mode", lambda: None)() or ""
        for act, indices in self._transform_targets():
            for i in indices:
                if quad == DEFAULT_QUAD:
                    act.frame_quads.pop(i, None)
                    if mode:
                        act.frame_transform_modes.pop(i, None)
                else:
                    act.frame_quads[i] = quad
                    if mode:
                        act.frame_transform_modes.setdefault(i, set()).add(mode)
            act.invalidate_transforms()
        self._transform_changed = True
        self.changed.emit()
        self._sync_transform_widgets()

    def _toggle_mirror(self, axis: str) -> None:
        if not self._action or not self._action.frames:
            return
        checked = (self.btn_mirror_h if axis == "h" else self.btn_mirror_v).isChecked()
        for act, indices in self._transform_targets():
            target = act.frame_mirror_h if axis == "h" else act.frame_mirror_v
            for i in indices:
                if checked:
                    target.add(i)
                else:
                    target.discard(i)
            act.invalidate_transforms()
        self._transform_changed = True
        self.changed.emit()

        self._refresh_canvas_label()

    def _on_ref_changed(self, idx: int):
        if self._action and idx >= 0:
            self._action.ref_frame = self.combo_ref.itemData(idx)
            self.changed.emit()

    def _on_opacity_changed(self, val: int):
        """透明度滑块调整当前帧透明度（非参照帧）。"""
        if self._action:
            self._action.frame_opacity = val / 100.0
            self.changed.emit()


class ActionPreviewWidget(QWidget):
    """动作预览窗口：预览画布 + 浮动工具条 + 播放控制 + 帧缩略图 + 动画调节组。"""

    frame_changed = pyqtSignal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(460)
        self._action: Action | None = None
        self._actions: list[Action] = []
        self._source_pixmap: QPixmap | None = None
        self._frame_index = 0
        self._playing = False
        self._direction = 1  # 往返播放方向
        self._timer = QTimer(self)
        self._timer.setInterval(100)  # 默认 10 fps
        self._timer.timeout.connect(self._tick)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # 预览区（画布 + 浮动工具条）
        self.canvas = PreviewCanvas(self)
        self.toolbar = PreviewFloatingBar(self.canvas)
        self.canvas.set_toolbar(self.toolbar)
        self.toolbar.zoom_in_requested.connect(self.canvas.zoom_in)
        self.toolbar.zoom_out_requested.connect(self.canvas.zoom_out)
        self.toolbar.loop_changed.connect(self._on_loop_changed)
        self.toolbar.anchor_toggled.connect(self._on_anchor_toggled)
        self.toolbar.bg_changed.connect(self._on_bg_changed)

        # 预览画布与下方控制区使用垂直分栏，用户可拖动横向分隔线调整高度。
        lower = QWidget()
        lower_layout = QVBoxLayout(lower)
        lower_layout.setContentsMargins(0, 0, 0, 0)
        lower_layout.setSpacing(4)
        self.preview_splitter = QSplitter(Qt.Orientation.Vertical)
        self.preview_splitter.setHandleWidth(6)
        self.preview_splitter.setStyleSheet(
            "QSplitter::handle { background: #3d3d3d; }"
            "QSplitter::handle:hover { background: #E91E63; }"
        )
        self.preview_splitter.addWidget(self.canvas)
        self.preview_splitter.addWidget(lower)
        self.preview_splitter.setStretchFactor(0, 1)
        self.preview_splitter.setStretchFactor(1, 0)
        self.preview_splitter.setSizes([360, 420])
        layout.addWidget(self.preview_splitter, 1)

        # 控制栏：播放/暂停 + 进度条 + 帧序号（带背景）
        ctrl_widget = QWidget()
        ctrl_widget.setStyleSheet("background-color: #1a1a1a; border: none; border-radius: 4px;")
        ctrl = QHBoxLayout(ctrl_widget)
        ctrl.setContentsMargins(6, 4, 6, 4)
        ctrl.setSpacing(6)

        self.btn_play = QPushButton()
        self.btn_play.setFixedSize(24, 24)
        self.btn_play.setIcon(make_tool_icon("play", 16))
        self.btn_play.setIconSize(QSize(16, 16))
        self.btn_play.setToolTip("播放/暂停")
        self.btn_play.setStyleSheet(
            "QPushButton { background: transparent; border: none; border-radius: 3px; }"
            "QPushButton:hover { background: rgba(233,30,99,0.3); }"
        )
        self.btn_play.clicked.connect(self.toggle_play)
        ctrl.addWidget(self.btn_play)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.setValue(0)
        self.slider.setFixedHeight(10)
        self.slider.setStyleSheet(
            "QSlider::groove:horizontal { height: 1px; background: #3d3d3d; border-radius: 1px;}"
            "QSlider::handle:horizontal { width: 6px; height: 6px; margin: -2px 0; background: #E91E63; border-radius: 3px;}"
            "QSlider::handle:horizontal:hover { background: #F06292; }"
        )
        self.slider.valueChanged.connect(self._on_slider_changed)
        ctrl.addWidget(self.slider, 1)

        self.lbl_frame = QLabel("0/0")
        self.lbl_frame.setStyleSheet("color: #bdbdbd; font-size: 11px; background: transparent; border: none;")
        self.lbl_frame.setMinimumWidth(40)
        ctrl.addWidget(self.lbl_frame)

        lower_layout.addWidget(ctrl_widget)

        # 帧缩略图
        self.thumb_list = QListWidget()
        self.thumb_list.setFixedHeight(56)
        self.thumb_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.thumb_list.setIconSize(QSize(28, 28))
        self.thumb_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.thumb_list.setMovement(QListWidget.Movement.Static)
        self.thumb_list.setFlow(QListWidget.Flow.LeftToRight)
        self.thumb_list.setWrapping(False)
        self.thumb_list.setSpacing(2)
        self.thumb_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.thumb_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.thumb_list.setStyleSheet(
            "QListWidget { background: #1a1a1a; border: none; border-radius: 4px; padding: 2px; }"
            "QListWidget::item { width: 40px; height: 36px; margin: 2px; border: 1px solid #555555; border-radius: 3px; background: transparent; }"
            "QListWidget::item:selected { background: rgba(233,30,99,0.25); border: 1px solid #E91E63; }"
        )
        self.thumb_list.currentRowChanged.connect(self._on_thumb_changed)
        lower_layout.addWidget(self.thumb_list)

        # 动画调节组（可滚动）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.adjust_panel = AnimAdjustPanel()
        self.adjust_panel.set_canvas(self.canvas)
        # 预览画布 ↔ 帧设置面板：变换模式按钮、拖转/拖四边形互相同步
        self.canvas.transform_mode_changed.connect(self.adjust_panel._on_canvas_mode_changed)
        self.canvas.transform_rotated.connect(self.adjust_panel._apply_rotation)
        self.canvas.transform_quadded.connect(self.adjust_panel._apply_quad)
        self.canvas.transform_pivot_moved.connect(self.adjust_panel._apply_pivot)
        self.adjust_panel.changed.connect(self._on_adjust_changed)
        scroll.setWidget(self.adjust_panel)
        lower_layout.addWidget(scroll, 1)

        # 原图信息栏
        self.lbl_source_info = QLabel("未加载原图")
        self.lbl_source_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_source_info.setStyleSheet(
            "QLabel { color: #888; font-size: 10px; padding: 3px 4px; "
            "background: #1a1a1a; border: 1px solid #333; border-radius: 3px; }"
        )
        lower_layout.addWidget(self.lbl_source_info)

        # 导出按钮
        self.btn_export = QPushButton("导出")
        self.btn_export.setFixedHeight(36)
        self.btn_export.setStyleSheet(
            "QPushButton { background: #E91E63; color: white; border: none; border-radius: 4px; font-size: 13px; }"
            "QPushButton:hover { background: #F06292; }"
        )
        self.btn_export.clicked.connect(self._on_export_clicked)
        lower_layout.addWidget(self.btn_export)

        self._square_preview = True
        QTimer.singleShot(0, self._apply_default_square)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._action is None:
            QTimer.singleShot(0, self._apply_default_square)

    def _apply_default_square(self):
        if self._action is not None:
            return
        # 底部预留：原图信息栏 + 导出按钮 + spacing，避免挤压底部控件
        bottom_reserve = 90
        side = max(160, min(self.width() - 8, self.height() - bottom_reserve))
        self.canvas.setMinimumHeight(side)
        self.canvas.setMaximumHeight(side)

    # ---- 对外接口 ----
    def set_actions(self, actions: list[Action]) -> None:
        """提供动作列表引用，用于帧设置的数据复用。"""
        self._actions = actions
        panel = getattr(self, "adjust_panel", None)
        if panel is not None:
            panel.set_actions(actions)

    def set_source_pixmap(self, pm: QPixmap | None) -> None:
        """提供原始大图引用，供导出弹窗的原图预览使用，并更新原图信息栏。"""
        self._source_pixmap = pm
        if not hasattr(self, "lbl_source_info"):
            return
        if pm is not None and not pm.isNull():
            w, h = pm.width(), pm.height()
            pixels = w * h
            if pixels >= 1_000_000:
                pixel_text = f"{pixels / 1_000_000:.1f}MP"
            elif pixels >= 1_000:
                pixel_text = f"{pixels / 1_000:.0f}K"
            else:
                pixel_text = f"{pixels}"
            mem_bytes = pixels * 4  # RGBA 估算
            if mem_bytes >= 1024 * 1024:
                mem_text = f"{mem_bytes / (1024 * 1024):.1f}MB"
            else:
                mem_text = f"{mem_bytes / 1024:.0f}KB"
            self.lbl_source_info.setText(
                f"原图：{w}×{h} px · {pixel_text} · 约{mem_text}"
            )
        else:
            self.lbl_source_info.setText("未加载原图")

    def set_action(self, action: Action | None):
        # Switching actions must not leave the previous playback timer running.
        self._timer.stop()
        self._action = action
        self._frame_index = 0
        self._playing = False
        self.btn_play.setIcon(make_tool_icon("play", 16))
        self.canvas.set_action(action)
        if action is None:
            QTimer.singleShot(0, self._apply_default_square)
        else:
            # 进入动作预览后由垂直分栏控制高度，不沿用无动作状态的方形限制。
            self.canvas.setMinimumHeight(100)
            self.canvas.setMaximumHeight(16777215)
        self.adjust_panel.set_action(action)
        self.adjust_panel.set_current_frame(0)
        if action and action.frames:
            self._timer.setInterval(int(1000 / max(1, action.fps)))
            self.slider.setRange(0, len(action.frames) - 1)
            self.slider.setValue(0)
            self._update_frame_label()
            self._populate_thumbs()
            self.toolbar.sync_action(action)
        else:
            self.slider.setRange(0, 0)
            self.lbl_frame.setText("0/0")
            self.thumb_list.clear()

    def _populate_thumbs(self):
        self.thumb_list.clear()
        if not self._action:
            return
        for i in range(len(self._action.frames)):
            item = QListWidgetItem()
            pm = action_frame_pixmap(self._action, i)
            thumb = QPixmap(28, 28)
            thumb.fill(Qt.GlobalColor.transparent)
            painter = QPainter(thumb)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            scaled = pm.scaled(
                24, 24,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            painter.drawPixmap(
                (28 - scaled.width()) // 2,
                (28 - scaled.height()) // 2,
                scaled,
            )
            painter.end()
            item.setIcon(QIcon(thumb))
            item.setText(str(i + 1))
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)
            self.thumb_list.addItem(item)
        self.thumb_list.setCurrentRow(0)

    def _refresh_thumbs(self):
        """重建缩略图但保持当前选中帧（旋转/镜像变化后同步）。"""
        if not self._action:
            return
        row = self.thumb_list.currentRow()
        self._populate_thumbs()
        if row >= 0:
            self.thumb_list.setCurrentRow(min(row, len(self._action.frames) - 1))

    def _update_frame_label(self):
        if self._action and self._action.frames:
            self.lbl_frame.setText(f"{self._frame_index + 1}/{len(self._action.frames)}")
        else:
            self.lbl_frame.setText("0/0")

    def toggle_play(self):
        if not self._action or not self._action.frames:
            return
        self._playing = not self._playing
        self.canvas.set_playing(self._playing)
        if self._playing:
            self.btn_play.setIcon(make_tool_icon("pause", 16))
            self._timer.start()
        else:
            self.btn_play.setIcon(make_tool_icon("play", 16))
            self._timer.stop()

    def _tick(self):
        if not self._action or not self._action.frames:
            return
        n = len(self._action.frames)
        if self._action.loop_mode == Action.LOOP:
            self._frame_index = (self._frame_index + 1) % n
        elif self._action.loop_mode == Action.ONCE:
            if self._frame_index < n - 1:
                self._frame_index += 1
            else:
                self.toggle_play()
                return
        elif self._action.loop_mode == Action.PINGPONG:
            if n == 1:
                self._frame_index = 0
                self._direction = 1
                self._sync_frame()
                return
            self._frame_index += self._direction
            if self._frame_index >= n:
                self._frame_index = n - 2
                self._direction = -1
            elif self._frame_index < 0:
                self._frame_index = 1
                self._direction = 1
        self._sync_frame()

    def _sync_frame(self):
        self.slider.blockSignals(True)
        self.slider.setValue(self._frame_index)
        self.slider.blockSignals(False)
        self.thumb_list.blockSignals(True)
        self.thumb_list.setCurrentRow(self._frame_index)
        self.thumb_list.blockSignals(False)
        self.canvas.set_frame(self._frame_index)
        self.adjust_panel.set_current_frame(self._frame_index)
        self._update_frame_label()
        self.frame_changed.emit(self._frame_index)

    def _on_slider_changed(self, val: int):
        self._frame_index = val
        self._sync_frame()

    def _on_thumb_changed(self, row: int):
        if row >= 0:
            self._frame_index = row
            self._sync_frame()

    # ---- 浮动工具条槽 ----
    def _on_loop_changed(self, mode: str):
        if self._action:
            self._action.loop_mode = mode

    def _on_anchor_toggled(self, checked: bool):
        if self._action:
            self._action.show_anchor = checked
            self.canvas.update()

    def _on_bg_changed(self, mode: str):
        if self._action:
            self._action.bg_mode = mode
            self.canvas.update()

    def _on_adjust_changed(self):
        if self._action:
            # 勾选“数据复用”的动作共享帧速度与四周扩展；未勾选动作
            # 保留自己的设置。当前动作是本次编辑的唯一数据源。
            if self._action.reuse_frame_settings:
                for action in self._actions:
                    if action is not self._action and action.reuse_frame_settings:
                        action.padding = self._action.padding
                        action.fps = self._action.fps
            self._timer.setInterval(int(1000 / max(1, self._action.fps)))
            self.canvas._update_scroll_timer()
            if self.canvas._fit and not self.canvas._drag_kind:
                # 拖动变换手柄期间保持视图缩放稳定，避免每帧重新自适应导致跳动
                self.canvas._apply_fit()
            self.canvas.update()
            self.adjust_panel._refresh_canvas_label()
            # 旋转/镜像变化后同步帧缩略图
            if getattr(self.adjust_panel, "_transform_changed", False):
                self.adjust_panel._transform_changed = False
                self._refresh_thumbs()

    def _on_export_clicked(self) -> None:
        dlg = ExportDialog(
            self._actions,
            self._source_pixmap,
            # 工程数据提供者由 MainWindow 注入（self._project_manifest 本类没有定义）
            project_provider=getattr(self, "_project_manifest", None),
            parent=self,
        )
        dlg.exec()
