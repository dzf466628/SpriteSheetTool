#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""动画设置横条（原图区下方）：作用于**整个动作**的参数。

与右侧「帧编辑」面板的分工：
- 本横条 = 动作级（一个动作一个值，与当前帧无关）：动作类型/移动参数、
  四周扩展、循环模式、锚点准星、地面线；
- 右侧帧编辑 = 帧级（按帧号存储）：变换、偏移、本帧时长（单帧覆盖）、参照帧/透明度。
  「谁最后操作听谁的」：横条调帧速 = 全部帧统一（清空逐帧时长差异）；右侧改本帧时长 = 只覆盖当前帧。

数据接口与原 AnimAdjustPanel 相同：``set_action(action)`` 同步控件，
``set_actions(actions)`` 提供动作列表（批量应用用），任何修改发 ``changed``。

紧凑度：默认窗口（1000 宽）左列约 520px，移动类型第一行内容最多，因此
速度滑块设 40~140 可伸缩、类型/方向去掉文字标签（按钮自解释 + tooltip）。
"""
from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup, QCheckBox, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QPushButton, QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

from .models import Action
from .widgets import CompactSpinBox, HoverIconButton, _SLIDER_QSS, make_tool_icon

# QSS 的 :disabled 对 QSlider 子控件 handle 不生效，禁用时手动切换到灰色样式
_SLIDER_QSS_GRAY = _SLIDER_QSS.replace("#E91E63", "#555555") \
    .replace("#F06292", "#666666") \
    .replace("background: #3d3d3d;", "background: #2a2a2a;")

# 循环模式 -> (图标, 中文名)
LOOP_ICONS = {
    Action.LOOP: ("loop", "循环播放"),
    Action.ONCE: ("once", "单次播放"),
    Action.PINGPONG: ("pingpong", "往返播放"),
}

_BAR_QSS = (
    "QWidget#AnimSettingsBar { background-color: #1f1f1f; border-top: 1px solid #333333; }"
    "QLabel { color: #bdbdbd; font-size: 11px; border: none; background: transparent; }"
    "QPushButton { background-color: #2b2b2b; border: 1px solid #3d3d3d; border-radius: 3px;"
    " color: #e8e8e8; font-size: 11px; padding: 2px 8px; }"
    "QPushButton:hover { background-color: #383838; border-color: #F06292; }"
    "QPushButton:checked { background-color: #E91E63; border-color: #E91E63; color: #ffffff; }"
    "QPushButton:disabled { color: #666666; border-color: #2b2b2b; background-color: #262626; }"
    "QCheckBox { color: #bdbdbd; font-size: 11px; spacing: 5px; background: transparent; }"
    "QCheckBox::indicator { width: 13px; height: 13px; border: 1px solid #555555;"
    " border-radius: 2px; background: #2b2b2b; }"
    "QCheckBox::indicator:hover { border-color: #F06292; }"
    "QCheckBox::indicator:checked { background: #E91E63; border-color: #E91E63; }"
    "QCheckBox:disabled { color: #666666; }"
    "QSpinBox { background-color: #2b2b2b; border: 1px solid #3d3d3d; border-radius: 3px;"
    " color: #e8e8e8; font-size: 11px; padding: 1px 4px; selection-background-color: #E91E63; }"
    "QSpinBox:disabled { color: #666666; background-color: #262626; border-color: #2b2b2b; }"
    "QSpinBox::up-button, QSpinBox::down-button { width: 16px; background-color: #353535; border: none; }"
    "QSpinBox::up-button:hover, QSpinBox::down-button:hover { background-color: #E91E63; }"
    "QSpinBox::up-arrow { image: none; border-left: 4px solid transparent;"
    " border-right: 4px solid transparent; border-bottom: 5px solid #e8e8e8; }"
    "QSpinBox::down-arrow { image: none; border-left: 4px solid transparent;"
    " border-right: 4px solid transparent; border-top: 5px solid #e8e8e8; }"
)

_ICON_BTN_QSS = (
    "QPushButton { background: #2b2b2b; border: 1px solid #3d3d3d; border-radius: 3px; }"
    "QPushButton:hover { background: #3a3a3a; border-color: #E91E63; }"
    "QPushButton:checked { background: #E91E63; border-color: #E91E63; }"
    "QPushButton:disabled { background: #262626; border-color: #2b2b2b; }"
)


class AnimSettingsBar(QWidget):
    """原图区下方的整段动画设置条（动画设置 / 变换 / 微调 三个分区）。"""

    changed = pyqtSignal()
    mode_requested = pyqtSignal(str)  # 全部帧变换模式请求（"" = 退出）

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("AnimSettingsBar")
        # 父窗口合成时普通 QWidget 的 QSS 背景不会自动重绘，必须显式开 StyledBackground，
        # 否则横条在主窗口里透出父容器的浅色底（独立 grab 正常、嵌进主窗口发白）。
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # 高度自适应内容（动作级两行 + 变换/微调两行），不再固定 60
        self.setMinimumHeight(186)
        self.setStyleSheet(_BAR_QSS)
        self._action: Action | None = None
        self._actions: list = []
        self._loop_mode = Action.LOOP
        self._all_dx = 0  # 全部帧统一偏移的当前值（显示与方向按钮的起点）
        self._all_dy = 0
        self._build_ui()
        self.set_action(None)

    # ------------------------------------------------------------------ UI
    def _icon_btn(self, tip: str, icon_kind: str | None = None,
                  checkable: bool = False, size: int = 24) -> QPushButton:
        btn = QPushButton()
        btn.setFixedSize(size, 24)
        btn.setToolTip(tip)
        btn.setCheckable(checkable)
        btn.setStyleSheet(_ICON_BTN_QSS)
        if icon_kind:
            btn.setIcon(make_tool_icon(icon_kind, 14))
            btn.setIconSize(QSize(14, 14))
        return btn

    @staticmethod
    def _section_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color: #8c8c8c;")
        return lbl


    @staticmethod
    def _vline() -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFixedWidth(1)
        line.setStyleSheet("color: #333333; background: #333333; border: none;")
        return line

    @staticmethod
    def _slider(value: int, low: int, high: int,
                max_width: int | None = 140) -> QSlider:
        """可伸缩滑块：窄屏最短 40px，宽屏最长 140px，中间随布局拉伸。"""
        s = QSlider(Qt.Orientation.Horizontal)
        s.setRange(low, high)
        s.setValue(value)
        s.setMinimumWidth(40)
        if max_width is not None:
            s.setMaximumWidth(max_width)
        s.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        s.setStyleSheet(_SLIDER_QSS)
        return s

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 4, 6, 4)
        outer.setSpacing(4)

        # 顶部标题栏：20px 高、与工具框等宽；红字左对齐
        self._bar_title = QLabel("动画编辑·全部帧")
        self._bar_title.setFixedHeight(20)
        self._bar_title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._bar_title.setStyleSheet(
            "background: #262626; color: #E91E63; font-size: 12px; padding-left: 8px; border-radius: 3px;")
        outer.addWidget(self._bar_title)

        cols = QHBoxLayout()
        cols.setSpacing(6)

        # ================= 左列分区：动画设置 =================
        self._group_anim = QGroupBox("动画设置")
        g1 = QVBoxLayout(self._group_anim)
        g1.setContentsMargins(8, 4, 8, 2)
        g1.setSpacing(4)

        # 动作类型（按钮文字已自解释，不加"类型"标签）
        self.btn_type_static = QPushButton("静止")
        self.btn_type_move = QPushButton("移动")
        for b in (self.btn_type_static, self.btn_type_move):
            b.setCheckable(True)
            b.setFixedSize(40, 24)
        self._type_group = QButtonGroup(self)
        self._type_group.addButton(self.btn_type_static)
        self._type_group.addButton(self.btn_type_move)
        self.btn_type_static.setChecked(True)
        self.btn_type_static.setToolTip("静止动作：帧在原地播放")
        self.btn_type_move.setToolTip("移动动作：帧按方向与速度整体位移")
        self.btn_type_static.clicked.connect(self._on_type_changed)
        self.btn_type_move.clicked.connect(self._on_type_changed)
        l_type = QHBoxLayout()
        l_type.setContentsMargins(0, 0, 0, 0)
        l_type.setSpacing(4)
        l_type.addWidget(self.btn_type_static)
        l_type.addWidget(self.btn_type_move)
        # 移动方向：放到「移动」右边
        self.dir_widget = QWidget()
        dir_layout = QHBoxLayout(self.dir_widget)
        dir_layout.setContentsMargins(0, 0, 0, 0)
        dir_layout.setSpacing(3)
        self._dir_group = QButtonGroup(self)
        self.btn_dir_up = self._icon_btn("向上移动", "arrow_up", checkable=True, size=22)
        self.btn_dir_down = self._icon_btn("向下移动", "arrow_down", checkable=True, size=22)
        self.btn_dir_left = self._icon_btn("向左移动", "arrow_left", checkable=True, size=22)
        self.btn_dir_right = self._icon_btn("向右移动", "arrow_right", checkable=True, size=22)
        for b in (self.btn_dir_up, self.btn_dir_down, self.btn_dir_left, self.btn_dir_right):
            self._dir_group.addButton(b)
            dir_layout.addWidget(b)
        self.btn_dir_right.setChecked(True)
        self.btn_dir_up.clicked.connect(lambda: self._set_direction("up"))
        self.btn_dir_down.clicked.connect(lambda: self._set_direction("down"))
        self.btn_dir_left.clicked.connect(lambda: self._set_direction("left"))
        self.btn_dir_right.clicked.connect(lambda: self._set_direction("right"))
        l_type.addWidget(self.dir_widget)
        l_type.addStretch(1)
        g1.addLayout(l_type)

        # 速度：单独一行（方向下面）
        self.speed_widget = QWidget()
        self.speed_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        speed_layout = QHBoxLayout(self.speed_widget)
        speed_layout.setContentsMargins(0, 0, 0, 0)
        speed_layout.setSpacing(2)
        lbl_speed = self._section_label("速度")
        lbl_speed.setFixedWidth(24)
        lbl_speed.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        speed_layout.addWidget(lbl_speed)
        self.slider_speed = self._slider(10, 1, 100, max_width=None)
        self.slider_speed.setFixedWidth(137)
        self.lbl_speed_value = QLabel("10")
        self.lbl_speed_value.setFixedWidth(20)
        self.lbl_speed_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.slider_speed.valueChanged.connect(self._on_speed_changed)
        self.slider_speed.valueChanged.connect(lambda v: self.lbl_speed_value.setText(str(v)))
        speed_layout.addWidget(self.slider_speed)
        speed_layout.addWidget(self.lbl_speed_value)

        l_speed = QHBoxLayout()
        l_speed.setContentsMargins(0, 0, 0, 0)
        l_speed.setSpacing(4)
        l_speed.addWidget(self.speed_widget, 1)
        g1.addLayout(l_speed)

        # 锚点 + 地面线（锚点放到扩展左边；行间距 3，使地面线数字右缘与上方「右」方向按钮对齐）
        l_anchor = QHBoxLayout()
        l_anchor.setContentsMargins(0, 0, 0, 0)
        l_anchor.setSpacing(3)
        l_anchor.addWidget(self._section_label("锚点"))
        self.btn_anchor = self._icon_btn("锚点准星显示开关", "anchor", checkable=True, size=22)
        self.btn_anchor.clicked.connect(self._emit)
        l_anchor.addWidget(self.btn_anchor)
        vline = self._vline()
        vline.setFixedHeight(16)
        l_anchor.addWidget(vline, 0, Qt.AlignmentFlag.AlignVCenter)
        self.chk_ground = QCheckBox("地面线")
        self.chk_ground.stateChanged.connect(self._on_ground_toggled)
        l_anchor.addWidget(self.chk_ground)
        self.btn_ground_up = self._icon_btn("地面线上移 1px", "arrow_up", size=22)
        self.btn_ground_down = self._icon_btn("地面线下移 1px", "arrow_down", size=22)
        self.btn_ground_up.clicked.connect(lambda: self._adjust_ground(1))
        self.btn_ground_down.clicked.connect(lambda: self._adjust_ground(-1))
        l_anchor.addWidget(self.btn_ground_up)
        l_anchor.addWidget(self.btn_ground_down)
        self.lbl_ground_offset = QLabel("0")
        self.lbl_ground_offset.setFixedWidth(25)
        self.lbl_ground_offset.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        l_anchor.addWidget(self.lbl_ground_offset)
        l_anchor.addStretch(1)
        g1.addLayout(l_anchor)

        # 四周扩展
        l_pad = QHBoxLayout()
        l_pad.setContentsMargins(0, 0, 0, 0)
        l_pad.setSpacing(4)
        l_pad.addWidget(self._section_label("扩展"))
        self.spin_padding = CompactSpinBox()
        self.spin_padding.setRange(0, 200)
        self.spin_padding.setValue(0)
        self.spin_padding.setFixedWidth(44)
        self.spin_padding.setFixedHeight(24)
        self.spin_padding.setToolTip("四周扩展（px）：每帧画布四边统一留白")
        self.spin_padding.valueChanged.connect(self._on_padding_changed)
        l_pad.addWidget(self.spin_padding)
        l_pad.addStretch(1)
        g1.addLayout(l_pad)

        # 循环 + 帧速并入「扩展」一行：扩展靠左，循环 + 帧速靠右
        l_pad.setSpacing(3)
        l_pad.addStretch(1)
        l_pad.addWidget(self._section_label("循环"))
        self.btn_loop = self._icon_btn("循环模式：循环播放（点击切换）", "loop")
        self.btn_loop.clicked.connect(self._cycle_loop)
        l_pad.addWidget(self.btn_loop)
        l_pad.addWidget(self._section_label("帧速"))
        self.spin_fps = CompactSpinBox()
        self.spin_fps.setRange(1, 60)
        self.spin_fps.setValue(10)
        self.spin_fps.setSuffix(" FPS")
        self.spin_fps.setFixedWidth(56)
        self.spin_fps.setFixedHeight(24)
        self.spin_fps.setToolTip("整体帧速（帧/秒）：作用于本动作全部帧，同时清除逐帧时长差异")
        self.spin_fps.valueChanged.connect(self._on_fps_changed)
        l_pad.addWidget(self.spin_fps)

        cols.addWidget(self._group_anim, 5)

        # ================= 中列分区：变换（全部帧） =================
        self._group_transform = QGroupBox("变换")
        g2 = QVBoxLayout(self._group_transform)
        g2.setContentsMargins(8, 4, 8, 2)
        g2.setSpacing(4)
        self._all_mode_group = QButtonGroup(self)
        self._all_mode_group.setExclusive(True)
        self._all_mode_buttons: dict[str, QPushButton] = {}
        mode_grid = QGridLayout()
        mode_grid.setContentsMargins(0, 0, 0, 0)
        mode_grid.setSpacing(4)
        # 一行两个按钮，按钮带符号图标 + 文字
        for idx, (key, text, icon) in enumerate((("rotate", "旋转", "rotate"),
                                                  ("scale", "缩放", "scale"),
                                                  ("skew", "斜切", "skew"),
                                                  ("perspective", "透视", "perspective"),
                                                  ("distort", "扭曲", "distort"))):
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setFixedHeight(24)
            btn.setFixedWidth(70)
            btn.setIcon(make_tool_icon(icon, 14))
            btn.setIconSize(QSize(14, 14))
            btn.setToolTip(f"{text}：作用于本动作全部帧（点后在预览画布上拖动）")
            self._all_mode_group.addButton(btn)
            self._all_mode_buttons[key] = btn
            btn.clicked.connect(lambda _checked, k=key: self._on_all_mode(k))
            mode_grid.addWidget(btn, idx // 2, idx % 2)
        g2.addLayout(mode_grid)
        # 镜像 + 清空（一行）
        l_mirror = QHBoxLayout()
        l_mirror.setContentsMargins(0, 0, 0, 0)
        l_mirror.setSpacing(4)
        self.btn_all_mirror_h = QPushButton("镜像H")
        self.btn_all_mirror_v = QPushButton("镜像V")
        self.btn_all_mirror_h.setIcon(make_tool_icon("mirror_h", 14))
        self.btn_all_mirror_h.setIconSize(QSize(14, 14))
        self.btn_all_mirror_v.setIcon(make_tool_icon("mirror_v", 14))
        self.btn_all_mirror_v.setIconSize(QSize(14, 14))
        for b in (self.btn_all_mirror_h, self.btn_all_mirror_v):
            b.setFixedHeight(24)
            b.setFixedWidth(70)
            b.setToolTip("作用于本动作全部帧")
        self.btn_all_mirror_h.setCheckable(True)
        self.btn_all_mirror_v.setCheckable(True)
        self.btn_all_mirror_h.clicked.connect(lambda: self._on_all_mirror("h"))
        self.btn_all_mirror_v.clicked.connect(lambda: self._on_all_mirror("v"))
        # 清空变换：照右侧帧编辑的样式（红色描边 + 红色图标 + 红色文字），
        # 放到「扭曲」右边（一行两个）
        self.btn_all_clear = HoverIconButton("清空变换")
        self.btn_all_clear.setFixedHeight(24)
        self.btn_all_clear.setFixedWidth(70)
        self.btn_all_clear.setToolTip("清空本动作全部帧的变换（帧偏移不动）")
        self.btn_all_clear.set_state_icons(
            make_tool_icon("reset", 12, color="#E91E63"),
            make_tool_icon("reset", 12, color="#ffffff"))
        self.btn_all_clear.setIconSize(QSize(12, 12))
        self.btn_all_clear.setStyleSheet(
            "QPushButton { background: #2b2b2b; border: 1px solid #E91E63; border-radius: 3px;"
            " color: #E91E63; font-size: 9px; }"
            "QPushButton:hover { background: #E91E63; border-color: #E91E63; color: #ffffff; }"
            "QPushButton:pressed { background: #C2185B; border-color: #C2185B; color: #ffffff; }"
        )
        self.btn_all_clear.clicked.connect(self._on_all_clear)
        mode_grid.addWidget(self.btn_all_clear, 2, 1)
        l_mirror.addWidget(self.btn_all_mirror_h)
        l_mirror.addWidget(self.btn_all_mirror_v)
        l_mirror.addStretch(1)
        g2.addLayout(l_mirror)
        cols.addWidget(self._group_transform, 3)

        # ================= 右列分区：微调（全部帧） =================
        self._group_offset = QGroupBox("微调")
        g3 = QVBoxLayout(self._group_offset)
        g3.setContentsMargins(8, 4, 8, 2)
        g3.setSpacing(4)
        self.btn_all_up = self._icon_btn("全部帧上移1px", "arrow_up")
        self.btn_all_down = self._icon_btn("全部帧下移1px", "arrow_down")
        self.btn_all_left = self._icon_btn("全部帧左移1px", "arrow_left")
        self.btn_all_right = self._icon_btn("全部帧右移1px", "arrow_right")
        self.btn_all_offset_reset = self._icon_btn("全部帧偏移归零", "reset")
        for b in (self.btn_all_up, self.btn_all_down, self.btn_all_left,
                  self.btn_all_right, self.btn_all_offset_reset):
            b.clicked.connect(self._on_all_offset_clicked)
        # 十字方向键（照右侧帧编辑的微调样式，左右紧贴中间）
        offset_grid = QGridLayout()
        offset_grid.setContentsMargins(0, 0, 0, 0)
        offset_grid.setSpacing(2)
        offset_grid.addWidget(self.btn_all_up, 0, 1)
        offset_grid.addWidget(self.btn_all_left, 1, 0)
        offset_grid.addWidget(self.btn_all_offset_reset, 1, 1)
        offset_grid.addWidget(self.btn_all_right, 1, 2)
        offset_grid.addWidget(self.btn_all_down, 2, 1)
        g3.addLayout(offset_grid)
        self.spin_all_off_x = CompactSpinBox()
        self.spin_all_off_x.setRange(-9999, 9999)
        self.spin_all_off_x.setFixedSize(58, 24)
        self.spin_all_off_x.setToolTip("全部帧统一水平偏移")
        self.spin_all_off_x.valueChanged.connect(self._on_all_offset_x_changed)
        self.slider_all_off_x = self._slider(0, -200, 200)
        self.slider_all_off_x.setToolTip("全部帧统一水平偏移（大幅调节）")
        self.slider_all_off_x.valueChanged.connect(self._on_all_offset_x_changed)
        self.spin_all_off_y = CompactSpinBox()
        self.spin_all_off_y.setRange(-9999, 9999)
        self.spin_all_off_y.setFixedSize(58, 24)
        self.spin_all_off_y.setToolTip("全部帧统一垂直偏移")
        self.spin_all_off_y.valueChanged.connect(self._on_all_offset_y_changed)
        self.slider_all_off_y = self._slider(0, -200, 200)
        self.slider_all_off_y.setToolTip("全部帧统一垂直偏移（大幅调节）")
        self.slider_all_off_y.valueChanged.connect(self._on_all_offset_y_changed)
        l_x = QHBoxLayout()
        l_x.setContentsMargins(0, 0, 0, 0)
        l_x.setSpacing(3)
        l_x.addWidget(self._section_label("X"))
        l_x.addWidget(self.spin_all_off_x)
        l_x.addWidget(self.slider_all_off_x, 1)
        g3.addLayout(l_x)
        l_y = QHBoxLayout()
        l_y.setContentsMargins(0, 0, 0, 0)
        l_y.setSpacing(3)
        l_y.addWidget(self._section_label("Y"))
        l_y.addWidget(self.spin_all_off_y)
        l_y.addWidget(self.slider_all_off_y, 1)
        g3.addLayout(l_y)
        cols.addWidget(self._group_offset, 3)

        outer.addLayout(cols)

        # 当前变换摘要：放进「变换（全部帧）」分组框底部
        self.lbl_transform_info = QLabel("无变换")
        self.lbl_transform_info.setStyleSheet("color: #8c8c8c; font-size: 10px;"
                                              " padding: 1px 2px;")
        self.lbl_transform_info.setWordWrap(True)
        self.lbl_transform_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_transform_info.setToolTip("当前动作全部帧的变换摘要（旋转 / 镜像 / 偏移 / 自由变换）")
        g2.addWidget(self.lbl_transform_info, 0, Qt.AlignmentFlag.AlignHCenter)

    def _update_transform_info(self) -> None:
        """底部摘要：当前动作全部帧的变换状态。"""
        a = self._action
        if a is None:
            self.lbl_transform_info.setText("无变换")
            return
        n = max(len(a.frames), 1)
        parts: list[str] = []
        rots = a.frame_rotations or {}
        if rots:
            vals = {round(float(v), 1) for v in rots.values()}
            if len(vals) == 1 and len(rots) == n:
                parts.append(f"旋转 {next(iter(vals))}°")
            else:
                parts.append("旋转 逐帧")
        if a.frame_mirror_h:
            parts.append("镜像H")
        if a.frame_mirror_v:
            parts.append("镜像V")
        if a.frame_quads:
            parts.append("自由变换")
        offs = a.frame_offsets or {}
        if offs:
            vals = set(offs.values())
            if len(vals) == 1 and len(offs) == n:
                dx, dy = next(iter(vals))
                parts.append(f"偏移 ({dx}, {dy})")
            else:
                parts.append("偏移 逐帧")
        self.lbl_transform_info.setText(" · ".join(parts) if parts else "无变换")

    # -------------------------------------------------------------- 数据同步
    def set_actions(self, actions: list) -> None:
        self._actions = actions

    def set_action(self, action: Action | None) -> None:
        self._action = action
        enabled = action is not None
        for w in self.findChildren(QWidget):
            w.setEnabled(enabled)
        if action is None:
            self._set_move_controls_enabled(False)
            self._update_transform_info()
            return

        self.spin_padding.blockSignals(True)
        self.spin_padding.setValue(action.padding)
        self.spin_padding.blockSignals(False)

        self.spin_fps.blockSignals(True)
        self.spin_fps.setValue(action.fps)
        self.spin_fps.blockSignals(False)

        self.chk_ground.blockSignals(True)
        self.chk_ground.setChecked(action.ground_enabled)
        self.chk_ground.blockSignals(False)
        self.lbl_ground_offset.setText(str(action.ground_offset))

        self.btn_anchor.blockSignals(True)
        self.btn_anchor.setChecked(action.show_anchor)
        self.btn_anchor.blockSignals(False)

        # 全部帧镜像按钮：所有帧都镜像时才勾选
        all_h = all(i in self._action.frame_mirror_h for i in range(len(self._action.frames)))
        self.btn_all_mirror_h.blockSignals(True)
        self.btn_all_mirror_h.setChecked(all_h)
        self.btn_all_mirror_h.blockSignals(False)
        all_v = all(i in self._action.frame_mirror_v for i in range(len(self._action.frames)))
        self.btn_all_mirror_v.blockSignals(True)
        self.btn_all_mirror_v.setChecked(all_v)
        self.btn_all_mirror_v.blockSignals(False)
        self._sync_all_offset(0, 0)
        self._update_transform_info()

        self._loop_mode = action.loop_mode
        icon_kind, tip = LOOP_ICONS[action.loop_mode]
        self.btn_loop.setIcon(make_tool_icon(icon_kind, 14))
        self.btn_loop.setToolTip(f"循环模式：{tip}（点击切换）")

        # 方向键 / 速度控件始终显示；静止时置灰不可点，切「移动」才可点
        if action.action_type == "move":
            self.btn_type_move.setChecked(True)
            self._set_move_controls_enabled(True)
        else:
            self.btn_type_static.setChecked(True)
            self._set_move_controls_enabled(False)
        dir_map = {"up": self.btn_dir_up, "down": self.btn_dir_down,
                   "left": self.btn_dir_left, "right": self.btn_dir_right}
        dir_map.get(action.move_direction, self.btn_dir_right).setChecked(True)
        self.slider_speed.blockSignals(True)
        self.slider_speed.setValue(action.move_speed)
        self.slider_speed.blockSignals(False)
        self.lbl_speed_value.setText(str(action.move_speed))

    # -------------------------------------------------------------- 回调
    def _emit(self) -> None:
        if self._action is not None:
            # 锚点按钮的 checked 状态写回动作
            self._action.show_anchor = self.btn_anchor.isChecked()
            self.changed.emit()

    def _on_type_changed(self) -> None:
        if not self._action:
            return
        if self.btn_type_move.isChecked():
            self._action.action_type = "move"
            self._set_move_controls_enabled(True)
        else:
            self._action.action_type = "static"
            self._set_move_controls_enabled(False)
        self.changed.emit()

    def _set_move_controls_enabled(self, enabled: bool) -> None:
        """移动控件（方向键 + 速度滑块）：一直显示；静止置灰、移动可点。"""
        self.dir_widget.setEnabled(enabled)
        self.speed_widget.setEnabled(enabled)
        self.slider_speed.setStyleSheet(_SLIDER_QSS if enabled else _SLIDER_QSS_GRAY)

    def _set_direction(self, direction: str) -> None:
        if self._action:
            self._action.move_direction = direction
            self.changed.emit()

    def _on_speed_changed(self, val: int) -> None:
        if self._action:
            self._action.move_speed = val
            self.changed.emit()

    def _on_padding_changed(self, val: int) -> None:
        if self._action:
            self._action.padding = val
            self.changed.emit()

    def _on_ground_toggled(self, state: int) -> None:
        if self._action:
            self._action.ground_enabled = state == 2
            self.changed.emit()

    def _adjust_ground(self, delta: int) -> None:
        if self._action:
            self._action.ground_offset += delta
            self.lbl_ground_offset.setText(str(self._action.ground_offset))
            self.changed.emit()

    def _on_fps_changed(self, val: int) -> None:
        """整体帧速：全部帧统一（清空逐帧时长差异）。谁最后操作听谁的。"""
        if self._action:
            self._action.fps = max(1, int(val))
            self._action.frame_durations.clear()
            self.changed.emit()

    def set_mode(self, mode: str) -> None:
        """画布变换模式变化（主窗口从预览画布收到）→ 同步本横条按钮。"""
        self._all_mode_group.setExclusive(False)
        for key, btn in self._all_mode_buttons.items():
            btn.blockSignals(True)
            btn.setChecked(key == mode)
            btn.blockSignals(False)
        self._all_mode_group.setExclusive(True)

    def set_current_frame(self, idx: int) -> None:
        """当前帧切换（主窗口从预览收到）→ 同步全部帧偏移的初始显示值。"""
        if self._action and 0 <= idx < len(self._action.frames):
            dx, dy = self._action.frame_offsets.get(idx, (0, 0))
        else:
            dx = dy = 0
        self._sync_all_offset(dx, dy)
        self._update_transform_info()

    def _sync_all_offset(self, dx: int, dy: int) -> None:
        self._all_dx, self._all_dy = int(dx), int(dy)
        for w, v in ((self.spin_all_off_x, dx), (self.spin_all_off_y, dy)):
            w.blockSignals(True)
            w.setValue(int(v))
            w.blockSignals(False)
        for w, v in ((self.slider_all_off_x, dx), (self.slider_all_off_y, dy)):
            w.blockSignals(True)
            w.setValue(max(w.minimum(), min(w.maximum(), int(v))))
            w.blockSignals(False)

    def _on_all_mode(self, key: str) -> None:
        """全部帧变换模式按钮：发信号由主窗口交给预览画布（作用于全部帧）。"""
        btn = self._all_mode_buttons.get(key)
        if btn is None:
            return
        self.mode_requested.emit(key if btn.isChecked() else "")

    def _on_all_mirror(self, axis: str) -> None:
        """镜像全部帧：按钮勾选 = 所有帧镜像，取消 = 所有帧解除。"""
        if not self._action:
            return
        target = self._action.frame_mirror_h if axis == "h" else self._action.frame_mirror_v
        btn = self.btn_all_mirror_h if axis == "h" else self.btn_all_mirror_v
        n = len(self._action.frames)
        if btn.isChecked():
            target.update(range(n))
        else:
            for i in range(n):
                target.discard(i)
        self._action.invalidate_transforms()
        self.changed.emit()
        self._update_transform_info()

    def _on_all_clear(self) -> None:
        """清空全部帧变换：旋转 / 镜像 / 透视四边形 / 旋转中心。"""
        if not self._action:
            return
        for i in range(len(self._action.frames)):
            self._action.frame_rotations.pop(i, None)
            self._action.frame_rotation_pivots.pop(i, None)
            self._action.frame_quads.pop(i, None)
            self._action.frame_transform_modes.pop(i, None)
            self._action.frame_mirror_h.discard(i)
            self._action.frame_mirror_v.discard(i)
        self._action.invalidate_transforms()
        self.changed.emit()
        self._update_transform_info()

    def _on_all_offset_clicked(self) -> None:
        """方向/重置按钮：按发送者把统一偏移值微调 1px 或归零。"""
        if not self._action:
            return
        sender = self.sender()
        dx, dy = 0, 0
        if sender is self.btn_all_up:
            dy = -1
        elif sender is self.btn_all_down:
            dy = 1
        elif sender is self.btn_all_left:
            dx = -1
        elif sender is self.btn_all_right:
            dx = 1
        elif sender is self.btn_all_offset_reset:
            self._set_all_offset(0, 0)
            return
        self._set_all_offset(self._all_dx + dx, self._all_dy + dy)

    def _on_all_offset_x_changed(self, val: int) -> None:
        self._set_all_offset(val, self._all_dy)

    def _on_all_offset_y_changed(self, val: int) -> None:
        self._set_all_offset(self._all_dx, val)

    def _set_all_offset(self, dx: int, dy: int) -> None:
        """全部帧统一偏移：写入每一帧（谁最后操作听谁的，底部 = 全部帧赢）。"""
        if not self._action:
            return
        n = len(self._action.frames)
        if dx == 0 and dy == 0:
            for i in range(n):
                self._action.frame_offsets.pop(i, None)
        else:
            for i in range(n):
                self._action.frame_offsets[i] = (int(dx), int(dy))
        self._sync_all_offset(dx, dy)
        self.changed.emit()
        self._update_transform_info()

    def _cycle_loop(self) -> None:
        if not self._action:
            return
        order = [Action.LOOP, Action.ONCE, Action.PINGPONG]
        idx = order.index(self._loop_mode)
        self._loop_mode = order[(idx + 1) % len(order)]
        self._action.loop_mode = self._loop_mode
        icon_kind, tip = LOOP_ICONS[self._loop_mode]
        self.btn_loop.setIcon(make_tool_icon(icon_kind, 14))
        self.btn_loop.setToolTip(f"循环模式：{tip}（点击切换）")
        self.changed.emit()

