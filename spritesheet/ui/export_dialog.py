#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""导出弹窗：动作选择 + 预览 + 导出选项（从 main_window.py 拆出，纯搬移，逻辑未改动）。

包含：
- ``PreviewArea``：中间预览区（棋盘格背景 + 滚轮缩放 / 拖动平移 / 双击重置）；
- ``_safe_export_name`` / ``_pixmap_to_pil``：导出文件名净化与 QPixmap→Pillow 转换；
- ``ExportDialog``：左侧动作选择（循环小预览 + 胶囊开关）、中间预览、右侧导出选项，
  以及图集打包（普通帧按元素图块去重复用、合成帧整块打包）、GIF、多格式 JSON、
  .sstool 工程导出与进度回调。

依赖方向：models ← transform ← widgets ← export_dialog（本模块不依赖 main_window）。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import re
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QDialog, QFileDialog, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QMessageBox, QProgressDialog, QPushButton,
    QRadioButton, QScrollArea, QVBoxLayout, QWidget,
)

from spritesheet import __app_name__, __version__, telemetry
from spritesheet.project_file import write_project
from spritesheet.settings import last_directory, set_last_directory
from spritesheet.ui.exporters import (
    FMT_NATIVE, FORMAT_CHOICES, FORMAT_SUFFIX, build_payload, write_payload,
)
from .models import Action
from .transform import (
    DEFAULT_QUAD, DEFAULT_ROTATION_PIVOT, action_anchor_pixels, action_frame_pixmap,
    action_frame_size, compute_preview_layout, transform_frame_pixmap,
)
from .widgets import ActionThumbPlayer, SwitchWidget, paint_checkerboard


