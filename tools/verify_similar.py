#!/usr/bin/env python
# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""相似帧专项回归：算法（分组 / 容差 / 循环长度）+ 界面（开关两态、两处标色、纯显示不动数据）。

口径来自用户：
  * 「主要是查找所有的元素中相似的 在右下角用相似的颜色做标记（颜色一定要区分好）」
  * 「原图页面要显示 帧缩略图也要显示」（缩略图改**右上角**，右下角留给帧序号）
  * 「阈值要放设置里 默认是要一模一样」「全图识别（有别的新图也要找）」「不带信息」
  * 「这个功能主要是找循环帧用的」

容差模式的相似度指标本体是 64x64 缩略指纹（``spritesheet/ui/similar.py`` 的 ``_features``
默认 size=64）：网格格子数/位序、t/1000 的容差刻度、只在有内容处求平均的色差门。
这部分原来只有「容差 5 时差一个像素算相似」这么一条间接断言，**没有被变异校验覆盖过**
（没人证明过指标算错时断言真会失败）。所以：

  * **第 2 节**直接把指标本体断言出来（4096 格 / 位序 / 刻度 / 色差门 / 容差模式真走指纹）；
  * **第 3 节**做变异校验：在**内存里** monkeypatch 把指标故意算错（64→32、判定反转），
    确认第 2 节那几条断言真的会红，跑完立刻还原 —— 只碰内存里的模块属性，源码一个字节不动。

取证用（想看「真跑一次是红的、退出码 1」）：在环境变量里给 SIMILAR_MUTATE=thumb32 或 flip，
整脚本就会在指标被改坏的前提下跑完，第 2 节那几条应当 [FAIL] 且退出码非 0；不带这个变量就是全绿。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PyQt6.QtCore import QElapsedTimer, Qt                          # noqa: E402
from PyQt6.QtGui import QColor, QPainter, QPixmap                    # noqa: E402
from PyQt6.QtWidgets import QApplication                            # noqa: E402

from settings_guard import protect_settings                         # noqa: E402

_APP = QApplication.instance() or QApplication(sys.argv)
PASS = FAIL = 0


