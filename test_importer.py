# -*- coding: utf-8 -*-
"""测试 importer 对各种 JSON 格式变体的识别能力。"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from spritesheet.importer import detect_format, load_project, FORMAT_LABELS

passed = 0
failed = 0


def make_fake_png(d: Path, name: str):
    """创建一个最小的有效 PNG（1x1 透明像素）。"""
    import struct, zlib
    def chunk(ctype, data):
        c = ctype + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xffffffff)
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
    idat = chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
    iend = chunk(b"IEND", b"")
    (d / name).write_bytes(sig + ihdr + idat + iend)


def test(name, data, expect_fmt=None, expect_frames=None):
    global passed, failed
    try:
        wrapped = data if isinstance(data, dict) else {"frames": data}
        fmt = detect_format(wrapped)
        ok = True
        if expect_fmt and fmt != expect_fmt:
            print(f"  FAIL {name}: expect {expect_fmt}, got {fmt}")
            ok = False
        # 创建临时目录 + JSON + 假 PNG
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            # 从 data 里提取图片名
            if isinstance(data, dict):
                img = (data.get("atlas") or {}).get("image") or data.get("image") or (data.get("meta") or {}).get("image") or "spritesheet.png"
            else:
                img = "spritesheet.png"
            make_fake_png(td, img)
            jf = td / "test.json"
            jf.write_text(json.dumps(data), encoding="utf-8")
            try:
                proj = load_project(jf)
                nframes = sum(len(a.frames) for a in proj.actions)
                if expect_frames and nframes != expect_frames:
                    print(f"  FAIL {name}: expect {expect_frames} frames, got {nframes}")
                    ok = False
                if ok:
                    print(f"  OK   {name}: fmt={FORMAT_LABELS.get(fmt, fmt)}, frames={nframes}, actions={len(proj.actions)}")
                    passed += 1
                else:
                    failed += 1
            except Exception as e:
                print(f"  FAIL {name}: {type(e).__name__}: {e}")
                failed += 1
    except Exception as e:
        print(f"  FAIL {name}: {type(e).__name__}: {e}")
        failed += 1


print("=== 1. 本工具格式 ===")
test("native", {
    "format": "spritesheet-animation",
    "atlas": {"image": "atlas.png", "width": 100, "height": 100},
    "animations": [{"name": "idle", "fps": 12, "frames": [
        {"parts": [{"rect": {"x": 0, "y": 0, "width": 16, "height": 16}, "pos": {"x": 0, "y": 0}}]}
    ]}],
}, expect_fmt="native", expect_frames=1)

print("=== 2. TexturePacker Hash ===")
test("tp_hash", {
    "frames": {"frame1.png": {"frame": {"x": 0, "y": 0, "w": 16, "h": 16}}},
    "meta": {"app": "texturepacker", "image": "atlas.png"},
}, expect_fmt="tp_hash", expect_frames=1)

print("=== 3. TexturePacker Array ===")
test("tp_array", {
    "frames": [{"filename": "f1.png", "frame": {"x": 0, "y": 0, "w": 16, "h": 16}}],
    "meta": {"app": "texturepacker"},
}, expect_fmt="tp_array", expect_frames=1)

print("=== 4. Aseprite ===")
test("aseprite", {
    "frames": [{"filename": "f1.png", "duration": 100, "frame": {"x": 0, "y": 0, "w": 16, "h": 16},
                "sourceSize": {"w": 16, "h": 16}, "spriteSourceSize": {"x": 0, "y": 0, "w": 16, "h": 16}}],
    "meta": {"frameTags": [{"name": "idle", "from": 0, "to": 0, "direction": "forward"}]},
}, expect_fmt="aseprite", expect_frames=1)

print("=== 5. 豆包平铺 ===")
test("doubao", {
    "image": "spritesheet.png", "size": {"width": 100, "height": 100}, "fps": 15,
    "frames": [{"name": "f1", "x": 0, "y": 0, "width": 16, "height": 16, "duration": 100}],
}, expect_fmt="doubao", expect_frames=1)

print("=== 6. 顶层数组 ===")
test("top_array", [
    {"filename": "f1.png", "frame": {"x": 0, "y": 0, "w": 16, "h": 16}},
], expect_fmt="tp_array", expect_frames=1)

print("=== 7. 豆包用 w/h 而非 width/height ===")
test("doubao_wh", {
    "image": "atlas.png", "fps": 15,
    "frames": [{"x": 0, "y": 0, "w": 16, "h": 16}],
}, expect_fmt="doubao", expect_frames=1)

print("=== 8. frames 用 Textures 键 ===")
test("textures_key", {
    "Textures": [{"filename": "f1.png", "frame": {"x": 0, "y": 0, "w": 16, "h": 16}}],
    "meta": {},
}, expect_fmt="tp_array", expect_frames=1)

print("=== 9. frame 字段是 rect 而非 frame ===")
test("rect_key", {
    "frames": [{"filename": "f1.png", "rect": {"x": 0, "y": 0, "w": 16, "h": 16}}],
    "meta": {},
}, expect_fmt="tp_array", expect_frames=1)

print("=== 10. 豆包无 fps 字段 ===")
test("doubao_nofps", {
    "image": "atlas.png",
    "frames": [{"x": 0, "y": 0, "width": 16, "height": 16, "duration": 200}],
}, expect_fmt="doubao", expect_frames=1)

print("=== 11. 豆包多帧 ===")
test("doubao_multi", {
    "image": "atlas.png", "fps": 12,
    "frames": [
        {"x": 0, "y": 0, "width": 16, "height": 16, "duration": 100},
        {"x": 20, "y": 0, "width": 16, "height": 16, "duration": 100},
        {"x": 40, "y": 0, "width": 16, "height": 16, "duration": 100},
    ],
}, expect_fmt="doubao", expect_frames=3)

print("=== 12. bounds 字段变体 ===")
test("bounds_key", {
    "frames": [{"filename": "f1.png", "bounds": {"x": 0, "y": 0, "w": 16, "h": 16}}],
    "meta": {},
}, expect_fmt="tp_array", expect_frames=1)

print()
print(f"=== 结果: {passed} passed, {failed} failed ===")
sys.exit(1 if failed else 0)
