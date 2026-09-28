# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""导入精灵图 JSON：把导出的 JSON 还原成软件工程。

支持四种格式（自动识别，无需用户选择）：
- 本工具格式（spritesheet-animation）：**完整还原**（合成帧 parts、fps、loop、
  锚点、画布留边 padding、地面线、动作类型与移动参数、动作配色、以及各帧在画布
  中的落点），是唯一的无损往返格式；
- TexturePacker JSON-Hash / JSON-Array：还原图块、动作分组、逐帧在画布中的落点
  （由 spriteSourceSize/sourceSize 反推），fps/loop/锚点/地面等取默认值；
- Aseprite JSON-Array：还原图块、动作分组、fps（由 duration 反推）、loop（由
  direction 推）、逐帧落点（由 sourceSize 反推）；锚点取默认值。

图片由 JSON 自身引用的文件名在同目录查找，因此拖入单个 JSON 即可。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

FMT_NATIVE = "native"
FMT_TP_HASH = "tp_hash"
FMT_TP_ARRAY = "tp_array"
FMT_ASEPRITE = "aseprite"
FMT_DOUBAO = "doubao"

FORMAT_LABELS = {
    FMT_NATIVE: "本工具格式",
    FMT_TP_HASH: "TexturePacker JSON-Hash",
    FMT_TP_ARRAY: "TexturePacker JSON-Array",
    FMT_ASEPRITE: "Aseprite JSON-Array",
    FMT_DOUBAO: "豆包视频关键帧格式",
}

DEFAULT_FPS = 10


@dataclass
class ImportedPart:
    """帧中的一个图块部件。"""
    rect: tuple[int, int, int, int]      # 图集中的位置与尺寸
    pos: tuple[int, int]                 # 相对帧左上角的偏移
    origin: tuple[int, int] | None = None  # 元素逻辑坐标（元素层关系，可选）


@dataclass
class ImportedFrame:
    parts: list[ImportedPart]
    duration_ms: int = 100
    # 该帧内容在「目标画布」中的左上角。本工具格式来自 frames[].offset；
    # TexturePacker / Aseprite 的帧以 sourceSize 原点为原点（内容偏移已体现在
    # 部件的 pos 里），因此为 (0, 0)。
    canvas_pos: tuple[int, int] | None = None
    # 目标画布尺寸：本工具格式的 animations[].canvas，
    # TexturePacker / Aseprite 的 sourceSize。用于还原画布留边与帧间相对位置。
    canvas_size: tuple[int, int] | None = None


@dataclass
class ImportedAction:
    name: str
    frames: list[ImportedFrame] = field(default_factory=list)
    fps: int = DEFAULT_FPS
    loop: str = "loop"
    anchor: tuple[float, float] = (0.5, 0.5)
    canvas: tuple[int, int] | None = None
    # ---- 以下字段仅「本工具格式」提供（TP/Aseprite 不含这些编辑语义）----
    padding: int | None = None
    ground: tuple[bool, int] | None = None
    action_type: str = "static"
    move: tuple[str, int] | None = None
    color: str | None = None


@dataclass
class ImportedProject:
    json_path: Path
    atlas_path: Path
    actions: list[ImportedAction]
    fmt: str
    warnings: list[str] = field(default_factory=list)
    # 元素表：图块位置 -> 元素逻辑坐标（用于还原合成帧的拼接间距）
    elements: list[dict] = field(default_factory=list)

    @property
    def format_label(self) -> str:
        return FORMAT_LABELS.get(self.fmt, self.fmt)


class ImportError_(Exception):
    """导入失败（结构无法识别或缺少图片）。"""


