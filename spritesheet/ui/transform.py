#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""帧变换与预览布局纯函数（从 main_window.py 拆出，纯搬移，逻辑未改动）。

预览、缩略图、GIF、图集/JSON 导出全部复用这里的函数，保证所见即所得。
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRect, Qt
from PyQt6.QtGui import QImage, QPixmap, QPolygonF, QTransform

from .models import Action


# 默认自由变换四边形（无变换）：归一化角序 (0,0)(1,0)(1,1)(0,1)
DEFAULT_QUAD = (0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0)
DEFAULT_ROTATION_PIVOT = (0.5, 0.5)


def _normalized_rotation_pivot(pivot) -> tuple[float, float]:
    """Return a finite, bounded normalized rotation pivot."""
    if not pivot or len(pivot) < 2:
        return DEFAULT_ROTATION_PIVOT
    try:
        x, y = float(pivot[0]), float(pivot[1])
    except (TypeError, ValueError):
        return DEFAULT_ROTATION_PIVOT
    if not (math.isfinite(x) and math.isfinite(y)):
        return DEFAULT_ROTATION_PIVOT
    return (max(-2.0, min(3.0, x)), max(-2.0, min(3.0, y)))


def _rotation_transform(width: int, height: int, angle: int,
                        pivot=None) -> tuple[QTransform, QPointF, QPointF]:
    """Build the rotation matrix and return (matrix, pivot_px, bounds_min)."""
    pxn, pyn = _normalized_rotation_pivot(pivot)
    pivot_px = QPointF(pxn * width, pyn * height)
    matrix = QTransform()
    matrix.translate(pivot_px.x(), pivot_px.y())
    matrix.rotate(int(angle) % 360)
    matrix.translate(-pivot_px.x(), -pivot_px.y())
    corners = [matrix.map(QPointF(0, 0)), matrix.map(QPointF(width, 0)),
               matrix.map(QPointF(width, height)), matrix.map(QPointF(0, height))]
    min_pt = QPointF(min(p.x() for p in corners), min(p.y() for p in corners))
    return matrix, pivot_px, min_pt


def _alpha_bounds(pm: QPixmap) -> QRect | None:
    """Return the non-transparent pixel bounds, or None for an empty image."""
    if not pm or pm.isNull():
        return None
    image = pm.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    bits = image.constBits()
    bits.setsize(image.sizeInBytes())
    data = bytes(bits)
    stride = image.bytesPerLine()
    left, top = image.width(), image.height()
    right = bottom = -1
    for y in range(image.height()):
        row = y * stride
        for x in range(image.width()):
            if data[row + x * 4 + 3] > 0:
                left = min(left, x)
                top = min(top, y)
                right = max(right, x)
                bottom = max(bottom, y)
    if right < left or bottom < top:
        return None
    return QRect(left, top, right - left + 1, bottom - top + 1)


def _trim_transparent(pm: QPixmap) -> tuple[QPixmap, QRect]:
    """Trim transparent margins while retaining a 1x1 empty frame."""
    bounds = _alpha_bounds(pm)
    if bounds is None:
        empty = QPixmap(1, 1)
        empty.fill(Qt.GlobalColor.transparent)
        return empty, QRect(0, 0, 1, 1)
    if bounds == pm.rect():
        return pm, bounds
    return pm.copy(bounds), bounds


def transform_frame_pixmap(pm: QPixmap, rotation: int, mirror_h: bool, mirror_v: bool,
                           quad: tuple | None = None, rotation_pivot=None) -> QPixmap:
    """把一帧按 镜像H → 镜像V → 旋转 → 自由四边形(quad) 顺序变换，返回新图（无变换时原样返回）。

    镜像与 90° 整数倍旋转使用 FastTransformation（精确像素、不插值），
    任意角度与非仿射透视才用 SmoothTransformation（平滑抗锯齿）。
    quad 为归一化 (0-1) 四角坐标，相对"镜像+旋转后的帧"，
    缺省或等于 DEFAULT_QUAD 时不透视。
    预览、缩略图、GIF、图集/JSON 导出全部使用同一函数，保证所见即所得。
    """
    if not pm or pm.isNull():
        return pm
    transformed = bool(mirror_h or mirror_v or int(rotation) % 360
                       or (quad is not None and tuple(quad) != DEFAULT_QUAD))
    if mirror_h:
        pm = pm.transformed(QTransform().scale(-1.0, 1.0), Qt.TransformationMode.FastTransformation)
    if mirror_v:
        pm = pm.transformed(QTransform().scale(1.0, -1.0), Qt.TransformationMode.FastTransformation)
    angle = int(rotation) % 360
    if angle:
        mode = (Qt.TransformationMode.FastTransformation if angle % 90 == 0
                else Qt.TransformationMode.SmoothTransformation)
        matrix, _, _ = _rotation_transform(pm.width(), pm.height(), angle, rotation_pivot)
        pm = pm.transformed(matrix, mode)
    # Normalize the content bounds before a quad transform. This makes the
    # quad coordinates describe the visible frame, not transparent source
    # padding, and ensures transformed frames do not carry old blank margins.
    if transformed:
        pm, _ = _trim_transparent(pm)
    if quad is not None and tuple(quad) != DEFAULT_QUAD:
        w, h = pm.width(), pm.height()
        src = QPolygonF([QPointF(0, 0), QPointF(w, 0), QPointF(w, h), QPointF(0, h)])
        dst = QPolygonF([QPointF(quad[0] * w, quad[1] * h), QPointF(quad[2] * w, quad[3] * h),
                         QPointF(quad[4] * w, quad[5] * h), QPointF(quad[6] * w, quad[7] * h)])
        m = QTransform()
        if QTransform.quadToQuad(src, dst, m) and m.isInvertible():
            pm = pm.transformed(m, Qt.TransformationMode.SmoothTransformation)
        pm, _ = _trim_transparent(pm)
    return pm


