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

支持范围（v1）：
- 普通帧工程（manifest 里没有 anim_doc 字段）。动图文档工程（anim_doc）会返回
  明确的错误信息 —— 不支持、不静默、不假装成功。
- 帧含「自由四边形透视变换」(quad) 时：extract / export 无法在 headless 下无损
  复刻，会明确报错并提示先在软件里处理该帧。
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
from mcp.server.fastmcp import FastMCP

from spritesheet import __app_name__, __version__
from spritesheet.importer import load_project
from spritesheet.project_file import png_bytes, png_from_bytes, read_project
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
        "所有路径参数用本地绝对路径；工程文件用 read_project 读取，与软件内的工程完全一致。"
    ),
)

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
# 内部辅助：帧渲染（与软件同一变换口径，Pillow 实现）
# ---------------------------------------------------------------------------
def _render_frame_image(action: dict, sections: dict, index: int,
                        manifest: dict | None = None):
    """渲染指定帧的画面（PIL.Image），与软件预览一致。

    顺序与软件 transform_frame_pixmap 相同：镜像H → 镜像V → 90°整数倍旋转；
    自由四边形(quad) 透视无法在 headless 下无损复刻 → 明确报错。
    帧图缺失时回退到「从合成大图抠单部件」（需 manifest 里的合成大图段名，
    仅无变换帧；见 _compose_from_atlas）。
    """
    quad = (action.get("frame_quads") or {}).get(str(index))
    if quad:
        raise ValueError(
            f"帧 {index} 含自由四边形透视变换（quad），headless 下无法无损复刻画面。"
            "请先在 SpriteSheetTool 里把该帧的透视去掉并重新保存工程。"
        )
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
    return img, "saved_frame_png"


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
    part = atlas.crop(rect)
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
def _action_shell(action: dict) -> SimpleNamespace:
    """把 manifest 里的动作字典包成 exporters 能鸭子访问的对象。

    exporters 只访问 .name / .frames（长度）/ .fps / .loop_mode /
    .frame_duration_ms(i)，不碰像素。
    """
    fps = int(action.get("fps") or 10)

    def frame_duration_ms(i: int) -> int:
        duration = (action.get("frame_durations") or {}).get(str(i))
        if duration is not None:
            return int(duration)
        return max(1, round(1000 / max(1, fps)))

    return SimpleNamespace(
        name=action.get("name") or "action",
        frames=[None] * _frame_count(action),
        fps=fps,
        loop_mode=action.get("loop_mode") or "loop",
        frame_duration_ms=frame_duration_ms,
    )


def _build_export_atlas(manifest: dict, sections: dict):
    """构造导出图集 + records（与 GUI 导出「原图坐标图集」行为一致）。

    - 图集 = 工程的合成大图；普通帧直接引用其中的 rect（frame_rects）；
    - 带镜像/旋转的帧用帧图 + Pillow 复刻变换后**烘焙追加到图集下方**，
      图集尺寸随之扩张（同 GUI 的 _build_original_atlas）；
    - 含 quad 透视的帧 → 报错要求先在软件里处理（不静默丢帧）。
    返回 (atlas: PIL.Image, records: dict)。
    """
    atlas = _combined_canvas(sections, manifest)
    shells: list[SimpleNamespace] = []
    records: dict = {}
    bake: list[tuple[SimpleNamespace, int, Image.Image]] = []

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
            if not (has_quad or has_rot or has_mirror):
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
    return atlas, records


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
# 六个工具
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
        "frames": int,
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
        "frames": len(frames),
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

    图集策略与软件导出行为一致：以工程合成大图为图集；带旋转/镜像的帧会
    复刻变换后烘焙追加到图集下方（JSON 引用即实际画面）。含自由四边形透视
    (quad) 的帧会明确报错（无法在无 GUI 下无损复刻，请先在软件里处理）。

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
    atlas, records = _build_export_atlas(manifest, sections)
    shells = [_action_shell(a) for a in _actions_of(manifest)]

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
    带镜像/旋转的帧会按软件同一顺序复刻变换；含自由四边形透视 (quad) 的帧
    明确报错（无法在无 GUI 下无损复刻）。老工程缺帧图时回退到从合成大图
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
# 启动
# ---------------------------------------------------------------------------
def run() -> int:
    """以 stdio 模式运行服务器并阻塞，直到 stdin 关闭（MCP 客户端结束）。"""
    mcp.run()
    return 0


if __name__ == "__main__":
    sys.exit(run())