def detect_format(data: dict) -> str:
    """按结构特征识别 JSON 格式。"""
    if isinstance(data.get("animations"), list) and isinstance(data.get("atlas"), dict):
        return FMT_NATIVE
    # 顶层直接是数组（某些工具输出 [entry, entry, ...]）
    if isinstance(data, list):
        return FMT_TP_ARRAY
    frames = data.get("frames")
    meta = data.get("meta") or {}
    if frames is None:
        # 兼容：顶层有 Textures/frames 等变体键名
        for alt in ("Textures", "textures", "sprites", "regions"):
            if isinstance(data.get(alt), (list, dict)):
                frames = data[alt]
                data["frames"] = frames  # 写回，让后续 parser 读到
                break
    if frames is None:
        raise ImportError_("JSON 里没有 frames / animations 字段，无法识别格式。")
    if isinstance(meta.get("frameTags"), list):
        return FMT_ASEPRITE
    if isinstance(frames, dict):
        return FMT_TP_HASH
    if isinstance(frames, list):
        # 豆包视频关键帧工具：frames 平铺，entry 本身就是 {name,x,y,width,height,duration}
        # （没有嵌套 frame/rect/region，也没有 filename 字段）
        if frames and isinstance(frames[0], dict):
            e0 = frames[0]
            if (not e0.get("frame") and not e0.get("rect") and not e0.get("bounds")
                and not e0.get("region") and not e0.get("filename")
                and not e0.get("spriteSourceSize")
                and ("x" in e0 or "width" in e0 or "w" in e0)):
                return FMT_DOUBAO
        return FMT_TP_ARRAY
    raise ImportError_("JSON 结构无法识别。")


def _rect(d: dict, keys=("x", "y", "width", "height")) -> tuple[int, int, int, int]:
    return (int(d[keys[0]]), int(d[keys[1]]), int(d[keys[2]]), int(d[keys[3]]))


def _wh(d: dict, w_keys=("w", "width", "W"), h_keys=("h", "height", "H")) -> tuple[int, int]:
    """从字典里取宽高，兼容 w/h、width/height、W/H 等字段名。"""
    w = 0
    for k in w_keys:
        if k in d:
            w = int(d[k])
            break
    h = 0
    for k in h_keys:
        if k in d:
            h = int(d[k])
            break
    return w, h


def _xy(d: dict) -> tuple[int, int]:
    return int(d.get("x", d.get("X", 0))), int(d.get("y", d.get("Y", 0)))


# ---------------- 本工具格式 ----------------

def _parse_native(data: dict, base: Path, warnings: list[str]) -> tuple[str, list[ImportedAction], list[dict]]:
    atlas = data.get("atlas") or {}
    image_name = atlas.get("image") or "spritesheet.png"
    actions: list[ImportedAction] = []
    for anim in data.get("animations", []):
        canvas = anim.get("canvas") or {}
        anchor_norm = ((anim.get("anchor") or {}).get("normalized") or {})
        canvas_size = (int(canvas.get("width", 0)), int(canvas.get("height", 0)))
        ground = anim.get("ground") or {}
        move = anim.get("move") or {}
        act = ImportedAction(
            name=str(anim.get("name") or "动作"),
            fps=int(anim.get("fps") or DEFAULT_FPS),
            loop=str(anim.get("loop") or "loop"),
            anchor=(float(anchor_norm.get("x", 0.5)), float(anchor_norm.get("y", 0.5))),
            canvas=canvas_size,
            padding=int(canvas.get("padding") or 0),
            ground=(bool(ground.get("enabled", False)), int(ground.get("offset", 0))),
            action_type=str(anim.get("type") or "static"),
            move=((str(move.get("direction") or "right"), int(move.get("speed", 10)))
                  if move else None),
            color=str(anim.get("color")) if anim.get("color") else None,
        )
        for fr in anim.get("frames", []):
            duration = int(fr.get("duration_ms") or 100)
            off = fr.get("offset") or {}
            canvas_pos = (int(off.get("x", 0)), int(off.get("y", 0)))
            parts: list[ImportedPart] = []
            for part in fr.get("parts") or []:
                r = part.get("rect") or {}
                p = part.get("pos") or {}
                o = part.get("origin")
                parts.append(ImportedPart(
                    rect=_rect(r),
                    pos=(int(p.get("x", 0)), int(p.get("y", 0))),
                    origin=(int(o["x"]), int(o["y"])) if isinstance(o, dict) else None,
                ))
            if not parts:
                # 旧版 JSON（无 parts）：用 rect + offset 视为单部件帧
                r = fr.get("rect")
                if r:
                    off = fr.get("offset") or {}
                    parts.append(ImportedPart(
                        rect=_rect(r),
                        pos=(int(off.get("x", 0)), int(off.get("y", 0))),
                    ))
            if parts:
                act.frames.append(ImportedFrame(
                    parts=parts,
                    duration_ms=duration,
                    canvas_pos=canvas_pos,
                    canvas_size=canvas_size or None,
                ))
        if act.frames:
            actions.append(act)
    if not actions:
        raise ImportError_("JSON 里没有可导入的帧。")
    elements = data.get("elements") or []
    return image_name, actions, elements


