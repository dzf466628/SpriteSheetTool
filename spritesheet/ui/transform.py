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

# 自由变换的尺寸上界。
# 交互拖动时鼠标一旦划出窗口很远，quad 会被放大到几万像素：120x90 的帧拖到
# 10000px 外就是 10120x10090（约 102MP、400MB），多帧整体变换时每帧都是这个
# 量级 → 每动一下鼠标都要重新渲染几十上百 MB 的 pixmap，界面直接卡死。
# 这两个上限同时约束"交互（拖动时夹住倍数）"和"渲染（兜底历史工程里的超大 quad）"。
MAX_TRANSFORM_RATIO = 8.0          # 相对原始帧尺寸的最大放大倍数
MAX_TRANSFORM_PIXELS = 32_000_000  # 单帧变换后像素数上限（约 5657x5657）


def transform_ratio_limit(width: int, height: int) -> float:
    """当前帧尺寸下允许的最大放大倍数（倍数上限与像素预算取小）。"""
    area = float(max(1, int(width))) * float(max(1, int(height)))
    return max(1.0, min(MAX_TRANSFORM_RATIO, math.sqrt(MAX_TRANSFORM_PIXELS / area)))


def clamp_quad_span(quad, width: int, height: int):
    """把超出尺寸预算的 quad 绕自身中心等比缩回预算内（形状与中心不变）。

    正常交互路径已经在上游把倍数夹住了，这里只做兜底：防的是历史工程/手工 JSON
    里存了超大 quad，一打开就卡死。
    """
    if quad is None:
        return quad
    w = float(max(1, int(width)))
    h = float(max(1, int(height)))
    xs = [float(quad[0]) * w, float(quad[2]) * w, float(quad[4]) * w, float(quad[6]) * w]
    ys = [float(quad[1]) * h, float(quad[3]) * h, float(quad[5]) * h, float(quad[7]) * h]
    span = max(max(xs) - min(xs), max(ys) - min(ys))
    # 与交互侧的夹法对齐（留一点余量，别把刚夹到上限的结果又削一刀）
    limit = max(w, h) * transform_ratio_limit(width, height) * 1.001
    if span <= limit or span <= 0.0:
        return quad
    k = limit / span
    cx = (max(xs) + min(xs)) / 2.0
    cy = (max(ys) + min(ys)) / 2.0
    out = []
    for i in range(4):
        out.append((cx + (xs[i] - cx) * k) / w)
        out.append((cy + (ys[i] - cy) * k) / h)
    return tuple(out)


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
        # 兜底：历史工程里可能存着超大 quad（会把 pixmap 撑到几百 MB）
        quad = clamp_quad_span(tuple(quad), pm.width(), pm.height())
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


