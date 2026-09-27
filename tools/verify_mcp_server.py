"""verify_mcp_server —— MCP Server 回归验证。

覆盖：
  1) 六个工具的全部成功路径：用脚本现场生成的假 .sstool 工程（v2 容器）
     与假精灵图 JSON，逐一真调用（不经 stdio，直接函数级调用来断言结构）；
  2) 错误路径：帧越界 / 找不到动作 / 未知导出格式 / anim_doc（动图文档）工程；
  3) 真实 stdio 握手：起子进程 `python -m spritesheet.mcp_server`，
     initialize + list_tools，断言已注册工具与 TOOL_DEFS 完全一致（九个工具）。

用法：.venv\\Scripts\\python.exe tools\\verify_mcp_server.py
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw  # noqa: E402

from spritesheet.mcp_server import (  # noqa: E402
    TOOL_DEFS, export_to_engine_json, extract_action_frames_png, extract_frame_png,
    get_action_detail, import_spritesheet_json, list_actions, project_overview,
    read_sstool_project, validate_project,
)
from spritesheet.project_file import png_bytes, read_project, write_project  # noqa: E402

# 输出目录固定在仓库内（避免系统临时目录权限问题），结束时清掉
OUT = ROOT / "build" / "verify_mcp_out"
OUT.mkdir(parents=True, exist_ok=True)

FAILURES: list[str] = []
passed = 0


def check(name: str, cond, detail: str = "") -> None:
    global passed
    if cond:
        passed += 1
        print(f"  [OK]   {name}")
    else:
        FAILURES.append(name)
        print(f"  [FAIL] {name} {detail}")


def _solid(color) -> Image.Image:
    img = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    ImageDraw.Draw(img).rectangle([0, 0, 7, 7], fill=color)
    return img


def _draw_rect(draw, xywh, color) -> None:
    """Pillow rectangle 需要 (left, top, right, bottom)；工程 rect 是 (x, y, w, h)。"""
    x, y, w, h = xywh
    draw.rectangle([x, y, x + w, y + h], fill=color)


def make_test_project() -> tuple[Path, dict]:
    """现场造一个 v2 工程：2 个动作（跑×3 帧、跳×2 帧含 90° 旋转）。"""
    combined = Image.new("RGBA", (100, 50), (0, 0, 0, 0))
    draw = ImageDraw.Draw(combined)
    rects_run = [(0, 0, 8, 8), (8, 0, 8, 8), (16, 0, 8, 8)]
    for rect, color in zip(rects_run, [(255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255)]):
        _draw_rect(draw, rect, color)
    rects_jump = [(20, 0, 8, 8), (28, 0, 8, 4)]  # 第二帧 8×4，旋转 90 后应输出 4×8
    _draw_rect(draw, rects_jump[0], (255, 255, 0, 255))
    _draw_rect(draw, rects_jump[1], (255, 165, 0, 255))

    sections: dict[str, bytes] = {"images/combined.png": png_bytes(combined)}
    frame_images = {
        "actions/0000/frame_0000.png": _solid((255, 0, 0, 255)),
        "actions/0000/frame_0001.png": _solid((0, 255, 0, 255)),
        "actions/0000/frame_0002.png": _solid((0, 0, 255, 255)),
        # 跳：帧图与 combined 里画的矩形同尺寸（第二帧 8×4）
        "actions/0001/frame_0000.png": _solid((255, 255, 0, 255)),
        "actions/0001/frame_0001.png": Image.new("RGBA", (8, 4), (255, 165, 0, 255)),
    }
    sections.update({name: png_bytes(img) for name, img in frame_images.items()})

    manifest = {
        "format": "spritesheet-tool-project", "version": 1,
        "app_version": "verify",
        "combined_image": "images/combined.png",
        "source_images": [],
        "actions": [
            {
                "name": "跑", "color": None,
                "selected_indices": [0, 1, 2], "composite_groups": [],
                "frame_layouts": None, "frame_layouts_base": None,
                "frame_rects": [list(r) for r in rects_run],
                "frame_parts": [[[list(r), 0, 0, 0, 0]] for r in rects_run],
                "frame_sections": [f"actions/0000/frame_{i:04d}.png" for i in range(3)],
                "fps": 10, "loop_mode": "loop", "padding": 1,
                "ground_enabled": False, "ground_offset": 0,
                "reuse_frame_settings": False, "show_anchor": True,
                "anchor": [0.5, 0.5], "frame_offsets": {}, "frame_durations": {},
                "frame_rotations": {}, "frame_mirror_h": [], "frame_mirror_v": [],
                "frame_rotation_pivots": {}, "frame_quads": {},
                "frame_transform_modes": {}, "ref_frame": 0, "ref_opacity": 1.0,
                "frame_opacity": 1.0, "bg_mode": "auto",
                "action_type": "run", "move_direction": "right", "move_speed": 120,
            },
            {
                "name": "跳", "color": None,
                "selected_indices": [0, 1], "composite_groups": [],
                "frame_layouts": None, "frame_layouts_base": None,
                "frame_rects": [list(r) for r in rects_jump],
                "frame_parts": [[[list(r), 0, 0, 0, 0]] for r in rects_jump],
                "frame_sections": [f"actions/0001/frame_{i:04d}.png" for i in range(2)],
                "fps": 12, "loop_mode": "once", "padding": 0,
                "ground_enabled": True, "ground_offset": 3,
                "reuse_frame_settings": False, "show_anchor": True,
                "anchor": [0.5, 0.5], "frame_offsets": {}, "frame_durations": {},
                "frame_rotations": {"1": 90}, "frame_mirror_h": [],
                "frame_mirror_v": [], "frame_rotation_pivots": {},
                "frame_quads": {}, "frame_transform_modes": {}, "ref_frame": 0,
                "ref_opacity": 1.0, "frame_opacity": 1.0, "bg_mode": "auto",
                "action_type": "jump", "move_direction": "up", "move_speed": 60,
            },
        ],
    }
    path = OUT / "fake.sstool"
    write_project(path, manifest, dict(sections))
    return path, manifest


def test_tools(project_path: Path, manifest: dict) -> None:
    proj = str(project_path)

    # ---- read_sstool_project ----
    info = read_sstool_project(proj)
    check("read: ok 字段", info.get("ok") is True)
    check("read: 动作数=2", len(info["actions"]) == 2,
          f"got={len(info['actions'])}")
    check("read: 资源段含 combined 与帧图",
          "images/combined.png" in info["section_names"] and
          "actions/0000/frame_0000.png" in info["section_names"])
    check("read: 画布尺寸已推导", info["actions"][0]["canvas_size"] == [10, 10],
          f"got={info['actions'][0]['canvas_size']}")

    # ---- list_actions ----
    actions = list_actions(proj)
    check("list: 2 个动作", len(actions) == 2)
    check("list: 帧数正确", [a["frames"] for a in actions] == [3, 2])
    check("list: 移动参数带出", actions[0]["move_direction"] == "right")

    # ---- get_action_detail ----
    detail = get_action_detail(proj, "跳")
    check("detail: 帧数=2", detail["frame_count"] == 2,
          f"got={detail['frame_count']}")
    check("detail: 第2帧旋转后尺寸 4x8",
          detail["frames"][1]["size"] == [4, 8],
          f"got={detail['frames'][1]['size']}")
    check("detail: 画布尺寸=2x2×(8/4)",
          detail["canvas_size"] == [8, 8])  # 基准 max_w=8, max_h=8, pad=0
    check("detail: 帧0时长=100ms(fps12取整)",
          detail["frames"][0]["duration_ms"] == 83,
          f"got={detail['frames'][0]['duration_ms']}")

    # ---- export_to_engine_json（三种格式）----
    fmt_paths = {
        "tp_hash": "fake.tp_hash.json",
        "tp_array": "fake.tp_array.json",
        "aseprite_array": "fake.aseprite.json",
    }
    for fmt, expected_name in fmt_paths.items():
        json_path = export_to_engine_json(proj, fmt, str(OUT))
        check(f"export {fmt}: 返回路径存在", Path(json_path).name == expected_name,
              f"got={Path(json_path).name}")
        payload = json.loads(Path(json_path).read_text(encoding="utf-8"))
        frames = payload["frames"]
        n = len(frames) if isinstance(frames, list) else len(frames)
        check(f"export {fmt}: 5 帧都在", n == 5, f"got={n}")
        atlas_png = OUT / "fake.png"
        check(f"export {fmt}: 配套图集已写出", atlas_png.exists() and atlas_png.stat().st_size > 0)
    # aseprite 特有：frameTags
    ase = json.loads((OUT / "fake.aseprite.json").read_text(encoding="utf-8"))
    check("export aseprite: 2 个 frameTags",
          len(ase["meta"].get("frameTags", [])) == 2)
    # tp_hash：旋转帧必须烘焙（rect 在图集下方，即 y >= 原图高 50）
    tp = json.loads((OUT / "fake.tp_hash.json").read_text(encoding="utf-8"))
    jump2 = tp["frames"]["跳_1"]
    check("export tp_hash: 旋转帧烘焙进图集下方",
          jump2["frame"]["y"] >= 50,
          f"y={jump2['frame']['y']}")

    # ---- extract_frame_png ----
    out = extract_frame_png(proj, "跑", 0)
    check("extract: ok", out["ok"] is True)
    check("extract: 尺寸 8x8", (out["width"], out["height"]) == (8, 8))
    raw = base64.b64decode(out["base64_png"])
    check("extract: base64 可解码为 PNG",
          raw[:8] == b"\x89PNG\r\n\x1a\n")
    img = Image.open(__import__("io").BytesIO(raw))
    check("extract: 像素为红（与合成大图一致）",
          img.convert("RGBA").getpixel((1, 1))[:3] == (255, 0, 0),
          f"got={img.convert('RGBA').getpixel((1,1))}")
    out2 = extract_frame_png(proj, "跳", 1)
    check("extract: 旋转帧输出 4x8",
          (out2["width"], out2["height"]) == (4, 8),
          f"got=({out2['width']},{out2['height']})")
    check("extract: 来源为已存帧图", out2["source"] == "saved_frame_png")

    # ---- project_overview ----
    ov = project_overview(proj)
    check("overview: ok", ov["ok"] is True)
    check("overview: 2 动作 5 总帧",
          ov["action_count"] == 2 and ov["total_frames"] == 5,
          f"{ov['action_count']}/{ov['total_frames']}")
    check("overview: 动作清单含帧数",
          len(ov["actions"]) == 2 and ov["actions"][0]["frames"] == 3)
    ov_raw = base64.b64decode(ov["overview_png"])
    check("overview: 总览图可解码 PNG", ov_raw[:8] == b"\x89PNG\r\n\x1a\n")
    ov_img = Image.open(io.BytesIO(ov_raw))
    check("overview: 两格缩略图横排（40x96）",
          (ov_img.width, ov_img.height) == (40, 96),
          f"{ov_img.width}x{ov_img.height}")

    # ---- extract_action_frames_png ----
    af = extract_action_frames_png(proj, "跑")
    check("frames: 抓全部 3 帧", af["frame_count"] == 3, str(af["frame_count"]))
    check("frames: 每帧 base64 非空",
          all(f["base64_png"] for f in af["frames"]))
    grid_raw = base64.b64decode(af["contact_sheet_png"])
    check("frames: 网格图可解码 PNG", grid_raw[:8] == b"\x89PNG\r\n\x1a\n")
    gimg = Image.open(io.BytesIO(grid_raw))
    check("frames: 3 帧排一行（宽 192）", gimg.width == 3 * 64, f"w={gimg.width}")

    # ---- validate_project ----
    vp = validate_project(proj)
    check("validate: 健康工程无 error/warning",
          vp["ok"] is True and vp["summary"] == {"errors": 0, "warnings": 0},
          str(vp["summary"]))
    m2, s2 = read_project(project_path)
    run_action = next(a for a in m2["actions"] if a["name"] == "跑")
    del s2[run_action["frame_sections"][1]]          # 故意弄丢第 2 帧图
    broken = OUT / "fake_broken.sstool"
    write_project(broken, m2, s2)
    vb = validate_project(str(broken))
    check("validate: 缺帧图报 error 且 ok=False",
          not vb["ok"] and vb["summary"]["errors"] >= 1, str(vb["summary"]))
    check("validate: 错误消息指向「跑」缺帧图",
          any(i["action"] == "跑" and "帧图缺失" in i["message"]
              for i in vb["issues"]), str(vb["issues"])[:120])

    # ---- import_spritesheet_json（TP Hash 样本）----
    sample_dir = OUT / "sample_json"
    sample_dir.mkdir(exist_ok=True)
    atlas = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    ImageDraw.Draw(atlas).rectangle([0, 0, 7, 7], fill=(0, 128, 255, 255))
    (sample_dir / "atlas.png").write_bytes(png_bytes(atlas))
    (sample_dir / "sheet.json").write_text(json.dumps({
        "frames": {
            "run_0": {"frame": {"x": 0, "y": 0, "w": 8, "h": 8},
                      "rotated": False, "trimmed": False,
                      "spriteSourceSize": {"x": 0, "y": 0, "w": 8, "h": 8},
                      "sourceSize": {"w": 8, "h": 8}},
        },
        "meta": {"image": "atlas.png", "size": {"w": 16, "h": 16},
                 "scale": "1"},
    }), encoding="utf-8")
    imported = import_spritesheet_json(str(sample_dir / "sheet.json"))
    check("import: 识别为 tp_hash", imported["format"] == "tp_hash",
          f"got={imported['format']}")
    check("import: 1 个动作 1 帧",
          len(imported["actions"]) == 1 and imported["actions"][0]["frames"] == 1)
    check("import: 找到配套图片", imported["atlas_path"].endswith("atlas.png"))

    # ---- 错误路径 ----
    def expect_error(name: str, fn) -> None:
        try:
            fn()
            check(name, False, "应当抛错但没有")
        except ValueError as exc:
            check(name, True, str(exc)[:80])

    expect_error("err: 帧越界",
                 lambda: extract_frame_png(proj, "跑", 99))
    expect_error("err: 找不到动作",
                 lambda: get_action_detail(proj, "不存在"))
    expect_error("err: 未知导出格式",
                 lambda: export_to_engine_json(proj, "png", str(OUT)))
    expect_error("err: 文件不存在",
                 lambda: read_sstool_project(str(OUT / "没有这个文件.sstool")))

    # anim_doc 工程：给 fake 加 anim_doc 字段再存一份
    bad = dict(manifest)
    bad["anim_doc"] = {"frames": ["anim/frame_0000.png"], "ops": []}
    anim_path = OUT / "animdoc.sstool"
    write_project(anim_path, bad, {"anim/frame_0000.png": png_bytes(_solid((1, 2, 3, 255)))})
    expect_error("err: anim_doc 明确拒绝",
                 lambda: list_actions(str(anim_path)))


async def test_handshake() -> None:
    """真实 stdio：子进程 python -m spritesheet.mcp_server → initialize + 工具清单。"""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    python = sys.executable
    params = StdioServerParameters(command=python, args=["-m", "spritesheet.mcp_server"])
    t0 = time.perf_counter()
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                info = await session.initialize()
                result = await session.list_tools()
                names = sorted(t.name for t in result.tools)
        elapsed = time.perf_counter() - t0
        check("握手: initialize 成功", getattr(info, "protocolVersion", None) is not None)
        expected = sorted(name for name, _ in TOOL_DEFS)
        check(f"握手: {len(expected)} 个工具与 TOOL_DEFS 一致 ({elapsed:.1f}s)",
              names == expected, f"got={names}")
    except Exception as exc:  # noqa: BLE001
        check("握手: 子进程可启动", False, f"{type(exc).__name__}: {exc}")


def main() -> int:
    print("verify_mcp_server —— MCP Server 回归")
    if OUT.exists():
        shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    project_path, manifest = make_test_project()
    test_tools(project_path, manifest)
    asyncio.run(test_handshake())

    print("=" * 50)
    print(f"PASSED {passed}  |  FAILED {len(FAILURES)}")
    if FAILURES:
        print("Failures:", *FAILURES, sep="\n  - ")
        return 1
    print("VERIFY MCP SERVER OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())