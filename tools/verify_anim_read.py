"""回归：动图（GIF / WebP）判定 + 解帧 + 逐帧时长 + 体积护栏。

用户要的功能是"拖进 GIF/WebP，是动图就问一句：转序列帧 / 当单张改"。这一步只做**解帧**，
所以这份回归只钉四件事：

1. **判定**：真动图（>1 帧）为 True；单帧 GIF/WebP、普通 PNG、坏文件、以及 PNG 动图
   （APNG，不在本轮范围）都为 False —— 判定错了会朝静态图乱弹询问。
2. **解帧取的是"合成好"的帧**：动图里多数帧只改局部（GIF 的 disposal、WebP 的 ANMF
   局部矩形），直接拿原始帧会缺上一层的内容。这里用"方块逐帧移动 + 只存变化区域"的
   样本，逐帧断言方块在它该在的位置、并且**上一帧的位置是空的**。
3. **逐帧时长**：GIF 从 info 读；WebP 在 Pillow 12.3 上读出来是 None（实测），文件里其实
   有 —— 所以另解一遍 RIFF/ANMF。两种格式都断言 80/120/160 这组值。
4. **护栏**：帧数、总像素超阈值时要给一句人话提醒；正常大小不给。

样本动图在系统临时目录里现造（不进版本库），跑完全程 offscreen，不读也不改用户设置。
Usage: .venv\\Scripts\\python.exe -X utf8 tools\\verify_anim_read.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication

from spritesheet.anim import (ANIM_EXT, DEFAULT_FRAME_MS, Animation,
                              frames_sheet_size, is_animated_file, pil_to_pixmap,
                              read_animation, size_warning)

# QApplication 必须挂在模块级：不建它就用 QPixmap/QImage 会直接 abort（连 print 都没有）。
_APP = QApplication.instance() or QApplication(sys.argv)

PASSED = 0
FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [OK]   {label}")
    else:
        FAILURES.append(label)
        print(f"  [FAIL] {label}   {detail}")


# ---------------------------------------------------------------- 样本
FRAME_W = FRAME_H = 64
BLOCK = 13          # 方块边长（每帧只挪 8px，所以局部帧一定是"块"而不是整张）
STEP = 8
DURS = [80, 120, 160, 80, 120, 160]
RED = (220, 40, 40)
RECT_X = [8 + STEP * i for i in range(len(DURS))]


def build_frames() -> list[Image.Image]:
    """每帧只有一个方块、位置逐帧右移 8px：上一帧的位置在本帧必须是空的。"""
    out = []
    for x in RECT_X:
        im = Image.new("RGBA", (FRAME_W, FRAME_H), (0, 0, 0, 0))
        ImageDraw.Draw(im).rectangle((x, 20, x + BLOCK - 1, 20 + 21 - 1), fill=RED + (255,))
        out.append(im)
    return out


def make_samples(root: Path) -> dict[str, Path]:
    frames = build_frames()
    gif = root / "move.gif"
    webp = root / "move.webp"
    # disposal=2（还原背景）：每帧只留自己的方块，跟 WebP 的"替换"语义一致，
    # 断言就能写成"方块在该在的地方、别处是空的"
    frames[0].save(gif, save_all=True, append_images=frames[1:], duration=DURS,
                   loop=0, disposal=2, optimize=True)
    # WebP 这里**故意用默认的有损存法**：真实世界里的动图 WebP 多数是有损的，而且实测
    # Pillow 的 `lossless=True` 会把整段动图存成**没有 alpha 的 RGB**（透明处变黑），
    # 那样样本就不代表真实文件了。有损会把 (220,40,40) 压偏一点，所以色值断言带容差。
    frames[0].save(webp, save_all=True, append_images=frames[1:], duration=DURS, loop=0)
    still_gif = root / "still.gif"
    frames[0].save(still_gif, format="GIF")
    still_webp = root / "still.webp"
    frames[0].convert("RGB").save(still_webp, format="WEBP")
    still_png = root / "still.png"
    frames[0].save(still_png)
    apng = root / "multi.png"
    try:
        frames[0].save(apng, save_all=True, append_images=frames[1:], duration=DURS)
    except Exception:  # noqa: BLE001 - 老 Pillow 存不了 APNG 就跳过那一条
        apng = root / "nonexistent.png"
    broken = root / "broken.gif"
    broken.write_text("这不是图片", encoding="utf-8")
    return {"gif": gif, "webp": webp, "still_gif": still_gif, "still_webp": still_webp,
            "still_png": still_png, "apng": apng, "broken": broken}


def is_red(color: QColor, tol: int = 24) -> bool:
    """够红就算对：有损 WebP 会把 (220,40,40) 压偏一点，断言不该输给编码器的色偏。"""
    return (color.red() > 200 - tol and color.green() < 60 + tol
            and color.blue() < 60 + tol and color.alpha() > 200)


def pixel(pm, x: int, y: int) -> QColor:
    return QColor(pm.toImage().pixelColor(x, y))


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="sstool_anim_"))
    try:
        files = make_samples(root)

        print("=== 1) 判定：只有真动图才算动图 ===")
        check("动图 GIF 判为动图", is_animated_file(files["gif"]))
        check("动图 WebP 判为动图", is_animated_file(files["webp"]))
        check("单帧 GIF 不判为动图", not is_animated_file(files["still_gif"]))
        check("单帧 WebP 不判为动图", not is_animated_file(files["still_webp"]))
        check("普通 PNG 不判为动图", not is_animated_file(files["still_png"]))
        check("坏文件不判为动图（不抛异常）", not is_animated_file(files["broken"]))
        check("APNG 不误判成本轮支持的动图",
              not is_animated_file(files["apng"]), "APNG 本轮不支持，必须当静态单图")
        check("后缀集合就是 gif/webp", ANIM_EXT == {".gif", ".webp"}, str(ANIM_EXT))

        print("=== 2) 解帧：帧数、尺寸、帧内容按规则合成 ===")
        for key, name in (("gif", "GIF"), ("webp", "WebP")):
            anim = read_animation(files[key])
            check(f"{name} 解得出来", isinstance(anim, Animation), repr(anim))
            if not isinstance(anim, Animation):
                continue
            check(f"{name} 帧数 = 6", anim.count == len(DURS), str(anim.count))
            check(f"{name} 尺寸 = 64×64", (anim.width, anim.height) == (FRAME_W, FRAME_H),
                  f"{anim.width}×{anim.height}")
            check(f"{name} 没有空帧", all(not pm.isNull() for pm in anim.frames))
            bad = []
            for index, pm in enumerate(anim.frames):
                x = RECT_X[index]
                # 方块正中必须是红的
                if not is_red(pixel(pm, x + BLOCK // 2, 30)):
                    bad.append(f"帧{index} 方块缺失({pixel(pm, x + BLOCK // 2, 30).name()})")
                # 上一帧方块的位置必须是空的（说明这一帧不是"累积"出来的残影）
                if index and pixel(pm, RECT_X[index - 1] + BLOCK // 2, 30).alpha() != 0:
                    bad.append(f"帧{index} 残留上一帧")
            check(f"{name} 每帧方块位置正确、无残留", not bad, "; ".join(bad[:3]))
            check(f"{name} 透明处是透明的（alpha 没被丢掉）",
                  pixel(anim.frames[0], 60, 60).alpha() == 0,
                  str(pixel(anim.frames[0], 60, 60).alpha()))

        print("=== 3) 逐帧时长：GIF 读 info，WebP 自己解 ANMF ===")
        for key, name in (("gif", "GIF"), ("webp", "WebP")):
            anim = read_animation(files[key])
            got = list(anim.durations) if anim else []
            check(f"{name} 逐帧时长 = {DURS}", got == DURS, str(got))

        print("=== 4) 解不出来的情况要有兜底，不能崩 ===")
        check("坏文件 read_animation 返回 None", read_animation(files["broken"]) is None)
        check("单帧文件 read_animation 返回 None（调用方按单图处理）",
              read_animation(files["still_png"]) is None)
        check("不存在的路径也不抛异常", read_animation(root / "nope.gif") is None)
        check("兜底时长是正数（读不到 duration 时用它）",
              isinstance(DEFAULT_FRAME_MS, int) and DEFAULT_FRAME_MS > 0, str(DEFAULT_FRAME_MS))

        print("=== 5) 体积护栏：超阈值提醒、正常不提醒 ===")
        check("10 帧 64×64 不提醒", size_warning(10, 64, 64) is None)
        check("300 帧要提醒帧数", bool(size_warning(300, 64, 64)),
              str(size_warning(300, 64, 64)))
        check("80 帧 × 1024² 要提醒内存", bool(size_warning(80, 1024, 1024)),
              str(size_warning(80, 1024, 1024)))
        check("单帧永远不提醒", size_warning(1, 8192, 8192) is None)
        warn = size_warning(300, 64, 64) or ""
        check("提醒里带上具体帧数（用户看得懂）", "300" in warn, warn)

        print("=== 6) 布局预演：一行最宽 4096、超了换行 ===")
        from PyQt6.QtGui import QPixmap
        row = QPixmap(1000, 100)
        col = [row] * 5
        size = frames_sheet_size(col, 4096, 16)
        # 1000×4 + 16×3 = 4048 ≤ 4096，放第 5 个要 5064 > 4096 → 第二行
        check("5 张 1000 宽排成两行：宽 4048", size.width() == 1000 * 4 + 16 * 3, str(size.width()))
        check("5 张 1000 宽排成两行：高 216", size.height() == 100 + 16 + 100, str(size.height()))
        single = frames_sheet_size([QPixmap(5000, 40)], 4096, 16)
        check("单张比上限还宽时自成一行、画布按它算",
              (single.width(), single.height()) == (5000, 40), f"{single.width()}×{single.height()}")
        check("空列表不炸", frames_sheet_size([], 4096, 16).width() >= 1)

        print("=== 7) 转换函数：不 copy 会读到野指针（钉住这个坑）===")
        src = Image.new("RGBA", (4, 4), (10, 20, 30, 255))
        pm = pil_to_pixmap(src)
        check("转换后尺寸正确", (pm.width(), pm.height()) == (4, 4))
        check("转换后颜色正确", pixel(pm, 1, 1).name() == QColor(10, 20, 30).name(),
              pixel(pm, 1, 1).name())
        del src
        import gc
        gc.collect()
        check("源图回收后像素依然正确（copy 生效）",
              pixel(pm, 2, 2).name() == QColor(10, 20, 30).name(), pixel(pm, 2, 2).name())
        from spritesheet.ui.key_panel import pil_to_pixmap as from_panel
        check("key_panel 里导出的还是同一个实现（只留一份）",
              from_panel is pil_to_pixmap, "两处实现分叉了")

        print()
        if FAILURES:
            print(f"FAILED  {PASSED} 通过 / {len(FAILURES)} 失败")
            for name in FAILURES:
                print(f"   - {name}")
            return 1
        print(f"ALL PASS  {PASSED}/{PASSED}")
        return 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())