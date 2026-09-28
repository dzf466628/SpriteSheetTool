# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

r"""冒烟测试模板 —— offscreen 模式下验证关键功能不崩。

用法：
    $env:QT_QPA_PLATFORM="offscreen"
    .venv\Scripts\python.exe smoke_test.py

每个用例一个 check 函数，最后统计通过/失败数。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

app = QApplication(sys.argv)

PASSED = 0
FAILED = 0


def check(name: str, fn):
    global PASSED, FAILED
    try:
        fn()
        print(f"  [OK]   {name}")
        PASSED += 1
    except Exception as e:
        print(f"  [FAIL] {name}: {e}")
        FAILED += 1


# ========== 在这里加你的测试用例 ==========

def test_app_launches():
    """应用能正常启动"""
    assert app is not None


def test_window_creates():
    """主窗口能创建"""
    # from your_package.ui.main_window import MainWindow
    # w = MainWindow()
    # assert w is not None
    pass


# ========== 运行所有测试 ==========

if __name__ == "__main__":
    print("=" * 50)

    check("app launches", test_app_launches)
    check("window creates", test_window_creates)

    print("=" * 50)
    print(f"PASSED {PASSED}  |  FAILED {FAILED}")
    if FAILED == 0:
        print("SMOKE TEST OK")
        sys.exit(0)
    else:
        print("SMOKE TEST FAILED")
        sys.exit(1)
