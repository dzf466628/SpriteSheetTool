"""第三方精灵图 JSON 导出器。

将本工具的动作/帧数据导出为业界通用格式，供其他引擎/软件直接使用：

- TexturePacker JSON Hash（frames 为对象，按键查帧）
- TexturePacker JSON Array（frames 为数组，保序）
- Aseprite JSON Array（frames 为数组 + meta.frameTags 动画标签）

设计约定（与主窗口解耦）：
- 本模块不 import main_window / PyQt 组件，只接收普通数据；
  Action 对象以鸭子类型访问（.name/.frames/.fps/.loop_mode）。
- 帧名统一为 ``{action_name}_{index}``（index 从 0 开始），
  Aseprite 的 frameTags 用动作名，from/to 指向该动作在全局帧数组中的区间。
- 帧记录 ``records[(action, index)]`` 为 dict：
  ``{"rect": (x,y,w,h), "offset": (ox,oy), "source": (sw,sh), "trimmed": bool}``
  其中 rect 是内容在图集中的位置，offset 是内容在原画布中的左上角，
  source 是原画布尺寸 —— 即标准 trimmed 语义
  （frame = 内容矩形，spriteSourceSize = 内容在源图中的位置，sourceSize = 源图尺寸）。
- 兼容旧的 4 元组 (x,y,w,h) 记录：视为未裁剪（trimmed=False）。
"""
from __future__ import annotations

import json
from pathlib import Path

# 导出格式标识（与 ExportDialog 下拉框 data 保持一致）
FMT_NATIVE = "native"
FMT_TP_HASH = "tp_hash"
FMT_TP_ARRAY = "tp_array"
FMT_ASEPRITE_ARRAY = "aseprite_array"

FORMAT_CHOICES = [
    (FMT_NATIVE, "本工具格式"),
    (FMT_TP_HASH, "TexturePacker JSON-Hash"),
    (FMT_TP_ARRAY, "TexturePacker JSON-Array"),
    (FMT_ASEPRITE_ARRAY, "Aseprite JSON-Array"),
]

# 文件名后缀：本工具格式无后缀（保持 spritesheet.json / {name}.json 兼容），
# 其余格式用「.格式名.json」区分，支持多格式同时导出。
FORMAT_SUFFIX = {
    FMT_NATIVE: "",
    FMT_TP_HASH: ".tp_hash",
    FMT_TP_ARRAY: ".tp_array",
    FMT_ASEPRITE_ARRAY: ".aseprite",
}


def _duration_ms(fps) -> int:
    """fps → 每帧毫秒（Aseprite duration 字段）。"""
    return max(1, round(1000 / max(1, int(fps or 10))))


def _loop_direction(loop_mode: str) -> str:
    """本工具 loop 值 → Aseprite frameTag direction。"""
    if loop_mode == "pingpong":
        return "pingpong"
    return "forward"  # loop / once 在 Aseprite 中都是 forward（播放一次由引擎控制）


def _frame_entries(action, records, prefix: str) -> list[dict]:
    """收集单个动作的帧条目。

    图集按「元素图块」打包，帧通过 parts 引用图块：
      - 普通帧：parts 只有 1 项，等价于单矩形
      - 合成帧：parts 有多项（多个元素拼接）
    TexturePacker / Aseprite 的帧模型是「一帧 = 一个矩形」，无法表达多部件帧，
    因此这里对合成帧使用其整块矩形（打包时已为该帧预留整块图，见 rect）。
    """
    entries = []
    for index, frame in enumerate(action.frames):
        record = records.get((action, index))
        if record is None:
            continue
        if isinstance(record, dict):
            # 多部件合成帧：优先用为其单独打包的整块矩形；
            # 合成帧与普通帧一样，都用 offset（帧在逻辑画布中的左上角）定位，
            # 这样引擎把整块贴到画布 offset 处重建的画面，与 GIF / 预览完全一致。
            # （旧实现合成帧写死 ox,oy=0,0，一旦画布大于整块就整体错位。）
            comp = record.get("composite_rect")
            if comp is not None:
                x, y, w, h = comp
            else:
                x, y, w, h = record["rect"]
            ox, oy = record["offset"]
            cw, ch = record.get("canvas", (w, h))
            # trimmed 表示「帧内容是源图的一个子矩形」：内容尺寸小于源图，或内容
            # 在源图中有偏移。原实现恒为 False，却同时写出非零 spriteSourceSize /
            # 更大的 sourceSize，二者自相矛盾 —— 只看 trimmed 的第三方引擎会忽略
            # 偏移、把帧贴到 (0,0)，合成帧就会整体错位。这里按实际关系如实标注。
            trimmed = bool(w != cw or h != ch or ox or oy)
        else:  # 兼容旧的 4 元组记录
            x, y, w, h = record
            ox, oy, cw, ch, trimmed = 0, 0, w, h, False
        entries.append({
            "_index": index,  # 内部字段：供 Aseprite 填逐帧 duration，输出前移除
            "filename": f"{prefix}_{index}",
            "frame": {"x": x, "y": y, "w": w, "h": h},
            "rotated": False,
            "trimmed": trimmed,
            "spriteSourceSize": {"x": ox, "y": oy, "w": w, "h": h},
            "sourceSize": {"w": cw, "h": ch},
        })
    return entries


