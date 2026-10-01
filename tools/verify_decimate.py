# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""抽帧算法的专项回归（纯函数，不碰 UI）。

对应需求原话：
  * 「1/2 仅去掉相邻的相似帧的一半」「1/4为仅去掉相邻相似帧的1/4」
  * 「强力去重为 所有相邻的相似帧只保留1帧」
  * 「一串就是 相似帧的颜色一样啊 只有相邻的才行 不连着的颜色一样不算一串」
  * 「最标准那帧」
  * 「3/5（抽的最小）」→ 档位按力度排 3/5 < 1/2 < 1/4 < 强力去重
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import Qt                                          # noqa: E402
from PyQt6.QtGui import QColor, QPainter, QPixmap                    # noqa: E402
from PyQt6.QtWidgets import QApplication                             # noqa: E402

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

_APP = QApplication.instance() or QApplication(sys.argv)

from spritesheet.ui.similar import (DECIMATE_RATIOS, compensate_offsets,  # noqa: E402
                                    decimate_indices, frame_source_table, plan_decimate,
                                    remap_frame_dict, remap_frame_set, split_runs)

PASS = 0
FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [OK] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def pm(color: str, size: int = 24, shift: int = 0) -> QPixmap:
    """造一张小图：底色 + 一个小方块（shift 让"同一姿势但略有差别"可复现）。"""
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.fillRect(2, 2, size - 4, size - 4, QColor(color))
    if shift:
        p.fillRect(4 + shift, 4 + shift, 6, 6, QColor("#FFFFFF"))
    p.end()
    return pix


