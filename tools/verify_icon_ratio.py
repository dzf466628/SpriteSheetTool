# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""图标文件本身的回归（PIL 层）：方形帧、比例未变、按高度铺满。

配套 `tools/verify_icon_imagelist.py`（shell 层：SHGetFileInfo 取回真实图标量比例）。
两个都要看：
  * 本工具管「文件里存的是什么」；
  * imagelist 那个管「资源管理器显示成什么样」——非方形帧会在显示时被拉伸，
    只有它抓得到（前一轮就是漏了这一步才把图标改坏）。

要点：**每一档都必须是方形画布**。源图 185×256 竖幅，方形画布两侧会透明，
这是正确的：图按高度铺满整档（256 档里就是 256 高），宽度按原比例（72%），
这是不拉伸前提下的最大尺寸。

用法: .venv\\Scripts\\python.exe tools\\verify_icon_ratio.py
"""
import sys
from pathlib import Path

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parent.parent
CANONICAL = (16, 24, 32, 48, 64, 128, 256)
FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'OK' if ok else 'FAIL'}]   {label} {detail}")
    if not ok:
        FAILURES.append(label)


def frames_of(path: Path) -> list[Image.Image]:
    ico = Image.open(path)
    sizes = sorted(ico.info.get("sizes") or [(ico.width, ico.height)], key=lambda s: s[1])
    out = []
    for size in sizes:
        ico.size = size
        out.append(ico.convert("RGBA").copy())
    return out


def solid_bbox(frame: Image.Image):
    return frame.getchannel("A").point(lambda a: 255 if a > 200 else 0).getbbox()


def main() -> int:
    sstool = ROOT / "SSTool.ico"
    master_path = ROOT / "SSTool_源图.png"

    print("=== 1. 原图（SSTool_源图.png）===")
    master = Image.open(master_path).convert("RGBA")
    ratio = master.width / master.height
    print(f"  原图 {master.width}x{master.height}  长宽比 {ratio:.4f}（0.723 = 竖幅）")
    check("原图四周没有透明边", master.getbbox() == (0, 0, master.width, master.height),
          f"bbox={master.getbbox()}")

    print()
    print("=== 2. SSTool.ico 每档必须是方形 ===")
    frames = frames_of(sstool)
    print(f"  帧数 {len(frames)}: " + ", ".join(f"{f.width}x{f.height}" for f in frames))
    squares = [f for f in frames if f.width == f.height]
    check("每一档都是方形画布", len(squares) == len(frames),
          f"非方形帧 {[f'{f.width}x{f.height}' for f in frames if f.width != f.height]}")
    check("档位齐全（含 16 与 256）",
          sorted(f.width for f in frames) == list(CANONICAL),
          f"实际 {sorted(f.width for f in frames)}")

    print()
    print("=== 3. 方形画布里的图：比例不变 + 按高度铺满 ===")
    for frame in frames:
        box = solid_bbox(frame)
        if box is None:
            check(f"{frame.width} 档有内容", False)
            continue
        w, h = box[2] - box[0], box[3] - box[1]
        art_ratio = w / h
        # 方形画布里，图不能变形（±1px 取整误差）
        tol = 2.0 / h + 1e-6
        check(f"{frame.width} 档 图比例 {art_ratio:.3f} 与源图一致", abs(art_ratio - ratio) <= tol,
              f"实际 {w}x{h}")
        # 图按高度铺满整档：这是不拉伸前提下的最大尺寸
        check(f"{frame.width} 档 图按高度铺满（{h} == {frame.height}）", h == frame.height,
              f"实际 {h}")
        # 宽度按原比例，不应等于画布宽（那说明被拉伸成方形了）
        check(f"{frame.width} 档 图未被拉宽（{w} < {frame.width}）", w < frame.width,
              f"实际 {w}")

    print()
    print("=== 4. 每档都是从 256 母图独立缩放（不是逐级缩）===")
    # 对照物必须是「母图整体缩到该档」：母图是方形画布（图两侧透明），
    # 缩到小尺寸时透明边也会参与滤波；如果改成「只把图缩到该档再居中」，
    # 16px 档会差出 15 个通道值 —— 那是量法不对，不是图标不对。
    mother = frames[-1]                       # 256 档就是母图
    worst = 0.0
    for frame in frames:
        expected = mother.resize(frame.size, Image.Resampling.LANCZOS)
        diff = ImageChops.difference(frame.convert("RGB"), expected.convert("RGB"))
        stat = sum(sum(p) for p in diff.getdata()) / (frame.width * frame.height * 3)
        worst = max(worst, stat)
        print(f"    {frame.width}x{frame.height}  与「母图缩到该档」的平均通道差 = {stat:.3f}")
    check("逐档缩放一致（平均通道差 < 1）", worst < 1.0, f"最大 {worst:.3f}")

    print()
    print(f"ICON FILE FAILED {len(FAILURES)}" if FAILURES else "ICON FILE OK")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
