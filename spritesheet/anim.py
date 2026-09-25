#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""动图（GIF / WebP）判定与解帧。

**为什么判定和解帧要用两套东西**：
- 判定用 Qt 的 ``QImageReader``（``imageCount`` / ``supportsAnimation``）：它只读文件头、
  不真正解码，拖进来判一下几乎不花时间。
- 解帧用 Pillow：动图里大多数帧是"只改局部"的（GIF 的 disposal、WebP 的 ANMF 局部矩形），
  Qt 的逐帧接口给的是**原始局部帧**，直接拿来用会缺背景/上一层的内容；Pillow 实测按规则
  合成好了（造了"方块逐帧移动 + 只存变化区域"的动图验证过）。Pillow 本来就是本项目的
  导出依赖，所以这里**零新增依赖**。

逐帧时长：GIF 从 ``im.info['duration']`` 直接读；WebP 在 Pillow 12.3 上读出来是 ``None``
（实测），只能自己解 RIFF 里的 ANMF 块 —— 文件里本来是有的，不读就白丢了。
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QImage, QImageReader, QPixmap

# 只认这两种后缀：用户要的就是 GIF / 动图 WebP。PNG 的动图（APNG）不在本轮范围内，
# 它会照旧走"当静态单图"的老路（不弹询问、不误判）。
ANIM_EXT = {".gif", ".webp"}

# 读不到逐帧时长时的兜底（Pillow 对个别文件也会给 None）
DEFAULT_FRAME_MS = 100

# 护栏阈值：超过就提醒一句（用户选"继续"仍然能做，只是慢/吃内存）。
# 200 帧这个数来自"帧数多到没法逐帧看"；总像素按 **60M**（RGBA 约 240MB）算 ——
# 实测 80 帧 × 1024² 铺成 4096 宽的行是 4096×20400 的画布、约 320MB，这个量级必须提醒。
BIG_FRAME_COUNT = 200
BIG_TOTAL_PIXELS = 60_000_000


@dataclass
class Animation:
    """一张动图解出来的东西：全部帧 + 每帧时长（毫秒）。"""

    frames: list[QPixmap]
    durations: list[int]
    width: int = 0
    height: int = 0
    fmt: str = ""

    @property
    def count(self) -> int:
        return len(self.frames)


def is_animated_file(path: str | Path) -> bool:
    """这个文件是不是**真动图**（多于 1 帧）。

    只看后缀属于 ANIM_EXT，再问 Qt 的帧数：单帧 GIF/WebP 返回 False，调用方就不会弹
    "要不要转序列帧"的询问 —— 静态图跟以前一模一样，不打扰。
    """
    p = Path(path)
    if p.suffix.lower() not in ANIM_EXT:
        return False
    try:
        reader = QImageReader(str(p))
        if not reader.canRead():
            return False
        if not reader.supportsAnimation():
            return False
        return int(reader.imageCount()) > 1
    except Exception:  # noqa: BLE001 - 判定失败就当静态图，绝不因为一个怪文件崩溃
        return False


def size_warning(count: int, width: int, height: int) -> str | None:
    """帧数/体积护栏：需要提醒时返回一句人话，否则 None。"""
    if count <= 1:
        return None
    total = int(count) * int(width) * int(height)
    if count > BIG_FRAME_COUNT:
        return (f"这个动图有 {count} 帧，铺到画布上会比较吃内存（可能几百 MB），"
                f"处理起来也会慢一些。")
    if total > BIG_TOTAL_PIXELS:
        mb = total * 4 / 1024 / 1024
        return (f"这个动图 {count} 帧 × {width}×{height}，全部铺开会占用约 {mb:.0f} MB 内存，"
                f"处理起来会慢一些。")
    return None


def read_animation(path: str | Path) -> Animation | None:
    """解出全部帧（已按动图规则合成）与逐帧时长；解不出来返回 None。

    返回 None 的两种情况：没装 Pillow、文件损坏/不是动图 —— 调用方回退到"取第一帧当单图"，
    跟改动前的行为一致。
    """
    p = Path(path)
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001 - Pillow 是可选依赖（导出 GIF 时才用）
        return None
    webp_durs: list[int] = []
    try:
        with Image.open(str(p)) as im:
            count = int(getattr(im, "n_frames", 1) or 1)
            if count <= 1:
                return None
            fmt = str(getattr(im, "format", "") or "")
            if fmt.upper() == "WEBP":
                # Pillow 不给 WebP 的逐帧时长，自己从文件里解一遍
                webp_durs = _webp_durations(p)
            frames: list[QPixmap] = []
            durations: list[int] = []
            for index in range(count):
                # seek 是**顺序**走的：GIF/WebP 的局部帧要靠上一步的状态才能合成出
                # 完整画面，跳着读会得到缺内容的帧
                im.seek(index)
                frames.append(pil_to_pixmap(im.convert("RGBA")))
                durations.append(_frame_ms(im, index, webp_durs))
            width, height = im.size
    except Exception:  # noqa: BLE001 - 损坏的文件不能让整个窗口崩掉
        return None
    if not frames:
        return None
    return Animation(frames=frames, durations=durations,
                     width=int(width), height=int(height), fmt=fmt.upper())