def action_frame_pixmap(action: Action, index: int) -> QPixmap:
    """返回第 index 帧应用变换并收缩透明边界后的显示像素（带缓存）。"""
    if not (0 <= index < len(action.frames)):
        return QPixmap()
    quad = action.frame_quads.get(index)
    pivot = action.frame_rotation_pivots.get(index, DEFAULT_ROTATION_PIVOT)
    key = (index, action.frame_rotations.get(index, 0),
           index in action.frame_mirror_h, index in action.frame_mirror_v,
           tuple(pivot), quad)
    cached = action._transform_cache.get(key)
    if cached is not None:
        return cached
    pm = transform_frame_pixmap(action.frames[index], key[1], key[2], key[3], quad, pivot)
    action._transform_cache[key] = pm
    return pm


def action_frame_size(action: Action, index: int) -> tuple[int, int]:
    """第 index 帧应用变换后的尺寸（宽, 高）。"""
    pm = action_frame_pixmap(action, index)
    return pm.width(), pm.height()


def frame_base_size(action: Action, index: int) -> tuple[int, int]:
    """镜像+旋转后、尚未加自由四边形(quad) 的帧尺寸。

    quad 的归一化坐标以该尺寸为基准（0-1），交互手柄也以它为几何基准。
    """
    if not (0 <= index < len(action.frames)):
        return 0, 0
    pm = transform_frame_pixmap(
        action.frames[index],
        action.frame_rotations.get(index, 0),
        index in action.frame_mirror_h,
        index in action.frame_mirror_v,
        None,
        action.frame_rotation_pivots.get(index, DEFAULT_ROTATION_PIVOT),
    )
    return pm.width(), pm.height()


def compute_preview_layout(action: Action, frame_index: int = 0):
    """计算预览画布布局。

    所有帧初始居中叠放（集合在一起，不保持原图中的分离位置）；
    画布基准取所有帧变换后的最大宽高 + padding（边缘像素权重第一）；
    逐帧偏移相对于居中位置，偏移推出基准边界时画布向对应方向扩大。
    返回 (canvas_w, canvas_h, positions, (pad, max_w, max_h))
    """
    if not action.frames:
        return 0, 0, [], (0, 0, 0)

    pad = action.padding
    sizes = [action_frame_size(action, i) for i in range(len(action.frames))]
    # 边缘像素权重第一：所有帧变换后的最大宽高作为画布基准
    max_w = max(s[0] for s in sizes)
    max_h = max(s[1] for s in sizes)

    positions = []
    # Positions already include the requested leading padding. Initialize the
    # extents there so the final canvas adds exactly one padding on each side,
    # rather than counting the leading padding twice.
    min_x, min_y = pad, pad
    max_right, max_bottom = pad + max_w, pad + max_h

    for i in range(len(action.frames)):
        pm_w, pm_h = sizes[i]
        # 所有帧初始居中叠放（集合在一起），再叠加逐帧偏移
        tx = pad + (max_w - pm_w) // 2
        ty = pad + (max_h - pm_h) // 2
        dx, dy = action.frame_offsets.get(i, (0, 0))
        tx += dx
        ty += dy
        positions.append((tx, ty, pm_w, pm_h))
        # 偏移推出基准边界时向对应方向扩大
        if tx < min_x:
            min_x = tx
        if ty < min_y:
            min_y = ty
        if tx + pm_w > max_right:
            max_right = tx + pm_w
        if ty + pm_h > max_bottom:
            max_bottom = ty + pm_h

    canvas_w = max_right - min_x + pad * 2
    canvas_h = max_bottom - min_y + pad * 2
    # 调整位置使左上角为pad
    if min_x < 0 or min_y < 0:
        adj_x = pad - min_x
        adj_y = pad - min_y
        positions = [(tx + adj_x, ty + adj_y, w, h) for tx, ty, w, h in positions]

    return canvas_w, canvas_h, positions, (pad, max_w, max_h)


def action_anchor_pixels(action: Action, canvas_w: int, canvas_h: int) -> tuple[int, int]:
    """返回动作自身逻辑画布内的锚点像素坐标。"""
    nx = max(0.0, min(1.0, float(action.anchor[0])))
    ny = max(0.0, min(1.0, float(action.anchor[1])))
    return round(canvas_w * nx), round(canvas_h * ny)
