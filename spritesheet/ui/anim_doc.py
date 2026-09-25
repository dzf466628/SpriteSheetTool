#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""② 「以单张图片修改」的动图文档：N 帧 + 逐帧时长 + 一串破坏性操作。

用户口径（原话 + 追问确认）：
  - 拖进动图选"以单张图片修改"= 画布上**只显示一帧**，另配一个「查看第几帧」的滑块
    （**只影响浏览，不影响编辑范围**）；
  - **所有破坏性编辑同步到全部帧**：抠除背景、画笔/橡皮、翻转/旋转/缩放，以及以后新增的；
  - 导出走现有那个导出弹窗（GIF 写回动图、PNG 每帧一张、JSON 带 duration）；
  - 用户确认"当成一种新的动图文档：不玩元素/动作那套"。

**撤销为什么不用整份快照**（这条是我定的，已经跟用户报备过）：
主窗口的撤销是"整份状态快照 + QPixmap 引用"。动图文档一份快照就是 N 帧，60 帧 × 512²
一份 60MB、几十步就上 G。这里改成 ``originals``（导入时的原始帧，**永不改动**）+ ``ops``
（用户做过的破坏性操作），任何时刻的画面 = 从头重放 ops：
撤销 = 少重放一条，重做 = 多重放一条，内存只跟**帧数**有关、跟**步数**无关。
代价是"撤掉中间某一步之后的操作"要从头重放（抠背景重放一遍是几百毫秒级）——
比每次操作都留一份全帧快照划算得多；只撤最后一步时，前缀很短、几乎瞬间。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from PyQt6.QtCore import QPointF, Qt, pyqtSignal
from PyQt6.QtGui import QPixmap, QTransform
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QMessageBox, QPushButton, QSizePolicy,
                             QSlider, QVBoxLayout, QWidget)

from spritesheet.anim import (DEFAULT_FRAME_MS, fps_and_overrides, pil_to_pixmap,
                              pixmap_to_pil)

from .models import Action

# 变换按钮的顺序就是用户会用的顺序：左右/上下翻转在最前（动图里最常用）
TRANSFORM_BUTTONS = (
    ("flip_h", "左右翻转", "整段动图左右翻转（每帧都做同样的变换）"),
    ("flip_v", "上下翻转", "整段动图上下翻转"),
    ("rot90", "转 90°", "每帧顺时针转 90°（宽高会互换）"),
    ("rot180", "转 180°", "每帧转 180°"),
)

STROKE_TOOL_LABEL = {"brush": "画笔", "eraser": "橡皮"}

# 拖入真动图时问用户的三条出路（用常量，别在几处地方各写一遍字符串）
MODE_FRAMES = "frames"      # ① 转成序列帧：每帧一张原图，自动建一个动作
MODE_DOC = "doc"            # ② 以单张图片修改：动图文档，改动同步到全部帧
MODE_CANCEL = "cancel"


def ask_animation_mode(parent, anim, name_hint: str = "") -> str:
    """问用户这张动图怎么处理（① 转序列帧 / ② 以单张图片修改 / 取消）。

    用户口径：**只有真动图（帧数 > 1）才问** —— 静态的 gif/webp 一声不响照旧走普通流程。
    每次都问（用户没要"以后都这样"，真要做是下一步的事，不先埋个没人管的勾选框）。
    """
    box = QMessageBox(parent)
    box.setWindowTitle("动图导入")
    box.setIcon(QMessageBox.Icon.Question)
    total = sum(anim.durations) if anim.durations else anim.count * DEFAULT_FRAME_MS
    box.setText(f"「{name_hint or '这张动图'}」是动图：{anim.count} 帧 · "
                f"{anim.width}×{anim.height} · 总时长约 {total / 1000:.1f} 秒")
    box.setInformativeText("要怎么处理？")
    btn_frames = box.addButton("转成序列帧", QMessageBox.ButtonRole.AcceptRole)
    btn_frames.setToolTip("每一帧变成一张原图横排铺开，并自动建一个动作"
                          "（保留原动图的逐帧时长）—— 之后按图集那样排帧、调锚点、导出")
    btn_doc = box.addButton("以单张图片修改", QMessageBox.ButtonRole.AcceptRole)
    btn_doc.setToolTip("画布上只显示一帧（可翻看），抠背景/画笔/翻转等改动"
                       "会同步到全部帧 —— 适合整体去背景、统一调色")
    box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(btn_frames)
    box.exec()
    clicked = box.clickedButton()
    if clicked is btn_frames:
        return MODE_FRAMES
    if clicked is btn_doc:
        return MODE_DOC
    return MODE_CANCEL