def check(name, ok, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [OK] {name} {extra}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


def wait(ms):
    t = QElapsedTimer()
    t.start()
    while t.elapsed() < ms:
        QApplication.processEvents()


def blob(w, h, color, dot=False):
    """一块内容。dot=True 时多一个像素——用来测"差一点点"在容差 0 下不算相似。"""
    pm = QPixmap(w, h)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.fillRect(0, 0, w, h, QColor(color))
    if dot:
        p.fillRect(1, 1, 2, 2, QColor("#FFFFFF"))
    p.end()
    return pm


def shape(w, h, x, y, bw, bh, color="#1565C0", hole=None):
    """w×h 的画布上只填一块 (x, y, bw, bh)，其余透明；hole=(hx, hy, hw, hh) 再挖掉一个洞。

    挖洞必须用 Clear 合成模式 —— 拿 alpha=0 的 fillRect 盖是盖不掉底色的（源覆盖会原样留着）。
    """
    pm = QPixmap(w, h)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.fillRect(x, y, bw, bh, QColor(color))
    if hole:
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        p.fillRect(hole[0], hole[1], hole[2], hole[3], QColor(0, 0, 0, 0))
    p.end()
    return pm


def metric_results(features_fn, similar_fn):
    """64×64 缩略指纹（容差模式的相似度指标本体）的 6 条断言，对给定的指标函数跑一遍。

    返回 [(名字, 是否通过, 附注)]。**正常断言和变异校验共用这一条路** —— 变异校验里红的，
    保证就是正常跑里绿的那几条，不会出现「校验的是另一套断言」。

    数字都是先实测再落断言（40x40 全不透明 → 64x64 上 4096 格；4x4 缺口 → 49/4096 ≈ 1.2%；
    25% 覆盖 + 色差 21 → 色差门在容差 42 附近翻转），留了 2 倍以上的余量，不是卡在边界上。
    """
    from spritesheet.ui.similar import group_elements

    solid_a = shape(40, 40, 0, 0, 40, 40)
    solid_b = shape(40, 40, 0, 0, 40, 40)         # 另画一张，内容一模一样
    right_half = shape(40, 40, 20, 0, 20, 40)     # 右半不透明 → 戳「每行 64 格」的位序
    notch = shape(40, 40, 0, 0, 40, 40, hole=(4, 4, 4, 4))
    small_a = shape(40, 40, 0, 0, 20, 20)         # 25% 覆盖
    small_b = shape(40, 40, 0, 0, 20, 20, color="#2A75D0")

    f_a = features_fn(solid_a)
    f_right = features_fn(right_half)
    f_notch = features_fn(notch)
    f_small_a = features_fn(small_a)
    f_small_b = features_fn(small_b)

    out = []
    out.append((
        "指纹网格就是 64×64 = 4096 格（不是 32×32）",
        f_a[1] == 4096 and f_a[0].bit_count() == 4096,
        f"count={f_a[1]} 位数={f_a[0].bit_count()}"))

    bit31 = (f_right[0] >> 31) & 1
    bit32 = (f_right[0] >> 32) & 1
    bit_last = (f_right[0] >> 4095) & 1
    out.append((
        "指纹按行存放、每行 64 格（右半不透明 → 2048 格、第 32 位亮第 31 位不亮）",
        f_right[1] == 2048 and bit32 == 1 and bit31 == 0 and bit_last == 1,
        f"count={f_right[1]} bit31={bit31} bit32={bit32} 末位={bit_last}"))

    v_notch_1 = similar_fn(f_a, f_notch, 1)
    v_notch_20 = similar_fn(f_a, f_notch, 20)
    out.append((
        "容差刻度 = t/1000 的格子：4×4 小缺口（约 1.2%）容差 1 判不同、容差 20 判同",
        v_notch_1 is False and v_notch_20 is True,
        f"容差 1 → {v_notch_1}，容差 20 → {v_notch_20}"))

    v_same = similar_fn(f_a, features_fn(solid_b), 1)
    out.append((
        "一模一样的两个元素在容差 1 下仍算同（相等不能被判成不同）",
        v_same is True, f"容差 1 → {v_same}"))

    color_diff = max(abs(u - v) for u, v in zip(f_small_a[2], f_small_b[2]))
    v_col_20 = similar_fn(f_small_a, f_small_b, 20)
    v_col_60 = similar_fn(f_small_a, f_small_b, 60)
    out.append((
        "色差只在有内容的地方平均（25% 覆盖、色差 21：容差 20 判不同、容差 60 判同）",
        v_col_20 is False and v_col_60 is True,
        f"mean 差={color_diff:.1f}，容差 20 → {v_col_20}，容差 60 → {v_col_60}"))

    groups_1 = group_elements([solid_a, notch], 1)
    groups_20 = group_elements([solid_a, notch], 20)
    out.append((
        "容差模式真按指纹分组（小缺口容差 1 不成组、容差 20 归成一组）",
        groups_1 == {} and len(groups_20) == 2 and len(set(groups_20.values())) == 1,
        f"容差 1 → {groups_1}，容差 20 → {groups_20}"))
    return out


# 变异清单：(说明, 代号, 必须变红的那几条断言名)
MUTANTS = (
    ("缩略尺寸 64 → 32（用户实测「容差 1 就全都像」的那个老 bug）", "thumb32",
     ("指纹网格就是 64×64 = 4096 格（不是 32×32）",
      "指纹按行存放、每行 64 格（右半不透明 → 2048 格、第 32 位亮第 31 位不亮）")),
    ("相似判定整体反转（阈值语义反了：像的判成不像）", "flip",
     ("容差刻度 = t/1000 的格子：4×4 小缺口（约 1.2%）容差 1 判不同、容差 20 判同",
      "一模一样的两个元素在容差 1 下仍算同（相等不能被判成不同）")),
)


def make_mutant(similar_mod, how):
    """造出一对「故意算错」的指标函数（指纹 / 判定），代号见 MUTANTS。"""
    real_f, real_s = similar_mod._features, similar_mod._similar_fast
    if how == "thumb32":
        return (lambda pm: real_f(pm, 32)), real_s
    if how == "flip":
        return real_f, (lambda fa, fb, tol: not real_s(fa, fb, tol))
    raise ValueError(f"不认识的变异代号：{how}")


def main():
    restore = protect_settings()
    from spritesheet.ui import similar as similar_mod
    from spritesheet.ui.similar import (group_color, group_elements, loop_hint,
                                        PALETTE)
    from spritesheet.settings import similar_tolerance

    # 取证开关：SIMILAR_MUTATE=thumb32|flip → 在**内存里**把指标改坏再整跑一遍，
    # 第 2 节那几条应当真红、退出码非 0（源码一个字节不动，进程退出即恢复）。
    mutant = os.environ.get("SIMILAR_MUTATE", "").strip()
    try:
        bad_f, bad_s = make_mutant(similar_mod, mutant) if mutant else (None, None)
    except ValueError as exc:
        print(f"[注意] {exc}：忽略这个取值")
        mutant, bad_f, bad_s = "", None, None
    if mutant:
        similar_mod._features, similar_mod._similar_fast = bad_f, bad_s
        print(f"[变异模式] SIMILAR_MUTATE={mutant}：64×64 指标已在内存里被改坏"
              f"（源码未动）→ 第 2 节那几条应当 [FAIL]")

    print("=== 1. 分组算法（纯函数）===")
    a = blob(40, 40, "#1565C0")
    b = blob(40, 40, "#1565C0")
    c = blob(40, 40, "#C62828")
    d = blob(40, 40, "#C62828")
    e = blob(40, 40, "#2E7D32")
    groups = group_elements([a, b, c, d, e], 0)
    check("一模一样的两个两对 → 分成两组", len(set(groups.values())) == 2, f"{groups}")
    check("落单的那个不进结果（没得标、不占颜色）", 4 not in groups, f"{groups}")
    check("同组同色、不同组不同色",
          group_color(groups[0]).name() != group_color(groups[2]).name(),
          f"{group_color(groups[0]).name()} / {group_color(groups[2]).name()}")
    check("色板够用且颜色互不相同", len(set(PALETTE)) == len(PALETTE) and len(PALETTE) >= 12,
          f"{len(PALETTE)} 色")

    near = blob(40, 40, "#1565C0", dot=True)
    groups0 = group_elements([a, near, c, c], 0)
    check("容差 0（默认）时「差一个像素」不算相似",
          near not in groups0 or groups0.get(0) != groups0.get(1), f"{groups0}")
    groups_t = group_elements([a, near], 5)
    check("容差 5 时那一点差别就算相似了", groups_t.get(0) == groups_t.get(1) == 0,
          f"{groups_t}")

    print("=== 2. 64×64 缩略指纹（指标本体：容差模式的相似判定就靠它）===")
    for name, ok, extra in metric_results(similar_mod._features, similar_mod._similar_fast):
        check(name, ok, extra)

    print("=== 3. 变异校验：把指标故意算错，证明第 2 节的断言真的会红 ===")
    real_f, real_s = similar_mod._features, similar_mod._similar_fast
    if mutant:
        print(f"  （本次已按 SIMILAR_MUTATE={mutant} 整跑变异模式，本段跳过 —— "
              f"第 2 节那些 [FAIL] 就是证据）")
    else:
        for title, how, expect_red in MUTANTS:
            m_bad_f, m_bad_s = make_mutant(similar_mod, how)
            similar_mod._features, similar_mod._similar_fast = m_bad_f, m_bad_s
            try:
                results = metric_results(m_bad_f, m_bad_s)   # 指标被改坏 → 该有断言红
            finally:
                similar_mod._features, similar_mod._similar_fast = real_f, real_s
            red = [name for name, ok, _ in results if not ok]
            hit = [e for e in expect_red if e in red]
            check(f"变异校验：{title} → 断言变红", len(hit) == len(expect_red),
                  f"红了 {len(red)}/{len(results)} 条，预期那几条命中 {len(hit)}/"
                  f"{len(expect_red)}")
        check("变异校验：跑完指纹/判定函数已逐对象还原（源码一个字节没动）",
              similar_mod._features is real_f and similar_mod._similar_fast is real_s)

    print("=== 4. 循环长度提示 ===")
    marks = {0: 0, 3: 0, 6: 0, 1: 1, 2: 1}       # 第 1/4/7 帧一组 → 循环长度 3
    hint = loop_hint(marks)
    check("报出最可能的循环长度", "循环长度 3 帧" in hint, hint)
    check("相邻两帧一样不算循环（那是重复帧）",
          loop_hint({0: 0, 1: 0, 3: 2, 4: 2}) == "", loop_hint({0: 0, 1: 0, 3: 2, 4: 2}))
    check("没有任何同组 → 不显示这一句", loop_hint({0: 0, 1: 1}) == "", loop_hint({0: 0, 1: 1}))

    print("=== 5. 界面：开关两态 + 两处标色（真点那颗按钮）===")
    from spritesheet.ui.main_window import MainWindow

    win = MainWindow()
    win.resize(1280, 800)
    win.show()
    wait(200)
    viewer = win.image_viewer
    panel = win.preview.adjust_panel
    # 图集：两格一样 + 一格不同；内容 40x40（超过设置里任何阈值的噪点门限）
    atlas = QPixmap(200, 60)
    atlas.fill(Qt.GlobalColor.transparent)
    p = QPainter(atlas)
    for x, col in ((10, "#1565C0"), (70, "#1565C0"), (130, "#C62828")):
        p.fillRect(x, 10, 40, 40, QColor(col))
    p.end()
    viewer.set_pixmap(atlas)
    wait(1500)
    n_el = len(viewer._frames)
    check("图集被切成 3 个元素", n_el == 3, f"{n_el} 个")
    action = win._create_animation_action("相似帧测试", [], 0, n_el)
    win._install_animation_action(action)
    wait(600)

    check("没按下时按钮是可用的（有动作、帧数 ≥2）", panel.btn_similar_frames.isEnabled())
    check("没按下时两处都没有标记",
          viewer.similar_marks == {} and panel.similar_color_provider is None)
    before = (dict(action.frame_offsets), tuple(action.anchor), action.padding)

    panel.btn_similar_frames.click()              # 按下
    wait(600)
    check("按下以后按钮进入「按下」态", panel.btn_similar_frames.isChecked())
    check("原图页面标了 2 个元素（那对一样的）", len(viewer.similar_marks) == 2,
          f"{viewer.similar_marks}")
    check("两个元素的颜色一样（同组同色）",
          len({c.name() for c in viewer.similar_marks.values()}) == 1,
          f"{[c.name() for c in viewer.similar_marks.values()]}")
    colors = [win._similar_color_for_frame(action, i) for i in range(3)]
    check("帧缩略图前两帧有色块、第三帧没有（不同组的不标）",
          colors[0] is not None and colors[1] is not None and colors[2] is None,
          f"{[c.name() if c else None for c in colors]}")
    check("缩略图问色回调挂在预览控件上（色块画在图标右上角，右下角留给帧序号）",
          callable(win.preview.similar_color_provider)
          and win.preview.similar_color_provider is not None)
    check("状态栏说清了找到几组", "找到 1 组相似帧" in win.lbl_status.text(),
          win.lbl_status.text()[:70])
    check("纯显示：工程数据一个字节没动",
          (dict(action.frame_offsets), tuple(action.anchor), action.padding) == before)

    panel.btn_similar_frames.click()              # 松开
    wait(500)
    check("松开以后按钮回到未按下态", not panel.btn_similar_frames.isChecked())
    check("两处标记一起清掉",
          viewer.similar_marks == {} and panel.similar_color_provider is None)

    print("=== 6. 真素材实测（你那张 8178x8178 图集，只用来看数，不做断言的死条件）===")
    sheet = ROOT / "9月25日(1)_keyed_spritesheet_0.png"
    if not sheet.exists():
        print("  （图集不在，跳过）")
    else:
        pm = QPixmap(str(sheet))
        viewer.set_pixmap(pm)
        wait(8000)
        els = len(viewer._frames)
        if els >= 2:
            a2 = win._create_animation_action("真图集", [], 0, els)
            win._install_animation_action(a2)
            wait(1200)
            panel.btn_similar_frames.click()
            wait(3000)
            print(f"  元素 {els} 个，容差 {similar_tolerance()}% →", win.lbl_status.text()[:110])
            print(f"  标色元素 {len(viewer.similar_marks)} 个")
            # 容差扫一遍：用户反馈"0 只找到两个、1 就一下子全找到了"，这里把每档的组数摆出来
            el_pms = [pm.copy(int(r[0]), int(r[1]), int(r[2]), int(r[3]))
                      for r in viewer._frames]
            from spritesheet.ui.similar import group_elements
            for tol in (0, 1, 2, 3, 5, 8, 12, 20):
                g = group_elements(el_pms, tol)
                print(f"  容差 {tol:>2}% → {len(set(g.values()))} 组 / {len(g)} 帧")
            panel.btn_similar_frames.click()
            wait(300)

    win.image_viewer.set_current_action(None)
    win.close()
    wait(200)
    restore()
    print(f"\nSIMILAR OK  {PASS}/{PASS + FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())