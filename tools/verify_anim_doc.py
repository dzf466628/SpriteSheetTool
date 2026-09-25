"""回归：「② 以单张图片修改」的动图文档 —— 单帧画布 + 改动同步全部帧 + 操作链撤销 + 导出壳动作。

用户口径（原话 + 追问确认）：
  1. 拖进动图选"以单张图片修改"= 画布上**只显示一帧**，另有「查看第几帧」滑块，
     **滑块只影响浏览，编辑照样作用于全部帧**；
  2. 所有破坏性编辑同步到全部帧：抠除背景、画笔/橡皮、翻转/旋转/缩放，以及以后新增的；
  3. 当成一种新的「动图文档」：**不玩元素/动作那套**；
  4. 导出走现有导出弹窗（GIF 写回动图、PNG 每帧一张、JSON 带 duration）；
  5. 撤销：我定的方案（已报备）—— 保留 original + 操作链重放，不存整帧快照。

这份回归逐条钉：
  - 画布上**没有元素**（`_frames == []`）、原图只有这一帧、也不跑自动检测；
  - 滑块切帧只换显示的那一帧，**帧数据一个像素都不变**（"只影响浏览"）；
  - 翻转/旋转：**每一帧**都真的变了（逐帧采样，不是只改当前帧）；
  - 画笔：写进操作链后重放到全部帧，而且**当前帧不会被涂两遍**（这条最容易写错）；
  - 抠背景：同一套参数对每帧各抠一遍，逐帧断言底色真的透明了；
  - 撤销/重做走操作链（Ctrl+Z 一次退一条），重做能回到一样的结果；
  - 预览/导出用的壳动作：帧数/帧序/逐帧时长与文档一致（导出弹窗就是读它）；
  - 无元素文档里那三个用不上的工具是禁用的（不留点着没反应的死按钮）。

样本动图临时造；跑完 win.close()（否则检测线程撞析构）；全程 settings_guard 存档用户设置。
Usage: .venv\\Scripts\\python.exe -X utf8 tools\\verify_anim_doc.py
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
from PyQt6.QtCore import QElapsedTimer, QPointF                     # noqa: E402
from PyQt6.QtGui import QColor, QPainter, QPixmap                      # noqa: E402
from PyQt6.QtWidgets import QApplication                            # noqa: E402

from settings_guard import protect_settings                         # noqa: E402
from spritesheet.anim import read_animation                          # noqa: E402
from spritesheet.project_file import read_project                   # noqa: E402
from spritesheet.settings import set_detect_min_area                # noqa: E402
from spritesheet.ui.anim_doc import KeyOp, StrokeOp, TransformOp    # noqa: E402
from spritesheet.ui.main_window import MainWindow                   # noqa: E402

_APP = QApplication.instance() or QApplication(sys.argv)

PASSED = 0
FAILURES: list[str] = []

W = H = 80
BG = (245, 245, 245)
RED = (210, 50, 50)
DURS = [70, 110, 150, 70, 110, 150]


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [OK]   {label}")
    else:
        FAILURES.append(label)
        print(f"  [FAIL] {label}   {detail}")


def wait(ms: int = 150) -> None:
    timer = QElapsedTimer()
    timer.start()
    while timer.elapsed() < ms:
        QApplication.processEvents()


def pixel(pm, x: int, y: int) -> QColor:
    return QColor(pm.toImage().pixelColor(int(x), int(y)))


def to_canvas(viewer, x: float, y: float) -> QPointF:
    """图片坐标 → 画布坐标（begin_paint_stroke 收的是画布坐标，内部再换算成图片坐标）。"""
    return QPointF(x * viewer._zoom + viewer._offset.x(),
                   y * viewer._zoom + viewer._offset.y())


def red_bbox(pm) -> tuple | None:
    """红色主体（红方块）的外接矩形 —— 用整块的位置验"变换真的作用到像素上了"。"""
    img = pm.toImage()
    xs, ys = [], []
    for y in range(img.height()):
        for x in range(img.width()):
            color = img.pixelColor(x, y)
            if color.red() > 180 and color.green() < 120:
                xs.append(x)
                ys.append(y)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def is_mirrored_of(pm, src) -> bool:
    """pm 是不是 src 的左右镜像（整图隔行逐像素比，容不下「采样碰巧对上」）。"""
    a = pm.toImage().convertToFormat(pm.toImage().Format.Format_RGBA8888)
    b = src.toImage().convertToFormat(src.toImage().Format.Format_RGBA8888)
    if a.size() != b.size():
        return False
    for y in range(0, a.height(), 7):          # 隔行扫，整图逐像素太重、没必要
        for x in range(a.width()):
            if a.pixelColor(x, y) != b.pixelColor(a.width() - 1 - x, y):
                return False
    return True


def make_gif(path: Path) -> None:
    """6 帧：白底 + 一个逐帧右移的红方块（每帧都能一眼看出"变没变、位置对不对"）。"""
    frames = []
    for i in range(len(DURS)):
        im = Image.new("RGBA", (W, H), BG + (255,))
        ImageDraw.Draw(im).rectangle((6 + i * 6, 30, 6 + i * 6 + 19, 49), fill=RED + (255,))
        frames.append(im)
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=DURS,
                   loop=0, disposal=2, optimize=True)


def write_broken(path: Path) -> None:
    """一个后缀是 .gif 但其实不是图的文件：验"坏文件不许炸、也不许被当动图"。"""
    path.write_bytes(b"GIF89a\x00\x00\x00\x00not really a gif")


def main() -> int:
    restore = protect_settings()
    # 显式设小识别阈值：本回归第 8/9 节会载入普通图片走自动检测，别让**用户自己调的阈值**
    # 决定断言结果（这是本项目踩过的坑：阈值一改，别人的回归就无缘无故变红）
    set_detect_min_area(4)
    root = Path(tempfile.mkdtemp(prefix="sstool_animdoc_"))
    win = None
    try:
        gif = root / "hero.gif"
        make_gif(gif)
        write_broken(root / "broken.gif")
        anim = read_animation(gif)
        check("动图解得出来（6 帧）", anim is not None and anim.count == 6, repr(anim))

        print("=== 1) 进动图文档：单帧画布、没有元素、工具收好 ===")
        win = MainWindow()
        win.resize(1100, 700)
        win.show()          # isVisible() 要窗口真的显示过才算数（offscreen 下也一样）
        wait(80)
        ok = win._open_animation_document(anim, name_hint=gif.stem, path=str(gif))
        check("进得去动图文档", ok)
        doc = win._anim_doc
        v = win.image_viewer
        check("文档帧数 = 6", doc is not None and doc.count == 6, str(doc and doc.count))
        check("画布上没有任何元素（不玩元素那套）", v._frames == [], str(v._frames))
        # 光断言"没有元素"会被"检测还在跑"蒙过去，所以再看一眼：动图文档**压根没起**
        # 检测线程（普通载图那条路会起）。两条一起才是"真的没跑检测"。
        check("动图文档压根不跑自动检测（没起检测线程）",
              v._detect_thread is None and not v._detect_threads,
              f"thread={v._detect_thread} list={v._detect_threads}")
        check("原图区只有当前这一帧", len(v._source_images) == 1, str(len(v._source_images)))
        check("编辑范围不是「只改当前帧」：没有任何按帧的选中", v._selected_sources == set())
        check("横条显示出来了", win.anim_doc_bar.isVisible())
        check("「动画设置」横条让位（动图文档里没有动作）", not win.anim_bar.isVisible())
        check("滑块范围 = 0..5", win.anim_doc_bar.slider.maximum() == 5,
              str(win.anim_doc_bar.slider.maximum()))
        check("用不上的工具被禁用（原图选择）", not v._v_toolbar.btn_source.isEnabled())
        check("用不上的工具被禁用（合成）", not v._toolbar.btn_compose.isEnabled())
        check("用不上的工具被禁用（复用）", not v._toolbar.btn_reuse.isEnabled())
        check("抠图/画笔还开着能用", v._v_toolbar.btn_key.isEnabled()
              and v._v_toolbar.btn_brush.isEnabled())
        check("禁用原因写在 tooltip 里（不是「点了没反应」）",
              "动图文档" in v._v_toolbar.btn_source.toolTip(),
              v._v_toolbar.btn_source.toolTip())

        print("=== 2) 滑块只影响浏览：切帧不改任何帧数据 ===")
        before_frames = [pm.copy() for pm in doc.frames]
        before_pixels = [pixel(pm, 10, 40).name() for pm in before_frames]
        win.anim_doc_bar.slider.setValue(4)
        wait(80)
        check("切到第 5 帧后 doc.index = 4", doc.index == 4, str(doc.index))
        check("画布显示的就是第 5 帧（红方块已右移）",
              pixel(v._pixmap, 6 + 4 * 6 + 9, 40).name() == QColor(*RED).name(),
              pixel(v._pixmap, 6 + 4 * 6 + 9, 40).name())
        check("帧数据一个像素都没变",
              [pixel(pm, 10, 40).name() for pm in doc.frames] == before_pixels)
        check("预览跟着当前帧给了图源（导出原图预览用）",
              win.preview._source_pixmap is v._pixmap)
        win.anim_doc_bar.slider.setValue(0)
        wait(60)

        print("=== 3) 翻转/旋转：每一帧都真的变了 ===")
        # 逐帧和"原帧的镜像"整图比对 —— 比采样几个点稳（第 5 帧的红方块正好在正中间，
        # 左右翻转后落在同一位置，采样点法会误判成"没翻转"）
        originals = [pm.copy() for pm in doc.frames]
        doc.apply(TransformOp("flip_h", label="左右翻转"), v)
        win._refresh_anim_doc_views()
        flipped_ok = [is_mirrored_of(frame, src) for frame, src in zip(doc.frames, originals)]
        check("6 帧全部左右翻转（逐帧与原帧镜像比对）", all(flipped_ok), str(flipped_ok))
        check("翻转后第 1 帧的红方块跑到了右边（画面真的变了）",
              pixel(doc.frames[0], W - 1 - 6 - 9, 40).name() == QColor(*RED).name()
              and pixel(doc.frames[0], 6 + 9, 40).name() == QColor(*BG).name(),
              f"{pixel(doc.frames[0], W - 1 - 6 - 9, 40).name()} / "
              f"{pixel(doc.frames[0], 6 + 9, 40).name()}")
        sizes_before = [(pm.width(), pm.height()) for pm in doc.frames]
        doc.apply(TransformOp("rot90", label="转 90°"), v)
        win._refresh_anim_doc_views()
        check("转 90° 后每帧宽高互换（80×80 的样本上这条是恒真的，留着当个记录）",
              [(pm.width(), pm.height()) for pm in doc.frames]
              == [(h, w) for w, h in sizes_before],
              str([(pm.width(), pm.height()) for pm in doc.frames]))
        # 上面那条在方图上等于什么都没验，所以再验**内容真的转了**：
        # 翻转后的红方块外接矩形是 (54,30,73,49) → 顺时针 90° 后应变成 (30,54,49,73)
        # （实测值，不是推的：Qt 顺时针旋转在 y 向下的坐标里就是 (x,y) → (H-1-y, x)）。
        # 外接矩形整块比对，比采样几个点稳，"变换没生效/转错方向"都跑不掉。
        rotated = doc.frames[0]
        check("转 90° 真的转了内容（红块换到了左下带）",
              red_bbox(rotated) == (30, 54, 49, 73), str(red_bbox(rotated)))
        check("6 帧都转了（逐帧的红块位置各自都对）",
              [red_bbox(pm) for pm in doc.frames]
              == [(30, 54 - 6 * i, 49, 73 - 6 * i) for i in range(doc.count)],
              str([red_bbox(pm) for pm in doc.frames]))
        check("横条上的尺寸文字跟着变", win.anim_doc_bar.lbl_frame.text().count("80×80") == 1,
              win.anim_doc_bar.lbl_frame.text())

        print("=== 4) 画笔：重放到全部帧，而且当前帧不会被涂两遍 ===")
        win2 = MainWindow()
        win2.resize(1100, 700)
        win2.show()
        wait(80)
        win2._open_animation_document(anim, name_hint="笔", path=str(gif))
        v2 = win2.image_viewer
        doc2 = win2._anim_doc
        v2.set_paint_color(QColor("#00FF00"))
        v2.set_paint_tool("brush")      # 画笔模式：begin_paint_stroke 只认 brush/eraser
        v2.brush_size = 6
        # 用查看器的真实落笔流程：开始一笔 → 拖一段 → 松手（主窗口会在信号里记操作）
        v2.begin_paint_stroke(to_canvas(v2, 40, 12))
        v2.paint_stroke_to(to_canvas(v2, 52, 12))
        v2.end_paint_stroke()
        wait(120)
        check("这一笔记成了一条操作", len(doc2.ops) == 1 and isinstance(doc2.ops[0], StrokeOp),
              str(doc2.ops))
        op = doc2.ops[0]
        check("记下的线段是 2 段（起点 + 拖一次）", len(op.segments) == 2,
              str(len(op.segments)))
        check("记下的是图片坐标（不在画布坐标上）",
              abs(op.segments[0][0].x() - 40) < 1.5 and abs(op.segments[0][0].y() - 12) < 1.5,
              str((op.segments[0][0].x(), op.segments[0][0].y())))
        strokes = [pixel(pm, 46, 12).name() == QColor("#00FF00").name() for pm in doc2.frames]
        check("6 帧全部有这一笔（逐帧采样中点）", all(strokes), str(strokes))
        # "当前帧被涂两遍"的症状：同一条线画两遍在橡皮下会擦得更狠、在画笔下颜色更深
        # （不透明色看不出深浅），所以用**橡皮**验：只擦一遍的话线只该被清掉一次。
        check("当前帧和其他帧的笔迹完全一致（没被涂两遍）",
              [pixel(pm, 46, 12).name() for pm in doc2.frames].count(QColor("#00FF00").name())
              == len(doc2.frames),
              str([pixel(pm, 46, 12).name() for pm in doc2.frames]))

        print("=== 5) 抠背景：同一套参数对每帧各抠一遍 ===")
        win3 = MainWindow()
        win3.resize(1100, 700)
        win3.show()
        wait(80)
        win3._open_animation_document(anim, name_hint="抠", path=str(gif))
        v3 = win3.image_viewer
        doc3 = win3._anim_doc
        check("抠之前每帧都是白底",
              all(pixel(pm, 2, 2).name() == QColor(*BG).name() for pm in doc3.frames))
        doc3.apply(KeyOp(params={"tolerance": 40, "soft": 0, "edge_only": True,
                                 "shrink": 0, "despill": 0}, color=BG), v3)
        win3._refresh_anim_doc_views()
        alphas = [pixel(pm, 2, 2).alpha() for pm in doc3.frames]
        check("6 帧的白底全部被抠掉（逐帧 alpha）", all(a == 0 for a in alphas), str(alphas))
        keep = [pixel(pm, 6 + i * 6 + 9, 40).name() == QColor(*RED).name()
                for i, pm in enumerate(doc3.frames)]
        check("每帧的主体（红方块）都还在", all(keep), str(keep))

        print("=== 6) 撤销/重做走操作链 ===")
        win4 = MainWindow()
        win4.resize(1100, 700)
        win4.show()
        wait(80)
        win4._open_animation_document(anim, name_hint="撤", path=str(gif))
        v4 = win4.image_viewer
        doc4 = win4._anim_doc
        snapshot0 = [pixel(pm, 2, 2).name() for pm in doc4.frames]
        doc4.apply(TransformOp("flip_h", label="左右翻转"), v4)
        win4._refresh_anim_doc_views()
        after_flip = [pixel(pm, 2, 2).name() for pm in doc4.frames]
        win4._do_undo()
        wait(60)
        check("撤销一条操作后回到原样",
              [pixel(pm, 2, 2).name() for pm in doc4.frames] == snapshot0,
              str([pixel(pm, 2, 2).name() for pm in doc4.frames]))
        check("撤销后操作链空、重做链有 1 条",
              doc4.ops == [] and len(doc4.redone) == 1, str((len(doc4.ops), len(doc4.redone))))
        win4._do_redo()
        wait(60)
        check("重做回到翻转后的样子",
              [pixel(pm, 2, 2).name() for pm in doc4.frames] == after_flip)
        check("重做后操作链又有 1 条", len(doc4.ops) == 1)
        # 新操作会作废重做链
        win4._do_undo()
        wait(40)
        doc4.apply(TransformOp("flip_v", label="上下翻转"), v4)
        check("撤销之后又来一条新操作 → 重做链作废", doc4.redone == [])

        print("=== 7) 导出/预览用的壳动作：帧序与逐帧时长 ===")
        action = doc3.synthetic_action()
        check("壳动作帧数 = 文档帧数", len(action.frames) == doc3.count,
              f"{len(action.frames)} vs {doc3.count}")
        check("壳动作逐帧时长 = 源动图时长",
              [action.frame_duration_ms(i) for i in range(6)] == DURS,
              str([action.frame_duration_ms(i) for i in range(6)]))
        check("壳动作帧就是文档当前帧（像素同一份）",
              all(action.frames[i] is doc3.frames[i] for i in range(doc3.count)))
        check("预览区拿到的就是这个壳动作", win3._anim_preview_action is not None
              and len(win3.preview._actions) == 1,
              str(len(win3.preview._actions)))
        anim_names = [a.name for a in win3.preview._actions]
        check("导出弹窗只会看到这一个动作（动图文档没有别的编排）",
              anim_names == [doc3.name], str(anim_names))

        print("=== 8) 切到普通工程要能干净退回 ===")
        win3._on_image_loaded(anim.frames[0])
        wait(300)
        check("导入普通图片后动图文档退场", win3._anim_doc is None, str(win3._anim_doc))
        check("横条收起来了", not win3.anim_doc_bar.isVisible())
        check("「动画设置」横条回来了", win3.anim_bar.isVisible())
        check("工具全部放回来（原图选择）", v3._v_toolbar.btn_source.isEnabled())
        check("工具全部放回来（合成/复用）",
              v3._toolbar.btn_compose.isEnabled() and v3._toolbar.btn_reuse.isEnabled())
        check("工具提示恢复成原来的（不是「动图文档：禁用」）",
              "动图文档" not in v3._v_toolbar.btn_source.toolTip(),
              v3._v_toolbar.btn_source.toolTip())

        print("=== 9) 入口：拖入真动图才问，静态图不问，取消什么都不做 ===")
        import spritesheet.ui.main_window as mw
        static_gif = root / "still.gif"
        Image.new("RGBA", (W, H), BG + (255,)).save(static_gif)
        saved_ask = mw.ask_animation_mode
        answers = {"mode": "cancel", "asked": 0}

        def fake_ask(parent, anim, name_hint=""):
            answers["asked"] += 1
            answers["anim"] = anim
            answers["name"] = name_hint
            return answers["mode"]

        win5 = MainWindow()
        win5.resize(1100, 700)
        win5.show()
        wait(80)
        try:
            mw.ask_animation_mode = fake_ask
            answers["asked"] = 0
            check("静态 gif 一声不响（当普通图片，不问）",
                  win5._handle_animated_path(str(static_gif)) is False)
            check("静态 gif 没弹窗", answers["asked"] == 0, str(answers["asked"]))
            check("png 也不问", win5._handle_animated_path(str(root / "nope.png")) is False)
            check("坏文件不当动图（不问、也不炸）",
                  win5._handle_animated_path(str(root / "broken.gif")) is False)

            answers["mode"] = "cancel"
            answers["asked"] = 0
            check("真动图会被拦下来问一句", win5._handle_animated_path(str(gif)) is True)
            check("真的问了、问的是这张动图", answers["asked"] == 1
                  and answers["anim"].count == 6, str(answers.get("anim")))
            check("按取消 → 什么都不导入（画布还是空的）",
                  win5._anim_doc is None and win5.actions == []
                  and (win5.image_viewer._pixmap is None
                       or win5.image_viewer._pixmap.isNull()))

            answers["mode"] = "doc"
            win5._handle_animated_path(str(gif))
            check("选「以单张图片修改」→ 进动图文档", win5._anim_doc is not None
                  and win5._anim_doc.count == 6, str(win5._anim_doc))

            answers["mode"] = "frames"
            win6 = MainWindow()
            win6.resize(1100, 700)
            win6.show()
            wait(80)
            win6._handle_animated_path(str(gif))
            wait(200)
            check("选「转成序列帧」→ 进原来那条序列帧路（不启动图文档）",
                  win6._anim_doc is None and len(win6.actions) == 1
                  and len(win6.image_viewer._source_images) == 6,
                  f"doc={win6._anim_doc} actions={len(win6.actions)} "
                  f"sources={len(win6.image_viewer._source_images)}")
        finally:
            mw.ask_animation_mode = saved_ask
        # win5/win6 不在这里关：统一到第 10 节收尾（一处 close + deleteLater，别分两套）

        print("=== 10) 存进 .sstool 再打开：帧、时长、操作链、撤销都在 ===")
        win7 = MainWindow()
        win7.resize(1100, 700)
        win7.show()
        wait(80)
        win7._open_animation_document(anim, name_hint=gif.stem, path=str(gif))
        v7 = win7.image_viewer
        doc7 = win7._anim_doc
        win7._on_anim_doc_transform("flip_h")          # 第 1 步：整段翻转
        v7.set_paint_color(QColor("#00FF00"))
        v7.set_paint_tool("brush")
        v7.brush_size = 6
        v7.begin_paint_stroke(to_canvas(v7, 40, 12))
        v7.paint_stroke_to(to_canvas(v7, 52, 12))
        v7.end_paint_stroke()                          # 第 2 步：一笔画笔
        wait(120)
        state_before = [(red_bbox(pm), pixel(pm, 46, 12).name()) for pm in doc7.frames]
        check("存盘前是两步操作", [op.label for op in doc7.ops] == ["左右翻转", "画笔"],
              str([op.label for op in doc7.ops]))

        proj = root / "hero_anim.sstool"
        check("动图文档能存成 .sstool", win7._save_project(str(proj)) and proj.is_file(),
              str(proj))
        manifest, sections = read_project(str(proj))
        payload = manifest.get("anim_doc") or {}
        check("清单里带着动图文档那一段", bool(payload))
        check("清单里存的是 originals + 操作链（不是每帧结果图）",
              len(payload.get("frames") or []) == doc7.count
              and [op.get("kind") for op in payload.get("ops") or []] == ["transform", "stroke"],
              str(payload.get("ops")))
        check("当前帧也存成了缩略图（.sstool 本身是能看的图）", "thumbnail.png" in sections)

        win8 = MainWindow()
        win8.resize(1100, 700)
        win8.show()
        wait(80)
        check("打开这份工程不报错", win8._open_project(str(proj)))
        doc8 = win8._anim_doc
        check("打开的是动图文档（不是图集工程）", doc8 is not None, str(doc8))
        check("帧数一样", doc8 is not None and doc8.count == doc7.count)
        check("逐帧时长一样", doc8 is not None and doc8.durations == doc7.durations,
              str(doc8 and doc8.durations))
        check("看到第几帧也一样", doc8 is not None and doc8.index == doc7.index)
        check("操作链也读回来了（两步：翻转 + 画笔）",
              doc8 is not None and [op.label for op in doc8.ops] == ["左右翻转", "画笔"],
              str([op.label for op in doc8.ops]))
        check("打开后的画面和存盘时逐帧一致（尺寸/红块位置/笔迹）",
              [(red_bbox(pm), pixel(pm, 46, 12).name()) for pm in doc8.frames] == state_before,
              str([(red_bbox(pm), pixel(pm, 46, 12).name()) for pm in doc8.frames]))
        check("打开后那三个用不上的工具还是收着的",
              not win8.image_viewer._v_toolbar.btn_source.isEnabled())
        check("导出弹窗的「导出 SSTOOL 工程」又可用（provider 不为 None）",
              win8.preview._project_manifest is not None)
        win8._do_undo()                                # 退回画笔那一步
        wait(60)
        check("打开后撤销栈是活的：退一步后操作链只剩 1 条（翻转）",
              [op.label for op in doc8.ops] == ["左右翻转"],
              str([op.label for op in doc8.ops]))
        check("撤销一步后：笔迹没了", all(pixel(pm, 46, 12).name() != QColor("#00FF00").name()
                                        for pm in doc8.frames),
              str([pixel(pm, 46, 12).name() for pm in doc8.frames]))
        check("撤销一步后：翻转还在（红块仍在右边那带）",
              [red_bbox(pm) for pm in doc8.frames]
              == [(54 - 6 * i, 30, 73 - 6 * i, 49) for i in range(doc8.count)],
              str([red_bbox(pm) for pm in doc8.frames]))
        win8._do_undo()                                # 再退回翻转
        wait(60)
        check("再撤销一步：操作链空了、画面回到没翻转的样子",
              doc8.ops == []
              and [red_bbox(pm) for pm in doc8.frames]
              == [(6 + 6 * i, 30, 25 + 6 * i, 49) for i in range(doc8.count)],
              f"ops={[op.label for op in doc8.ops]} "
              f"bbox={[red_bbox(pm) for pm in doc8.frames]}")

        print("=== 11) 横条：两行 + 两种模式 + 两条转换路（真点按钮） ===")
        win9 = MainWindow()
        win9.resize(1100, 700)
        win9.show()
        wait(80)
        win9._open_animation_document(anim, name_hint=gif.stem, path=str(gif))
        bar9 = win9.anim_doc_bar
        v9 = win9.image_viewer
        doc9 = win9._anim_doc
        wait(60)
        # ① 两行：结构上真的两行 + 最小宽度不再撑爆左侧栏（改之前一行要 1153px，栏只有 588）
        check("横条现在是两行", bar9.layout().count() == 2, str(bar9.layout().count()))
        row2 = bar9.layout().itemAt(1).layout()
        check("滑块在第二行",
              any(row2.itemAt(i).widget() is bar9.slider for i in range(row2.count())))
        check("四个变换按钮也在第二行",
              all(any(row2.itemAt(i).widget() is b for i in range(row2.count()))
                  for b in bar9._buttons.values()))
        check("横条的最小宽度不再是 1153（一行放不下的老问题）",
              bar9.minimumSizeHint().width() < 700,
              f"{bar9.minimumSizeHint().width()} vs 栏宽 {bar9.parentWidget().width()}")
        check("横条最小宽度装得进左侧栏",
              bar9.minimumSizeHint().width() <= bar9.parentWidget().width(),
              f"{bar9.minimumSizeHint().width()} vs {bar9.parentWidget().width()}")
        # ② 文档模式：按钮写着「转为序列帧」，四个变换按钮是露着的
        check("文档模式：按钮文字是「转为序列帧」",
              bar9.btn_convert.text() == "转为序列帧", bar9.btn_convert.text())
        check("文档模式：四个变换按钮露着",
              all(b.isVisible() and b.isEnabled() for b in bar9._buttons.values()))

        # 先在文档里做两处改动（翻转 + 一笔画笔）：转过去以后必须还在
        win9._on_anim_doc_transform("flip_h")
        v9.set_paint_color(QColor("#00FF00"))
        v9.set_paint_tool("brush")
        v9.brush_size = 6
        v9.begin_paint_stroke(to_canvas(v9, 40, 12))
        v9.paint_stroke_to(to_canvas(v9, 52, 12))
        v9.end_paint_stroke()
        wait(120)
        doc_state = [(red_bbox(pm), pixel(pm, 46, 12).name()) for pm in doc9.frames]
        check("转换前是两步操作", [op.label for op in doc9.ops] == ["左右翻转", "画笔"],
              str([op.label for op in doc9.ops]))

        # ③ ②→①：**真点**那个按钮
        bar9.btn_convert.click()
        wait(200)
        check("转成序列帧后不再是动图文档", win9._anim_doc is None)
        check("记住的来源信息有了（序列帧模式靠它）", win9._anim_source is not None)
        check("铺出 6 张原图、建了 1 个动作",
              len(v9._source_images) == 6 and len(win9.actions) == 1,
              f"sources={len(v9._source_images)} actions={len(win9.actions)}")
        action9 = win9.actions[0] if win9.actions else None
        check("动作正好 6 帧、逐帧时长跟着动图",
              action9 is not None and len(action9.frames) == 6
              and [action9.frame_duration_ms(i) for i in range(6)] == list(DURS),
              str(action9 and [action9.frame_duration_ms(i) for i in range(6)]))
        # 改动真的烙进像素了：每张原图那块里，红块位置 = 翻转后的位置、绿笔迹也在
        composite9 = v9._pixmap
        rects9 = win9._live_animation_frames()
        crops = [composite9.copy(*r) for r in (rects9 or [])]
        check("转换用的是改过的像素（每帧红块都在翻转后的位置）",
              [red_bbox(c) for c in crops]
              == [(54 - 6 * i, 30, 73 - 6 * i, 49) for i in range(6)],
              str([red_bbox(c) for c in crops]))
        check("转换后每帧都带着那笔绿笔迹（没被还原成原始帧）",
              all(pixel(c, 46, 12).name() == QColor("#00FF00").name() for c in crops),
              str([pixel(c, 46, 12).name() for c in crops]))
        check("这里和存进工程的快照口径一致：转换前那一帧帧就是这些像素",
              [red_bbox(c) for c in crops] == [b for b, _ in doc_state])
        # 横条切到序列帧模式
        check("横条标题切成「序列帧 · …」", bar9.lbl_title.text().startswith("序列帧"),
              bar9.lbl_title.text())
        check("按钮文字变成「转为动图文档」",
              bar9.btn_convert.text() == "转为动图文档", bar9.btn_convert.text())
        check("序列帧模式下四个变换按钮收起来（跟动画设置横条里的变换区不重复）",
              not any(b.isVisible() for b in bar9._buttons.values()))
        check("序列帧模式下横条是露着的、按钮可用",
              bar9.isVisible() and bar9.btn_convert.isEnabled())
        check("序列帧模式的「动画设置」横条照样在（fps/锚点还得能用）",
              win9.anim_bar.isVisible())
        # ④ 序列帧模式的滑块：跳到第 i 帧，只切当前帧、不动数据
        before_sel = list(action9.selected_indices)
        before_pixels = doc_state
        bar9.slider.setValue(3)
        wait(80)
        check("拖滑块真的跳到那个动作的第 4 帧",
              win9.preview._frame_index == 3 and v9.current_action is action9,
              f"frame={win9.preview._frame_index} action={getattr(v9.current_action, 'name', None)}")
        check("滑块只切帧：选中元素没动",
              list(action9.selected_indices) == before_sel
              and list(v9.current_action.selected_indices) == before_sel)
        check("滑块只切帧：图上一个像素没动",
              [red_bbox(composite9.copy(*r)) for r in rects9] == [b for b, _ in before_pixels])
        # ⑤ 撤销：退回动图文档（用户口径：转换记一条全局撤销）
        win9._do_undo()
        wait(200)
        check("Ctrl+Z 退回了动图文档", win9._anim_doc is not None)
        check("退回来以后操作链原样还在（两步）",
              win9._anim_doc is not None
              and [op.label for op in win9._anim_doc.ops] == ["左右翻转", "画笔"],
              str(win9._anim_doc and [op.label for op in win9._anim_doc.ops]))
        check("退回来以后画面就是刚才那两处改动",
              win9._anim_doc is not None
              and [(red_bbox(pm), pixel(pm, 46, 12).name()) for pm in win9._anim_doc.frames]
              == doc_state,
              str(win9._anim_doc and [(red_bbox(pm), pixel(pm, 46, 12).name())
                                      for pm in win9._anim_doc.frames]))
        check("退回来以后来源记忆也清掉了、横条回到文档模式",
              win9._anim_source is None and bar9.btn_convert.text() == "转为序列帧")
        win9._do_redo()
        wait(200)
        check("重做又回到序列帧工程",
              win9._anim_doc is None and len(win9.actions) == 1
              and len(v9._source_images) == 6 and bar9.btn_convert.text() == "转为动图文档",
              f"doc={win9._anim_doc} actions={len(win9.actions)}")

        # ⑥ ①→②：从序列帧收回动图文档（用**现在的像素**）
        bar9.btn_convert.click()
        wait(250)
        doc10 = win9._anim_doc
        check("收回成动图文档了", doc10 is not None)
        check("帧数对（6 帧）", doc10 is not None and doc10.count == 6,
              str(doc10 and doc10.count))
        check("收回来的帧就是刚才那些像素（红块在翻转后的位置、绿笔迹也在）",
              doc10 is not None
              and [(red_bbox(pm), pixel(pm, 46, 12).name()) for pm in doc10.frames] == doc_state,
              str(doc10 and [(red_bbox(pm), pixel(pm, 46, 12).name()) for pm in doc10.frames]))
        check("新文档的操作链是空的（像素已经烙进帧里了）",
              doc10 is not None and doc10.ops == [], str(doc10 and doc10.ops))
        check("收回后来源记忆清掉、横条回到文档模式",
              win9._anim_source is None and bar9.btn_convert.text() == "转为序列帧")
        check("收回后动作列表清空（动图文档不玩动作那套）",
              win9.actions == [] and v9.current_action is None)
        win9._do_undo()
        wait(250)
        check("Ctrl+Z 又退回序列帧工程（来回转换都能退）",
              win9._anim_doc is None and len(win9.actions) == 1
              and win9._anim_source is not None and len(v9._source_images) == 6,
              f"doc={win9._anim_doc} actions={len(win9.actions)}")

        # ⑦ 反证：普通图片工程不显示这条 gif 横条
        win10 = MainWindow()
        win10.resize(1000, 700)
        win10.show()
        wait(80)
        plain = QPixmap(64, 64)
        plain.fill(QColor("#00000000"))
        painter = QPainter(plain)
        painter.fillRect(8, 8, 40, 40, QColor("#E91E63"))
        painter.end()
        win10._on_image_loaded(plain)
        wait(200)
        check("普通图片工程：这条 gif 横条不显示",
              not win10.anim_doc_bar.isVisible() and win10._anim_source is None)
        check("普通图片工程：「动画设置」横条照旧显示", win10.anim_bar.isVisible())

        # ⑧ 存成 .sstool 再打开：这条 gif 横条与「转为动图文档」都得还在
        check("（准备）现在是序列帧工程", win9._anim_doc is None
              and win9._anim_source is not None and len(win9.actions) == 1)
        proj8 = root / "hero_frames.sstool"
        check("序列帧工程能存成 .sstool", win9._save_project(str(proj8)) and proj8.is_file())
        manifest8, _sections8 = read_project(str(proj8))
        payload8 = manifest8.get("anim_source") or {}
        check("清单里带着「从动图来」那一段",
              payload8.get("indices") and len(payload8.get("indices") or []) == 6
              and payload8.get("action_index") == 0,
              str(payload8))
        win11 = MainWindow()
        win11.resize(1100, 700)
        win11.show()
        wait(80)
        check("打开这份工程不报错", win11._open_project(str(proj8)))
        bar11 = win11.anim_doc_bar
        wait(80)
        check("打开后还是普通工程（不是动图文档）",
              win11._anim_doc is None and len(win11.actions) == 1)
        check("打开后这条 gif 横条还在（存了工程也没丢）",
              win11._anim_source is not None and bar11.isVisible(),
              f"source={win11._anim_source} visible={bar11.isVisible()}")
        check("打开后横条标题是「序列帧 · …」", bar11.lbl_title.text().startswith("序列帧"),
              bar11.lbl_title.text())
        check("打开后转换按钮文字是「转为动图文档」且可用",
              bar11.btn_convert.text() == "转为动图文档" and bar11.btn_convert.isEnabled(),
              f"{bar11.btn_convert.text()} enabled={bar11.btn_convert.isEnabled()}")
        check("打开后动作重新认回来了（滑块能跳到第 i 帧）",
              win11._anim_source is not None
              and win11._anim_source.action is win11.actions[0],
              str(win11._anim_source and win11._anim_source.action))
        bar11.slider.setValue(2)
        wait(80)
        check("打开后滑块真的能跳帧", win11.preview._frame_index == 2,
              str(win11.preview._frame_index))
        bar11.btn_convert.click()                       # 打开后照样能转为动图文档
        wait(250)
        doc11 = win11._anim_doc
        check("打开后「转为动图文档」照样能用（6 帧）",
              doc11 is not None and doc11.count == 6, str(doc11 and doc11.count))
        check("收回来的帧尺寸跟原图一致",
              doc11 is not None and all(
                  pm.width() == W and pm.height() == H for pm in doc11.frames),
              str(doc11 and [(pm.width(), pm.height()) for pm in doc11.frames[:2]]))

        print("=== 12) 收尾 ===")
        import gc
        for w in (win, win2, win3, win4, win5, win6, win7, win8, win9, win10, win11):
            if w is None:
                continue
            # 先松掉动图文档（本回归里唯一大量持有 QPixmap 的地方），再 close + deleteLater。
            # 只 close() 不销毁的话这些窗口会活到解释器退出：那批 QPixmap 就拖到
            # QApplication 已经析构之后才释放 —— offscreen 下正好是 0xC0000409 abort
            # （本项目记过的老坑）。deleteLater + 泵事件，让它们在"应用还活着"时析构干净。
            w._anim_doc = None
            w.close()
            w.deleteLater()
        win = win2 = win3 = win4 = win5 = win6 = win7 = win8 = None
        _APP.processEvents()
        gc.collect()
        wait(200)
        restore()
        check("设置存档文件已被清掉（用户设置已还原）",
              not (ROOT / "tools" / ".settings_backup.json").exists(),
              "tools/.settings_backup.json 还在 = 用户设置没还原")
        check("没有残留主窗口", all(not w.isVisible() for w in _APP.topLevelWidgets()))
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