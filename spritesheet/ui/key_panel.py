"""抠除背景的参数浮层：浮在原图区画布上，参数一改就实时预览。

单独一个模块的理由：这面板自己有十几个控件 + 防抖 + 预览计算，塞进 image_viewer.py
只会让那个 2000 多行的文件更难读；算法全在 ``spritesheet/keying.py``（纯 Pillow）。

三个体验上的决定（都有实测依据）：

1. **拖动滑块只算"快速预览"（跳过去色溢），松手 350ms 后再补一次完整效果。**
   2048² 实测去色溢占全程 2/3（370ms / 546ms），而它只影响边缘那圈的颜色 ——
   拖动过程中没必要每次都算。
2. **预览不动 ``viewer._pixmap``，只挂 ``viewer._preview_pixmap``。** 撤销栈、帧、
   动作全程原封不动，"取消"就只是把预览清掉，零副作用；点"应用"才真正落地。
   这么做还有一个必须的理由：主界面快照存的是 ``_pixmap`` 的**引用**，预览期间
   反复替换它会让撤销记录错乱。
3. **参数控件由 ``keying.PARAM_SPEC`` 生成**，不在这里手写一遍 —— 以后算法加参数，
   界面自动跟上，不会出现"算法改了界面没跟上"。
"""
from __future__ import annotations

import weakref

from PIL import Image, ImageChops
from PyQt6.QtCore import QEvent, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (QButtonGroup, QCheckBox, QHBoxLayout, QLabel,
                             QPushButton, QSlider, QVBoxLayout, QWidget)

from spritesheet import keying

# 面板内按钮统一外观：深底细边 + **悬浮/按下反馈**（用户要求"按钮要添加悬浮动效"）。
# 主题里虽然也有 hover，但作用范围按钮自己带 QSS，会把主题那份盖掉一半，
# 干脆在这里给面板所有按钮一套统一样式，免得"有的有反馈、有的没有"。
BUTTON_QSS = (
    "QPushButton { background: #333333; border: 1px solid #484848; border-radius: 4px;"
    " color: #e6e6e6; font-size: 11px; padding: 0 8px; }"
    "QPushButton:hover { background: #434343; border-color: #F06292; color: #ffffff; }"
    "QPushButton:pressed { background: #2a2a2a; border-color: #E91E63; }"
    "QPushButton:disabled { color: #7a7a7a; background: #2e2e2e; border-color: #3b3b3b; }"
)
BUTTON_ACCENT_QSS = (
    "QPushButton { background: rgba(233,30,99,0.85); border: 1px solid transparent;"
    " border-radius: 4px; color: #ffffff; font-size: 11px; padding: 0 10px; }"
    "QPushButton:hover { background: rgba(240,98,146,1.0); }"
    "QPushButton:pressed { background: rgba(200,20,80,1.0); }"
    "QPushButton:disabled { background: #4a4a4a; color: #8a8a8a; }"
)


class _HintLabel(QLabel):
    """提示 / 警告文字：**固定占两行高**。

    用户反馈："黄色的那个提示文字容器保持两行高度不然总跳" —— 提示时有时无，
    面板高度跟着变，下面的按钮就来回跳。统计那行删掉之后，这里是面板里唯一会变高的
    元素，所以给它 11px 字的**两行**固定高度：有一行提示也好、两行也好、空着也好，
    下面的按钮都钉在原地。
    """

    LINES = 2

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        font = self.font()
        font.setPixelSize(11)
        self.setFont(font)
        self.setWordWrap(True)
        # 按字体度量算两行高（含行距），不写字面数字 —— 换字体/换 DPI 也不会切字
        self.setFixedHeight(self.fontMetrics().lineSpacing() * self.LINES + 2)


def _opaque_count(img: Image.Image) -> int:
    """内容像素数（alpha ≥ 128）。走 Pillow 的 alpha 直方图，统计在 C 层做。"""
    return sum(img.getchannel("A").histogram()[128:])


def _changed_count(before: Image.Image, after: Image.Image) -> int:
    """alpha 被改动过的像素数。ImageChops.difference 也是 C 层逐像素差。"""
    diff = ImageChops.difference(before.getchannel("A"), after.getchannel("A"))
    return sum(diff.histogram()[1:])


