"""图标比例回归：SSTool.ico 必须是「原图等比」，SpriteSheetTool.ico 保持方形。

要证明三件事：
  1) SSTool.ico 每一档都是**原图长宽比**（不再套方形画布）；
  2) 每一档的实心内容都**占满整帧**（square 模式会只剩 72% 宽，四周一圈透明）；
  3) 每一档都等于原图按该档尺寸重缩放（逐档独立缩放，不是从大图逐级缩下去的）。

用法: .venv\\Scripts\\python.exe tools\\verify_icon_ratio.py
"""
import sys
from pathlib import Path

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parent.parent
FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'OK' if ok else 'FAIL'}]   {label} {detail}")
    if not ok:
        FAILURES.append(label)


def frames_of(path: Path) -> list[Image.Image]:
    ico = Image.open(path)
    sizes = sorted(ico.info.get("sizes") or [(ico.width, ico.height)],
                   key=lambda s: s[1])
    out = []
    for size in sizes:
        ico.size = size
        out.append(ico.convert("RGBA").copy())
    return out


def fill_ratio(frame: Image.Image) -> tuple[float, float]:
    box = frame.getchannel("A").point(lambda a: 255 if a > 200 else 0).getbbox()
    if box is None:
        return 0.0, 0.0
    return (box[2] - box[0]) / frame.width, (box[3] - box[1]) / frame.height


def main() -> int:
    sstool = ROOT / "SSTool.ico"
    app_icon = ROOT / "SpriteSheetTool.ico"
    master_path = ROOT / "SSTool_源图.png"

    print("=== 1. 原图（SSTool_源图.png）===")
    master = Image.open(master_path).convert("RGBA")
    ratio = master.width / master.height
    print(f"  原图 {master.width}x{master.height}  长宽比 {ratio:.4f}")
    check("原图四周没有透明边", master.getbbox() == (0, 0, master.width, master.height),
          f"bbox={master.getbbox()}")

    print()
    print("=== 2. SSTool.ico 每档比例 ===")
    frames = frames_of(sstool)
    print(f"  帧数 {len(frames)}: " + ", ".join(f"{f.width}x{f.height}" for f in frames))
    check("至少 7 档", len(frames) >= 7, f"实际 {len(frames)}")

    all_ratio = True
    all_full = True
    for frame in frames:
        fr = frame.width / frame.height
        # 允许 ±1px 的取整误差
        tolerance = (1.0 / frame.height) + 1e-6
        if abs(fr - ratio) > tolerance:
            all_ratio = False
            print(f"    [FAIL] {frame.width}x{frame.height} 比例 {fr:.4f}（原图 {ratio:.4f}）")
        fill_w, fill_h = fill_ratio(frame)
        if fill_w < 0.98 or fill_h < 0.98:
            all_full = False
            print(f"    [FAIL] {frame.width}x{frame.height} 实心只占 {fill_w:.0%}x{fill_h:.0%}")
    check("每一档都是原图长宽比", all_ratio)
    check("每一档实心内容占满整帧（无透明边框）", all_full)

    print()
    print("=== 3. 每一档都独立按原图缩放 ===")
    worst = 0.0
    for frame in frames:
        expected = master.resize(frame.size, Image.Resampling.LANCZOS)
        diff = ImageChops.difference(frame.convert("RGB"), expected.convert("RGB"))
        stat = sum(sum(p) for p in diff.getdata()) / (frame.width * frame.height * 3)
        worst = max(worst, stat)
        print(f"    {frame.width}x{frame.height}  与原图缩放的平均通道差 = {stat:.3f}")
    check("逐档缩放一致（平均通道差 < 1）", worst < 1.0, f"最大 {worst:.3f}")

    print()
    print("=== 4. 软件本体图标仍是标准方形 ===")
    app_frames = frames_of(app_icon)
    squares = [f for f in app_frames if f.width == f.height]
    check("SpriteSheetTool.ico 全是方形帧", len(squares) == len(app_frames),
          f"实际 {[f'{f.width}x{f.height}' for f in app_frames]}")

    print()
    print(f"ICON RATIO FAILED {len(FAILURES)}" if FAILURES else "ICON RATIO OK")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