@dataclass
class KeyOp:
    """抠除背景：参数 + 颜色规则。

    颜色规则跟抠图面板自己的口径一致（见 ``key_panel._run_blocks``）：
      - ``per_frame_guess=True``（面板选了「抠全部」）→ 每帧按自己的四边猜色；
      - 否则就用 ``color`` 这一个颜色（吸管取的、或面板对当前帧猜出来的那个）。
    """

    params: dict
    color: tuple | None = None
    per_frame_guess: bool = False
    label: str = "抠除背景"

    def apply(self, pm: QPixmap, viewer) -> QPixmap:
        from spritesheet import keying
        pil = pixmap_to_pil(pm)
        colors = keying.estimate_bg_color(pil) if self.per_frame_guess else self.color
        if colors is None:
            # 没指定颜色（或这帧四边几乎没不透明像素）就按这帧自己猜一次；
            # 连猜都猜不出来 = 这帧本来就没背景，原样返回，不硬抠
            colors = keying.estimate_bg_color(pil)
        if colors is None:
            return pm
        out, _stats = keying.key_image(pil, colors, **self.params)
        return pil_to_pixmap(out)


@dataclass
class StrokeOp:
    """画笔/橡皮：同一批线段、同样的工具与笔刷，往每一帧各画一遍。"""

    tool: str
    size: int
    color: str
    pixel_perfect: bool
    segments: list[tuple[QPointF, QPointF]] = field(default_factory=list)
    label: str = "画笔"

    def apply(self, pm: QPixmap, viewer) -> QPixmap:
        return viewer.replay_stroke(pm, self.segments, self.tool, self.size,
                                    self.color, self.pixel_perfect)


@dataclass
class TransformOp:
    """翻转/旋转：每帧做同样的像素变换。"""

    kind: str                        # flip_h / flip_v / rot90 / rot180
    label: str = "变换"

    def apply(self, pm: QPixmap, viewer) -> QPixmap:
        image = pm.toImage()
        if self.kind == "flip_h":
            image = image.mirrored(True, False)
        elif self.kind == "flip_v":
            image = image.mirrored(False, True)
        else:
            angle = {"rot90": 90, "rot180": 180, "rot270": 270}.get(self.kind, 0)
            # 像素画不能插值：FastTransformation = 最近邻，边缘不会被糊掉
            image = image.transformed(QTransform().rotate(angle),
                                      Qt.TransformationMode.FastTransformation)
        return QPixmap.fromImage(image)


class AnimDoc:
    """一个动图文档：帧、时长、"看第几帧"、以及那条可撤销的操作链。"""

    def __init__(self, frames: list[QPixmap], durations: list[int] | None = None,
                 name: str = "", path: str = "") -> None:
        self.originals = list(frames)
        durs = list(durations or [])
        self.durations = [max(1, int(ms)) for ms in durs]
        if len(self.durations) < len(self.originals):
            self.durations += [100] * (len(self.originals) - len(self.durations))
        self.name = name or "动图"
        self.path = path
        self.ops: list = []
        self.redone: list = []
        self.index = 0                      # 当前**看**第几帧（只看，不是编辑范围）
        self._frames = list(self.originals)

    # ---------------------------------------------------------------- 状态
    @property
    def frames(self) -> list[QPixmap]:
        return self._frames

    @property
    def count(self) -> int:
        return len(self._frames)

    def duration_at(self, index: int) -> int:
        if 0 <= index < len(self.durations):
            return max(1, int(self.durations[index]))
        return 100

    def frame(self, index: int | None = None) -> QPixmap | None:
        idx = self.index if index is None else index
        if 0 <= idx < len(self._frames):
            return self._frames[idx]
        return None

    def rebuild(self, viewer) -> list[QPixmap]:
        """从头重放：画面 = originals 依次过一遍 ops。"""
        frames = list(self.originals)
        for op in self.ops:
            frames = [op.apply(pm, viewer) for pm in frames]
        self._frames = frames
        if self.index >= len(self._frames):
            self.index = max(0, len(self._frames) - 1)
        return frames

    # ---------------------------------------------------------------- 操作链
    def apply(self, op, viewer) -> None:
        """记一条操作并落地（新操作之后，原来的重做链就作废了）。"""
        self.ops.append(op)
        self.redone.clear()
        self.rebuild(viewer)

    def can_undo(self) -> bool:
        return bool(self.ops)

    def can_redo(self) -> bool:
        return bool(self.redone)

    def undo(self, viewer) -> bool:
        if not self.ops:
            return False
        self.redone.append(self.ops.pop())
        self.rebuild(viewer)
        return True

    def redo(self, viewer) -> bool:
        if not self.redone:
            return False
        self.ops.append(self.redone.pop())
        self.rebuild(viewer)
        return True

    def undo_label(self) -> str:
        return self.ops[-1].label if self.ops else ""

    def redo_label(self) -> str:
        return self.redone[-1].label if self.redone else ""

    # ---------------------------------------------------------------- 预览/导出
    def synthetic_action(self) -> Action:
        """给预览区和导出弹窗用的动作：只装帧和逐帧时长。

        为什么不真建一个动作放进动作列表：用户口径就是"动图文档不玩元素/动作那套"。
        这里造的是**一次性**的壳（``Action.frames`` 就是个普通列表），预览播放和导出
        GIF/PNG/JSON 全都读它 —— 于是"导出和我们软件现在一样"这句话是白拿的。
        """
        action = Action(self.name, "#E91E63")
        action.frames = list(self._frames)
        fps, overrides = fps_and_overrides(self.durations)
        action.fps = fps
        action.frame_durations = overrides
        return action