def frame_pivot_output(action: Action, index: int, pivot=None) -> tuple[float, float]:
    """第 index 帧：旋转中心在该帧"镜像+旋转并裁剪后"基图里的坐标。

    ``pivot`` 不传就用该帧当前记录的值；传了就用传进来的 —— 整体模式需要
    "落位用快照 pivot、红点用当前 pivot"两个口径分开算。

    必须与 ``transform_frame_pixmap`` 逐像素同管线，否则红色锚点画的位置和实际
    旋转围绕的点不一致，旋转/缩放就会"不按锚点来"。

    两个历史坑：
    ① 无旋转无镜像时 ``transform_frame_pixmap`` 的 ``transformed=False``，
       **不裁剪**透明边；这里若额外裁剪，锚点会偏出一圈透明边（约 2px）。
    ② 任意角度旋转时 ``QPixmap.transformed`` 输出图的平移量是
       ``-floor(映射外接矩形左上)``（整数），不是理论浮点 min_pt。

    结果按 (帧号, 角度, 镜像, 旋转中心) 缓存在 action 上，``invalidate_transforms``
    一起失效 —— 整体变换绘制要对每一帧求它，不能每次重绘都重算。
    """
    if not (0 <= index < len(action.frames)):
        return (0.0, 0.0)
    src = action.frames[index]
    src_w, src_h = src.width(), src.height()
    if pivot is None:
        pivot = action.frame_rotation_pivots.get(index, DEFAULT_ROTATION_PIVOT)
    else:
        pivot = tuple(pivot)
    angle = int(action.frame_rotations.get(index, 0)) % 360
    mh = index in action.frame_mirror_h
    mv = index in action.frame_mirror_v
    transformed = bool(mh or mv or angle)
    cache = getattr(action, "_pivot_out_cache", None)
    ckey = (index, angle, mh, mv, tuple(pivot))
    if cache is not None:
        hit = cache.get(ckey)
        if hit is not None:
            return hit

    pm = src
    if mh:
        pm = pm.transformed(QTransform().scale(-1.0, 1.0), Qt.TransformationMode.FastTransformation)
    if mv:
        pm = pm.transformed(QTransform().scale(1.0, -1.0), Qt.TransformationMode.FastTransformation)
    # 镜像不改 raster 尺寸；旋转 matrix 与 transform_frame_pixmap 一样用原始
    # 归一化 pivot 在镜像后 raster 上重新定位，所以追踪点初始 = pivot*(w,h)。
    pin = QPointF(pivot[0] * src_w, pivot[1] * src_h)
    if angle:
        mode = (Qt.TransformationMode.FastTransformation if angle % 90 == 0
                else Qt.TransformationMode.SmoothTransformation)
        matrix, _pivot_px, _min_pt = _rotation_transform(src_w, src_h, angle, pivot)
        corners = [matrix.map(QPointF(0, 0)), matrix.map(QPointF(src_w, 0)),
                   matrix.map(QPointF(src_w, src_h)), matrix.map(QPointF(0, src_h))]
        br = QPolygonF(corners).boundingRect()
        # Qt QImage/QPixmap.transformed 的实际整数平移
        ox, oy = math.floor(br.left()), math.floor(br.top())
        pm = pm.transformed(matrix, mode)
        pin = matrix.map(pin) - QPointF(ox, oy)
    if transformed:
        bounds = _alpha_bounds(pm)
        if bounds is not None:
            pin = QPointF(pin.x() - bounds.x(), pin.y() - bounds.y())
    out = (pin.x(), pin.y())
    if cache is not None:
        cache[ckey] = out
    return out


def frame_pivot_in_pixmap(action: Action, index: int, pivot=None) -> tuple[float, float]:
    """第 index 帧：旋转中心在该帧**最终绘制用的 pixmap** 里的像素坐标。

    ``pivot`` 同 ``frame_pivot_output``：不传用当前值，传了用传进来的。

    = ``frame_pivot_output`` 再走一遍该帧的 quad（与 ``action_frame_pixmap`` 的
    变换管线一致），并以 quad 包围盒最小角为原点 —— 绘制时 pixmap 就是贴在这个
    原点上的。
    """
    px, py = frame_pivot_output(action, index, pivot)
    quad = action.frame_quads.get(index, DEFAULT_QUAD)
    if quad is None or tuple(quad) == DEFAULT_QUAD:
        return px, py
    w, h = frame_base_size(action, index)
    if w <= 0 or h <= 0:
        return px, py
    src_poly = QPolygonF([QPointF(0, 0), QPointF(w, 0), QPointF(w, h), QPointF(0, h)])
    dst = [QPointF(quad[0] * w, quad[1] * h), QPointF(quad[2] * w, quad[3] * h),
           QPointF(quad[4] * w, quad[5] * h), QPointF(quad[6] * w, quad[7] * h)]
    mo_x = min(p.x() for p in dst)
    mo_y = min(p.y() for p in dst)
    matrix = QTransform()
    if not QTransform.quadToQuad(src_poly, QPolygonF(dst), matrix):
        return px - mo_x, py - mo_y
    pt = matrix.map(QPointF(px, py))
    return pt.x() - mo_x, pt.y() - mo_y


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


def frame_has_content(action: Action, index: int) -> bool:
    """第 index 帧有没有画面（非透明像素）。

    **不能**拿"裁切后是不是 1x1"来判断：``action_frame_pixmap`` 只在帧带了变换时才会
    收缩透明边界（原始帧图原样返回），所以一帧全透明的图仍然是它原来那个尺寸。
    老老实实扫 alpha（``_alpha_bounds`` 是 C 层快路径，普通精灵帧几十微秒）。
    """
    if not (0 <= index < len(action.frames)):
        return False
    return _alpha_bounds(action_frame_pixmap(action, index)) is not None


