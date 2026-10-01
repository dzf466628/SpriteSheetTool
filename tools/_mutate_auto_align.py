# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""变异验证：往产品代码里塞 4 个"看起来对"的错，看回归是否按预期变红。

约定（跟之前几批一致）：
  * 改之前把原文件按**字节**存下来，验完按字节还原（read_bytes/write_bytes）
  * 每个变异都要求"预期的那几条断言变红"，红了才算这条断言不是空的
  * 还原后再跑一次全绿，确认没把文件改坏
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
TEST = str(ROOT / "tools" / "verify_auto_align.py")

MUTATIONS = [
    ("transform.py", "均分判定不验算就认（碎屑/散图都会被当成均分）",
     "        if ok:\n            return index_of                                 # 验算通过：均分判定成立",
     "        if True:\n            return index_of                                 # 验算通过：均分判定成立",
     ["散着摆的图判不出均分"]),
    ("main_window.py", "均分格那条路拿「整张画布坐标」当格内落点",
     "            local_boxes.append((r[0] - cell[0], r[1] - cell[1], r[2], r[3]))",
     "            local_boxes.append((r[0], r[1], r[2], r[3]))",
     ["每帧都落回它那一格里的原位"]),
    ("main_window.py", "按钮压根不走均分格那条路",
     "        grid_offsets = self._grid_align_offsets(action)",
     "        grid_offsets = None",
     ["状态栏写明走的是均分格那条路"]),
]


def run_test() -> tuple[int, str]:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    p = subprocess.run([PY, "-X", "utf8", "-u", TEST], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, cwd=str(ROOT),
                       timeout=900)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main() -> int:
    failed = []
    for fname, what, old, new, expect in MUTATIONS:
        path = ROOT / "spritesheet" / "ui" / fname
        original = path.read_bytes()
        text = original.decode("utf-8")
        if text.count(old) != 1:
            print(f"[SKIP] {what}: 锚点出现 {text.count(old)} 次，不敢乱改")
            failed.append(what)
            continue
        path.write_bytes(text.replace(old, new).encode("utf-8"))
        try:
            code, out = run_test()
            reds = [e for e in expect if e not in out.replace("[OK]", "[OK]")]
            missing = [e for e in expect if f"[FAIL] {e}" not in out]
            if code != 0 and not missing:
                print(f"[OK] 变异被抓住：{what} → 预期断言变红 ({', '.join(expect)})")
            else:
                print(f"[BAD] 变异没被抓住：{what} (exit={code}, 缺={missing})")
                failed.append(what)
        finally:
            path.write_bytes(original)          # 字节级还原
            if path.read_bytes() != original:
                print(f"[BAD] {fname} 还原不一致！")
                failed.append(f"{fname} 还原")
        del reds
    code, out = run_test()
    if code == 0 and "AUTO ALIGN OK" in out:
        print("[OK] 还原后回归全绿：" + [l for l in out.splitlines() if "AUTO ALIGN OK" in l][0])
    else:
        print("[BAD] 还原后回归不是全绿")
        failed.append("还原后全绿")
    print()
    if failed:
        print("变异验证未通过：" + "; ".join(failed))
        return 1
    print(f"变异验证通过：{len(MUTATIONS)}/{len(MUTATIONS)} 条都被抓住，还原字节级一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())