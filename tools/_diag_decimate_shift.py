"""诊断：抽帧后「后面的元素会位移」到底是哪一帧挪了、偏移有没有贴错帧。

做法（量化，不靠肉眼）：
1. 造一支 atlas，元素**尺寸各不相同**（这样落点差异一定量得出来），并且让其中两帧**完全相同**
   （才能形成"一串"、被强力去重抽掉）；
2. 给每帧预设一个**可区分的偏移**（模拟自动对齐），并记下抽帧前每帧的：
   内容签名（md5）+ 尺寸 + 落点 + 偏移；
3. 走**真代码路径**抽帧（`MainWindow._on_extract_decimate`）；
4. 抽帧后重新量一遍，按**内容签名**把"旧帧"和"新帧"对上，然后逐帧比对：
   - 这一帧的内容是不是还是原来那一帧的？
   - 它身上的偏移，是不是还是**它自己**那个值？（贴错帧就会立刻暴露）
   - 它的落点有没有变？

三种形态都测：普通（一帧一元素）/ 合成帧（composite_groups）/ 导入布局（frame_layouts）。
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QElapsedTimer, Qt                          # noqa: E402
from PyQt6.QtGui import QColor, QPainter, QPixmap                    # noqa: E402
from PyQt6.QtWidgets import QApplication                            # noqa: E402

from settings_guard import protect_settings                         # noqa: E402

_APP = QApplication.instance() or QApplication(sys.argv)


def wait(ms):
    t = QElapsedTimer()
    t.start()
    while t.elapsed() < ms:
        QApplication.processEvents()


def sig(pm: QPixmap) -> str:
    """帧内容签名：尺寸 + 像素哈希（内容相同就该同签名）。"""
    img = pm.toImage()
    buf = img.bits()
    buf.setsize(img.sizeInBytes())
    return f"{img.width()}x{img.height()}:{hashlib.md5(bytes(buf)).hexdigest()[:10]}"


def snapshot(action):
    """量一遍：每帧 (签名, 尺寸, 落点, 偏移)。"""
    from spritesheet.ui.transform import action_frame_size, compute_preview_layout
    positions = compute_preview_layout(action)[2]
    out = []
    for i, pm in enumerate(action.frames):
        out.append({
            "sig": sig(pm),
            "size": action_frame_size(action, i),
            "spot": (positions[i][0], positions[i][1]) if i < len(positions) else None,
            "off": tuple(action.frame_offsets.get(i, (0, 0))),
        })
    return out


def report(name, before, after):
    print(f"\n===== {name} =====")
    print(f"帧数 {len(before)} → {len(after)}")
    # 按内容签名把旧帧对新帧（同签名可能出现多次，按顺序取用）
    used = set()
    pairs = []
    for old_i, old in enumerate(before):
        for new_i, new in enumerate(after):
            if new_i in used or new["sig"] != old["sig"]:
                continue
            used.add(new_i)
            pairs.append((old_i, new_i))
            break
    print("内容配对（旧帧→新帧）：", pairs)
    unpaired = [i for i in range(len(before)) if i not in {o for o, _ in pairs}]
    print("被抽掉的旧帧：", unpaired)
    bad = []
    for old_i, new_i in pairs:
        old, new = before[old_i], after[new_i]
        moved = old["spot"] != new["spot"]
        off_bad = old["off"] != new["off"]
        print(f"  旧{old_i}→新{new_i}: 尺寸 {old['size']}→{new['size']}"
              f" 落点 {old['spot']}→{new['spot']}{'  ← 挪了' if moved else ''}"
              f" 偏移 {old['off']}→{new['off']}{'  ← 贴错帧/丢了' if off_bad else ''}")
        if moved or off_bad:
            bad.append((old_i, new_i, moved, off_bad))
    print("结论：", "❌ 有问题 " + repr(bad) if bad else "✅ 保留帧的内容、落点、偏移都逐帧一致")


def make_action(win, shape):
    """造一个动作并在三种形态下重建帧。shape: plain / composite / layouts。"""
    viewer = win.image_viewer
    total = len(viewer._frames)
    action = win._create_animation_action(f"诊断-{shape}", [], 0, total)
    win._install_animation_action(action)
    wait(300)
    if shape == "comp_run":
        # 关键形态：两个**相邻且完全相同**的合成帧 → 强力去重真的会抽掉一帧
        # 元素 0/1/2/3 全同，合成组 [[0,1],[2,3]] → 两帧一模一样
        action.selected_indices = [0, 1, 2, 3]
        action.composite_groups = [[0, 1], [2, 3]]
    elif shape == "lay_run":
        # 关键形态：两条布局完全相同（同一个元素、同样帧内位置）→ 真的会抽掉一帧
        action.selected_indices = [0, 1]
        action.frame_layouts = [[(0, 0, 0)], [(1, 0, 0)], [(4, 5, 3)], [(3, 8, 1)]]
        action.frame_layouts_base = 4
    elif shape == "composite":
        # 元素 0、1 合成一帧；2、3 各自成帧
        action.composite_groups = [[0, 1]]
    elif shape == "layouts":
        # 导入工程的形态：每帧显式列出部件与**帧内位置**（带非零 rx/ry 才测得出位移）
        action.frame_layouts = [[(0, 0, 0)], [(1, 12, 4)], [(2, 0, 0)], [(3, 6, 9)]]
        action.frame_layouts_base = 4
    elif shape == "composite2":
        # 更刁的形态：第一个元素不属于任何合成组，合成组在**后面**
        action.composite_groups = [[1, 2]]
    viewer.crop_frames_for_action(action)
    wait(200)
    # 预设可区分的偏移（模拟自动对齐结果）：每帧一个不同值
    action.frame_offsets = {i: (i * 3, i * 2) for i in range(len(action.frames))}
    return action


def run_shape(win, shape):
    action = make_action(win, shape)
    before = snapshot(action)
    print(f"\n---- {shape}：抽帧前每帧 ----")
    for i, s in enumerate(before):
        print(f"  帧{i}: {s['size']} 落点{s['spot']} 偏移{s['off']} 签名{s['sig']}")
    if len(before) < 2:
        print("  帧太少，跳过")
        return
    win._on_extract_decimate("dedupe", 0)      # 走真代码路径
    wait(300)
    report(shape, before, snapshot(action))


def main():
    restore = protect_settings()
    from spritesheet.ui.main_window import MainWindow
    win = MainWindow()
    win.resize(1280, 800)
    win.show()
    wait(200)
    # 4 个**完全相同**的 40×40 蓝块（保证能形成"一串"被真的抽掉）+ 1 个 60×30 红块（尺寸不同）
    atlas = QPixmap(340, 100)
    atlas.fill(Qt.GlobalColor.transparent)
    p = QPainter(atlas)
    for x in (10, 60, 110, 160):
        p.fillRect(x, 10, 40, 40, QColor("#1565C0"))
    p.fillRect(215, 20, 60, 30, QColor("#C62828"))
    p.end()
    win.image_viewer.set_pixmap(atlas)
    wait(1500)
    print("检测到的元素数：", len(win.image_viewer._frames))
    for shape in ("plain", "comp_run", "lay_run", "composite2", "layouts"):
        run_shape(win, shape)

    # ---- 切换档位：必须以**原动作的帧**为基准（不是当前帧），否则越切越少 ----
    print("\n===== 切换档位序列（判据：每档都从原动作 5 帧算，不是从当前帧叠着算）=====")
    make_action(win, "plain")
    action = win.image_viewer.current_action
    total0 = len(action.frames)
    seq = [("keep_1_4", 2), ("keep_1_2", 3), ("dedupe", 2), ("keep_3_5", 4)]
    print(f"原动作帧数：{total0}（4 个全同的蓝块 = 一串，后面 1 个红块）")
    ok = True
    for mode, expect in seq:
        win._on_extract_decimate(mode, 0)
        wait(200)
        n = len(win.image_viewer.current_action.frames)
        hit = n == expect
        ok = ok and hit
        print(f"  抽帧[{mode}] → {n} 帧（应从原动作算得 {expect}）{'✔' if hit else '✘ 越切越少=叠着算了'}")
        if not hit:
            break
    print("切换档位结论：", "✅ 每档都按原动作的帧算" if ok else "❌ 有问题")

    win.close()
    wait(200)
    restore()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())