def _cluster_1d(values, tol):
    """把一维数值按"大间隔就断开"聚成组，返回 (每组的均值, 每个值属于哪一组)。

    精灵图是均分的话，内容块中心在某一个方向上会聚成"等距的若干组"（组内差几像素、
    组间差一整格），所以断点很好找：比容差大的间隔就断开。
    """
    order = sorted(range(len(values)), key=lambda i: values[i])
    groups: list[list[int]] = []
    for idx in order:
        if groups and values[idx] - values[groups[-1][-1]] <= tol:
            groups[-1].append(idx)
        else:
            groups.append([idx])
    labels = [0] * len(values)
    centers = []
    for gi, g in enumerate(groups):
        for idx in g:
            labels[idx] = gi
        centers.append(sum(values[i] for i in g) / len(g))
    return centers, labels


def _center_pitch(values):
    """一维中心的"簇间距"（中位数）——均分的话相邻格的中心差就是一格。"""
    ordered = sorted(values)
    gaps = [b - a for a, b in zip(ordered, ordered[1:]) if b > 0]
    if not gaps:
        return 0.0
    gaps.sort()
    return gaps[len(gaps) // 2]


def detect_sheet_grid(rects, canvas_w: int, canvas_h: int):
    """判定这张精灵图是不是「横竖均分」，是的话返回 {帧序: (列, 行)}，不是就返回 None。

    用户口径：「拖进来默认那些灰线是**紧切**的线，不要动；点对齐以后别参考灰线，要看的是
    整个精灵图**横竖均分**的状态 —— 那才是正常那一帧的框，跟紧切框一比差值就出来了」。
    所以判均分是这条路的地基，判错就是给错数据，必须当场能验。

    判法（对"检测多检出几个碎屑"免疫，所以**不能**要求"每格元素个数相等"）：
      1. 内容块中心在某个方向上会聚成等距的若干组 → 组数 = 那个方向的格数，
         组间距的中位数 = 一格多长
      2. 另一个方向的格数由"画布尺寸 ÷ 格长"推（不靠该方向中心是否整齐 —— 横向会动时
         中心是散的）；同时把两个方向的聚簇结果也当候选
      3. 每个候选都**逐个内容块验算**：按均分切格子，看内容是否完整落在自己那一格里
         （格子归属用"中心落在第几格"算，不用排名 —— 碎屑、缺格都不影响）
         —— 第一个全中的候选才算判定成立；全都不中 → 返回 None，宁可不做也不给错偏移

    纯函数，只看框不看图，不碰任何数据。
    """
    count = len(rects or ())
    if count < 4 or canvas_w <= 0 or canvas_h <= 0:
        return None
    xs = [float(r[0]) + float(r[2]) / 2.0 for r in rects]
    ys = [float(r[1]) + float(r[3]) / 2.0 for r in rects]

    def clusters(values):
        ordered = sorted(values)
        gaps = [b - a for a, b in zip(ordered, ordered[1:]) if b > 0]
        tol = max(2.0, (max(gaps) * 0.25) if gaps else 2.0)
        return _cluster_1d(values, tol)[0]

    cx, cy = clusters(xs), clusters(ys)
    cols_guess, rows_guess = len(cx), len(cy)
    pitch_x, pitch_y = _center_pitch(cx), _center_pitch(cy)
    candidates: list[tuple[int, int]] = []

    def add(cols, rows):
        cols, rows = int(cols), int(rows)
        if cols >= 2 and rows >= 2 and (cols, rows) not in candidates:
            candidates.append((cols, rows))

    add(cols_guess, rows_guess)
    if pitch_y > 0:
        add(round(canvas_w / pitch_y), rows_guess)          # 方格假设
    if pitch_x > 0:
        add(cols_guess, round(canvas_h / pitch_x))
    for cols, rows in list(candidates):                     # 非方格时容错：附近再试两档
        add(cols, round(canvas_h / (canvas_w / cols)) if cols else 0)
        add(round(canvas_w / (canvas_h / rows)) if rows else 0, rows)

    for cols, rows in candidates:
        cell_w, cell_h = canvas_w / cols, canvas_h / rows
        index_of: dict[int, tuple[int, int]] = {}
        ok = True
        for i, r in enumerate(rects):
            col = min(max(int(xs[i] // cell_w), 0), cols - 1)
            row = min(max(int(ys[i] // cell_h), 0), rows - 1)
            index_of[i] = (col, row)
            left, top, cw, ch = cell_rect(index_of, i, canvas_w, canvas_h, cols, rows)
            if r[0] < left - 1 or r[0] + r[2] > left + cw + 1:
                ok = False
                break
            if r[1] < top - 1 or r[1] + r[3] > top + ch + 1:
                ok = False
                break
        if ok:
            return index_of                                 # 验算通过：均分判定成立
    return None


def grid_shape(index_of) -> tuple[int, int]:
    """从 {帧序: (列, 行)} 反推 (列数, 行数)。"""
    if not index_of:
        return 0, 0
    return (max(v[0] for v in index_of.values()) + 1,
            max(v[1] for v in index_of.values()) + 1)


def cell_rect(index_of, i: int, canvas_w: int, canvas_h: int,
              cols: int, rows: int) -> tuple[int, int, int, int]:
    """第 i 帧所在的「均分格」矩形 (x, y, w, h)；边界取整方式与导出时的 480/482 交替一致。"""
    col, row = index_of[i]
    left = int(round(col * canvas_w / cols))
    right = int(round((col + 1) * canvas_w / cols))
    top = int(round(row * canvas_h / rows))
    bottom = int(round((row + 1) * canvas_h / rows))
    return (left, top, right - left, bottom - top)


def compute_source_anchor_offsets(local_boxes, frame_sizes, content_in_frame,
                                 max_w: int, max_h: int, padding: int = 0):
    """按「原图锚点」精确补回偏移 —— 这是**精确**那条路，不做任何推断。

    用户口径（原话）："你先正常切割记录锚点位置，然后再紧切割记录位置，这两个位置的
    差值不就是偏移量吗" —— 对，就是这个：

      * 正常切割 = 整帧原图，锚点是它**画布**上的固定点（默认底部中心 = 脚底），每帧同一个点
      * 紧切割 = alpha 外框，这一帧的内容在原图坐标系里的位置是 ``local_boxes[i]``
      * 于是"把紧切图放回它原来的位置"就等价于把偏移补回去

    这里只要求"放回原位置"，不要求把锚点摆到画布某个特定点上 —— 各帧相对位置对了，
    播放就不会飘（整体平移多少不影响观看）。所以 ``offset = 原图坐标 − 居中落点``，
    落点公式与 ``compute_preview_layout`` 完全一致（居中 + 偏移），画布会按既有规则
    自己长大到装得下，padding 一个字不用动。

    参数：
        local_boxes[i]     该帧内容在**原图坐标系**里的 (x, y, w, h)，None = 这帧不参与
        frame_sizes[i]     该帧显示尺寸 (w, h)（与布局里用的一致）
        content_in_frame[i]  内容在该帧图像内部的偏移 (cx, cy)（单元素紧切帧就是 (0, 0)）
    返回 ``{帧序: (dx, dy)}``；信息不全的帧不出现在字典里（保留它原来的偏移）。
    """
    out: dict[int, tuple[int, int]] = {}
    count = len(local_boxes or ())
    for i in range(count):
        box = local_boxes[i]
        size = frame_sizes[i] if i < len(frame_sizes) else None
        inner = content_in_frame[i] if i < len(content_in_frame) else None
        if box is None or size is None or inner is None:
            continue
        bx, by, _bw, _bh = box
        w, h = size
        cix, ciy = inner
        place_x = padding + (max_w - w) // 2
        place_y = padding + (max_h - h) // 2
        out[i] = (int(round(bx)) - (place_x + int(round(cix))),
                  int(round(by)) - (place_y + int(round(ciy))))
    return out


def _frame_profile(pm: QPixmap, step: int = 4) -> tuple[list[int], list[int]]:
    """抽样的 alpha 列/行投影（给互相关用）。

    先在 C 层把图缩小（``scaled``），再用 Python 过一遍小图 —— 300x350 的帧缩到
    75x87 只有 6500 个字节，几百帧也就几百万次操作，点按钮不会卡界面。
    """
    if not pm or pm.isNull():
        return [], []
    sw = max(1, pm.width() // step)
    sh = max(1, pm.height() // step)
    small = pm.scaled(sw, sh, Qt.AspectRatioMode.IgnoreAspectRatio,
                      Qt.TransformationMode.FastTransformation)
    image = small.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    bits = image.constBits()
    bits.setsize(image.sizeInBytes())
    data = bytes(bits)
    stride = image.bytesPerLine()
    cols = [0] * sw
    rows = [0] * sh
    for y in range(sh):
        row = data[y * stride:y * stride + sw * 4]
        s = 0
        for x, v in enumerate(row[3::4]):
            if v:
                cols[x] += v
                s += v
        rows[y] = s
    return cols, rows


def _correlate_profiles(ref: list[int], prof: list[int], span: int) -> int:
    """``prof`` 要对上 ``ref`` 需要移动多少（重叠区点积最大处）。"""
    if not ref or not prof:
        return 0
    best, best_score = 0, None
    for shift in range(-span, span + 1):
        score = 0
        for i, v in enumerate(prof):
            if not v:
                continue
            j = i + shift
            if 0 <= j < len(ref):
                score += v * ref[j]
        if best_score is None or score > best_score:
            best_score, best = score, shift
    return best


def compute_align_offsets(action: Action, progress=None) -> dict[int, tuple[int, int]]:
    """**事后估算**：帧间 alpha 投影互相关 → 以"内容质量中位"的那一帧为准。

    只在拿不到"正常切割"（原图已经是紧切小图）的时候用这条路 —— 精确那条是
    ``compute_source_anchor_offsets``。为什么不用外框：外框由最远的那几个像素决定，
    尾巴/手臂一展开就把中线拽走（实测某段素材上外框法残留 6.0px，互相关 1.1px，
    而"点一下按钮什么都没变"正是外框法的典型表现）。

    为什么参考帧取"中位"而不是"内容最多"：飘的往往就是展开的那几帧，拿最展开的帧
    当基准等于把对的帧全带偏。中位数天然偏向多数帧（没展开的那些）。

    纯函数式：修正量只由各帧图像本身决定，所以连点两次结果一样（幂等）；
    代价是会把用户手动拖过的偏移覆盖掉 —— 一条 Ctrl+Z 能退回来。
    """
    count = len(action.frames)
    if count < 2:
        return dict(action.frame_offsets)          # 一帧谈不上"对齐"
    positions = compute_preview_layout(action)[2]
    if len(positions) != count:
        return dict(action.frame_offsets)
    live = [i for i in range(count) if frame_has_content(action, i)]
    if len(live) < 2:
        return dict(action.frame_offsets)
    step = 4
    profiles: dict[int, tuple[list[int], list[int]]] = {}
    mass: dict[int, int] = {}
    for n_done, i in enumerate(live):
        cols, rows = _frame_profile(action_frame_pixmap(action, i), step)
        profiles[i] = (cols, rows)
        mass[i] = sum(cols)
        if progress is not None:
            # 真进度（用户要求）：逐帧取 alpha 投影 —— 这一步才是这条路的重活，
            # 后面的互相关很便宜，所以只在这里报数
            progress(n_done + 1, len(live))
    # 参考帧 = 内容量（投影质量）中位的那一帧，见 docstring
    order = sorted(live, key=lambda i: mass[i])
    ref_i = order[len(order) // 2]
    ref_x, ref_y = profiles[ref_i]
    # 目标落点取"参考帧去掉自身偏移后的居中落点"（= 纯函数，跟当前偏移无关），
    # 于是连点两次结果完全一样：第一次算完各帧内容已经重合，第二次算出来还是同一组值。
    # 反例（踩过）：拿"当前落点"当基准 → 第二次把上一次的修正又减一遍，按钮来回摆。
    ref_off = action.frame_offsets.get(ref_i, (0, 0))
    base_ref_x = positions[ref_i][0] - int(ref_off[0])
    base_ref_y = positions[ref_i][1] - int(ref_off[1])
    span_x = max(4, max(1, max(len(profiles[i][0]) for i in live)) // 4)
    span_y = max(4, max(1, max(len(profiles[i][1]) for i in live)) // 4)
    offsets = dict(action.frame_offsets)
    for i in live:
        off = action.frame_offsets.get(i, (0, 0))
        base_x = positions[i][0] - int(off[0])
        base_y = positions[i][1] - int(off[1])
        dx = _correlate_profiles(ref_x, profiles[i][0], span_x) * step
        dy = _correlate_profiles(ref_y, profiles[i][1], span_y) * step
        # 互相关是在"各帧自己内部"的坐标里算的 → 换算成落点：落点_i 应等于 落点_ref + 位移
        offsets[i] = (int(round(base_ref_x + dx - base_x)),
                      int(round(base_ref_y + dy - base_y)))
    return offsets


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
