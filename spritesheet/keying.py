"""抠除背景：传统手调算法（纯 Pillow，不依赖 Qt，可脱离界面单独测）。

为什么不做 AI 抠图：本工具吃的是游戏精灵图和 AI 生成的关键帧，背景基本是纯色或单调
渐变，传统"取样 + 容差 + 边缘连通"够用；而 rembg 那条路线会连带拉进 scipy /
scikit-image / opencv / onnxruntime 一堆用不上的库（实测解包后 +150~200MB，光
onnxruntime 就 40MB），安装包会从 30MB 涨到 200MB 量级，不划算。本模块只依赖
Pillow —— 它本来就在包里（GIF 导出在用），所以抠图**新增依赖 0**。

三个决定成败的实现细节（都实测过，别改成"看起来更直观"的写法）：

1. **所有逐像素运算走 Pillow 的 C 层**（ImageChops.difference / lighter、point、
   MinFilter、ImageMath）。2048² 实测整条管线约 130ms —— 这是"拖滑块能实时预览"的
   前提；同样的事用 Python 逐像素循环要好几秒，滑块一拖就卡死。
2. **只有"与图片边缘连通"这一件事是 Python，但按行程（run）而不是按像素**：每行切成
   若干连续区间，再用并查集把相邻行里有列重叠的区间并起来。循环次数是行程数级别，
   2048² 也就几毫秒；纯 Python 的逐像素 flood fill 同样要好几秒。
3. **alpha 按色距渐变，而不是硬二值**：硬二值会把抗锯齿边缘切成锯齿。真正那"一圈
   残留白边"是靠**收边**（对 alpha 做 MinFilter 腐蚀）去掉的，不是靠把容差调大 ——
   容差调大只会连角色身上的浅色一起吃掉。

参数（界面上都能调，定义见 PARAM_SPEC）：

- ``tolerance``  每通道差值 ≤ 容差就算背景。用"最大通道差"而不是欧氏距离：用户能自己
  心算（"这底色跟纯白每通道差 20 以内"），出问题也好解释。
- ``soft``       容差之外再留一段做 alpha 渐变（0 = 硬边）。
- ``edge_only``  只清"与图片边缘连通"的背景，保护角色内部的同色像素（白衣服、眼白、
  高光），默认开。
- ``shrink``     alpha 收缩 / 扩张 N 像素：正数 = 收缩（腐蚀，去残留），负数 = 扩张。
- ``despill``    半透明边缘里混进来的背景色做 un-premultiply 减掉，0~100。
"""
from __future__ import annotations

from PIL import Image, ImageChops, ImageFilter, ImageMath

# 参数默认值。界面上的初始值、以及"重置"都用这一份，避免两处各写一套。
DEFAULTS: dict = {
    "tolerance": 28,
    "soft": 40,
    "edge_only": True,
    "shrink": 0.0,
    "despill": 30,
}

# 界面用它自动生成控件：改这里就够了，不用两头改。
PARAM_SPEC: tuple = (
    {"key": "tolerance", "label": "容差", "kind": "int", "min": 0, "max": 150, "step": 1,
     "tip": "每通道差值不超过它就当成背景。底色有噪点、有渐变就调大；\n"
            "调太大连角色身上的浅色也会被吃掉。"},
    {"key": "soft", "label": "软边", "kind": "int", "min": 0, "max": 150, "step": 1,
     "tip": "容差之外再留一段做透明渐变，保住抗锯齿边缘（0 = 硬边，像素图可以设 0）。"},
    {"key": "edge_only", "label": "只清边缘连通", "kind": "bool",
     "tip": "只清从图片边缘能连过去的背景，角色内部的同色像素（白衣服、眼白）不动。\n"
            "如果背景被角色围成一圈清不掉，把这个关掉。"},
    {"key": "shrink", "label": "收边", "kind": "float", "min": -2.0, "max": 4.0, "step": 0.5,
     "decimals": 1,
     "tip": "alpha 收缩 / 扩张几像素。正数收缩：去掉边缘那圈残留底色（最常见的一步）；\n"
            "负数扩张：边缘被啃掉一点时补回来。\n"
            "0.5 一档（共 13 档）：整数档就是「收缩 1/2/3/4 像素」，和以前逐像素一致；\n"
            "半档是整数档之间的过渡（边缘变成半透明，用完可以再微调）。"},
    {"key": "despill", "label": "去色溢", "kind": "int", "min": 0, "max": 100, "step": 5,
     "tip": "半透明边缘里混着背景色（白底就发白、绿幕就发绿），把它减掉。\n"
            "0 = 不动颜色。"},
)