def pixmap_to_pil(pixmap: QPixmap) -> Image.Image:
    """QPixmap -> Pillow RGBA。

    与 ``export_dialog._pixmap_to_pil`` 是同一套做法：那边是私有函数、只服务导出，
    这边还要反向转换，所以各留一份、互不依赖（不为了省 5 行代码把两个层焊在一起）。
    """
    image = pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = image.bits()
    ptr.setsize(image.sizeInBytes())
    return Image.frombytes("RGBA", (image.width(), image.height()), bytes(ptr))


def pil_to_pixmap(img: Image.Image) -> QPixmap:
    """Pillow RGBA -> QPixmap。

    必须 ``copy()``：``QImage(data, ...)`` 只是"包"住这块 bytes，不持有所有权，
    bytes 被回收后图像就是野指针（症状是偶发花屏或崩溃）。
    """
    data = img.tobytes("raw", "RGBA")
    qimage = QImage(data, img.width, img.height, QImage.Format.Format_RGBA8888).copy()
    return QPixmap.fromImage(qimage)


class KeyPanel(QWidget):
    """抠除背景的参数浮层（画布上的浮动卡片，样式与右上/左侧工具条同一套）。"""

    applied = pyqtSignal(object, dict)      # (抠好的 QPixmap, 统计)
    cancelled = pyqtSignal()
    pick_mode_changed = pyqtSignal(bool)    # 吸管开关（主窗口据此换画布光标）

    WIDTH = 276
    FAST_DELAY = 120        # 拖动中：防抖后再算，免得每个像素位移都算一次
    FULL_DELAY = 350        # 松手后：补一次带去色溢的完整效果

    def __init__(self, viewer, parent=None) -> None:
        super().__init__(parent)
        # 用弱引用持有 viewer：面板挂在画布上（Qt 父子树已经保证它活着），若再强引用回
        # viewer 就形成一对 Python 循环引用 —— viewer._pick_target 又指向本面板。
        # 解释器退出时 GC 顺序会让 Qt 在析构中 fail-fast（0xC0000409），
        # HistoryPanel 为此踩过坑，这里照同一套处理。
        self._viewer_ref = weakref.ref(viewer)
        self._source_pixmap = None
        self._source_pil = None
        self._preview = None
        self._bg_color = None
        self._params = dict(keying.DEFAULTS)
        self._stats = {}
        self._sliders: dict = {}
        self._values: dict = {}
        self._drag_offset = None
        # 原图分块：多张原图时「只抠选中的」/「抠全部」指的就是这些块。
        # 具体抠哪几块由 viewer 的选中状态决定（见 _target_indices），这里不另存一份。
        self._rects: list = []
        self._block_pils: list = []
        self._scope = "single"

        self.setObjectName("keyPanel")
        self.setFixedWidth(self.WIDTH)
        self.setToolTip("拖动标题处可移动这个面板")

        self._fast_timer = QTimer(self)
        self._fast_timer.setSingleShot(True)
        self._fast_timer.timeout.connect(lambda: self._compute(despill=False))
        self._full_timer = QTimer(self)
        self._full_timer.setSingleShot(True)
        self._full_timer.timeout.connect(lambda: self._compute(despill=True))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(6)

        title_row = QHBoxLayout()
        title_row.setSpacing(4)
        title = QLabel("抠除背景")
        title.setStyleSheet("color: #f0f0f0; font-size: 12px; font-weight: bold;")
        title_row.addWidget(title)
        title_row.addStretch(1)
        self.btn_close = QPushButton("×")
        self.btn_close.setFixedSize(18, 18)
        self.btn_close.setToolTip("关闭（等于取消）")
        self.btn_close.setStyleSheet(
            "QPushButton { background: transparent; border: none; color: #aaa; font-size: 14px; }"
            "QPushButton:hover { color: #fff; background: rgba(233,30,99,0.3); border-radius: 3px; }"
        )
        self.btn_close.clicked.connect(self._on_cancel)
        title_row.addWidget(self.btn_close)
        layout.addLayout(title_row)

        # ---- 背景色取样 ----
        # 行首标签固定 48px：两行（取样色 / 作用范围）的控件因此**左边缘对齐成一列**，
        # 不靠猜（"按数字对齐"是这个工程的老规矩）
        pick_row = QHBoxLayout()
        pick_row.setSpacing(4)
        pick_row.addWidget(self._hint_label("取样色", 48))
        self.swatch = QLabel()
        self.swatch.setFixedSize(22, 22)
        self.swatch.setToolTip("当前拿去抠的背景色（吸管取色 / 自动猜色都会更新它）")
        pick_row.addWidget(self.swatch)
        self.btn_pick = QPushButton("吸管")
        self.btn_pick.setCheckable(True)
        self.btn_pick.setFixedHeight(22)
        self.btn_pick.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_pick.setToolTip("打开后在原图上点一下，就用那一点的颜色当背景色")
        self.btn_pick.setStyleSheet(BUTTON_QSS)
        self.btn_pick.toggled.connect(self._on_pick_toggled)
        pick_row.addWidget(self.btn_pick)
        self.btn_guess = QPushButton("重新猜色")
        self.btn_guess.setFixedHeight(22)
        self.btn_guess.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_guess.setToolTip("重新从四边取样猜背景色（默认就是这么来的）")
        self.btn_guess.setStyleSheet(BUTTON_QSS)
        self.btn_guess.clicked.connect(self._guess_color)
        pick_row.addWidget(self.btn_guess)
        pick_row.addStretch(1)
        layout.addLayout(pick_row)

        # ---- 作用范围（只有多张原图时才有意义，单张时整块隐藏）----
        self.scope_row = QWidget()
        scope_layout = QHBoxLayout(self.scope_row)
        scope_layout.setContentsMargins(0, 0, 0, 0)
        scope_layout.setSpacing(4)
        scope_layout.addWidget(self._hint_label("作用范围", 48))
        self._scope_group = QButtonGroup(self)
        self.btn_this = self._make_scope_button(
            "只抠选中的", "只处理画布上粉色实线框住的原图（可点选、也可拖框多选）")
        self.btn_all = self._make_scope_button("抠全部", "每张原图各自猜自己的背景色，全部抠一遍")
        self.btn_this.setChecked(True)
        for button in (self.btn_this, self.btn_all):
            self._scope_group.addButton(button)
            scope_layout.addWidget(button)
        scope_layout.addStretch(1)
        self.btn_this.toggled.connect(lambda on: on and self._on_scope_changed("single"))
        self.btn_all.toggled.connect(lambda on: on and self._on_scope_changed("all"))
        self.scope_row.setVisible(False)
        layout.addWidget(self.scope_row)

        layout.addWidget(self._separator())

        # ---- 参数（由 PARAM_SPEC 生成）----
        for spec in keying.PARAM_SPEC:
            if spec["kind"] in ("int", "float"):
                layout.addLayout(self._make_int_row(spec))
            else:
                box = QCheckBox(spec["label"])
                box.setChecked(bool(self._params[spec["key"]]))
                box.setToolTip(spec.get("tip", ""))
                box.toggled.connect(lambda checked, k=spec["key"]: self._on_param_changed(k, checked))
                layout.addWidget(box)

        layout.addWidget(self._separator())

        # 统计那行（内容 xx% → yy% · 清掉 zz% · 连通块 n 个）**整块删掉**：
        # 用户明确说"那个信息窗口可以不要"。判断效果看画布上的预览就够了，
        # 参数行自己就是"我调了什么"的说明书。位置腾出来给参数和按钮，面板也更矮。
        # 留下的 lbl_hint 只承担警告，而且**固定两行高**（见 _HintLabel），
        # 免得提示时有时无把下面的按钮顶得来回跳。
        self.lbl_hint = _HintLabel("")
        self.lbl_hint.setStyleSheet("color: #ffb74d; font-size: 11px;")
        layout.addWidget(self.lbl_hint)

        # ---- 应用 / 取消（等宽：一眼看出是"二选一"的收尾动作）----
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setFixedHeight(24)
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel.setStyleSheet(BUTTON_QSS)
        self.btn_cancel.setToolTip("放弃这次抠图（原图一点没动）")
        self.btn_cancel.clicked.connect(self._on_cancel)
        btn_row.addWidget(self.btn_cancel, 1)
        self.btn_apply = QPushButton("应用")
        self.btn_apply.setFixedHeight(24)
        self.btn_apply.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_apply.setToolTip("把抠好的结果换成原图（可以用 Ctrl+Z 撤销）")
        self.btn_apply.setStyleSheet(BUTTON_ACCENT_QSS)
        self.btn_apply.clicked.connect(self._on_apply)
        btn_row.addWidget(self.btn_apply, 1)
        layout.addLayout(btn_row)

        # 画布尺寸变化时跟着竖条重新贴边
        if parent is not None:
            parent.installEventFilter(self)
        # 选中项在画布上变了（工具或面板里点选/框选/Esc）：面板要跟着换目标并重算预览。
        # 只连一次（放在构造里），重复连会一次点击触发多遍重算。
        if viewer is not None:
            viewer.source_selection_changed.connect(self._on_selection_changed)

    # ------------------------------------------------------------ 小工具
    @property
    def _viewer(self):
        """弱引用解析：viewer 已被回收时返回 None（退出阶段才可能出现）。"""
        return self._viewer_ref()

    def _hint_label(self, text: str, width: int = 0) -> QLabel:
        """行首说明文字；给 ``width`` 时固定宽度，让上下两行控件对齐成一列。"""
        label = QLabel(text)
        label.setStyleSheet("color: #bdbdbd; font-size: 11px;")
        if width:
            label.setFixedWidth(width)
        return label

    def _make_scope_button(self, text: str, tip: str) -> QPushButton:
        """作用范围按钮：跟工程里其它"模式二选一"一个套路（QButtonGroup + checkable）。

        样式统一用 BUTTON_QSS（含悬浮/按下反馈）再补一条选中态，
        免得这两个按钮跟面板里其它按钮"有的有反馈、有的没有"。
        """
        button = QPushButton(text)
        button.setCheckable(True)
        button.setFixedHeight(22)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip(tip)
        button.setStyleSheet(
            BUTTON_QSS
            + "QPushButton:checked { background: rgba(233,30,99,0.85);"
            " border-color: #E91E63; color: #ffffff; }"
            "QPushButton:checked:hover { background: rgba(240,98,146,1.0); }"
        )
        return button

    def _separator(self) -> QWidget:
        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet("background: #3d3d3d;")
        return line

    def _make_int_row(self, spec: dict) -> QHBoxLayout:
        """一行参数：标签 + 滑块 + 当前值。

        ``kind == "float"`` 的参数（例如「收边」0.5 一档）滑块本身只能走整数，
        所以滑块值当**档位序号**用，真实值 = min + 序号 × step；
        这样 13 档就是滑块上的 0..12，拖动一格正好半像素。
        """
        row = QHBoxLayout()
        row.setSpacing(4)
        name = self._hint_label(spec["label"])
        name.setFixedWidth(76)
        name.setToolTip(spec.get("tip", ""))
        row.addWidget(name)
        is_float = spec["kind"] == "float"
        step = float(spec.get("step", 1)) if is_float else int(spec.get("step", 1))
        lmin = float(spec["min"]) if is_float else int(spec["min"])
        lmax = float(spec["max"]) if is_float else int(spec["max"])
        ticks = max(1, int(round((lmax - lmin) / step))) if is_float else 1
        slider = QSlider(Qt.Orientation.Horizontal)
        if is_float:
            slider.setRange(0, ticks)
            slider.setSingleStep(1)
            slider.setPageStep(max(1, ticks // 6))
            slider.setValue(int(round((float(self._params[spec["key"]]) - lmin) / step)))
        else:
            slider.setRange(lmin, lmax)
            slider.setSingleStep(step)
            slider.setPageStep(step * 5)
            slider.setValue(int(self._params[spec["key"]]))
        slider.setToolTip(spec.get("tip", ""))
        if is_float:
            slider.valueChanged.connect(
                lambda tick, k=spec["key"], lo=lmin, st=step, d=int(spec.get("decimals", 1)):
                self._on_param_changed(k, round(lo + tick * st, d)))
        else:
            slider.valueChanged.connect(lambda value, k=spec["key"]: self._on_param_changed(k, value))
        self._sliders[spec["key"]] = slider
        row.addWidget(slider, 1)
        value_label = self._hint_label(self._format_param(spec, self._params[spec["key"]]))
        value_label.setFixedWidth(34 if is_float else 28)
        value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._values[spec["key"]] = value_label
        row.addWidget(value_label)
        return row

    @staticmethod
    def _format_param(spec: dict, value) -> str:
        """滑块右边那个数字：小数档位显示 1 位小数（0.5 不能被四舍五入成 1）。"""
        if spec["kind"] == "float":
            return f"{float(value):.{int(spec.get('decimals', 1))}f}"
        return str(int(value))

    # ------------------------------------------------------------ 生命周期
    def start(self) -> None:
        """打开面板：抓原图、分块、猜色、算第一版预览、贴到竖条右边。"""
        viewer = self._viewer
        self._source_pixmap = viewer._pixmap
        self._source_pil = pixmap_to_pil(self._source_pixmap) if self._source_pixmap else None
        self._preview = None
        self.btn_pick.setChecked(False)
        # 分块位置由 viewer 现算（重放拼装算法），这里只裁出每块的图
        self._rects = viewer.source_rects()
        if not self._rects and self._source_pil is not None:
            self._rects = [(0, 0, self._source_pil.width, self._source_pil.height)]
        self._block_pils = [self._source_pil.crop((x, y, x + w, y + h))
                            for (x, y, w, h) in self._rects]
        self._scope = "single"
        multi = len(self._rects) > 1
        self.scope_row.setVisible(multi)
        self.btn_this.setChecked(True)
        # 分块边框与"点哪张"由 viewer 的**同一份**选中状态负责（「原图选择」工具也是它），
        # 面板只管读 —— 这样工具与面板不会各搞一套、互相打架
        viewer._source_panel = multi
        if multi and not viewer.selected_sources():
            # 一张都没选就先默认选中第一张：否则「只抠选中的」在画布上看不见对象
            viewer.set_selected_sources([0])
        self._guess_color(silent=True)
        self.adjustSize()
        self.sync_position()
        self.show()
        self.raise_()

    def cancel(self) -> None:
        """取消/关闭：把预览清掉，原图与撤销栈一点没动。选中项保留（工具还要用）。"""
        self._fast_timer.stop()
        self._full_timer.stop()
        self.btn_pick.setChecked(False)
        self._preview = None
        self._release_viewer()
        self.hide()

    def _release_viewer(self) -> None:
        """收起预览与取色钩子、关掉面板那侧的边框（应用/取消都要走这里，别漏）。

        注意**不清选中项**：选中是「原图选择」工具的状态，面板只是借来用，
        关掉面板顺手清掉的话，用户刚框选好的一批图就白选了。
        """
        viewer = self._viewer
        if viewer is None:
            return
        viewer._preview_pixmap = None
        viewer._source_panel = False
        viewer._pick_target = None
        viewer.canvas.update()

    def _on_selection_changed(self) -> None:
        """画布上的选中项变了：刷新取色控件可用状态，并重算一版预览。"""
        if not self.isVisible():
            return
        self._sync_scope_controls()
        if len(self._target_indices()) == 1:
            # 换成另一张原图了：取样色必须跟着换，否则会拿上一张的底色去抠这一张
            self._guess_color(silent=True)
        self._fast_timer.start(self.FAST_DELAY)
        self._full_timer.start(self.FULL_DELAY)

    def _current_block_pil(self):
        """取色/猜色针对的那一块（多选时不猜色，只取第一块当参考）。"""
        indices = self._target_indices()
        if not indices:
            return None
        return self._block_pils[indices[0]]

    def _on_scope_changed(self, scope: str) -> None:
        self._scope = scope
        self._sync_scope_controls()
        self._fast_timer.start(self.FAST_DELAY)
        self._full_timer.start(self.FULL_DELAY)

    def _sync_scope_controls(self) -> None:
        """只有"正好处理一块"时取样色才有意义。

        多块时每块各自猜色（多张原图底色常常不一样），取样色不参与 —— 那就把取色
        控件关掉，而不是留着几个"点了没反应"的按钮。
        """
        single = len(self._target_indices()) == 1
        for widget in (self.btn_pick, self.btn_guess, self.swatch):
            widget.setEnabled(single)
        if not single and self.btn_pick.isChecked():
            self.btn_pick.setChecked(False)

    def sync_position(self) -> None:
        """贴到左侧竖条右边；竖条被拖走后也跟着走；超出画布就夹回来。"""
        parent = self.parentWidget()
        if parent is None:
            return
        bar = getattr(self._viewer, "_v_toolbar", None)
        bar_x = bar.x() if bar is not None else 6
        bar_y = bar.y() if bar is not None else 38
        bar_w = bar.width() if bar is not None else 36
        x = bar_x + bar_w + 8
        y = bar_y
        x = max(0, min(parent.width() - self.width(), x))
        y = max(0, min(parent.height() - self.height(), y))
        self.move(x, y)

    # ------------------------------------------------------------ 参数
    def _on_param_changed(self, key: str, value) -> None:
        self._params[key] = value
        label = self._values.get(key)
        if label is not None:
            spec = next((s for s in keying.PARAM_SPEC if s["key"] == key), None)
            label.setText(self._format_param(spec, value) if spec else str(value))
        self._fast_timer.start(self.FAST_DELAY)
        self._full_timer.start(self.FULL_DELAY)

    def _on_pick_toggled(self, checked: bool) -> None:
        self._viewer._pick_target = self if checked else None
        self.pick_mode_changed.emit(checked)
        self.lbl_hint.setText("在原图上点一下取色（Esc 或再点一次「吸管」退出）" if checked else "")

    def sample_at(self, ix: int, iy: int) -> None:
        """画布上点了一下（吸管模式）：取原图那一点的**原色**当背景色。

        注意取的是 ``_source_pixmap``（抠图前的原图），不是预览图 —— 否则连点两下
        会越取越偏（第二下取到的是已经变透明的像素）。
        """
        if self._source_pil is None:
            return
        if not (0 <= ix < self._source_pil.width and 0 <= iy < self._source_pil.height):
            return
        r, g, b = self._source_pil.getpixel((ix, iy))[:3]
        self._set_bg_color((r, g, b))
        self.btn_pick.setChecked(False)

    def _guess_color(self, silent: bool = False) -> None:
        source = self._current_block_pil()
        if source is None:
            return
        guess = keying.estimate_bg_color(source)
        if guess is None:
            self._set_bg_color((0, 0, 0))
            self.lbl_hint.setText("这张图四边几乎没有不透明像素 —— 可能已经是透明底了。")
            return
        self._set_bg_color(guess)
        if not silent:
            self.lbl_hint.setText(f"已重新猜色：{guess}")

    def _set_bg_color(self, color) -> None:
        self._bg_color = tuple(int(v) for v in color[:3])
        r, g, b = self._bg_color
        self.swatch.setToolTip(f"当前背景色：rgb({r}, {g}, {b})")
        # 深浅两色描边，保证任何底色上这个小方块都看得见
        edge = "#000" if (r + g + b) > 380 else "#888"
        self.swatch.setStyleSheet(
            f"background: rgb({r},{g},{b}); border: 1px solid {edge}; border-radius: 2px;"
        )
        self._fast_timer.start(self.FAST_DELAY)
        self._full_timer.start(self.FULL_DELAY)

    # ------------------------------------------------------------ 预览
    def _target_indices(self) -> list:
        """要处理的块索引：只抠选中的 -> 画布上选中的那些；抠全部 -> 每一块。

        选中的一张都没有时兜底成第 1 张 —— 否则「只抠选中的」会变成点了没反应的按钮。
        """
        if not self._block_pils:
            return []
        if self._scope == "all" and len(self._block_pils) > 1:
            return list(range(len(self._block_pils)))
        viewer = self._viewer
        picked = viewer.selected_sources() if viewer is not None else []
        indices = [i for i in picked if i < len(self._block_pils)]
        return indices or [0]

    def _target_blocks(self) -> list:
        """要处理的 (矩形, 图像) 列表，顺序与 _target_indices() 一致。"""
        return [(self._rects[i], self._block_pils[i]) for i in self._target_indices()]

    def _run_blocks(self, blocks: list, params: dict):
        """逐块抠，再贴回整张原图的副本上 —— 块以外的像素一个都不碰。

        抠全部时每块**各自猜色**：多张原图的底色常常不一样，拿同一个色去抠会漏掉别的图。
        统计口径统一成"按块面积加权"（多块时把各块的像素数加起来再除总面积），
        这样"内容占比"在多块场景下也是对的。
        """
        result = self._source_pil.copy()
        per_block = len(blocks) > 1
        counts = {"area": 0, "opaque_before": 0, "opaque_after": 0, "changed": 0, "components": 0}
        colors = []
        for rect, pil in blocks:
            guess = (keying.estimate_bg_color(pil) or self._bg_color) if per_block else self._bg_color
            keyed, stats = keying.key_image(pil, guess, **params)
            result.paste(keyed, (rect[0], rect[1]))
            colors.append(tuple(stats["bg_color"]))
            counts["area"] += pil.width * pil.height
            counts["opaque_before"] += _opaque_count(pil)
            counts["opaque_after"] += _opaque_count(keyed)
            counts["changed"] += _changed_count(pil, keyed)
            counts["components"] += stats["components"]
        area = counts["area"] or 1
        return result, {
            "bg_color": colors[0] if colors else self._bg_color,
            "components": counts["components"],
            "changed_pct": counts["changed"] * 100.0 / area,
            "content_before_pct": counts["opaque_before"] * 100.0 / area,
            "content_after_pct": counts["opaque_after"] * 100.0 / area,
        }

    def _compute(self, despill: bool) -> None:
        """算一版预览。despill=False 是拖动中的快速版（跳过去色溢）。"""
        if self._source_pil is None or self._bg_color is None:
            return
        params = dict(self._params)
        if not despill:
            params["despill"] = 0
        try:
            indices = self._target_indices()
            preview_pil, stats = self._run_blocks(self._target_blocks(), params)
        except Exception as exc:                     # noqa: BLE001 - 界面别被算法异常打挂
            self.lbl_hint.setText(f"抠图失败：{exc}")
            return
        self._preview = pil_to_pixmap(preview_pil)
        self._stats = stats
        self._viewer._preview_pixmap = self._preview
        self._viewer.canvas.update()

        # 统计那行（"第 N 张：内容 x% → y% · 清掉 z% · 连通块 n 个"）按用户要求整块删了，
        # 面板只留**能让人改动作**的提示：多张时的取样色规则、几乎没清掉东西、快速预览。
        # 纯数字仍然算在 stats 里（日志/测试还在用），只是不再往面板上贴。
        total = len(self._block_pils)
        hints = []
        if total > 1 and len(indices) > 1:
            hints.append("抠全部：每张原图各自猜色" if self._scope == "all"
                         else "选中的每张各自猜色（取样色不参与）")
        if stats["changed_pct"] < 0.1:
            hints.append("几乎没清掉东西：把容差调大，或关掉「只清边缘连通」"
                         "（背景可能被角色围住了）")
        if not despill:
            hints.append("松手后会补一遍带「去色溢」的完整效果")
        self.lbl_hint.setText("　".join(hints))

    # ------------------------------------------------------------ 应用/取消
    def _on_apply(self) -> None:
        if self._preview is None:
            self.lbl_hint.setText("还没有可应用的结果")
            return
        if self._viewer._pixmap is not self._source_pixmap:
            # 预览期间原图被换过（比如中途又加了图）：以当前原图为准重来一版，
            # 否则会把用户后来的改动覆盖掉。
            self.start()
            self.lbl_hint.setText("原图刚变过，已按最新原图重算，请再点一次「应用」")
            return
        self._fast_timer.stop()
        self._full_timer.stop()
        self.btn_pick.setChecked(False)
        preview, stats = self._preview, dict(self._stats)
        self._preview = None
        self._release_viewer()
        self.hide()
        self.applied.emit(preview, stats)

    def _on_cancel(self) -> None:
        self.cancel()
        self.cancelled.emit()

    # ------------------------------------------------------------ 事件
    def paintEvent(self, event):
        """卡片外观：与左右两条工具条同一套（底色 30,30,30 + 1px #444 + 圆角 6）。"""
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(30, 30, 30, 245))
        p.setPen(QPen(QColor("#444"), 1))
        p.drawRoundedRect(self.rect(), 6, 6)
        p.end()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Resize and obj is self.parentWidget():
            if self.isVisible():
                self.sync_position()
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event):
        """按住标题/空白处可拖动整块面板（按钮、滑块不在这里，不会被带走）。"""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.position().toPoint()
            self.raise_()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is None:
            super().mouseMoveEvent(event)
            return
        parent = self.parentWidget()
        want = self.mapToParent(event.position().toPoint() - self._drag_offset)
        if parent is not None:
            want.setX(max(0, min(parent.width() - self.width(), want.x())))
            want.setY(max(0, min(parent.height() - self.height(), want.y())))
        self.move(want)
        event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            if self.btn_pick.isChecked():
                self.btn_pick.setChecked(False)     # 先退吸管，再按一次才是取消
            else:
                self._on_cancel()
            event.accept()
            return
        super().keyPressEvent(event)