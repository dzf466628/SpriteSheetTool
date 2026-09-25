"""相似帧的变异验证：故意改坏产品代码，确认对应断言会红；改完逐字节还原。

只改产品代码（spritesheet/），改前 read_bytes 存一份、跑完 write_bytes 还原，
还原后再跑一遍确认全绿 —— 跟之前的 _mutate_auto_align.py 一个套路。

两个回归各管一段：
  * tools/verify_similar.py              —— 相似帧算法 + 开关两态 + 普通帧标色（SIMILAR OK）
  * tools/_check_similar_composite.py    —— 合成帧的彩块（CHECK SIMILAR COMPOSITE OK）
合成帧那几条断言只认后者：合并帧的帧 rect 是整块包围盒，普通帧那条路查不到，
所以"合成帧有没有彩块"必须在合成帧的动作上验，改坏了 verify_similar 也不会红。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
VERIFY = str(ROOT / "tools" / "verify_similar.py")
CHECK = str(ROOT / "tools" / "_check_similar_composite.py")
CHECK_LEGACY = str(ROOT / "tools" / "_check_similar_composite.py")

# (文件, 说明, 原锚点, 坏锚点, 期望变红的断言片段, 该跑哪个回归)
# 回归：None = verify_similar.py；"check" = _check_similar_composite.py（合成帧彩块）；
#       "check_legacy" = 同一支 check 带 --legacy（对照改前：合成帧没彩块）
MUTATIONS = [
    ("spritesheet/ui/similar.py", "落单的帧也当成一组（会给没同伴的帧标色）",
     "    groups = sorted((ids for ids in members.values() if len(ids) >= 2),",
     "    groups = sorted((ids for ids in members.values() if len(ids) >= 1),",
     ["落单的那个不进结果"], None),
    ("spritesheet/ui/similar.py", "相邻两帧一样也当循环（重复帧被报成循环）",
     "            if d < 2 or marks[i] != marks[j]:",
     "            if d < 0 or marks[i] != marks[j]:",
     ["相邻两帧一样不算循环"], None),
    ("spritesheet/ui/main_window.py", "松开开关时不清标记（色块留在画布上）",
     "            viewer.similar_marks = {}\n            self._similar_index_of = {}",
     "            self._similar_index_of = {}",
     ["两处标记一起清掉"], None),
    ("spritesheet/ui/main_window.py", "合成帧只按 frame_rects 查色（退回改前：合成帧没彩块）",
     "        else:\n            parts = getattr(action, \"frame_parts\", None) or ()",
     "        elif False:\n            parts = getattr(action, \"frame_parts\", None) or ()",
     ["合成帧拿到了组色", "合成帧的缩略图右上角", "多帧动作里：合成帧"], "check"),
    ("spritesheet/ui/main_window.py", "有合成帧就把开关灰掉（退回改前那条拦截）",
     "        count = len(getattr(self.image_viewer, \"_frames\", None) or ())\n"
     "        if count >= 2:",
     "        if self._action_has_composite(self.image_viewer.current_action):\n"
     "            return False, \"当前动作里有合成帧（多个元素并成了一帧），彩块标不了\"\n"
     "        count = len(getattr(self.image_viewer, \"_frames\", None) or ())\n"
     "        if count >= 2:",
     ["有合成帧时「相似帧」按钮可点"], "check"),
]


def run_script(script, *extra):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run([PY, "-X", "utf8", "-u", script, *extra], cwd=str(ROOT),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env)
    return proc.stdout + proc.stderr


def run_verify():
    return run_script(VERIFY)


def run_check(legacy=False):
    return run_script(CHECK, *(["--legacy"] if legacy else []))


def _report(where, out):
    """一次跑完的结论：红了哪几条 + 是不是全绿。"""
    failed = [line for line in out.splitlines() if line.startswith("  [FAIL]")]
    green = "SIMILAR OK" in out or "CHECK SIMILAR COMPOSITE OK" in out
    if failed or not green:
        print(f"    {where}：红 {len(failed)} 条"
              + (f" → {failed[0].strip()[:80]}" if failed else "（但没看到全绿那行）"))
    else:
        print(f"    {where}：全绿")
    return failed


def main():
    ok = True
    for rel, title, old, new, expect, which in MUTATIONS:
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
            if which is None:
                failed = _report("verify_similar", run_verify())
            else:
                failed = _report("_check_similar_composite", run_check(which == "check_legacy"))
                _report("verify_similar（这一条不该红）", run_verify())
        finally:
            path.write_bytes(original)          # 逐字节还原
        assert path.read_bytes() == original, f"还原失败：{rel}"
        hit = [line for line in failed if any(e in line for e in expect)]
        if hit:
            print(f"[OK] 变异被抓住：{title} → 预期断言变红 ({expect[0]})")
        else:
            print(f"[BAD] 变异没被抓住：{title}（期望 {expect}）")
            print("    实际失败项：", failed[:4] or "无")
            ok = False
    out = run_verify()
    if "SIMILAR OK" in out:
        print("[OK] 还原后回归全绿：", [l for l in out.splitlines() if "SIMILAR OK" in l][0])
    else:
        print("[BAD] 还原后回归没全绿：", out.splitlines()[-3:])
        ok = False
    out = run_check()
    if "CHECK SIMILAR COMPOSITE OK" in out:
        print("[OK] 还原后合成帧探针全绿：",
              [l for l in out.splitlines() if "CHECK SIMILAR COMPOSITE" in l][0])
    else:
        print("[BAD] 还原后合成帧探针没全绿：", out.splitlines()[-3:])
        ok = False
    print(f"\n变异验证{'通过' if ok else '失败'}：{len(MUTATIONS)} 条，还原字节级一致")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())