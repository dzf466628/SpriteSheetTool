#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""动作预览区（从 main_window.py 拆出，纯搬移，逻辑未改动）。

包含：
- ``PreviewFloatingBar``：播放器右上角浮动工具条（缩放 / 循环模式 / 锚点准星 / 背景）；
- ``PreviewCanvas``：预览画布（居中叠放播放、移动背景滚动、洋葱皮、锚点、地面线，
  以及 PS 式自由变换手柄：旋转 / 缩放 / 斜切 / 透视 / 扭曲）；
- ``AnimAdjustPanel``：帧编辑面板（当前帧的自由变换、偏移、本帧时长、参照帧/透明度）；
  动作级参数（动作类型/帧速/扩展/循环/锚点/地面线）在 ``anim_settings.AnimSettingsBar``；
- ``ActionPreviewWidget``：预览窗口整体装配（画布 + 工具条 + 播放控制 + 帧缩略图 + 调节组）。

依赖方向：models ← transform ← widgets ← export_dialog ← preview（本模块不依赖 main_window；
导出弹窗由 ``ActionPreviewWidget._on_export_clicked`` 打开，属单向依赖）。
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QElapsedTimer, QPointF, QRect, QRectF, QSize, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QTransform,
)
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QPushButton, QScrollArea, QSizePolicy, QSlider,
    QSplitter, QVBoxLayout, QWidget,
)

from .export_dialog import ExportDialog
from .models import Action
from .transform import (
    DEFAULT_QUAD, DEFAULT_ROTATION_PIVOT, _alpha_bounds, _normalized_rotation_pivot,
    _rotation_transform, action_anchor_pixels, action_frame_pixmap, action_frame_size,
    compute_preview_layout, distribute_whole_quad, frame_base_size, frame_has_content,
    frame_pivot_in_pixmap, frame_pivot_output, transform_ratio_limit, whole_reference,
)
from .widgets import (
    CompactComboBox, CompactSpinBox, EditableNumberLabel, HintToast, HoverIconButton,
    ResponsiveButtonGrid, _BIPOLAR_SLIDER_QSS, _GROUP_QSS, _SLIDER_QSS, make_tool_icon,
    paint_checkerboard,
)

# 无动作时点控件的统一提示（四处入口共用一句，避免文案漂移）
NO_ACTION_HINT = "请先添加动作：点左侧「+ 添加动作」"


# 四角顺序（与 DEFAULT_QUAD 一致）：0=左上 1=右上 2=右下 3=左下。
# 源矩形里 (0,1) 与 (2,3) 是**水平**边，(1,2) 与 (3,0) 是垂直边 ——
# 自由变换的联动判定按这个来（透视模式要用，见 _update_drag）。
_HORIZONTAL_EDGES = frozenset({(0, 1), (2, 3)})