# ---------------------------------------------------------------- 存进 .sstool
# 存的是 **originals + 操作链**，不是"当前这一帧帧的像素"：
#   - 体积小得多（画笔/翻转/抠图都只是一条参数，不是每帧一张图）；
#   - 重新打开后撤销栈还是活的（用户在存盘前做的那几步，打开后照样能 Ctrl+Z 退回去）；
#   - 帧像素只存一份（originals），跟内存里的模型完全一致，不存在"两份真值"。
# 代价是打开时要重放一遍（几帧到几十帧的画笔/翻转很快；抠图慢一些，一次性开销）。

_TRANSFORM_LABEL = {kind: text for kind, text, _tip in TRANSFORM_BUTTONS}


def op_to_dict(op) -> dict | None:
    """一条操作 → 可存进 JSON 的普通字典（不认识的类型返回 None，宁可存不了也别硬塞）。"""
    if isinstance(op, TransformOp):
        return {"kind": "transform", "t": op.kind}
    if isinstance(op, StrokeOp):
        return {
            "kind": "stroke", "tool": op.tool, "size": int(op.size),
            "color": op.color, "pp": bool(op.pixel_perfect),
            "segs": [[float(a.x()), float(a.y()), float(b.x()), float(b.y())]
                     for a, b in op.segments],
        }
    if isinstance(op, KeyOp):
        return {
            "kind": "key", "params": dict(op.params),
            "color": list(op.color) if op.color else None,
            "per_frame": bool(op.per_frame_guess),
        }
    return None


def op_from_dict(data) -> object | None:
    """字典 → 一条操作；读不出来返回 None（调用方据此判定"这份工程存坏了"）。"""
    if not isinstance(data, dict):
        return None
    kind = data.get("kind")
    try:
        if kind == "transform":
            key = str(data.get("t", ""))
            if key not in _TRANSFORM_LABEL:
                return None
            return TransformOp(key, label=_TRANSFORM_LABEL[key])
        if kind == "stroke":
            segments = []
            for item in data.get("segs") or []:
                segments.append((QPointF(float(item[0]), float(item[1])),
                                 QPointF(float(item[2]), float(item[3]))))
            tool = str(data.get("tool", "brush"))
            if tool not in ("brush", "eraser") or not segments:
                return None
            return StrokeOp(tool=tool, size=max(1, int(data.get("size", 1))),
                            color=str(data.get("color", "#FFFFFF")),
                            pixel_perfect=bool(data.get("pp")),
                            segments=segments,
                            label=STROKE_TOOL_LABEL.get(tool, "画笔"))
        if kind == "key":
            color = data.get("color")
            return KeyOp(params=dict(data.get("params") or {}),
                         color=tuple(int(v) for v in color) if color else None,
                         per_frame_guess=bool(data.get("per_frame")))
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    return None