class PreviewArea(QWidget):
    """导出弹窗中间预览区：棋盘格背景 + 居中缩放显示一张预览图。
    支持滚轮缩放、鼠标拖动平移、双击重置。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self.setMinimumSize(220, 220)
        self.setStyleSheet("background: #141414; border: 1px solid #4a4a4a; border-radius: 4px;")
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        # 用户缩放（1.0 = 适应窗口）与平移偏移
        self._zoom = 1.0
        self._pan_x = 0
        self._pan_y = 0
        self._dragging = False
        self._last_pos = None

    def set_preview(self, pm: QPixmap | None) -> None:
        self._pixmap = pm
        self._zoom = 1.0
        self._pan_x = 0
        self._pan_y = 0
        self.update()

    def reset_view(self) -> None:
        self._zoom = 1.0
        self._pan_x = 0
        self._pan_y = 0
        self.update()

    def _fit_rect(self):
        """返回当前 pixmap 在控件中的绘制矩形 (x, y, w, h)。"""
        pm = self._pixmap
        if pm is None or pm.isNull():
            return None
        base = min(self.width() / pm.width(), self.height() / pm.height())
        s = base * self._zoom
        dw, dh = pm.width() * s, pm.height() * s
        x = (self.width() - dw) / 2 + self._pan_x
        y = (self.height() - dh) / 2 + self._pan_y
        return x, y, dw, dh

    def wheelEvent(self, event) -> None:
        if self._pixmap is None or self._pixmap.isNull():
            return
        old_pan_x, old_pan_y = self._pan_x, self._pan_y
        old_rect = self._fit_rect()
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self._zoom = max(0.1, min(20.0, self._zoom * factor))
        # 以鼠标位置为缩放中心，保持光标下的图像点不动
        new_rect = self._fit_rect()
        if old_rect and new_rect:
            mx, my = event.position().x(), event.position().y()
            ox, oy, ow, oh = old_rect
            nx, ny, nw, nh = new_rect
            if ow > 0 and oh > 0:
                rx = (mx - ox) / ow
                ry = (my - oy) / oh
                self._pan_x = (mx - nx) - rx * nw + old_pan_x
                self._pan_y = (my - ny) - ry * nh + old_pan_y
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._last_pos = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging and self._last_pos is not None:
            cur = event.position().toPoint()
            self._pan_x += cur.x() - self._last_pos.x()
            self._pan_y += cur.y() - self._last_pos.y()
            self._last_pos = cur
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self._last_pos = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mouseDoubleClickEvent(self, event) -> None:
        self.reset_view()

    def paintEvent(self, event) -> None:
        if self.width() <= 0 or self.height() <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        # 棋盘格背景（一次平铺，替代逐格 fillRect）
        paint_checkerboard(p, self.rect(), 10)
        rect = self._fit_rect()
        if rect is not None:
            x, y, dw, dh = rect
            p.drawPixmap(int(x), int(y), int(dw), int(dh), self._pixmap)
        else:
            p.setPen(QColor("#666"))
            p.setFont(QFont("Microsoft YaHei", 10))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "（无预览）")
        p.end()


def _safe_export_name(name: str, used: set[str] | None = None) -> str:
    """将动作名转换为安全文件名，并处理同名动作。"""
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", (name or "animation")).strip(" .")
    value = value or "animation"
    if used is None:
        return value
    base = value
    index = 2
    while value in used:
        value = f"{base}_{index}"
        index += 1
    used.add(value)
    return value


def _pixmap_to_pil(pixmap: QPixmap):
    """将 QPixmap 转成 Pillow RGBA 图像，保持原始像素尺寸。"""
    from PIL import Image

    image = pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = image.bits()
    ptr.setsize(image.sizeInBytes())
    return Image.frombytes("RGBA", (image.width(), image.height()), bytes(ptr))


class ExportDialog(QDialog):
    """导出弹窗：左侧动作选择（循环小预览+胶囊开关），中间预览，右侧导出选项。"""

    def __init__(self, actions: list, source_pixmap: QPixmap | None = None,
                 project_provider=None, project_name: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("导出")
        self.resize(900, 600)
        # 分组框只保留标题文字，彻底隐藏边框/背景/标题描边
        self.setStyleSheet(
            "QGroupBox { background: transparent; border: none; border-radius: 0px;"
            " margin-top: 14px; padding-top: 0px; font-weight: bold;"
            " color: #F06292; font-size: 12px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 2px; top: 2px;"
            " padding: 0px; background: transparent; border: none; }"
        )
        self.actions = actions
        self._source_pixmap = source_pixmap
        # The provider returns the complete document, so exporting a project
        # never depends on which actions are selected in this dialog.
        self._project_provider = project_provider
        # 导出产物以工程名（当前标签页的名字）开头；没给就退回 spritesheet
        self._project_name = (project_name or "spritesheet").strip() or "spritesheet"
        self._switches: dict = {}
        self._players: list[ActionThumbPlayer] = []
        self._players_by_action: dict = {}
        self._build_ui()
        self._update_preview()

    def _build_ui(self) -> None:
        main = QVBoxLayout(self)
        main.setContentsMargins(12, 12, 12, 12)
        main.setSpacing(10)

        content = QHBoxLayout()
        content.setSpacing(10)

        # ---- 左侧：动作列表（循环小预览 + 名称 + 开关）----
        left_box = QGroupBox("选择导出动作")
        left_layout = QVBoxLayout(left_box)
        left_layout.setContentsMargins(8, 12, 8, 8)
        left_layout.setSpacing(4)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content_widget = QWidget()
        v = QVBoxLayout(content_widget)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        if not self.actions:
            empty = QLabel("（暂无动作）")
            empty.setStyleSheet("color: #666; font-size: 12px;")
            v.addWidget(empty)
        else:
            for act in self.actions:
                # ---- 动作卡牌（单行紧凑布局）----
                card = QFrame()
                card.setStyleSheet(
                    "QFrame { background: #232323; border: 1px solid #3a3a3a; border-radius: 6px; }"
                )
                card_layout = QGridLayout(card)
                card_layout.setContentsMargins(8, 5, 8, 5)
                card_layout.setHorizontalSpacing(8)
                card_layout.setVerticalSpacing(0)
                card_layout.setColumnMinimumWidth(0, 52)
                card_layout.setColumnMinimumWidth(2, 42)

                # 预览窗（左）
                player = ActionThumbPlayer(act, 52)
                self._players.append(player)
                self._players_by_action[act] = player
                card_layout.addWidget(player, 0, 0, 2, 1, Qt.AlignmentFlag.AlignCenter)

                # 动作名 + 信息（中，垂直）
                info_col = QVBoxLayout()
                info_col.setSpacing(1)
                info_col.setContentsMargins(0, 0, 0, 0)
                label = QLabel(act.name)
                label.setStyleSheet(
                    "QLabel { color: #e8e8e8; font-size: 12px; font-weight: bold;"
                    " background: transparent; border: none; }"
                )
                info_col.addWidget(label)
                info_label = QLabel(self._action_info_text(act))
                info_label.setStyleSheet(
                    "QLabel { color: #999;"
                    " background: transparent; border: none; }"
                )
                info_label.setWordWrap(True)
                info_col.addWidget(info_label)
                info_col.addStretch()
                card_layout.addLayout(info_col, 0, 1, 2, 1)

                # 开关（右）
                sw = SwitchWidget(checked=True)
                sw.toggled.connect(
                    lambda checked, action=act: self._on_action_toggle(action, checked)
                )
                self._switches[act] = sw
                card_layout.addWidget(sw, 0, 2, 2, 1, Qt.AlignmentFlag.AlignCenter)

                v.addWidget(card)
        v.addStretch()
        scroll.setWidget(content_widget)
        left_layout.addWidget(scroll)
        content.addWidget(left_box, 23)

        # ---- 中间：预览区 ----
        mid_box = QGroupBox("预览")
        mid_layout = QVBoxLayout(mid_box)
        mid_layout.setContentsMargins(8, 12, 8, 8)
        self.preview_area = PreviewArea()
        mid_layout.addWidget(self.preview_area, 1)
        content.addWidget(mid_box, 30)

        # ---- 右侧：导出选项 ----
        right_box = QGroupBox("导出选项")
        right_layout = QVBoxLayout(right_box)
        right_layout.setContentsMargins(12, 12, 12, 8)
        right_layout.setSpacing(8)

        lbl_mode = QLabel("导出方式")
        lbl_mode.setStyleSheet("color: #F06292; font-weight: bold;")
        right_layout.addWidget(lbl_mode)
        self.rb_original = QRadioButton("原图导出")
        self.rb_repack = QRadioButton("重构导出")
        self.rb_repack.setChecked(True)  # 默认重构导出
        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self.rb_original)
        self._mode_group.addButton(self.rb_repack)
        self.rb_original.toggled.connect(lambda: self._update_preview())
        right_layout.addWidget(self.rb_original)
        right_layout.addWidget(self.rb_repack)

        right_layout.addSpacing(8)
        lbl_extra = QLabel("附加选项")
        lbl_extra.setStyleSheet("color: #F06292; font-weight: bold;")
        right_layout.addWidget(lbl_extra)
        # 顺序即优先级：SSTOOL 工程排第一位（它是最完整的产物），JSON 紧随其后。
        self.cb_project = QCheckBox("导出 SSTOOL 工程")
        # 默认导出：工程文件是最完整的交付物。但工程数据得有人提供 ——
        # 没注入 provider 的调用方（脚本/性能采样）不能默认勾上，否则点导出直接报错。
        has_project = self._project_provider is not None
        self.cb_project.setChecked(has_project)
        self.cb_project.setEnabled(has_project)
        self.cb_project.setToolTip(
            "导出一个包含全部原图和编辑状态的单文件工程" if has_project
            else "当前窗口没有提供工程数据，无法导出 SSTOOL 工程"
        )
        self.cb_gif = QCheckBox("导出 GIF（预览）")
        self.cb_separate = QCheckBox("分别导出")
        self.cb_separate.toggled.connect(self._on_separate_toggled)
        right_layout.addWidget(self.cb_project)
        right_layout.addWidget(self.cb_gif)
        right_layout.addWidget(self.cb_separate)

        # JSON 格式：独立分组框 + 竖排（可多选，同时导出多种格式）
        json_fmt_group = QGroupBox("JSON 格式（可多选）")
        json_fmt_group.setStyleSheet(
            "QGroupBox { background: transparent; border: 1px solid #333; border-radius: 6px;"
            " margin-top: 10px; padding-top: 4px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; top: 0px; padding: 0 4px;"
            " color: #F06292; font-weight: bold; }"
        )
        json_fmt_layout = QVBoxLayout(json_fmt_group)
        json_fmt_layout.setContentsMargins(10, 10, 8, 8)
        json_fmt_layout.setSpacing(4)
        self._json_fmt_checks: dict = {}
        for fmt_id, fmt_label in FORMAT_CHOICES:
            cb = QCheckBox(fmt_label)
            cb.setChecked(fmt_id == FMT_NATIVE)  # 默认只勾本工具格式
            cb.setStyleSheet("color: #ccc;")  # 字号与上方复选框一致（继承默认字体）
            self._json_fmt_checks[fmt_id] = cb
            json_fmt_layout.addWidget(cb)
        for _cb in self._json_fmt_checks.values():
            _cb.toggled.connect(lambda _=False: self._update_preview())
        right_layout.addWidget(json_fmt_group)

        right_layout.addSpacing(8)
        lbl_info_title = QLabel("图像信息")
        lbl_info_title.setStyleSheet("color: #F06292; font-weight: bold;")
        right_layout.addWidget(lbl_info_title)
        self.lbl_preview_info = QLabel("")
        self.lbl_preview_info.setWordWrap(True)
        self.lbl_preview_info.setStyleSheet(
            "QLabel { color: #aaa; background: #232323; "
            "border: 1px solid #3a3a3a; border-radius: 4px; padding: 6px; }"
        )
        right_layout.addWidget(self.lbl_preview_info)

        right_layout.addStretch()
        content.addWidget(right_box, 20)

        main.addLayout(content, 1)

        # ---- 底部按钮 ----
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_cancel = QPushButton("取消")
        btn_cancel.setFixedSize(80, 30)
        btn_cancel.clicked.connect(self.reject)
        btn_export = QPushButton("导出")
        btn_export.setFixedSize(80, 30)
        btn_export.setStyleSheet(
            "QPushButton { background: #E91E63; color: white; border: none; border-radius: 4px; }"
            "QPushButton:hover { background: #F06292; }"
        )
        btn_export.clicked.connect(self._perform_export)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_export)
        main.addLayout(btn_row)

    # ---- 预览 ----
    def _on_action_toggle(self, action, checked: bool) -> None:
        """动作取消导出时立即停止缩略图定时器，避免后台无效播放。"""
        player = self._players_by_action.get(action)
        if player is not None:
            if checked:
                player.start()
            else:
                player.stop()
        self._update_preview()

    def _on_separate_toggled(self, checked: bool) -> None:
        """拆分导出必须使用重构帧，确保每个动作都有独立自洽的图集。"""
        self.rb_original.setEnabled(not checked)
        if checked and self.rb_original.isChecked():
            self.rb_repack.setChecked(True)
        # 分别导出时预览展示的是第 1 个动作的图集，需要同步刷新说明
        self._update_preview()

    @staticmethod
    def _action_info_text(act) -> str:
        """动作信息摘要：帧数 · 帧尺寸 · 帧率 · 循环模式 · 动作类型。"""
        n_frames = len(act.frames)
        if getattr(act, "frame_rects", None):
            max_w = max(r[2] for r in act.frame_rects)
            max_h = max(r[3] for r in act.frame_rects)
            size_text = f"{max_w}×{max_h}"
        else:
            size_text = "—"
        fps = int(getattr(act, "fps", 10) or 10)
        loop_map = {Action.LOOP: "循环", Action.ONCE: "单次", Action.PINGPONG: "往返"}
        loop_text = loop_map.get(getattr(act, "loop_mode", Action.LOOP), "循环")
        atype = "移动" if getattr(act, "action_type", "static") == "move" else "静止"
        return f"{n_frames}帧·{size_text}·{fps}FPS·{loop_text}·{atype}"

    def _build_repack_pixmap(self) -> QPixmap | None:
        """使用与导出完全相同的参数生成预览，确保预览与实际 PNG 一致。

        合成帧一律按整块打包（与导出一致，图集可能略大但画面必然相同）；
        分别导出时每个动作单独一张图集 -> 预览展示第 1 个动作的那张。
        """
        selected = self.selected_actions()
        if self.cb_separate.isChecked():
            selected = selected[:1]
        atlas, _ = self._build_packed_atlas(selected)
        return atlas

    @staticmethod
    def _final_frames(action: Action) -> list[QPixmap]:
        """生成动作最终帧：统一画布、padding 和单帧偏移均在此应用。"""
        if not action.frames:
            return []
        canvas_w, canvas_h, positions, _ = compute_preview_layout(action, 0)
        if canvas_w <= 0 or canvas_h <= 0:
            return []
        result = []
        for index, frame in enumerate(action.frames):
            if frame.isNull():
                continue
            x, y, _, _ = positions[index]
            canvas = QPixmap(canvas_w, canvas_h)
            canvas.fill(Qt.GlobalColor.transparent)
            painter = QPainter(canvas)
            painter.drawPixmap(x, y, action_frame_pixmap(action, index))
            painter.end()
            result.append(canvas)
        return result

    @staticmethod
    def _pack_frames(frames: list[QPixmap], gap: int = 1) -> tuple[QPixmap | None, list[tuple[int, int, int, int]]]:
        """用紧凑 shelf packing 排列帧，保持帧像素大小不变。"""
        if not frames:
            return None, []
        total_area = sum(frame.width() * frame.height() for frame in frames)
        max_width = max(frame.width() for frame in frames)
        sum_width = sum(frame.width() for frame in frames) + gap * (len(frames) - 1)
        target_width = max(max_width, int(math.ceil(math.sqrt(max(1, total_area)))))
        # 用固定数量的候选宽度覆盖从最窄到横向排列，避免大图时采样范围失控。
        candidates = {max_width, target_width, sum_width}
        span = max(0, sum_width - max_width)
        sample_count = min(64, max(12, len(frames) * 4))
        if span:
            for step in range(sample_count + 1):
                ratio = step / sample_count
                candidates.add(max_width + round(span * ratio))
        candidates = sorted(max(max_width, min(sum_width, width)) for width in candidates)
        candidates = list(dict.fromkeys(candidates))

        layouts = []
        ordered = sorted(range(len(frames)), key=lambda i: (frames[i].height(), frames[i].width()), reverse=True)
        for width in candidates:
            rows: list[list[int]] = []
            row_widths: list[int] = []
            row_heights: list[int] = []
            for index in ordered:
                frame = frames[index]
                placed = False
                for row, used_width in enumerate(row_widths):
                    needed = frame.width() + (gap if rows[row] else 0)
                    if used_width + needed <= width:
                        rows[row].append(index)
                        row_widths[row] += needed
                        row_heights[row] = max(row_heights[row], frame.height())
                        placed = True
                        break
                if not placed:
                    rows.append([index])
                    row_widths.append(frame.width())
                    row_heights.append(frame.height())
            used_width = max(row_widths, default=0)
            height = sum(row_heights) + gap * max(0, len(rows) - 1)
            area = used_width * height
            aspect_error = abs(math.log(max(1, used_width) / max(1, height)))
            layouts.append((area, aspect_error, used_width, rows, row_widths, row_heights, height))

        # 先保持面积接近最优，再在 5% 面积容差内优先选择接近正方形的布局。
        min_area = min(layout[0] for layout in layouts)
        eligible = [layout for layout in layouts if layout[0] <= min_area * 1.05]
        best = min(eligible, key=lambda layout: (layout[1], layout[0], abs(layout[2] - layout[6])))

        _, _, width, rows, row_widths, row_heights, height = best
        positions = [None] * len(frames)
        y = 0
        for row, indices in enumerate(rows):
            x = 0
            for index in indices:
                frame = frames[index]
                positions[index] = (x, y, frame.width(), frame.height())
                x += frame.width() + gap
            y += row_heights[row] + gap
        canvas = QPixmap(max(1, width), max(1, height))
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        for index, frame in enumerate(frames):
            x, y, _, _ = positions[index]
            painter.drawPixmap(x, y, frame)
        painter.end()
        return canvas, positions

    def _build_packed_atlas(self, actions: list[Action]):
        """重构打包：普通帧以「元素图块」为单位打包并内容去重；合成帧直接打包整块。

        图集内容分两类：
        - 普通帧：登记为去重后的**元素图块**（原图中被检测出的元素原像素），
          同一元素被多个动作/帧复用时图集里只存一份；
        - 合成帧：直接打包拼接好的**完整整块**（一帧一块，不拆部件、不参与图块复用）。

        合成帧不复用的原因：parts 拆装链路复杂，容易在导出/导入往返中引入
        部件丢失或位置错位；整块导出后，第三方格式与本工具格式的表现完全一致，
        图集略大，但导出安全性与画面正确性最高（以导出安全为基准）。
        """
        src = self._source_pixmap
        tile_pixmaps: list[QPixmap] = []      # 待打包的图块/整块
        tile_index: dict[str, int] = {}       # 内容哈希 -> 图块下标（快速索引）
        tile_bytes: list[bytes] = []

        def content_of(pm: QPixmap) -> bytes:
            img = pm.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
            buf = img.constBits()
            buf.setsize(img.sizeInBytes())
            return bytes(buf)

        def register(pm: QPixmap) -> int:
            """登记一个图块，返回其在唯一图块表里的下标（内容相同则复用）。"""
            raw = content_of(pm)
            key = hashlib.sha1(raw).hexdigest()
            for cand in tile_index.get(key, ()):   # hash 命中后逐字节复核
                if tile_bytes[cand] == raw:
                    return cand
            idx = len(tile_pixmaps)
            tile_index.setdefault(key, []).append(idx)
            tile_pixmaps.append(pm)
            tile_bytes.append(raw)
            return idx

        frames_info = []  # 每帧: (action, index, 帧在画布中的(x,y), (cw,ch), parts, frame)
        for action in actions:
            if not action.frames:
                continue
            cw, ch, positions, _ = compute_preview_layout(action, 0)
            for index, frame in enumerate(action.frames):
                if frame.isNull():
                    continue
                fx, fy, _, _ = positions[index]
                raw_parts = action.frame_parts[index] if index < len(action.frame_parts) else []
                rot = action.frame_rotations.get(index, 0)
                mh = index in action.frame_mirror_h
                mv = index in action.frame_mirror_v
                quad = action.frame_quads.get(index)
                pivot = action.frame_rotation_pivots.get(index, DEFAULT_ROTATION_PIVOT)
                parts: list[tuple[int, int, int, int, int]] = []   # (tile_id, rel_x, rel_y, origin_x, origin_y)
                if len(raw_parts) > 1:
                    # 合成帧：整块直接入表（不复用、不拆部件），一帧一块。
                    tile_idx = len(tile_pixmaps)
                    tile_pixmaps.append(transform_frame_pixmap(frame, rot, mh, mv, quad, pivot))
                    parts.append((tile_idx, 0, 0, 0, 0))
                elif raw_parts:
                    # 普通帧（单部件）：以元素图块登记，内容去重复用。
                    part = raw_parts[0]
                    # 新结构：(裁剪矩形, 帧内x, 帧内y, 逻辑x, 逻辑y)
                    if len(part) == 5:
                        srect, rx, ry, ox, oy = part
                    else:  # 兼容旧结构
                        srect, rx, ry = part
                        ox, oy = srect[0], srect[1]
                    parts.append((register(transform_frame_pixmap(src.copy(*srect), rot, mh, mv, quad, pivot)), rx, ry, ox, oy))
                else:
                    rect = action.frame_rects[index] if index < len(action.frame_rects) else None
                    if rect:
                        parts.append((register(transform_frame_pixmap(src.copy(*rect), rot, mh, mv, quad, pivot)), 0, 0, rect[0], rect[1]))
                frames_info.append((action, index, (fx, fy), (cw, ch), parts, frame))

        atlas, positions = self._pack_frames(tile_pixmaps)

        records = {}
        for action, index, (fx, fy), (cw, ch), parts, frame in frames_info:
            parts_out = []
            for tid, rx, ry, ox, oy in parts:
                tx, ty, tw, th = positions[tid]
                parts_out.append({
                    "rect": (tx, ty, tw, th),   # 图集中的位置（裁剪用）
                    "pos": (rx, ry),            # 帧内相对位置
                    "origin": (ox, oy),         # 元素的逻辑坐标（元素层关系）
                })
            first = parts_out[0]["rect"] if parts_out else (0, 0, 0, 0)
            records[(action, index)] = {
                "parts": parts_out,
                "rect": first,
                "composite_rect": None,         # 兼容旧记录读取；合成帧现已是整块，不再单独打包
                "offset": (fx, fy),
                "source": action_frame_size(action, index),
                "canvas": (cw, ch),
                "trimmed": False,
            }
        return atlas, records

    def _build_original_atlas(self, actions: list[Action]):
        """原图模式直接输出源图，并使用原图中的元素矩形。

        存在旋转/镜像帧时，源图无法直接表达变换：把变换帧烘焙成图块
        **追加到原图下方**（未变换帧仍引用原图区域），保证 JSON 引用
        与实际画面一致（GIF 始终走 _final_frames，天然带变换）。
        """
        if self._source_pixmap is None or self._source_pixmap.isNull():
            return None, {}
        src = self._source_pixmap

        def frame_has_transform(action: Action, index: int) -> bool:
            return bool(action.frame_rotations.get(index, 0)
                        or index in action.frame_mirror_h
                        or index in action.frame_mirror_v
                        or action.frame_quads.get(index) not in (None, DEFAULT_QUAD))

        need_bake = [
            (action, index, rect)
            for action in actions
            for index, rect in enumerate(action.frame_rects)
            if 0 <= index < len(action.frames) and frame_has_transform(action, index)
        ]
        records = {}

        if not need_bake:
            for action in actions:
                for index, rect in enumerate(action.frame_rects):
                    x, y, width, height = rect
                    records[(action, index)] = {
                        # 原图模式下帧直接引用原图区域，等价于「单部件、部件即在原图中的位置」
                        "parts": [{"rect": (x, y, width, height), "pos": (0, 0)}],
                        "rect": (x, y, width, height),
                        "composite_rect": (x, y, width, height),
                        "offset": (0, 0),
                        "source": (width, height),
                        "canvas": (width, height),
                        "trimmed": False,
                    }
            return src, records

        # 有变换帧：原图下方追加变换块（横向居中，逐行排布）
        baked = [(action, index, action_frame_pixmap(action, index)) for action, index, _ in need_bake]
        baked_w = max((pm.width() for _, _, pm in baked), default=1)
        baked_h = sum(pm.height() for _, _, pm in baked) + max(0, len(baked) - 1) + 2
        canvas_w = max(src.width(), baked_w + 2)
        canvas = QPixmap(canvas_w, src.height() + baked_h)
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        painter.drawPixmap(0, 0, src)
        cur_y = src.height() + 2
        bake_rects: dict[tuple, tuple[int, int, int, int]] = {}
        for action, index, pm in baked:
            bx = max(1, (canvas_w - pm.width()) // 2)
            painter.drawPixmap(bx, cur_y, pm)
            bake_rects[(action, index)] = (bx, cur_y, pm.width(), pm.height())
            cur_y += pm.height() + 1
        painter.end()

        for action in actions:
            for index, rect in enumerate(action.frame_rects):
                if 0 <= index < len(action.frames) and frame_has_transform(action, index):
                    bx, by, bw, bh = bake_rects[(action, index)]
                    records[(action, index)] = {
                        "parts": [{"rect": (bx, by, bw, bh), "pos": (0, 0)}],
                        "rect": (bx, by, bw, bh),
                        "composite_rect": (bx, by, bw, bh),
                        "offset": (0, 0),
                        "source": (bw, bh),
                        "canvas": (bw, bh),
                        "trimmed": False,
                    }
                else:
                    x, y, width, height = rect
                    records[(action, index)] = {
                        "parts": [{"rect": (x, y, width, height), "pos": (0, 0)}],
                        "rect": (x, y, width, height),
                        "composite_rect": (x, y, width, height),
                        "offset": (0, 0),
                        "source": (width, height),
                        "canvas": (width, height),
                        "trimmed": False,
                    }
        return canvas, records

    def _update_preview(self) -> None:
        if self.rb_original.isChecked():
            pm, _ = self._build_original_atlas(self.selected_actions())
            mode_name = "原图"
        else:
            pm = self._build_repack_pixmap()
            mode_name = "重构图"
        self.preview_area.set_preview(pm)
        # 信息栏要说明"这张预览对应导出里的哪张图"，避免与实际产出不符
        note = ""
        if mode_name == "重构图":
            if self.cb_separate.isChecked():
                n = len(self.selected_actions())
                note = f"　·　分别导出：共 {max(1, n)} 张，此处为第 1 张"
            else:
                note = "　·　合成帧按整块打包（图集略大，导出更安全）"
        # 更新预览图尺寸信息
        if pm is not None and not pm.isNull():
            pixels = pm.width() * pm.height()
            if pixels >= 1_000_000:
                px_text = f"{pixels / 1_000_000:.1f}MP"
            elif pixels >= 1_000:
                px_text = f"{pixels / 1_000:.0f}K"
            else:
                px_text = f"{pixels}"
            self.lbl_preview_info.setText(
                f"{mode_name}：{pm.width()}×{pm.height()} px · {px_text}{note}"
            )
        else:
            self.lbl_preview_info.setText(f"{mode_name}：（无内容）")

    @staticmethod
    def _animation_json(action: Action, atlas_name: str, gif_name: str | None, records: dict) -> dict:
        frames = []
        for index, frame in enumerate(action.frames):
            record = records.get((action, index))
            if record is None:
                continue
            frame_ox, frame_oy = record["offset"]
            sw, sh = record["source"]
            parts = record.get("parts") or []
            parts_json = []
            for part in parts:
                px, py, pw, ph = part["rect"]
                rx, ry = part["pos"]
                po_x, po_y = part.get("origin", (0, 0))
                parts_json.append({
                    "rect": {"x": px, "y": py, "width": pw, "height": ph},
                    "pos": {"x": rx, "y": ry},
                    "origin": {"x": po_x, "y": po_y},
                })
            x, y, width, height = record["rect"]
            frames.append({
                "index": index,
                # parts：该帧由哪些图块组成（普通帧 1 个，合成帧多个）。
                # 渲染时把每个图块的 rect 裁出、贴到「帧在画布中的 offset + 该部件的 pos」。
                "parts": parts_json,
                "rect": {"x": x, "y": y, "width": width, "height": height},
                "source_size": {"width": sw, "height": sh},
                "offset": {"x": frame_ox, "y": frame_oy},
                "trimmed": bool(record.get("trimmed", False)),
                "duration_ms": action.frame_duration_ms(index),
            })
        canvas_w, canvas_h, _, _ = compute_preview_layout(action, 0) if action.frames else (0, 0, [], (0, 0, 0))
        anchor_x, anchor_y = action_anchor_pixels(action, canvas_w, canvas_h)
        result = {
            "name": action.name,
            "color": action.color,
            "type": action.action_type,
            "fps": action.fps,
            "loop": action.loop_mode,
            "canvas": {"width": canvas_w, "height": canvas_h, "padding": action.padding},
            "anchor": {
                "x": anchor_x,
                "y": anchor_y,
                "unit": "px",
                "space": "action_canvas",
                "normalized": {"x": action.anchor[0], "y": action.anchor[1]},
            },
            "ground": {"enabled": action.ground_enabled, "offset": action.ground_offset},
            "move": {
                "direction": action.move_direction,
                "speed": action.move_speed,
            } if action.action_type == "move" else None,
            "atlas": atlas_name,
            "frames": frames,
        }
        if gif_name:
            result["gif"] = gif_name
        return result

    @staticmethod
    def _write_gif(path: Path, action: Action, frames: list[QPixmap]) -> None:

        if not frames:
            return
        images = [_pixmap_to_pil(frame) for frame in frames]
        # 逐帧时长（毫秒）；往返播放时反向段与正向帧时长一一对应
        durations = [action.frame_duration_ms(i) for i in range(len(images))]
        if action.loop_mode == Action.PINGPONG and len(images) > 1:
            images = images + images[-2:0:-1]
            durations = durations + durations[-2:0:-1]
        duration = durations
        loop = 0 if action.loop_mode == Action.LOOP else 1
        images[0].save(
            str(path),
            format="GIF",
            save_all=True,
            append_images=images[1:],
            duration=duration,
            loop=loop,
            disposal=2,
            optimize=False,
        )

    def _write_json(self, path: Path, atlas_name: str, atlas: QPixmap, animations: list[dict]) -> None:
        # 汇总「元素表」：按 (图集位置, 逻辑坐标) 去重，给每个元素分配稳定 id，
        # 并让 frames[].parts 通过 element 字段引用它。
        # elements 让消费方（尤其是 AI）能看出图集里哪些图块属于同一元素、
        # 以及它们在原始素材中的相对位置；导入本软件时也靠它还原元素坐标系。
        elements: list[dict] = []
        element_ids: dict[tuple, int] = {}
        for anim in animations:
            for fr in anim.get("frames", []):
                for part in fr.get("parts", []):
                    r = part["rect"]
                    o = part.get("origin", {"x": 0, "y": 0})
                    key = (r["x"], r["y"], r["width"], r["height"], o["x"], o["y"])
                    eid = element_ids.get(key)
                    if eid is None:
                        eid = len(elements)
                        element_ids[key] = eid
                        elements.append({
                            "id": eid,
                            "rect": dict(r),
                            "origin": {"x": o["x"], "y": o["y"]},
                        })
                    part["element"] = eid
        payload = {
            "format": "spritesheet-animation",
            "version": "1.1",
            "generator": {"name": __app_name__, "version": __version__},
            "atlas": {"image": atlas_name, "width": atlas.width(), "height": atlas.height()},
            "elements": elements,
            "animations": animations,
            "usage": {
                "summary": "animations 是动作数组（不是字典）。每个动作有一个逻辑画布 canvas；每一帧由若干「图块部件」组成（frames[].parts）：把每个部件的 rect 从 atlas.image 裁出，贴到画布坐标 (帧.offset + 部件.pos)，就得到该帧的完整画面。普通帧只有 1 个部件，合成帧有多个（例如纵向拼接的多个元素）。按 fps（或 duration_ms）播放，用 anchor 定位角色原点。",
                "animations_is_array": "animations 是数组：请按索引或遍历 name 字段定位动作，不要写成 animations['name']。",
                "coordinate_system": "两套坐标不要混用：parts[].rect 的 x/y 是图集坐标（从图集左上角算）；frames[].offset 与 parts[].pos 是逻辑画布坐标（从画布左上角算）。图块的最终落点 = offset + pos。",
                "canvas": "canvas 是动作的逻辑画布尺寸（width×height）。它只用于对齐：把所有图块按 offset+pos 贴到这张画布上，使各帧元素位置稳定（播放不抖动）。图集里的图块本身是元素原始像素，不含为对齐而填充的透明边。",
                "parts": "frames[].parts 是该帧的组成部件数组。每个部件 = 一个图块：rect 指明它在图集中的位置，pos 指明它相对帧左上角的偏移。同一图块会被多个帧/多个动作复用（合成帧也复用元素图块），所以图集里不会有重复内容。",
                "source_size": "source_size 是该帧的整体尺寸（合成帧为多个部件的外接尺寸），仅作参考；渲染以 parts 为准。",
                "anchor": "anchor 是动作逻辑画布内的锚点像素坐标（取值范围 0..canvas 对应轴），用于定位角色原点；不是图集坐标。",
                "trimmed": "固定为 false：图块就是元素原始像素，图集里没有为对齐而填充的透明边。",
                "loop_values": {"loop": "循环", "once": "播放一次", "pingpong": "往返播放"},
                "fields": {
                    "animations[]": {
                        "name": "动作名（用于显示/查找，不一定唯一，按 name 查时取第一个匹配即可）",
                        "color": "动作的界面标识色（#RRGGBB），仅用于显示，不影响画面",
                        "type": "动作类型：static=静止，move=移动（配合 move 字段描述移动语义）",
                        "fps": "默认播放帧率（帧/秒）；某帧未单独指定时长时按它计算（默认 duration_ms = round(1000/fps)）",
                        "loop": "循环模式：loop/once/pingpong，见 usage.loop_values",
                        "canvas": {"width": "逻辑画布宽（像素），用于对齐帧", "height": "逻辑画布高（像素）", "padding": "画布四周扩展的安全边距（像素）"},
                        "anchor": {
                            "x": "锚点 X 像素坐标（0..canvas.width）",
                            "y": "锚点 Y 像素坐标（0..canvas.height）",
                            "unit": "固定为 px",
                            "space": "固定为 action_canvas：坐标属于该动作自己的画布，不是图集坐标",
                            "normalized": {"x": "锚点归一化 X（0~1，0=画布左缘，1=右缘）", "y": "锚点归一化 Y（0~1，0=画布上缘，1=下缘）"},
                        },
                        "ground": {
                            "enabled": "是否启用模拟地面线",
                            "offset": "地面线相对自动位置（主体最底部）的向上偏移像素；仅 ground.enabled=true 时有意义",
                        },
                        "move": "type=move 时是对象（含 direction/speed）；type=static 时为 null，忽略即可。",
                        "move.direction": "移动方向：up/down/left/right（仅 type=move 时存在）",
                        "move.speed": "移动速度（1~100，相对值，单位由使用方定义；仅 type=move 时存在）",
                        "atlas": "该动作的帧所在图集文件名（= 顶层 atlas.image）",
                        "frames[]": {
                            "index": "帧序号（0 起，连续）",
                            "parts[]": {
                                "rect": {"x": "图块在图集中的 X", "y": "图块在图集中的 Y", "width": "图块宽（= 元素原始宽）", "height": "图块高（= 元素原始高）"},
                                "pos": {"x": "该图块相对帧左上角的水平偏移", "y": "该图块相对帧左上角的垂直偏移"},
                            },
                            "rect": "便捷字段：单部件帧即该图块位置；多部件帧时请改用 parts。",
                            "source_size": {"width": "该帧整体宽（合成帧为外接宽）", "height": "该帧整体高"},
                            "offset": {"x": "该帧在逻辑画布中的左上角 X", "y": "该帧在逻辑画布中的左上角 Y"},
                            "trimmed": "固定为 false：图块未做透明边裁剪",
                            "duration_ms": "该帧显示时长（毫秒）；可逐帧不同，未单独调整时 = round(1000/fps)",
                        },
                    },
                    "atlas": {"image": "图集图片文件名（与 JSON 同目录）", "width": "图集宽（像素）", "height": "图集高（像素）"},
                },
                "steps": [
                    "读取 atlas.image 图片。",
                    "遍历 animations（数组），对每个动作 anim：",
                    "  建一张 anim.canvas.width × anim.canvas.height 的透明画布（逻辑画布）。",
                    "  对第 i 帧：遍历 anim.frames[i].parts，按 part.rect 从图集裁出图块，",
                    "  贴到画布坐标 (frame.offset.x + part.pos.x, frame.offset.y + part.pos.y)。",
                    "  这张画布就是该帧的完整画面；按 anim.fps（或 frames[i].duration_ms）显示，循环行为见 loop。",
                    "  用 anim.anchor 定位角色原点（锚点在逻辑画布坐标系内）。",
                    "  type=move 时，move.direction / move.speed 描述角色移动动画的走向与相对速度（可作播放方向/速率提示）。",
                ],
                "example": {
                    "language": "python",
                    "code": (
                        "import json\n"
                        "from PIL import Image\n"
                        "data = json.load(open('spritesheet.json', encoding='utf-8'))\n"
                        "atlas = Image.open(data['atlas']['image'])\n"
                        "for anim in data['animations']:  # animations 是数组\n"
                        "    cw = anim['canvas']['width']\n"
                        "    ch = anim['canvas']['height']\n"
                        "    frames = []\n"
                        "    for f in anim['frames']:\n"
                        "        canvas = Image.new('RGBA', (cw, ch), (0, 0, 0, 0))\n"
                        "        fo = f['offset']\n"
                        "        for part in f['parts']:   # 普通帧 1 个部件，合成帧多个\n"
                        "            r, pos = part['rect'], part['pos']\n"
                        "            tile = atlas.crop((r['x'], r['y'],\n"
                        "                               r['x'] + r['width'],\n"
                        "                               r['y'] + r['height']))\n"
                        "            canvas.paste(tile, (fo['x'] + pos['x'], fo['y'] + pos['y']))\n"
                        "        frames.append(canvas)\n"
                        "    # frames[i] 即第 i 帧完整画面；用 anim['anchor'] 定位角色原点\n"
                        "    # 播放间隔 = f['duration_ms'] 毫秒，循环行为见 anim['loop']"
                    ),
                },
            },
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _json_formats(self) -> list[str]:
        """返回勾选的 JSON 格式标识列表（按 FORMAT_CHOICES 顺序）。"""
        return [fmt for fmt, _ in FORMAT_CHOICES if self._json_fmt_checks.get(fmt, None) is not None
                and self._json_fmt_checks[fmt].isChecked()]

    def _write_animation_jsons(
        self,
        base_name: str,
        atlas_name: str,
        atlas: QPixmap,
        actions: list,
        records: dict,
        gif_names: dict | None = None,
    ) -> list[Path]:
        """为每个勾选的 JSON 格式写出一个文件，返回生成的文件列表。

        base_name 是不含扩展名的目标路径（如 {folder}/spritesheet 或 {folder}/{name}），
        各格式自动追加后缀：本工具格式 -> .json，其余 -> .{fmt}.json。
        """
        gif_names = gif_names or {}
        generated: list[Path] = []
        for fmt in self._json_formats():
            suffix = FORMAT_SUFFIX.get(fmt, "")
            path = Path(f"{base_name}{suffix}.json")
            if fmt == FMT_NATIVE:
                animations = [
                    self._animation_json(action, atlas_name, gif_names.get(action), records)
                    for action in actions
                ]
                self._write_json(path, atlas_name, atlas, animations)
            else:
                # 第三方格式：动作名去重后作为帧名前缀（与 PNG/GIF 文件名规则一致）
                prefixes: dict = {}
                used_names: set[str] = set()
                for action in actions:
                    prefixes[action] = _safe_export_name(action.name, used_names)
                payload = build_payload(
                    fmt,
                    actions,
                    records,
                    atlas_name,
                    atlas.width(),
                    atlas.height(),
                    f"{__app_name__} {__version__}",
                    prefixes,
                )
                write_payload(path, payload)
            generated.append(path)
        return generated

    # ---- 命名规则：**只有这一份**，_planned_names / 同名检测 / 真正导出都用它 ----

    def base_name(self, suffix: str = "") -> str:
        """导出产物的基名 = 工程名（当前标签页的名字）+ 可选序号后缀。"""
        return _safe_export_name(self._project_name) + suffix

    def _file_count(self) -> int:
        """本次导出会写出多少个**文件**（不含文件夹）。"""
        selected = self.selected_actions()
        json_count = len(self._json_formats())
        gif_count = 1 if self.cb_gif.isChecked() else 0
        project = 1 if self.cb_project.isChecked() else 0
        if not selected:
            return project
        if self.cb_separate.isChecked():
            return len(selected) * (1 + gif_count + json_count) + project
        return 1 + len(selected) * gif_count + json_count + project

    def target_root(self, directory: Path, suffix: str = "") -> Path:
        """这一堆文件到底放在哪。

        规则（用户要求）：**超过两个文件就放进一个以工程名命名的文件夹**，
        免得多次导出的产物全摊在同一个目录里互相覆盖、混在一起。
        单独导出一个工程文件、或者只导出一张图时，就直接放在所选目录下。
        """
        if self._file_count() > 2:
            return directory / self.base_name(suffix)
        return directory

    def _planned_names(self, root: Path, suffix: str = "") -> list[Path]:
        """列出本次导出会写入的目标路径（用于同名检测）。

        root 是**已经解析过的落点目录**（见 ``target_root``）。
        必须与 `_export_to_directory` 里的命名保持一致 —— 现在两边都走
        ``base_name`` / ``target_root``，不要再各自拼字符串。
        """
        selected = self.selected_actions()
        json_formats = self._json_formats()
        base = self.base_name(suffix)
        out: list[Path] = []
        if self.cb_separate.isChecked():
            used: set[str] = set()
            for action in selected:
                # 每个动作一个文件夹，名字也以工程名开头
                out.append(root / f"{base}_{_safe_export_name(action.name, used)}")
        else:
            if selected:
                out.append(root / f"{base}.png")
            if self.cb_gif.isChecked():
                used_gif: set[str] = set()
                for action in selected:
                    out.append(root / f"{base}_{_safe_export_name(action.name, used_gif)}.gif")
            for fmt in json_formats:
                out.append(root / f"{base}{FORMAT_SUFFIX.get(fmt, '')}.json")
        if self.cb_project.isChecked():
            out.append(root / f"{base}.sstool")
        return out

    def _ask_name_policy(self, existing: list[Path]) -> str:
        """弹窗询问同名处理，返回 "overwrite" / "rename" / "cancel"。

        单独抽成一个方法是为了可测：测试里替换它（或替换 resolve_name_conflicts 的
        判断）就不必去 mock Qt 的模态对话框。
        """
        shown = "\n".join(f"· {p.name}" for p in existing[:8])
        more = f"\n… 以及另外 {len(existing) - 8} 项" if len(existing) > 8 else ""
        box = QMessageBox(self)
        box.setWindowTitle("目标已存在")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(f"导出目录里已有 {len(existing)} 个同名目标：\n\n{shown}{more}")
        box.setInformativeText("要覆盖它们，还是自动加序号（如 _2）另存？")
        btn_over = box.addButton("覆盖", QMessageBox.ButtonRole.DestructiveRole)
        btn_ren = box.addButton("加序号另存", QMessageBox.ButtonRole.AcceptRole)
        btn_cancel = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(btn_ren)
        box.exec()
        clicked = box.clickedButton()
        if clicked is btn_cancel or clicked is None:
            return "cancel"
        return "overwrite" if clicked is btn_over else "rename"

    def resolve_name_conflicts(self, directory: str) -> str | None:
        """同名文件处理：返回要使用的序号后缀（""=直接覆盖），None=用户取消。

        分别导出时判断的是「动作文件夹是否已存在」，其余情况判断具体文件。
        落点用 ``target_root`` 解析（超过两个文件会先加一层以工程名命名的文件夹；
        那一层文件夹里同名才会问）。
        """
        picked = Path(directory)
        root = self.target_root(picked)
        existing = [p for p in self._planned_names(root) if p.exists()]
        if not existing:
            return ""
        policy = self._ask_name_policy(existing)
        if policy == "cancel":
            return None
        if policy == "overwrite":
            return ""
        for n in range(2, 100):
            cand = f"_{n}"
            if not any(p.exists() for p in self._planned_names(self.target_root(picked, cand), cand)):
                return cand
        return f"_{len(existing) + 1}"

    def _export_to_directory(self, directory: str, progress=None,
                             suffix: str = "") -> list[Path]:
        """执行导出；progress 为可选回调 progress(value:int, total:int, label:str) -> bool，
        返回 False 表示用户取消。suffix 为同名处理选定的序号后缀（""=直接覆盖）。"""
        selected = self.selected_actions()
        if not selected and not (self.cb_project.isChecked() and self._project_provider):
            raise ValueError("请至少选择一个导出动作")
        telemetry.track("导出图集PNG")
        if self.export_gif():
            telemetry.track("导出GIF")
        if self.export_json():
            telemetry.track("导出JSON")
        if self.cb_project.isChecked():
            telemetry.track("导出工程文件")
        picked = Path(directory)
        # 落点：超过两个文件就先建一层以工程名命名的文件夹（规则见 target_root）
        root = self.target_root(picked, suffix)
        root.mkdir(parents=True, exist_ok=True)
        base = self.base_name(suffix)
        generated: list[Path] = []
        used_names: set[str] = set()
        json_formats = self._json_formats()

        def report(done: int, total: int, label: str) -> bool:
            if progress is not None and not progress(done, total, label):
                raise ValueError("导出已取消")
            return True

        def export_project(done: int, total: int) -> int:
            if not self.cb_project.isChecked():
                return done
            if self._project_provider is None:
                raise ValueError("无法导出 SSTOOL：当前窗口未提供工程数据")
            manifest, sections = self._project_provider()
            project_path = root / f"{base}.sstool"
            write_project(project_path, manifest, sections)
            generated.append(project_path)
            done += 1
            report(done, total, f"[{done}/{total}] SSTOOL 工程")
            return done

        if self.cb_separate.isChecked():
            per_action = 1 + (1 if self.cb_gif.isChecked() else 0) + len(json_formats)
            total = len(selected) * per_action + (1 if self.cb_project.isChecked() else 0)
            done = 0
            for action in selected:
                name = _safe_export_name(action.name, used_names)
                # 每个动作一个文件夹，名字以工程名开头（与 _planned_names 一致）
                stem = f"{base}_{name}"
                folder = root / stem
                folder.mkdir(parents=True, exist_ok=True)
                frames = self._final_frames(action)
                # 图集按元素图块打包（普通帧去重复用；合成帧直接打包整块，不丢部件）
                atlas, full_records = self._build_packed_atlas([action])
                if atlas is None:
                    continue
                atlas_name = f"{stem}.png"
                atlas_path = folder / atlas_name
                if not atlas.save(str(atlas_path), "PNG"):
                    raise IOError(f"无法写入 {atlas_path}")
                generated.append(atlas_path)
                done += 1
                report(done, total, f"[{done}/{total}] {name} · 图集 PNG")
                gif_name = f"{stem}.gif" if self.cb_gif.isChecked() else None
                if gif_name:
                    gif_path = folder / gif_name
                    self._write_gif(gif_path, action, frames)
                    generated.append(gif_path)
                    done += 1
                    report(done, total, f"[{done}/{total}] {name} · GIF")
                if json_formats:
                    for json_path in self._write_animation_jsons(folder / stem, atlas_name, atlas, [action], full_records, {action: gif_name}):
                        generated.append(json_path)
                        done += 1
                        report(done, total, f"[{done}/{total}] {name} · {json_path.name}")
            export_project(done, total)
            return generated

        # The project checkbox can be used on its own to export the complete
        # document even when no action cards are selected.
        if not selected:
            export_project(0, 1)
            return generated

        if self.rb_original.isChecked():
            atlas, records = self._build_original_atlas(selected)
        else:
            # 重构打包：普通帧去重复用元素图块；合成帧直接打包整块，
            # 因此所有 JSON 格式（含 TP/Aseprite）的合成帧都是一帧一块，画面一致。
            atlas, records = self._build_packed_atlas(selected)
        if atlas is None:
            raise ValueError("所选动作没有可导出的帧")

        gif_names: dict[Action, str] = {}
        gif_count = len(selected) if self.cb_gif.isChecked() else 0
        total = 2 + gif_count + len(json_formats) + (1 if self.cb_project.isChecked() else 0)
        done = 0
        report(done, total, "正在打包图集 …")
        atlas_name_out = f"{base}.png"
        atlas_path = root / atlas_name_out
        if not atlas.save(str(atlas_path), "PNG"):
            raise IOError(f"无法写入 {atlas_path}")
        generated.append(atlas_path)
        done += 1
        report(done, total, f"已写入 {atlas_name_out}")
        if self.cb_gif.isChecked():
            gif_used: set[str] = set()
            for action in selected:
                # GIF 文件名也以工程名开头，和其余产物保持一致
                gif_name = f"{base}_{_safe_export_name(action.name, gif_used)}.gif"
                gif_names[action] = gif_name
                gif_path = root / gif_name
                self._write_gif(gif_path, action, self._final_frames(action))
                generated.append(gif_path)
                done += 1
                report(done, total, f"[{done}/{total}] GIF · {gif_name}")
        if json_formats:
            for json_path in self._write_animation_jsons(root / base, atlas_name_out, atlas, selected, records, gif_names):
                generated.append(json_path)
                done += 1
                report(done, total, f"[{done}/{total}] JSON · {json_path.name}")
        export_project(done, total)
        return generated

    def _perform_export(self) -> None:
        if self.cb_gif.isChecked() and importlib.util.find_spec("PIL.Image") is None:
            QMessageBox.critical(self, "无法导出", "GIF 导出需要 Pillow，请先安装 requirements.txt 中的依赖。")
            return
        directory = QFileDialog.getExistingDirectory(self, "选择导出文件夹", last_directory())
        if not directory:
            return
        set_last_directory(directory)
        # 同名处理：先问覆盖还是加序号另存（取消则整体中止）
        suffix = self.resolve_name_conflicts(directory)
        if suffix is None:
            return
        dlg = QProgressDialog("正在导出 …", "取消", 0, 100, self)
        dlg.setWindowTitle("导出进度")
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(True)
        dlg.setAutoReset(True)
        dlg.show()

        def on_progress(value: int, total: int, label: str) -> bool:
            dlg.setLabelText(label)
            dlg.setMaximum(max(total, 1))
            dlg.setValue(value)
            QApplication.processEvents()
            return not dlg.wasCanceled()

        try:
            generated = self._export_to_directory(directory, progress=on_progress,
                                                  suffix=suffix)
        except (ValueError, IOError, OSError) as exc:
            dlg.close()
            if str(exc) != "导出已取消":
                QMessageBox.warning(self, "导出失败", str(exc))
            return
        dlg.close()
        QMessageBox.information(self, "导出完成", f"已生成 {len(generated)} 个文件。\n{directory}")
        self.accept()

    def _stop_players(self) -> None:
        for player in self._players:
            player.stop()

    def done(self, result: int) -> None:
        """所有关闭路径的底层入口，确保动画定时器一定停止。"""
        self._stop_players()
        super().done(result)

    def closeEvent(self, event) -> None:
        self._stop_players()
        super().closeEvent(event)

    def reject(self) -> None:
        self._stop_players()
        super().reject()

    def accept(self) -> None:
        self._stop_players()
        super().accept()

    def selected_actions(self) -> list:
        return [act for act, sw in self._switches.items() if sw.isChecked()]

    def export_mode(self) -> str:
        return "original" if self.rb_original.isChecked() else "repack"

    def export_json(self) -> bool:
        return len(self._json_formats()) > 0

    def export_gif(self) -> bool:
        return self.cb_gif.isChecked()

    def export_separate(self) -> bool:
        return self.cb_separate.isChecked()