# ---------------- TexturePacker / Aseprite ----------------

def _entry_to_frame(entry: dict) -> ImportedFrame | None:
    # 帧矩形：标准 TP 用 entry.frame，其他工具可能用 rect/bounds/region，
    # 甚至 entry 本身就是 {x,y,w,h}
    fr = entry.get("frame") or entry.get("rect") or entry.get("bounds") \
        or entry.get("region") or {}
    if not fr or not isinstance(fr, dict):
        # entry 本身可能就是矩形
        if any(k in entry for k in ("w", "h", "width", "height")):
            fr = entry
        else:
            return None
    fx, fy = _xy(fr)
    fw, fh = _wh(fr)
    if fw <= 0 or fh <= 0:
        return None
    rect = (fx, fy, fw, fh)
    # 内容在源画布中的偏移（spriteSourceSize）
    sss = entry.get("spriteSourceSize") or entry.get("spriteSource") or {}
    pos = _xy(sss) if isinstance(sss, dict) else (0, 0)
    size = entry.get("sourceSize") or entry.get("source") or {}
    canvas_size = None
    if isinstance(size, dict):
        cw, ch = _wh(size)
        if cw > 0 and ch > 0:
            canvas_size = (cw, ch)
    duration = entry.get("duration") or entry.get("duration_ms") or 100
    return ImportedFrame(parts=[ImportedPart(rect=rect, pos=pos)],
                         duration_ms=int(duration),
                         canvas_pos=(0, 0),
                         canvas_size=canvas_size)


def _parse_tp(data: dict, warnings: list[str], is_hash: bool) -> tuple[str, list[ImportedAction], list[dict]]:
    meta = data.get("meta") or {}
    image_name = meta.get("image") or "spritesheet.png"
    frames = data.get("frames")
    entries: list[tuple[str, dict]] = []
    if is_hash:
        for name, entry in (frames or {}).items():
            entries.append((name, entry))
    else:
        for entry in (frames or []):
            entries.append((entry.get("filename") or "frame", entry))

    # 按「名字去掉末尾 _数字」分组为动作
    grouped: dict[str, list[dict]] = {}
    order: list[str] = []
    for name, entry in entries:
        stem = name
        if "_" in name:
            head, _, tail = name.rpartition("_")
            if tail.isdigit() and head:
                stem = head
        if stem not in grouped:
            grouped[stem] = []
            order.append(stem)
        grouped[stem].append(entry)

    actions: list[ImportedAction] = []
    for stem in order:
        act = ImportedAction(name=stem)
        for entry in grouped[stem]:
            frame = _entry_to_frame(entry)
            if frame:
                act.frames.append(frame)
        if act.frames:
            actions.append(act)
    # 兜底：标准分组没解析出帧时，把所有原始 entry 尝试直接解析后放进一个动作
    if not actions and entries:
        act = ImportedAction(name="动作1")
        for _name, entry in entries:
            frame = _entry_to_frame(entry)
            if frame:
                act.frames.append(frame)
        if act.frames:
            actions.append(act)
    if not actions:
        raise ImportError_("JSON 里没有可导入的帧。")
    warnings.append("TexturePacker 格式不含帧率/循环/锚点信息，已使用默认值（fps=10、loop）。")
    return image_name, actions, []


def _parse_aseprite(data: dict, warnings: list[str]) -> tuple[str, list[ImportedAction], list[dict]]:
    meta = data.get("meta") or {}
    image_name = meta.get("image") or "spritesheet.png"
    frames_raw = data.get("frames") or []
    frames = [_entry_to_frame(e) for e in frames_raw]
    frames = [f for f in frames if f is not None]
    tags = meta.get("frameTags") or []
    actions: list[ImportedAction] = []
    if tags:
        for tag in tags:
            frm = int(tag.get("from", 0))
            to = int(tag.get("to", frm))
            seg = frames[frm: to + 1]
            if not seg:
                continue
            duration = seg[0].duration_ms or 100
            actions.append(ImportedAction(
                name=str(tag.get("name") or "动作"),
                fps=max(1, round(1000 / max(1, duration))),
                loop="pingpong" if tag.get("direction") == "pingpong" else "loop",
                frames=list(seg),
            ))
    elif frames:
        actions.append(ImportedAction(name="动作1", frames=frames))
    if not actions:
        raise ImportError_("JSON 里没有可导入的帧。")
    warnings.append("Aseprite 格式不含锚点信息，已使用默认值（居中）。")
    return image_name, actions, []


