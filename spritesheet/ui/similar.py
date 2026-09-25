#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""相似帧查找：把全图元素里"长得像"的归成组，给「帧工具 · 相似帧」那颗开关按钮用。

用户口径（原话）：
  * 「主要是查找所有的元素中相似的 在右下角用相似的颜色做标记（颜色一定要区分好）」
  * 「原图页面要显示 帧缩略图也要显示」（缩略图那边改到**右上角** —— 右下角留给帧序号）
  * 「阈值要放设置里 默认是要一模一样」
  * 「全图识别（有别的新图也要找）」
  * 「不带信息」（只要颜色，色块旁边不写第几组/共几帧）
  * 「这个功能主要是**找循环帧**用的」→ 除了颜色，还要能说出循环长度

分组口径：
  * 容差 = 0（默认）→ **一模一样**才算一组：直接比整张 RGBA 的 md5，字节级相同才同色。
  * 容差 > 0 → 缩到 32x32 比「形状重合度（IoU）+ 颜色差」，两个都过得去才算相似；
    分组用并查集（A≈B、B≈C 就归一组），保证颜色稳定、不会闪来闪去。

性能：289 个元素两两硬比是 4 万次。所以先分桶（一模一样按尺寸+哈希；容差模式按尺寸粗桶），
只在桶里细比 —— 点一下是毫秒级。
"""
from __future__ import annotations

import hashlib

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage, QPixmap

# 一套"互相拉得开"的标记色（避开标线 #00FFFF / 选中 #E91E63 / 网格 #808080）：
# 同色 = 同一组相似帧；相邻两组在色相上差得远，一眼能分开。
PALETTE = (
    "#FF6D00",  # 橙
    "#00E676",  # 绿
    "#2979FF",  # 蓝
    "#FFD600",  # 黄
    "#D500F9",  # 紫
    "#00E5FF",  # 青
    "#FF1744",  # 红
    "#76FF03",  # 黄绿
    "#651FFF",  # 靛
    "#FF9100",  # 琥珀
    "#1DE9B6",  # 蓝绿
    "#F50057",  # 玫红
    "#FFEA00",  # 亮黄
    "#00B0FF",  # 天蓝
    "#C6FF00",  # 青柠
    "#FF4081",  # 粉
)


def group_color(group: int) -> QColor:
    """第 group 组的标记色（组号从 0 起；超过色板长度就循环用）。"""
    return QColor(PALETTE[max(0, int(group)) % len(PALETTE)])


def _rgba_bytes(pm: QPixmap) -> bytes:
    """整张图的 RGBA 字节（一模一样就靠它比）。"""
    image = pm.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    bits = image.constBits()
    bits.setsize(image.sizeInBytes())
    return bytes(bits)


def _features(pm: QPixmap, size: int = 64):
    """一个元素的「指纹」：(遮罩位图, 遮罩像素数, 遮罩内平均色) —— 容差模式用。

    遮罩存成一个 int 位图：两两比时一次 AND + bit_count 就出交并集，289 个元素四万次
    比较也是毫秒级（拿列表逐像素扫要几十秒）。
    **必须 64 而不是 32**：32x32 会把"换了个姿势"平均掉，两帧不同姿势的 IoU 也能到 99%+，
    结果容差 1 就"全都像"（用户实测反馈的就是这个）。
    """
    small = pm.scaled(size, size, Qt.AspectRatioMode.IgnoreAspectRatio,
                      Qt.TransformationMode.FastTransformation)
    image = small.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    bits = image.constBits()
    bits.setsize(image.sizeInBytes())
    data = bytes(bits)
    stride = image.bytesPerLine()
    mask = 0
    count = 0
    sum_r = sum_g = sum_b = 0
    for y in range(size):
        row = data[y * stride:y * stride + size * 4]
        for x in range(size):
            if row[x * 4 + 3] >= 8:
                mask |= 1 << (y * size + x)
                count += 1
                sum_r += row[x * 4]
                sum_g += row[x * 4 + 1]
                sum_b += row[x * 4 + 2]
    mean = (sum_r / count, sum_g / count, sum_b / count) if count else (0.0, 0.0, 0.0)
    return mask, count, mean


def _similar_fast(feature_a, feature_b, tolerance: int) -> bool:
    """形状重合度（遮罩 IoU）+ 内容平均色差 —— 这两个量都只在「有内容的地方」上算。

    **刻度**（64x64 = 4096 个格）：容差 t 表示"最多允许 t/1000 的格子不一样"，
        t=0 → 必须一模一样（走 md5 那条更快的路，不到这儿）
        t=1 → 允许 0.1%（64x64 上约 4 格）不一样
        t=5 → 0.5%（约 20 格）；t=20 → 2%（约 82 格）
    这样 0/1/2/3… 是一档一档有用的，不会出现"1 就全都找到"。

    原来两个坑都在这里：① 色差是在整张（含透明背景）上求平均，透明像素全是 0 把差异
    稀释掉了；② 32x32 太粗，不同姿势也能到 99%+。两处都按用户在真素材上的实测反馈改掉了。
    """
    mask_a, count_a, mean_a = feature_a
    mask_b, count_b, mean_b = feature_b
    inter = (mask_a & mask_b).bit_count()
    union = count_a + count_b - inter
    if union == 0:
        return True                       # 两张都全透明，算一样
    if inter / union < 1.0 - tolerance / 1000.0:
        return False
    return max(abs(a - b) for a, b in zip(mean_a, mean_b)) <= tolerance * 0.5


def group_elements(pixmaps, tolerance: int = 0, progress=None) -> dict[int, int]:
    """全图元素按"像不像"分组：返回 {元素下标: 组号}，**只含成员 ≥2 的组**。

    唯一的组（没有同伴的帧）不出现在结果里 —— 它们没得标，也不该占一个颜色。

    ``progress``：可选回调 ``progress(done, total_units)``，给"**真**进度条"报进度
    （用户口径：相似帧 / 抽帧这类计算要真进度条，不要转圈）。容差模式才是重活，
    所以按"取特征 + 逐帧跟代表帧比"两阶段报，总单位 = 2×帧数。
    """
    count = len(pixmaps or ())
    if count < 2:
        return {}
    total_units = count * 2
    parent = list(range(count))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)   # 往小下标并：组号天然按首次出现排序

    tolerance = max(0, int(tolerance))
    if tolerance == 0:
        buckets: dict[tuple, list[int]] = {}
        for i, pm in enumerate(pixmaps):
            if pm is None or pm.isNull():
                continue
            key = (pm.width(), pm.height(), hashlib.md5(_rgba_bytes(pm)).digest())
            buckets.setdefault(key, []).append(i)
            if progress is not None:
                # 精确模式只有一个阶段（纯 md5，很快）：按 0→100% 报满
                progress((i + 1) * 2, total_units)
        for ids in buckets.values():
            for j in ids[1:]:
                union(ids[0], j)
    else:
        # 容差模式：**跟每组的第一帧（代表帧）比**，不是跟组里任何一个比 ——
        # 后者会链式传染（A≈B、B≈C 就把 A 和 C 也归一组），最后"全都是一组"。
        features = {}
        order = []
        for i, pm in enumerate(pixmaps):
            if pm is None or pm.isNull():
                continue
            features[i] = _features(pm)
            order.append(i)
            if progress is not None:
                progress(count + i + 1, total_units)
        reps: list[tuple[int, tuple]] = []
        for n_done, i in enumerate(order):
            for gi, (_rep_i, rep_f) in enumerate(reps):
                if _similar_fast(features[i], rep_f, tolerance):
                    union(i, _rep_i)
                    break
            else:
                reps.append((i, features[i]))
            if progress is not None:
                # 第二阶段：逐帧跟各代表帧比 —— 单位接着第一阶段的 50% 往后报（50%→100%）
                progress(count + n_done + 1, total_units)

    members: dict[int, list[int]] = {}
    for i in range(count):
        members.setdefault(find(i), []).append(i)
    groups = sorted((ids for ids in members.values() if len(ids) >= 2),
                    key=lambda ids: ids[0])
    out: dict[int, int] = {}
    for gi, ids in enumerate(groups):
        for i in ids:
            out[i] = gi
    return out


# 抽帧档位：按"抽掉多少"从轻到重（用户要求行内顺序 3/5 → 1/2 → 1/4 → 强力去重）。
# 值是保留比例 (num, den)；None = 强力去重（一串只留 1 帧）。
DECIMATE_RATIOS = {
    "keep_3_5": (3, 5),
    "keep_1_2": (1, 2),
    "keep_1_4": (1, 4),
    "dedupe": None,
}
DECIMATE_MODES = ("keep_3_5", "keep_1_2", "keep_1_4", "dedupe")


def _iou(feat_a, feat_b) -> float:
    """两张帧特征的重合度（和分组里用的分数同一个口径）。"""
    inter = (feat_a[0] & feat_b[0]).bit_count()
    union = (feat_a[0] | feat_b[0]).bit_count()
    return inter / union if union else 1.0


def split_runs(marks: dict[int, int], total: int) -> list[list[int]]:
    """把帧切成「串」：**同一个相似组 + 帧序上紧挨着**才算一串。

    用户口径：「一串就是 相似帧的颜色一样啊 只有相邻的才行 不连着的颜色一样不算一串」。
    中间夹了别的帧、或没配对的帧，就把串断开；长度 < 2 的不算串。
    注：颜色是组号映射出来的，极端情况下两个组可能撞同一个颜色，那也不该并成一串，
    所以这里判的是**组号**而不是颜色值。
    """
    runs: list[list[int]] = []
    cur: list[int] = []
    prev = None
    for i in range(total):
        gid = marks.get(i)
        if gid is not None and gid == prev:
            cur.append(i)
        else:
            if len(cur) > 1:
                runs.append(cur)
            cur = [i] if gid is not None else []
        prev = gid
    if len(cur) > 1:
        runs.append(cur)
    return runs


def _center_offset(pixmaps, run: list[int]) -> int:
    """这串里「最标准」那帧的相对位置：跟同串其他帧平均最像的那一帧。

    并列时取靠前的，保证同样输入结果可复现（用户要求：一串里留最标准那帧）。
    """
    feats = [_features(pixmaps[i]) for i in run]
    best_j, best_score = 0, -1.0
    for j in range(len(feats)):
        score = 0.0
        for k in range(len(feats)):
            if j != k:
                score += _iou(feats[j], feats[k])
        score /= max(1, len(feats) - 1)
        if score > best_score + 1e-9:
            best_j, best_score = j, score
    return best_j


def _pick_offsets(n: int, keep: int, center: int) -> list[int]:
    """在一串 n 帧里挑 keep 个位置：均匀铺开，并且**一定包含最标准那帧**。"""
    if keep >= n:
        return list(range(n))
    if keep <= 1:
        return [center]
    picked = {center}
    for k in range(keep):                       # 先均匀撒点，再保证中心帧在里头
        picked.add(round(k * (n - 1) / (keep - 1)))
    while len(picked) > keep:                   # 撒多了就把离中心最远的去掉
        far = max(picked, key=lambda j: (abs(j - center), j))
        if far == center:
            break
        picked.discard(far)
    return sorted(picked)


def decimate_indices(pixmaps, marks: dict[int, int], mode: str = "keep_1_2") -> list[int]:
    """抽帧：返回**保留**的帧号（升序）。mode 取值见 ``DECIMATE_RATIOS``。

    规则（用户口径）：一串 N 帧里按档位只留 ceil(N*num/den) 帧，**最少留 1 帧**（绝不把一串抽空）；
    不相邻的、没配对的帧原样保留；每串保留的帧里必定包含最标准那一帧。
    """
    total = len(pixmaps)
    if total <= 1 or not marks:
        return list(range(total))
    ratio = DECIMATE_RATIOS.get(mode, (1, 2))
    keep_idx = set(range(total))
    for run in split_runs(marks, total):
        n = len(run)
        if ratio is None:
            keep = 1                            # 强力去重：一串只留 1 帧
        else:
            num, den = ratio
            keep = max(1, (n * num + den - 1) // den)
        if keep >= n:
            continue                            # 一串太短，没得抽
        keep_pos = set(_pick_offsets(n, keep, _center_offset(pixmaps, run)))
        for j, idx in enumerate(run):
            if j not in keep_pos:
                keep_idx.discard(idx)
    return sorted(keep_idx)


def frame_source_table(selected_indices, composite_groups=None, frame_layouts=None,
                       n_elements: int | None = None):
    """逐帧 → 组成它的元素索引。**必须与 image_viewer.crop_frames_for_action 完全一致**。

    那函数的真实做法（别照模型注释想当然）：
      * 有 ``frame_layouts``：一条布局一帧（部件索引全在布局里）；元素索引越界的布局会被
        跳过（`if not valid: continue`），所以帧号要压掉这些条目；
      * 否则按 ``selected_indices`` **顺序**遍历，遇到属于某个合成组的元素就**整组出一帧**
        （并把这些元素标成 emitted，后面再遇到就跳过）；不在任何组里的元素各自出一帧；
      * 组内元素索引全都越界的那一帧同样被跳过。

    踩过的坑：最初我写成"帧号 k 的成员 = composite_groups[k]"，那只有在"每个元素都恰好属于
    某个组、且组序与选中序完全对齐"时才碰巧成立。一旦有元素不属于任何组（合成组在后），
    映射就整体错位 —— 抽帧会删错元素，画面里"后面的元素就位移了"。
    """
    def _ok(i: int) -> bool:
        if not isinstance(i, int):
            return False
        if n_elements is None:
            return True
        return 0 <= i < n_elements

    if frame_layouts:
        table = []
        for layout in frame_layouts:
            members = [part[0] for part in layout if _ok(part[0])]
            if members:
                table.append(members)
        return table
    group_by_idx = {}
    for group in (composite_groups or []):
        for idx in group:
            group_by_idx[idx] = list(group)
    table = []
    emitted: set[int] = set()
    for idx in (selected_indices or []):
        if idx in emitted:
            continue
        group = [i for i in group_by_idx.get(idx, [idx]) if _ok(i)]
        if not group:
            continue
        table.append(group)
        emitted.update(group)
    return table


def frame_element_sources(frame_index: int, selected_indices, composite_groups=None,
                          frame_layouts=None, n_elements: int | None = None) -> list[int]:
    """第 frame_index 帧是由哪些**元素索引**来的（薄封装，便于单点调用）。"""
    table = frame_source_table(selected_indices, composite_groups, frame_layouts, n_elements)
    if 0 <= frame_index < len(table):
        return table[frame_index]
    return []


def plan_decimate(n_frames: int, selected_indices, composite_groups=None,
                  frame_layouts=None, frame_layouts_base: int = 0,
                  keep=None, n_elements: int | None = None) -> dict:
    """抽帧计划（纯函数，不碰 UI）：算出「删哪些元素 + 每帧结构怎么重排」。

    返回 dict：
      ``drop_elements``  要从 ``selected_indices`` 里删掉的元素索引（升序去重）
      ``keep_selected``  新的 selected_indices（保持原相对顺序）
      ``keep_composite`` 新的 composite_groups（没有就是 []）
      ``keep_layouts``   新的 frame_layouts（原本是 None 就还是 None）
      ``base_after``     新的 frame_layouts_base
      ``frame_map``      {旧帧号: 新帧号}（被抽掉的帧不在里面）
      ``removed_frames`` 被抽掉的旧帧号（升序）

    重要：元素可能被**多帧共用**（合成帧/导入布局都会），所以只有当某元素**所有**引用它的帧
    都被抽掉时才删这个元素 —— 否则会把还留着的帧弄坏（这是最容易踩的坑）。
    """
    total = int(n_frames)
    keep_set = set(range(total)) if keep is None else {int(i) for i in keep if 0 <= int(i) < total}
    removed = [i for i in range(total) if i not in keep_set]
    frame_map = {old: new for new, old in enumerate(sorted(keep_set))}

    sources = {}
    table = frame_source_table(selected_indices, composite_groups, frame_layouts, n_elements)
    for i in range(total):
        sources[i] = table[i] if i < len(table) else []
    still_used: set[int] = set()
    for old in sorted(keep_set):
        still_used.update(sources.get(old, []))
    drop_elements = sorted({e for old in removed for e in sources.get(old, [])
                            if e not in still_used})

    keep_selected = [e for e in selected_indices if e not in set(drop_elements)]
    keep_composite = []
    if composite_groups:
        keep_composite = [list(composite_groups[old]) for old in sorted(keep_set)
                          if old < len(composite_groups)]
    keep_layouts = None
    if frame_layouts is not None:
        keep_layouts = [list(frame_layouts[old]) for old in sorted(keep_set)
                        if old < len(frame_layouts)]
    base = int(frame_layouts_base or 0)
    base_after = sum(1 for old in sorted(keep_set) if old < base)
    return {
        "drop_elements": drop_elements,
        "keep_selected": keep_selected,
        "keep_composite": keep_composite,
        "keep_layouts": keep_layouts,
        "base_after": base_after,
        "frame_map": frame_map,
        "removed_frames": removed,
    }


def compensate_offsets(sizes_old: dict, offsets_old: dict, sizes_new: dict,
                       frame_map: dict[int, int]) -> dict:
    """抽帧后把每个保留帧的偏移补偿掉，让它的**落点逐帧不变**。

    落点公式（transform.compute_preview_layout）：
        x = padding + (max_w − frame_w)//2 + offset_x      （y 同理）
    ``max_w`` 是"本动作所有帧里最大的宽"。抽帧会把某些帧抽掉，**相似 ≠ 同一尺寸**：
    一串里尺寸可以不一样，而保留的是"最标准那帧" —— 它可能比被抽掉的更大或更小。
    于是 ``max`` 会变，``(max−size)//2`` 这一项对所有帧一起变 ⇒ 画面整体位移。

    补偿量 = 居中项的变化量，逐帧算（每帧尺寸不同、补偿量也不同）：
        new_offset = old_offset + [ (max_old−size)//2 − (max_new−size)//2 ]

    返回新的 ``{新帧号: (dx, dy)}``（补偿后为 (0,0) 的帧不写进去，保持字典干净）。
    """
    def _maxwh(sizes: dict) -> tuple[int, int]:
        if not sizes:
            return 0, 0
        return (max(s[0] for s in sizes.values()), max(s[1] for s in sizes.values()))

    max_old = _maxwh(sizes_old)
    max_new = _maxwh(sizes_new)
    out: dict[int, tuple[int, int]] = {}
    for old, new in frame_map.items():
        size = sizes_old.get(old)
        if size is None or new not in sizes_new:
            continue
        dx = (max_old[0] - size[0]) // 2 - (max_new[0] - size[0]) // 2
        dy = (max_old[1] - size[1]) // 2 - (max_new[1] - size[1]) // 2
        ex, ey = offsets_old.get(old, (0, 0))
        nx, ny = ex + dx, ey + dy
        if nx or ny:
            out[new] = (nx, ny)
    return out


def remap_frame_dict(mapping, frame_map: dict[int, int]) -> dict:
    """把 ``{帧号: 值}`` 这种每帧字典按 frame_map 重排（被抽掉的帧直接丢掉）。"""
    out = {}
    for old, value in (mapping or {}).items():
        new = frame_map.get(int(old))
        if new is not None:
            out[new] = value
    return out


def remap_frame_set(members, frame_map: dict[int, int]) -> set:
    """把 ``{帧号, ...}`` 这种每帧集合按 frame_map 重排。"""
    return {frame_map[int(i)] for i in (members or ()) if int(i) in frame_map}


def loop_hint(marks: dict[int, int]) -> str:
    """「找循环帧」的结论：当前动作里最可能的循环长度，一句话。

    marks = {帧下标: 组号}（只含当前动作的帧）。做法直白：同一组的两帧距离 d = j - i
    就是一次"循环落点"，统计哪个 d 出现得最多，它就是循环长度。d < 2 的排除掉 ——
    相邻两帧长得一样是"重复帧"，不是循环。
    没找到就返回空串（调用方不显示这一句）。
    """
    ids = sorted(marks or {})
    counter: dict[int, int] = {}
    example: dict[int, tuple[int, int]] = {}
    for ai in range(len(ids)):
        for bi in range(ai + 1, len(ids)):
            i, j = ids[ai], ids[bi]
            d = j - i
            if d < 2 or marks[i] != marks[j]:
                continue
            counter[d] = counter.get(d, 0) + 1
            example.setdefault(d, (i, j))
    if not counter:
        return ""
    best = max(counter.items(), key=lambda kv: (kv[1], -kv[0]))[0]
    i, j = example[best]
    return f"第 {i + 1} 帧 ≈ 第 {j + 1} 帧 → 循环长度 {best} 帧"