# 猜背景色时四边各取几像素
BORDER_SAMPLE = 2


def estimate_bg_color(img: Image.Image, border: int = BORDER_SAMPLE):
    """从图片四边猜背景色，返回 (r, g, b)；返回 None 表示四边几乎没有不透明像素。

    不只看左上角一个像素：AI 出的图常有噪点、渐变、边缘脏点，单点取样会偏，抠出来
    就是一张花脸。做法是四边各取 border 像素宽的条，统计里面不透明像素的颜色，
    取出现最多的那个（颜色先量化到 8 级抗噪，再取该桶内像素的均值消掉量化误差）。
    """
    w, h = img.size
    rgba = img if img.mode == "RGBA" else img.convert("RGBA")
    b = max(1, min(int(border), w, h))
    strips = (
        rgba.crop((0, 0, w, b)),
        rgba.crop((0, h - b, w, h)),
        rgba.crop((0, 0, b, h)),
        rgba.crop((w - b, 0, w, h)),
    )
    buckets: dict = {}          # 量化色 -> [r 总和, g 总和, b 总和, 个数]
    for strip in strips:
        data = strip.tobytes()  # RGBA，按 4 字节一步走，比 getdata() 快且不触发弃用警告
        for i in range(0, len(data), 4):
            if data[i + 3] < 128:       # 已经是透明的像素不参与猜色
                continue
            key = (data[i] // 8, data[i + 1] // 8, data[i + 2] // 8)
            slot = buckets.get(key)
            if slot is None:
                buckets[key] = [data[i], data[i + 1], data[i + 2], 1]
            else:
                slot[0] += data[i]
                slot[1] += data[i + 1]
                slot[2] += data[i + 2]
                slot[3] += 1
    if not buckets:
        return None
    best = max(buckets.values(), key=lambda slot: slot[3])
    return (best[0] // best[3], best[1] // best[3], best[2] // best[3])


def _normalize_colors(bg_colors) -> list:
    """把"单个颜色"和"颜色列表"统一成列表，省得调用方判断。"""
    if not bg_colors:
        return [(0, 0, 0)]
    if isinstance(bg_colors[0], int):
        return [tuple(bg_colors[:3])]
    return [tuple(color[:3]) for color in bg_colors]


def diff_map(img: Image.Image, bg_colors) -> Image.Image:
    """每像素"与背景色的最大通道差"（0~255 的 L 图）。给了多个取样点时取最小差。"""
    rgb = img.convert("RGB")
    result = None
    for color in _normalize_colors(bg_colors):
        solid = Image.new("RGB", rgb.size, tuple(color))
        diff = ImageChops.difference(rgb, solid)
        r, g, b = diff.split()
        # lighter 就是逐像素取大：三个通道取最大 = 最大通道差（全程 C 层）
        one = ImageChops.lighter(ImageChops.lighter(r, g), b)
        result = one if result is None else ImageChops.darker(result, one)
    return result


def alpha_lut(tolerance: int, soft: int) -> list:
    """色距 -> alpha 的 256 项查找表，交给 point() 一次算完整张图（C 层）。"""
    tol = max(0, min(255, int(tolerance)))
    soft = max(0, int(soft))
    lut = []
    for value in range(256):
        if value <= tol:
            lut.append(0)                       # 底色，直接透明
        elif soft <= 0:
            lut.append(255)                     # 硬边：不是底色就完全不透明
        else:
            lut.append(min(255, (value - tol) * 255 // soft))
    return lut


def _row_runs(row: bytes) -> list:
    """一行里值为 255 的连续区间（闭区间）。按行程走，不按像素走。"""
    runs = []
    pos = row.find(255)
    while pos >= 0:
        end = row.find(0, pos)
        if end < 0:
            end = len(row)
        runs.append((pos, end - 1))
        pos = row.find(255, end)
    return runs


def edge_connected_mask(diff: Image.Image, tolerance: int, soft: int = 0):
    """求"与图片边缘连通"的底色区域，返回 (0/255 的 L 图, 连通块数)。

    候选 = 色距 ≤ **容差 + 软边** 的像素。这里必须把软边那一段也算进来：抗锯齿环的
    色距正好落在"容差之外、容差 + 软边之内"，若不算进来，这一圈会被当成"非背景"而
    保持完全不透明 —— 表现就是软边滑块拖了没反应（实测踩过这个坑）。

    判定用行程 + 并查集：先按行切行程，再把相邻行里列区间有重叠的行程 union 起来；
    最后凡是"碰到图片最外圈"的连通块整块保留。循环次数是行程数级别（不是像素数），
    所以 2048² 也就几毫秒。
    """
    limit = max(0, min(255, int(tolerance) + max(0, int(soft))))
    binary = diff.point([255 if value <= limit else 0 for value in range(256)])
    w, h = binary.size
    data = binary.tobytes()

    rows = [_row_runs(data[y * w:(y + 1) * w]) for y in range(h)]
    offsets = []
    total = 0
    for runs in rows:
        offsets.append(total)
        total += len(runs)

    parent = list(range(total))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]       # 路径压缩
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # 相邻行行程求交：两侧区间都有序，双指针一趟扫完
    for y in range(h - 1):
        up, down = rows[y], rows[y + 1]
        i = j = 0
        base_up, base_down = offsets[y], offsets[y + 1]
        while i < len(up) and j < len(down):
            a0, a1 = up[i]
            b0, b1 = down[j]
            if a0 <= b1 and b0 <= a1:
                union(base_up + i, base_down + j)
            if a1 < b1:
                i += 1
            else:
                j += 1

    # 碰边的行程（上下两行整行、左右两列）
    touching = []
    for y, runs in enumerate(rows):
        base = offsets[y]
        for k, (x0, x1) in enumerate(runs):
            if y == 0 or y == h - 1 or x0 == 0 or x1 == w - 1:
                touching.append(base + k)

    # 保留所有"所在连通块碰到图片最外圈"的行程，整块刷 255
    keep = bytearray(w * h)
    roots = {find(idx) for idx in touching}
    for y, runs in enumerate(rows):
        row_base = y * w
        base = offsets[y]
        for k, (x0, x1) in enumerate(runs):
            if find(base + k) in roots:
                keep[row_base + x0: row_base + x1 + 1] = b"\xff" * (x1 - x0 + 1)
    return Image.frombytes("L", (w, h), bytes(keep)), len(roots)


def key_image(img: Image.Image, bg_colors, **params):
    """按参数抠一次，返回 (新图, 统计)。

    **绝不修改传入的 img**：主界面快照存的是引用（QPixmap 隐式共享），就地改会把
    撤销用的"改之前"一起改掉，症状是"点了撤销画面没变"。

    管线（顺序有讲究）：
        色距图 -> 按查找表映射 alpha -> （只清边缘连通时）限定到连通区 ->
        与原 alpha 取更小值（只减不增）-> 收边 -> 去色溢 -> 写回 alpha
    """
    cfg = dict(DEFAULTS)
    cfg.update({key: value for key, value in params.items() if key in DEFAULTS})
    colors = _normalize_colors(bg_colors)

    rgba = img if img.mode == "RGBA" else img.convert("RGBA")
    alpha0 = rgba.getchannel("A")
    diff = diff_map(rgba, colors)
    mapped = diff.point(alpha_lut(cfg["tolerance"], cfg["soft"]))

    if cfg["edge_only"]:
        region, components = edge_connected_mask(diff, cfg["tolerance"], cfg["soft"])
        opaque = Image.new("L", rgba.size, 255)
        base = Image.composite(mapped, opaque, region)   # 只有连通区里允许被清
    else:
        components = 1
        base = mapped

    # 只减不增：原来就透明的像素不会因为"它的颜色像背景"被写回不透明
    final = ImageChops.darker(base, alpha0)

    shrink = float(cfg["shrink"])
    if shrink:
        final = _apply_shrink(final, shrink)

    if cfg["despill"] > 0:
        out = _apply_despill(rgba, final, colors[0], cfg["despill"])
    else:
        out = rgba.copy()
    out.putalpha(final)

    total = rgba.size[0] * rgba.size[1]
    before = alpha0.histogram()
    after = final.histogram()
    changed = sum(ImageChops.difference(alpha0, final).histogram()[1:])
    stats = {
        "bg_color": colors[0],
        "components": components,
        "changed_pct": (changed * 100.0 / total) if total else 0.0,
        "content_before_pct": (sum(before[128:]) * 100.0 / total) if total else 0.0,
        "content_after_pct": (sum(after[128:]) * 100.0 / total) if total else 0.0,
        "opaque_before_pct": (before[255] * 100.0 / total) if total else 0.0,
        "opaque_after_pct": (after[255] * 100.0 / total) if total else 0.0,
    }
    return out, stats


def _apply_shrink(alpha: Image.Image, shrink: float) -> Image.Image:
    """收边（``shrink`` 可为小数，0.5 一档）：正数收缩 alpha，负数扩张。

    整数档就是老的 ``MinFilter/MaxFilter``（滤波器尺寸必须是奇数，所以整数粒度只能是
    1 像素），**逐像素结果与以前一致**；半档用"相邻两个整数档之间做 alpha 线性插值"
    来实现 —— 于是半档表现为边缘半透明（不是真的收掉半个像素，alpha 蒙版做不到那种事，
    但档位确实更细了，配合去色溢看着就是"收得更狠一点"）。
    """
    whole = int(abs(shrink))
    frac = abs(shrink) - whole
    filt = ImageFilter.MinFilter if shrink > 0 else ImageFilter.MaxFilter
    if whole >= 1:
        level_a = alpha.filter(filt(min(9, whole * 2 + 1)))
    else:
        # |shrink| < 1（也就是 0.5 那一档）：整数部分是 0，"A 档"就是原图。
        # 千万别写成 MinFilter(1) —— PIL 的 rank filter 尺寸 1 会直接
        # 整数除零崩掉（0xC0000094），实测踩过。
        level_a = alpha
    next_size = min(9, whole * 2 + 3)
    if frac < 0.001 or next_size == min(9, max(1, whole * 2 + 1)):
        return level_a
    level_b = alpha.filter(filt(next_size))
    return ImageChops.blend(level_a, level_b, frac)


def _apply_despill(rgba: Image.Image, alpha: Image.Image, bg_color, strength: int) -> Image.Image:
    """把半透明边缘里混进去的背景色减掉（un-premultiply）。

    边缘像素是"角色色 F 与背景色 B 按 alpha 混合"的结果：C = a·F + (1-a)·B，
    反解 F = (C - (1-a)·B) / a —— 这一步是除法，Pillow 里只有 ImageMath 能做，
    而它是 C 层实现的，所以整张图也是毫秒级。表达式是固定的常量字符串（不含任何外部
    输入），因此用 unsafe_eval 是安全的；``max(a, 1)`` 是除零保护（a=0 的像素本来就
    完全透明，颜色值无所谓）。
    """
    ratio = max(0, min(100, int(strength))) / 100.0
    if ratio <= 0:
        return rgba.convert("RGBA")

    # 只有"半透明"的像素需要还原颜色：全不透明的算出来就是原色，全透明的颜色看不见。
    # 所以先框出半透明那一圈再算 —— 精灵图里这圈通常只是外轮廓（硬边图甚至直接是空的），
    # 能省掉大部分计算，这是 2048² 从 500ms 掉到可接受范围的关键一步。
    band = alpha.point([255 if 0 < value < 255 else 0 for value in range(256)])
    box = band.getbbox()
    if box is None:
        return rgba.convert("RGBA")          # 没有半透明像素，没什么可去的
    rgb = rgba.convert("RGB")
    crop = rgb.crop(box)
    crop_alpha = alpha.crop(box)

    channels = []
    for channel, bg_value in zip(crop.split(), bg_color):
        expr = ("convert((c * 255 - (255 - a) * %d) / max(a, 1), 'L')" % int(bg_value))
        channels.append(ImageMath.unsafe_eval(expr, c=channel, a=crop_alpha))
    fixed = Image.merge("RGB", channels)

    blended = Image.blend(crop, fixed, ratio)
    # 框里也可能夹着全透明的像素（比如角色中间的洞）：除以 max(a,1)=1 会算出负数被截成 0。
    # 颜色虽然看不见，但有些引擎在边缘做双线性采样时会读到透明像素的 RGB，别去动它。
    solid = crop_alpha.point([255 if value > 0 else 0 for value in range(256)])
    patched = Image.composite(blended, crop, solid)

    out = rgb.copy()
    out.paste(patched, box)
    return out.convert("RGBA")