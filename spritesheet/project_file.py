"""Single-file SpriteSheetTool project format.

The format is intentionally not an archive.  It is a small binary container
with a JSON manifest and length-delimited PNG sections, similar in spirit to
an editor document with embedded assets.

**文件布局（v2，2026-09 起）**：为了让资源管理器能直接显示工程内容缩略图，
``.sstool`` 文件以一张**合法 PNG** 开头（就是工程里存的那张带角标的缩略图），
容器数据紧接在 PNG 的 IEND 之后：

    [ PNG 图像数据 … IEND ] [ MAGIC + header + manifest + sections ]

系统图片解码器读到 IEND 即停，尾部数据被忽略；本程序从 PNG 结束处开始解析容器。
旧版（v1，容器在最前）仍可正常读取，只是那种文件没有可显示的缩略图。
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

from PyQt6.QtCore import QByteArray, QBuffer, QIODevice
from PyQt6.QtGui import QPixmap

MAGIC = b"SSTOOL\x00\x01"
VERSION = 1
_HEADER = struct.Struct("<8sIQI")  # magic, version, manifest length, section count
_SECTION = struct.Struct("<HQ")       # name length, payload length
MAX_MANIFEST = 64 * 1024 * 1024
MAX_SECTION = 2 * 1024 * 1024 * 1024

# PNG 头部魔数；缩略图 section 的名字（位于文件最前的那张图）
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
THUMB_SECTION = "thumbnail.png"


def png_image_end(data: bytes) -> int | None:
    """返回 PNG 数据的结束偏移（IEND 之后）；不是合法 PNG 时返回 None。

    按块结构逐个跳过，而不是搜索 ``IEND`` 字面量 —— 后者可能出现在块数据里。
    """
    if not data.startswith(PNG_SIGNATURE):
        return None
    off = len(PNG_SIGNATURE)
    total = len(data)
    while off + 12 <= total:
        length = struct.unpack_from(">I", data, off)[0]
        ctype = data[off + 4:off + 8]
        off += 12 + length            # 长度(4) + 类型(4) + 数据 + CRC(4)
        if ctype == b"IEND":
            return off if off <= total else None
        if length > MAX_SECTION:      # 明显异常，避免无界循环
            return None
    return None


def write_project(path: str | Path, manifest: dict, sections: dict[str, bytes]) -> None:
    """Write a complete project atomically as one binary .sstool file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # 缩略图提到文件最前（成为 .sstool 的"脸"），避免在容器里重复存一份
    sections = dict(sections)
    leading_png = sections.pop(THUMB_SECTION, b"")
    if leading_png and png_image_end(leading_png) is None:
        # 不是合法 PNG：放回容器，退回「无前置缩略图」的旧布局
        sections[THUMB_SECTION] = leading_png
        leading_png = b""
    manifest_bytes = json.dumps(
        manifest, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    if len(manifest_bytes) > MAX_MANIFEST:
        raise ValueError("工程清单过大")
    temp = target.with_name(target.name + ".tmp")
    with temp.open("wb") as fh:
        # ① 前置图片：让 .sstool 本身是一张合法 PNG（资源管理器缩略图用）
        if leading_png:
            fh.write(leading_png)
        # ② 容器正文
        fh.write(_HEADER.pack(MAGIC, VERSION, len(manifest_bytes), len(sections)))
        fh.write(manifest_bytes)
        for name, payload in sections.items():
            name_bytes = str(name).encode("utf-8")
            if len(name_bytes) > 65535:
                raise ValueError("工程资源名称过长")
            if len(payload) > MAX_SECTION:
                raise ValueError(f"工程资源过大：{name}")
            fh.write(_SECTION.pack(len(name_bytes), len(payload)))
            fh.write(name_bytes)
            fh.write(payload)
    temp.replace(target)


def read_project(path: str | Path) -> tuple[dict, dict[str, bytes]]:
    """Read and validate a single-file project.

    兼容两种布局：v2（文件以合法 PNG 开头，容器在 IEND 之后）与
    v1（容器在最前）。前置图片会在读取时补回 ``sections["thumbnail.png"]``，
    让上层看到的 section 集合与写入前一致。
    """
    source = Path(path)
    data = source.read_bytes()
    offset = png_image_end(data) or 0        # 前置 PNG 的结束位置（无前置图则为 0）
    leading_png = data[:offset]
    with source.open("rb") as fh:
        fh.seek(offset)
        header = fh.read(_HEADER.size)
        if len(header) != _HEADER.size:
            raise ValueError("工程文件不完整")
        magic, version, manifest_len, section_count = _HEADER.unpack(header)
        if magic != MAGIC:
            raise ValueError("不是有效的 SSTOOL 工程文件")
        if version > VERSION:
            raise ValueError(f"工程文件版本过新：{version}")
        if manifest_len > MAX_MANIFEST:
            raise ValueError("工程清单过大")
        try:
            manifest = json.loads(fh.read(manifest_len).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("工程清单损坏") from exc
        if not isinstance(manifest, dict):
            raise ValueError("工程清单格式错误")
        sections: dict[str, bytes] = {}
        for _ in range(section_count):
            raw = fh.read(_SECTION.size)
            if len(raw) != _SECTION.size:
                raise ValueError("工程资源索引损坏")
            name_len, payload_len = _SECTION.unpack(raw)
            if payload_len > MAX_SECTION:
                raise ValueError("工程资源过大")
            name_raw = fh.read(name_len)
            payload = fh.read(payload_len)
            if len(name_raw) != name_len or len(payload) != payload_len:
                raise ValueError("工程资源数据不完整")
            name = name_raw.decode("utf-8")
            if name in sections:
                raise ValueError(f"工程资源重复：{name}")
            sections[name] = payload
    if leading_png and THUMB_SECTION not in sections:
        sections[THUMB_SECTION] = leading_png      # 前置图即缩略图，补回 section
    return manifest, sections


# ---------------- QPixmap / JSON 片段辅助 ----------------
# 原 main_window.py 顶部的工程序列化工具函数（按 SPLIT_GUIDE §5.3 归到本模块）。
def _rect_from_dict(d: dict) -> tuple[int, int, int, int]:
    """把 JSON 里的 rect 字典转成 (x, y, w, h) 元组。"""
    return (int(d.get("x", 0)), int(d.get("y", 0)),
            int(d.get("width", d.get("w", 0))), int(d.get("height", d.get("h", 0))))


def _pixmap_bytes(pm: QPixmap | None) -> bytes:
    if pm is None or pm.isNull():
        return b""
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    pm.save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def _pixmap_from_bytes(data: bytes) -> QPixmap:
    pm = QPixmap()
    if data:
        pm.loadFromData(data, "PNG")
    return pm
