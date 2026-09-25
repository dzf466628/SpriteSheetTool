"""相似帧的变异验证：故意改坏产品代码，确认对应断言会红；改完逐字节还原。

只改产品代码（spritesheet/），改前 read_bytes 存一份、跑完 write_bytes 还原，
还原后再跑一遍确认全绿 —— 跟之前的 _mutate_auto_align.py 一个套路。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
VERIFY = str(ROOT / "tools" / "verify_similar.py")

MUTATIONS = [
    ("spritesheet/ui/similar.py", "落单的帧也当成一组（会给没同伴的帧标色）",
     "    groups = sorted((ids for ids in members.values() if len(ids) >= 2),",
     "    groups = sorted((ids for ids in members.values() if len(ids) >= 1),",
     ["落单的那个不进结果"]),
    ("spritesheet/ui/similar.py", "相邻两帧一样也当循环（重复帧被报成循环）",
     "            if d < 2 or marks[i] != marks[j]:",
     "            if d < 0 or marks[i] != marks[j]:",
     ["相邻两帧一样不算循环"]),
    ("spritesheet/ui/main_window.py", "松开开关时不清标记（色块留在画布上）",
     "            viewer.similar_marks = {}\n            self._similar_index_of = {}",
     "            self._similar_index_of = {}",
     ["两处标记一起清掉"]),
]


def run_verify():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run([PY, "-X", "utf8", "-u", VERIFY], cwd=str(ROOT),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env)
    return proc.stdout + proc.stderr


def main():
    ok = True
    for rel, title, old, new, expect in MUTATIONS:
        path = ROOT / rel
        original = path.read_bytes()
        text = original.decode("utf-8")
        # main_window.py 是 CRLF（core.autocrlf=true 签出来的），锚点统一按文件实际换行符来，
        # 否则 count == 0 —— 踩过一次"锚点不唯一（0 次）"的假失败。
        if "\r\n" in text:
            old = old.replace("\r\n", "\n").replace("\n", "\r\n")
            new = new.replace("\r\n", "\n").replace("\n", "\r\n")
        if text.count(old) != 1:
            print(f"[BAD] 锚点不唯一（{text.count(old)} 次）：{rel} — {title}")
            ok = False
            continue
        path.write_bytes(text.replace(old, new).encode("utf-8"))
        try:
            out = run_verify()
        finally:
            path.write_bytes(original)          # 逐字节还原
        assert path.read_bytes() == original, f"还原失败：{rel}"
        failed = [line for line in out.splitlines() if line.startswith("  [FAIL]")]
        hit = [line for line in failed if any(e in line for e in expect)]
        if hit:
            print(f"[OK] 变异被抓住：{title} → 预期断言变红 ({expect[0]})")
        else:
            print(f"[BAD] 变异没被抓住：{title}（期望 {expect}）")
            print("    实际失败项：", failed[:4] or "无")
            ok = False
    out = run_verify()
    if "SIMILAR FRAMES OK" in out:
        print("[OK] 还原后回归全绿：", [l for l in out.splitlines() if "SIMILAR FRAMES" in l][0])
    else:
        print("[BAD] 还原后回归没全绿：", out.splitlines()[-3:])
        ok = False
    print(f"\n变异验证{'通过' if ok else '失败'}：{len(MUTATIONS)} 条，还原字节级一致")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())