def _prefixes_for(actions, prefixes: dict | None) -> dict:
    """返回 action → 帧名前缀；未指定时用动作名本身。"""
    if prefixes:
        return prefixes
    return {a: (a.name or "action") for a in actions}


def _tp_meta(atlas_name: str, atlas_w: int, atlas_h: int, app_label: str) -> dict:
    return {
        "app": app_label,
        "version": "1.0",
        "image": atlas_name,
        "format": "RGBA8888",
        "size": {"w": atlas_w, "h": atlas_h},
        "scale": "1",
    }


def build_tp_hash(actions, records, atlas_name: str, atlas_w: int, atlas_h: int, app_label: str, prefixes: dict | None = None) -> dict:
    """TexturePacker JSON-Hash：frames 为 {帧名: 条目}。"""
    prefix_map = _prefixes_for(actions, prefixes)
    frames: dict = {}
    for action in actions:
        for entry in _frame_entries(action, records, prefix_map.get(action, action.name)):
            entry.pop("_index", None)
            frames[entry.pop("filename")] = entry
    return {"frames": frames, "meta": _tp_meta(atlas_name, atlas_w, atlas_h, app_label)}


def build_tp_array(actions, records, atlas_name: str, atlas_w: int, atlas_h: int, app_label: str, prefixes: dict | None = None) -> dict:
    """TexturePacker JSON-Array：frames 为保序数组。"""
    prefix_map = _prefixes_for(actions, prefixes)
    frames: list = []
    for action in actions:
        for entry in _frame_entries(action, records, prefix_map.get(action, action.name)):
            entry.pop("_index", None)
            frames.append(entry)
    return {"frames": frames, "meta": _tp_meta(atlas_name, atlas_w, atlas_h, app_label)}


def build_aseprite_array(actions, records, atlas_name: str, atlas_w: int, atlas_h: int, app_label: str, prefixes: dict | None = None) -> dict:
    """Aseprite JSON-Array：frames 为数组 + meta.frameTags 动画标签。"""
    prefix_map = _prefixes_for(actions, prefixes)
    frames: list = []
    tags: list = []
    cursor = 0
    for action in actions:
        prefix = prefix_map.get(action, action.name)
        entries = _frame_entries(action, records, prefix)
        if not entries:
            continue
        duration_of = getattr(action, "frame_duration_ms", None)
        for entry in entries:
            _idx = entry.pop("_index", 0)
            entry["duration"] = int(duration_of(_idx)) if callable(duration_of) else _duration_ms(action.fps)
            frames.append(entry)
        tags.append({
            "name": prefix,
            "from": cursor,
            "to": cursor + len(entries) - 1,
            "direction": _loop_direction(action.loop_mode),
        })
        cursor += len(entries)
    meta = {
        "app": app_label,
        "version": "1.3",
        "image": atlas_name,
        "format": "RGBA8888",
        "size": {"w": atlas_w, "h": atlas_h},
        "scale": "1",
        "frameTags": tags,
        "layers": [],
        "slices": [],
    }
    return {"frames": frames, "meta": meta}


def build_payload(fmt: str, actions, records, atlas_name: str, atlas_w: int, atlas_h: int, app_label: str, prefixes: dict | None = None) -> dict:
    """按格式标识生成对应 JSON 字典。"""
    if fmt == FMT_TP_HASH:
        return build_tp_hash(actions, records, atlas_name, atlas_w, atlas_h, app_label, prefixes)
    if fmt == FMT_TP_ARRAY:
        return build_tp_array(actions, records, atlas_name, atlas_w, atlas_h, app_label, prefixes)
    if fmt == FMT_ASEPRITE_ARRAY:
        return build_aseprite_array(actions, records, atlas_name, atlas_w, atlas_h, app_label, prefixes)
    raise ValueError(f"未知 JSON 导出格式：{fmt}")


def write_payload(path: Path, payload: dict) -> None:
    """以 UTF-8 / 缩进 2 写出 JSON。"""
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
