# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""SpriteSheetTool MCP Server（headless，stdio 传输）。

让 Claude Desktop / Cursor 等 MCP 客户端直接读写精灵图工程 —— 导入精灵图 JSON、
读 .sstool 工程元信息、列出/细看动作、导出引擎 JSON、取任意帧的 PNG 预览。
全程零 GUI：不创建 QApplication、不 import main_window / preview / image_viewer，
图片一律用 Pillow 编解码。

启动方式：
    python -m spritesheet.mcp_server     # 开发环境（MCP 客户端按此配置）
    SpriteSheetTool.exe --mcp            # 打包版（见 main.py 的 --mcp 分支）

设计约束：
- 本模块可以安全地被 GUI 侧 import（「MCP 设置」对话框读 TOOL_DEFS 显示工具列表），
  此时不启动服务器、不碰 stdin/stdout；
- TOOL_DEFS 是工具的**单一注册表**：MCP 注册与对话框显示都从这里来，两边永远一致；
- 所有工具参数里的路径都是**本地绝对路径**（Windows 盘符路径，如 C:\\...\\cat.sstool），
  相对路径也接受但会先解析为绝对路径再处理。

支持范围：
- 普通帧工程和动图文档工程均可读取；动图文档的可序列化操作链可继续追加和重放。
- 帧含自由四边形透视变换（quad）时，读取、编辑、PNG 和引擎导出都走 NumPy/Pillow
  的 headless 透视渲染。
"""
from __future__ import annotations

import base64
import json
import math
import shutil
import sys
import time
import numbers
from pathlib import Path

from PIL import Image, ImageDraw
from mcp.server.fastmcp import FastMCP

# 精灵图工程的合成大图本来就是超大合法素材（如 3596×35588 ≈ 1.3 亿像素，
# 远超 Pillow 默认 8947 万像素的 DecompressionBomb 阈值）。这是本工具的
# 正常使用场景而非 DOS 攻击，解除上限避免每次导出都打警告/未来转异常。
Image.MAX_IMAGE_PIXELS = None

from spritesheet import __app_name__, __version__
from spritesheet import keying, keying_modes
from spritesheet.importer import load_project
from spritesheet.project_file import (  # noqa: E501
    png_bytes, png_from_bytes, read_project, write_project,
)
from spritesheet.ui import exporters

# ---------------------------------------------------------------------------
# 服务器实例
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "spritesheet_mcp",
    instructions=(
        "SpriteSheetTool 精灵图工程读写服务器。典型流程：\n"
        "1) 用户给了别人的精灵图 JSON —— 用 import_spritesheet_json 先看它是什么格式、"
        "有哪些动作；\n"
        "2) 用户给了 .sstool 工程 —— 用 read_sstool_project / list_actions 看结构，"
        "get_action_detail 看某个动作的逐帧细节；\n"
        "3) 要看某一帧长什么样 —— extract_frame_png 返回 PNG 的 base64；\n"
        "4) 要转给游戏引擎 —— export_to_engine_json 导出 TexturePacker / Aseprite JSON"
        "（连同配套图集 PNG 一起写出）。\n"
        "创建新工程的固定流程：先调用 create_empty_project（或 create_project_from_json），"
        "再调用 create_action，最后用 add_frame / add_composite_frame 添加帧；"
        "不要手写 .sstool 清单，也不要填写 frame_layouts_base 等内部字段，"
        "这些字段由服务器自动维护。写入后可用 validate_project 检查，再用 read_sstool_project / list_actions 读取。\n"
        "所有路径参数用本地绝对路径；工程文件用 read_project 读取，与软件内的工程完全一致。"
    ),
)
# FastMCP currently falls back to the installed MCP SDK version when its
# low-level server has no explicit version (for example, it would report
# ``1.30.0`` here).  Clients should see the application's version instead.
mcp._mcp_server.version = __version__

# ---------------------------------------------------------------------------
# 工具注册表（名称, 描述首行）——「MCP 设置」对话框显示用，与下面的 @mcp.tool 一致
# ---------------------------------------------------------------------------
TOOL_DEFS = [
    ("import_spritesheet_json",
     "导入精灵图 JSON（自动识别本工具/TexturePacker Hash/Aseprite/豆包格式），返回动作清单与警告"),
    ("read_sstool_project",
     "读取 .sstool 工程元信息：动作结构、锚点、画布、padding、地面线、动作类型、资源段清单"),
    ("list_actions",
     "列出 .sstool 工程全部动作：名称、帧数、fps、loop、画布尺寸"),
    ("get_action_detail",
     "查看指定动作的逐帧详情：图集 rect、逐帧偏移、画布落点、时长、变换参数"),
    ("export_to_engine_json",
     "把 .sstool 工程导出为引擎 JSON（tp_hash / tp_array / aseprite_array），同时写出配套图集 PNG"),
    ("extract_frame_png",
     "合成指定动作的指定帧画面，返回 PNG 的 base64 字符串（AI 可直接看图）"),
    ("project_overview",
     "工程总览：全部动作清单 + 各动作首帧拼成一张总览图（base64），一眼看清整个工程"),
    ("extract_action_frames_png",
     "一次提取动作的全部帧：逐帧 base64 列表 + 网格总览图（看整条动画序列）"),
    ("validate_project",
     "工程健康检查：空动作、缺帧图、孤儿引用、rect 越界等，返回 error/warning 清单"),
    ("find_similar_frames",
     "相似帧分组：按每帧用到的元素组合分组（与软件相似帧彩块同口径），找重复帧候选"),
    ("trace_motion",
     "逐帧运动轨迹：内容中心点序列 + 相邻帧位移，判断动作循环是否平滑"),
    ("update_action_meta",
     "修改动作属性并写回工程（fps/loop/padding/锚点/类型/移动参数/重命名，写前自动备份）"),
    ("delete_action",
     "删除动作（含其全部帧图）并写回工程，写前自动备份"),
    ("remove_frames",
     "删除动作的若干帧并写回工程（同软件删帧口径重排，写前自动备份；含合成帧的动作拒绝）"),
    ("create_empty_project",
     "创建新的空白 .sstool 工程；下一步调用 create_action，再用 add_frame 或 add_composite_frame 添加帧"),
    ("create_project_from_json",
     "把外部精灵图 JSON 转换为可打开的 .sstool 工程；转换后可直接 list_actions / extract_frame_png / export_project_asset"),
    ("create_action",
     "在工程中创建一个新动作；不要手写 frame_layouts_base 等内部字段，创建后继续调用 add_frame 添加帧"),
    ("add_frame",
     "从本地 PNG 添加或插入动作帧，并同步写入合成图集"),
    ("duplicate_frame",
     "复制动作中的指定帧并插入到目标位置"),
    ("reorder_frames",
     "按给定顺序重排动作帧并同步所有逐帧字段"),
    ("update_frame",
     "修改逐帧时长、偏移、旋转、镜像、透视和变换模式"),
    ("add_source_image",
     "向工程追加一张源图并更新合成图集"),
    ("remove_source_image",
     "从工程源图列表移除一张源图（不删除仍被帧引用的合成图集像素）"),
    ("move_source_image",
     "调整工程源图顺序并同步源图位置元数据"),
    ("add_composite_frame",
     "用多张本地图片按位置合成一个动作帧；先 create_action，再调用此工具添加多部件帧"),
    ("key_project_background",
     "对普通序列帧工程的图片或帧执行基础色键/卡通 profile 抠图并自动备份；先确认工程已有图像"),
    ("export_project_asset",
     "导出 PNG、GIF、本工具 JSON、.sstool 或逐动作 PNG 等完整资源；kind 必须使用文档列出的枚举"),
    ("read_animation_document",
     "读取动图文档工程的帧数、时长和操作链"),
    ("extract_animation_frame_png",
     "提取动图文档指定帧，包含已保存的翻转、旋转、画笔和抠图操作"),
    ("update_animation_document",
     "向动图文档追加翻转、旋转、画笔或抠图操作并自动备份；先 read_animation_document 确认是动图文档"),
    ("auto_align_action",
     "自动算出逐帧偏移并写回，让整条动画落点对齐（均分格/精确/估算三条路，默认不跑估算）"),
    ("draw_on_frames",
     "在动作的帧上画线或擦除（画笔/橡皮，可指定单帧或全部帧，写前自动备份）"),
    ("decimate_action_frames",
     "按相似度智能抽帧（keep_3_5 / keep_1_2 / keep_1_4 / dedupe 四档），保留帧落点自动补偿不位移"),
    ("transform_action_frames",
     "把旋转/镜像一次套用到动作的多帧或全部帧（软件里一键作用于全部帧的等价物）"),
    ("edit_animation_document",
     "编辑动图文档已保存的操作链：删除某一项或调整顺序"),
]

# ---------------------------------------------------------------------------
# 内部辅助：工程读取与校验
# ---------------------------------------------------------------------------
def _read_checked(project_path: str) -> tuple[dict, dict[str, bytes]]:
    """read_project + 基础校验：返回 (manifest, sections)。

    - 文件不存在 / 不是有效工程 → ValueError（带可行动提示）；
    - 动图文档工程（anim_doc）→ ValueError 明确说明暂不支持。
    """
    path = Path(project_path)
    if not path.exists():
        raise ValueError(f"工程文件不存在：{path}。请检查路径是否为本地绝对路径。")
    try:
        manifest, sections = read_project(path)
    except Exception as exc:  # noqa: BLE001 - read_project 抛 ValueError，这里统一成友好消息
        raise ValueError(f"无法读取工程文件 {path}：{exc}") from exc
    if manifest.get("anim_doc"):
        raise ValueError(
            "该工程是「动图文档」模式（anim_doc），MCP 目前只支持普通序列帧工程。"
            "请先在 SpriteSheetTool 里把动图文档转换为序列帧再保存。"
        )
    return manifest, sections


def _actions_of(manifest: dict) -> list[dict]:
    return manifest.get("actions") or []


def _frame_count(action: dict) -> int:
    """动作的帧数：frame_sections / frame_rects / frame_parts 谁最长听谁的。"""
    for key in ("frame_sections", "frame_rects", "frame_parts"):
        value = action.get(key)
        if value:
            return len(value)
    return 0


def _find_action(manifest: dict, action_name: str) -> dict:
    for action in _actions_of(manifest):
        if (action.get("name") or "") == action_name:
            return action
    names = "、".join(f"「{a.get('name')}」" for a in _actions_of(manifest)) or "（无动作）"
    raise ValueError(f"找不到动作「{action_name}」。当前工程里的动作：{names}")


def _new_action(name: str, *, fps: int = 10, loop_mode: str = "loop",
                padding: int = 0, action_type: str = "static",
                color: str | None = None) -> dict:
    """Create an action dictionary matching MainWindow's native schema."""
    return {
        "name": str(name), "color": color,
        "selected_indices": [], "composite_groups": [],
        "frame_layouts": None, "frame_layouts_base": 0,
        "frame_rects": [], "frame_parts": [], "frame_sections": [],
        "fps": int(fps), "loop_mode": str(loop_mode), "padding": int(padding),
        "ground_enabled": False, "ground_offset": 0,
        "reuse_frame_settings": False, "show_anchor": True,
        "anchor": [0.5, 0.5], "frame_offsets": {}, "frame_durations": {},
        "frame_rotations": {}, "frame_mirror_h": [], "frame_mirror_v": [],
        "frame_rotation_pivots": {}, "frame_quads": {},
        "frame_transform_modes": {}, "ref_frame": 0, "ref_opacity": 1.0,
        "frame_opacity": 1.0, "bg_mode": "auto",
        "action_type": str(action_type), "move_direction": "right", "move_speed": 0,
    }


def _next_section(sections: dict[str, bytes], prefix: str, suffix: str = ".png") -> str:
    index = 0
    while True:
        name = f"{prefix}{index:04d}{suffix}"
        if name not in sections:
            return name
        index += 1


def _load_rgba(path: str | Path) -> Image.Image:
    target = Path(path)
    if not target.exists():
        raise ValueError(f"图片文件不存在：{target}")
    try:
        with Image.open(target) as source:
            return source.convert("RGBA")
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"无法读取图片 {target}：{exc}") from exc


def _combined_image(manifest: dict, sections: dict[str, bytes]) -> Image.Image:
    name = manifest.get("combined_image") or ""
    image = png_from_bytes(sections.get(name, b"")) if name else None
    if image is None:
        raise ValueError("工程缺少有效的 combined_image 合成图")
    return image.convert("RGBA")


def _store_combined(manifest: dict, sections: dict[str, bytes], image: Image.Image) -> None:
    name = manifest.get("combined_image") or "images/combined.png"
    manifest["combined_image"] = name
    sections[name] = png_bytes(image.convert("RGBA"))


def _append_to_combined(manifest: dict, sections: dict[str, bytes], image: Image.Image) -> tuple[int, int, int, int]:
    """Append an image below the existing atlas and return its atlas rect."""
    atlas = _combined_image(manifest, sections)
    x = 0
    y = atlas.height
    width = max(atlas.width, image.width)
    merged = Image.new("RGBA", (width, atlas.height + image.height), (0, 0, 0, 0))
    merged.alpha_composite(atlas, (0, 0))
    merged.alpha_composite(image, (x, y))
    _store_combined(manifest, sections, merged)
    return (x, y, image.width, image.height)


def _copy_frame_fields(action: dict, source: int, target: int) -> None:
    """Copy all per-frame metadata from source to target index."""
    for key in ("frame_offsets", "frame_durations", "frame_rotations",
                "frame_rotation_pivots", "frame_quads", "frame_transform_modes"):
        values = action.get(key) or {}
        if str(source) in values:
            values[str(target)] = values[str(source)]
    for key in ("frame_mirror_h", "frame_mirror_v"):
        values = action.get(key) or []
        if source in values and target not in values:
            values.append(target)


def _insert_index(count: int, index: int | None) -> int:
    if index is None:
        return count
    value = int(index)
    if not 0 <= value <= count:
        raise ValueError(f"插入位置越界：{value}，当前共有 {count} 帧。")
    return value


def _reorder_action(action: dict, order: list[int]) -> None:
    count = _frame_count(action)
    if sorted(int(i) for i in order) != list(range(count)):
        raise ValueError(f"order 必须恰好包含 0 到 {count - 1} 且每项只出现一次。")
    order = [int(i) for i in order]
    for key in ("frame_sections", "frame_rects", "frame_parts"):
        values = action.get(key) or []
        action[key] = [values[i] for i in order]
    old_to_new = {old: new for new, old in enumerate(order)}
    for key in ("frame_offsets", "frame_durations", "frame_rotations",
                "frame_rotation_pivots", "frame_quads", "frame_transform_modes"):
        values = action.get(key) or {}
        action[key] = {str(old_to_new[int(k)]): v for k, v in values.items()
                       if str(k).lstrip("-").isdigit() and int(k) in old_to_new}
    for key in ("frame_mirror_h", "frame_mirror_v"):
        action[key] = [old_to_new[i] for i in action.get(key, []) if i in old_to_new]
    # Explicit imported layouts are one row per output frame.  Reorder those
    # rows together with the frame arrays; composite_groups describe source
    # element membership and therefore keep their element order.
    layouts = action.get("frame_layouts")
    if layouts is not None:
        action["frame_layouts"] = [layouts[i] for i in order if i < len(layouts)]
        action["frame_layouts_base"] = min(
            int(action.get("frame_layouts_base") or 0), len(action["frame_layouts"])
        )
    if isinstance(action.get("ref_frame"), int):
        action["ref_frame"] = old_to_new.get(action["ref_frame"], 0)


def _shift_frame_fields(action: dict, start: int, delta: int = 1) -> None:
    """Shift per-frame indexes at/after start when inserting frames."""
    for key in ("frame_offsets", "frame_durations", "frame_rotations",
                "frame_rotation_pivots", "frame_quads", "frame_transform_modes"):
        source = action.get(key) or {}
        action[key] = {
            str(int(k) + delta if int(k) >= start else int(k)): value
            for k, value in source.items()
        }
    for key in ("frame_mirror_h", "frame_mirror_v"):
        action[key] = [int(i) + delta if int(i) >= start else int(i)
                       for i in action.get(key, [])]
    ref = action.get("ref_frame")
    if isinstance(ref, int) and ref >= start:
        action["ref_frame"] = ref + delta


def _read_any_project(project_path: str) -> tuple[dict, dict[str, bytes]]:
    path = Path(project_path)
    if not path.exists():
        raise ValueError(f"工程文件不存在：{path}")
    try:
        return read_project(path)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"无法读取工程文件 {path}：{exc}") from exc


def _render_anim_doc_frame(manifest: dict, sections: dict[str, bytes], index: int) -> Image.Image:
    """Render one anim_doc frame with its serializable operation chain in Pillow."""
    payload = manifest.get("anim_doc") or {}
    names = payload.get("frames") or []
    if not 0 <= int(index) < len(names):
        raise ValueError(f"动图帧号越界：{index}（共 {len(names)} 帧）")
    image = png_from_bytes(sections.get(names[int(index)], b""))
    if image is None:
        raise ValueError(f"动图文档缺少帧数据：{names[int(index)]}")
    image = image.convert("RGBA")
    for op in payload.get("ops") or []:
        kind = op.get("kind")
        if kind == "transform":
            transform = str(op.get("t", ""))
            if transform == "flip_h":
                image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            elif transform == "flip_v":
                image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
            elif transform in {"rot90", "rot180", "rot270"}:
                image = image.rotate({"rot90": -90, "rot180": -180, "rot270": -270}[transform],
                                     expand=True, resample=Image.Resampling.NEAREST)
        elif kind == "key":
            color = op.get("color")
            colors = tuple(int(v) for v in color) if color else keying.estimate_bg_color(image)
            if colors:
                image, _ = keying_modes.key_image(
                    image, colors, mode_id=str(op.get("mode_id", "traditional")),
                    cartoon_strength=str(op.get("cartoon_strength", "balanced")),
                    cartoon_edge_type=str(op.get("cartoon_edge_type", "auto")),
                    **(op.get("params") or {}),
                )
        elif kind == "stroke":
            draw = ImageDraw.Draw(image)
            color = (0, 0, 0, 0) if op.get("tool") == "eraser" else str(op.get("color", "#FFFFFF"))
            width = max(1, int(op.get("size", 1)))
            for segment in op.get("segs") or []:
                if len(segment) >= 4:
                    draw.line(tuple(float(v) for v in segment[:4]), fill=color, width=width)
    return image


