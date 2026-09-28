# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""变异测试：证明 verify_extract.py 的断言真的会咬（一次性工具，不进回归）。

做法：把 verify_extract.py 读进来，把某一行的期望值**改错**，再把改过的源码
交给一个**新的 python 子进程**去跑（-c 传源码，不落盘）。为什么不落盘、也不在
本进程里 exec：脚本收尾是 `os._exit()`，同进程 exec 会把变异测试自己一起带死
（第一版就是这么踩的）；落盘临时文件又会跟同一个 workspace 里别的回归脚本打架
（这个目录刚出现 _probe_thumb64.py / _check_similar_composite.py，说明有并行的
脚本在跑，所以连 tools/.settings_backup.json 这种共用名字都要绕开 —— 新子进程里
用 `TMP` 环境变量把存档指到私有目录）。

用法: QT_QPA_PLATFORM=offscreen .venv\\Scripts\\python.exe -X utf8 -u tools\\_mutate_extract.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TARGET = HERE / "verify_extract.py"

# (原样, 改成, 说明)
MUTATIONS = [
    ("EXPECT_AFTER_CANCEL = 3", "EXPECT_AFTER_CANCEL = 2",
     "把「取消后应回到 3」写成 2（就是探针里那条过时注释的口径）"),
    ("not dlg.isVisible()", "dlg.isVisible()",
     "把「取消后窗口已经关掉」反过来写"),
]


def run_mutant(source: str, tag: str) -> int:
    # 变异体必须**当文件跑**、不能用 `-c`（脚本开头要拿 `__file__` 定位仓库根，
    # `-c` 下没有 `__file__` → NameError），而且必须落在 **tools/ 里**
    # （它要 `from settings_guard import protect_settings`，那是同目录模块 ——
    # 放系统临时目录会 ModuleNotFoundError，这条也踩过）。
    # 文件名带 tag + 进程号，跑完在 finally 里删掉；只留这一个文件，不碰 verify_extract.py。
    path = HERE / f"_mutant_extract_{tag}_{os.getpid()}.py"
    path.write_text(source, encoding="utf-8")
    sandbox = tempfile.mkdtemp(prefix=f"mut_extract_{tag}_")
    try:
        env = dict(os.environ)
        env.update({
            "QT_QPA_PLATFORM": "offscreen",
            "PYTHONIOENCODING": "utf-8",
            # settings_guard 的存档落在 %TMP%，本来是 tools/ 下那个共用名字：
            # 指到私有目录，别跟并行脚本抢、也别留垃圾
            "TMP": sandbox, "TEMP": sandbox,
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        out_path = Path(sandbox) / "out.txt"
        with open(out_path, "wb") as fp:
            proc = subprocess.run(
                [sys.executable, "-X", "utf8", "-u", str(path)],
                cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL,
                stdout=fp, stderr=subprocess.STDOUT, timeout=600)
        print(out_path.read_text(encoding="utf-8", errors="replace").rstrip())
    finally:
        path.unlink(missing_ok=True)
        shutil.rmtree(sandbox, ignore_errors=True)
    print(f"  >>> 变异体 {tag} 退出码 = {proc.returncode}")
    return proc.returncode


def main() -> int:
    source = TARGET.read_text(encoding="utf-8")
    print(f"变异测试对象：{TARGET}")
    codes = []
    for index, (original, mutated, why) in enumerate(MUTATIONS, start=1):
        if original not in source:
            print(f"  源码里找不到要改的那句：[{original}]")
            return 2
        print(f"\n===== 变异 {index}：{why} =====")
        print(f"  （{original}  →  {mutated}）")
        codes.append(run_mutant(source.replace(original, mutated, 1), f"mut{index}"))
    print("\n===== 变异测试结论 =====")
    for index, code in enumerate(codes, start=1):
        print(f"  变异 {index} 退出码 = {code}（期望 1：断言咬住了、脚本红着退出）")
    ok = all(code == 1 for code in codes)
    print("MUTATE OK：两条变异都被断言抓住（EXTRACT FAILED + 退出码 1）"
          if ok else "MUTATE BAD：有变异没被抓住，说明那条断言是空的")
    return 0 if ok else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)