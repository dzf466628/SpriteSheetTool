# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

r"""验证脚本模板 —— 专门验证视觉/交互细节（offscreen 测不了的）。

用法：
    .venv\Scripts\python.exe tools\verify_xxx.py

注意：
- Windows 控制台默认 GBK，输出特殊字符会崩，所以入口加了 UTF-8 重配
- 不要用 offscreen 模式，这个脚本就是要跑真实平台
"""
import os
import sys
from pathlib import Path

# Windows 控制台 UTF-8 重配（输出 ✕/✓ 等特殊字符不崩）
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

os.environ.pop("QT_QPA_PLATFORM", None)  # 不要用 offscreen

from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILURES = []


def check(label: str, ok: bool, detail: str = ""):
    if ok:
        print(f"  [OK] {label}")
    else:
        msg = f"  [FAIL] {label}"
        if detail:
            msg += f"  ({detail})"
        print(msg)
        FAILURES.append(label)


# ========== 在这里加你的验证逻辑 ==========

def main():
    app = QApplication(sys.argv)  # noqa: F841  必须保留 QApplication 实例引用（坑1）

    print("=== 开始验证 ===")

    # 示例：
    # check("控件存在", widget is not None)
    # check("尺寸正确", widget.width() == 100)

    print("=" * 50)
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} 项")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("ALL PASSED")
        sys.exit(0)


if __name__ == "__main__":
    main()