def _native_from_import(json_path: str, out_path: str) -> dict:
    """Convert any supported importer input into a native .sstool container."""
    project = load_project(json_path)
    atlas = _load_rgba(project.atlas_path)
    sections: dict[str, bytes] = {"images/combined.png": png_bytes(atlas)}
    actions: list[dict] = []
    for ai, imported in enumerate(project.actions):
        action = _new_action(imported.name, fps=imported.fps,
                             loop_mode=imported.loop, padding=imported.padding or 0,
                             action_type=imported.action_type,
                             color=imported.color)
        action["anchor"] = list(imported.anchor or (0.5, 0.5))
        if imported.ground:
            action["ground_enabled"], action["ground_offset"] = imported.ground
        if imported.move:
            action["move_direction"], action["move_speed"] = imported.move
        for fi, frame in enumerate(imported.frames):
            if not frame.parts:
                continue
            part_rects = [tuple(int(v) for v in part.rect) for part in frame.parts]
            left = min(r[0] for r in part_rects); top = min(r[1] for r in part_rects)
            right = max(r[0] + r[2] for r in part_rects)
            bottom = max(r[1] + r[3] for r in part_rects)
            rect = (left, top, right - left, bottom - top)
            # A frame can reference several atlas parts whose logical positions
            # differ from their atlas coordinates.  Store one complete, cropped
            # frame image and normalize the part positions to that crop; the
            # accumulated origin becomes the frame offset.
            min_x = min(int(part.pos[0]) for part in frame.parts)
            min_y = min(int(part.pos[1]) for part in frame.parts)
            max_x = max(int(part.pos[0]) + int(part.rect[2]) for part in frame.parts)
            max_y = max(int(part.pos[1]) + int(part.rect[3]) for part in frame.parts)
            if max_x <= min_x or max_y <= min_y:
                min_x, min_y, max_x, max_y = 0, 0, rect[2], rect[3]
            crop = Image.new("RGBA", (max_x - min_x, max_y - min_y), (0, 0, 0, 0))
            normalized_parts = []
            for part in frame.parts:
                pr = tuple(int(v) for v in part.rect)
                piece = atlas.crop((pr[0], pr[1], pr[0] + pr[2], pr[1] + pr[3]))
                px = int(part.pos[0]) - min_x
                py = int(part.pos[1]) - min_y
                crop.alpha_composite(piece, (px, py))
                origin = part.origin if part.origin is not None else (pr[0], pr[1])
                normalized_parts.append([
                    list(pr), px, py, int(origin[0]), int(origin[1]),
                ])
            section = f"actions/{ai:04d}/frame_{fi:04d}.png"
            sections[section] = png_bytes(crop)
            action["frame_rects"].append(list(rect))
            action["frame_parts"].append(normalized_parts)
            action["frame_sections"].append(section)
            action["selected_indices"].append(fi)
            if frame.duration_ms != max(1, round(1000 / max(1, imported.fps))):
                action["frame_durations"][str(fi)] = int(frame.duration_ms)
            frame_offset = (
                int((frame.canvas_pos or (0, 0))[0]) + min_x,
                int((frame.canvas_pos or (0, 0))[1]) + min_y,
            )
            if frame_offset != (0, 0):
                action["frame_offsets"][str(fi)] = [frame_offset[0], frame_offset[1]]
        actions.append(action)
    manifest = {
        "format": "spritesheet-tool-project", "version": 1,
        "app_version": __version__, "combined_image": "images/combined.png",
        "source_images": [], "thumbnail": None,
        "viewer": {"frames": [], "frame_logical": {}, "ignored_frames": [],
                    "cleared_frames": [], "source_offsets": []},
        "preview": {"action_index": 0 if actions else -1, "frame_index": 0},
        "actions": actions,
    }
    write_project(out_path, manifest, sections)
    return {"ok": True, "project_path": str(Path(out_path).resolve()),
            "format": project.fmt, "actions": [
                {"name": a["name"], "frames": len(a["frame_sections"])} for a in actions
            ], "warnings": project.warnings,
            "next_step": "调用 list_actions 或 get_action_detail 查看动作；需要导出时调用 export_project_asset"}


def _rotated_size(width: int, height: int, rotation: int) -> tuple[int, int]:
    """镜像+90°整数倍旋转后的帧尺寸（与软件 frame_base_size 的尺寸口径一致）。"""
    if rotation in (90, 270):
        return height, width
    return width, height


def _frame_sizes(action: dict, sections: dict) -> list[tuple[int, int] | None]:
    """逐帧显示尺寸：优先读已存帧 PNG 的文件头（Pillow 懒加载，不吃内存），
    帧图缺失的帧返回 None（老工程没有帧图时退回 rect 尺寸）。"""
    sizes: list[tuple[int, int] | None] = []
    for name in (action.get("frame_sections") or []):
        img = png_from_bytes(sections.get(name, b""))
        if img is not None and img.width > 0 and img.height > 0:
            sizes.append((img.width, img.height))
        else:
            sizes.append(None)
    return sizes


def _action_canvas(action: dict, sections: dict) -> dict:
    """按软件的预览画布规则推导动作画布尺寸。

    规则（与 compute_preview_layout 的基准一致）：所有帧变换后的最大宽高为基准，
    画布 = 最大宽高 + 两侧 padding。**不含**逐帧偏移把画布进一步撑大的情况
    （那种偏移超界的工程极少，返回字段里已注明口径）。
    返回 {"canvas": [w, h], "max_frame": [w, h], "padding": int}。
    """
    pad = int(action.get("padding") or 0)
    max_w = max_h = 0
    sizes = _frame_sizes(action, sections)
    if not sizes or all(s is None for s in sizes):
        # 老工程没有帧图：用 frame_rects 的尺寸兜底
        rects = action.get("frame_rects") or []
        sizes = [tuple(r[2:4]) for r in rects]
    rotations = action.get("frame_rotations") or {}
    for index, size in enumerate(sizes):
        if not size:
            continue
        w, h = _rotated_size(size[0], size[1], int(rotations.get(str(index), 0) or 0))
        max_w = max(max_w, w)
        max_h = max(max_h, h)
    return {
        "canvas": [max_w + 2 * pad, max_h + 2 * pad],
        "max_frame": [max_w, max_h],
        "padding": pad,
    }