# ---------------- 豆包视频关键帧工具 ----------------

def _parse_doubao(data: dict, warnings: list[str]) -> tuple[str, list[ImportedAction], list[dict]]:
    """豆包视频关键帧工具导出的平铺格式：
    {"image":..., "size":{width,height}, "fps":...,
     "frames":[{name,x,y,width,height,duration}, ...]}
    所有帧归为一个动作；每帧 duration 直接保留，fps 取顶层（round 到 int）。
    """
    image_name = data.get("image") or "spritesheet.png"
    frames_raw = data.get("frames") or []
    frames: list[ImportedFrame] = []
    for entry in frames_raw:
        if not isinstance(entry, dict):
            continue
        fx = int(entry.get("x", entry.get("X", 0)))
        fy = int(entry.get("y", entry.get("Y", 0)))
        fw = int(entry.get("width", entry.get("w", entry.get("W", 0))))
        fh = int(entry.get("height", entry.get("h", entry.get("H", 0))))
        if fw <= 0 or fh <= 0:
            continue
        duration = int(entry.get("duration") or entry.get("duration_ms") or 100)
        frames.append(ImportedFrame(
            parts=[ImportedPart(rect=(fx, fy, fw, fh), pos=(0, 0))],
            duration_ms=duration,
            canvas_pos=(0, 0),
            canvas_size=(fw, fh),
        ))
    if not frames:
        raise ImportError_("JSON 里没有可导入的帧。")
    fps_raw = data.get("fps")
    if fps_raw:
        fps_val = max(1, round(float(fps_raw)))
    else:
        fps_val = max(1, round(1000 / max(1, frames[0].duration_ms)))
    act = ImportedAction(name="动作1", frames=frames, fps=fps_val)
    warnings.append(f"豆包视频关键帧格式：共 {len(frames)} 帧，已归为一个动作（fps={fps_val}）。")
    return image_name, [act], []


def load_project(json_path: str | Path) -> ImportedProject:
    """读取 JSON（可选自动找同目录图片）并解析为工程结构。"""
    path = Path(json_path)
    if not path.exists():
        raise ImportError_(f"文件不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))  # utf-8-sig 容忍 BOM
    except (OSError, json.JSONDecodeError) as exc:
        raise ImportError_(f"JSON 解析失败：{exc}") from exc
    # 顶层直接是数组：包成 {"frames": [...]}
    if isinstance(data, list):
        data = {"frames": data}
    if not isinstance(data, dict):
        raise ImportError_("JSON 顶层不是对象，无法识别。")

    fmt = detect_format(data)
    warnings: list[str] = []
    elements: list[dict] = []
    if fmt == FMT_NATIVE:
        image_name, actions, elements = _parse_native(data, path.parent, warnings)
    elif fmt == FMT_TP_HASH:
        image_name, actions, _ = _parse_tp(data, warnings, is_hash=True)
    elif fmt == FMT_TP_ARRAY:
        image_name, actions, _ = _parse_tp(data, warnings, is_hash=False)
    elif fmt == FMT_DOUBAO:
        image_name, actions, _ = _parse_doubao(data, warnings)
    else:
        image_name, actions, _ = _parse_aseprite(data, warnings)

    # 图片按 JSON 里引用的文件名在同目录查找
    atlas_path = path.parent / image_name
    if not atlas_path.exists():
        # 退化：找同目录同名不同扩展，或任意 png
        candidates = [p for p in path.parent.glob("*.png") if p.is_file()]
        if len(candidates) == 1:
            atlas_path = candidates[0]
            warnings.append(f"未找到 {image_name}，已改用同目录的 {atlas_path.name}。")
        else:
            raise ImportError_(
                f"找不到配套图片：{atlas_path}\n"
                "请把导出的 PNG 与 JSON 放在同一目录（文件名需与 JSON 中记录的一致）。"
            )
    return ImportedProject(json_path=path, atlas_path=atlas_path, actions=actions,
                           fmt=fmt, warnings=warnings, elements=elements)
