"""Single-file SpriteSheetTool project format.

The format is intentionally not an archive.  It is a small binary container
with a JSON manifest and length-delimited PNG sections, similar in spirit to
an editor document with embedded assets.
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


def write_project(path: str | Path, manifest: dict, sections: dict[str, bytes]) -> None:
    """Write a complete project atomically as one binary .sstool file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    manifest_bytes = json.dumps(
        manifest, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    if len(manifest_bytes) > MAX_MANIFEST:
        raise ValueError("工程清单过大")
    temp = target.with_name(target.name + ".tmp")
    with temp.open("wb") as fh:
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
    """Read and validate a single-file project."""
    source = Path(path)
    with source.open("rb") as fh:
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