def pil_to_pixmap(rgba) -> QPixmap:
    """Pillow 的 RGBA 图 → QPixmap。

    **必须 copy()**：``QImage(data, ...)`` 不持有那段 bytes，出作用域就被回收，
    不拷贝的话画面上会读到已释放的内存（花屏/崩溃，且不一定立刻复现）。
    """
    data = rgba.tobytes("raw", "RGBA")
    img = QImage(data, rgba.width, rgba.height, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(img.copy())


def pixmap_to_pil(pixmap: QPixmap):
    """QPixmap → Pillow RGBA（``pil_to_pixmap`` 的反向）。

    （从 ``ui/key_panel.py`` 搬来：动图文档重放"抠背景"也要用它，两处各留一份迟早走样。
    ``ui/export_dialog.py`` 里那份私有实现**不合并** —— 那是原来就定下的"两个界面层
    各留一份、互不依赖"，这里只统一"模型层与界面层"之间那一份。）
    """
    image = pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = image.bits()
    ptr.setsize(image.sizeInBytes())
    from PIL import Image
    return Image.frombytes("RGBA", (image.width(), image.height()), bytes(ptr))


def _frame_ms(im, index: int, webp_durs: list[int]) -> int:
    """第 index 帧的时长（毫秒）：GIF 读 info，WebP 用解出来的 ANMF，都没有就兜底。"""
    raw = im.info.get("duration")
    try:
        if raw is not None:
            return max(1, int(raw))
    except (TypeError, ValueError):
        pass
    if index < len(webp_durs):
        return max(1, int(webp_durs[index]))
    return DEFAULT_FRAME_MS


def _webp_durations(path: Path) -> list[int]:
    """解 WebP 的 ANMF 块，取每帧时长（毫秒）。解不出来返回空列表。

    RIFF 结构：``RIFF <size> WEBP`` 之后是一串 ``<4 字节块名><4 字节小端长度><数据>``，
    长度是奇数时补一个填充字节。ANMF 的数据前 12 字节是 x/2、y/2、w-1、h-1（各 3 字节
    小端），紧接着 3 字节就是这一帧的时长（毫秒）。
    """
    try:
        data = path.read_bytes()
    except OSError:
        return []
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return []
    durs: list[int] = []
    pos = 12
    while pos + 8 <= len(data):
        chunk = data[pos:pos + 4]
        size = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        if chunk == b"ANMF":
            payload = data[pos + 8:pos + 8 + size]
            if len(payload) >= 15:
                durs.append(int.from_bytes(payload[12:15], "little"))
        pos += 8 + size + (size & 1)
    return durs


def plan_frame_offsets(frames: list[QPixmap], start_y: int = 0,
                       max_row: int = 4096, gap: int = 16
                       ) -> tuple[list[tuple[int, int]], QSize]:
    """横向排、一行最宽 ``max_row``、超了就换行：算出每张图的偏移和整块画布尺寸。

    为什么是横排换行而不是一列排到底（用户口径："横排一长条超过阈值换到第二行"）：
    竖着排一列，帧一多画布高度就是帧数 × 帧高，几百帧直接爆内存；横排换行能把宽高
    都压在 4096 这个量级里。

    ``start_y`` 是"接在已有内容下面"的起始 y（追加导入时用），单独导入给 0。
    ``gap`` 必须与查看器的 ``SOURCE_GAP`` 是同一个数（那边是拼图时的间距）。
    """
    offsets: list[tuple[int, int]] = []
    x = y = row_h = 0
    width = 0
    for pm in frames:
        if x and x + gap + pm.width() > max_row:
            # 换行：y 落到下一行，x 归零（帧比 max_row 还宽时它就自成一行，不硬切）
            y += row_h + gap
            x = 0
            row_h = 0
        if x:
            x += gap
        offsets.append((x, start_y + y))
        x += pm.width()
        row_h = max(row_h, pm.height())
        width = max(width, x)
    height = start_y + y + row_h if frames else start_y
    return offsets, QSize(max(1, width), max(1, height))


def frames_sheet_size(frames: list[QPixmap], max_row: int, gap: int) -> QSize:
    """按上面的排列规则预演画布要多大（护栏和"会不会太大"的提醒共用同一套算法）。"""
    return plan_frame_offsets(frames, 0, max_row, gap)[1]


def fps_and_overrides(durations: list[int]) -> tuple[int, dict[int, int]]:
    """逐帧时长 → (动作 fps, 需要**逐帧覆盖**的表)。

    为什么几乎每帧都写覆盖：动作的缺省时长是 ``round(1000 / fps)``，fps 是整数，反推
    不出 80ms 这种值（12.5fps 只能取 13 → 77ms）。用户要的是"把原动图的逐帧时长带上"，
    所以 fps 只用来给"没覆盖的帧"兜底，真正的时长一帧一条写清楚 —— 导出 GIF 时读的就是
    这个表，原样还原。
    """
    if not durations:
        return 10, {}
    counts: dict[int, int] = {}
    for ms in durations:
        counts[int(ms)] = counts.get(int(ms), 0) + 1
    # 最常见的那个时长用来定 fps（同长动图看起来自然，也少一堆"已改时长"标记）
    base_ms = max(counts.items(), key=lambda kv: (kv[1], -kv[0]))[0]
    fps = max(1, min(60, round(1000 / max(1, base_ms))))
    default_ms = max(1, round(1000 / fps))
    overrides = {i: max(1, int(ms)) for i, ms in enumerate(durations)
                 if max(1, int(ms)) != default_ms}
    return fps, overrides