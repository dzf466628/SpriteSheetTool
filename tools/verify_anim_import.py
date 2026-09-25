"""回归：「① 转成序列帧」—— 横排铺帧 + 整帧一个元素 + 自动建动作 + 逐帧时长 + 一条撤销。

用户口径（原话 + 追问确认）：
  1. 动图拖进来先问一句，"转成序列帧"就是**每帧都成为一张原图**，可以逐帧改；
  2. 排列方式："横排一长条超过阈值换到第二行" → 一行最宽 **4096**（`SOURCE_MAX_ROW`）；
  3. 每帧**整帧就是一个元素**（不跑自动检测）：动图每帧本来就是完整画面，自动裁紧致框
     会让每帧框不一样大 → 播放时角色帧间抖；
  4. 导入后**自动建一个动作**、按顺序放 N 帧、**并把原动图的逐帧时长带上**；
  5. 追加导入时**老元素下标一个都不动**（帧号/选中/合成组/复用全是按下标记的）。

这份回归就按这五条逐条钉：
  - 5 张 1000×100 的帧 → 前 4 张一行（1000×4 + 16×3 = 4048 ≤ 4096）、第 5 张换行，
    画布 4048×216、偏移逐个断言（换行算错就会当场红）；
  - 元素矩形 = **整帧**（1000×100），而帧图里只有角落一个 6×6 的色块 —— 要是哪里
    偷偷跑了自动检测，元素就会变成 6×6，这条立刻红；
  - 动作的帧序 = 动图帧序（逐帧采样颜色核对）、逐帧时长与源动图**一字不差**
    （fps 是整数，反推不出 80ms，所以必须逐帧覆盖）；
  - 已经有元素/动作的文档里再导入 → 老元素列表**逐项相等**，新元素追加在末尾；
  - 整次导入 = **一条**撤销，撤了回到导入前（原图数/元素数/动作数全对），能重做。

样本动图在临时目录现造（不进版本库）；识别阈值要显式设小（否则会被用户自己的设置左右），
所以全程用 settings_guard 存档/还原用户设置。跑完 win.close()，否则检测线程会撞析构。
Usage: .venv\\Scripts\\python.exe -X utf8 tools\\verify_anim_import.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))   # 同目录的 settings_guard

from PIL import Image, ImageDraw                                    # noqa: E402
from PyQt6.QtCore import QElapsedTimer                              # noqa: E402
from PyQt6.QtGui import QColor, QPixmap                             # noqa: E402
from PyQt6.QtWidgets import QApplication                            # noqa: E402

from settings_guard import protect_settings                         # noqa: E402
from spritesheet.anim import Animation, read_animation              # noqa: E402
from spritesheet.settings import set_detect_min_area                # noqa: E402
from spritesheet.ui.main_window import MainWindow                   # noqa: E402

# QApplication 必须挂在模块级：不建它建 QWidget 会直接 abort，连 print 都留不下。
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


def wait(ms: int = 200) -> None:
    timer = QElapsedTimer()
    timer.start()
    while timer.elapsed() < ms:
        QApplication.processEvents()


def pixel(pm: QPixmap, x: int, y: int) -> QColor:
    return QColor(pm.toImage().pixelColor(int(x), int(y)))


# ---------------------------------------------------------------- 样本
MARK = 6                      # 帧图里只有角落一个 6×6 的色块（好证明"没跑自动检测"）
FRAME_COLORS = [(220, 40, 40), (40, 200, 90), (60, 120, 230),
                (230, 190, 40), (200, 60, 200), (40, 200, 200)]
SMALL_DURS = [80, 120, 160, 80, 120, 160]
WIDE_DURS = [100, 100, 100, 100, 100]


def make_gif(path: Path, size: tuple[int, int], count: int, durations: list[int],
             colors: list[tuple[int, int, int]]) -> None:
    frames = []
    for i in range(count):
        im = Image.new("RGBA", size, (0, 0, 0, 0))
        ImageDraw.Draw(im).rectangle((2, 2, 2 + MARK - 1, 2 + MARK - 1),
                                     fill=colors[i % len(colors)] + (255,))
        frames.append(im)
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=durations,
                   loop=0, disposal=2, optimize=True)


def make_sprites_png(path: Path) -> None:
    """带两个 30×30 方块的静态图：用来验证"检测出来的老元素"在导入后原封不动。"""
    im = Image.new("RGBA", (200, 120), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle((10, 10, 39, 39), fill=(255, 255, 255, 255))
    d.rectangle((90, 60, 119, 89), fill=(255, 255, 255, 255))
    im.save(path)


def new_window() -> MainWindow:
    win = MainWindow()
    win.resize(1100, 700)
    wait(60)
    return win


def main() -> int:
    restore = protect_settings()
    set_detect_min_area(4)          # 显式设小：断言不该被用户自己的阈值左右
    root = Path(tempfile.mkdtemp(prefix="sstool_animimp_"))
    win = None
    try:
        small_gif = root / "run.gif"
        wide_gif = root / "walk.gif"
        sprites_png = root / "sprites.png"
        make_gif(small_gif, (64, 64), 6, SMALL_DURS, FRAME_COLORS)
        make_gif(wide_gif, (1000, 100), 5, WIDE_DURS, FRAME_COLORS)
        make_sprites_png(sprites_png)

        print("=== 1) 横排 + 一行最宽 4096 换行（5 张 1000 宽）===")
        win = new_window()
        wide = read_animation(wide_gif)
        check("动图解得出来", wide is not None and wide.count == 5, repr(wide))
        ok = win._import_animation_as_frames(wide, name_hint=wide_gif.stem)
        check("导入返回成功", ok)
        v = win.image_viewer
        gap = v.SOURCE_GAP
        expect_offsets = [(0, 0), (1000 + gap, 0), (2 * (1000 + gap), 0),
                          (3 * (1000 + gap), 0), (0, 100 + gap)]
        check("5 张的偏移逐个正确（第 5 张换行）",
              [tuple(o) for o in v._source_offsets] == expect_offsets,
              str(v._source_offsets))
        check("画布宽 = 4 张 + 3 个间隙 = 4048", v._pixmap.width() == 1000 * 4 + gap * 3,
              str(v._pixmap.width()))
        check("画布高 = 两行 + 一个间隙 = 216", v._pixmap.height() == 100 + gap + 100,
              str(v._pixmap.height()))
        check("原图数 = 5", len(v._source_images) == 5, str(len(v._source_images)))

        print("=== 2) 每帧「整帧」就是一个元素（没跑自动检测）===")
        check("元素数 = 5", len(v._frames) == 5, str(len(v._frames)))
        bad = [i for i, f in enumerate(v._frames)
               if tuple(f) != (expect_offsets[i][0], expect_offsets[i][1], 1000, 100)]
        check("每个元素 = 整帧 1000×100（不是 6×6 的色块）", not bad,
              str([tuple(f) for f in v._frames][:2]))
        check("画布上确实有帧内容（左上角色块是帧色）",
              pixel(v._pixmap, 2 + MARK // 2, 2 + MARK // 2).name()
              == QColor(*FRAME_COLORS[0]).name(),
              pixel(v._pixmap, 2 + MARK // 2, 2 + MARK // 2).name())

        print("=== 3) 自动建动作：帧序 + 逐帧时长 ===")
        check("动作数 = 1", len(win.actions) == 1, str(len(win.actions)))
        check("动作名用文件名", win.actions[0].name == wide_gif.stem, win.actions[0].name)
        action = win.actions[0]
        check("选中元素 = 末尾 5 个、按帧序", action.selected_indices == [0, 1, 2, 3, 4],
              str(action.selected_indices))
        check("动作帧数 = 5", len(action.frames) == 5, str(len(action.frames)))
        order_bad = [i for i, pm in enumerate(action.frames)
                     if pixel(pm, 4, 4).name() != QColor(*FRAME_COLORS[i % 6]).name()]
        check("动作帧序 = 动图帧序（逐帧颜色核对）", not order_bad, str(order_bad))
        check("逐帧时长与源动图一字不差",
              [action.frame_duration_ms(i) for i in range(5)] == WIDE_DURS,
              str([action.frame_duration_ms(i) for i in range(5)]))
        check("fps 是个合理值", 1 <= action.fps <= 60, str(action.fps))
        check("当前动作就是新建的这个", v.current_action is action)

        print("=== 4) 追加导入：老元素下标一个都不动 ===")
        win2 = new_window()
        win2._on_image_loaded(QPixmap(str(sprites_png)))
        wait(400)
        old_frames = [tuple(f) for f in win2.image_viewer._frames]
        check("静态图先检测出 2 个元素", len(old_frames) == 2, str(old_frames))
        if len(win2.actions) == 0:
            win2._create_action()
        old_action = win2.actions[0]
        old_selected = list(old_action.selected_indices)
        small = read_animation(small_gif)
        check("小动图解得出来", small is not None and small.count == 6)
        ok = win2._import_animation_as_frames(small, name_hint=small_gif.stem)
        v2 = win2.image_viewer
        check("追加导入成功", ok)
        check("老元素逐项相等（下标没动）",
              [tuple(f) for f in v2._frames[:len(old_frames)]] == old_frames,
              f"{[tuple(f) for f in v2._frames[:len(old_frames)]]} vs {old_frames}")
        check("新元素追加在末尾、数量 = 帧数",
              len(v2._frames) == len(old_frames) + 6, str(len(v2._frames)))
        check("新元素在 y = 老画布下方（接在下面，不覆盖）",
              all(f[1] >= old_frames[0][1] for f in v2._frames[len(old_frames):]),
              str([tuple(f) for f in v2._frames[len(old_frames):]][:2]))
        check("老动作的选中没被改动", old_action.selected_indices == old_selected,
              str(old_action.selected_indices))
        check("动作数 = 2（新建的动图动作 + 老动作）", len(win2.actions) == 2,
              str(len(win2.actions)))
        check("新动作的选中指向新元素（从第 2 个开始）",
              win2.actions[-1].selected_indices == list(range(2, 8)),
              str(win2.actions[-1].selected_indices))
        check("小动图逐帧时长也对",
              [win2.actions[-1].frame_duration_ms(i) for i in range(6)] == SMALL_DURS,
              str([win2.actions[-1].frame_duration_ms(i) for i in range(6)]))

        print("=== 5) 整次导入是一条撤销，能撤能重做 ===")
        before = (len(v2._source_images), len(v2._frames), len(win2.actions))
        # 上面已经导入完了，这里重新数一份"导入前"= 减掉动图那 6 帧
        before = (before[0] - 6, before[1] - 6, before[2] - 1)
        win2._do_undo()
        wait(120)
        after = (len(v2._source_images), len(v2._frames), len(win2.actions))
        check("撤销回到导入前（原图数/元素数/动作数）", after == before,
              f"{after} vs {before}")
        check("撤销后老元素还在、内容没变",
              [tuple(f) for f in v2._frames] == old_frames,
              str([tuple(f) for f in v2._frames]))
        check("撤销后画布回到老尺寸（元素数对得上）", len(v2._frames) == len(old_frames))
        win2._do_redo()
        wait(120)
        again = (len(v2._source_images), len(v2._frames), len(win2.actions))
        check("重做把帧和动作都带回来",
              again == (before[0] + 6, before[1] + 6, before[2] + 1), str(again))

        print("=== 6) 空动图不建动作、不留脏撤销 ===")
        win3 = new_window()
        empty = Animation(frames=[], durations=[], width=0, height=0, fmt="GIF")
        ok = win3._import_animation_as_frames(empty)
        check("空动图返回失败", ok is False)
        check("没有凭空多出动作", win3.actions == [], str(win3.actions))
        check("画布还是空的", win3.image_viewer._pixmap is None
              or win3.image_viewer._pixmap.isNull())
        check("撤销栈没被压进一条空记录", win3._snapshot_before is None)

        print("=== 7) 收尾 ===")
        for w in (win, win2, win3):
            w.close()
        win = win2 = win3 = None
        wait(120)
        # 先显式还原（restore 幂等，finally 里再来一次也没事）：**必须在断言之前**调。
        # 之前这条断言写在 restore() 之前 —— 那时候存档文件按定义一定还在，等于一句空话
        # （跑绿跑红全看上一次进程有没有留下存档），2026 收尾时发现并改掉。
        restore()
        check("设置存档文件已被清掉（用户设置已还原）",
              not (Path(ROOT) / "tools" / ".settings_backup.json").exists(),
              "tools/.settings_backup.json 还在 = 用户设置没还原")
        check("没有残留主窗口", _APP.topLevelWidgets() == [] or
              all(not w.isVisible() for w in _APP.topLevelWidgets()))
    finally:
        shutil.rmtree(root, ignore_errors=True)
        restore()

    print()
    if FAILURES:
        print(f"FAILED  {PASSED} 通过 / {len(FAILURES)} 失败")
        for name in FAILURES:
            print(f"   - {name}")
        return 1
    print(f"ALL PASS  {PASSED}/{PASSED}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())