def _canvas_pos(action: dict, sections: dict, index: int,
                width: int, height: int) -> tuple[int, int]:
    """帧内容在画布中的左上角（居中 + 逐帧偏移），与软件的预览摆放一致。"""
    pad = int(action.get("padding") or 0)
    canvas = _action_canvas(action, sections)
    max_w, max_h = canvas["max_frame"]
    dx, dy = (action.get("frame_offsets") or {}).get(str(index), [0, 0])
    return (pad + (max_w - width) // 2 + int(dx or 0),
            pad + (max_h - height) // 2 + int(dy or 0))


# ---------------------------------------------------------------------------
# 内部辅助：对齐几何（与 spritesheet/ui/transform.py 同一套算法）
#
# 刻意在这里**重写一遍**而不是 import ui.transform：那个模块顶部 import PyQt6，
# 而本服务器的 --mcp 分支是 headless 的（main.py 刻意不加载 QtWidgets）。
# 以下都是只看框、不看图的纯函数，两边口径必须保持一致。
# ---------------------------------------------------------------------------
def _cluster_1d(values, tol):
    """一维数值按"大间隔就断开"聚组，返回 (每组均值, 每个值属于哪一组)。"""
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


def _center_pitch(values) -> float:
    """一维中心的"簇间距"（中位数）——均分的话相邻格的中心差就是一格。"""
    ordered = sorted(values)
    gaps = [b - a for a, b in zip(ordered, ordered[1:]) if b > 0]
    if not gaps:
        return 0.0
    gaps.sort()
    return gaps[len(gaps) // 2]


class _SheetGrid(dict):
    """detect 的返回：{帧序: (列, 行)} + 验证通过的那组网格几何。"""
    __slots__ = ("cols", "rows", "pitch_x", "pitch_y")


def _cell_rect(index_of: dict, i: int, canvas_w: int, canvas_h: int,
               cols: int, rows: int) -> tuple[int, int, int, int]:
    """第 i 帧所在的「均分格」矩形 (x, y, w, h)。"""
    col, row = index_of[i]
    left = int(round(col * canvas_w / cols))
    right = int(round((col + 1) * canvas_w / cols))
    top = int(round(row * canvas_h / rows))
    bottom = int(round((row + 1) * canvas_h / rows))
    return (left, top, right - left, bottom - top)


def _detect_sheet_grid(rects, canvas_w: int, canvas_h: int):
    """判定这张精灵图是不是「横竖均分」；是则返回 _SheetGrid，否则 None。

    判法（对"多检出几个碎屑"免疫，不要求每格元素个数相等）：
      1. 内容块中心在某方向会聚成等距若干组 → 组数 = 该方向格数，组间距中位 = 一格多长；
      2. 另一方向格数由"画布尺寸 ÷ 格长"推，并纳入聚簇结果当候选；
      3. 每候选逐个内容块验算"内容完整落在自己那格里"，第一个全中的才算成立。
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
        add(round(canvas_w / pitch_y), rows_guess)
    if pitch_x > 0:
        add(cols_guess, round(canvas_h / pitch_x))
    for cols, rows in list(candidates):
        add(cols, round(canvas_h / (canvas_w / cols)) if cols else 0)
        add(round(canvas_w / (canvas_h / rows)) if rows else 0, rows)

    for cols, rows in candidates:
        cell_w, cell_h = canvas_w / cols, canvas_h / rows
        if pitch_x > 0 and abs(cell_w - pitch_x) > cell_w * 0.15:
            continue
        if pitch_y > 0 and abs(cell_h - pitch_y) > cell_h * 0.15:
            continue
        index_of: dict[int, tuple[int, int]] = {}
        ok = True
        for i, r in enumerate(rects):
            col = min(max(int(xs[i] // cell_w), 0), cols - 1)
            row = min(max(int(ys[i] // cell_h), 0), rows - 1)
            index_of[i] = (col, row)
            left, top, cw, ch = _cell_rect(index_of, i, canvas_w, canvas_h, cols, rows)
            if r[0] < left - 1 or r[0] + r[2] > left + cw + 1:
                ok = False
                break
            if r[1] < top - 1 or r[1] + r[3] > top + ch + 1:
                ok = False
                break
        if ok:
            result = _SheetGrid()
            result.cols, result.rows = cols, rows
            result.pitch_x, result.pitch_y = pitch_x, pitch_y
            result.update(index_of)
            return result
    return None


def _alpha_bounds_image(img: Image.Image):
    """图像 alpha 的紧切外框 (x, y, w, h)；全透明返回 None。"""
    if img is None:
        return None
    box = img.convert("RGBA").getchannel("A").getbbox()
    if not box:
        return None
    return (box[0], box[1], box[2] - box[0], box[3] - box[1])


def _align_offsets_grid(action: dict, sections: dict, manifest: dict):
    """路①：精灵图**横竖均分** → 每格就是"正常那一帧"的框，差值就是误差。

    返回 (offsets, how) 或 None（判不出均分 / 有带变换或多元素合成的帧）。
    """
    combined_name = manifest.get("combined_image") or ""
    atlas = png_from_bytes(sections.get(combined_name, b"")) if combined_name else None
    if atlas is None:
        return None
    total = len(action.get("frame_rects") or [])
    if total < 4:
        return None
    rects = [tuple(r) for r in (action.get("frame_rects") or [])]
    if len(rects) != total:
        return None
    rotations = action.get("frame_rotations") or {}
    quads = action.get("frame_quads") or {}
    mirror_h = set(action.get("frame_mirror_h") or [])
    mirror_v = set(action.get("frame_mirror_v") or [])
    parts_all = action.get("frame_parts") or []
    for i in range(total):
        if rotations.get(str(i)) or i in mirror_h or i in mirror_v or quads.get(str(i)):
            return None                     # 带变换的帧：帧内位置变了
        parts = parts_all[i] if i < len(parts_all) else None
        if not parts or len(parts) != 1:
            return None                     # 多元素合成的帧
    index_of = _detect_sheet_grid(rects, atlas.width, atlas.height)
    if not index_of:
        return None
    cols, rows = index_of.cols, index_of.rows
    use_pitch = bool(index_of.pitch_x > 0 and index_of.pitch_y > 0)
    if use_pitch:
        for i in range(total):
            r = rects[i]
            if abs(r[0] - int(round(index_of[i][0] * index_of.pitch_x))) > 2 \
                    or abs(r[1] - int(round(index_of[i][1] * index_of.pitch_y))) > 2:
                use_pitch = False
                break
    # 每帧内容在"格/原图"坐标系里的框
    local_boxes: list[tuple[int, int, int, int]] = []
    frame_sizes: list[tuple[int, int]] = []
    content_in_frame: list[tuple[int, int]] = []
    for i in range(total):
        r = rects[i]
        if use_pitch:
            base_x = int(round(index_of[i][0] * index_of.pitch_x))
            base_y = int(round(index_of[i][1] * index_of.pitch_y))
        else:
            col, row = index_of[i]
            grid = _cell_rect(index_of, i, atlas.width, atlas.height, cols, rows)
            base_x, base_y = grid[0], grid[1]
        local_boxes.append((base_x, base_y, r[2], r[3]))
        frame_sizes.append((int(r[2]), int(r[3])))
        content_in_frame.append((int(r[0]) - base_x, int(r[1]) - base_y))
    return _compute_source_anchor_offsets(
        local_boxes, frame_sizes, content_in_frame, cols, rows, use_pitch,
        index_of, atlas, int(action.get("padding") or 0),
    ), ("均分格" if True else "")


def _compute_source_anchor_offsets(local_boxes, frame_sizes, content_in_frame,
                                   cols, rows, use_pitch, index_of, atlas,
                                   padding: int = 0):
    """把每帧放回它在原图里的位置：offset = 原图坐标 − 居中落点。

    与 ui/transform.compute_source_anchor_offsets 同一公式；"画布基准"这一版
    按均分格推算，避免依赖 MainWindow 的预览布局对象。
    """
    if use_pitch:
        max_w = int(round(cols * index_of.pitch_x)) if index_of.pitch_x > 0 else atlas.width
        max_h = int(round(rows * index_of.pitch_y)) if index_of.pitch_y > 0 else atlas.height
    else:
        max_w, max_h = atlas.width, atlas.height
    out: dict[int, tuple[int, int]] = {}
    for i in range(len(local_boxes)):
        box = local_boxes[i]
        size = frame_sizes[i] if i < len(frame_sizes) else None
        inner = content_in_frame[i] if i < len(content_in_frame) else None
        if box is None or size is None or inner is None:
            continue
        bx, by = box[0], box[1]
        w, h = size
        cix, ciy = inner
        place_x = padding + (max_w - w) // 2
        place_y = padding + (max_h - h) // 2
        out[i] = (int(round(bx)) - (place_x + int(round(cix))),
                  int(round(by)) - (place_y + int(round(ciy))))
    return out


def _frame_profile(img: Image.Image, step: int = 4):
    """抽样的 alpha 列/行投影（给互相关用）。"""
    if img is None:
        return [], []
    img = img.convert("RGBA")
    sw = max(1, img.width // step)
    sh = max(1, img.height // step)
    small = img.resize((sw, sh), Image.Resampling.NEAREST)
    alpha = small.getchannel("A")
    px = list(alpha.getdata())
    cols = [0] * sw
    rows = [0] * sh
    for y in range(sh):
        base = y * sw
        s = 0
        for x in range(sw):
            v = px[base + x]
            if v:
                cols[x] += v
                s += v
        rows[y] = s
    return cols, rows


def _correlate_profiles(ref, prof, span: int) -> int:
    """prof 要对上 ref 需要移动多少（重叠区点积最大处）。"""
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


def _align_offsets_estimate(action: dict, sections: dict, manifest: dict):
    """路③：原图信息丢了 → 按帧间重合度估算（可能不准）。"""
    total = _frame_count(action)
    if total < 2:
        return {}
    images = []
    for i in range(total):
        try:
            img = _render_frame_image(action, sections, i, manifest)[0]
        except Exception:  # noqa: BLE001
            return {}
        images.append(img)
    ref_cols, ref_rows = _frame_profile(images[0], step=4)
    span = max(8, int(max(images[0].width, images[0].height) * 0.25))
    offsets: dict[int, tuple[int, int]] = {}
    for i in range(total):
        cols, rows = _frame_profile(images[i], step=4)
        dx = _correlate_profiles(ref_cols, cols, span)
        dy = _correlate_profiles(ref_rows, rows, span)
        offsets[i] = (int(dx) * 4, int(dy) * 4)
    return offsets


def _frame_feature(img: Image.Image, size: int = 64):
    """一帧的「指纹」：(64×64 遮罩位图, 遮罩像素数, 遮罩内平均色)。

    与 ui/similar.py 的 _features 同一口径（64 而不是 32：32 会把"换了姿势"平均掉）。
    """
    if img is None:
        return (0, 0, (0.0, 0.0, 0.0))
    small = img.convert("RGBA").resize((size, size), Image.Resampling.NEAREST)
    px = list(small.getdata())
    mask = 0
    count = 0
    sum_r = sum_g = sum_b = 0
    for i, (r, g, b, a) in enumerate(px):
        if a >= 8:
            mask |= 1 << i
            count += 1
            sum_r += r
            sum_g += g
            sum_b += b
    mean = (sum_r / count, sum_g / count, sum_b / count) if count else (0.0, 0.0, 0.0)
    return (mask, count, mean)


def _similar_fast(feature_a, feature_b, tolerance: int) -> bool:
    """形状重合度（遮罩 IoU）+ 内容平均色差 —— 只在「有内容的地方」上算。

    刻度：容差 t 表示"最多允许 t/1000 的格子不一样"，与 ui/similar.py 完全一致。
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


def _group_elements(images, tolerance: int = 0) -> dict[int, int]:
    """把帧按相似度分组，返回 {帧号: 组号}（与 GUI group_elements 同口径）。

    先按"完全一样"的 md5 归并（快），再对剩余的两两比 IoU + 色差。
    """
    total = len(images)
    marks: dict[int, int] = {}
    group = 0
    features = [_frame_feature(img) for img in images]
    if tolerance <= 0:
        seen: dict[bytes, int] = {}
        for i, img in enumerate(images):
            if img is None:
                continue
            key = img.convert("RGBA").tobytes()
            if key not in seen:
                seen[key] = group
                group += 1
            marks[i] = seen[key]
        return marks
    for i in range(total):
        if i in marks:
            continue
        marks[i] = group
        for j in range(i + 1, total):
            if j in marks:
                continue
            if _similar_fast(features[i], features[j], tolerance):
                marks[j] = group
        group += 1
    return marks


def _split_runs(marks: dict[int, int], total: int) -> list[list[int]]:
    """一串 = **同组号 + 帧序相邻**；不同组或不相邻就断开（用户口径）。"""
    runs: list[list[int]] = []
    current: list[int] = []
    for i in range(total):
        if i not in marks:
            if current:
                runs.append(current)
                current = []
            continue
        if current and marks[current[-1]] == marks[i] and current[-1] == i - 1:
            current.append(i)
        else:
            if current:
                runs.append(current)
            current = [i]
    if current:
        runs.append(current)
    return [r for r in runs if len(r) > 1]


def _iou(feat_a, feat_b) -> float:
    mask_a, count_a, _ = feat_a
    mask_b, count_b, _ = feat_b
    inter = (mask_a & mask_b).bit_count()
    union = count_a + count_b - inter
    return inter / union if union else 1.0


def _center_offset(images, run: list[int]) -> int:
    """这串里「最标准」那帧的相对位置：跟同串其他帧平均最像的那一帧。"""
    feats = [_frame_feature(images[i]) for i in run]
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
    for k in range(keep):
        picked.add(round(k * (n - 1) / (keep - 1)))
    while len(picked) > keep:
        far = max(picked, key=lambda j: (abs(j - center), j))
        if far == center:
            break
        picked.discard(far)
    return sorted(picked)


# 档位 → (分子, 分母)；None = 强力去重（一串只留 1 帧）。与 DECIMATE_RATIOS 一致。
_DECIMATE_RATIOS: dict[str, tuple[int, int] | None] = {
    "keep_3_5": (3, 5),
    "keep_1_2": (1, 2),
    "keep_1_4": (1, 4),
    "dedupe": None,
}


def _decimate_indices(images, marks: dict[int, int], mode: str) -> list[int]:
    """抽帧：返回**保留**的帧号（升序）。一串 N 帧里按档位只留 ceil(N*num/den) 帧，
    最少留 1 帧；不相邻/没配对的帧原样保留；每串必含最标准那帧。"""
    total = len(images)
    if total <= 1 or not marks:
        return list(range(total))
    ratio = _DECIMATE_RATIOS.get(mode, (1, 2))
    keep_idx = set(range(total))
    for run in _split_runs(marks, total):
        n = len(run)
        if ratio is None:
            keep = 1
        else:
            num, den = ratio
            keep = max(1, (n * num + den - 1) // den)
        if keep >= n:
            continue
        keep_pos = set(_pick_offsets(n, keep, _center_offset(images, run)))
        for j, idx in enumerate(run):
            if j not in keep_pos:
                keep_idx.discard(idx)
    return sorted(keep_idx)


def _compensate_offsets(sizes_old: dict, offsets_old: dict, sizes_new: dict,
                        frame_map: dict[int, int]) -> dict:
    """抽帧后补偿偏移，让每个保留帧的**落点逐帧不变**（相似 ≠ 同尺寸，
    抽出较大的帧会让 max 变小、居中项对所有帧一起变 ⇒ 画面整体位移）。

    new_offset = old_offset + [ (max_old−size)//2 − (max_new−size)//2 ]
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


# ---------------------------------------------------------------------------
# 内部辅助：帧渲染（与软件同一变换口径，Pillow 实现）
# ---------------------------------------------------------------------------
def _render_frame_image(action: dict, sections: dict, index: int,
                        manifest: dict | None = None):
    """渲染指定帧的画面（PIL.Image），与软件预览一致。

    顺序与软件 transform_frame_pixmap 相同：镜像H → 镜像V → 90°整数倍旋转；
    自由四边形(quad) 透视由 NumPy + Pillow 在 headless 下复刻。
    帧图缺失时回退到「从合成大图抠单部件」（需 manifest 里的合成大图段名，
    仅无变换帧；见 _compose_from_atlas）。
    """
    quad = (action.get("frame_quads") or {}).get(str(index))
    rotation = int((action.get("frame_rotations") or {}).get(str(index), 0) or 0)
    mirror_h = index in (action.get("frame_mirror_h") or [])
    mirror_v = index in (action.get("frame_mirror_v") or [])

    names = action.get("frame_sections") or []
    name = names[index] if index < len(names) else None
    img = png_from_bytes(sections.get(name, b"")) if name else None
    if img is None:
        # 老工程没有帧图：从合成大图抠（仅支持无变换单部件帧）
        composed = _compose_from_atlas(action, sections, manifest, index)
        if composed is None:
            raise ValueError(
                f"帧 {index} 既没有已存帧图，又无法从合成大图重建（可能是合成帧），"
                "无法提取画面。请用 SpriteSheetTool 重新保存一次工程再试。"
            )
        return composed, "recomposed_from_atlas"

    img = img.convert("RGBA")
    if mirror_h:
        img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if mirror_v:
        img = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    if rotation:
        img = img.rotate(-rotation, expand=True,
                         resample=Image.Resampling.NEAREST)
    if quad and len(quad) == 8:
        img = _apply_quad(img, quad)
    return img, "saved_frame_png"


def _apply_quad(image: Image.Image, quad: list | tuple) -> Image.Image:
    """Render normalized four-corner coordinates with Pillow's perspective map."""
    if len(quad) != 8:
        return image
    w, h = image.size
    points = [(float(quad[i]) * w, float(quad[i + 1]) * h) for i in range(0, 8, 2)]
    min_x = math.floor(min(x for x, _ in points)); min_y = math.floor(min(y for _, y in points))
    max_x = math.ceil(max(x for x, _ in points)); max_y = math.ceil(max(y for _, y in points))
    out_w, out_h = max(1, max_x - min_x), max(1, max_y - min_y)
    dst = [(x - min_x, y - min_y) for x, y in points]
    src = [(0.0, 0.0), (float(w), 0.0), (float(w), float(h)), (0.0, float(h))]
    matrix = []
    values = []
    for (sx, sy), (dx, dy) in zip(src, dst):
        matrix.append([sx, sy, 1.0, 0.0, 0.0, 0.0, -dx * sx, -dx * sy])
        values.append(dx)
        matrix.append([0.0, 0.0, 0.0, sx, sy, 1.0, -dy * sx, -dy * sy])
        values.append(dy)
    try:
        import numpy as np
    except ImportError:
        return image
    try:
        homography = np.linalg.solve(np.asarray(matrix, dtype=float), np.asarray(values, dtype=float))
        forward = np.array([
            [homography[0], homography[1], homography[2]],
            [homography[3], homography[4], homography[5]],
            [homography[6], homography[7], 1.0],
        ])
        inverse = np.linalg.inv(forward)
        coeffs = tuple(float(v) for v in inverse.flatten()[:8])
    except (ValueError, np.linalg.LinAlgError):
        return image
    return image.transform((out_w, out_h), Image.Transform.PERSPECTIVE, coeffs,
                           resample=Image.Resampling.NEAREST, fillcolor=(0, 0, 0, 0))


def _compose_from_atlas(action: dict, sections: dict,
                        manifest: dict | None, index: int):
    """兜底：无变换单部件帧 → 从合成大图按 rect 抠出，摆到画布居中+偏移位置。

    只支持单部件、无变换的普通帧（frame_parts[index] 恰有 1 个部件）；
    其它情况返回 None（调用方改为报错）。"""
    if (action.get("frame_rotations") or {}).get(str(index)) or \
            index in (action.get("frame_mirror_h") or []) or \
            index in (action.get("frame_mirror_v") or []) or \
            (action.get("frame_quads") or {}).get(str(index)):
        return None
    parts = action.get("frame_parts") or []
    if not (0 <= index < len(parts)) or len(parts[index]) != 1:
        return None
    rects = action.get("frame_rects") or []
    if not (0 <= index < len(rects)) or manifest is None:
        return None
    rect = tuple(int(v) for v in rects[index])
    width, height = rect[2], rect[3]
    atlas = _combined_canvas(sections, manifest)
    # Pillow crop 用 (left, top, right, bottom) 绝对坐标；rect 是 (x, y, w, h)
    part = atlas.crop((rect[0], rect[1], rect[0] + width, rect[1] + height))
    canvas_info = _action_canvas(action, sections)
    canvas_w, canvas_h = canvas_info["canvas"]
    pos_x, pos_y = _canvas_pos(action, sections, index, width, height)
    canvas_img = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    canvas_img.alpha_composite(part, (pos_x, pos_y))
    return canvas_img


def _combined_canvas(sections: dict, manifest: dict):
    """返回合成大图（PIL.Image，RGBA）；工程没存合成大图时抛明确错误。"""
    name = manifest.get("combined_image") or ""
    img = png_from_bytes(sections.get(name, b"")) if name else None
    if img is None:
        raise ValueError(
            "工程里没有合成大图（combined_image），无法完成该操作。"
            "请用 SpriteSheetTool 打开并重新保存一次工程。"
        )
    return img.convert("RGBA")


# ---------------------------------------------------------------------------
# 内部辅助：engine JSON 导出的 records / atlas（复刻 GUI 的「原图坐标图集」模式）
# ---------------------------------------------------------------------------
class _ActionShell:
    """把 manifest 里的动作字典包成 exporters 能鸭子访问的对象（可哈希）。

    exporters 只访问 .name / .frames（长度）/ .fps / .loop_mode /
    .frame_duration_ms(i)，不碰像素。records 的键是 (action, index)，所以
    外壳必须可哈希 —— 不用 SimpleNamespace（它定义了 __eq__，unhashable）。
    本类不定义 __eq__，哈希回落到 object 的 id()。
    """

    __slots__ = ("name", "frames", "fps", "loop_mode", "frame_duration_ms")

    def __init__(self, action: dict) -> None:
        self.name = action.get("name") or "action"
        self.frames = [None] * _frame_count(action)
        self.fps = int(action.get("fps") or 10)
        self.loop_mode = action.get("loop_mode") or "loop"
        durations = action.get("frame_durations") or {}
        fps = self.fps

        def frame_duration_ms(i: int) -> int:
            duration = durations.get(str(i))
            if duration is not None:
                return int(duration)
            return max(1, round(1000 / max(1, fps)))

        self.frame_duration_ms = frame_duration_ms


def _action_shell(action: dict) -> _ActionShell:
    return _ActionShell(action)


def _build_export_atlas(manifest: dict, sections: dict):
    """构造导出图集 + records（与 GUI 导出「原图坐标图集」行为一致）。

    - 图集 = 工程的合成大图；普通帧直接引用其中的 rect（frame_rects）；
    - 带镜像/旋转的帧用帧图 + Pillow 复刻变换后**烘焙追加到图集下方**，
      图集尺寸随之扩张（同 GUI 的 _build_original_atlas）；
    - 含 quad 透视的帧 → 复刻后烘焙追加到图集下方。
    返回 (atlas: PIL.Image, records: dict, shells: list) —— records 的键
    用的就是返回的 shells（外壳哈希是 id()，调用方必须用同一批对象，
    不能重新 _action_shell 一份，否则 exporters 查 records 全部落空）。
    """
    atlas = _combined_canvas(sections, manifest)
    shells: list[_ActionShell] = []
    records: dict = {}
    bake: list[tuple[_ActionShell, int, Image.Image]] = []

    for action in _actions_of(manifest):
        shell = _action_shell(action)
        shells.append(shell)
        rects = action.get("frame_rects") or []
        for index in range(_frame_count(action)):
            if not (index < len(rects)):
                continue  # records 缺失的帧 exporters 会跳过
            rect = tuple(int(v) for v in rects[index])
            has_quad = bool((action.get("frame_quads") or {}).get(str(index)))
            has_rot = bool((action.get("frame_rotations") or {}).get(str(index)))
            has_mirror = index in (action.get("frame_mirror_h") or []) or \
                index in (action.get("frame_mirror_v") or [])
            frame_parts = action.get("frame_parts") or []
            has_multiple_parts = index < len(frame_parts) and len(frame_parts[index] or []) > 1
            if not (has_quad or has_rot or has_mirror or has_multiple_parts):
                w, h = rect[2], rect[3]
                records[(shell, index)] = {
                    "parts": [{"rect": rect, "pos": (0, 0)}],
                    "rect": rect,
                    "composite_rect": rect,
                    "offset": (0, 0),
                    "source": (w, h),
                    "canvas": (w, h),
                    "trimmed": False,
                }
            else:
                frame_img, _ = _render_frame_image(action, sections, index, manifest)
                bake.append((shell, index, frame_img))

    if bake:
        atlas = _paste_baked(atlas, bake, records)
    return atlas, records, shells


def _paste_baked(atlas: Image.Image, bake, records: dict) -> Image.Image:
    """把带变换的帧图块追加到图集下方（间距/落位与 GUI 一致），更新 records。"""
    max_bw = max(b[2].width for b in bake)
    total_bh = sum(b[2].height for b in bake) + max(0, len(bake) - 1) + 2
    canvas = Image.new("RGBA", (max(atlas.width, max_bw + 2), atlas.height + total_bh),
                       (0, 0, 0, 0))
    canvas.alpha_composite(atlas, (0, 0))
    cur_y = atlas.height + 2
    for shell, index, frame_img in bake:
        bx = max(1, (canvas.width - frame_img.width) // 2)
        canvas.alpha_composite(frame_img, (bx, cur_y))
        w, h = frame_img.width, frame_img.height
        rect = (bx, cur_y, w, h)
        records[(shell, index)] = {
            "parts": [{"rect": rect, "pos": (0, 0)}],
            "rect": rect,
            "composite_rect": rect,
            "offset": (0, 0),
            "source": (w, h),
            "canvas": (w, h),
            "trimmed": False,
        }
        cur_y += h + 1
    return canvas


# ---------------------------------------------------------------------------
# 十四个工具
# ---------------------------------------------------------------------------
@mcp.tool(annotations={
    "title": "导入精灵图 JSON",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
})
def import_spritesheet_json(json_path: str) -> dict:
    """导入精灵图 JSON，自动识别格式并返回动作清单。

    支持五种格式（无需指定，自动识别）：本工具导出格式（spritesheet-animation，
    无损往返）、TexturePacker JSON-Hash、TexturePacker JSON-Array、Aseprite
    JSON-Array、豆包视频关键帧格式。图片按 JSON 引用的文件名在同目录查找。

    参数：
      - json_path (str)：精灵图 JSON 的本地绝对路径（图片放在同一目录）。

    返回 dict：
      {
        "format": "native|tp_hash|tp_array|aseprite|doubao",
        "json_path": str,
        "atlas_path": str,          # 找到的配套图片路径
        "actions": [{ "name": str, "frames": int, "fps": int, "loop": str,
                      "anchor": [x, y], "canvas": [w, h]|null,
                      "padding": int|null, "action_type": str,
                      "move_direction": str|null, "move_speed": int|null,
                      "color": str|null }],
        "warnings": [str]
      }

    典型用法：拿到别人的精灵图 JSON 时先调用本工具看结构，再决定怎么用。
    文件不存在 / JSON 损坏 / 找不到配套图片时抛错误（含原因与建议）。
    """
    try:
        proj = load_project(json_path)
    except Exception as exc:  # noqa: BLE001 - importer 抛自定义异常，统一消息后上抛
        raise ValueError(f"导入失败：{exc}") from exc
    actions = []
    for action in proj.actions:
        item = {
            "name": action.name,
            "frames": len(action.frames),
            "fps": action.fps,
            "loop": action.loop,
            "anchor": [round(x, 6) for x in action.anchor] if action.anchor else None,
            "canvas": list(action.canvas) if action.canvas else None,
            "padding": action.padding,
            "action_type": action.action_type,
            "move_direction": action.move[0] if action.move else None,
            "move_speed": action.move[1] if action.move else None,
            "color": action.color,
        }
        actions.append(item)
    return {
        "format": proj.fmt,
        "json_path": str(proj.json_path),
        "atlas_path": str(proj.atlas_path),
        "actions": actions,
        "warnings": proj.warnings,
    }


@mcp.tool(annotations={
    "title": "读取 .sstool 工程元信息",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
})
def read_sstool_project(project_path: str) -> dict:
    """读取 .sstool 工程文件的结构化元信息（不返回图片二进制）。

    返回内容：工程格式/版本、动作列表（名称、帧数、fps、loop、锚点、画布尺寸、
    padding、地面线、动作类型与移动参数、配色）、以及工程内的资源段名称清单
    （combined 大图、source 图、各帧 PNG 都在里面，名称可直接用于后续工具）。

    参数：
      - project_path (str)：.sstool 工程的本地绝对路径。

    返回 dict：
      {
        "ok": true,
        "path": str,
        "format": "spritesheet-tool-project",
        "version": 1,
        "app_version": str,
        "combined_image": str|null,      # 合成大图段名
        "source_count": int,             # 源图数量
        "section_names": [str],          # 全部资源段名称（不含内容）
        "actions": [{ "name", "frames", "fps", "loop_mode", "anchor",
                      "canvas_size": [w, h], "padding",
                      "ground_enabled": bool, "ground_offset": int,
                      "action_type", "move_direction", "move_speed", "color" }]
      }

    注意：画布尺寸按软件预览规则推导（所有帧变换后最大宽高 + 两侧 padding 的基准
    画布，未含逐帧偏移向外撑大的部分）；动图文档工程会明确报错（暂不支持）。
    """
    manifest, sections = _read_checked(project_path)
    actions = []
    for action in _actions_of(manifest):
        canvas = _action_canvas(action, sections)
        actions.append(_action_summary(action, canvas))
    return {
        "ok": True,
        "path": str(Path(project_path).resolve()),
        "format": manifest.get("format"),
        "version": manifest.get("version"),
        "app_version": manifest.get("app_version"),
        "combined_image": manifest.get("combined_image"),
        "source_count": len(manifest.get("source_images") or []),
        "section_names": sorted(sections.keys()),
        "actions": actions,
    }


def _action_summary(action: dict, canvas: dict) -> dict:
    """单个动作的摘要字段（read_sstool_project / list_actions 共用）。"""
    return {
        "name": action.get("name"),
        "frames": _frame_count(action),
        "fps": action.get("fps"),
        "loop_mode": action.get("loop_mode"),
        "anchor": [round(float(v), 6) for v in (action.get("anchor") or [0.5, 0.5])],
        "canvas_size": canvas["canvas"],
        "padding": action.get("padding"),
        "ground_enabled": action.get("ground_enabled"),
        "ground_offset": action.get("ground_offset"),
        "action_type": action.get("action_type"),
        "move_direction": action.get("move_direction"),
        "move_speed": action.get("move_speed"),
        "color": action.get("color"),
    }


@mcp.tool(annotations={
    "title": "列出全部动作",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
})
def list_actions(project_path: str) -> list:
    """列出 .sstool 工程里的全部动作（名称、帧数、fps、loop、画布尺寸）。

    参数：
      - project_path (str)：.sstool 工程的本地绝对路径。

    返回 list[dict]：每项 { name, frames, fps, loop_mode, anchor,
    canvas_size: [w, h], padding, action_type }（字段与 read_sstool_project
    的 actions 相同）。工程读取失败时抛错误（含原因）。
    """
    manifest, sections = _read_checked(project_path)
    return [_action_summary(a, _action_canvas(a, sections))
            for a in _actions_of(manifest)]


@mcp.tool(annotations={
    "title": "查看动作逐帧详情",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
})
def get_action_detail(project_path: str, action_name: str) -> dict:
    """查看指定动作的逐帧细节。

    每帧给出：图集 rect（该帧内容在合成大图里的位置与尺寸）、逐帧偏移
    （相对居中位置的偏移，软件里的 frame_offsets 口径）、画布落点 canvas_pos
    （内容摆在画布里的实际左上角，居中 + 偏移）、时长 duration_ms、
    变换参数（rotation / mirror / quad）与变换前后的尺寸。

    参数：
      - project_path (str)：.sstool 工程的本地绝对路径；
      - action_name (str)：动作名称（用 list_actions 查可用名称）。

    返回 dict：
      {
        "name": str,
        "frame_count": int,
        "fps": int,
        "loop_mode": str,
        "anchor": [x, y],
        "padding": int,
        "canvas_size": [w, h],        # 基准画布（未含偏移撑大）
        "frames": [
          {
            "index": int,
            "rect": [x, y, w, h],               # 图集中位置
            "offset": [dx, dy],                 # 逐帧偏移（相对居中）
            "canvas_pos": [x, y],               # 内容在画布中的左上角
            "duration_ms": int,
            "rotation": int, "mirror_h": bool, "mirror_v": bool,
            "quad": [...]|null,
            "source_size": [w, h],              # 该帧内容尺寸
            "size": [w, h]                      # 变换后尺寸
          }
        ]
      }

    找不到动作时抛错误，并列出当前工程所有动作名。
    """
    manifest, sections = _read_checked(project_path)
    action = _find_action(manifest, action_name)
    canvas = _action_canvas(action, sections)
    rotations = action.get("frame_rotations") or {}
    rects = action.get("frame_rects") or []
    fps = int(action.get("fps") or 10)
    offsets = action.get("frame_offsets") or {}
    durations = action.get("frame_durations") or {}
    mirrors_h = action.get("frame_mirror_h") or []
    mirrors_v = action.get("frame_mirror_v") or []
    quads = action.get("frame_quads") or {}

    frames = []
    for index in range(_frame_count(action)):
        rect = tuple(int(v) for v in rects[index]) if index < len(rects) else None
        source_w = rect[2] if rect else 0
        source_h = rect[3] if rect else 0
        rotation = int((rotations.get(str(index), 0) or 0))
        w, h = _rotated_size(source_w, source_h, rotation)
        dx, dy = (offsets.get(str(index)) or [0, 0])
        duration = durations.get(str(index))
        if duration is None:
            duration = max(1, round(1000 / max(1, fps)))
        pos_x, pos_y = _canvas_pos(action, sections, index, w, h)
        frames.append({
            "index": index,
            "rect": list(rect) if rect else None,
            "offset": [int(dx or 0), int(dy or 0)],
            "canvas_pos": [pos_x, pos_y],
            "duration_ms": int(duration),
            "rotation": rotation,
            "mirror_h": index in mirrors_h,
            "mirror_v": index in mirrors_v,
            "quad": quads.get(str(index)),
            "source_size": [source_w, source_h] if rect else None,
            "size": [w, h],
        })
    return {
        "name": action.get("name"),
        "frame_count": len(frames),
        "fps": fps,
        "loop_mode": action.get("loop_mode"),
        "anchor": [round(float(v), 6) for v in (action.get("anchor") or [0.5, 0.5])],
        "padding": action.get("padding"),
        "canvas_size": canvas["canvas"],
        "frames": frames,
    }


@mcp.tool(annotations={
    "title": "导出引擎 JSON",
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
})
def export_to_engine_json(project_path: str, fmt: str, out_dir: str) -> str:
    """把 .sstool 工程导出为第三方引擎 JSON，同时写出配套图集 PNG。

    格式（fmt，不区分大小写）：
      - "tp_hash"         TexturePacker JSON-Hash（frames 为对象）
      - "tp_array"        TexturePacker JSON-Array（frames 为数组）
      - "aseprite_array"  Aseprite JSON-Array（带 frameTags 动画标签）

    图集策略与软件导出行为一致：以工程合成大图为图集；带旋转/镜像/透视的帧会
    复刻变换后烘焙追加到图集下方（JSON 引用即实际画面）。

    参数：
      - project_path (str)：.sstool 工程本地绝对路径（导出名取工程文件名）；
      - fmt (str)：上面三种之一；
      - out_dir (str)：输出目录（不存在会自动创建）。

    返回 str：导出的 **JSON 文件绝对路径**（同名图集 PNG 就在它旁边，
    如 cat.tp_hash.json + cat.png）。
    """
    manifest, sections = _read_checked(project_path)
    fmt_normalized = str(fmt).strip().lower()
    allowed = {
        exporters.FMT_TP_HASH: exporters.FMT_TP_HASH,
        exporters.FMT_TP_ARRAY: exporters.FMT_TP_ARRAY,
        exporters.FMT_ASEPRITE_ARRAY: exporters.FMT_ASEPRITE_ARRAY,
    }
    if fmt_normalized not in allowed:
        raise ValueError(
            f"未知导出格式「{fmt}」。可选：{', '.join(sorted(allowed))}"
            "（不区分大小写）。"
        )
    atlas, records, shells = _build_export_atlas(manifest, sections)

    payload = exporters.build_payload(
        fmt_normalized, shells, records,
        atlas_name=f"{Path(project_path).stem}.png",
        atlas_w=atlas.width, atlas_h=atlas.height,
        app_label=f"{__app_name__} v{__version__}",
    )
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    suffix = exporters.FORMAT_SUFFIX[fmt_normalized]
    json_path = out / f"{Path(project_path).stem}{suffix}.json"
    atlas_path = out / f"{Path(project_path).stem}.png"
    exporters.write_payload(json_path, payload)
    atlas_path.write_bytes(png_bytes(atlas))
    return str(json_path)


@mcp.tool(annotations={
    "title": "提取指定帧 PNG",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
})
def extract_frame_png(project_path: str, action_name: str, frame_index: int) -> dict:
    """合成指定动作的指定帧画面，返回 PNG 的 base64 编码字符串。

    返回的 base64 是标准 PNG 字节流，客户端可直接补 data URI 前缀
    （data:image/png;base64,xxx）用于展示，或落盘为 .png 文件。

    画面口径：无变换帧直接用工程里保存的帧图（与软件预览是同一张图）；
    带镜像/旋转/透视的帧会按软件同一顺序复刻变换。老工程缺帧图时回退到从合成大图
    重建（单部件无变换帧），返回里带 source 字段说明实际数据来源。

    参数：
      - project_path (str)：.sstool 工程本地绝对路径；
      - action_name (str)：动作名称；
      - frame_index (int)：帧序号，从 0 开始（越界会明确报错）。

    返回 dict：
      {
        "ok": true,
        "action": str,
        "frame_index": int,
        "width": int, "height": int,
        "format": "png",
        "base64_png": str,              # base64 编码的 PNG 字节
        "source": "saved_frame_png" | "recomposed_from_atlas"
      }

    帧越界 / 找不到动作 / 无法合成时抛错误（含原因与下一步建议）。
    """
    manifest, sections = _read_checked(project_path)
    action = _find_action(manifest, action_name)
    count = _frame_count(action)
    if not (0 <= frame_index < count):
        raise ValueError(
            f"帧序号越界：{frame_index}（动作「{action_name}」共 {count} 帧，"
            "从 0 开始）。"
        )
    img, source = _render_frame_image(action, sections, int(frame_index), manifest)
    raw = png_bytes(img)
    return {
        "ok": True,
        "action": action.get("name"),
        "frame_index": int(frame_index),
        "width": img.width,
        "height": img.height,
        "format": "png",
        "base64_png": base64.b64encode(raw).decode("ascii"),
        "source": source,
    }


# ---------------------------------------------------------------------------
# 批量查看工具（总览 / 全部帧 / 健康检查）——纯只读
# ---------------------------------------------------------------------------
def _fit_thumb(img, target_h: int, max_w: int) -> "Image.Image":
    """等比缩到不超过 target_h × max_w（宽高比极端也不撑爆拼图），返回 RGBA。"""
    if img is None:
        return Image.new("RGBA", (max_w, target_h), (46, 46, 46, 255))
    w, h = img.size
    scale = min(target_h / max(1, h), max_w / max(1, w), 1.0)
    nw = max(1, round(w * scale))
    nh = max(1, round(h * scale))
    return img.convert("RGBA").resize((nw, nh), Image.Resampling.BILINEAR)


def _action_thumb(action: dict, sections: dict, manifest: dict) -> "Image.Image | None":
    """动作首帧缩略图；取不到（quad/缺帧图/老爷工程）时返回 None。"""
    try:
        img, _ = _render_frame_image(action, sections, 0, manifest)
        return img
    except Exception:  # noqa: BLE001 - 任一帧渲染不了都不影响别的动作出总览
        return None


@mcp.tool(annotations={
    "title": "工程总览",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
})
def project_overview(project_path: str) -> dict:
    """一次返回 .sstool 工程的全貌：动作清单 + 每动作首帧拼成的总览图。

    比 list_actions 更进一步：每个动作附一张首帧缩略图，所有动作横排拼成
    一张总览 PNG（base64），AI 一眼看清整个工程长得什么样、有哪些动作、
    每个多少帧。取不到首帧的动作用灰块占位（不报错、不影响整体）。

    参数：
      - project_path (str)：.sstool 工程的本地绝对路径。

    返回 dict：
      {
        "ok": true,
        "project": str,                # 文件名
        "action_count": int,
        "total_frames": int,
        "actions": [{ name, frames, fps, loop_mode, anchor, canvas_size,
                      padding, action_type, move_direction, move_speed, color }],
        "overview_png": str|None       # base64 PNG（黑底横排，动作多时会很宽）
      }

    工程读取失败时抛错误（含原因与建议）。
    """
    manifest, sections = _read_checked(project_path)
    actions = _actions_of(manifest)
    summaries = [_action_summary(a, _action_canvas(a, sections)) for a in actions]
    thumbs = []
    row_w = 0
    for a in actions:
        thumb = _fit_thumb(_action_thumb(a, sections, manifest), 88, 128)
        thumbs.append(thumb)
        row_w += thumb.width + 8
    overview_png = None
    if thumbs:
        sheet = Image.new("RGBA", (row_w + 8, 88 + 8), (20, 20, 20, 255))
        x = 8
        for thumb in thumbs:
            sheet.alpha_composite(thumb, (x, 4))
            x += thumb.width + 8
        overview_png = base64.b64encode(png_bytes(sheet)).decode("ascii")
    return {
        "ok": True,
        "project": Path(project_path).name,
        "action_count": len(actions),
        "total_frames": sum(_frame_count(a) for a in actions),
        "actions": summaries,
        "overview_png": overview_png,
    }


@mcp.tool(annotations={
    "title": "提取动作全部帧",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
})
def extract_action_frames_png(project_path: str, action_name: str) -> dict:
    """提取动作的**全部**帧画面：逐帧 base64 列表 + 一张网格总览图。

    与 extract_frame_png 同一套渲染口径（镜像/旋转/透视复刻）；
    适合 AI 一次看完整条动画序列。帧数很多时 contact_sheet 按每行最多 16 帧
    自动换行（宽度不会失控）。

    参数：
      - project_path (str)：.sstool 工程本地绝对路径；
      - action_name (str)：动作名称（用 list_actions 查可用名称）。

    返回 dict：
      {
        "ok": true,
        "action": str,
        "frame_count": int,
        "frames": [{ "frame_index", "width", "height", "base64_png", "source" }],
        "contact_sheet_png": str     # 一张 base64 PNG：全部帧按序号排列的网格图
      }

    找不到动作或帧数据损坏时抛错误（含原因）。
    """
    manifest, sections = _read_checked(project_path)
    action = _find_action(manifest, action_name)
    count = _frame_count(action)
    frames = []
    sheet_cell = 64
    per_row = 16
    if count:
        cols = min(per_row, count)
        rows = (count + per_row - 1) // per_row
        sheet = Image.new("RGBA", (cols * sheet_cell, rows * sheet_cell),
                          (20, 20, 20, 255))
    else:
        sheet = None
    for index in range(count):
        img, source = _render_frame_image(action, sections, index, manifest)
        raw = png_bytes(img)
        frames.append({
            "frame_index": index,
            "width": img.width,
            "height": img.height,
            "base64_png": base64.b64encode(raw).decode("ascii"),
            "source": source,
        })
        if sheet is not None:
            thumb = _fit_thumb(img, sheet_cell - 8, sheet_cell - 8)
            cx = (index % per_row) * sheet_cell + (sheet_cell - thumb.width) // 2
            cy = (index // per_row) * sheet_cell + (sheet_cell - thumb.height) // 2
            sheet.alpha_composite(thumb, (cx, cy))
    contact_sheet_png = base64.b64encode(png_bytes(sheet)).decode("ascii") \
        if sheet is not None else None
    return {
        "ok": True,
        "action": action.get("name"),
        "frame_count": count,
        "frames": frames,
        "contact_sheet_png": contact_sheet_png,
    }


@mcp.tool(annotations={
    "title": "工程健康检查",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
})
def validate_project(project_path: str) -> dict:
    """检查 .sstool 工程的数据完整性，列出所有问题（error / warning）。

    error 级 = 会直接影响预览/导出正确性的硬伤；warning 级 = 老工程常见的
    可回退情况。检查项：
      - 空动作（0 帧，error）；
      - frame_sections 里引用的帧图在工程里不存在（error）；
      - 帧图数量与 rect 数量对不上（warning）；
      - 帧 rect 越出合成大图 / 宽高 ≤ 0（warning）。

    参数：
      - project_path (str)：.sstool 工程的本地绝对路径。

    返回 dict：
      {
        "ok": bool,                    # error 数为 0 即 true
        "project": str,
        "action_count": int,
        "summary": { "errors": int, "warnings": int },
        "issues": [{ "level": "error"|"warning", "scope": "project"|"action"|"frame",
                     "action": str|None, "frame": int|None, "message": str }]
      }
    """
    manifest, sections = _read_checked(project_path)
    issues: list[dict] = []
    error_count = warning_count = 0

    def add(level: str, scope: str, action_name, frame_index, message: str) -> None:
        nonlocal error_count, warning_count
        if level == "error":
            error_count += 1
        else:
            warning_count += 1
        issues.append({
            "level": level,
            "scope": scope,
            "action": action_name,
            "frame": frame_index,
            "message": message,
        })

    atlas = None
    atlas_name = manifest.get("combined_image") or ""
    atlas_img = png_from_bytes(sections.get(atlas_name, b"")) if atlas_name else None
    if atlas_img is None:
        add("warning", "project", None, None,
            f"工程缺少合成大图（combined_image={atlas_name!r}），导出/部分预览会受限。")
    else:
        atlas = (atlas_img.width, atlas_img.height)

    actions = _actions_of(manifest)
    if not actions:
        add("warning", "project", None, None, "工程里没有任何动作。")

    for action in actions:
        name = action.get("name") or "?"
        frame_sections = action.get("frame_sections") or []
        if not frame_sections:
            add("error", "action", name, None, "空动作：没有任何帧。")
            continue
        rects = action.get("frame_rects") or []
        if len(rects) != len(frame_sections):
            add("warning", "action", name, None,
                f"帧图 {len(frame_sections)} 张与 rect {len(rects)} 条数量不一致。")
        missing = [s for s in frame_sections if s not in sections]
        if missing:
            add("error", "action", name, None,
                f"有 {len(missing)} 帧图缺失：{missing[:3]}{'…' if len(missing) > 3 else ''}")
        if atlas is not None and len(rects) == len(frame_sections):
            aw, ah = atlas
            for index, rect in enumerate(rects):
                rx, ry, rw, rh = (int(v) for v in rect[:4])
                if rw <= 0 or rh <= 0:
                    add("warning", "frame", name, index,
                        f"rect 宽高异常 ({rw}x{rh})。")
                elif rx < 0 or ry < 0 or rx + rw > aw or ry + rh > ah:
                    add("warning", "frame", name, index,
                        f"rect 越出合成大图 {aw}x{ah}：({rx},{ry},{rw},{rh})。")

    return {
        "ok": error_count == 0,
        "project": Path(project_path).name,
        "action_count": len(actions),
        "summary": {"errors": error_count, "warnings": warning_count},
        "issues": issues,
    }


# ---------------------------------------------------------------------------
# 分析工具（相似分组 / 运动轨迹）——纯只读
# ---------------------------------------------------------------------------
def _frame_elements(action: dict, index: int):
    """一帧用到的元素组合（规范化后做分组键）：优先 frame_parts 的 rect 集合，
    老工程退回 frame_rects。与软件里"相似彩块按元素组合分组"是同一口径。"""
    parts = action.get("frame_parts") or []
    if 0 <= index < len(parts):
        keys = sorted(tuple(int(v) for v in p[0]) for p in (parts[index] or []))
        return tuple(keys)
    rects = action.get("frame_rects") or []
    if 0 <= index < len(rects):
        return (tuple(int(v) for v in rects[index]),)
    return None


@mcp.tool(annotations={
    "title": "相似帧分组",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
})
def find_similar_frames(project_path: str, action_name: str) -> dict:
    """把动作的帧按「用到的元素组合」分组 —— 与软件的相似帧彩块同一口径。

    同一组里的帧在软件里会标成同一种彩块颜色：它们用同一套精灵元素（rect
    集合相同，可视为重复帧的候选）。AI 可以据此建议抽帧 / 删除重复帧。
    注意：这里比的是"用了哪些元素"，不是像素级相似；元素相同但位置略有
    不同的帧会进同一组（这正是软件彩块的含义）。

    参数：
      - project_path (str)：.sstool 工程的本地绝对路径；
      - action_name (str)：动作名称（用 list_actions 查可用名称）。

    返回 dict：
      {
        "ok": true,
        "action": str,
        "frame_count": int,
        "group_count": int,
        "groups": [
          { "group_id": int, "frame_count": int,
            "frames": [int, ...],      # 组内帧号（升序）
            "rects": [[x, y, w, h], ...] }   # 该组共用的元素 rect 集合
        ]
      }

    找不到动作时抛错误（含可用的动作名）。
    """
    manifest, sections = _read_checked(project_path)
    action = _find_action(manifest, action_name)
    count = _frame_count(action)
    by_key: dict[tuple, list[int]] = {}
    for index in range(count):
        key = _frame_elements(action, index)
        by_key.setdefault(key, []).append(index)
    groups = []
    for group_id, (key, frames) in enumerate(sorted(
            by_key.items(),
            key=lambda kv: (kv[1][0], kv[0] is None))):
        groups.append({
            "group_id": group_id,
            "frame_count": len(frames),
            "frames": frames,
            "rects": [list(r) for r in key] if key else None,
        })
    return {
        "ok": True,
        "action": action.get("name"),
        "frame_count": count,
        "group_count": len(groups),
        "groups": groups,
    }


@mcp.tool(annotations={
    "title": "逐帧运动轨迹",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
})
def trace_motion(project_path: str, action_name: str) -> dict:
    """逐帧内容中心点轨迹：看动作是不是位移平滑（走路/跳跃循环质量）。

    每帧取内容在画布上的中心（居中摆放 + 逐帧偏移后的实际位置），返回
    中心点序列、相邻帧位移、总位移与包围盒 —— AI 可直接判断循环是否平滑、
    是否有跳变帧。

    参数：
      - project_path (str)：.sstool 工程的本地绝对路径；
      - action_name (str)：动作名称（用 list_actions 查可用名称）。

    返回 dict：
      {
        "ok": true,
        "action": str,
        "frame_count": int,
        "centers": [[cx, cy], ...],          # 逐帧中心（画布坐标，含 float 中点）
        "deltas": [[dx, dy], ...],           # 相邻帧位移（第 0 帧为 [0, 0]）
        "bbox": [min_x, min_y, max_x, max_y],  # 中心点包围盒
        "total_displacement": [dx, dy],      # 首帧到末帧中心位移
      }

    找不到动作时抛错误（含可用的动作名）。
    """
    manifest, sections = _read_checked(project_path)
    action = _find_action(manifest, action_name)
    count = _frame_count(action)
    rects = action.get("frame_rects") or []
    rotations = action.get("frame_rotations") or {}
    offsets = action.get("frame_offsets") or {}
    canvas = _action_canvas(action, sections)
    max_w, max_h = canvas["max_frame"]
    pad = int(action.get("padding") or 0)

    centers: list[list[float]] = []
    for index in range(count):
        rect = tuple(int(v) for v in rects[index]) if index < len(rects) else (0, 0, 0, 0)
        w, h = _rotated_size(rect[2], rect[3],
                             int((rotations.get(str(index), 0) or 0)))
        dx, dy = (offsets.get(str(index)) or [0, 0])
        px = pad + (max_w - w) // 2 + int(dx or 0)
        py = pad + (max_h - h) // 2 + int(dy or 0)
        centers.append([px + w / 2, py + h / 2])
    deltas: list[list[int]] = [[0, 0]]
    for i in range(1, count):
        deltas.append([round(centers[i][0] - centers[i - 1][0]),
                       round(centers[i][1] - centers[i - 1][1])])
    xs = [c[0] for c in centers]
    ys = [c[1] for c in centers]
    bbox = [round(min(xs or [0])), round(min(ys or [0])),
            round(max(xs or [0])), round(max(ys or [0]))] if centers else [0, 0, 0, 0]
    total = [round(centers[-1][0] - centers[0][0]),
             round(centers[-1][1] - centers[0][1])] if count > 1 else [0, 0]
    return {
        "ok": True,
        "action": action.get("name"),
        "frame_count": count,
        "centers": centers,
        "deltas": deltas,
        "bbox": bbox,
        "total_displacement": total,
    }


# ---------------------------------------------------------------------------
# 写回编辑工具（改工程文件 = 破坏性操作：每次都先自动备份原文件）
#    备份：原文件同目录生成 <名>.sstool.bak-<时间戳>，改坏了随时可回退。
# ---------------------------------------------------------------------------
def _backup_then_write(project_path: str, manifest: dict, sections: dict) -> str:
    """写回前把原工程复制成带时间戳的 .bak 备份，再写回；返回备份路径。

    同秒内多次写回会追加 -1/-2 后缀（同一文件绝不覆盖上个备份）。"""
    path = Path(project_path)
    stamp = time.strftime("%Y%m%d%H%M%S")
    bak = path.parent / f"{path.name}.bak-{stamp}"
    n = 1
    while bak.exists():
        bak = path.parent / f"{path.name}.bak-{stamp}-{n}"
        n += 1
    if path.exists():
        shutil.copy2(path, bak)
    write_project(path, manifest, sections)
    return str(bak)


def _remap_frame_fields(action: dict, keep: list[int]) -> None:
    """按帧号重排（删除帧后）：dict 字段按 str(旧号)→str(新号) 重映射，
    列表字段（frame_sections / frame_rects / frame_parts）只留保留帧；
    ref_frame（洋葱皮参照帧）跟着重排、被删则清掉。与软件删帧管道同口径。"""
    new_idx = {old: new for new, old in enumerate(keep)}
    for key in ("frame_offsets", "frame_durations", "frame_rotations",
                "frame_rotation_pivots", "frame_quads", "frame_transform_modes"):
        src = action.get(key) or {}
        remapped = {}
        for k, v in src.items():
            try:
                old = int(k)
            except (TypeError, ValueError):
                continue
            if old in new_idx:
                remapped[str(new_idx[old])] = v
        action[key] = remapped
    for key in ("frame_mirror_h", "frame_mirror_v"):
        src = action.get(key) or []
        action[key] = [new_idx[i] for i in src if i in new_idx]
    for key in ("frame_sections", "frame_rects", "frame_parts"):
        src = action.get(key) or []
        action[key] = [src[old] for old in keep if old < len(src)]
    ref = action.get("ref_frame")
    if isinstance(ref, int) and ref in new_idx:
        action["ref_frame"] = new_idx[ref]
    elif ref is not None:
        action["ref_frame"] = -1


@mcp.tool(annotations={
    "title": "修改动作属性",
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": False,
})
def update_action_meta(
    project_path: str, action_name: str,
    new_name: str | None = None,
    fps: int | None = None,
    loop_mode: str | None = None,
    padding: int | None = None,
    anchor: list | None = None,
    action_type: str | None = None,
    move_direction: str | None = None,
    move_speed: int | None = None,
) -> dict:
    """修改动作的属性并写回工程（自动备份原文件，改错可回退）。

    只改你传了的字段；没传的保持原样。合法值：
      - fps: 1~60（整数）；
      - loop_mode: "loop"（循环）/ "once"（单次）/ "pingpong"（往返）；
      - padding: 0~64（整数）；
      - anchor: 两个数字的列表，如 [0.5, 0.5]（值域 -1~2）；
      - action_type: 字符串（如 walk / run / jump / idle，不强制白名单）；
      - move_direction: "left" / "right" / "up" / "down"；
      - move_speed: 1~998（整数，移动速度，像素/帧）；
      - new_name: 新的动作名（不能与其它动作重名）。

    参数：
      - project_path (str)：.sstool 工程的本地绝对路径；
      - action_name (str)：要改的动作名；
      - 其余参数均为可选；传了才改。

    返回 dict：{ "ok", "action", "backup_path", "changed": {字段: 旧值→新值} }

    找不到动作 / 参数非法时抛错误（含原因）；写入前已备份原文件。
    """
    manifest, sections = _read_checked(project_path)
    action = _find_action(manifest, action_name)
    changed: dict[str, list] = {}

    def _set(field: str, value) -> None:
        old = action.get(field)
        if old != value:
            changed[field] = [old, value]
            action[field] = value

    if fps is not None:
        if not (1 <= int(fps) <= 60):
            raise ValueError(f"fps 必须在 1~60 之间，收到 {fps}。")
        _set("fps", int(fps))
    if loop_mode is not None:
        if loop_mode not in ("loop", "once", "pingpong"):
            raise ValueError(
                f"loop_mode 必须是 loop / once / pingpong，收到「{loop_mode}」。")
        _set("loop_mode", loop_mode)
    if padding is not None:
        if not (0 <= int(padding) <= 64):
            raise ValueError(f"padding 必须在 0~64 之间，收到 {padding}。")
        _set("padding", int(padding))
    if anchor is not None:
        if (not isinstance(anchor, (list, tuple)) or len(anchor) != 2
                or not all(isinstance(v, (int, float)) and -1 <= float(v) <= 2
                           for v in anchor)):
            raise ValueError(f"anchor 需要两个数字（-1~2），收到 {anchor!r}。")
        _set("anchor", [round(float(v), 6) for v in anchor])
    if action_type is not None:
        if not str(action_type).strip():
            raise ValueError("action_type 不能为空。")
        _set("action_type", str(action_type).strip())
    if move_direction is not None:
        if move_direction not in ("left", "right", "up", "down"):
            raise ValueError(
                f"move_direction 必须是 left/right/up/down，收到「{move_direction}」。")
        _set("move_direction", move_direction)
    if move_speed is not None:
        if not (1 <= int(move_speed) <= 998):
            raise ValueError(f"move_speed 必须在 1~998 之间，收到 {move_speed}。")
        _set("move_speed", int(move_speed))
    if new_name is not None:
        if not str(new_name).strip():
            raise ValueError("new_name 不能为空。")
        if (str(new_name).strip() != action_name
                and any((a.get("name") or "") == str(new_name).strip()
                        for a in _actions_of(manifest))):
            raise ValueError(f"已有同名动作「{new_name}」。")
        _set("name", str(new_name).strip())

    backup = _backup_then_write(project_path, manifest, sections)
    return {
        "ok": True,
        "action": action.get("name"),
        "backup_path": backup,
        "changed": changed,
    }


@mcp.tool(annotations={
    "title": "删除动作",
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": False,
})
def delete_action(project_path: str, action_name: str) -> dict:
    """从工程里删除一个动作（含它的全部帧图），并写回工程。

    破坏性操作：写入前会先把原工程备份成 <名>.sstool.bak-<时间戳>，
    改错可以直接用备份文件恢复。合成的合成大图（combined_image）不动，
    其它动作不受影响。

    参数：
      - project_path (str)：.sstool 工程的本地绝对路径；
      - action_name (str)：要删除的动作名（用 list_actions 查可用名称）。

    返回 dict：{ "ok", "deleted": str, "remaining_actions": int,
                 "backup_path": str }

    找不到动作时抛错误（含可用的动作名）。
    """
    manifest, sections = _read_checked(project_path)
    action = _find_action(manifest, action_name)
    for section in (action.get("frame_sections") or []):
        sections.pop(section, None)
    manifest["actions"] = [a for a in _actions_of(manifest)
                           if (a.get("name") or "") != action_name]
    backup = _backup_then_write(project_path, manifest, sections)
    return {
        "ok": True,
        "deleted": action_name,
        "remaining_actions": len(manifest["actions"]),
        "backup_path": backup,
    }


@mcp.tool(annotations={
    "title": "删除动作的若干帧",
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": False,
})
def remove_frames(project_path: str, action_name: str, frames: list) -> dict:
    """从动作里删除指定帧并写回工程（自动备份原文件）。

    与软件里「删除此帧 / 抽帧」同一套重排口径：frame_sections / frame_rects /
    frame_parts 与所有按帧号的字典（偏移 / 时长 / 旋转 / 镜像 / 透视）同步
    重排，被删帧的帧图 PNG 从工程里清除；至少保留一帧。
    **限制**：动作含合成帧（composite_groups）或帧布局（frame_layouts）时
    拒绝删除 —— 那种结构只能在 SpriteSheetTool 里删，避免 headless 重排失真。

    参数：
      - project_path (str)：.sstool 工程的本地绝对路径；
      - action_name (str)：动作名；
      - frames (list[int])：要删除的帧号（从 0 开始，重复/越界会报错）。

    返回 dict：{ "ok", "action", "old_count", "new_count",
                 "removed": [..], "backup_path": str }

    找不到动作 / 帧号非法 / 含合成帧时抛错误（含原因）。
    """
    manifest, sections = _read_checked(project_path)
    action = _find_action(manifest, action_name)
    count = _frame_count(action)
    if action.get("composite_groups"):
        raise ValueError(
            "该动作含合成帧（composite_groups），删除帧请在 SpriteSheetTool 里操作。")
    if action.get("frame_layouts"):
        raise ValueError(
            "该动作含帧布局（frame_layouts），删除帧请在 SpriteSheetTool 里操作。")
    drop = sorted(set(int(f) for f in (frames or [])))
    if drop != sorted(int(f) for f in (frames or [])):
        raise ValueError(f"frames 里有重复项：{frames!r}")
    for index in drop:
        if not (0 <= index < count):
            raise ValueError(
                f"帧号越界：{index}（动作「{action_name}」共 {count} 帧，从 0 开始）。")
    if len(drop) >= count:
        raise ValueError(f"至少要保留 1 帧（动作共 {count} 帧）。")
    keep = [i for i in range(count) if i not in set(drop)]
    for index in drop:
        sections_list = action.get("frame_sections") or []
        if index < len(sections_list):
            sections.pop(sections_list[index], None)
    _remap_frame_fields(action, keep)
    backup = _backup_then_write(project_path, manifest, sections)
    return {
        "ok": True,
        "action": action.get("name"),
        "old_count": count,
        "new_count": len(keep),
        "removed": drop,
        "backup_path": backup,
    }


@mcp.tool(annotations={
    "title": "创建空白工程", "readOnlyHint": False,
    "destructiveHint": True, "idempotentHint": False, "openWorldHint": False,
})
def create_empty_project(project_path: str, width: int = 1, height: int = 1) -> dict:
    """创建可直接被 SpriteSheetTool 打开的空白 .sstool 工程。

    推荐下一步调用 ``create_action``，然后调用 ``add_frame`` 或
    ``add_composite_frame``。不要自行构造 .sstool JSON；动作和帧的内部
    布局字段由服务器维护。
    """
    path = Path(project_path)
    if path.exists():
        raise ValueError(f"目标工程已存在：{path}。请换一个路径。")
    width, height = int(width), int(height)
    if not (1 <= width <= 32768 and 1 <= height <= 32768):
        raise ValueError("width/height 必须在 1~32768 之间。")
    path.parent.mkdir(parents=True, exist_ok=True)
    combined = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    manifest = {
        "format": "spritesheet-tool-project", "version": 1,
        "app_version": __version__, "combined_image": "images/combined.png",
        "source_images": [], "thumbnail": None,
        "viewer": {"frames": [], "frame_logical": {}, "ignored_frames": [],
                    "cleared_frames": [], "source_offsets": []},
        "preview": {"action_index": -1, "frame_index": 0}, "actions": [],
    }
    write_project(path, manifest, {"images/combined.png": png_bytes(combined)})
    return {"ok": True, "project_path": str(path.resolve()), "size": [width, height],
            "next_step": "调用 create_action 创建动作，再调用 add_frame 或 add_composite_frame 添加帧"}


@mcp.tool(annotations={
    "title": "JSON 转工程", "readOnlyHint": False,
    "destructiveHint": True, "idempotentHint": False, "openWorldHint": False,
})
def create_project_from_json(json_path: str, project_path: str) -> dict:
    """把 importer 支持的外部 JSON 和配套图集转换成原生 .sstool 工程。"""
    target = Path(project_path)
    if target.exists():
        raise ValueError(f"目标工程已存在：{target}。请换一个路径。")
    target.parent.mkdir(parents=True, exist_ok=True)
    return _native_from_import(json_path, target)


@mcp.tool(annotations={
    "title": "创建动作", "readOnlyHint": False,
    "destructiveHint": True, "idempotentHint": False, "openWorldHint": False,
})
def create_action(project_path: str, action_name: str, fps: int = 10,
                  loop_mode: str = "loop", padding: int = 0,
                  action_type: str = "static", color: str | None = None) -> dict:
    """向工程追加一个空动作。

    创建动作后必须用 ``add_frame`` 或 ``add_composite_frame`` 添加帧。
    ``frame_layouts_base`` 等字段是工程内部数据，不是此工具的输入，
    不要直接编辑工程清单。
    """
    manifest, sections = _read_checked(project_path)
    name = str(action_name).strip()
    if not name:
        raise ValueError("action_name 不能为空。")
    if any((a.get("name") or "") == name for a in _actions_of(manifest)):
        raise ValueError(f"已有同名动作「{name}」。")
    if not 1 <= int(fps) <= 60:
        raise ValueError("fps 必须在 1~60 之间。")
    if loop_mode not in ("loop", "once", "pingpong"):
        raise ValueError("loop_mode 必须是 loop / once / pingpong。")
    if not 0 <= int(padding) <= 64:
        raise ValueError("padding 必须在 0~64 之间。")
    action = _new_action(name, fps=int(fps), loop_mode=loop_mode,
                         padding=int(padding), action_type=action_type, color=color)
    manifest["actions"] = _actions_of(manifest) + [action]
    backup = _backup_then_write(project_path, manifest, sections)
    return {"ok": True, "action": name, "frame_count": 0, "backup_path": backup,
            "next_step": "调用 add_frame 或 add_composite_frame 添加动作帧"}


@mcp.tool(annotations={
    "title": "添加动作帧", "readOnlyHint": False,
    "destructiveHint": True, "idempotentHint": False, "openWorldHint": False,
})
def add_frame(project_path: str, action_name: str, image_path: str,
              index: int | None = None, duration_ms: int | None = None,
              offset: list | None = None) -> dict:
    """把本地 PNG 追加/插入动作，并把像素追加进工程合成图集。

    这是 ``create_action`` 之后的标准加帧步骤；服务器会自动维护动作
    的 frame_sections、frame_parts 和内部布局字段。
    """
    manifest, sections = _read_checked(project_path)
    action = _find_action(manifest, action_name)
    image = _load_rgba(image_path)
    count = _frame_count(action)
    target = _insert_index(count, index)
    rect = _append_to_combined(manifest, sections, image)
    ai = len(_actions_of(manifest)) - 1
    section = _next_section(sections, f"actions/{ai:04d}/frame_")
    sections[section] = png_bytes(image)
    _shift_frame_fields(action, target, 1)
    action["frame_sections"].insert(target, section)
    action["frame_rects"].insert(target, list(rect))
    action["frame_parts"].insert(target, [[list(rect), 0, 0, rect[0], rect[1]]])
    if duration_ms is not None:
        if int(duration_ms) <= 0:
            raise ValueError("duration_ms 必须大于 0。")
        action["frame_durations"][str(target)] = int(duration_ms)
    if offset is not None:
        if not isinstance(offset, (list, tuple)) or len(offset) != 2:
            raise ValueError("offset 必须是 [x, y]。")
        action["frame_offsets"][str(target)] = [int(offset[0]), int(offset[1])]
    backup = _backup_then_write(project_path, manifest, sections)
    return {"ok": True, "action": action_name, "index": target,
            "rect": list(rect), "frame_count": _frame_count(action),
            "backup_path": backup}


@mcp.tool(annotations={
    "title": "复制动作帧", "readOnlyHint": False,
    "destructiveHint": True, "idempotentHint": False, "openWorldHint": False,
})
def duplicate_frame(project_path: str, action_name: str, frame_index: int,
                    insert_index: int | None = None) -> dict:
    """复制一帧的像素和全部逐帧参数。"""
    manifest, sections = _read_checked(project_path)
    action = _find_action(manifest, action_name)
    count = _frame_count(action)
    source = int(frame_index)
    if not 0 <= source < count:
        raise ValueError(f"帧号越界：{source}（共 {count} 帧）。")
    if action.get("composite_groups") and action.get("frame_layouts") is None:
        raise ValueError(
            "该动作使用 composite_groups 生成帧，但没有显式 frame_layouts；"
            "为避免复制后元素帧映射失真，请先在 SpriteSheetTool 中转换为显式布局。"
        )
    target = _insert_index(count, insert_index)
    old_section = (action.get("frame_sections") or [])[source]
    new_section = _next_section(sections, f"actions/{len(_actions_of(manifest)) - 1:04d}/frame_")
    sections[new_section] = sections.get(old_section, b"")
    _shift_frame_fields(action, target, 1)
    for key in ("frame_sections", "frame_rects", "frame_parts"):
        values = action.get(key) or []
        values.insert(target, values[source])
    action["frame_sections"][target] = new_section
    _copy_frame_fields(action, source + (1 if source >= target else 0), target)
    layouts = action.get("frame_layouts")
    if layouts is not None:
        source_layout = source + 1 if source >= target else source
        layouts.insert(target, list(layouts[source_layout]) if source_layout < len(layouts) else [])
        base = int(action.get("frame_layouts_base") or 0)
        if target < base:
            action["frame_layouts_base"] = base + 1
    backup = _backup_then_write(project_path, manifest, sections)
    return {"ok": True, "action": action_name, "source_index": source,
            "index": target, "frame_count": _frame_count(action),
            "backup_path": backup}


@mcp.tool(annotations={
    "title": "重排动作帧", "readOnlyHint": False,
    "destructiveHint": True, "idempotentHint": False, "openWorldHint": False,
})
def reorder_frames(project_path: str, action_name: str, order: list) -> dict:
    """按 order 重排帧，并同步偏移、时长、变换、镜像和参考帧。"""
    manifest, sections = _read_checked(project_path)
    action = _find_action(manifest, action_name)
    _reorder_action(action, [int(i) for i in order])
    backup = _backup_then_write(project_path, manifest, sections)
    return {"ok": True, "action": action_name, "order": [int(i) for i in order],
            "backup_path": backup}


@mcp.tool(annotations={
    "title": "修改逐帧参数", "readOnlyHint": False,
    "destructiveHint": True, "idempotentHint": False, "openWorldHint": False,
})
def update_frame(project_path: str, action_name: str, frame_index: int,
                 duration_ms: int | None = None, offset: list | None = None,
                 rotation: int | None = None, mirror_h: bool | None = None,
                 mirror_v: bool | None = None, quad: list | None = None,
                 clear_quad: bool = False, rotation_pivot: list | None = None,
                 transform_modes: list | None = None) -> dict:
    """修改软件工程中一帧的全部可序列化变换参数。"""
    manifest, sections = _read_checked(project_path)
    action = _find_action(manifest, action_name)
    index = int(frame_index)
    count = _frame_count(action)
    if not 0 <= index < count:
        raise ValueError(f"帧号越界：{index}（共 {count} 帧）。")
    key = str(index)
    changed = []
    if duration_ms is not None:
        if int(duration_ms) <= 0:
            raise ValueError("duration_ms 必须大于 0。")
        action["frame_durations"][key] = int(duration_ms); changed.append("duration_ms")
    if offset is not None:
        if len(offset) != 2:
            raise ValueError("offset 必须是 [x, y]。")
        action["frame_offsets"][key] = [int(offset[0]), int(offset[1])]; changed.append("offset")
    if rotation is not None:
        action["frame_rotations"][key] = int(rotation) % 360; changed.append("rotation")
    for field, value in (("frame_mirror_h", mirror_h), ("frame_mirror_v", mirror_v)):
        if value is not None:
            values = action[field] or []
            if bool(value) and index not in values:
                values.append(index)
            elif not bool(value) and index in values:
                values.remove(index)
            action[field] = sorted(values); changed.append(field)
    if clear_quad:
        action["frame_quads"].pop(key, None); changed.append("quad")
    elif quad is not None:
        if len(quad) != 8 or not all(isinstance(v, (int, float)) for v in quad):
            raise ValueError("quad 必须是 8 个数字：[x0,y0,x1,y1,x2,y2,x3,y3]。")
        action["frame_quads"][key] = [float(v) for v in quad]; changed.append("quad")
    if rotation_pivot is not None:
        if len(rotation_pivot) != 2:
            raise ValueError("rotation_pivot 必须是 [x, y]。")
        action["frame_rotation_pivots"][key] = [float(rotation_pivot[0]), float(rotation_pivot[1])]
        changed.append("rotation_pivot")
    if transform_modes is not None:
        action["frame_transform_modes"][key] = sorted({str(v) for v in transform_modes})
        changed.append("transform_modes")
    backup = _backup_then_write(project_path, manifest, sections)
    return {"ok": True, "action": action_name, "frame_index": index,
            "changed": changed, "backup_path": backup}


@mcp.tool(annotations={
    "title": "添加源图", "readOnlyHint": False,
    "destructiveHint": True, "idempotentHint": False, "openWorldHint": False,
})
def add_source_image(project_path: str, image_path: str, index: int | None = None) -> dict:
    """追加一张源图，写入 source_images 并拼到 combined 图集底部。"""
    manifest, sections = _read_checked(project_path)
    image = _load_rgba(image_path)
    rect = _append_to_combined(manifest, sections, image)
    name = _next_section(sections, "images/source_")
    sections[name] = png_bytes(image)
    sources = list(manifest.get("source_images") or [])
    target = _insert_index(len(sources), index)
    sources.insert(target, name)
    manifest["source_images"] = sources
    viewer = manifest.setdefault("viewer", {})
    offsets = list(viewer.get("source_offsets") or [])
    if len(offsets) != len(sources) - 1:
        offsets = [[0, 0] for _ in range(len(sources) - 1)]
    offsets.insert(target, [rect[0], rect[1]])
    viewer["source_offsets"] = offsets
    backup = _backup_then_write(project_path, manifest, sections)
    return {"ok": True, "source": name, "index": target, "rect": list(rect),
            "source_count": len(sources), "backup_path": backup}


@mcp.tool(annotations={
    "title": "移除源图", "readOnlyHint": False,
    "destructiveHint": True, "idempotentHint": False, "openWorldHint": False,
})
def remove_source_image(project_path: str, index: int) -> dict:
    """从 source_images 列表移除一张源图；合成图集像素和帧引用保持不变。"""
    manifest, sections = _read_checked(project_path)
    sources = list(manifest.get("source_images") or [])
    index = int(index)
    if not 0 <= index < len(sources):
        raise ValueError(f"源图序号越界：{index}（共 {len(sources)} 张）。")
    name = sources.pop(index)
    if name == manifest.get("combined_image"):
        raise ValueError("不能移除 combined_image 合成图本身。")
    manifest["source_images"] = sources
    viewer = manifest.setdefault("viewer", {})
    offsets = list(viewer.get("source_offsets") or [])
    if len(offsets) != len(sources) + 1:
        offsets = [[0, 0] for _ in range(len(sources) + 1)]
    if index < len(offsets):
        offsets.pop(index)
    viewer["source_offsets"] = offsets
    if not any(name == a for a in sources) and not any(name in (a.get("frame_sections") or [])
                                                       for a in _actions_of(manifest)):
        sections.pop(name, None)
    backup = _backup_then_write(project_path, manifest, sections)
    return {"ok": True, "removed": name, "index": index,
            "source_count": len(sources), "backup_path": backup}


@mcp.tool(annotations={
    "title": "移动源图", "readOnlyHint": False,
    "destructiveHint": True, "idempotentHint": False, "openWorldHint": False,
})
def move_source_image(project_path: str, index: int, new_index: int) -> dict:
    """调整 source_images 顺序，并同步 viewer.source_offsets。"""
    manifest, sections = _read_checked(project_path)
    sources = list(manifest.get("source_images") or [])
    index, new_index = int(index), int(new_index)
    if not 0 <= index < len(sources) or not 0 <= new_index < len(sources):
        raise ValueError(f"源图序号必须在 0~{len(sources) - 1} 之间。")
    item = sources.pop(index); sources.insert(new_index, item)
    manifest["source_images"] = sources
    viewer = manifest.setdefault("viewer", {})
    offsets = list(viewer.get("source_offsets") or [])
    if len(offsets) != len(sources):
        offsets = [[0, 0] for _ in sources]
    value = offsets.pop(index); offsets.insert(new_index, value)
    viewer["source_offsets"] = offsets
    backup = _backup_then_write(project_path, manifest, sections)
    return {"ok": True, "source": item, "index": new_index,
            "source_count": len(sources), "backup_path": backup}


@mcp.tool(annotations={
    "title": "创建合成帧", "readOnlyHint": False,
    "destructiveHint": True, "idempotentHint": False, "openWorldHint": False,
})
def add_composite_frame(project_path: str, action_name: str, parts: list,
                        index: int | None = None, canvas_size: list | None = None) -> dict:
    """用多个本地图片按 x/y 合成一帧，并保留各部件的图集 rect/位置。

    先用 ``create_action`` 创建动作；每个 part 需要 ``image_path``、``x``、
    ``y``，可选 ``canvas_size`` 限制合成画布。内部 frame_parts/layouts 字段
    由服务器自动维护，不需要客户端拼写。
    """
    manifest, sections = _read_checked(project_path)
    action = _find_action(manifest, action_name)
    if not parts:
        raise ValueError("parts 不能为空。每项需要 image_path、x、y。")
    loaded = []
    max_w = max_h = 0
    for item in parts:
        if not isinstance(item, dict) or not item.get("image_path"):
            raise ValueError("每个 part 必须包含 image_path。")
        image = _load_rgba(item["image_path"])
        x, y = int(item.get("x", 0)), int(item.get("y", 0))
        if x < 0 or y < 0:
            raise ValueError("合成帧部件 x/y 不能为负数。")
        loaded.append((image, x, y))
        max_w = max(max_w, x + image.width); max_h = max(max_h, y + image.height)
    if canvas_size is not None:
        if len(canvas_size) != 2:
            raise ValueError("canvas_size 必须是 [width, height]。")
        max_w, max_h = int(canvas_size[0]), int(canvas_size[1])
    if not (1 <= max_w <= 32768 and 1 <= max_h <= 32768):
        raise ValueError("合成帧画布尺寸必须在 1~32768 之间。")
    composed = Image.new("RGBA", (max_w, max_h), (0, 0, 0, 0))
    frame_parts = []
    for image, x, y in loaded:
        if x + image.width > max_w or y + image.height > max_h:
            raise ValueError("部件超出 canvas_size。")
        composed.alpha_composite(image, (x, y))
        rect = _append_to_combined(manifest, sections, image)
        frame_parts.append([list(rect), x, y, rect[0], rect[1]])
    count = _frame_count(action); target = _insert_index(count, index)
    ai = _actions_of(manifest).index(action)
    section = _next_section(sections, f"actions/{ai:04d}/frame_")
    sections[section] = png_bytes(composed)
    rect = _append_to_combined(manifest, sections, composed)
    _shift_frame_fields(action, target, 1)
    action["frame_sections"].insert(target, section)
    action["frame_rects"].insert(target, list(rect))
    action["frame_parts"].insert(target, frame_parts)
    backup = _backup_then_write(project_path, manifest, sections)
    return {"ok": True, "action": action_name, "index": target,
            "part_count": len(frame_parts), "rect": list(rect),
            "frame_count": _frame_count(action), "backup_path": backup,
            "next_step": "继续调用 add_composite_frame 或 add_frame 添加帧；完成后可调用 validate_project 检查"}


@mcp.tool(annotations={
    "title": "工程抠图", "readOnlyHint": False,
    "destructiveHint": True, "idempotentHint": False, "openWorldHint": False,
})
def key_project_background(project_path: str, mode_id: str = "traditional",
                           scope: str = "all", bg_color: list | None = None,
                           cartoon_strength: str = "balanced",
                           cartoon_edge_type: str = "auto", params: dict | None = None) -> dict:
    """对普通序列帧工程中的合成图、源图和帧图执行基础色键或卡通 profile。

    ``mode_id`` 只能是 ``traditional`` 或 ``cartoon``；``scope`` 是
    ``combined``、``sources``、``frames``、``all`` 之一。工具会自动备份，
    不需要客户端直接修改 PNG section 或工程清单。
    """
    manifest, sections = _read_checked(project_path)
    if mode_id not in {p.id for p in keying_modes.MODE_PROFILES}:
        raise ValueError(f"未知抠图模式：{mode_id}。可选模式会由 MODE_PROFILES 提供。")
    if scope not in ("combined", "frames", "sources", "all"):
        raise ValueError("scope 必须是 combined / frames / sources / all。")
    common = dict(params or {})
    common.update({"cartoon_strength": cartoon_strength, "cartoon_edge_type": cartoon_edge_type})
    colors = tuple(int(v) for v in bg_color) if bg_color is not None else None
    names: list[str] = []
    if scope in ("combined", "all") and manifest.get("combined_image"):
        names.append(manifest["combined_image"])
    if scope in ("sources", "all"):
        names.extend(manifest.get("source_images") or [])
    if scope in ("frames", "all"):
        for action in _actions_of(manifest):
            names.extend(action.get("frame_sections") or [])
    changed = []
    stats = []
    for name in dict.fromkeys(names):
        image = png_from_bytes(sections.get(name, b""))
        if image is None:
            continue
        color = colors or keying.estimate_bg_color(image)
        if color is None:
            continue
        result, one = keying_modes.key_image(image, color, mode_id=mode_id, **common)
        sections[name] = png_bytes(result)
        changed.append(name)
        stats.append({"section": name, "changed_pct": float(one.get("changed_pct", 0.0)),
                      "bg_color": list(one.get("bg_color", color))})
    backup = _backup_then_write(project_path, manifest, sections)
    return {"ok": True, "mode_id": mode_id, "scope": scope,
            "changed_sections": changed, "stats": stats, "backup_path": backup}


def _native_json_payload(manifest: dict, sections: dict[str, bytes], atlas_name: str) -> dict:
    atlas = _combined_image(manifest, sections)
    animations = []
    for action in _actions_of(manifest):
        canvas = _action_canvas(action, sections)
        anchor = action.get("anchor") or [0.5, 0.5]
        frames = []
        rects = action.get("frame_rects") or []
        parts = action.get("frame_parts") or []
        offsets = action.get("frame_offsets") or {}
        durations = action.get("frame_durations") or {}
        fps = int(action.get("fps") or 10)
        for index in range(_frame_count(action)):
            raw_parts = parts[index] if index < len(parts) else []
            native_parts = []
            for part in raw_parts:
                rect = part[0]
                native_parts.append({"rect": {"x": int(rect[0]), "y": int(rect[1]),
                                                "width": int(rect[2]), "height": int(rect[3])},
                                     "pos": {"x": int(part[1]), "y": int(part[2])}})
            if not native_parts and index < len(rects):
                rect = rects[index]
                native_parts = [{"rect": {"x": int(rect[0]), "y": int(rect[1]),
                                            "width": int(rect[2]), "height": int(rect[3])},
                                 "pos": {"x": 0, "y": 0}}]
            dx, dy = offsets.get(str(index), [0, 0])
            frames.append({"parts": native_parts,
                           "offset": {"x": int(dx), "y": int(dy)},
                           "duration_ms": int(durations.get(str(index), max(1, round(1000 / max(1, fps))))),
                           "rotation": int((action.get("frame_rotations") or {}).get(str(index), 0) or 0),
                           "mirror_h": index in (action.get("frame_mirror_h") or []),
                           "mirror_v": index in (action.get("frame_mirror_v") or [])})
        animations.append({"name": action.get("name"), "fps": fps,
                           "loop": action.get("loop_mode") or "loop",
                           "anchor": {"normalized": {"x": float(anchor[0]), "y": float(anchor[1])}},
                           "canvas": {"width": canvas["canvas"][0], "height": canvas["canvas"][1],
                                      "padding": int(action.get("padding") or 0)},
                           "ground": {"enabled": bool(action.get("ground_enabled")),
                                      "offset": int(action.get("ground_offset") or 0)},
                           "type": action.get("action_type") or "static",
                           "move": {"direction": action.get("move_direction"),
                                    "speed": int(action.get("move_speed") or 0)},
                           "color": action.get("color"), "frames": frames})
    return {"format": "spritesheet-animation", "version": 1,
            "atlas": {"image": atlas_name, "width": atlas.width, "height": atlas.height},
            "animations": animations}


def _build_image_sequence_atlas(images: list[Image.Image], gap: int = 1):
    """Pack standalone rendered frames vertically for document exports."""
    if not images:
        raise ValueError("没有可导出的帧。")
    width = max(image.width for image in images)
    height = sum(image.height for image in images) + max(0, len(images) - 1) * gap
    atlas = Image.new("RGBA", (max(1, width), max(1, height)), (0, 0, 0, 0))
    records = []
    y = 0
    for image in images:
        image = image.convert("RGBA")
        atlas.alpha_composite(image, (0, y))
        records.append((0, y, image.width, image.height))
        y += image.height + gap
    return atlas, records


def _native_json_payload_from_images(images: list[Image.Image], durations: list[int],
                                     atlas_name: str) -> tuple[dict, Image.Image]:
    atlas, rects = _build_image_sequence_atlas(images)
    max_w = max(image.width for image in images)
    max_h = max(image.height for image in images)
    frames = []
    for index, rect in enumerate(rects):
        x, y, width, height = rect
        frames.append({
            "parts": [{"rect": {"x": x, "y": y, "width": width, "height": height},
                       "pos": {"x": 0, "y": 0}}],
            "offset": {"x": 0, "y": 0},
            "duration_ms": int(durations[index] if index < len(durations) else 100),
            "rotation": 0, "mirror_h": False, "mirror_v": False,
        })
    payload = {
        "format": "spritesheet-animation", "version": 1,
        "atlas": {"image": atlas_name, "width": atlas.width, "height": atlas.height},
        "animations": [{
            "name": "animation", "fps": 10, "loop": "loop",
            "anchor": {"normalized": {"x": 0.5, "y": 0.5}},
            "canvas": {"width": max_w, "height": max_h, "padding": 0},
            "ground": {"enabled": False, "offset": 0},
            "type": "static", "move": {"direction": "right", "speed": 0},
            "color": None, "frames": frames,
        }],
    }
    return payload, atlas


def _engine_payload_from_images(images: list[Image.Image], durations: list[int],
                                fmt: str, atlas_name: str) -> tuple[dict, Image.Image]:
    atlas, rects = _build_image_sequence_atlas(images)
    action = {
        "name": "animation", "fps": 10, "loop_mode": "loop",
        "frame_durations": {str(i): int(v) for i, v in enumerate(durations)},
    }
    shell = _ActionShell(action)
    shell.frames = [None] * len(images)
    records = {}
    for index, rect in enumerate(rects):
        width, height = rect[2], rect[3]
        records[(shell, index)] = {
            "rect": rect, "composite_rect": rect, "offset": (0, 0),
            "source": (width, height), "canvas": (width, height),
            "trimmed": False,
        }
    payload = exporters.build_payload(
        fmt, [shell], records, atlas_name=atlas_name,
        atlas_w=atlas.width, atlas_h=atlas.height,
        app_label=f"{__app_name__} v{__version__}",
    )
    return payload, atlas


@mcp.tool(annotations={
    "title": "导出工程资源", "readOnlyHint": False,
    "destructiveHint": False, "idempotentHint": True, "openWorldHint": False,
})
def export_project_asset(project_path: str, kind: str, out_dir: str,
                         action_name: str | None = None) -> dict:
    """统一导出入口：PNG、GIF、工程 JSON、引擎 JSON、.sstool 和逐帧 PNG。

    ``kind`` 可用：``png``、``gif``、``native_json``、``tp_hash``、
    ``tp_array``、``aseprite_array``、``sstool``、``frames_png``、
    ``separate``。GIF/逐帧 PNG 可用 ``action_name`` 限定动作，省略则导出全部动作。
    """
    manifest, sections = _read_any_project(project_path)
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    stem = Path(project_path).stem
    kind = str(kind).strip().lower()
    results: list[str] = []
    if manifest.get("anim_doc"):
        payload = manifest["anim_doc"]
        images = [_render_anim_doc_frame(manifest, sections, i)
                  for i in range(len(payload.get("frames") or []))]
        if not images:
            raise ValueError("动图文档没有可导出的帧。")
        durations = [int(v) for v in (payload.get("durations") or [])]
        durs = [durations[i] if i < len(durations) else 100 for i in range(len(images))]
        if kind == "sstool":
            path = out / f"{stem}.sstool"
            if path.resolve() != Path(project_path).resolve():
                shutil.copy2(project_path, path)
            results.append(str(path.resolve()))
        elif kind == "png":
            path = out / f"{stem}.png"; path.write_bytes(png_bytes(images[0])); results.append(str(path.resolve()))
        elif kind in ("native_json", "native", "tp_hash", "tp_array", "aseprite_array", "aseprite"):
            normalized = "native_json" if kind in ("native_json", "native") else kind
            if normalized == "aseprite":
                normalized = "aseprite_array"
            atlas_name = f"{stem}.png"
            if normalized == "native_json":
                payload_out, atlas = _native_json_payload_from_images(images, durs, atlas_name)
                json_path = out / f"{stem}.json"
            else:
                payload_out, atlas = _engine_payload_from_images(images, durs, normalized, atlas_name)
                json_path = out / f"{stem}{exporters.FORMAT_SUFFIX[normalized]}.json"
            json_path.write_text(json.dumps(payload_out, ensure_ascii=False, indent=2), encoding="utf-8")
            atlas_path = out / atlas_name; atlas_path.write_bytes(png_bytes(atlas))
            results.extend([str(json_path.resolve()), str(atlas_path.resolve())])
        elif kind in ("gif", "frames_png", "separate"):
            if kind in ("gif", "separate"):
                path = out / f"{stem}.gif"
                images[0].save(path, save_all=True, append_images=images[1:], duration=durs,
                                loop=0, disposal=2)
                results.append(str(path.resolve()))
            if kind in ("frames_png", "separate"):
                folder = out / f"{stem}_frames"; folder.mkdir(exist_ok=True)
                for index, image in enumerate(images):
                    path = folder / f"frame_{index:04d}.png"; path.write_bytes(png_bytes(image)); results.append(str(path.resolve()))
        else:
            raise ValueError(
                "动图文档支持的 kind 是 png / gif / native_json / tp_hash / tp_array / "
                "aseprite_array / sstool / frames_png / separate。"
            )
        return {"ok": True, "kind": kind, "anim_doc": True, "files": results}
    if kind == "png":
        path = out / f"{stem}.png"; path.write_bytes(png_bytes(_combined_image(manifest, sections))); results.append(str(path.resolve()))
    elif kind == "sstool":
        path = out / f"{stem}.sstool"
        if path.resolve() != Path(project_path).resolve():
            shutil.copy2(project_path, path)
        results.append(str(path.resolve()))
    elif kind == "native_json":
        path = out / f"{stem}.json"
        path.write_text(json.dumps(_native_json_payload(manifest, sections, f"{stem}.png"), ensure_ascii=False, indent=2), encoding="utf-8")
        atlas = out / f"{stem}.png"; atlas.write_bytes(png_bytes(_combined_image(manifest, sections)))
        results.extend([str(path.resolve()), str(atlas.resolve())])
    elif kind in ("native", "tp_hash", "tp_array", "aseprite_array", "aseprite"):
        normalized = "native_json" if kind == "native" else kind
        if normalized == "aseprite":
            normalized = "aseprite_array"
        if normalized == "native_json":
            path = out / f"{stem}.json"
            path.write_text(
                json.dumps(_native_json_payload(manifest, sections, f"{stem}.png"),
                           ensure_ascii=False, indent=2), encoding="utf-8"
            )
            atlas = out / f"{stem}.png"; atlas.write_bytes(png_bytes(_combined_image(manifest, sections)))
            results.extend([str(path.resolve()), str(atlas.resolve())])
        else:
            json_path = Path(export_to_engine_json(project_path, normalized, str(out)))
            atlas_path = out / f"{stem}.png"
            results.extend([str(json_path.resolve()), str(atlas_path.resolve())])
    elif kind in ("gif", "frames_png", "separate"):
        actions = [_find_action(manifest, action_name)] if action_name else _actions_of(manifest)
        if not actions:
            raise ValueError("工程没有可导出的动作。")
        for action in actions:
            safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(action.get("name") or "action")) or "action"
            images = [_render_frame_image(action, sections, i, manifest)[0]
                      for i in range(_frame_count(action))]
            if not images:
                continue
            durations = action.get("frame_durations") or {}
            fps = int(action.get("fps") or 10)
            durs = [int(durations.get(str(i), max(1, round(1000 / max(1, fps)))))
                    for i in range(len(images))]
            if kind in ("gif", "separate"):
                path = out / f"{stem}_{safe}.gif"
                images[0].save(path, save_all=True, append_images=images[1:], duration=durs,
                                loop=0 if action.get("loop_mode") != "once" else 1, disposal=2)
                results.append(str(path.resolve()))
            if kind in ("frames_png", "separate"):
                folder = out / f"{stem}_{safe}"
                folder.mkdir(exist_ok=True)
                for index, image in enumerate(images):
                    path = folder / f"frame_{index:04d}.png"; path.write_bytes(png_bytes(image)); results.append(str(path.resolve()))
    else:
        raise ValueError(
            "kind 必须是 png / gif / native_json / tp_hash / tp_array / aseprite_array / "
            "sstool / frames_png / separate。"
        )
    return {"ok": True, "kind": kind, "files": results}


@mcp.tool(annotations={
    "title": "读取动图文档", "readOnlyHint": True,
    "destructiveHint": False, "idempotentHint": True, "openWorldHint": False,
})
def read_animation_document(project_path: str) -> dict:
    """读取 anim_doc 工程，不再把它当普通序列帧工程拒绝。"""
    manifest, sections = _read_any_project(project_path)
    payload = manifest.get("anim_doc")
    if not payload:
        raise ValueError("这不是动图文档工程（manifest.anim_doc 缺失）。")
    frames = payload.get("frames") or []
    return {"ok": True, "project": str(Path(project_path).resolve()),
            "name": payload.get("name"), "frame_count": len(frames),
            "durations": [int(v) for v in (payload.get("durations") or [])],
            "current_index": int(payload.get("index", 0) or 0),
            "operations": [{"kind": op.get("kind"), "label": op.get("label")} for op in (payload.get("ops") or [])],
            "section_names": sorted(sections)}


@mcp.tool(annotations={
    "title": "提取动图文档帧", "readOnlyHint": True,
    "destructiveHint": False, "idempotentHint": True, "openWorldHint": False,
})
def extract_animation_frame_png(project_path: str, frame_index: int) -> dict:
    """提取动图文档指定帧并重放可序列化操作链。"""
    manifest, sections = _read_any_project(project_path)
    if not manifest.get("anim_doc"):
        raise ValueError("这不是动图文档工程。")
    frame_index = int(frame_index)
    image = _render_anim_doc_frame(manifest, sections, frame_index)
    durations = manifest["anim_doc"].get("durations") or []
    return {"ok": True, "frame_index": int(frame_index),
            "width": image.width, "height": image.height,
            "duration_ms": int(durations[frame_index] if frame_index < len(durations) else 100),
            "png_base64": base64.b64encode(png_bytes(image)).decode("ascii")}


@mcp.tool(annotations={
    "title": "修改动图文档", "readOnlyHint": False,
    "destructiveHint": True, "idempotentHint": False, "openWorldHint": False,
})
def update_animation_document(project_path: str, operation: dict) -> dict:
    """向 anim_doc 操作链追加可序列化操作并写回工程。

    operation 形状：``{"kind":"transform","t":"flip_h|flip_v|rot90|rot180|rot270"}``；
    ``{"kind":"stroke","tool":"brush|eraser","segs":[[x1,y1,x2,y2]],"size":4}``；
    或 ``{"kind":"key","mode_id":"traditional|cartoon","color":[r,g,b],"params":{}}``。
    """
    manifest, sections = _read_any_project(project_path)
    payload = manifest.get("anim_doc")
    if not payload:
        raise ValueError("这不是动图文档工程。")
    if not isinstance(operation, dict):
        raise ValueError("operation 必须是对象。")
    kind = str(operation.get("kind", ""))
    allowed = {"transform", "stroke", "key"}
    if kind not in allowed:
        raise ValueError("operation.kind 必须是 transform / stroke / key。")
    op = dict(operation)
    if kind == "transform" and str(op.get("t")) not in {"flip_h", "flip_v", "rot90", "rot180", "rot270"}:
        raise ValueError("transform.t 必须是 flip_h / flip_v / rot90 / rot180 / rot270。")
    if kind == "stroke":
        if str(op.get("tool", "brush")) not in ("brush", "eraser"):
            raise ValueError("stroke.tool 必须是 brush 或 eraser。")
        segs = op.get("segs")
        if not isinstance(segs, list) or not segs:
            raise ValueError("stroke.segs 不能为空。")
        normalized_segs = []
        for index, segment in enumerate(segs):
            if not isinstance(segment, (list, tuple)) or len(segment) != 4:
                raise ValueError(f"stroke.segs[{index}] 必须是 [x1, y1, x2, y2]。")
            if not all(isinstance(value, numbers.Real) and math.isfinite(float(value))
                       for value in segment):
                raise ValueError(f"stroke.segs[{index}] 的坐标必须是有限数字。")
            normalized_segs.append([float(value) for value in segment])
        op["segs"] = normalized_segs
        op["size"] = max(1, int(op.get("size", 1)))
    if kind == "key":
        params = dict(op.get("params") or {})
        op["params"] = params
        mode_id = str(op.get("mode_id", "traditional"))
        if mode_id not in {p.id for p in keying_modes.MODE_PROFILES}:
            raise ValueError(f"未知抠图模式：{mode_id}")
        color = op.get("color")
        if color is not None and len(color) != 3:
            raise ValueError("key.color 必须是 [r, g, b]。")
    payload.setdefault("ops", []).append(op)
    backup = _backup_then_write(project_path, manifest, sections)
    return {"ok": True, "operation_index": len(payload["ops"]) - 1,
            "kind": kind, "backup_path": backup}


@mcp.tool(annotations={
    "title": "自动对齐", "readOnlyHint": False,
    "destructiveHint": False, "idempotentHint": False, "openWorldHint": False,
})
def auto_align_action(project_path: str, action_name: str,
                      allow_estimate: bool = False) -> dict:
    """自动算出逐帧偏移并写回，让整条动画的落点对齐（与软件「自动对齐」同一套算法）。

    用户口径：**只改帧的偏移数据** —— 锚点、padding、帧图像、尺寸一概不碰。
    三条路，自己按数据判断走哪条：
      - **均分格**（首选，精确）：精灵图本身横竖均分 → 每格就是"正常那一帧"的框，
        紧切框在格里的落点差就是误差；
      - **精确**：原点信息够时按「原图锚点 − 紧切框中心」把每帧放回原位置；
      - **估算**：原图已经是紧切小图 / 带变换 / 多元素合成时，只能按帧间重合度猜。
        **默认不做**（可能不准）；要跑请显式传 allow_estimate=True。

    参数：
      - project_path (str)：.sstool 工程绝对路径；
      - action_name (str)：目标动作名；
      - allow_estimate (bool)：是否允许走"估算"那条路，默认 False。

    返回 dict：{ "ok", "action", "method", "offsets_changed", "backup_path", "note" }
    判不出均分且未开估算时返回 method="none" 并说明原因（不写回、不备份）。
    """
    manifest, sections = _read_any_project(project_path)
    action = _find_action(manifest, action_name)
    total = _frame_count(action)
    if total < 2:
        raise ValueError(f"动作「{action_name}」只有 {total} 帧，至少要两帧才能对齐。")
    if not manifest.get("combined_image"):
        return {"ok": False, "action": action_name, "method": "none",
                "offsets_changed": 0, "backup_path": "",
                "note": "工程没有合成图，拿不到像素，无法对齐。"}

    offsets = None
    method = ""
    note = ""
    grid = _align_offsets_grid(action, sections, manifest)
    if grid is not None:
        offsets, method = grid[0], "grid"
        note = "均分格：每格就是正常那一帧的框，差值即误差（不是估的）"
    if offsets is None:
        try:
            offsets = _align_offsets_estimate(action, sections, manifest)
            method = "estimate"
            note = "估算：按帧间重合度对齐，可能不准，建议人工核对"
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "action": action_name, "method": "none",
                    "offsets_changed": 0, "backup_path": "",
                    "note": f"算不出对齐偏移：{exc}"}
        if not allow_estimate:
            return {"ok": False, "action": action_name, "method": "none",
                    "offsets_changed": 0, "backup_path": "",
                    "note": ("判不出「均分格」且原图信息不足，只能估算；"
                             "若接受可能不准，请传 allow_estimate=True 重试。")}

    old = dict(action.get("frame_offsets") or {})
    merged = dict(old)
    changed = 0
    for index, value in (offsets or {}).items():
        pair = [int(value[0]), int(value[1])]
        if pair == [0, 0]:
            merged.pop(str(index), None)
        else:
            merged[str(index)] = pair
        if old.get(str(index)) != merged.get(str(index)):
            changed += 1
    if changed == 0:
        return {"ok": True, "action": action_name, "method": method,
                "offsets_changed": 0, "backup_path": "",
                "note": "这些帧本来就在该在的位置上，偏移没变"}
    action["frame_offsets"] = merged
    backup = _backup_then_write(project_path, manifest, sections)
    return {"ok": True, "action": action_name, "method": method,
            "offsets_changed": changed, "backup_path": backup, "note": note}


@mcp.tool(annotations={
    "title": "绘制 / 擦除帧", "readOnlyHint": False,
    "destructiveHint": True, "idempotentHint": False, "openWorldHint": False,
})
def draw_on_frames(project_path: str, action_name: str, operation: dict) -> dict:
    """在动作的帧上画线或擦除（与软件画笔/橡皮同一口径：直接改帧像素，写前自动备份）。

    operation 形状：``{"kind":"brush"|"eraser","segs":[[x1,y1,x2,y2]],
    "size":4,"scope":"current"|"all","frames":[0,2]}``

      - ``kind``："brush" 画不透明前景色，"eraser" 把笔画擦成透明；
      - ``segs``：**画布坐标系**下线段的端点列表（与软件预览画布同一坐标系）；
      - ``size``：笔宽（像素，默认 4，至少 1）；
      - ``scope``："current" 只用 ``frames`` 指定的帧；"all" 作用于全部帧（默认）；
      - ``frames``：scope="current" 时的帧号列表。

    返回 dict：{ "ok", "action", "kind", "frames_drawn", "backup_path" }
    """
    manifest, sections = _read_any_project(project_path)
    action = _find_action(manifest, action_name)
    if not isinstance(operation, dict):
        raise ValueError("operation 必须是对象。")
    kind = str(operation.get("kind") or "")
    if kind not in ("brush", "eraser"):
        raise ValueError("operation.kind 必须是 brush 或 eraser。")
    segs = operation.get("segs")
    if not isinstance(segs, list) or not segs:
        raise ValueError("operation.segs 不能为空。")
    normalized = []
    for index, segment in enumerate(segs):
        if not isinstance(segment, (list, tuple)) or len(segment) != 4:
            raise ValueError(f"operation.segs[{index}] 必须是 [x1, y1, x2, y2]。")
        if not all(isinstance(v, numbers.Real) and math.isfinite(float(v)) for v in segment):
            raise ValueError(f"operation.segs[{index}] 的坐标必须是有限数字。")
        normalized.append([float(v) for v in segment])
    size = max(1, int(operation.get("size") or 4))
    scope = str(operation.get("scope") or "all")
    if scope not in ("current", "all"):
        raise ValueError("operation.scope 必须是 current 或 all。")
    total = _frame_count(action)
    if scope == "current":
        raw = operation.get("frames")
        if not isinstance(raw, list) or not raw:
            raise ValueError('scope="current" 时必须给 frames（帧号列表）。')
        targets = []
        for value in raw:
            index = int(value)
            if not 0 <= index < total:
                raise ValueError(f"帧号越界：{index}（共 {total} 帧）")
            targets.append(index)
    else:
        targets = list(range(total))

    color = (255, 255, 255, 255)
    sections_names = action.get("frame_sections") or []
    drawn = 0
    for index in targets:
        section = sections_names[index] if index < len(sections_names) else None
        if not section or section not in sections:
            continue
        img = png_from_bytes(sections[section])
        if img is None:
            continue
        img = img.convert("RGBA")
        ox, oy = _canvas_pos(action, sections, index, img.width, img.height)
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        pen = ImageDraw.Draw(layer)
        for x1, y1, x2, y2 in normalized:
            pen.line([x1 - ox, y1 - oy, x2 - ox, y2 - oy],
                     fill=color, width=size, joint="curve")
            radius = max(1, size / 2.0)
            for px, py in ((x1 - ox, y1 - oy), (x2 - ox, y2 - oy)):
                pen.ellipse([px - radius, py - radius, px + radius, py + radius], fill=color)
        if kind == "eraser":
            base = img.copy()
            base.paste((0, 0, 0, 0), (0, 0), layer)
            img = base
        else:
            img = Image.alpha_composite(img, layer)
        sections[section] = png_bytes(img)
        drawn += 1
    if drawn == 0:
        return {"ok": False, "action": action_name, "kind": kind,
                "frames_drawn": 0, "backup_path": "",
                "note": "这些帧没有可写的帧图，什么都没改。"}
    backup = _backup_then_write(project_path, manifest, sections)
    return {"ok": True, "action": action_name, "kind": kind,
            "frames_drawn": drawn, "backup_path": backup}


@mcp.tool(annotations={
    "title": "智能抽帧", "readOnlyHint": False,
    "destructiveHint": True, "idempotentHint": False, "openWorldHint": False,
})
def decimate_action_frames(project_path: str, action_name: str,
                           mode: str = "keep_1_2", tolerance: int = 0) -> dict:
    """按相似度智能抽帧（与软件四档抽帧同一套算法），写前自动备份。

    用户口径：一串 = **同相似组号 + 帧序相邻**；每串只留 ceil(N*比例) 帧且**必含最标准那帧**；
    不相邻、没配对的帧原样保留。留在原位的帧落点会被补偿，画面**不位移**。

    参数：
      - mode："keep_3_5"（抽 3/5，力度最小）/ "keep_1_2" / "keep_1_4" /
        "dedupe"（强力去重：一串只留 1 帧）；
      - tolerance：相似度容差 0~255。t 表示"最多允许 t/1000 的格子不一样"
        （0 = 必须一模一样；20 = 允许 2%）。

    返回 dict：{ "ok", "action", "mode", "frames_before", "frames_after",
                 "removed": [被抽掉的帧号], "backup_path", "note" }
    """
    manifest, sections = _read_any_project(project_path)
    action = _find_action(manifest, action_name)
    if mode not in _DECIMATE_RATIOS:
        raise ValueError("mode 必须是 keep_3_5 / keep_1_2 / keep_1_4 / dedupe。")
    tolerance = int(tolerance)
    if not 0 <= tolerance <= 255:
        raise ValueError("tolerance 必须在 0~255 之间。")
    total = _frame_count(action)
    if total < 2:
        return {"ok": False, "action": action_name, "mode": mode,
                "frames_before": total, "frames_after": total, "removed": [],
                "backup_path": "", "note": "帧太少，抽不动"}
    parts_all = action.get("frame_parts") or []
    for i in range(total):
        parts = parts_all[i] if i < len(parts_all) else None
        if parts and len(parts) > 1:
            return {"ok": False, "action": action_name, "mode": mode,
                    "frames_before": total, "frames_after": total, "removed": [],
                    "backup_path": "",
                    "note": "有合成帧（一帧由多个元素拼成），抽帧会算错裁剪来源，已拒绝"}
    images = []
    sizes_old: dict[int, tuple[int, int]] = {}
    for i in range(total):
        try:
            img = _render_frame_image(action, sections, i, manifest)[0]
        except Exception:  # noqa: BLE001
            img = None
        if img is None:
            return {"ok": False, "action": action_name, "mode": mode,
                    "frames_before": total, "frames_after": total, "removed": [],
                    "backup_path": "", "note": f"第 {i} 帧读不出来，已放弃"}
        images.append(img)
        sizes_old[i] = (img.width, img.height)
    marks = _group_elements(images, tolerance=tolerance)
    keep = _decimate_indices(images, marks, mode)
    removed = [i for i in range(total) if i not in set(keep)]
    if not removed:
        return {"ok": False, "action": action_name, "mode": mode,
                "frames_before": total, "frames_after": total, "removed": [],
                "backup_path": "", "note": "没有相邻的相似帧，这一档抽不掉东西"}
    offsets_old = {int(k): tuple(v) for k, v in (action.get("frame_offsets") or {}).items()}
    frame_map = {old: new for new, old in enumerate(keep)}
    _remap_frame_fields(action, keep)
    sizes_new = {frame_map[old]: sizes_old[old] for old in keep if old in sizes_old}
    compensated = _compensate_offsets(sizes_old, offsets_old, sizes_new, frame_map)
    action["frame_offsets"] = {str(k): [int(v[0]), int(v[1])] for k, v in compensated.items()}
    backup = _backup_then_write(project_path, manifest, sections)
    return {"ok": True, "action": action_name, "mode": mode,
            "frames_before": total, "frames_after": len(keep),
            "removed": removed, "backup_path": backup,
            "note": "已按原动作的帧重算，保留帧的落点已补偿（画面不位移）"}


@mcp.tool(annotations={
    "title": "动作级批量变换", "readOnlyHint": False,
    "destructiveHint": False, "idempotentHint": False, "openWorldHint": False,
})
def transform_action_frames(project_path: str, action_name: str,
                           transform: dict,
                           scope: str = "all", frames: list | None = None) -> dict:
    """把一次变换套用到动作的**多帧/全部帧**（软件里一键作用于全部帧的等价物）。

    transform 形状：``{"rotate":90|180|270,"mirror_h":true,"mirror_v":true,
    "flip":bool}`` —— 出现的字段才生效：
      - ``rotate``：90 / 180 / 270（顺时针角度）；
      - ``mirror_h`` / ``mirror_v``：水平/垂直镜像，true 加上、false 去掉；
      - ``flip``：同时翻转水平与垂直（等价于转 180）。

    参数：
      - scope："all"（全部帧，默认）或 "current"（只用 frames 指定的帧）；
      - frames：scope="current" 时的帧号列表。

    返回 dict：{ "ok", "action", "frames_changed", "backup_path", "changed" }
    """
    manifest, sections = _read_any_project(project_path)
    action = _find_action(manifest, action_name)
    if not isinstance(transform, dict) or not transform:
        raise ValueError("transform 必须是非空对象。")
    total = _frame_count(action)
    if scope not in ("all", "current"):
        raise ValueError("scope 必须是 all 或 current。")
    if scope == "current":
        if not isinstance(frames, list) or not frames:
            raise ValueError('scope="current" 时必须给 frames（帧号列表）。')
        targets = []
        for value in frames:
            index = int(value)
            if not 0 <= index < total:
                raise ValueError(f"帧号越界：{index}（共 {total} 帧）")
            targets.append(index)
    else:
        targets = list(range(total))

    rotate = transform.get("rotate")
    if rotate is not None:
        rotate = int(rotate)
        if rotate not in (90, 180, 270):
            raise ValueError("transform.rotate 必须是 90 / 180 / 270。")
    mirror_h = transform.get("mirror_h")
    mirror_v = transform.get("mirror_v")
    if transform.get("flip"):
        mirror_h = not bool(mirror_h)
        mirror_v = not bool(mirror_v)
    if rotate is None and mirror_h is None and mirror_v is None:
        raise ValueError("transform 里至少要有一个字段：rotate / mirror_h / mirror_v / flip。")

    rotations = dict(action.get("frame_rotations") or {})
    mh = set(action.get("frame_mirror_h") or [])
    mv = set(action.get("frame_mirror_v") or [])
    changed = 0
    for index in targets:
        touched = False
        if rotate is not None:
            now = int(rotations.get(str(index), 0) or 0)
            new = (now + rotate) % 360
            if new == 0:
                rotations.pop(str(index), None)
            else:
                rotations[str(index)] = new
            touched = True
        if mirror_h is not None:
            if bool(mirror_h):
                mh.add(index)
            else:
                mh.discard(index)
            touched = True
        if mirror_v is not None:
            if bool(mirror_v):
                mv.add(index)
            else:
                mv.discard(index)
            touched = True
        if touched:
            changed += 1
    if changed == 0:
        return {"ok": True, "action": action_name, "frames_changed": 0,
                "backup_path": "", "changed": {}}
    action["frame_rotations"] = rotations
    action["frame_mirror_h"] = sorted(mh)
    action["frame_mirror_v"] = sorted(mv)
    backup = _backup_then_write(project_path, manifest, sections)
    detail: dict = {}
    if rotate is not None:
        detail["rotate"] = rotate
    if mirror_h is not None:
        detail["mirror_h"] = bool(mirror_h)
    if mirror_v is not None:
        detail["mirror_v"] = bool(mirror_v)
    return {"ok": True, "action": action_name, "frames_changed": changed,
            "backup_path": backup, "changed": detail}


@mcp.tool(annotations={
    "title": "编辑动图文档操作链", "readOnlyHint": False,
    "destructiveHint": True, "idempotentHint": False, "openWorldHint": False,
})
def edit_animation_document(project_path: str, operation: dict) -> dict:
    """编辑动图文档的**已保存操作链**：删除某一项或调整顺序（写前自动备份）。

    operation 形状：``{"op":"delete"|"move","index":2,"to":0}``
      - ``op="delete"``：删掉第 index 项操作；
      - ``op="move"``：把第 index 项移到 to 位置（重排操作顺序）。

    追加新操作用 update_animation_document；本工具只管已有的操作链项。

    返回 dict：{ "ok", "op", "ops_count", "removed"|"moved", "backup_path" }
    """
    manifest, sections = _read_any_project(project_path)
    payload = manifest.get("anim_doc")
    if not payload:
        raise ValueError("这不是动图文档工程（没有操作链可编辑）。")
    if not isinstance(operation, dict):
        raise ValueError("operation 必须是对象。")
    op = str(operation.get("op") or "")
    if op not in ("delete", "move"):
        raise ValueError('operation.op 必须是 "delete" 或 "move"。')
    ops = payload.setdefault("ops", [])
    count = len(ops)
    if count == 0:
        raise ValueError("这个动图文档还没有任何操作，没得编辑。")
    try:
        index = int(operation.get("index"))
    except (TypeError, ValueError):
        raise ValueError("operation.index 必须是整数。") from None
    if not 0 <= index < count:
        raise ValueError(f"operation.index 越界：{index}（共 {count} 项）")
    if op == "delete":
        removed = ops.pop(index)
        backup = _backup_then_write(project_path, manifest, sections)
        return {"ok": True, "op": op, "ops_count": len(ops),
                "removed": removed, "backup_path": backup}
    try:
        to = int(operation.get("to"))
    except (TypeError, ValueError):
        raise ValueError("op=\"move\" 时 operation.to 必须是整数。") from None
    if not 0 <= to < count:
        raise ValueError(f"operation.to 越界：{to}（共 {count} 项）")
    moved = ops.pop(index)
    ops.insert(to, moved)
    backup = _backup_then_write(project_path, manifest, sections)
    return {"ok": True, "op": op, "ops_count": len(ops),
            "moved": {"from": index, "to": to}, "backup_path": backup}


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------
def run() -> int:
    """以 stdio 模式运行服务器并阻塞，直到 stdin 关闭（MCP 客户端结束）。"""
    try:
        mcp.run()
    except BaseException as exc:
        # MCP 客户端结束会话时可能先关闭 stdout。新版 anyio 会把写回失败
        # 包成嵌套 ExceptionGroup；这是正常断开，不应弹出 PyInstaller 崩溃对话框。
        def _only_broken_pipe(error: BaseException) -> bool:
            if isinstance(error, BrokenPipeError):
                return True
            if isinstance(error, BaseExceptionGroup):
                return bool(error.exceptions) and all(_only_broken_pipe(item) for item in error.exceptions)
            return False

        if _only_broken_pipe(exc):
            return 0
        raise
    return 0


if __name__ == "__main__":
    sys.exit(run())
