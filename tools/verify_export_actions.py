"""导出对话框的动作列表必须跟着主窗口走 —— 走真实入口的端到端回归。

钉住的 bug（本次修复）：
  动图文档模式下，preview 只挂**一个「壳动作」**（main_window._refresh_anim_doc_views
  → self.preview.set_actions([action])）。而**导出对话框的动作列表正是 preview._actions**
  （preview.ActionPreviewWidget._on_export_clicked → ExportDialog(self._actions, …)）。
  退出动图文档时（_apply_anim_doc_mode 的 doc is None 分支）原来**直接 return**，没把
  动作列表还回 self.actions ⇒ 导出对话框左侧一直是那一个壳动作（或空的）—— 用户表现就是
  「点导出以后左边没有动作」→ 只导出一个 GIF，其它动作全都不出。

修复：main_window._apply_anim_doc_mode 的 doc is None 分支补
  `self.preview.set_actions(self.actions)`。
  这一处是**所有「退出动图文档 / 常规刷新」路径的共同出口**：
    * 转为序列帧        _convert_anim_doc_to_frames → _apply_anim_doc_mode(fit=True)
    * 文档里载入普通图  _on_image_loaded            → _apply_anim_doc_mode()
    * 导入动图建序列帧  _import_animation_frames    → _apply_anim_doc_mode(fit=False)

断言全部走真实入口：真开动图文档、真调横条的「转为序列帧」、真按 _on_export_clicked
的构造方式建 ExportDialog、真调 _export_to_directory 把文件写出来数个数。

用法: QT_QPA_PLATFORM=offscreen .venv\\Scripts\\python.exe -X utf8 -u tools\\verify_export_actions.py

收尾与 verify_extract.py 同规：断言跑完 → sys.stdout.flush() + os._exit(code)，
绕开本项目 offscreen 析构阶段的偶发 0xC0000005。
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

from PyQt6.QtCore import QCoreApplication, QElapsedTimer          # noqa: E402
from PyQt6.QtGui import QColor, QPainter, QPixmap                 # noqa: E402
from PyQt6.QtWidgets import QApplication                          # noqa: E402

from spritesheet.anim import Animation                            # noqa: E402
from spritesheet.ui import export_dialog as export_dialog_mod     # noqa: E402
from spritesheet.ui.export_dialog import ExportDialog             # noqa: E402
from spritesheet.ui.main_window import MainWindow                 # noqa: E402

_APP = QApplication.instance() or QApplication(sys.argv)

# 导出会 track 使用统计；这里只数文件，把联网那一步一并堵死（track 只累加计数也不该发）
export_dialog_mod.telemetry.track = lambda *a, **k: None

COUNT = 0
FAILS: list[str] = []


def check(label: str, ok: bool) -> None:
    global COUNT
    COUNT += 1
    if ok:
        print(f"  [OK]   {label}")
    else:
        FAILS.append(label)
        print(f"  [FAIL] {label}")


def wait(ms: int = 60) -> None:
    timer = QElapsedTimer()
    timer.start()
    while timer.elapsed() < ms:
        QCoreApplication.processEvents()


def make_anim(n: int = 3, w: int = 24, h: int = 24) -> Animation:
    """造一支真动图：每帧一个位置不同的红块，保证帧间像素确有差别。"""
    frames: list[QPixmap] = []
    durations: list[int] = []
    for i in range(n):
        pm = QPixmap(w, h)
        pm.fill(QColor(0, 0, 0, 0))
        painter = QPainter(pm)
        painter.fillRect(2 + i * 4, 4, 6, 6, QColor(220, 30, 60, 255))
        painter.end()
        frames.append(pm)
        durations.append(80 + i * 10)
    return Animation(frames=frames, durations=durations, width=w, height=h, fmt="GIF")


def main() -> int:
    win = MainWindow()
    wait(120)

    anim = make_anim(3)

    # ---- 1. 进动图文档：这里 preview 就该只有 1 个壳动作（不是 bug，是文档模式的定义）
    win._open_animation_document(anim, "gif动图", "")
    wait(80)
    check("动图文档里 preview 只有 1 个「壳动作」（文档模式的正确状态）",
          len(win.preview._actions) == 1)
    check("动图文档里主窗口动作列表是空的（文档不玩动作）",
          len(win.actions) == 0)
    check("动图文档里 viewer 的动作也是空的",
          len(win.image_viewer.actions) == 0)

    # ---- 2. 真实入口：横条上那颗「转为序列帧」
    win._on_anim_doc_convert()
    wait(120)

    # ★ 核心断言：修复点。修复前这里是 [壳动作] vs [动图动作]，必然红
    check("转为序列帧后：preview 的动作列表已换回主窗口这一份",
          list(win.preview._actions) == list(win.actions))
    check("转为序列帧后：preview 与主窗口共用同一份列表（引用不失联）",
          win.preview._actions is win.actions)
    check("转为序列帧后：建出了 1 个动图动作", len(win.actions) == 1)
    check("动图动作带上了逐帧时长（80/90/100ms）",
          [win.actions[0].frame_duration_ms(i) for i in range(3)] == [80, 90, 100])

    # ---- 3. 再走真实入口加第 2 个动作（用户场景：其它动作都没出）
    win._create_action()
    wait(80)
    check("加了第 2 个动作后一共有 2 个动作", len(win.actions) == 2)

    extra = win.actions[-1]
    extra.selected_indices = [0]
    win.image_viewer.crop_frames_for_action(extra)
    wait(80)
    check("第 2 个动作裁出了帧（否则 GIF 会被静默跳过，断言就变成空的）",
          len(extra.frames) >= 1)
    check("加了第 2 个动作后 preview 仍与主窗口同步",
          list(win.preview._actions) == list(win.actions))

    # ---- 4. 按 preview._on_export_clicked 的真实方式建导出对话框
    dlg = ExportDialog(
        win.preview._actions,
        win.preview._source_pixmap,
        project_provider=None,
        project_name="测试工程",
        parent=win.preview,
    )
    wait(80)

    check("导出对话框拿到了全部动作（不是只有壳动作、也不是空的）",
          len(dlg.actions) == len(win.actions))
    check("导出对话框左侧的动作卡牌数 == 动作数",
          len(dlg._switches) == len(win.actions))
    check("动作开关默认全开：selected_actions == 全部动作",
          len(dlg.selected_actions()) == len(win.actions))

    # ---- 5. 端到端：真导出，数 GIF 文件个数
    work = Path(tempfile.gettempdir()) / "sstool_export_actions_check"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)          # 默认 mode 可写（不用 mkdtemp）

    dlg.cb_gif.setChecked(True)
    generated = []
    _err = None
    try:
        generated = dlg._export_to_directory(str(work))
    except Exception as exc:                                        # noqa: BLE001
        _err = exc
    check("导出调用没有抛异常（列表失联时会抛「所选动作没有可导出的帧」）", _err is None)
    wait(120)

    root = dlg.target_root(work)
    gifs = sorted(root.glob("*.gif"))
    pngs = sorted(root.glob("*.png"))
    check(f"按动作写出了 {len(win.actions)} 个 GIF（实际 {len(gifs)} 个）",
          len(gifs) == len(win.actions))
    check("图集 PNG 只有 1 张（合并模式：所有动作打成一张）", len(pngs) == 1)
    check("返回的产物清单里 GIF 数也对得上",
          len([p for p in generated if p.suffix.lower() == ".gif"]) == len(win.actions))

    try:
        from PIL import Image

        ok_all = True
        counts = []
        for gif in gifs:
            with Image.open(gif) as im:
                counts.append(int(im.n_frames))
                if im.n_frames < 1:
                    ok_all = False
        check("至少写出了 1 个 GIF（0 个时下面那条会空过）", len(gifs) >= 1)
        check("每个 GIF 都能被 Pillow 打开且至少 1 帧", ok_all)
        check("两个 GIF 的帧数分别是 3 / 1（没有互相串味）",
              sorted(counts) == [1, 3])
    except Exception as exc:                                        # noqa: BLE001
        check(f"Pillow 读 GIF 失败：{exc}", False)

    # ---- 6. 反证：普通工程（没沾过动图文档）里列表也必须同步
    win2 = MainWindow()
    wait(80)
    check("新窗口：preview 与主窗口动作列表一开始就是同一份（都是空）",
          list(win2.preview._actions) == list(win2.actions))

    dlg.close()
    wait(80)
    win.close()
    win2.close()
    wait(200)
    shutil.rmtree(work, ignore_errors=True)

    print()
    if FAILS:
        print(f"EXPORT ACTIONS FAILED {COUNT - len(FAILS)}/{COUNT}")
        for item in FAILS:
            print(f"  - {item}")
    else:
        print(f"EXPORT ACTIONS OK  {COUNT}/{COUNT}")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    _code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(_code)