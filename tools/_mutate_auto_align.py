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
    ("transform.py", "估算用「当前落点」当基准（等于把上次的修正又减一遍）",
     "        base_x = positions[i][0] - int(off[0])\n        base_y = positions[i][1] - int(off[1])",
     "        base_x = positions[i][0]\n        base_y = positions[i][1]",
     ["再点一次偏移没变"]),
    ("transform.py", "估算把全透明的帧也算进去",
     "    live = [i for i in range(count) if frame_has_content(action, i)]",
     "    live = [i for i in range(count)]",
     ["全透明的那帧不参与对齐"]),
    ("main_window.py", "认不出「原图已经被紧切」",
     'return "原图本身就已经是紧切过的小图（四周没有留白）"',
     'return ""',
     ["检测器自己认出", "先弹了提示"]),
    ("main_window.py", "紧切时不再弹提示确认，直接动手",
     "if not self._confirm_estimate_align(reason):",
     "if False:",
     ["点「取消」→ 偏移一点都不动"]),
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