class PreviewFloatingBar(QWidget):
    """播放器右上角浮动工具条：缩放 / 背景切换（循环与锚点已移至「动画设置」横条）。"""

    bg_changed = pyqtSignal(str)
    zoom_in_requested = pyqtSignal()
    zoom_out_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # 宽度=三颗背景按钮（24×3 + 间距 + 边距）：撤掉放大/缩小后不能再留 136 的空档
        # 位置由 _reposition_toolbar() 顶着画布右上角，所以缩短后自然就是右对齐
        self.setFixedSize(86, 32)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._bg_mode = "checker"

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)

        # 放大/缩小按钮已按用户要求撤掉（「功能还在就是没必要放这里」）：缩放走滚轮/快捷键，
        # 信号仍保留在类里，只是这里不再摆按钮。不要再加回来。

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

    def sync_action(self, action: Action):
        """从动作同步背景按钮状态（不触发信号）。"""
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
    # 按住 Shift 时：锁轴换方向所需的额外位移（滞回，避免 45° 附近来回抖）
    AXIS_LOCK_HYSTERESIS = 4.0
    # 按住 Shift 旋转时的角度吸附档位（PS 是 15°）
    ROTATE_SNAP_DEGREES = 15

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
    # 参照帧模式下鼠标拖动当前帧：发送(帧索引, dx, dy)
    frame_offset_dragged = pyqtSignal(int, int, int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(200)
        self.setStyleSheet("background-color: #141414; border: 1px solid #4a4a4a; border-radius: 4px;")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self._action: Action | None = None
        self._frame_index: int = 0
        # Shift 锁轴：本次拖拽锁定在哪个轴（None = 未锁）
        self._axis_lock: str | None = None
        self._last_drag_screen: QPointF | None = None
        # 缩放与拖动
        self._zoom = 1.0
        self._offset = QPointF(0, 0)
        self._fit = True  # 初始自适应
        self._panning = False
        self._pan_start = None
        # 必须能收键盘：不然空格键根本到不了这里（用户反馈「按住空格拖不动」多是这个）
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
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
        self._drag_pivot_screen = QPointF(0, 0)
        self._drag_ratio_limit = 8.0
        self._drag_use_pivot = False     # 缩放是否以红色锚点为不动点
        # ---- 视图锚定：变换拖动期间把「不动点」钉死在屏幕上 ----
        # 布局 compute_preview_layout 只按"变换后的内容包围盒"给每帧定位，帧的
        # 绝对落点信息会被丢掉。于是旋转时被钉住的是内容包围盒的左上角、不是旋转
        # 中心 —— 画面看起来是"图像在爬"而不是"绕红点转"；四边形的位移量(mo)在
        # 松手重排布局后也会被丢掉，表现为松手回弹几十像素。
        # 这里不改数据/导出布局，只在绘制期给画布加一个平移，把本次交互的"不动点"
        # （旋转中心 / 拖动锚点角）钉在它拖动开始时的屏幕位置：目标不动、灰色画布框
        # 自己动。松手后锚点继续保持，直到换帧/换动作/退出变换/缩放平移。
        self._view_shift = QPointF(0.0, 0.0)
        self._anchor_spec: tuple | None = None   # ('pivot',) | ('frac', fx, fy)
        self._anchor_screen: QPointF | None = None
        self._last_resize: QSize | None = None
        # 用户要求：编辑锚点只影响之后的操作，不影响已经做过的变换。落位现在与 pivot
        # 完全无关（_paint_whole_frames 按实时布局画），所以这里不再需要快照。
        # Hover feedback deliberately uses a tiny pulse rather than a layout
        # animation, so the control frame never changes size while painting.
        self._hover_kind: str | None = None
        self._hover_index: int = -1
        self._hover_phase = 0.0
        self._hover_timer = QTimer(self)
        self._hover_timer.setInterval(45)
        self._hover_timer.timeout.connect(self._advance_handle_hover)
        self._hover_timer.start()
        # ---- 偏移拖动期间冻结画布区域 ----
        # 用户反馈："拖动滑块（帧）不动、只有区域在动，很难受；让帧真的动，
        # 松手以后再计算区域"。所以拖动期间画布尺寸/位置全部冻结，只把"相对拖动
        # 起点的偏移增量"加到当前帧上；松手再按新偏移重算并集。
        self._frozen_layout: tuple | None = None
        self._frozen_index: int = 0
        # 多选叠加帧（除主帧外的选中帧，半透明叠加显示）
        self._overlay_frames: list[int] = []
        self._frozen_offset: tuple[int, int] = (0, 0)
        # 冻结期间参与位置增量叠加的帧及各自起始偏移（整体平移时为多帧）
        self._frozen_indices: list[int] = []
        self._frozen_offsets: dict = {}
        # 整体(GIF 级)平移：变换模式下在整体框内部拖动，作用于全部目标帧
        self._frame_drag_whole: bool = False
        # 当前变换模式的发起方：'frame'（右侧帧编辑）/ 'all'（底部动作编辑横条）。
        # 两套变换工具按钮独立：谁发起谁点亮，切换/退出时只有发起方弹回。
        self._transform_owner = ""
        # 本次变换作用的目标帧（动作编辑=全部帧；帧编辑=当前帧/Ctrl 多选）。
        # 多于 1 帧时进入「整体(GIF 级)变换」：手柄围绕整体包围盒、围绕共同中心。
        self._transform_indices: list[int] = []
        self._drag_whole: tuple | None = None
        # 参照帧模式下鼠标拖动当前帧的状态
        self._frame_drag_active: bool = False
        self._frame_drag_last: QPointF | None = None
        self._toast = HintToast(self)

    # ---- 无动作提示 ----
    def hint_no_action(self) -> None:
        """没有当前动作时，在被点的控件上给出"先添加动作"的提示。"""
        self._toast.show(NO_ACTION_HINT)

    # ---- 偏移拖动：冻结画布区域 ----
    def begin_layout_freeze(self, indices=None) -> None:
        """冻结画布尺寸与各帧位置（拖动偏移滑块/方向按钮时调用）。

        indices 指定拖动会改变哪些帧的偏移（整体平移时传全部目标帧）；
        默认仅当前帧（参照帧拖动/偏移控件的原行为）。
        """
        if self._frozen_layout is not None:
            return
        if not self._action or not self._action.frames:
            return
        if not (0 <= self._frame_index < len(self._action.frames)):
            return
        # 拖偏移是要让帧跟着手走，不能被视图锚定抵消掉
        self.clear_view_anchor()
        self._frozen_layout = compute_preview_layout(self._action, self._frame_index)
        self._frozen_index = self._frame_index
        self._frozen_offset = self._action.frame_offsets.get(self._frame_index, (0, 0))
        if indices is None:
            indices = [self._frame_index]
        idxs = sorted({int(i) for i in indices if 0 <= int(i) < len(self._action.frames)})
        self._frozen_indices = idxs
        self._frozen_offsets = {i: self._action.frame_offsets.get(i, (0, 0)) for i in idxs}
        self.update()

    def end_layout_freeze(self) -> None:
        """松手：解除冻结并重绘画布（区域并集按新偏移重算）。"""
        if self._frozen_layout is None:
            return
        self._frozen_layout = None
        self._frozen_indices = []
        self._frozen_offsets = {}
        self.update()

    def layout_frozen(self) -> bool:
        return self._frozen_layout is not None

    def _paint_layout(self):
        """绘制用的布局：冻结期间 = 冻结布局 + 当前帧的偏移增量。"""
        if self._frozen_layout is None:
            return self._current_layout()
        canvas_w, canvas_h, positions, meta = self._frozen_layout
        idxs = self._frozen_indices or [self._frozen_index]
        positions = list(positions)
        changed = False
        for fi in idxs:
            if self._action and 0 <= fi < len(positions):
                base = self._frozen_offsets.get(fi, self._frozen_offset if fi == self._frozen_index else (0, 0))
                cur = self._action.frame_offsets.get(fi, (0, 0))
                dx = cur[0] - base[0]
                dy = cur[1] - base[1]
                if dx or dy:
                    tx, ty, fw, fh = positions[fi]
                    positions[fi] = (tx + dx, ty + dy, fw, fh)
                    changed = True
        if not changed:
            # 保持与旧行为一致：无增量时直接返回冻结布局
            return self._frozen_layout
        return canvas_w, canvas_h, positions, meta

    def set_toolbar(self, toolbar):
        self._toolbar = toolbar
        self._reposition_toolbar()

    def resizeEvent(self, event):
        # 只有尺寸真的变了才重置视图锚定/自适应：Qt 在离屏渲染(render)等场景
        # 会补发一次同尺寸 resize，不能因此把正在进行的变换锚定清掉。
        size = event.size()
        changed = self._last_resize is not None and size != self._last_resize
        self._last_resize = QSize(size)
        if changed:
            self.clear_view_anchor()
            if self._fit:
                self._apply_fit()
        self._reposition_toolbar()

    def _reposition_toolbar(self):
        if self._toolbar:
            self._toolbar.move(self.width() - self._toolbar.width() - 6, 6)

    def set_action(self, action: Action | None):
        self._action = action
        self._frame_index = 0
        self._frozen_layout = None
        self.clear_view_anchor()
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

    def set_overlay_frames(self, indices: list[int]) -> None:
        """设置除主帧外需叠加显示的帧（Ctrl 多选）。"""
        self._overlay_frames = list(indices)
        self.update()

    def _drag_target_indices(self) -> list[int]:
        """画布拖动帧偏移的目标帧：Ctrl 多选时 = 全部选中帧，否则 = 当前帧。"""
        get_rows = getattr(self, "_get_selected_rows", None)
        if get_rows is not None:
            try:
                rows = get_rows()
            except Exception:
                rows = []
            valid = [r for r in rows if 0 <= r < len(self._action.frames)]
            if valid:
                return valid
        return [self._frame_index]

    def set_frame(self, idx: int):
        if self._action and 0 <= idx < len(self._action.frames):
            # 换帧时丢弃冻结布局：冻结里的"当前帧偏移增量"只对冻结时的帧有意义
            self._frozen_layout = None
            self.clear_view_anchor()      # 锚点绑定在原来那一帧上
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
        self.clear_view_anchor()      # 视图缩放语义变了，锚定没有意义
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
        self.clear_view_anchor()
        self._offset = QPointF(
            mouse_pos.x() - (mouse_pos.x() - self._offset.x()) * (new_zoom / old_zoom),
            mouse_pos.y() - (mouse_pos.y() - self._offset.y()) * (new_zoom / old_zoom),
        )
        self._zoom = new_zoom
        self._fit = False
        self.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Space:
            self.clear_view_anchor()
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

    def set_transform_mode(self, mode: str | None, owner: str | None = None) -> None:
        """进入/退出变换模式（'rotate'/'scale'/'skew'/'perspective'/'distort'/''/None）。

        owner 标记模式来源：'frame' = 右侧帧编辑面板，'all' = 底部动作编辑横条。
        两套按钮各自独立，退出（mode=None）时保持原 owner，用于让发起方弹回。
        """
        mode = mode or None
        if owner is not None:
            self._transform_owner = owner
        if mode is not None and mode == self._transform_mode:
            mode = None  # 再点同按钮 = 退出
        if mode is None:
            # 退出变换：锚点解除（平移已并进视图偏移，画面不会跳）
            self.clear_view_anchor()
        self._transform_mode = mode
        self._drag_kind = None
        self._drag_layout = None
        self._drag_whole = None
        if mode is None:
            self._transform_indices = []
        self._hover_kind, self._hover_index = None, -1
        self.setCursor(Qt.CursorShape.CrossCursor if self._transform_mode else Qt.CursorShape.ArrowCursor)
        self.update()
        self.transform_mode_changed.emit(self._transform_mode or "")

    def transform_mode(self) -> str | None:
        return self._transform_mode

    def set_transform_targets(self, indices) -> None:
        """设置本次变换作用的帧（动作编辑=全部帧；帧编辑=当前帧或 Ctrl 多选）。"""
        if not self._action:
            self._transform_indices = []
            return
        n = len(self._action.frames)
        self._transform_indices = sorted({int(i) for i in indices if 0 <= int(i) < n})

    def _whole_active(self) -> bool:
        """整体(GIF 级)变换：作用于 2 帧以上，手柄围绕整体包围盒、围绕共同中心。"""
        return bool(self._transform_mode) and len(self._transform_indices) > 1

    def _whole_frame(self, layout=None):
        """整体参考框（画布逻辑坐标）：(x0,y0,x1,y1,Cx,Cy,W,H) 或 None。

        W/H 取目标帧 base 尺寸（镜像旋转后、未加 quad）的最大者，中心取目标帧
        当前布局并集的中心；各帧内容在 W×H 参考框里居中（见 whole_reference）。
        """
        if not self._action or not self._transform_indices:
            return None
        layout = layout or self._current_layout()
        if layout is None:
            return None
        _, _, positions, _ = layout
        idxs = [i for i in self._transform_indices if 0 <= i < len(positions)]
        if not idxs:
            return None
        W, H, _entries = whole_reference(self._action, idxs)
        # 参考框的原点 = **代表帧 base 矩形**在画布上的左上角（= 代表帧落位 - 它在
        # 参考框里的居中偏移）。必须这么定，不能再用"各帧落位并集的中心"：
        # ① `_whole_current_ref` 是把代表帧的 quad 按 (ox,oy) 换算到参考框的，
        #    两者必须同源，否则手柄框和实际变换不是同一个矩形；
        # ② 并集中心会被"各帧裁剪尺寸的变化"带着跑 —— 拖动中裁剪尺寸一直在变，
        #    基准框乱飘，手柄就抓不住鼠标（实测偏 39px）。
        mi, mox, moy, _mw, _mh = self._whole_master_entry()[2]
        mtx, mty = positions[mi][0], positions[mi][1]
        x0, y0 = mtx - mox, mty - moy
        cx, cy = x0 + W / 2.0, y0 + H / 2.0
        return x0, y0, x0 + W, y0 + H, cx, cy, W, H

    def _whole_master_entry(self):
        """(W,H,entry)；entry 优先取占满参考框的帧，否则取面积最大帧，用于反推整体 quad。"""
        W, H, entries = whole_reference(self._action, self._transform_indices)
        full = [e for e in entries if e[3] == W and e[4] == H]
        return W, H, (full[0] if full else max(entries, key=lambda e: e[3] * e[4]))

    def _whole_current_ref(self):
        """从代表帧当前 quad 反推整体参考框的归一化 quad（让手柄贴合当前整体形状）。"""
        W, H, entry = self._whole_master_entry()
        _mi, ox, oy, w, h = entry
        q = self._action.frame_quads.get(_mi, DEFAULT_QUAD)
        ref = [0.0] * 8
        for k in range(4):
            ref[2 * k] = (ox + q[2 * k] * w) / W if W else 0.0
            ref[2 * k + 1] = (oy + q[2 * k + 1] * h) / H if H else 0.0
        return tuple(ref)

    def _whole_handles(self, layout=None) -> list[QPointF]:
        """整体变换的 4 个手柄（画布逻辑坐标）= 参考框经当前整体 quad 后的四角。"""
        fr = self._whole_frame(layout)
        if fr is None:
            return []
        x0, y0, _x1, _y1, _cx, _cy, W, H = fr
        ref = self._whole_current_ref()
        return [QPointF(x0 + ref[2 * k] * W, y0 + ref[2 * k + 1] * H) for k in range(4)]

    def _frame_pivot_canvas(self, i: int, layout=None) -> QPointF | None:
        """第 i 帧"当前 pivot"落在画布上的位置（与单帧路径同一个口径）。

        = 该帧的布局落位 + pivot 在该帧 pixmap 内的像素位置（pixmap 就是贴在这个
        落位上的）。整体模式下红点取代表帧的这个位置。
        """
        if not self._action or not (0 <= i < len(self._action.frames)):
            return None
        layout = layout or self._current_layout()
        if layout is None:
            return None
        _cw, _ch, positions, _meta = layout
        if not (0 <= i < len(positions)):
            return None
        tx, ty, _fw, _fh = positions[i]
        px, py = frame_pivot_in_pixmap(self._action, i)
        return QPointF(tx + px, ty + py)

    def _whole_live_dot_canvas(self, layout=None) -> QPointF | None:
        """整体模式下红点该画的位置 = 代表帧当前 pivot 的画布坐标。"""
        if not self._action or not self._transform_indices:
            return None
        try:
            master = self._whole_master_entry()[2][0]
        except (ValueError, TypeError, IndexError):
            master = self._transform_indices[0]
        return self._frame_pivot_canvas(master, layout)

    def _whole_anchor_canvas(self, layout=None) -> QPointF | None:
        """整体变换里红点该画的位置（= 代表帧当前 pivot 的画布坐标）。"""
        return self._whole_live_dot_canvas(layout)

    def _current_layout(self):
        if not self._action or not self._action.frames:
            return None
        return compute_preview_layout(self._action, self._frame_index)

    def _ui_layout(self):
        """绘制/命中/锚点统一用的布局。

        · 单帧路径：拖动期间用**快照**布局（那条路径靠 `_dragging_frame_rect` 的
          "quad 包围盒最小角位移"补偿对齐松手后的位置，已逐像素验证过）；
        · 整体(GIF 级/动作编辑)路径：一律用**实时**布局 —— 它就是
          `compute_preview_layout`，也就是松手后/播放的落位。拖动中与松手后
          用的是同一条公式、同一批尺寸，所以画面逐像素一致（用户要的"不闪"）。

        为什么不用快照 + 补偿：各帧内容不同 → 旋转/缩放后裁剪尺寸各不相同 →
        快照布局和松手后的布局差**每帧不一样**，那正是"其它帧位置变了又回去"。
        """
        if self._drag_kind is not None and not self._whole_active():
            return self._drag_layout
        return self._paint_layout()

    def _quad_dst(self, quad):
        """归一化 quad → (相对旋转后帧原点的 4 角像素点, 基准宽, 基准高)。"""
        w, h = frame_base_size(self._action, self._frame_index)
        return ([QPointF(quad[0] * w, quad[1] * h), QPointF(quad[2] * w, quad[3] * h),
                 QPointF(quad[4] * w, quad[5] * h), QPointF(quad[6] * w, quad[7] * h)], w, h)

    def _rotation_pivot_output(self) -> QPointF:

        """旋转中心在"旋转/镜像后、未应用 quad"的基础图像中的坐标。

        必须与 ``transform_frame_pixmap`` / ``frame_base_size`` **逐像素同管线**，
        否则红色锚点画的位置和实际旋转围绕的点不一致，旋转/缩放时就会"歪、闪"。

        两处历史坑：
        ① 无旋转无镜像时 ``transform_frame_pixmap`` 的 ``transformed=False``，
           **不裁剪**透明边；这里若额外裁剪，pivot 会偏出一圈透明边（约 2px）。
        ② 任意角度旋转时，``QPixmap.transformed`` 输出图的平移量是
           ``-floor(映射外接矩形左上)``（整数），不是理论浮点 ``-min_pt``；
           用浮点 min_pt 推算 pivot 会再偏 0.5~1.7px。这里直接复刻 Qt 的 floor
           平移，并使用与渲染一致的 Fast/Smooth 模式求 alpha 包围盒。
        """
        if not self._action or not self._action.frames:
            return QPointF(0, 0)
        px, py = frame_pivot_output(self._action, self._frame_index)
        return QPointF(px, py)

    def _whole_pivot_ref(self) -> tuple[float, float]:
        """整体变换的旋转中心在参考框里的归一化位置（由代表帧的 pivot 反推）。

        各帧的 pivot 是"同一个视觉点在各帧自己的原图坐标"，代表帧填满参考框，所以
        取代表帧的 pivot 换算到参考框即可；没有设置过时就是 (0.5, 0.5)（共同中心）。
        """
        if not self._action or not self._transform_indices:
            return DEFAULT_ROTATION_PIVOT
        try:
            W, H, entry = self._whole_master_entry()
        except (ValueError, TypeError):
            return DEFAULT_ROTATION_PIVOT
        mi, ox, oy, w, h = entry
        if W <= 0 or H <= 0 or w <= 0 or h <= 0:
            return DEFAULT_ROTATION_PIVOT
        pv = self._action.frame_rotation_pivots.get(mi, DEFAULT_ROTATION_PIVOT)
        px, py = pv[0] * w, pv[1] * h
        quad = self._action.frame_quads.get(mi, DEFAULT_QUAD)
        if tuple(quad) != DEFAULT_QUAD:
            src = QPolygonF([QPointF(0, 0), QPointF(w, 0), QPointF(w, h), QPointF(0, h)])
            dst = QPolygonF([QPointF(quad[0] * w, quad[1] * h), QPointF(quad[2] * w, quad[3] * h),
                             QPointF(quad[4] * w, quad[5] * h), QPointF(quad[6] * w, quad[7] * h)])
            m = QTransform()
            if QTransform.quadToQuad(src, dst, m):
                pt = m.map(QPointF(px, py))
                px, py = pt.x(), pt.y()
        return ((ox + px) / W, (oy + py) / H)

    def _rotation_pivot_canvas(self, layout=None, mo=None) -> QPointF:
        if not self._action or not self._action.frames:
            return QPointF(0, 0)
        layout = layout or self._current_layout()
        if layout is None:
            return QPointF(0, 0)
        if self._whole_active():
            pt = self._whole_anchor_canvas(layout)
            return pt if pt is not None else QPointF(0, 0)
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
        if self._whole_active():
            return QPointF(0, 0)
        quad = self._action.frame_quads.get(self._frame_index, DEFAULT_QUAD)
        dst, _, _ = self._quad_dst(quad)
        return QPointF(min(p.x() for p in dst), min(p.y() for p in dst))

    def _handles_canvas(self, mo: QPointF, layout=None, anchor_override=None) -> list[QPointF]:
        """quad 四角 → 画布逻辑坐标（layout 传 None 用当前布局，或传拖动锁定的快照）。"""
        if self._whole_active():
            return self._whole_handles(layout)
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

    # ---- 逻辑坐标 ↔ 屏幕坐标（含视图锚定平移）----
    def _to_screen_raw(self, pt: QPointF) -> QPointF:
        """不含视图锚定平移的屏幕坐标（锚点计算用，避免自我引用）。"""
        return QPointF(self._offset.x() + pt.x() * self._zoom,
                       self._offset.y() + pt.y() * self._zoom)

    def _logical_to_screen(self, pt: QPointF) -> QPointF:
        return QPointF(self._to_screen_raw(pt).x() + self._view_shift.x(),
                       self._to_screen_raw(pt).y() + self._view_shift.y())

    def _screen_to_logical(self, pt: QPointF) -> QPointF:
        return QPointF((pt.x() - self._offset.x() - self._view_shift.x()) / self._zoom,
                       (pt.y() - self._offset.y() - self._view_shift.y()) / self._zoom)

    # ---- 视图锚定：本次变换的「不动点」 ----
    def _scale_uses_pivot(self, kind: str) -> bool:
        """缩放以谁为不动点：默认 = 红色锚点（Aseprite/PS 的参考点），Alt = 老行为对角固定。"""
        if kind != "corner" or self._transform_mode != "scale":
            return False
        mods = QApplication.keyboardModifiers()
        return not bool(mods & Qt.KeyboardModifier.AltModifier)

    def _anchor_spec_for_drag(self, kind: str, index: int):
        """拖动期间应该钉住不动的那个点。

        · 旋转/整体变换：旋转中心（整体=共同中心）；
        · 拖角（缩放/透视/扭曲）：对角那个角是几何不动的，钉它；
        · 斜切（拖边）：对边中点在居中剪切时……非居中时对边不动，钉对边中点；
        · 拖红点本身：钉内容包围盒中心 —— 只有红点动，图像和手柄框都不动。
        """
        if kind == "pivot":
            # 拖红点：图像和手柄框原地不动，只有红点在动
            return ("frac", 0.5, 0.5)
        if kind == "rotate" or self._transform_mode == "rotate":
            # 旋转：钉住旋转中心（整体 GIF 级也是那个点，红点可偏心）；
            # 旋转模式下拖角点也是旋转，同样钉红点。
            return ("pivot",)
        if kind == "corner" and self._drag_use_pivot:
            # 以红色锚点为不动点缩放：屏幕上也把它钉住（否则画布重排会让它漂）
            return ("pivot",)
        if self._whole_active():
            # 整体斜切：钉目标帧共同中心
            return ("frac", 0.5, 0.5)
        k = int(index) if int(index) >= 0 else 0
        if kind == "corner":
            a = (k + 2) % 4
            return ("frac", 1.0 if a in (1, 2) else 0.0, 1.0 if a in (2, 3) else 0.0)
        if kind == "edge":
            fx, fy = {(0,): (0.5, 1.0), (1,): (0.0, 0.5),
                      (2,): (0.5, 0.0), (3,): (1.0, 0.5)}.get((k,), (0.5, 0.5))
            return ("frac", fx, fy)
        return ("frac", 0.5, 0.5)

    def _anchor_raw_canvas(self, spec=None) -> QPointF | None:
        """锚点在没有视图平移时的画布逻辑坐标（拖动中与绘制几何同源）。"""
        spec = self._anchor_spec if spec is None else spec
        if spec is None or not self._action:
            return None
        layout = self._ui_layout()
        mo = self._drag_mo if self._drag_kind else self._current_mo()
        if spec[0] == "pivot":
            return self._rotation_pivot_canvas(layout, mo)
        pts = self._handles_canvas(mo, layout=layout)
        if not pts:
            return None
        xs = [p.x() for p in pts]
        ys = [p.y() for p in pts]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        return QPointF(x0 + (x1 - x0) * spec[1], y0 + (y1 - y0) * spec[2])

    def _sync_view_shift(self) -> None:
        """重算视图平移，让锚点落回它被钉住的屏幕位置。"""
        if self._anchor_spec is None or self._anchor_screen is None:
            self._view_shift = QPointF(0.0, 0.0)
            return
        raw = self._anchor_raw_canvas()
        if raw is None:
            self._view_shift = QPointF(0.0, 0.0)
            return
        self._view_shift = QPointF(
            self._anchor_screen.x() - self._to_screen_raw(raw).x(),
            self._anchor_screen.y() - self._to_screen_raw(raw).y(),
        )

    def _set_view_anchor(self, spec) -> None:
        """开始一次变换交互：记住不动点当前的屏幕位置。"""
        self.clear_view_anchor()
        if spec is None:
            return
        raw = self._anchor_raw_canvas(spec)
        if raw is None:
            return
        self._anchor_spec = tuple(spec)
        self._anchor_screen = self._to_screen_raw(raw)

    def _bake_view_shift(self) -> None:
        """把当前平移并进视图偏移：之后即使解除锚定也不会跳。"""
        if self._view_shift.x() or self._view_shift.y():
            self._offset = QPointF(self._offset.x() + self._view_shift.x(),
                                   self._offset.y() + self._view_shift.y())
        self._view_shift = QPointF(0.0, 0.0)

    def clear_view_anchor(self) -> None:
        """解除视图锚定（换帧/换动作/退出变换/缩放平移时调用）。"""
        self._bake_view_shift()
        self._anchor_spec = None
        self._anchor_screen = None

    def view_anchored(self) -> bool:
        return self._anchor_spec is not None

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

    def _hit_whole_body(self, screen_pos) -> bool:
        """整体变换模式下，屏幕点是否落在整体四边形内部（用于框内拖动=整体平移）。"""
        if not self._whole_active():
            return False
        pts = self._whole_handles()
        if len(pts) < 4:
            return False
        lp = self._screen_to_logical(screen_pos)
        return QPolygonF(pts).containsPoint(lp, Qt.FillRule.OddEvenFill)

    def _hit_inside_box(self, screen_pos, margin: float = 26.0) -> bool:
        """屏幕点是否落在"变换 UI 区域"内（控制框 + 手柄外一圈）。

        用途：判断"这一点是不是框外空白"。手柄方块是**跨在框线上**画的（一半在框外），
        用户瞄准角点时经常落在框外那一半甚至再往外几像素 —— 这种点绝不能被当成
        "点空白退出"，否则就是"点手柄反而把手柄弹掉了"。所以这里除了框内，
        还把框外 26px 也算进来。
        """
        pts = self._handles_canvas(
            self._drag_mo if self._drag_kind else self._current_mo(),
            layout=self._ui_layout(),
        )
        if len(pts) < 4:
            return False
        lp = self._screen_to_logical(screen_pos)
        poly = QPolygonF(pts)
        if poly.containsPoint(lp, Qt.FillRule.OddEvenFill):
            return True
        m = margin / max(0.01, self._zoom)
        return poly.boundingRect().adjusted(-m, -m, m, m).contains(lp)

    def _hit_test(self, screen_pos):
        """返回 (kind, index)，只让当前模式的玫红控件可命中。

        命中半径用**圆**而不是曼哈顿菱形：菱形在斜方向只有半径的 1/√2，
        正好是"瞄准角点往斜外偏一点就点不到"的元凶。
        """
        if not self._transform_mode or not self._action or not self._action.frames:
            return None, -1
        self._sync_view_shift()      # 命中判定必须和上一帧画出来的位置一致
        layout = self._ui_layout()
        mo = self._drag_mo if self._drag_kind else self._current_mo()
        pts = self._handles_canvas(mo, layout=layout)
        if not pts:
            return None, -1
        screens = [self._logical_to_screen(p) for p in pts]
        mode = self._transform_mode
        near = lambda a, b, r: math.hypot(a.x() - b.x(), a.y() - b.y()) < r
        # 旋转中心（红色十字）：所有变换模式都显示、都可拖（含整体 GIF 级变换）。
        # 整体模式下框内拖动=整体平移，所以红点的"抓手"收窄到 12px，把中间一小块
        # 以外的区域都留给平移；单帧模式没有这个冲突，保持宽松的 16px。
        pivot = self._rotation_pivot_canvas(layout, mo)
        pivot_s = self._logical_to_screen(pivot)
        if near(pivot_s, screen_pos, 12.0 if self._whole_active() else 16.0):
            return "pivot", -1
        if mode == "rotate":
            rot = self._rotate_handle_screen(pts, pivot)
            if rot is not None and near(rot, screen_pos, 20.0):
                return "rotate", -1
            # 四角也接受：旋转模式下拖角点同样旋转（PS 里在框外/角上拖就是旋转）。
            # 以前这里只认圆柄，角点画成空心、点了没反应，用户以为"手柄拖不动"。
            for i, s in enumerate(screens):
                if near(s, screen_pos, 20.0):
                    return "corner", i
            return None, -1
        if mode in ("scale", "perspective", "distort"):
            for i, s in enumerate(screens):
                if near(s, screen_pos, 20.0):
                    return "corner", i
        elif mode == "skew":
            for i in range(4):
                mid = (screens[i] + screens[(i + 1) % 4]) / 2.0
                if near(mid, screen_pos, 18.0):
                    return "edge", i
        return None, -1

    def _begin_drag(self, kind: str, index: int, screen_pos: QPointF) -> None:
        self.transform_edit_started.emit()
        self._drag_kind = kind
        self._drag_index = index
        self._drag_layout = self._current_layout()
        self._axis_lock = None          # Shift 锁轴的状态，每次拖拽重新判定
        # 本次交互的不动点：拖动期间钉死在屏幕上，动的只有灰色画布框。
        self._drag_use_pivot = self._scale_uses_pivot(kind)
        self._set_view_anchor(self._anchor_spec_for_drag(kind, index))
        if self._whole_active():
            # 整体(GIF 级)变换：手柄/基准全部围绕目标帧整体参考框
            self._rebase_whole_drag()
            _W, _H, wentry = self._whole_master_entry()
            master = wentry[0]
            self._drag_orig = tuple(self._action.frame_quads.get(master, DEFAULT_QUAD))
            self._drag_rot_start = self._action.frame_rotations.get(master, 0)
        else:
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
        # 本次拖动允许的最大放大倍数（像素预算，防止拖出窗口后把 pixmap 撑爆）
        self._drag_ratio_limit = transform_ratio_limit(self._drag_wrot, self._drag_hrot)
        if kind == "rotate" or self._transform_mode == "rotate":
            # 角度一律在**屏幕坐标**里量：锚定把旋转中心钉在固定屏幕点，
            # 用逻辑坐标会因为视图平移而变化，形成正反馈。
            self._drag_pivot_screen = self._logical_to_screen(self._drag_pivot_canvas)
            self._drag_rot_mouse_start = math.degrees(math.atan2(
                screen_pos.y() - self._drag_pivot_screen.y(),
                screen_pos.x() - self._drag_pivot_screen.x(),
            ))

    def _rebase_whole_drag(self) -> None:
        """整体变换：把拖动的几何基准刷成**当前实时几何**（每次鼠标移动都刷）。

        整体模式的落位走实时布局（= 松手后/播放的落位，见 `_ui_layout`），而布局
        会随各帧尺寸变化微动。基准框如果不跟着刷，手柄就会和鼠标差出一截
        （实测 10~35px，"手柄跟不上/点不到"）。每步都从当前状态重新取基准，
        手柄就始终贴在鼠标底下；因为每一步的尺寸变化很小，这一步引入的残差是
        二阶小量（亚像素），不会来回抖。
        """
        layout = self._paint_layout()
        fr = self._whole_frame(layout) if layout is not None else None
        if fr is None:
            return
        x0, y0, _x1, _y1, cx, cy, W, H = fr
        self._drag_layout = layout
        self._drag_whole = (x0, y0, W, H)
        self._drag_wrot, self._drag_hrot = W, H
        self._drag_mo = QPointF(0, 0)
        self._drag_orig_c = [QPointF(p) for p in self._whole_handles(layout)]
        self._drag_center = QPointF(cx, cy)
        # 整体模式的"不动点"= 各帧旋转中心重合的那个点（红点），不是参考框中心
        self._drag_pivot_canvas = self._whole_anchor_canvas(layout) or QPointF(cx, cy)

    def _pivot_edit_index(self) -> int:
        """当前"可拖的红点"属于哪一帧：整体模式是代表帧，否则是当前帧。"""
        if self._whole_active():
            try:
                return self._whole_master_entry()[2][0]
            except (ValueError, TypeError, IndexError):
                return self._transform_indices[0] if self._transform_indices else self._frame_index
        return self._frame_index

    def _quad_dst_for(self, idx: int, quad):
        """任意帧的 (quad 四角像素点, 基准宽, 基准高)。"""
        w, h = frame_base_size(self._action, idx)
        return ([QPointF(quad[0] * w, quad[1] * h), QPointF(quad[2] * w, quad[3] * h),
                 QPointF(quad[4] * w, quad[5] * h), QPointF(quad[6] * w, quad[7] * h)], w, h)

    def _frame_mo_for(self, idx: int) -> QPointF:
        """任意帧当前 quad 的包围盒最小角（pixmap 左上角对应的基准点）。

        `_emit_pivot_from_canvas` 的逆映射要用它：红点画在
        ``布局 + (pivot 绝对位置 - 包围盒最小角)``，反解时要加回这个最小角。
        """
        quad = self._action.frame_quads.get(idx, DEFAULT_QUAD)
        dst, _, _ = self._quad_dst_for(idx, quad)
        return QPointF(min(p.x() for p in dst), min(p.y() for p in dst))

    def _emit_pivot_from_canvas(self, canvas_point: QPointF) -> None:
        """把画布上的 pivot 点反算回原图归一化坐标（``_rotation_pivot_output`` 的逆）。

        必须与正向严格互逆（同样的镜像/旋转顺序、Qt floor 平移、alpha 裁剪口径），
        否则拖动红色锚点时手到点不到、松手锚点回弹。
        """
        if not self._action or not self._action.frames:
            return
        # 整体模式下红点画在"代表帧"上，所以逆映射也按代表帧的几何算；
        # 写回时同一个归一化 pivot 会发给所有目标帧（同一视觉部位，各帧一致）。
        idx = self._pivot_edit_index()
        layout = self._ui_layout()
        if layout is None:
            return
        _, _, positions, _ = layout
        if not (0 <= idx < len(positions)):
            return
        tx, ty, _, _ = positions[idx]
        quad = self._action.frame_quads.get(idx, DEFAULT_QUAD)
        dst, w, h = self._quad_dst_for(idx, quad)
        mo = self._frame_mo_for(idx)
        point = QPointF(canvas_point.x() - tx + mo.x(),
                        canvas_point.y() - ty + mo.y())
        if tuple(quad) != DEFAULT_QUAD:
            src_poly = QPolygonF([QPointF(0, 0), QPointF(w, 0), QPointF(w, h), QPointF(0, h)])
            qm = QTransform()
            if not QTransform.quadToQuad(src_poly, QPolygonF(dst), qm) or not qm.isInvertible():
                return
            point = qm.inverted()[0].map(point)
        # 至此 point 在"旋转/镜像裁剪后、未应用 quad"的坐标系
        src = self._action.frames[idx]
        sw, sh = src.width(), src.height()
        angle = int(self._action.frame_rotations.get(idx, 0)) % 360
        mh = idx in self._action.frame_mirror_h
        mv = idx in self._action.frame_mirror_v
        transformed = bool(mh or mv or angle)
        current = self._action.frame_rotation_pivots.get(idx, DEFAULT_ROTATION_PIVOT)

        pm = src
        if mh:
            pm = pm.transformed(QTransform().scale(-1.0, 1.0), Qt.TransformationMode.FastTransformation)
        if mv:
            pm = pm.transformed(QTransform().scale(1.0, -1.0), Qt.TransformationMode.FastTransformation)
        matrix = None
        ox = oy = 0.0
        if angle:
            mode = (Qt.TransformationMode.FastTransformation if angle % 90 == 0
                    else Qt.TransformationMode.SmoothTransformation)
            matrix, _pp, _mp = _rotation_transform(sw, sh, angle, current)
            corners = [matrix.map(QPointF(0, 0)), matrix.map(QPointF(sw, 0)),
                       matrix.map(QPointF(sw, sh)), matrix.map(QPointF(0, sh))]
            br = QPolygonF(corners).boundingRect()
            ox, oy = math.floor(br.left()), math.floor(br.top())
            pm = pm.transformed(matrix, mode)
        if transformed:
            bounds = _alpha_bounds(pm)
            if bounds is not None:
                point = QPointF(point.x() + bounds.x(), point.y() + bounds.y())
        if angle and matrix is not None:
            point = QPointF(point.x() + ox, point.y() + oy)
            if matrix.isInvertible():
                point = matrix.inverted()[0].map(point)
        pivot = (max(-2.0, min(3.0, point.x() / max(1, sw))),
                 max(-2.0, min(3.0, point.y() / max(1, sh))))
        self.transform_pivot_moved.emit(tuple(round(v, 6) for v in pivot))

    # 手柄跟手校正的上限（画布像素）：这一步只该抵消"视图锚定"带来的几像素漂移。
    # 超过这个量说明**几何本身在跳**（典型：帧从"无变换=不裁剪"变成"有变换=裁剪"，
    # 落位中心跟着变），那是落位口径的正常变化，绝不能拿变换去硬补 —— 否则整个
    # GIF 会被平移几十像素、缩放中心也跑掉（实测 76px，测试里表现为 quad 不再居中）。
    NUDGE_LIMIT = 12.0

    def _nudge_whole_shift(self, want: list[QPointF], shift_before: QPointF) -> None:
        """整体模式：补偿"视图锚定"带来的手柄漂移（一步预测-校正）。

        拖动时鼠标的屏幕点是用**上一帧的** ``_view_shift`` 换算成画布坐标的，而这一帧
        重画时 ``_view_shift`` 会因为"把红点钉在屏幕上"而变一点点 —— 于是手柄框整体
        落后鼠标 4~6px（手感上就是"手柄有点脱手"）。
        这里先按新几何把 ``_view_shift`` 算出来，用差值把参考框整体平移回去；
        校正后再画时残差是二阶小量（亚像素），不会来回抖。
        """
        self._sync_view_shift()
        dx = (shift_before.x() - self._view_shift.x()) / max(0.01, self._zoom)
        dy = (shift_before.y() - self._view_shift.y()) / max(0.01, self._zoom)
        if abs(dx) < 0.5 and abs(dy) < 0.5:
            return
        if math.hypot(dx, dy) > self.NUDGE_LIMIT:
            # 几何在跳（不是锚定漂移）：交给落位自己走，不拿变换去补
            return
        self._emit_quad_from_canvas([QPointF(p.x() + dx, p.y() + dy) for p in want])

    def _emit_quad_from_canvas(self, canvas_pts, nudge_shift=None) -> None:
        """把 4 个画布逻辑角点换算成归一化 quad 并发出信号。

        nudge_shift 不为 None 时（整体模式拖动角点/边点）：发出后按视图锚定的
        变化量做一次平移校正，保证手柄真的贴在鼠标底下（见 `_nudge_whole_shift`）。
        """
        if self._whole_active():
            x0, y0, W, H = self._drag_whole
            quad = []
            for pt in canvas_pts:
                quad.append((pt.x() - x0) / W if W else 0.0)
                quad.append((pt.y() - y0) / H if H else 0.0)
            self.transform_quadded.emit(tuple(round(v, 6) for v in quad))
            if nudge_shift is not None:
                self._nudge_whole_shift(list(canvas_pts), nudge_shift)
            return
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

    def _locked_delta(self, dx: float, dy: float) -> tuple[float, float]:
        """Shift 锁轴：只保留一个方向的分量（PS 那个手感）。

        连续判断 + 滞回：
          · 一开始按位移大的轴锁定；
          · 之后要换轴，另一轴必须**超出 4px** 才切 —— 否则鼠标在 45° 附近来回时
            会一会儿锁横一会儿锁竖，看起来像卡顿。
        """
        if self._axis_lock is None:
            self._axis_lock = "x" if abs(dx) >= abs(dy) else "y"
        elif self._axis_lock == "x":
            if abs(dy) > abs(dx) + self.AXIS_LOCK_HYSTERESIS:
                self._axis_lock = "y"
        else:
            if abs(dx) > abs(dy) + self.AXIS_LOCK_HYSTERESIS:
                self._axis_lock = "x"
        return (dx, 0.0) if self._axis_lock == "x" else (0.0, dy)

    def _update_drag(self, screen_pos: QPointF, modifiers=None) -> None:
        shift = bool((modifiers or Qt.KeyboardModifier.NoModifier)
                     & Qt.KeyboardModifier.ShiftModifier)
        mode = self._transform_mode
        if self._whole_active():
            # 整体模式落位走实时布局 → 基准框每步都可能微动，先刷新基准再算，
            # 手柄才贴在鼠标底下（不刷新就会差出十几到几十像素）。
            self._rebase_whole_drag()
        # 红点（旋转中心）在任何变换模式下都能拖，整体(GIF 级)变换按参考框分发到各帧。
        if self._drag_kind == "pivot":
            cur = self._screen_to_logical(screen_pos)
            self._drag_pivot_canvas = cur
            # 整体(GIF 级)变换下红点画在**代表帧**上，逆映射同样按代表帧算，
            # 写回时同一个归一化 pivot 发给所有目标帧（同一视觉部位）。
            self._emit_pivot_from_canvas(cur)
            return
        if mode == "rotate":
            if self._drag_kind in ("rotate", "corner"):
                # 旋转模式下圆柄和角点都是"拖着转"（PS 手感）
                piv = self._drag_pivot_screen
                current_mouse_angle = math.degrees(math.atan2(
                    screen_pos.y() - piv.y(), screen_pos.x() - piv.x()))
                delta = (current_mouse_angle - self._drag_rot_mouse_start + 180.0) % 360.0 - 180.0
                angle = (self._drag_rot_start + int(round(delta))) % 360
                if shift:
                    # Shift = 吸附到 15° 的整数倍（PS 一样）
                    angle = int(round(angle / self.ROTATE_SNAP_DEGREES)) * self.ROTATE_SNAP_DEGREES % 360
                self.transform_rotated.emit(angle)
            return
        cur = self._screen_to_logical(screen_pos)
        kind = self._drag_kind
        orig_c = self._drag_orig_c
        # 尺寸兜底：鼠标划出窗口很远时会把 quad 放大成几万像素（每帧几百 MB），
        # 这里把位移/倍数夹在预算内 —— 手柄到位即停，不会再把程序拖死。
        span_limit = self._drag_ratio_limit * max(self._drag_wrot, self._drag_hrot)
        if kind == "corner":
            k = self._drag_index
            if mode == "distort":
                dx = cur.x() - orig_c[k].x()
                dy = cur.y() - orig_c[k].y()
                if shift:
                    dx, dy = self._locked_delta(dx, dy)
                dx = max(-span_limit, min(span_limit, dx))
                dy = max(-span_limit, min(span_limit, dy))
                new = [QPointF(p.x(), p.y()) for p in orig_c]
                new[k] = QPointF(orig_c[k].x() + dx, orig_c[k].y() + dy)
            elif mode == "perspective":
                dx = cur.x() - orig_c[k].x()
                dy = cur.y() - orig_c[k].y()
                if shift:
                    dx, dy = self._locked_delta(dx, dy)
                dx = max(-span_limit, min(span_limit, dx))
                dy = max(-span_limit, min(span_limit, dy))
                new = [QPointF(p.x(), p.y()) for p in orig_c]
                # 透视：本角跟鼠标走，**两条相邻边的另一端朝相反方向动**（对称收放），
                # 对角不动 —— 这样两条邻边一起收/放，形状才会汇聚成梯形。
                #
                # 两处都踩过坑，别看错：
                # ① 邻角要按**边的方向**挑，不能写死 (k+1)→X、(k-1)→Y。源矩形里
                #    (0,1)(2,3) 是水平边、(1,2)(3,0) 是垂直边，所以 k=1/3 时两个邻角
                #    是反过来的；写死会让右上/左下沿错轴移动。
                # ② 邻角的位移必须是 **-d**（相反方向）。同向位移在几何上恒等于
                #    "两对对边都保持平行" = 平行四边形 = 仿射变换，看着就是斜切/平移，
                #    完全不是透视（用户报的正是"斜切和透视差不多、透视不对"）。
                #    相反方向才会让对边不再平行 —— 这就是梯形（透视）的判据。
                nxt, prv = (k + 1) % 4, (k - 1) % 4
                if (min(k, nxt), max(k, nxt)) in _HORIZONTAL_EDGES:
                    horizontal_neighbor, vertical_neighbor = nxt, prv
                else:
                    horizontal_neighbor, vertical_neighbor = prv, nxt
                new[k].setX(orig_c[k].x() + dx)
                new[k].setY(orig_c[k].y() + dy)
                new[horizontal_neighbor].setX(orig_c[horizontal_neighbor].x() - dx)
                new[vertical_neighbor].setY(orig_c[vertical_neighbor].y() - dy)
            elif mode == "scale":
                if self._drag_use_pivot:
                    # 不动点 = 红色锚点（Aseprite / PS 参考点）：四角一起按比例走，
                    # 锚点原地不动 —— 用户要的"缩放按锚点位置来"。
                    anchor = QPointF(self._drag_pivot_canvas)
                elif self._whole_active():
                    # Alt（老行为）下的整体缩放：围绕整体中心，多帧才保持重叠
                    anchor = QPointF(sum(p.x() for p in orig_c) / 4.0,
                                     sum(p.y() for p in orig_c) / 4.0)
                else:
                    anchor = orig_c[(k + 2) % 4]
                den_x = orig_c[k].x() - anchor.x()
                den_y = orig_c[k].y() - anchor.y()
                if abs(den_x) < 1e-6 or abs(den_y) < 1e-6:
                    return
                rx = (cur.x() - anchor.x()) / den_x
                ry = (cur.y() - anchor.y()) / den_y
                if shift:
                    # Shift = 等比缩放（保持宽高比）：取变化更大的那个轴的比例，
                    # 两个轴都用它 —— 这样形状不会被拉扁。
                    r = rx if abs(rx - 1.0) >= abs(ry - 1.0) else ry
                    rx = ry = r
                lo, hi = 1.0 / self._drag_ratio_limit, self._drag_ratio_limit
                rx = max(lo, min(hi, rx))
                ry = max(lo, min(hi, ry))
                new = [QPointF(anchor.x() + (p.x() - anchor.x()) * rx,
                               anchor.y() + (p.y() - anchor.y()) * ry) for p in orig_c]
            else:
                return
            self._emit_quad_from_canvas(new, QPointF(self._view_shift))
        elif kind == "edge" and mode == "skew":
            k = self._drag_index
            a, b = k, (k + 1) % 4
            new = [QPointF(p.x(), p.y()) for p in orig_c]
            if k in (0, 2):  # 上/底边：沿 X 斜切
                dx = cur.x() - (orig_c[a].x() + orig_c[b].x()) / 2.0
                dx = max(-span_limit, min(span_limit, dx))
                new[a].setX(orig_c[a].x() + dx)
                new[b].setX(orig_c[b].x() + dx)
                if shift or self._whole_active():
                    # Shift（或整体变换）= 居中剪切：对边朝相反方向等量移动，中心不动
                    # （不然光拖一条边，图形会整体往上/下跑）。
                    for other in ((k + 2) % 4, (k + 3) % 4):
                        new[other].setX(orig_c[other].x() - dx)
            else:            # 左/右边：沿 Y 斜切
                dy = cur.y() - (orig_c[a].y() + orig_c[b].y()) / 2.0
                dy = max(-span_limit, min(span_limit, dy))
                new[a].setY(orig_c[a].y() + dy)
                new[b].setY(orig_c[b].y() + dy)
                if shift or self._whole_active():
                    for other in ((k + 2) % 4, (k + 3) % 4):
                        new[other].setY(orig_c[other].y() - dy)
            self._emit_quad_from_canvas(new, QPointF(self._view_shift))

    def _end_drag(self) -> None:
        had_drag = self._drag_kind is not None
        self._drag_kind = None
        self._drag_layout = None
        self._drag_use_pivot = False
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

    def _hit_current_frame(self, pos: QPointF) -> bool:
        """判断点击位置是否在当前帧矩形内（参照帧模式下用于启动拖动）。"""
        if not self._action or not self._action.frames:
            return False
        if not (0 <= self._frame_index < len(self._action.frames)):
            return False
        _, _, positions, _ = self._paint_layout()
        tx, ty, fw, fh = positions[self._frame_index]
        scale = max(0.01, self._zoom)
        cx, cy = self._offset.x(), self._offset.y()
        rect = QRectF(cx + tx * scale, cy + ty * scale, fw * scale, fh * scale)
        return rect.contains(pos)

    def mousePressEvent(self, event):
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        if self._panning and event.button() == Qt.MouseButton.LeftButton:
            self._pan_start = event.position() - self._offset
            return
        # 没有动作时画布上只有"选择动作以预览"几个字，点它是没有任何反应的；
        # 直接给出原因（用户要求：点图片或控件要提示先添加动作）。
        if (not self._action or not self._action.frames) and event.button() == Qt.MouseButton.LeftButton:
            self.hint_no_action()
            return
        # 参照帧模式：点击当前帧可直接拖动记录帧偏移
        if (self._action and self._action.ref_frame >= 0
                and not self._transform_mode and not self._panning
                and event.button() == Qt.MouseButton.LeftButton):
            if self._hit_current_frame(event.position()):
                self.begin_layout_freeze()
                self._frame_drag_active = True
                self._frame_drag_last = event.position()
                self.setCursor(Qt.CursorShape.SizeAllCursor)
                return
        if self._transform_mode and event.button() == Qt.MouseButton.LeftButton:
            kind, idx = self._hit_test(event.position())
            if kind is None:
                if self._whole_active() and self._hit_whole_body(event.position()):
                    # 整体(GIF 级)平移：在整体框内部拖动，所有目标帧一起移动
                    self.begin_layout_freeze(list(self._transform_indices))
                    self._frame_drag_active = True
                    self._frame_drag_whole = True
                    self._frame_drag_last = event.position()
                    self.setCursor(Qt.CursorShape.SizeAllCursor)
                elif self._hit_inside_box(event.position()):
                    # 框内但没点到可拖控件（典型：旋转模式下点四个空心角点）：
                    # 什么都不做，**但不退出** —— 退出条件是"点框外空白"。
                    # 以前这里会直接退出，用户点了手柄看着像被弹起。
                    return
                else:
                    self.set_transform_mode(None)  # 点框外空白退出
            else:
                self._begin_drag(kind, idx, event.position())

    def mouseMoveEvent(self, event):
        if self._panning and self._pan_start is not None:
            self._offset = event.position() - self._pan_start
            self.update()
            return
        # 参照帧模式下拖动当前帧：屏幕增量 / 缩放 = 逻辑增量
        if self._frame_drag_active and self._frame_drag_last is not None:
            delta = event.position() - self._frame_drag_last
            self._frame_drag_last = event.position()
            scale = max(0.01, self._zoom)
            dx = int(round(delta.x() / scale))
            dy = int(round(delta.y() / scale))
            if dx or dy:
                # 整体平移作用于全部目标帧；否则 Ctrl 多选时移动所有选中帧
                if self._frame_drag_whole:
                    indices = list(self._transform_indices)
                else:
                    indices = self._drag_target_indices()
                for idx in indices:
                    cur = self._action.frame_offsets.get(idx, (0, 0))
                    nx, ny = cur[0] + dx, cur[1] + dy
                    if nx == 0 and ny == 0:
                        self._action.frame_offsets.pop(idx, None)
                    else:
                        self._action.frame_offsets[idx] = (nx, ny)
                main = self._frame_index
                mnx, mny = self._action.frame_offsets.get(main, (0, 0))
                self.frame_offset_dragged.emit(main, mnx, mny)
                self.update()
            return
        if self._drag_kind and self._transform_mode:
            # 修饰键（Shift 锁轴 / 等比 / 15° 吸附）从每个移动事件里读，
            # 松手前按住或松开 Shift 会在下一次移动时生效（PS 也是这样）。
            self._last_drag_screen = event.position()
            self._update_drag(event.position(), event.modifiers())
        elif self._transform_mode:
            self._update_hover(event.position())

    def mouseReleaseEvent(self, event):
        if self._panning:
            self._pan_start = None
        if self._frame_drag_active:
            self._frame_drag_active = False
            self._frame_drag_last = None
            self._frame_drag_whole = False
            self.end_layout_freeze()
            self.unsetCursor()
        if self._drag_kind:
            self._end_drag()

    def _paint_whole_frames(self, p: QPainter, cx: float, cy: float, scale: float, layout=None):
        """整体(GIF 级)变换：把所有目标帧叠合绘制。

        主帧不透明、其余半透明（看出多帧是否重合）。

        落位口径只有一个：**和播放/帧编辑完全一样** —— 每帧按 compute_preview_layout
        的位置画（含它自己的 frame_offsets 帧偏移）。也就是 "动作编辑 = 单帧模式全选
        所有帧"：你看到的相对位置就是导出/GIF 里的相对位置。

        三个历史坑（都已修）：
        · 以前按"整体参考框居中"叠合，直接把各帧的 frame_offsets 丢掉了 ——
          测过：播放里四帧内容中心 y=64/58/66/52，整体模式却全被压到 59，
          用户看到的就是"坐标不统一、有上有下"；
        · 以前拖动用 fast(不裁剪) 图、松手用精确图，两套口径不一致 —— 实测其它帧
          在拖动中偏出 12.5px、松手又归 0，就是"其它帧位置变了又回去"；
        · 落位与 pivot 绑定过：改红点会连带挪动各帧（实测 31.5px）。现在落位只看布局，
          与 pivot 无关 —— 改红点就只动红点。
        """
        if not self._action or not self._transform_indices:
            return None
        layout = layout or self._current_layout()
        if layout is None:
            return None
        _cw, _ch, positions, _meta = layout
        opacity = self._action.frame_opacity
        main_rect = None
        for i in self._transform_indices:
            if not (0 <= i < len(positions)):
                continue
            tx, ty, _fw, _fh = positions[i]
            pm = action_frame_pixmap(self._action, i)
            # 位置取**实时布局**（= 松手后/播放的落位），尺寸取当前变换结果。
            # 不做任何拖动补偿：补偿只对"单帧"能等价成立（差一个整幅平移，被视图
            # 锚定吸收）；多帧内容不同时每帧的补偿量不一样，正是"变了又回去"的闪。
            rect = QRectF(cx + tx * scale, cy + ty * scale,
                          pm.width() * scale, pm.height() * scale)
            is_main = (i == self._frame_index)
            p.setOpacity(opacity if is_main else 0.4)
            p.drawPixmap(rect, pm, QRectF(pm.rect()))
            if is_main:
                main_rect = rect
        p.setOpacity(1.0)
        if main_rect is None and self._transform_indices:
            # 当前帧不在目标集合（理论不会）：用第一个目标帧占位
            i = self._transform_indices[0]
            if 0 <= i < len(positions):
                tx, ty, fw, fh = positions[i]
                main_rect = QRectF(cx + tx * scale, cy + ty * scale, fw * scale, fh * scale)
        return main_rect

    def _dragging_frame_rect(self, cx: float, cy: float, scale: float) -> QRectF:
        """单帧变换拖动期间当前帧的屏幕矩形（与松手后布局同口径，避免闪跳）。

        锁定拖动开始时的快照布局，按 quad 包围盒最小角的位移做平移补偿，尺寸取
        当前变换（旋转/quad）裁剪后的实时结果；旋转无 quad 时补偿为 0，单帧左上
        角恒为 padding，因此拖动中与松手后的位置/尺寸一致，不会回弹。
        """
        idx = self._frame_index
        _, _, pos_drag, _ = self._drag_layout
        tx, ty, _, _ = pos_drag[idx]
        mo0 = self._drag_mo
        mo_new = self._current_mo()
        offx = mo_new.x() - mo0.x()
        offy = mo_new.y() - mo0.y()
        fw, fh = action_frame_size(self._action, idx)
        return QRectF(cx + (tx + offx) * scale, cy + (ty + offy) * scale,
                      fw * scale, fh * scale)

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
            self._toast.paint(p, self.rect())
            p.end()
            return

        # 冻结期间（拖偏移）用冻结布局：画布区域不动，帧真的跟着偏移走
        canvas_w, canvas_h, positions, (pad, max_w, max_h) = self._paint_layout()
        # 视图锚定：把本次变换的不动点钉回它原来的屏幕位置 —— 目标不动、
        # 灰色画布框自己在动（而不是让图像在屏幕上乱跑/松手回弹）。
        self._sync_view_shift()
        # 缩放居中（支持用户缩放和拖动）
        scale = self._zoom
        cx = self._offset.x() + self._view_shift.x()
        cy = self._offset.y() + self._view_shift.y()
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

        # 整体(GIF 级)变换：所有目标帧围绕共同中心叠合；否则走单帧/洋葱皮/多选叠加
        # 整体(GIF 级)变换：所有目标帧围绕共同中心叠合；否则走单帧/洋葱皮/多选叠加
        frame_rect = (self._paint_whole_frames(
                          p, cx, cy, scale,
                          (canvas_w, canvas_h, positions, (pad, max_w, max_h)))
                      if self._whole_active() else None)
        if frame_rect is None:
            # 参照帧（洋葱皮，半透明）
            if 0 <= self._action.ref_frame < len(self._action.frames) and self._action.ref_frame != self._frame_index:
                ref_tx, ref_ty, ref_w, ref_h = positions[self._action.ref_frame]
                ref_rect = QRectF(cx + ref_tx * scale, cy + ref_ty * scale, ref_w * scale, ref_h * scale)
                p.setOpacity(self._action.ref_opacity)
                ref_pm = action_frame_pixmap(self._action, self._action.ref_frame)
                p.drawPixmap(ref_rect, ref_pm, QRectF(ref_pm.rect()))
                p.setOpacity(1.0)

            # 多选叠加帧（Ctrl 多选：除主帧外的选中帧，半透明叠加显示）
            for fi in self._overlay_frames:
                if 0 <= fi < len(self._action.frames) and fi != self._frame_index:
                    ox, oy, ow, oh = positions[fi]
                    o_rect = QRectF(cx + ox * scale, cy + oy * scale, ow * scale, oh * scale)
                    p.setOpacity(0.5)
                    o_pm = action_frame_pixmap(self._action, fi)
                    p.drawPixmap(o_rect, o_pm, QRectF(o_pm.rect()))
                    p.setOpacity(1.0)

            # 当前帧（始终显示一帧，居中显示）
            if self._transform_mode and self._drag_layout is not None:
                # 拖动期间锁定快照布局、按 quad 包围盒最小角补偿，尺寸取实时变换结果
                frame_rect = self._dragging_frame_rect(cx, cy, scale)
            else:
                tx, ty, fw, fh = positions[self._frame_index]
                frame_rect = QRectF(cx + tx * scale, cy + ty * scale, fw * scale, fh * scale)
            p.setOpacity(self._action.frame_opacity)
            frame_pm = action_frame_pixmap(self._action, self._frame_index)
            p.drawPixmap(frame_rect, frame_pm, QRectF(frame_pm.rect()))
            p.setOpacity(1.0)
        # 参照帧模式：当前帧高亮边框（提示可拖动）
        if self._action.ref_frame >= 0:
            p.setPen(QPen(QColor("#E91E63"), 2.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(frame_rect)

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

        self._toast.paint(p, self.rect())
        p.end()

    def _draw_transform_handles(self, p: QPainter) -> None:
        # 拖动中帧/手柄框/红点/圆圈统一用快照布局几何，不做额外平移补偿
        # （旧 anchor_override 会让手柄框在旋转拖动时脱离帧内容、松手弹回=闪跳）。
        pts = self._handles_canvas(
            self._drag_mo if self._drag_kind else self._current_mo(),
            layout=self._ui_layout(),
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

        # 四角手柄：缩放/透视/扭曲/**旋转**用（旋转模式下拖角点也是旋转）；
        # 边中点手柄：斜切用（斜切是唯一拖边的模式）
        corner_control = mode in ("scale", "perspective", "distort", "rotate")
        edge_control = mode == "skew"
        for i, s in enumerate(screens):
            draw_square(s, corner_control, "corner", i, 9.0)
        if edge_control:
            for i in range(4):
                mid = (screens[i] + screens[(i + 1) % 4]) / 2.0
                draw_square(mid, edge_control, "edge", i, 8.0)

        # 旋转中心（红色十字）：所有变换模式都显示，都可拖动（含整体 GIF 级变换）。
        pivot_canvas = self._rotation_pivot_canvas(
            self._ui_layout(),
            self._drag_mo if self._drag_kind else None,
        )
        if self._drag_kind == "pivot":
            pivot_canvas = self._drag_pivot_canvas
        pivot_hover = self._hover_kind == "pivot"
        pulse = 1.0 + (0.18 * (0.5 + 0.5 * math.sin(self._hover_phase)) if pivot_hover else 0.0)
        center_s = self._logical_to_screen(pivot_canvas)
        # 红色十字 + 中心圆：醒目、区别于四角粉色手柄
        red = "#FF1744"
        red_hover = "#FF5252"
        p.setPen(QPen(QColor(red_hover if pivot_hover else red), 1.6))
        p.setBrush(QColor(red_hover if pivot_hover else red))
        p.drawEllipse(center_s, 5.0 * pulse, 5.0 * pulse)
        p.setPen(QPen(QColor(255, 90, 90, 200) if pivot_hover else QColor(255, 60, 60, 170), 1.2))
        p.drawLine(center_s + QPointF(-10, 0), center_s + QPointF(10, 0))
        p.drawLine(center_s + QPointF(0, -10), center_s + QPointF(0, 10))
        if mode == "rotate":
            pivot_canvas = self._rotation_pivot_canvas(
                self._ui_layout(),
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
                handle_hover = self._hover_kind == "rotate"
                handle_r = 6.0 * (1.16 if handle_hover else 1.0)
                p.setBrush(QColor("#FF4081" if handle_hover else "#E91E63"))
                p.setPen(QPen(QColor("#FFB0C8" if handle_hover else "#F06292"), 1.2))
                p.drawEllipse(rot, handle_r, handle_r)


def build_frame_thumb(action, index: int, color=None, size: int = 36) -> QPixmap:
    """一帧的缩略图：图像居中 + **右下角帧序号** + **右上角相似帧彩块**。

    帧缩略图和抽帧窗口**共用这一份** —— 两处必须长得一样（用户要求抽帧窗口的缩略图
    也要显示帧号和相似帧彩块）。色块画右上角，右下角留给帧序号。
    size 可以放大（抽帧窗口要 64×64 更好认），内部留白、帧号、彩块都按比例跟着走。
    """
    pm = action_frame_pixmap(action, index)
    thumb = QPixmap(size, size)
    thumb.fill(Qt.GlobalColor.transparent)
    painter = QPainter(thumb)
    if pm is not None and not pm.isNull():
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        inner = max(1, int(size * 30 / 36))
        scaled = pm.scaled(inner, inner, Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        painter.drawPixmap((size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled)
    painter.setPen(QColor("#E0E0E0"))
    painter.setFont(QFont("Microsoft YaHei UI", max(7, int(size * 7 / 36)), QFont.Weight.Bold))
    painter.drawText(QRect(size // 2, int(size * 20 / 36), size // 2 - 4, int(size * 12 / 36)),
                     Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom, str(index + 1))
    if color is not None:
        side = max(8, int(size * 9 / 36))        # 相似帧彩块：右上角，跟着放大
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(QRect(size - side - 3, 3, side, side), 2, 2)
    painter.end()
    return thumb


class AnimAdjustPanel(QWidget):
    """帧编辑面板：只作用于**当前帧**的变换、偏移、时长、参照帧/透明度。

    作用于整段动作的参数（类型/帧速/扩展/循环/锚点/地面线）已移至
    原图区下方的 ``AnimSettingsBar``。
    """

    changed = pyqtSignal()
    align_requested = pyqtSignal()      # 「自动对齐」：由主窗口算偏移 + 落一条撤销
    similar_toggled = pyqtSignal(bool)  # 「相似帧」开关：True = 按下开始标色，False = 松开清掉
    extract_requested = pyqtSignal()     # 「抽帧」：由主窗口开抽帧窗口
    # 主窗口塞进来的两个回调，必须是**类属性**：面板构造过程中就可能 set_action →
    # _populate_thumbs 会读它们，那时实例属性还没建（踩过一次 AttributeError）。
    align_reason_provider = None
    similar_color_provider = None
    # 主窗口塞进来的回调：() → (能不能按, 为什么不能)。「相似帧」看的是**全图元素**，
    # 所以原图上只要有两个以上元素就能按 —— 用户要求「没有添加动作或者选择的时候也可以点击」。
    similar_enable_provider = None

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._action: Action | None = None
        self._actions: list = []
        self._transform_changed = False
        self.canvas = None
        # 偏移拖动（滑块/方向按钮按住）期间为 True：画布区域冻结，
        # 松手时才重算并集（用户要求：拖动时帧要真的动、松手再算区域）。
        self._offset_drag = False
        # 变换作用域：右侧按钮 = 单帧(frame)；底部「动画设置」横条 = 全部帧(all)
        self._transform_scope = "frame"
        # 多选帧回调：由 ActionPreviewWidget 注入，返回当前选中的帧索引列表
        self._get_selected_rows = None
        self._build_ui()

    def _build_ui(self) -> None:
        self.setStyleSheet(_GROUP_QSS)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ---- 帧工具：整段动作的批量活儿（不碰单帧设置）----
        # 放在「帧变换」上面：它俩是"对整段动作"的操作，比单帧变换高一层。
        tools_wrap = QWidget()
        tools_wrap.setStyleSheet("background: transparent;")
        tools_row = QHBoxLayout(tools_wrap)
        tools_row.setContentsMargins(0, 0, 0, 0)
        tools_row.setSpacing(5)
        self.btn_auto_align = self._tool_btn(
            "自动对齐",
            "自动对齐：把每一帧按原图锚点放回它原来的位置（原图不是整帧时改为按重合度估算）。\n"
            "只改逐帧偏移：锚点、尺寸、帧图像都不动，一条 Ctrl+Z 可退回。")
        # 主窗口塞进来的回调：返回 "" = 可以走精确那条路；否则返回"为什么只能估算"
        # （要看原图是不是整帧，这是主窗口/图元那边的信息，面板自己拿不到）
        self.align_reason_provider = None
        self.btn_similar_frames = self._tool_btn(
            "相似帧",
            "相似帧：按下去把**全图所有元素**里长得像的帧找出来，同一组标同一种颜色"
            "（原图页面标在元素框右下角，帧缩略图标在右上角 —— 右下角留给帧序号）。\n"
            "再按一下松开，标记全部消失。判定的容差在「软件设置 → 相似帧」里，默认 0 = 一模一样。\n"
            "用途是找循环帧：状态栏会顺手报出最可能的循环长度。")
        self.btn_similar_frames.setCheckable(True)
        # 不同状态要看得出来：按下=粉色高亮（在 _tool_btn 的样式后面补一条 :checked 规则）
        self.btn_similar_frames.setStyleSheet(
            self.btn_similar_frames.styleSheet()
            + "QPushButton:checked { background: #E91E63; color: #FFFFFF;"
              " border: 1px solid #E91E63; }")
        # 主窗口塞进来的回调：(动作, 帧下标) → QColor 或 None（这一帧属于哪一组相似帧）
        self.similar_color_provider = None
        tools_row.addWidget(self.btn_auto_align)
        tools_row.addWidget(self.btn_similar_frames)
        # 第三颗「抽帧」：点开抽帧窗口（500x600：预览 + 进度条 + 帧缩略图 + 4 个抽帧选项）
        self.btn_extract = self._tool_btn(
            "抽帧", "抽帧：打开抽帧窗口（500×600）—— 上面预览当前动作，中间进度条 + 帧缩略图"
                    "（带帧序号和相似帧彩块），下面 4 个抽帧选项。\n"
                    "这一版先把位置摆好，4 个选项还没实装。")
        tools_row.addWidget(self.btn_extract)
        layout.addWidget(tools_wrap)
        self.btn_auto_align.clicked.connect(lambda: self.align_requested.emit())
        self.btn_similar_frames.toggled.connect(
            lambda on: self.similar_toggled.emit(bool(on)))
        self.btn_extract.clicked.connect(lambda: self.extract_requested.emit())

        # ---- 帧变换（只作用于当前帧；整段动画参数在「动画设置」横条）----
        g1 = QGroupBox("帧变换")
        f1 = QFormLayout(g1)
        f1.setContentsMargins(6, 12, 6, 6)
        f1.setSpacing(4)
        # ---- 自由变换（每帧独立；勾选"数据复用"时整组帧一起改）----
        # 变换模式按钮：点击后直接在预览上拖手柄调整（PS 式自由变换）
        trans_wrap = QWidget()
        trans_wrap.setStyleSheet("background: transparent;")
        trans_v = QVBoxLayout(trans_wrap)
        trans_v.setContentsMargins(0, 0, 0, 0)
        trans_v.setSpacing(5)
        self.btn_mode_rotate = self._small_btn("旋转", "旋转：点击后在预览上拖圆形旋转手柄或旋转中心；按住 Shift 吸附 15°", "rotate")
        self.btn_mode_scale = self._small_btn("缩放", "缩放：拖角点以红色锚点为中心缩放（锚点原地不动）；按住 Alt = 对角固定（老手感）；按住 Shift = 等比", "scale")
        self.btn_mode_skew = self._small_btn("斜切", "斜切：点击后在预览上拖边中点；按住 Shift = 居中剪切（对边反向）", "skew")
        self.btn_mode_perspective = self._small_btn("透视", "透视：点击后在预览上拖角点（对边反向收放 → 梯形）；按住 Shift 锁横/竖", "perspective")
        self.btn_mode_distort = self._small_btn("扭曲", "扭曲：点击后在预览上拖角点（单角自由）；按住 Shift 锁横/竖", "distort")
        self.btn_mirror_h = self._small_btn("水平镜像", "水平镜像（左右翻转）", "mirror_h")
        self.btn_mirror_v = self._small_btn("垂直镜像", "垂直镜像（上下翻转）", "mirror_v")
        # 清空变换：**红色**（与其他按钮"反着"）—— 别的按钮平时是灰字灰边、悬停才显红，
        # 它平时就是红字红边 + 红色图标，悬停反相成红底白字，一眼能看出它是个"动作"
        # 而不是模式。所以不走 _small_btn（那个是 24x24 定尺、灰底样式）。
        # 用 HoverIconButton：悬停底色变玫红后红图标会糊在底上，用户要求悬停时**图标转白**
        # ——QSS 改不了 pixmap 颜色，只能换 QIcon。
        self.btn_clear_transform = HoverIconButton("清空变换")
        self.btn_clear_transform.setToolTip(
            "清空当前帧的 旋转 / 镜像 / 透视 / 旋转中心（帧偏移不动）")
        self.btn_clear_transform.set_state_icons(
            make_tool_icon("reset", 14, color="#E91E63"),
            make_tool_icon("reset", 14, color="#ffffff"))
        self.btn_clear_transform.setIconSize(QSize(14, 14))
        self.btn_clear_transform.setStyleSheet(
            "QPushButton { background: #2b2b2b; border: 1px solid #E91E63; border-radius: 3px;"
            " color: #E91E63; font-size: 11px; font-weight: bold; }"
            "QPushButton:hover { background: #E91E63; border-color: #E91E63; color: #ffffff; }"
            "QPushButton:pressed { background: #C2185B; border-color: #C2185B; color: #ffffff; }"
        )
        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        mode_buttons = (self.btn_mode_rotate, self.btn_mode_scale, self.btn_mode_skew,
                        self.btn_mode_perspective, self.btn_mode_distort)
        for b in mode_buttons + (self.btn_mirror_h, self.btn_mirror_v):
            b.setCheckable(True)
            b.setFixedHeight(28)
            # 最小宽度放小：两个一行时够用；真正的"排几个"交给 ResponsiveButtonGrid 按宽度算
            b.setMinimumWidth(80)
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_clear_transform.setFixedHeight(28)
        self.btn_clear_transform.setMinimumWidth(80)
        self.btn_clear_transform.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        for b in mode_buttons:
            self._mode_group.addButton(b)
        self.btn_mode_rotate.toggled.connect(lambda on: self._on_mode_button("rotate", on))
        self.btn_mode_scale.toggled.connect(lambda on: self._on_mode_button("scale", on))
        self.btn_mode_skew.toggled.connect(lambda on: self._on_mode_button("skew", on))
        self.btn_mode_perspective.toggled.connect(lambda on: self._on_mode_button("perspective", on))
        self.btn_mode_distort.toggled.connect(lambda on: self._on_mode_button("distort", on))
        # 5 个变换模式 + 清空变换 + 2 个镜像 = **8 个，正好每行 2 个排 4 行**（末行不空）。
        # 清空变换放在模式之后、镜像之前：前 3 行是"改"，第 4 行是"翻"，边界清楚。
        self._transform_grid = ResponsiveButtonGrid(
            list(mode_buttons) + [self.btn_clear_transform,
                                  self.btn_mirror_h, self.btn_mirror_v],
            max_columns=2, min_button=84)
        self.btn_clear_transform.clicked.connect(self._clear_frame_transform)
        self.btn_mirror_h.clicked.connect(lambda: self._toggle_mirror("h"))
        self.btn_mirror_v.clicked.connect(lambda: self._toggle_mirror("v"))
        trans_v.addWidget(self._transform_grid)
        # 当前变换摘要：单独一行、占满宽度
        self.lbl_transform_summary = QLabel("当前变换：无变换")
        self.lbl_transform_summary.setMinimumHeight(48)
        self.lbl_transform_summary.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_transform_summary.setWordWrap(True)
        self.lbl_transform_summary.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.lbl_transform_summary.setStyleSheet(
            "QLabel { color: #bdbdbd; background: #252525; border: 1px solid #3d3d3d; "
            "border-radius: 3px; padding: 0 7px; }"
        )
        self.lbl_transform_summary.setToolTip("当前帧的全部变换状态；旋转角度只读")
        trans_v.addWidget(self.lbl_transform_summary)
        f1.addRow(trans_wrap)

        layout.addWidget(g1)

        # ---- 当前帧微调 / 参照 ----
        g3 = QGroupBox("当前帧微调 / 参照")
        g3_layout = QVBoxLayout(g3)
        g3_layout.setContentsMargins(6, 12, 6, 6)
        g3_layout.setSpacing(4)

        offset_header = QHBoxLayout()
        offset_header.setSpacing(4)
        offset_header.addWidget(QLabel("帧偏移"))
        offset_header.addStretch()
        g3_layout.addLayout(offset_header)

        self.btn_up = self._small_btn("", "当前帧上移1px（可按住连续移动）", "arrow_up")
        self.btn_down = self._small_btn("", "当前帧下移1px（可按住连续移动）", "arrow_down")
        self.btn_left = self._small_btn("", "当前帧左移1px（可按住连续移动）", "arrow_left")
        self.btn_right = self._small_btn("", "当前帧右移1px（可按住连续移动）", "arrow_right")
        self.btn_up.clicked.connect(lambda: self._adjust_frame(0, -1))
        self.btn_down.clicked.connect(lambda: self._adjust_frame(0, 1))
        self.btn_left.clicked.connect(lambda: self._adjust_frame(-1, 0))
        self.btn_right.clicked.connect(lambda: self._adjust_frame(1, 0))
        # 按住不放 = 连续微调（用户要"真的在移动"的感觉；单点一次仍是 1px）
        for btn in (self.btn_up, self.btn_down, self.btn_left, self.btn_right):
            btn.setAutoRepeat(True)
            btn.setAutoRepeatDelay(400)
            btn.setAutoRepeatInterval(60)
            # 按下即冻结画布区域，松开才重算 —— 否则帧"不动"、只有画布在长大
            btn.pressed.connect(self._begin_offset_drag)
            btn.released.connect(self._end_offset_drag)
        self.btn_reset = self._small_btn("", "重置当前帧偏移", "reset")
        self.btn_reset.clicked.connect(self._reset_frame_offset)

        # 水平滑块（下方）与垂直滑块（右侧）：用于大幅调节偏移。
        # 用**双向**样式（不填 sub-page）：这两个滑块以 0 为中心（-200~+200），
        # 用普通样式时值为 0 会左半边全亮，看着像"只填了一半"而不是"在中间"。
        self.slider_off_h = QSlider(Qt.Orientation.Horizontal)
        self.slider_off_h.setRange(-200, 200)
        self.slider_off_h.setValue(0)
        self.slider_off_h.setFixedHeight(20)
        self.slider_off_h.setStyleSheet(_BIPOLAR_SLIDER_QSS)
        self.slider_off_h.setToolTip("大幅调节水平偏移（-200 ~ +200，居中为 0）")
        self.slider_off_h.valueChanged.connect(self._on_offset_slider_h)
        # 拖动期间冻结画布区域（帧真的动），松手再重算并集
        self.slider_off_h.sliderPressed.connect(self._begin_offset_drag)
        self.slider_off_h.sliderReleased.connect(self._end_offset_drag)
        self.slider_off_v = QSlider(Qt.Orientation.Vertical)
        self.slider_off_v.setRange(-200, 200)
        self.slider_off_v.setValue(0)
        self.slider_off_v.setFixedWidth(20)
        self.slider_off_v.setStyleSheet(_BIPOLAR_SLIDER_QSS)
        self.slider_off_v.setInvertedAppearance(True)  # 向上拖=值减小=帧向上移
        self.slider_off_v.setToolTip("大幅调节垂直偏移（-200 ~ +200，居中为 0，向上为负）")
        self.slider_off_v.valueChanged.connect(self._on_offset_slider_v)
        self.slider_off_v.sliderPressed.connect(self._begin_offset_drag)
        self.slider_off_v.sliderReleased.connect(self._end_offset_drag)

        offset_grid = QGridLayout()
        offset_grid.setContentsMargins(0, 0, 0, 0)
        offset_grid.setSpacing(2)
        offset_grid.addWidget(self.btn_up, 0, 1)
        offset_grid.addWidget(self.btn_left, 1, 0)
        offset_grid.addWidget(self.btn_reset, 1, 1)
        offset_grid.addWidget(self.btn_right, 1, 2)
        offset_grid.addWidget(self.btn_down, 2, 1)
        # 左右按钮紧贴中间（不加列拉伸）
        # 右侧：垂直滑块（跨 3 行）——调 Y
        offset_grid.addWidget(self.slider_off_v, 0, 3, 3, 1)
        # 偏移数值：放到右边竖着显示（X 上 / Y 下），双击可直接输入
        self.spin_off_x = EditableNumberLabel(0)
        self.spin_off_x.setToolTip("当前帧水平偏移 X：双击可直接输入")
        self.spin_off_x.value_edited.connect(self._on_offset_label_changed)
        self.spin_off_y = EditableNumberLabel(0)
        self.spin_off_y.setToolTip("当前帧垂直偏移 Y：双击可直接输入")
        self.spin_off_y.value_edited.connect(self._on_offset_label_changed)
        l_off_x = QHBoxLayout()
        l_off_x.setSpacing(2)
        l_off_x.addWidget(QLabel("X"))
        l_off_x.addWidget(self.spin_off_x)
        offset_grid.addLayout(l_off_x, 0, 4)
        l_off_y = QHBoxLayout()
        l_off_y.setSpacing(2)
        l_off_y.addWidget(QLabel("Y"))
        l_off_y.addWidget(self.spin_off_y)
        offset_grid.addLayout(l_off_y, 1, 4)
        # 下方：水平滑块（跨 3 列）——调 X
        offset_grid.addWidget(self.slider_off_h, 3, 0, 1, 3)
        g3_layout.addLayout(offset_grid)

        # 本帧时长：默认跟随帧速度(fps)，可逐帧覆盖（毫秒）
        dur_wrap = QWidget()
        dur_wrap.setStyleSheet("background: transparent;")
        dur_row = QHBoxLayout(dur_wrap)
        dur_row.setContentsMargins(0, 4, 0, 0)
        dur_row.setSpacing(4)
        dur_row.addWidget(QLabel("本帧时长"))
        self.spin_duration = CompactSpinBox()
        self.spin_duration.setRange(10, 5000)
        self.spin_duration.setSingleStep(10)
        self.spin_duration.setSuffix(" ms")
        self.spin_duration.setToolTip("当前帧显示时长（毫秒）；等于默认时长时自动跟随帧速度，改了则只影响这一帧")
        self.spin_duration.valueChanged.connect(self._on_duration_changed)
        dur_row.addWidget(self.spin_duration, 1)
        self.btn_duration_reset = self._small_btn("", "重置本帧时长为默认（跟随帧速度）", "reset")
        self.btn_duration_reset.clicked.connect(self._reset_duration)
        dur_row.addWidget(self.btn_duration_reset)
        g3_layout.addWidget(dur_wrap)

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

    def _tool_btn(self, text: str, tip: str, enabled: bool = True) -> QPushButton:
        # 按钮一律不抢键盘焦点：否则点过按钮之后空格会去「按」它，预览就没法空格拖动了
        # （Qt 默认行为）。空格的活儿留给画布。
        """「帧工具」那一行的按钮：跟着面板配色，比 24x24 的小图标按钮宽（要写汉字）。"""
        btn = QPushButton(text)
        btn.setToolTip(tip)
        btn.setEnabled(enabled)
        btn.setMinimumHeight(26)
        btn.setStyleSheet(
            "QPushButton { background: #2b2b2b; border: 1px solid #3d3d3d; border-radius: 3px;"
            " color: #e8e8e8; font-size: 11px; padding: 2px 4px; }"
            "QPushButton:hover { background: #3a3a3a; border-color: #E91E63; }"
            "QPushButton:pressed { background: #4a4a4a; }"
            "QPushButton:disabled { color: #6a6a6a; border-color: #333; }"
        )
        return btn

    def _sync_align_buttons(self) -> None:
        """「自动对齐」的可用性：要有当前动作、至少两帧、且至少两帧有画面。

        没得对齐时按钮禁用并写明原因 —— 不留"点了没反应的按钮"。
        """
        action = self._action
        ok = action is not None and len(action.frames) >= 2
        why = ""
        if action is None:
            why = "先选一个动作（当前没有动作）"
        elif len(action.frames) < 2:
            why = "这个动作只有一帧，谈不上对齐"
        else:
            live = sum(1 for i in range(len(action.frames))
                       if frame_has_content(action, i))
            if live < 2:
                ok = False
                why = "这个动作里带画面的帧不足两帧，没有可对齐的内容"
        self.btn_auto_align.setEnabled(ok)
        if ok:
            # 能不能走"精确"那条路由主窗口判断（要看原图是不是整帧），这里只负责把话说清楚
            reason = ""
            if callable(self.align_reason_provider):
                try:
                    reason = self.align_reason_provider(action) or ""
                except Exception:  # noqa: BLE001
                    reason = ""
            if not reason:
                self.btn_auto_align.setToolTip(
                    "自动对齐（精确）：这些原图是整帧画面，锚点位置算得准 ——\n"
                    "按「原图锚点 − 紧切框中心」把每帧放回它在原图里的位置，\n"
                    "于是播放/导出时角色不再帧间乱飘。\n"
                    "只改逐帧偏移：锚点、尺寸、帧图像都不动，一条 Ctrl+Z 可退回。")
            else:
                self.btn_auto_align.setToolTip(
                    f"自动对齐（只能估算）：{reason}\n"
                    "原始位置（正常切割那个框）已经不在工程里，只能按帧间重合度去猜 ——\n"
                    "**可能不准，建议点完手动核对那几帧**，点下去会先弹提示确认。\n"
                    "只改逐帧偏移：锚点、尺寸、帧图像都不动，一条 Ctrl+Z 可退回。")
        else:
            self.btn_auto_align.setToolTip(f"自动对齐：现在用不了 —— {why}")

        # 「相似帧」开关：找的是**全图所有元素**（不止这个动作），所以只要原图上有两个以上元素
        # 就能按 —— 没加动作、没选帧的时候也能直接看原图（用户明确要求）。
        sim_ok, why_sim = True, "原图上还没有元素"
        if callable(self.similar_enable_provider):
            try:
                sim_ok, why_sim = self.similar_enable_provider()
            except Exception:  # noqa: BLE001 — 探测失败不能影响面板同步
                sim_ok, why_sim = False, "读不到原图元素"
        elif action is not None and len(action.frames) >= 2:
            sim_ok, why_sim = True, ""
        else:
            sim_ok = False
            why_sim = ("先选一个动作（当前没有动作）" if action is None
                       else "这个动作只有一帧，没有可比的帧")
        if not self.btn_similar_frames.isChecked():
            self.btn_similar_frames.setEnabled(bool(sim_ok))
            if sim_ok:
                self.btn_similar_frames.setToolTip(
                    "相似帧：按下去把**全图所有元素**里长得像的帧找出来，同一组标同一种颜色"
                    "（原图页面标在元素框右下角，帧缩略图标在右上角 —— 右下角留给帧序号）。\n"
                    "再按一下松开，标记全部消失。判定容差在「软件设置 → 相似帧」里，默认 0 = 一模一样。\n"
                    "用途是找循环帧：状态栏会顺手报出最可能的循环长度。")
            else:
                self.btn_similar_frames.setToolTip(f"相似帧：现在用不了 —— {why_sim}")

        # 「抽帧」：得有当前动作才谈得上抽帧。动图文档模式下 actions 是空的，
        # 那时候这颗按钮必须灰掉并写明原因 —— 不留"点了没反应"的按钮。
        ex_ok, why_ex = (action is not None), "先选一个动作（当前没有动作）"
        ex_provider = getattr(self, "extract_enable_provider", None)
        if callable(ex_provider):
            try:
                ex_ok, why_ex = ex_provider()
            except Exception:  # noqa: BLE001 — 探测失败不能影响面板同步
                ex_ok, why_ex = False, "读不到当前动作"
        self.btn_extract.setEnabled(bool(ex_ok))
        self.btn_extract.setToolTip(
            "抽帧：按相似度把一串**相邻**的重复帧抽稀（4 档由轻到重：抽帧3/5、1/2、1/4、强力去重）。\n"
            "窗口里可空格拖动、调循环与帧率、实时改相似度；每档都按**这个动作的所有帧**重算，"
            "结果实时落到当前动作，点「取消」可整轮撤回。"
            if ex_ok else f"抽帧：现在用不了 —— {why_ex}")

    def set_action(self, action: Action | None):
        self._action = action
        if action is None:
            self._sync_offset_widgets(0, 0)
            self.combo_ref.clear()
            self._sync_transform_widgets()
            self._sync_duration_widget()
            self._sync_align_buttons()
            return
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
        self._sync_duration_widget()
        self._sync_align_buttons()

    def _refresh_canvas_label(self):
        if self._action and self._action.frames:
            idx = getattr(self, "_current_frame", 0)
            dx, dy = self._action.frame_offsets.get(idx, (0, 0))
            self._sync_offset_widgets(dx, dy)
        else:
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

    def _begin_offset_drag(self) -> None:
        """偏移拖动开始：冻结画布区域，期间帧跟着偏移真实移动。"""
        if self._offset_drag:
            return
        self._offset_drag = True
        if self.canvas is not None:
            self.canvas.begin_layout_freeze()

    def _end_offset_drag(self) -> None:
        """偏移拖动结束：重算画布区域（并集可能因偏移扩大/缩小）并刷新标签。"""
        if not self._offset_drag:
            return
        self._offset_drag = False
        # 变换作用域：右侧按钮 = 单帧(frame)；底部「动画设置」横条 = 全部帧(all)
        self._transform_scope = "frame"
        if self.canvas is not None:
            self.canvas.end_layout_freeze()
        self._refresh_canvas_label()
        self.changed.emit()

    def _set_current_offset(self, dx: int | None = None, dy: int | None = None) -> None:
        """设置当前帧偏移的某个分量并刷新界面。"""
        self._exit_transform_mode()
        if not self._action or not self._action.frames:
            self._hint_no_action()
            return
        idx = getattr(self, "_current_frame", 0)
        if not (0 <= idx < len(self._action.frames)):
            return
        cur = self._action.frame_offsets.get(idx, (0, 0))
        nx = cur[0] if dx is None else int(dx)
        ny = cur[1] if dy is None else int(dy)
        if (nx, ny) == cur:
            return
        # 增量 = 目标值 - 当前帧旧值；Ctrl 多选时所有选中帧按各自旧值加同一增量，
        # 保持各帧原有偏移差（"整组一起动"，而不是把每帧偏移统一成同一个值）。
        delta = (nx - cur[0], ny - cur[1])
        for act, indices in self._transform_targets():
            for i in indices:
                ci = act.frame_offsets.get(i, (0, 0))
                tnx, tny = ci[0] + delta[0], ci[1] + delta[1]
                if tnx == 0 and tny == 0:
                    act.frame_offsets.pop(i, None)
                else:
                    act.frame_offsets[i] = (tnx, tny)
        if self._offset_drag:
            # 拖动中：画布区域冻结（用户要求"松手以后再计算区域"），
            # 只同步控件显示；区域尺寸留到 _end_offset_drag 再算。
            self._sync_offset_widgets(nx, ny)
        else:
            self._sync_offset_widgets(nx, ny)
            self._refresh_canvas_label()   # 边缘并集随微调实时变化
        self.changed.emit()

    def _on_offset_label_changed(self, _value: int) -> None:
        self._set_current_offset(self.spin_off_x.value(), self.spin_off_y.value())

    def _on_offset_slider_h(self, value: int) -> None:
        self._set_current_offset(dx=value)

    def _on_offset_slider_v(self, value: int) -> None:
        self._set_current_offset(dy=value)

    def _adjust_frame(self, dx: int, dy: int):
        """方向按钮：当前帧偏移微调 1px。"""
        self._exit_transform_mode()
        if not self._action or not self._action.frames:
            self._hint_no_action()
            return
        idx = getattr(self, "_current_frame", 0)
        if 0 <= idx < len(self._action.frames):
            cur = self._action.frame_offsets.get(idx, (0, 0))
            self._set_current_offset(cur[0] + dx, cur[1] + dy)

    def _reset_frame_offset(self):
        self._exit_transform_mode()
        if not self._action:
            self._hint_no_action()
            return
        for act, indices in self._transform_targets():
            for i in indices:
                act.frame_offsets.pop(i, None)
        self._sync_offset_widgets(0, 0)
        self._refresh_canvas_label()
        self.changed.emit()

    def set_current_frame(self, idx: int):
        """切帧：同步变换控件**和偏移控件**。

        偏移控件以前没同步 —— 切到没有偏移的帧，滑块还停在上一个帧的位置。
        后果是看起来"偏移是所有帧共用的"、而且"滑块不居中"（用户报的就是这两句）。
        实测：第 0 帧设 (30,-12) 后切到第 1 帧，滑块仍显示 (30,-12)、数字框也一样，
        而 frame_offsets 里其实只有 0 号帧。数据层一直是逐帧的，坏的是显示。
        """
        self._current_frame = idx
        self._sync_transform_widgets()
        if self._action and 0 <= idx < len(self._action.frames):
            self._sync_offset_widgets(*self._action.frame_offsets.get(idx, (0, 0)))
        else:
            self._sync_offset_widgets(0, 0)
        self._sync_duration_widget()

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
        items = []
        angle = self._action.frame_rotations.get(idx, 0)
        if angle:
            # 角度占固定 3 位：位数变化时这一行不会跟着左右抖（用户实测反馈）
            items.append(f"旋转 {int(angle) % 360:>3d}°")
        modes = self._action.frame_transform_modes.get(idx, set())
        # "skew" 保留在映射里：老工程里可能记录了这次变换用过斜切，摘要要能照实显示
        # （按钮已经去掉了，只是不再能施加新的斜切）。
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
        # 什么都没有时明确写"无变换"，不要显示成"旋转 0°"——那看着像有变换，
        # 而且「清空变换」按钮点完就该看到它变成"无变换"（否则不知道清没清掉）。
        self.lbl_transform_summary.setText("当前变换：" + (" · ".join(items) if items else "无变换"))

    def _transform_targets(self) -> list:
        """变换目标：底部横条 = 全部帧；右侧单帧 = Ctrl 多选帧（无多选则当前帧）。"""
        if not self._action:
            return []
        if self._transform_scope == "all":
            return [(self._action, list(range(len(self._action.frames))))]
        # 单帧模式：Ctrl 多选时作用于所有选中帧
        get_rows = getattr(self, "_get_selected_rows", None)
        if get_rows is not None:
            try:
                rows = get_rows()
            except Exception:
                rows = []
            valid = [r for r in rows if 0 <= r < len(self._action.frames)]
            if len(valid) > 1:
                return [(self._action, valid)]
        return [(self._action, [getattr(self, "_current_frame", 0)])]

    # ---- 自由变换：模式按钮 + 预览手柄回调 ----
    def set_canvas(self, canvas) -> None:
        self.canvas = canvas

    def _hint_no_action(self) -> bool:
        """没有当前动作时给出"先添加动作"提示；返回 True 表示调用方应直接返回。

        用户报的是"加了图片、点控件完全没反应"。这些控件在无动作时确实是空转
        （数据没地方落），所以不是静默 return，而是把原因画到预览画布上。
        """
        if self._action and self._action.frames:
            return False
        canvas = getattr(self, "canvas", None)
        if canvas is not None:
            canvas.hint_no_action()
        return True

    def _exit_transform_mode(self) -> None:
        """切换到其他工具/按钮时自动退出变换模式（按钮弹回、手柄消失）。"""
        canvas = getattr(self, "canvas", None)
        if canvas is not None and canvas.transform_mode():
            canvas.set_transform_mode(None)

    def _on_mode_button(self, mode: str, on: bool) -> None:
        canvas = getattr(self, "canvas", None)
        if canvas is None:
            return
        if on and self._hint_no_action():
            # 无动作时不允许进入变换模式：按钮立刻弹回，避免"按钮亮着手柄却没有"
            self._on_canvas_mode_changed("")
            return
        if on:
            # 右侧按钮进入的变换模式只作用于当前帧
            self._transform_scope = "frame"
            canvas.set_transform_mode(mode, "frame")
            for _act, inds in self._transform_targets():
                canvas.set_transform_targets(inds)
                break
        elif canvas.transform_mode() == mode:
            canvas.set_transform_mode(None)

    def _on_canvas_mode_changed(self, mode: str) -> None:
        """预览画布侧的退出（点空白/Esc/再点同按钮）→ 同步按钮选中态。

        只有「右侧帧编辑面板」发起的变换才点亮这里的按钮；
        底部动作编辑横条发起的（owner=='all'）一律弹回，两套工具互不联动。
        """
        canvas = getattr(self, "canvas", None)
        owner = getattr(canvas, "_transform_owner", "") if canvas is not None else ""
        actual = canvas.transform_mode() if canvas is not None else ""
        # 右侧按钮只在「帧编辑(frame)」发起时点亮；动作编辑(all)发起一律弹回
        button_mode = mode if owner == "frame" else ""
        # 作用域只在画布真正退出变换（actual 为空）时才回到单帧。
        # 注意不能用被置空的 mode 判断：all 激活时 mode 也会被置空（仅为不亮按钮），
        # 否则会把刚设好的 "all" 立刻重置回 "frame"，导致动作编辑只改当前帧。
        if not actual:
            self._transform_scope = "frame"
        btn = {"rotate": self.btn_mode_rotate, "scale": self.btn_mode_scale,
               "skew": self.btn_mode_skew, "perspective": self.btn_mode_perspective,
               "distort": self.btn_mode_distort}.get(button_mode)
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

    def enter_transform_mode_all(self, mode: str) -> None:
        """底部「动画设置」横条激活变换模式：作用于全部帧。"""
        canvas = getattr(self, "canvas", None)
        if canvas is None:
            return
        if mode:
            if self._hint_no_action():
                return
            self._transform_scope = "all"
            canvas.set_transform_mode(mode, "all")
            if self._action:
                canvas.set_transform_targets(list(range(len(self._action.frames))))
        elif canvas.transform_mode():
            self._transform_scope = "frame"
            canvas.set_transform_mode(None, "all")

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
                # 旋转中心不再在整体模式下被清掉：用户可能特意把红点挪到脚底/关节，
                # 整体旋转同样要绕那个点（各帧 pivot 已按同一视觉点分发）。
            act.invalidate_transforms()
        self._transform_changed = True
        self.changed.emit()
        self._sync_transform_widgets()

    def _apply_pivot(self, pivot) -> None:
        """旋转中心变更，只写入旋转参数，不触碰动作锚点。

        整体(GIF 级)变换没有特殊分支：红点画在**代表帧**上，逆映射算出来的就是
        代表帧自己的归一化 pivot，同一个值发给所有目标帧 —— 各帧原图尺寸一致时
        它对应同一个视觉部位（等同"全选所有帧、每帧各设同一个锚点"）。
        """
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
            if len(indices) > 1:
                # 整体(GIF 级)：把参考框 quad 逐帧换算成各帧自己的 quad，保持重叠
                dist = distribute_whole_quad(act, indices, quad)
                for i, q in dist.items():
                    if q is None:
                        act.frame_quads.pop(i, None)
                        if mode:
                            act.frame_transform_modes.pop(i, None)
                    else:
                        act.frame_quads[i] = q
                        if mode:
                            act.frame_transform_modes.setdefault(i, set()).add(mode)
            else:
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

    def _clear_frame_transform(self) -> None:
        """清空当前帧的变换：旋转 / 两种镜像 / 透视四边形 / 旋转中心（回调记录的模式也清）。

        为什么需要它：整份界面里原来只有"重置当前帧偏移"，**没有任何地方能一键清掉
        旋转/镜像/透视** —— 试完透视想还原只能一个个角拖回去或按撤销。
        尊重"数据复用"：勾选时清的是整组帧，和旋转/镜像/quad 的处理保持一致。
        帧偏移不在这里清（它属于下面"当前帧微调"那一栏，有自己的重置按钮）。
        """
        self._exit_transform_mode()
        if not self._action or not self._action.frames:
            self._hint_no_action()
            return
        for act, indices in self._transform_targets():
            for i in indices:
                act.frame_rotations.pop(i, None)
                act.frame_rotation_pivots.pop(i, None)
                act.frame_quads.pop(i, None)
                act.frame_transform_modes.pop(i, None)
                act.frame_mirror_h.discard(i)
                act.frame_mirror_v.discard(i)
            act.invalidate_transforms()
        self._transform_changed = True
        self.changed.emit()
        self._sync_transform_widgets()
        canvas = getattr(self, "canvas", None)
        if canvas is not None:
            canvas.update()
        self._refresh_canvas_label()

    def _toggle_mirror(self, axis: str) -> None:
        self._exit_transform_mode()
        if not self._action or not self._action.frames:
            self._hint_no_action()
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

        self._refresh_canvas_label()
        canvas = getattr(self, "canvas", None)
        if canvas is not None:
            canvas.update()

    def _on_ref_changed(self, idx: int):
        if self._action and idx >= 0:
            self._action.ref_frame = self.combo_ref.itemData(idx)
            self.changed.emit()

    def _on_opacity_changed(self, val: int):
        """透明度滑块调整当前帧透明度（非参照帧）。"""
        if self._action:
            self._action.frame_opacity = val / 100.0
            self.changed.emit()

    def _sync_duration_widget(self) -> None:
        """把当前帧时长同步到 spin（阻塞信号）；重置按钮仅在该帧被单独设置时可用。"""
        idx = getattr(self, "_current_frame", 0)
        if self._action and 0 <= idx < len(self._action.frames):
            ms = self._action.frame_duration_ms(idx)
            overridden = idx in self._action.frame_durations
        else:
            ms, overridden = 100, False
        self.spin_duration.blockSignals(True)
        self.spin_duration.setValue(ms)
        self.spin_duration.blockSignals(False)
        self.btn_duration_reset.setEnabled(overridden)

    def _on_duration_changed(self, val: int) -> None:
        """本帧时长 spin：等于默认时长则跟随帧速度，不同才逐帧覆盖。"""
        if not self._action or not self._action.frames:
            return
        idx = getattr(self, "_current_frame", 0)
        if not (0 <= idx < len(self._action.frames)):
            return
        val = max(10, int(val))
        default_ms = max(1, round(1000 / max(1, self._action.fps)))
        if val == default_ms:
            self._action.frame_durations.pop(idx, None)
        else:
            self._action.frame_durations[idx] = val
        self.btn_duration_reset.setEnabled(idx in self._action.frame_durations)
        self.changed.emit()

    def _reset_duration(self) -> None:
        """本帧时长重置为默认（跟随帧速度）。"""
        if not self._action:
            return
        idx = getattr(self, "_current_frame", 0)
        self._action.frame_durations.pop(idx, None)
        self._sync_duration_widget()
        self.changed.emit()



class ActionPreviewWidget(QWidget):
    """动作预览窗口：预览画布 + 浮动工具条 + 播放控制 + 帧缩略图 + 帧编辑面板。"""

    frame_changed = pyqtSignal(int)
    action_changed = pyqtSignal(object)  # 当前动作切换（供「动画设置」横条同步）
    # 主窗口塞进来的回调：(动作, 帧下标) → QColor 或 None。**类属性**：构造过程中就可能
    # 建缩略图，那时实例属性还没建（踩过一次 AttributeError）。
    similar_color_provider = None

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
        # 放大/缩小按钮已撤掉（功能还在：滚轮/快捷键），所以这里不再连按钮信号
        self.toolbar.bg_changed.connect(self._on_bg_changed)

        # 上下两块各自"成一体"，中间是可拖动分隔线：
        #   上 = 预览（画布 + 播放控制条 + 帧缩略图）
        #   下 = 帧编辑（标题 + 参数面板 + 原图信息 + 导出）
        # 为什么要分组：以前只有"画布 / 其余全部"两段，最大化窗口时"其余全部"跟着长高，
        # 帧编辑面板内部的按钮/输入框被拉散（用户报的"内部按钮混乱"）。现在两块各有
        # 默认高度，分隔线可以独立拖动（用户要求"可以独立拖动"）。
        self.preview_box = QWidget()
        preview_box_layout = QVBoxLayout(self.preview_box)
        preview_box_layout.setContentsMargins(0, 0, 0, 0)
        preview_box_layout.setSpacing(4)
        self.edit_box = QWidget()
        self.edit_layout = QVBoxLayout(self.edit_box)
        self.edit_layout.setContentsMargins(0, 0, 0, 0)
        self.edit_layout.setSpacing(4)

        self.preview_splitter = QSplitter(Qt.Orientation.Vertical)
        # handleWidth 必须 >0，否则分隔线拖不动（用户要求"可以独立拖动"）
        self.preview_splitter.setHandleWidth(6)
        self.preview_splitter.setChildrenCollapsible(False)
        self.preview_splitter.setStyleSheet(
            "QSplitter::handle { background: #3d3d3d; }"
            "QSplitter::handle:hover { background: #E91E63; }"
        )
        self.preview_splitter.addWidget(self.preview_box)
        self.preview_splitter.addWidget(self.edit_box)
        # 拉伸因子：只有上面的"预览"跟着窗口长高，下面的"帧编辑"保持默认高度
        # （里面的按钮/控件因此不会被拉变形）
        self.preview_splitter.setStretchFactor(0, 1)
        self.preview_splitter.setStretchFactor(1, 0)
        # 用户一旦自己拖过分隔线，就不再自动改回默认高度
        self._user_split = False
        self.preview_splitter.splitterMoved.connect(self._on_splitter_moved)
        layout.addWidget(self.preview_splitter, 1)

        # 控制栏：播放/暂停 + 进度条 + 帧序号（带背景）
        self.ctrl_widget = QWidget()
        self.ctrl_widget.setStyleSheet("background-color: #1a1a1a; border: none; border-radius: 4px;")
        ctrl = QHBoxLayout(self.ctrl_widget)
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

        preview_box_layout.addWidget(self.canvas, 1)
        preview_box_layout.addWidget(self.ctrl_widget)

        # 帧缩略图
        self.thumb_list = QListWidget()
        self.thumb_list.setFixedHeight(52)
        # IconMode + 固定 gridSize + item.setSizeHint，点击区域与格子完全一致
        self.thumb_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.thumb_list.setIconSize(QSize(36, 36))
        self.thumb_list.setResizeMode(QListWidget.ResizeMode.Fixed)
        self.thumb_list.setMovement(QListWidget.Movement.Static)
        self.thumb_list.setFlow(QListWidget.Flow.LeftToRight)
        self.thumb_list.setWrapping(False)
        self.thumb_list.setSpacing(0)
        self.thumb_list.setGridSize(QSize(48, 48))
        self.thumb_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.thumb_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.thumb_list.setStyleSheet(
            "QListWidget { background: #1a1a1a; border: none; border-radius: 4px; padding: 2px; }"
            "QListWidget::item { border: 1px solid #555555; border-radius: 3px; background: transparent; }"
            "QListWidget::item:selected { background: rgba(233,30,99,0.25); border: 1px solid #E91E63; }"
        )
        self.thumb_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.thumb_list.currentRowChanged.connect(self._on_thumb_changed)
        self.thumb_list.itemSelectionChanged.connect(self._on_thumb_selection_changed)
        preview_box_layout.addWidget(self.thumb_list)

        # 帧编辑区总标题（与原图区下方「动画设置」横条对应：横条管整段，这里管单帧）
        self.panel_title = QLabel("帧编辑·当前帧")
        self.panel_title.setFixedHeight(22)
        self.panel_title.setStyleSheet(
            "QLabel { color: #F06292; font-size: 11px; background: #1f1f1f;"
            " border: 1px solid #333333; border-radius: 3px; padding: 0 8px; }"
        )
        self.edit_layout.addWidget(self.panel_title)

        # 帧编辑面板（可滚动）
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.adjust_panel = AnimAdjustPanel()
        self.adjust_panel.set_canvas(self.canvas)
        self.adjust_panel._get_selected_rows = self._selected_rows
        self.canvas._get_selected_rows = self._selected_rows
        # 预览画布 ↔ 帧设置面板：变换模式按钮、拖转/拖四边形互相同步
        self.canvas.transform_mode_changed.connect(self.adjust_panel._on_canvas_mode_changed)
        self.canvas.transform_rotated.connect(self.adjust_panel._apply_rotation)
        self.canvas.transform_quadded.connect(self.adjust_panel._apply_quad)
        self.canvas.transform_pivot_moved.connect(self.adjust_panel._apply_pivot)
        # 参照帧模式下鼠标拖动当前帧：同步右侧偏移控件显示
        self.canvas.frame_offset_dragged.connect(self._on_canvas_frame_dragged)
        self.adjust_panel.changed.connect(self._on_adjust_changed)
        # 变换手柄松手后补刷拖动期间挂起的缩略图
        self.canvas.transform_edit_finished.connect(self._on_transform_edit_finished)
        # 画布进入变换模式 → 暂停播放（播放每 tick 会切帧、打断编辑并清掉视图锚定）
        self.canvas.transform_mode_changed.connect(self._on_canvas_transform_mode)
        self.scroll.setWidget(self.adjust_panel)
        self.edit_layout.addWidget(self.scroll, 1)

        # 原图信息栏
        self.lbl_source_info = QLabel("未加载原图")
        self.lbl_source_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_source_info.setStyleSheet(
            "QLabel { color: #888; font-size: 10px; padding: 3px 4px; "
            "background: #1a1a1a; border: 1px solid #333; border-radius: 3px; }"
        )
        self.edit_layout.addWidget(self.lbl_source_info)

        # 导出按钮
        self.btn_export = QPushButton("导出")
        self.btn_export.setFixedHeight(36)
        self.btn_export.setStyleSheet(
            "QPushButton { background: #E91E63; color: white; border: none; border-radius: 4px; font-size: 13px; }"
            "QPushButton:hover { background: #F06292; }"
        )
        self.btn_export.clicked.connect(self._on_export_clicked)
        self.edit_layout.addWidget(self.btn_export)

        self._square_preview = True
        QTimer.singleShot(0, self._apply_default_square)

    # 预览块里**画布**的默认高度（控制条 32 / 缩略图 52 是固定高，跟着一起"成一体"）。
    # 用户要求："固定"= 默认状态下就是这么大，避免最大化窗口后内部按钮被拉乱。
    PREVIEW_CANVAS_H = 200

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._action is None:
            QTimer.singleShot(0, self._apply_default_square)

    def _apply_default_square(self):
        if self._action is not None:
            return
        # 无动作时也由 splitter 控制画布高度，不锁方形（默认 200px）
        self.canvas.setMinimumHeight(100)
        self.canvas.setMaximumHeight(16777215)
        self._apply_default_split()

    def _edit_min_height(self) -> int:
        """帧编辑块的最小高度 = 能完整放下内部控件（标题+面板+信息+导出按钮）。

        用户要求"帧编辑宽高固定，避免内部按钮混乱"：面板里的行是固定高度的，
        高度不够时会被压扁/裁掉。所以这里把最小高度钉在内容需要的像素上
        （最多只允许占整块的六成，避免小窗口被顶爆）。
        """
        panel_h = self.adjust_panel.sizeHint().height()
        chrome = (self.panel_title.sizeHint().height()
                  + self.lbl_source_info.sizeHint().height()
                  + self.btn_export.height()
                  + 3 * self.edit_layout.spacing() + 8)
        need = panel_h + chrome
        total = self.preview_splitter.height()
        if total > 0:
            # 最多让帧编辑块占满"整块 - 200"（至少给预览留 200px）；小窗口才出现压缩
            need = min(need, max(150, total - 200))
        return max(150, need)

    def _on_splitter_moved(self, *_args) -> None:
        """用户拖动了"预览 / 帧编辑"之间的分隔线：之后不再自动套用默认高度。"""
        self._user_split = True

    def _apply_default_split(self) -> None:
        """给上下两块设**默认**高度（用户自己拖过分隔线后就不再干预）。

        默认值只在"还没被用户改过"时生效，所以窗口最大化/还原都不会把内部控件
        拉乱；多出来的高度按拉伸因子全给上面的预览画布 —— 画布本来就自适应缩放，
        播放控制条、缩略图、帧编辑面板的高度都保持不变。
        """
        # 帧编辑块的最小高度跟着面板内容走（内容变多也不会被压扁）
        self.edit_box.setMinimumHeight(self._edit_min_height())
        if self._user_split:
            return
        total = self.preview_splitter.height()
        if total <= 0:
            return
        ctrl_h = self.ctrl_widget.sizeHint().height()
        thumb_h = self.thumb_list.height() or self.thumb_list.sizeHint().height()
        top = min(self.PREVIEW_CANVAS_H + ctrl_h + thumb_h + 8, max(120, total - 160))
        bottom = max(self.edit_box.minimumHeight(), total - top)
        self.preview_splitter.setSizes([int(top), int(bottom)])

    # ---- 对外接口 ----
    def set_actions(self, actions: list[Action]) -> None:
        """提供动作列表引用。"""
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
        had_action = self._action is not None
        self._action = action
        self.action_changed.emit(action)
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
            # 第一次从无动作进入有动作：把上下两块压回**默认**高度
            # （放开方形锁后 splitter 会按拉伸因子撑满，这里手动压回；
            #   之后用户拖过分隔线就不再覆盖）
            if not had_action:
                self._apply_default_split()
        self.adjust_panel.set_action(action)
        self.adjust_panel.set_current_frame(0)
        if action and action.frames:
            self._timer.setInterval(action.frame_duration_ms(0))
            self.slider.setRange(0, len(action.frames) - 1)
            self.slider.setValue(0)
            self._update_frame_label()
            self._populate_thumbs()
            self.toolbar.sync_action(action)
        else:
            self.slider.setRange(0, 0)
            self.lbl_frame.setText("0/0")
            self._populate_placeholder_thumbs()
            self._update_panel_title()

    def _populate_thumbs(self):
        self.thumb_list.clear()
        if not self._action:
            return
        for i in range(len(self._action.frames)):
            item = QListWidgetItem()
            _mark = None
            if self.similar_color_provider is not None:
                try:
                    _mark = self.similar_color_provider(self._action, i)
                except Exception:  # noqa: BLE001 — 标色失败绝不能影响缩略图重建
                    _mark = None
            item.setIcon(QIcon(build_frame_thumb(self._action, i, _mark)))
            item.setSizeHint(QSize(48, 48))
            self.thumb_list.addItem(item)
        # 重建后默认行 0；blockSignals 避免触发 currentRowChanged → _on_thumb_changed
        # （那会把 _frame_index 重置成 0，并在变换拖动中误触发切帧副作用）
        self.thumb_list.blockSignals(True)
        self.thumb_list.setCurrentRow(0)
        self.thumb_list.blockSignals(False)

    def _refresh_thumbs(self):
        """重建缩略图但保持当前选中帧与多选状态（旋转/镜像变化后同步）。

        之前只恢复了 currentRow（主帧）：一次变换触发重建后，Ctrl 多选的
        其余帧全部被 clear() 丢掉，导致"第一次变换生效、第二次只作用于主帧"。
        """
        if not self._action:
            return
        row = self.thumb_list.currentRow()
        selected = self._selected_rows()
        self._populate_thumbs()
        n = len(self._action.frames)
        if row >= 0:
            # 恢复选中帧是程序性操作：blockSignals 防止误触用户切帧逻辑
            self.thumb_list.blockSignals(True)
            self.thumb_list.setCurrentRow(min(row, n - 1))
            self.thumb_list.blockSignals(False)
        for i in selected:
            if i < n:
                self.thumb_list.item(i).setSelected(True)

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
        # 播放与变换互斥：正在变换就**暂停播放**，而不是把变换模式踢掉。
        # （老代码在这里 set_transform_mode(None)：那会顺手清掉视图锚定、把正在进行的
        #   拖动打断，播放中每 tick 闪一下 —— 实测一次 tick 就有 54% 像素在变。）
        canvas = getattr(self, "canvas", None)
        if canvas is not None and canvas.transform_mode():
            if self._playing:
                self.toggle_play()
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
        # 程序性换行后同步标题与叠加（延迟到事件循环，信号期间读 selection 会原生崩溃）
        self._on_thumb_selection_changed()
        # 逐帧时长：每切一帧，定时器按该帧自己的时长走
        if self._action and self._action.frames:
            self._timer.setInterval(self._action.frame_duration_ms(self._frame_index))
        self.frame_changed.emit(self._frame_index)

    def _on_slider_changed(self, val: int):
        # 用户拖滑杆切帧：自动退出变换模式（按钮弹回）
        if getattr(self, "canvas", None) is not None and self.canvas._transform_mode:
            self.canvas.set_transform_mode(None)
        self._frame_index = val
        self._sync_frame()

    # 帧序号用纯数字（逗号分隔），不再用带圈数字

    def _populate_placeholder_thumbs(self):
        """无动作时也显示帧描边占位框，不让缩略图区空着。"""
        self.thumb_list.clear()
        for _ in range(4):
            item = QListWidgetItem("")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            item.setSizeHint(QSize(48, 48))
            self.thumb_list.addItem(item)

    def _selected_rows(self) -> list[int]:
        """遍历 item.isSelected() 获取选中行（避免 selectedItems() 在部分平台原生崩溃）。"""
        return [i for i in range(self.thumb_list.count())
                if self.thumb_list.item(i).isSelected()]

    def _update_panel_title(self):
        """帧编辑标题：单选/多选都显示帧序号（如 帧编辑·1,2）。"""
        if not self._action or not self._action.frames:
            self.panel_title.setText("帧编辑·当前帧")
            return
        rows = sorted(self._selected_rows())
        if not rows:
            self.panel_title.setText("帧编辑·当前帧")
            return
        nums = ",".join(str(i + 1) for i in rows)
        self.panel_title.setText(f"帧编辑·{nums}")

    def _on_thumb_selection_changed(self):
        """Ctrl 多选：除主帧外的选中帧叠加显示在预览窗口。

        注意：不能在 itemSelectionChanged 信号处理期间直接读 selectedItems()，
        在部分平台（offscreen/Windows）会触发原生崩溃；延迟到事件循环里处理。
        """
        QTimer.singleShot(0, self._apply_selection)

    def _apply_selection(self):
        """事件循环里读取当前选择，更新标题与叠加帧。"""
        self._update_panel_title()
        if not self._action or not self._action.frames:
            return
        rows = sorted(self._selected_rows())
        main = self._frame_index
        self.canvas.set_overlay_frames([r for r in rows if r != main])

    def _on_thumb_changed(self, row: int):
        if not self._action or not self._action.frames:
            return
        if row < 0:
            return
        # Ctrl+点击：只切换选中状态，不切换主帧（主帧保持原帧，新帧作为叠加）
        modifiers = QApplication.keyboardModifiers()
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            return
        # 用户点击缩略图切帧 = 切换到别的编辑目标：自动退出变换模式（按钮弹回）
        if getattr(self, "canvas", None) is not None and self.canvas._transform_mode:
            self.canvas.set_transform_mode(None)
        self._frame_index = row
        self._sync_frame()

    # ---- 浮动工具条槽 ----
    def _on_bg_changed(self, mode: str):
        if self._action:
            self._action.bg_mode = mode
            self.canvas.update()

    def _on_canvas_frame_dragged(self, idx: int, dx: int, dy: int) -> None:
        """参照帧模式下画布拖动当前帧后，同步右侧偏移控件与标题。"""
        self.adjust_panel._sync_offset_widgets(dx, dy)
        self.adjust_panel._refresh_canvas_label()
        self.adjust_panel.changed.emit()

    def _on_adjust_changed(self):
        if self._action:
            # 帧速/四周扩展的跨动作同步改为「动画设置」横条上的显式一次性按钮，
            # 单帧编辑不再隐式联动其它动作。
            self._timer.setInterval(self._action.frame_duration_ms(self._frame_index))
            self.canvas._update_scroll_timer()
            if self.canvas._fit and not self.canvas._drag_kind and not self.canvas.view_anchored():
                # 拖动变换手柄期间保持视图缩放稳定，避免每帧重新自适应导致跳动；
                # 视图锚定生效时（拖动中及松手后）同样不重算，否则整屏会重新缩放。
                self.canvas._apply_fit()
            self.canvas.update()
            if self.canvas._drag_kind is not None:
                # 变换手柄拖动中 _refresh_canvas_label 会 compute_preview_layout
                # 对全部帧做精算（每帧透明裁剪），仅为刷新一个尺寸文本，每帧跑会
                # 拖垮交互；这里只标脏，松手(_on_transform_edit_finished)再补刷。
                # 注意：整体平移走 _frame_drag_active 而非 _drag_kind，不受影响，
                # 偏移数值仍实时跟随。
                self._label_dirty = True
            else:
                self.adjust_panel._refresh_canvas_label()
            # 旋转/镜像/四边形变化后同步帧缩略图。但拖动手柄期间每次 mouseMove
            # 都会走到这里，重建全部缩略图（每帧全分辨率精算+透明裁剪，18 帧约
            # 上百 ms）会把拖动卡死；拖动中只标脏、松手(_on_transform_edit_finished)
            # 后补刷一次即可。
            if getattr(self.adjust_panel, "_transform_changed", False):
                self.adjust_panel._transform_changed = False
                if self.canvas._drag_kind is not None:
                    self._thumbs_dirty = True
                else:
                    self._refresh_thumbs()

    def _on_canvas_transform_mode(self, mode: str) -> None:
        """画布进入变换模式 → 暂停播放。

        播放每 tick 会切帧（`set_frame` 会清掉视图锚定），而拖动中的变换一旦被打断，
        画面每 tick 闪一次（实测一次 tick 就有 54% 像素变化）。与其让播放拆掉编辑，
        不如进编辑就停播。
        """
        if mode and self._playing:
            self.toggle_play()

    def _on_transform_edit_finished(self) -> None:
        """变换手柄松手：补刷拖动期间挂起的帧缩略图与画布尺寸标签。"""
        if getattr(self, "_thumbs_dirty", False):
            self._thumbs_dirty = False
            self._refresh_thumbs()
        if getattr(self, "_label_dirty", False):
            self._label_dirty = False
            self.adjust_panel._refresh_canvas_label()

    def _on_export_clicked(self) -> None:
        # 导出产物以当前标签页的工程名开头（也用于"超过两个文件就装进同名文件夹"）
        project_name = getattr(self, "_project_name_provider", None)
        dlg = ExportDialog(
            self._actions,
            self._source_pixmap,
            # 工程数据提供者由 MainWindow 注入（self._project_manifest 本类没有定义）
            project_provider=getattr(self, "_project_manifest", None),
            project_name=project_name() if callable(project_name) else "",
            parent=self,
        )
        dlg.exec()