def anim_doc_payload(doc: AnimDoc, frame_names: list[str]) -> dict:
    """动图文档 → 工程清单里那一段（帧的像素由调用方存成 sections）。"""
    ops = []
    for op in doc.ops:
        data = op_to_dict(op)
        if data is None:
            # 有一条存不了：整份就别存了 —— 存一半的工程打开后画面跟存盘时不一样，
            # 那比"存不了"更糟（用户会以为软件把图弄坏了）
            raise ValueError(f"这种操作暂时存不进工程：{op.label}")
        ops.append(data)
    return {
        "name": doc.name,
        "path": doc.path,
        "index": int(doc.index),
        "durations": [int(ms) for ms in doc.durations],
        "frames": list(frame_names),
        "ops": ops,
    }


def frames_source_payload(source: FramesSource, action_index: int) -> dict:
    """序列帧那份"从动图来"的记忆 → 工程清单里那一段（存进 .sstool 用）。

    动作对象存不进 JSON，所以记它的**下标 + 名字**，打开时按这两样重新认回来。
    """
    return {
        "indices": [int(i) for i in source.source_indices],
        "durations": [int(ms) for ms in source.durations],
        "name": source.name,
        "path": source.path,
        "source_total": int(source.source_total),
        "action_index": int(action_index),
        "action_name": source.action.name if source.action is not None else "",
    }


def frames_source_from_payload(payload: dict, actions: list[Action]) -> FramesSource | None:
    """清单里那一段 + 刚重建好的动作列表 → FramesSource；没有/读不出来返回 None。"""
    if not payload:
        return None
    indices = [int(i) for i in (payload.get("indices") or [])]
    if not indices:
        return None
    name = str(payload.get("action_name") or "")
    # 注意别写 `int(raw or -1)`：下标 0 是**假值**，会被 `or` 吃掉变成 -1，
    # 于是"第 0 个动作"永远认不回来（本轮真踩了一次，回归里那条"滑块能跳帧"红了）
    raw_index = payload.get("action_index", -1)
    index = int(raw_index) if raw_index is not None else -1
    action = actions[index] if 0 <= index < len(actions) else None
    if action is not None and name and action.name != name:
        # 下标对上、名字对不上（工程被别的版本动过）：按名字再找一遍；还找不到就当没有
        # 动作 —— 那条横条只是不能"跳到第 i 帧"，转换照样能用（它只要原图下标）
        action = next((item for item in actions if item.name == name), None)
    return FramesSource(
        source_indices=indices,
        durations=[int(ms) for ms in (payload.get("durations") or [])],
        action=action,
        name=str(payload.get("name") or ""),
        path=str(payload.get("path") or ""),
        source_total=int(payload.get("source_total") or 0),
    )


def anim_doc_from_payload(payload: dict, frames: list[QPixmap]) -> AnimDoc:
    """清单里那一段 + 读出来的帧 → 动图文档（操作链读不出来就报错，别假装成功）。"""
    if not frames:
        raise ValueError("动图文档没有任何帧数据")
    ops = []
    for data in payload.get("ops") or []:
        op = op_from_dict(data)
        if op is None:
            raise ValueError("工程里的动图操作链读不出来（可能是更新版本存的）")
        ops.append(op)
    durations = [int(ms) for ms in (payload.get("durations") or [])]
    doc = AnimDoc(frames, durations, name=str(payload.get("name") or "动图"),
                  path=str(payload.get("path") or ""))
    doc.index = max(0, min(int(payload.get("index", 0) or 0), doc.count - 1))
    doc.ops = ops
    return doc


@dataclass
class FramesSource:
    """序列帧模式下"这份工程是从动图来的"这句记忆（切标签页、来回转换都靠它）。

    记的是**原图下标**而不是矩形：用户拖动原图之后矩形会变、下标不变 —— 用的时候再去
    ``viewer.source_rects()`` 取当前矩形，拖动过也照样对得上。
    """

    source_indices: list[int]            # 每帧对应哪张原图（顺序 = 动图帧序）
    durations: list[int]                 # 逐帧时长（毫秒）
    action: Action | None = None         # 导入时自动建的那个动作（切帧、取时长用）
    name: str = ""
    path: str = ""
    source_total: int = 0                # 记下来时的原图总数（数量变了就说明对不上了）

    @property
    def count(self) -> int:
        return len(self.source_indices)


