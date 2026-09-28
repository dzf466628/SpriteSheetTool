# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""更新服务器连通性与目录列表检查。

发版后跑一次，确认：
  1) 源站地址能取到目录列表（.index.php；直接访问目录会 403）
  2) 证书严格校验通过（源站需持有 dudua.synology.me 的证书；App 不做任何回落）
  3) 列表里能解析出安装包版本号，且最高版本 > 当前版本（否则客户端不会提示更新）

Usage: .venv\\Scripts\\python.exe tools\\verify_updater.py [本地版本]
"""
import os
import sys
import urllib.request
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from spritesheet import __version__
from spritesheet.updater import UPDATE_URL, _INSTALLER_RE, _open, _parse_version


def main() -> int:
    local = sys.argv[1] if len(sys.argv) > 1 else __version__
    print(f"  更新地址 : {UPDATE_URL}")
    print(f"  本地版本 : {local}")

    print("\n=== 1) 严格校验证书（App 的实际行为，不放宽 TLS）===")
    strict_ok = True
    try:
        with urllib.request.urlopen(UPDATE_URL, timeout=12) as resp:
            print(f"  通过  状态 {resp.status}")
    except Exception as exc:  # noqa: BLE001
        strict_ok = False
        print(f"  失败  {type(exc).__name__}: {str(exc)[:140]}")
        print("  提示：证书必须是 dudua.synology.me 的有效证书，"
              "否则客户端会明确报错（不回落）")

    print("\n=== 2) 取目录列表 ===")
    resp = _open(UPDATE_URL, 12)
    with resp:
        html = resp.read().decode("utf-8", errors="replace")
        status = resp.status
    print(f"  状态 {status}  长度 {len(html)}")

    versions = sorted({m.group(1) for m in _INSTALLER_RE.finditer(html)},
                      key=_parse_version)
    print(f"\n=== 3) 目录里的安装包（{len(versions)} 个）===")
    for v in versions:
        flag = "  <-- 会提示更新" if _parse_version(v) > _parse_version(local) else ""
        print(f"  {v}{flag}")
    if not versions:
        print("  （没有找到任何安装包：请把 SpriteSheetTool_v.X.Y.Z.exe 上传到该目录）")
        return 1
    newest = versions[-1]
    if _parse_version(newest) > _parse_version(local):
        print(f"\n结论：服务器最高 {newest} > 本地 {local}，客户端会提示更新")
    else:
        print(f"\n结论：服务器最高 {newest} <= 本地 {local}，客户端不提示更新"
              f"（发新版后需上传新安装包）")
    print(f"  证书严格校验: {'通过' if strict_ok else '失败'}")
    return 0 if strict_ok else 1


if __name__ == "__main__":
    sys.exit(main())
