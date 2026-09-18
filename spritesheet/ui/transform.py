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
    """Return the non-transparent pixel bounds, or None for an empty image.

    性能：原实现对每个像素做一次 Python 循环（1024x1024 约 244ms，而变换
    每次都要调用它）。这里把 alpha 通道一次性切出来，逐行用 C 层的
    ``max`` / ``lstrip`` / ``rstrip`` 求「本行是否有内容、首个/末个非零列」，
    Python 层迭代次数从 O(w*h) 降到 O(h)，像素扫描全部留在 C 层
    （1024x1024 约 5ms，约 50 倍）。

    语义与逐像素实现完全一致：alpha > 0 即视为不透明；整行透明直接跳过。
    """
    if not pm or pm.isNull():
        return None
    image = pm.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    width, height = image.width(), image.height()
    if width <= 0 or height <= 0:
        return None
    bits = image.constBits()
    bits.setsize(image.sizeInBytes())
    data = bytes(bits)
    stride = image.bytesPerLine()
    if stride == width * 4:
        # 快路径：RGBA8888 每像素 4 字节，行已 4 字节对齐 -> 直接步长切片取 alpha
        alpha = data[3::4]
    else:
        # 兜底：逐行取 alpha，丢弃行尾 padding
        alpha = b"".join(
            data[y * stride + 3:y * stride + width * 4:4] for y in range(height)
        )
    left, top = width, height
    right = bottom = -1
    for y in range(height):
        off = y * width
        row = alpha[off:off + width]
        if max(row) == 0:          # C 层一次判定：整行透明，跳过
            continue
        if y < top:
            top = y
        bottom = y                 # y 递增，最后一个非空行即 bottom
        first = width - len(row.lstrip(b"\x00"))
        last = len(row.rstrip(b"\x00")) - 1
        if first < left:
            left = first
        if last > right:
            right = last
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
                           quad: tuple | None = None, rotation_pivot=None,
                           fast: bool = False) -> QPixmap:
    """把一帧按 镜像H → 镜像V → 旋转 → 自由四边形(quad) 顺序变换，返回新图（无变换时原样返回）。

    镜像与 90° 整数倍旋转使用 FastTransformation（精确像素、不插值），
    任意角度与非仿射透视才用 SmoothTransformation（平滑抗锯齿）。
    quad 为归一化 (0-1) 四角坐标，相对"镜像+旋转后的帧"，
    缺省或等于 DEFAULT_QUAD 时不透视。
    预览、缩略图、GIF、图集/JSON 导出全部使用同一函数，保证所见即所得。

    fast=True 用于交互拖动：跳过两次全像素透明裁剪（_alpha_bounds 对大图约
    2.6ms/次，多帧整体拖动时每帧扫两遍会占满主线程导致卡顿），直接返回变换
    包围盒像素；中心对称绘制下视觉与精算一致，松手后调用方再以 fast=False
    精算并缓存（含裁剪），保证最终尺寸/导出精确。
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
    # 交互拖动(fast)跳过这次全像素扫描，直接用变换包围盒。
    if transformed and not fast:
        pm, _ = _trim_transparent(pm)
    if quad is not None and tuple(quad) != DEFAULT_QUAD:
        w, h = pm.width(), pm.height()
        src = QPolygonF([QPointF(0, 0), QPointF(w, 0), QPointF(w, h), QPointF(0, h)])
        dst = QPolygonF([QPointF(quad[0] * w, quad[1] * h), QPointF(quad[2] * w, quad[3] * h),
                         QPointF(quad[4] * w, quad[5] * h), QPointF(quad[6] * w, quad[7] * h)])
        m = QTransform()
        if QTransform.quadToQuad(src, dst, m) and m.isInvertible():
            pm = pm.transformed(m, Qt.TransformationMode.SmoothTransformation)
        if not fast:
            pm, _ = _trim_transparent(pm)
    return pm


def action_frame_pixmap(action: Action, index: int, fast: bool = False) -> QPixmap:
    """返回第 index 帧应用变换并收缩透明边界后的显示像素（带缓存）。

    fast=True（交互拖动）跳过透明裁剪、且结果不写入缓存，避免包围盒临时
    结果污染精算缓存；fast=False 走完整管线并按变换键缓存。
    """
    if not (0 <= index < len(action.frames)):
        return QPixmap()
    quad = action.frame_quads.get(index)
    pivot = action.frame_rotation_pivots.get(index, DEFAULT_ROTATION_PIVOT)
    key = (index, action.frame_rotations.get(index, 0),
           index in action.frame_mirror_h, index in action.frame_mirror_v,
           tuple(pivot), quad)
    if not fast:
        cached = action._transform_cache.get(key)
        if cached is not None:
            return cached
    pm = transform_frame_pixmap(action.frames[index], key[1], key[2], key[3], quad, pivot,
                                fast=fast)
    if not fast:
        action._transform_cache[key] = pm
    return pm


def action_frame_size(action: Action, index: int) -> tuple[int, int]:
    """第 index 帧应用变换后的尺寸（宽, 高）。"""
    pm = action_frame_pixmap(action, index)
    return pm.width(), pm.height()


def frame_base_size(action: Action, index: int) -> tuple[int, int]:
    """镜像+旋转后、尚未加自由四边形(quad) 的帧尺寸。

    quad 的归一化坐标以该尺寸为基准（0-1），交互手柄也以它为几何基准。

    性能：交互路径（命中测试 / 手柄绘制 / 拖动）每次鼠标移动都会调用本函数，
    原实现每次都重跑一遍完整变换 + alpha 裁剪（512x512 旋转帧约 84ms，
    一次拖动 20 次移动就要 1.7s，直接表现为卡顿）。这里按
    (帧号, 旋转, 镜像, 旋转中心) 缓存结果，与 ``action_frame_pixmap`` 的缓存
    同源失效（``Action.invalidate_transforms``）。
    """
    if not (0 <= index < len(action.frames)):
        return 0, 0
    key = (index, action.frame_rotations.get(index, 0),
           index in action.frame_mirror_h, index in action.frame_mirror_v,
           tuple(action.frame_rotation_pivots.get(index, DEFAULT_ROTATION_PIVOT)))
    cached = action._base_size_cache.get(key)
    if cached is not None:
        return cached
    pm = transform_frame_pixmap(
        action.frames[index],
        action.frame_rotations.get(index, 0),
        index in action.frame_mirror_h,
        index in action.frame_mirror_v,
        None,
        action.frame_rotation_pivots.get(index, DEFAULT_ROTATION_PIVOT),
    )
    size = (pm.width(), pm.height())
    action._base_size_cache[key] = size
    return size


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


def whole_reference(action: Action, indices):
    """整体变换参考框：所有目标帧以最大 base 尺寸居中叠放的统一矩形。

    返回 (W, H, entries)；entries = [(i, ox, oy, w_i, h_i), ...]，
    其中 (w_i,h_i)=frame_base_size（镜像旋转后、未加 quad），(ox,oy) 是该帧
    内容在 W×H 参考框里的居中偏移（与 compute_preview_layout 的居中口径一致）。
    所有帧中心重合于参考框中心 —— 这是"对整个 GIF 做变换"时各帧保持重叠的基准。
    """
    idxs = [i for i in dict.fromkeys(indices) if 0 <= i < len(action.frames)]
    if not idxs:
        return 0, 0, []
    sizes = [frame_base_size(action, i) for i in idxs]
    W = max(s[0] for s in sizes)
    H = max(s[1] for s in sizes)
    entries = []
    for i, (w, h) in zip(idxs, sizes):
        ox = (W - w) // 2
        oy = (H - h) // 2
        entries.append((i, ox, oy, w, h))
    return W, H, entries


def distribute_whole_quad(action: Action, indices, ref_quad):
    """把"参考框 W×H 的归一化四角 ref_quad"换算成每帧自己的归一化 quad。

    帧 i 内容在参考框中居中于 (ox,oy)；整体四边形映射 M（参考框四角→ref_quad
    四角，含缩放/斜切/透视）作用到帧 i 内容四角，再平移回帧 i 局部坐标并按
    帧 base 尺寸归一化。围绕参考框中心的变换下，各帧结果围绕各自内容中心、
    在画布上保持重叠（中心对称缩放时 quad 与帧尺寸无关）。

    返回 {帧号: quad 或 None（None=恢复默认）}。
    """
    ref_quad = tuple(ref_quad)
    W, H, entries = whole_reference(action, indices)
    if not entries:
        return {}
    ref_src = QPolygonF([QPointF(0, 0), QPointF(W, 0), QPointF(W, H), QPointF(0, H)])
    ref_dst = QPolygonF([
        QPointF(ref_quad[0] * W, ref_quad[1] * H),
        QPointF(ref_quad[2] * W, ref_quad[3] * H),
        QPointF(ref_quad[4] * W, ref_quad[5] * H),
        QPointF(ref_quad[6] * W, ref_quad[7] * H),
    ])
    matrix = QTransform()
    ok = QTransform.quadToQuad(ref_src, ref_dst, matrix)
    result: dict = {}
    for i, ox, oy, w, h in entries:
        if not ok or ref_quad == DEFAULT_QUAD or w <= 0 or h <= 0:
            result[i] = None
            continue
        local = [QPointF(ox, oy), QPointF(ox + w, oy),
                 QPointF(ox + w, oy + h), QPointF(ox, oy + h)]
        mapped = [matrix.map(pt) for pt in local]
        qx0, qy0 = mapped[0].x() - ox, mapped[0].y() - oy
        qx1, qy1 = mapped[1].x() - ox, mapped[1].y() - oy
        qx2, qy2 = mapped[2].x() - ox, mapped[2].y() - oy
        qx3, qy3 = mapped[3].x() - ox, mapped[3].y() - oy
        quad = (qx0 / w, qy0 / h, qx1 / w, qy1 / h,
                qx2 / w, qy2 / h, qx3 / w, qy3 / h)
        # 与默认矩形几乎一致则视为无变换，避免写入浮点噪声
        if max(abs(quad[k] - DEFAULT_QUAD[k]) for k in range(8)) < 1e-4:
            result[i] = None
        else:
            result[i] = tuple(round(v, 6) for v in quad)
    return result