class AnimDocBar(QWidget):
    """动图那条横条：**两种模式共用**（用户口径：序列帧模式也要显示这个 gif 工具）。

    - 动图文档模式：看第几帧 + 提示 + 四个整段变换按钮 + 「转为序列帧」；
    - 序列帧模式：看第几帧 + 「转为动图文档」。这里**不放**那四个变换按钮 —— 序列帧那边
      「动画设置」横条里的变换区已经在管"作用于全部帧"，摆两套只会让人不知道用哪个。

    「查看第几帧」两种模式都**只看**：文档模式改的是 ``doc.index``，序列帧模式只是把预览
    切到那一帧，两边都不动任何数据。

    为什么排两行：一行里塞标题 + 帧信息 + 滑块 + 提示 + 四个按钮，实测推荐宽度 **1153px**，
    而左侧画布区只有 **588px** —— 右边直接被切掉（用户报的"一行显示不全"）。
    """

    frame_changed = pyqtSignal(int)          # 只看：切到第几帧
    transform_requested = pyqtSignal(str)    # flip_h / flip_v / rot90 / rot180
    convert_requested = pyqtSignal()         # 转换按钮：按当前模式决定方向

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._doc: AnimDoc | None = None
        self._source: FramesSource | None = None
        self._rects: list[tuple[int, int, int, int]] = []
        self._convert_text = ""
        self._convert_tip = ""
        col = QVBoxLayout(self)
        col.setContentsMargins(8, 4, 8, 4)
        col.setSpacing(4)

        # 第一行：标题 + 帧信息 + 提示 +「转换模式」按钮
        row1 = QHBoxLayout()
        row1.setSpacing(8)
        self.lbl_title = QLabel("动图文档")
        self.lbl_title.setStyleSheet("color: #F06292; font-weight: bold; font-size: 12px;")
        row1.addWidget(self.lbl_title)

        self.lbl_frame = QLabel("第 1 / 1 帧")
        self.lbl_frame.setStyleSheet("color: #e6e6e6; font-size: 12px;")
        row1.addWidget(self.lbl_frame)

        self.lbl_hint = QLabel("改动同步全部帧")
        self.lbl_hint.setStyleSheet("color: #9a9a9a; font-size: 11px;")
        # 提示是**次要信息**：宽度不够时让它先被挤掉，别把整条横条的最小宽度顶上去
        # （它就是"一行显示不全"的元凶之一 —— 标签的最小宽度是照文字算的，挤不动）
        self.lbl_hint.setSizePolicy(QSizePolicy.Policy.Ignored,
                                    QSizePolicy.Policy.Preferred)
        row1.addWidget(self.lbl_hint)
        row1.addStretch(1)

        self.btn_convert = QPushButton("转为序列帧")
        self.btn_convert.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_convert.clicked.connect(lambda: self.convert_requested.emit())
        self._style_button(self.btn_convert)
        row1.addWidget(self.btn_convert)

        # 第二行：滑块（吃掉剩余宽度）+ 四个整段变换按钮
        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.setMinimumWidth(140)
        self.slider.setToolTip("只看第几帧（不改变编辑范围：所有改动照样作用于全部帧）")
        self.slider.valueChanged.connect(self._on_slider)
        row2.addWidget(self.slider, 1)

        self._buttons: dict[str, QPushButton] = {}
        for kind, text, tip in TRANSFORM_BUTTONS:
            button = QPushButton(text)
            button.setToolTip(tip)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            self._style_button(button)
            button.clicked.connect(lambda _=False, k=kind: self.transform_requested.emit(k))
            row2.addWidget(button)
            self._buttons[kind] = button

        col.addLayout(row1)
        col.addLayout(row2)
        self._set_convert_text("转为序列帧", "")
        self.setEnabled(False)

    @staticmethod
    def _style_button(button: QPushButton) -> None:
        button.setStyleSheet(
            "QPushButton { background: #333333; border: 1px solid #484848;"
            " border-radius: 4px; color: #e6e6e6; font-size: 11px; padding: 3px 10px; }"
            "QPushButton:hover { background: #434343; border-color: #F06292;"
            " color: #ffffff; }"
            "QPushButton:pressed { background: #2a2a2a; border-color: #E91E63; }"
            "QPushButton:disabled { color: #7a7a7a; background: #2e2e2e;"
            " border-color: #3b3b3b; }"
        )

    # ------------------------------------------------------------ 两种模式
    def set_doc(self, doc: AnimDoc | None) -> None:
        """动图文档模式（``doc`` 为 None = 这条横条不适应）。"""
        self._doc = doc
        self._source = None
        self._rects = []
        self._show_transform_buttons(True)
        self._set_convert_text(
            "转为序列帧",
            "把现在这一帧帧（改过的样子）铺成序列帧工程：每帧一张原图 + 自动建一个动作")
        if doc is None:
            self.lbl_title.setText("动图文档")
            self.lbl_frame.setText("第 1 / 1 帧")
            self.lbl_hint.setText("改动同步全部帧")
            self.lbl_hint.setToolTip("所有改动（抠背景 / 画笔 / 翻转旋转…）都同步到全部帧")
            self._set_slider(0, 0, 0)
            self.setEnabled(False)
            return
        self.setEnabled(True)
        self.lbl_title.setText(f"动图文档 · {doc.name}" if doc.name else "动图文档")
        self._set_slider(0, max(0, doc.count - 1), max(0, min(doc.index, doc.count - 1)))
        self._sync_label()

    def set_frames_state(self, source: FramesSource | None,
                         rects: list[tuple[int, int, int, int]] | None = None,
                         index: int = 0) -> None:
        """序列帧模式：横条只留"看第几帧"和"转为动图文档"。

        ``rects`` 由主窗口按**当前**原图位置算好传进来（弹窗这边不碰查看器）。
        """
        self._doc = None
        self._source = source
        self._rects = [tuple(int(v) for v in r) for r in (rects or [])]
        self._show_transform_buttons(False)
        self._set_convert_text(
            "转为动图文档",
            "收回成动图文档：拿这几帧**现在的像素**（改过的样子）建一个动图文档")
        if source is None:
            self.lbl_title.setText("序列帧")
            self.lbl_frame.setText("第 1 / 1 帧")
            self._set_slider(0, 0, 0)
            self.setEnabled(False)
            return
        self.setEnabled(True)
        self.lbl_title.setText(f"序列帧 · {source.name}" if source.name else "序列帧")
        self.lbl_hint.setText("只看，不改数据")
        self.lbl_hint.setToolTip("「查看第几帧」只切当前帧：不改选中、不改任何数据")
        self._set_slider(0, max(0, source.count - 1), max(0, min(int(index), source.count - 1)))
        self._sync_label()

    def set_convert_enabled(self, ok: bool, why: str = "") -> None:
        """转换按钮的可用性（不可用时写明原因，别留一个点了没反应的按钮）。"""
        self.btn_convert.setEnabled(bool(ok))
        self.btn_convert.setToolTip(why or self._convert_tip)

    def _set_convert_text(self, text: str, tip: str) -> None:
        self._convert_text, self._convert_tip = text, tip
        self.btn_convert.setText(text)
        self.btn_convert.setToolTip(tip)

    def _show_transform_buttons(self, on: bool) -> None:
        for button in self._buttons.values():
            button.setVisible(on)

    def _set_slider(self, low: int, high: int, value: int) -> None:
        self.slider.blockSignals(True)
        self.slider.setRange(low, high)
        self.slider.setValue(value)
        self.slider.blockSignals(False)

    # ------------------------------------------------------------ 刷新
    def refresh(self) -> None:
        """操作落地之后刷一下（帧数/尺寸可能变了：转 90° 宽高互换）。"""
        if self._doc is not None:
            self._set_slider(0, max(0, self._doc.count - 1),
                             max(0, min(self._doc.index, self._doc.count - 1)))
        self._sync_label()

    def _sync_label(self) -> None:
        if self._doc is not None:
            pm = self._doc.frame()
            size = f"{pm.width()}×{pm.height()}" if pm is not None else "—"
            self.lbl_frame.setText(
                f"第 {self._doc.index + 1} / {self._doc.count} 帧 · {size} · "
                f"{self._doc.duration_at(self._doc.index)}ms"
            )
            return
        if self._source is None or not self._rects:
            return
        i = max(0, min(self.slider.value(), min(self._source.count, len(self._rects)) - 1))
        rect = self._rects[i]
        ms = (self._source.durations[i] if i < len(self._source.durations)
              else DEFAULT_FRAME_MS)
        self.lbl_frame.setText(
            f"第 {i + 1} / {self._source.count} 帧 · {rect[2]}×{rect[3]} · {ms}ms")

    def _on_slider(self, value: int) -> None:
        self._sync_label()
        self.frame_changed.emit(int(value))