def main() -> int:
    print("== 1. 切串：同组 + 相邻才算一串 ==")
    marks = {0: 1, 1: 1, 2: 1, 5: 1, 6: 1, 9: 2, 10: 2}
    runs = split_runs(marks, 12)
    check("相邻同组切成一串，断开的另算一串",
          runs == [[0, 1, 2], [5, 6], [9, 10]], runs)
    check("没配对的帧（不在 marks 里）不进串",
          all(4 not in r and 11 not in r for r in runs), runs)
    check("隔开的两段同组不会并成一串（不连着不算一串）",
          len([r for r in runs if r[0] == 0]) == 1 and [5, 6] in runs, runs)

    print("== 2. 各档保留比例（一串 N 帧） ==")
    # 一串 20 帧同组
    marks20 = {i: 7 for i in range(20)}
    pixmaps20 = [pm("#1565C0") for _ in range(20)]
    expect = {"keep_3_5": 12, "keep_1_2": 10, "keep_1_4": 5, "dedupe": 1}
    for mode, want in expect.items():
        got = decimate_indices(pixmaps20, marks20, mode)
        check(f"{mode}：一串 20 帧留 {want} 帧", len(got) == want, f"实际 {len(got)}")
    check("档位顺序 = 由轻到重（3/5 > 1/2 > 1/4 > 强力）",
          [len(decimate_indices(pixmaps20, marks20, m)) for m in
           ("keep_3_5", "keep_1_2", "keep_1_4", "dedupe")] == [12, 10, 5, 1])

    print("== 3. 短串至少留 1 帧，绝不清空 ==")
    for n in (2, 3, 4):
        marks_n = {i: 3 for i in range(n)}
        pixmaps_n = [pm("#1565C0") for _ in range(n)]
        for mode in DECIMATE_RATIOS:
            got = decimate_indices(pixmaps_n, marks_n, mode)
            check(f"一串 {n} 帧 + {mode}：最少留 1 帧", len(got) >= 1, got)

    print("== 4. 不相邻 / 未配对的帧原样保留 ==")
    marks_mix = {0: 1, 1: 1, 2: 1}
    pixmaps_mix = [pm("#1565C0") for _ in range(8)]
    got = decimate_indices(pixmaps_mix, marks_mix, "dedupe")
    check("只动那一串，其他帧一个不少",
          got == [0] + list(range(3, 8)), got)

    print("== 5. 留的是「最标准」那帧 ==")
    # 注意：特征里的"像不像"看的是**不透明形状**，所以异类必须是形状不同
    # （挖个洞 / 缺个角），加个白点在同一块方框里是看不出区别的。
    def damaged(size=24, hole=None, corner=False):
        pix = pm("#1565C0", size)
        p = QPainter(pix)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        if hole:
            p.fillRect(*hole, Qt.GlobalColor.transparent)
        if corner:
            p.fillRect(2, 2, 4, 4, Qt.GlobalColor.transparent)
        p.end()
        return pix

    # 中间三帧（下标 1/2/3）形状一致，两端是异类 → 中位帧应在 1/2/3 里，并列取靠前 = 下标 1
    run_marks = {i: 5 for i in range(5)}
    pixmaps_run = [damaged(hole=(8, 8, 6, 6)), pm("#1565C0"), pm("#1565C0"),
                   pm("#1565C0"), damaged(corner=True)]
    got = decimate_indices(pixmaps_run, run_marks, "dedupe")
    check("一串 5 帧时留下最标准那帧（并列取靠前 = 下标 1，而不是首帧 0）", got == [1], got)
    got2 = decimate_indices(pixmaps_run, run_marks, "keep_1_2")
    check("留 3 帧时也包含最标准那帧", 2 in got2 and len(got2) == 3, got2)

    print("== 6. 真实素材上的规模检查（不依赖具体图片，只用合成串） ==")
    big_marks = {}
    for i in range(300):
        big_marks[i] = i // 10          # 每 10 帧一串，共 30 串
    big = [pm("#C62828") for _ in range(300)]
    got = decimate_indices(big, big_marks, "keep_1_2")
    check("300 帧 / 30 串 → 1/2 后剩 150 帧", len(got) == 150, len(got))
    got = decimate_indices(big, big_marks, "dedupe")
    check("300 帧 / 30 串 → 强力去重后剩 30 帧", len(got) == 30, len(got))
    check("结果严格升序且不重复", got == sorted(set(got)), got[:5])

    print("== 7. 抽帧计划：帧是从元素派生的，元素被多帧共用时不能误删 ==")
    # 一帧一个元素：抽掉帧 1、3
    plan = plan_decimate(4, [10, 11, 12, 13], [], None, 0, keep=[0, 2])
    check("一帧一元素：删掉对应元素",
          plan["drop_elements"] == [11, 13] and plan["keep_selected"] == [10, 12],
          plan)
    check("frame_map 把旧帧号映射到新帧号",
          plan["frame_map"] == {0: 0, 2: 1} and plan["removed_frames"] == [1, 3], plan)

    # 合成帧：元素 20 同时出现在帧 0 和帧 2；抽掉帧 1（只用元素 21）时不能删 20
    groups = [[20], [21], [20]]
    plan = plan_decimate(3, [20, 21], groups, None, 0, keep=[0, 2])
    check("被抽帧共用的元素保留、只被抽掉帧用到的元素才删",
          plan["drop_elements"] == [21] and plan["keep_selected"] == [20], plan)
    check("composite_groups 跟着重排",
          plan["keep_composite"] == [[20], [20]], plan["keep_composite"])

    # 同理：抽掉帧 0 和 2（都只用元素 20），帧 1 还在用 21 → 20 可以删
    plan = plan_decimate(3, [20, 21], groups, None, 0, keep=[1])
    check("元素只被抽掉的帧用到时才删",
          plan["drop_elements"] == [20] and plan["keep_selected"] == [21], plan)

    # 导入工程：frame_layouts 每帧多个元素
    layouts = [[(30, 0, 0), (31, 2, 0)], [(32, 0, 0)], [(33, 0, 0), (34, 1, 1)]]
    plan = plan_decimate(3, [30, 31, 32, 33, 34], [], layouts, 3, keep=[0, 2])
    check("frame_layouts 里被抽帧的元素会删掉",
          plan["drop_elements"] == [32] and plan["keep_selected"] == [30, 31, 33, 34], plan)
    check("frame_layouts 跟着重排、base_after 正确",
          plan["keep_layouts"] == [[(30, 0, 0), (31, 2, 0)], [(33, 0, 0), (34, 1, 1)]]
          and plan["base_after"] == 2, plan)

    print("== 8. 每帧结构重排（偏移/时长/旋转/镜像等） ==")
    offsets = {0: (1, 1), 2: (2, 2), 3: (3, 3)}
    check("frame_offsets 按新帧号重排、被抽掉的丢掉",
          remap_frame_dict(offsets, {0: 0, 2: 1}) == {0: (1, 1), 1: (2, 2)}, offsets)
    check("frame_mirror 这类集合也能重排（保留的换新号、被抽掉的丢掉）",
          remap_frame_set({0, 3}, {0: 0, 2: 1}) == {0},
          remap_frame_set({0, 3}, {0: 0, 2: 1}))

    print("== 9. 帧↔元素映射必须照 app 的真做法（错这里=抽帧删错元素=后面的帧位移） ==")
    # 真做法（image_viewer.crop_frames_for_action）：按 selected_indices 顺序走，
    # 遇到属于合成组的元素就整组出一帧，组内元素标 emitted；不在组里的各自出一帧。
    check("合成组在开头：元素0自成帧、[1,2]合成一帧",
          frame_source_table([0, 1, 2], [[1, 2]], None, 5) == [[0], [1, 2]],
          frame_source_table([0, 1, 2], [[1, 2]], None, 5))
    # ↓ 这一条就是当年 bug 的表达式：老写法"帧号 k = composite_groups[k]"会给出 [[2,3],...]
    check("合成组在**后面**时不会错位（老写法 frame0=[2,3] 是错的）",
          frame_source_table([0, 1, 2, 3], [[2, 3]], None, 5) == [[0], [1], [2, 3]],
          frame_source_table([0, 1, 2, 3], [[2, 3]], None, 5))
    check("元素越界的部件会被跳过（帧号不能因此错位）",
          frame_source_table([5, 6], None, [[(99, 0, 0)], [(5, 0, 0)], [(6, 1, 1)]], 10)
          == [[5], [6]],
          frame_source_table([5, 6], None, [[(99, 0, 0)], [(5, 0, 0)], [(6, 1, 1)]], 10))
    plan = plan_decimate(3, [0, 1, 2, 3], [[1, 2]], None, 0, keep=[0, 2])
    check("合成帧被抽掉时，组内两个元素一起删；组外元素保留",
          plan["drop_elements"] == [1, 2] and plan["keep_selected"] == [0, 3], plan)

    print("== 10. 落点逐帧不变：相似 ≠ 同一尺寸，max 变小不许让画面位移 ==")
    # 三个尺寸不同的帧（容差>0 时一串里尺寸可以不一样），抽掉最大的那个（旧 1，60×60），
    # 保留 0(40×40) 和 2(44×44) —— max 从 60 变 44，居中项对所有帧一起变 ⇒ 会整体位移
    sizes_old = {0: (40, 40), 1: (60, 60), 2: (44, 44)}
    sizes_new = {0: (40, 40), 1: (44, 44)}
    frame_map = {0: 0, 2: 1}
    pad = 0

    def spot(size, off, maxwh):
        return (pad + (maxwh[0] - size[0]) // 2 + off[0],
                pad + (maxwh[1] - size[1]) // 2 + off[1])

    old_offsets = {0: (0, 0), 2: (5, -3)}
    spots_before = {0: spot(sizes_old[0], old_offsets[0], (60, 60)),
                    2: spot(sizes_old[2], old_offsets[2], (60, 60))}
    fixed = compensate_offsets(sizes_old, old_offsets, sizes_new, frame_map)
    spots_after = {0: spot(sizes_new[0], fixed.get(0, (0, 0)), (44, 44)),
                   1: spot(sizes_new[1], fixed.get(1, (0, 0)), (44, 44))}
    check("补偿后保留帧的落点与抽帧前逐一相同（旧0→新0、旧2→新1）",
          spots_after[0] == spots_before[0] and spots_after[1] == spots_before[2],
          (spots_before, spots_after, fixed))
    check("补偿量就是居中项的变化量（40×40 帧 max 60→44 ⇒ dx=+8）",
          fixed.get(0) == (8, 8), fixed)
    check("尺寸没变（max 没变）时不产生任何补偿",
          compensate_offsets({0: (40, 40), 1: (40, 40)}, {0: (1, 2)}, {0: (40, 40)},
                             {0: 0}) == {0: (1, 2)},
          compensate_offsets({0: (40, 40), 1: (40, 40)}, {0: (1, 2)}, {0: (40, 40)}, {0: 0}))

    print(f"\nDECIMATE OK  {PASS}/{PASS + FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())